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
from typing import List, Tuple

from cms.db import (
    Contest,
    TrainingProgram,
    Submission,
    Student,
    Task,
    TrainingDay,
    Participation,
)
from cms.grading.scorecache import invalidate_score_cache
from cms.server.admin.handlers.utils import (
    build_user_to_student_map,
    get_available_contests,
)
from cmscommon.datetime import make_datetime

from .archive import (
    StudentArchiveData,
    perform_training_day_archiving,
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

        # 1. Create Training Day
        training_day = self._setup_training_day(training_program, contest)

        # 2. Migrate Tasks to Managing Contest
        imported_tasks = list(contest.tasks)
        self._migrate_tasks(managing_contest, training_day, imported_tasks)

        # 3. Identify Participants
        participating, non_participating_ids = self._identify_participants(
            training_program, contest
        )

        # 4. Migrate Submissions
        self._migrate_submissions(
            imported_tasks,
            participating,
            non_participating_ids,
            training_day,
            contest.name,
        )

        # 5. Rebuild Score Cache
        self._rebuild_score_cache(participating, imported_tasks)

        # 6. Prepare Archive Data
        student_datas = self._prepare_archive_data(participating, imported_tasks)

        # 7. Perform Archiving (Shared Logic)
        perform_training_day_archiving(
            self.sql_session, training_day, imported_tasks, student_datas
        )

        # 8. Delete the original contest
        self.sql_session.delete(contest)

    def _setup_training_day(
        self, training_program: TrainingProgram, contest: Contest
    ) -> TrainingDay:
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
        return training_day

    def _migrate_tasks(
        self, managing_contest: Contest, training_day: TrainingDay, tasks: List[Task]
    ) -> None:
        if not tasks:
            return

        existing_max_num = max(
            (t.num for t in managing_contest.tasks if t.num is not None),
            default=-1,
        )

        for task in tasks:
            task.contest_id = None
            task.num = None
        self.sql_session.flush()

        for i, task in enumerate(tasks):
            task.training_day_num = i
            task.num = existing_max_num + 1 + i
            task.contest_id = managing_contest.id
            task.training_day_id = training_day.id
        self.sql_session.flush()

    def _identify_participants(
        self, training_program: TrainingProgram, contest: Contest
    ) -> Tuple[List[Tuple[Student, Participation]], List[int]]:
        user_to_student = build_user_to_student_map(training_program)
        participating = []
        non_participating_ids = []

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
        return participating, non_participating_ids

    def _migrate_submissions(
        self,
        tasks: List[Task],
        participating: List[Tuple[Student, Participation]],
        non_participating_ids: List[int],
        training_day: TrainingDay,
        contest_name: str,
    ) -> None:
        task_ids = {t.id for t in tasks}

        # Migrate for students
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

        # Delete orphans
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
                    contest_name,
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

    def _rebuild_score_cache(
        self, participating: List[Tuple[Student, Participation]], tasks: List[Task]
    ) -> None:
        for student, _ in participating:
            managing_participation = student.participation
            for task in tasks:
                invalidate_score_cache(
                    self.sql_session,
                    participation_id=managing_participation.id,
                    task_id=task.id,
                )
        self.sql_session.flush()

    def _prepare_archive_data(
        self, participating: List[Tuple[Student, Participation]], tasks: List[Task]
    ) -> List[StudentArchiveData]:
        student_datas = []

        for student, imported_participation in participating:
            managing_participation = student.participation

            # Simple attendance logic for import
            if imported_participation.starting_time is None:
                status = "missed"
                location = None
            else:
                status = "participated"
                location = "home"

            attendance_kwargs = {
                "status": status,
                "location": location,
            }

            student_datas.append(
                StudentArchiveData(
                    student=student,
                    attendance_kwargs=attendance_kwargs,
                    visible_tasks=tasks,  # All imported tasks are visible
                    score_participation=managing_participation,
                    submission_participation=managing_participation,
                    history_participation=managing_participation,
                    starting_time=imported_participation.starting_time,
                    user_id=imported_participation.user_id,
                    user_display=f"{imported_participation.user.username} (id={imported_participation.user_id})",
                )
            )

        return student_datas
