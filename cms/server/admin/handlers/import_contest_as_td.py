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

"""Admin handler for importing a contest as a training day.

This creates a new active (living) training day from an existing contest,
migrating submissions for participating students. The training day can
then be archived normally using the existing archive flow.
"""

import logging

from cms.db import (
    Contest,
    TrainingProgram,
    Submission,
    Student,
    TrainingDay,
    Participation,
)
from cms.grading.scorecache import invalidate_score_cache
from cms.server.admin.handlers.utils import (
    build_user_to_student_map,
    get_available_contests,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission

logger = logging.getLogger(__name__)


class ImportContestAsTrainingDayHandler(BaseHandler):
    """Import an existing contest as an active training day."""

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
                f"Contest '{contest.name}' has been imported as a "
                f"training day. You can now archive it when ready."
            )

        self.redirect(fallback_page)

    def _import_contest(
        self,
        training_program: TrainingProgram,
        managing_contest: Contest,
        contest: Contest,
    ) -> None:
        """Import a contest as a living training day.

        Creates a TrainingDay linked to the contest, moves tasks to the
        managing contest, and migrates submissions for enrolled students.
        The training day remains active and can be archived normally.
        """
        # 1. Create training day linked to the imported contest
        position = len(training_program.training_days)
        training_day = TrainingDay(
            training_program=training_program,
            position=position,
        )
        training_day.contest_id = contest.id
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

        # 6. Invalidate score cache for managing participations on migrated tasks
        for student, _imported_participation in participating:
            managing_participation = student.participation
            for task in imported_tasks:
                invalidate_score_cache(
                    self.sql_session,
                    participation_id=managing_participation.id,
                    task_id=task.id,
                )
        self.sql_session.flush()
