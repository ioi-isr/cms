#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Admin handler for importing a contest as a past training day.

This creates a new training day from an existing contest, migrates
submissions for participating students, and archives the training day
in one operation. It reuses the shared archiving functions from archive.py.
"""

import logging

from cms.db import (
    Contest,
    TrainingProgram,
    Submission,
    Student,
    Task,
    TrainingDay,
    Participation,
    ArchivedAttendance,
)
from cms.grading.scorecache import invalidate_score_cache
from cms.server.admin.handlers.utils import (
    build_task_data_for_archive,
    build_user_to_student_map,
    get_available_contests,
)
from cmscommon.datetime import make_datetime

from .archive import (
    ensure_student_tasks,
    collect_task_scores_and_submissions,
    collect_score_history,
    create_archived_ranking,
)
from .base import BaseHandler, require_permission

logger = logging.getLogger(__name__)


class ImportContestAsTrainingDayHandler(BaseHandler):
    """Import an existing contest as a past (archived) training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "training_days"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        contest_id_str = self.get_argument("contest_id", "")
        if not contest_id_str:
            self.service.add_notification(
                make_datetime(), "Error", "No contest selected"
            )
            self.redirect(fallback_page)
            return

        contest = self.safe_get_item(Contest, contest_id_str)

        available_ids = {c.id for c in get_available_contests(self.sql_session)}
        if contest.id not in available_ids:
            self.service.add_notification(
                make_datetime(), "Error",
                "Selected contest is not available for import"
            )
            self.redirect(fallback_page)
            return

        try:
            self._import_contest(training_program, managing_contest, contest)
        except Exception as error:
            self.sql_session.rollback()
            logger.exception("Import contest as training day failed")
            self.service.add_notification(
                make_datetime(), "Import failed", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Contest imported",
                f"Contest '{contest.name}' has been imported as a past "
                f"training day successfully"
            )

        self.redirect(fallback_page)

    def _import_contest(
        self,
        training_program: TrainingProgram,
        managing_contest: Contest,
        contest: Contest,
    ) -> None:
        """Perform the full import: create TD, migrate, archive, delete."""
        # 1. Create training day (archived immediately, no contest_id)
        position = len(training_program.training_days)
        training_day = TrainingDay(
            training_program=training_program,
            position=position,
            name=contest.name,
            description=contest.description,
            start_time=contest.start,
        )
        if contest.stop and contest.start:
            training_day.duration = contest.stop - contest.start
        self.sql_session.add(training_day)
        self.sql_session.flush()

        # 2. Move tasks from imported contest to managing contest
        imported_tasks = list(contest.tasks)
        if not imported_tasks:
            logger.warning(
                "Imported contest %s has no tasks", contest.id
            )

        existing_max_num = max(
            (t.num for t in managing_contest.tasks if t.num is not None),
            default=-1,
        )

        for task in imported_tasks:
            task.contest_id = None
            task.num = None
        self.sql_session.flush()

        for i, task in enumerate(imported_tasks):
            task.training_day_num = i
            task.num = existing_max_num + 1 + i
            task.contest_id = managing_contest.id
            task.training_day_id = training_day.id
        self.sql_session.flush()

        # 3. Determine participating students
        user_to_student = build_user_to_student_map(training_program)
        participating: list[tuple[Student, Participation]] = []
        non_participating_ids: list[int] = []

        for participation in contest.participations:
            student = user_to_student.get(participation.user_id)
            if student is not None:
                participating.append((student, participation))
            else:
                non_participating_ids.append(participation.id)

        logger.info(
            "Import contest %s: %d participating students, "
            "%d non-participating participations",
            contest.name, len(participating), len(non_participating_ids),
        )

        # 4. Migrate submissions for participating students
        task_ids = {t.id for t in imported_tasks}
        for student, imported_participation in participating:
            managing_participation = student.participation
            submissions = (
                self.sql_session.query(Submission)
                .filter(
                    Submission.participation_id == imported_participation.id
                )
                .filter(Submission.task_id.in_(task_ids))
                .all()
            )
            for sub in submissions:
                sub.opaque_id = Submission.generate_opaque_id(
                    self.sql_session, managing_participation.id
                )
                sub.participation_id = managing_participation.id
                sub.training_day_id = training_day.id
        self.sql_session.flush()

        # 5. Delete submissions from non-participating users (with warning)
        if non_participating_ids:
            orphan_count = (
                self.sql_session.query(Submission)
                .filter(
                    Submission.participation_id.in_(non_participating_ids)
                )
                .filter(Submission.task_id.in_(task_ids))
                .count()
            )
            if orphan_count > 0:
                logger.warning(
                    "Deleting %d submission(s) from %d non-participating "
                    "user(s) in imported contest '%s'",
                    orphan_count,
                    len(non_participating_ids),
                    contest.name,
                )
                (
                    self.sql_session.query(Submission)
                    .filter(
                        Submission.participation_id.in_(non_participating_ids)
                    )
                    .filter(Submission.task_id.in_(task_ids))
                    .delete(synchronize_session="fetch")
                )
                self.sql_session.flush()

        # 6. Invalidate and rebuild score cache for managing participations
        for student, imported_participation in participating:
            managing_participation = student.participation
            for task in imported_tasks:
                invalidate_score_cache(
                    self.sql_session,
                    participation_id=managing_participation.id,
                    task_id=task.id,
                )
        self.sql_session.flush()

        # 7. Build archived_tasks_data
        training_day.archived_tasks_data = {
            str(task.id): build_task_data_for_archive(task)
            for task in imported_tasks
        }

        # 8. Build attendance and ranking records for each participating student
        for student, imported_participation in participating:
            managing_participation = student.participation

            if imported_participation.starting_time is None:
                status = "missed"
                location = None
            else:
                status = "participated"
                location = "home"

            archived_attendance = ArchivedAttendance(
                status=status,
                location=location,
            )
            archived_attendance.training_day_id = training_day.id
            archived_attendance.student_id = student.id
            self.sql_session.add(archived_attendance)

            student_tags = (
                list(student.student_tags) if student.student_tags else []
            )
            student_missed = imported_participation.starting_time is None

            ensure_student_tasks(
                self.sql_session, student, imported_tasks, training_day
            )

            user_display = (
                f"{imported_participation.user.username} "
                f"(id={imported_participation.user_id})"
            )

            task_scores, submissions = collect_task_scores_and_submissions(
                self.sql_session,
                training_day=training_day,
                score_participation=managing_participation,
                submission_participation=managing_participation,
                visible_tasks=imported_tasks,
                student_missed=student_missed,
                starting_time=imported_participation.starting_time,
                user_display=user_display,
            )

            history = collect_score_history(
                self.sql_session,
                history_participation=managing_participation,
                training_day_task_ids=task_ids,
                student_missed=student_missed,
                starting_time=imported_participation.starting_time,
                user_id=imported_participation.user_id,
                user_display=user_display,
                training_day_name=training_day.name or "",
            )

            create_archived_ranking(
                self.sql_session, training_day, student,
                student_tags, task_scores, submissions, history,
            )

        # 9. Delete the imported contest (cascades participations etc.)
        self.sql_session.delete(contest)
