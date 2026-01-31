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

"""Admin handlers for Training Program Tasks and Rankings.

This module contains handlers for managing tasks within training programs
and displaying training program rankings.

Handlers:
- TrainingProgramTasksHandler: Manage tasks in a training program
- AddTrainingProgramTaskHandler: Add a task to a training program
- RemoveTrainingProgramTaskHandler: Remove a task from a training program
- TrainingProgramRankingHandler: Show ranking for a training program
"""

import json
import logging

from cms.db import (
    Contest,
    TrainingProgram,
    Task,
    Student,
    StudentTask,
)
from cms.server.util import calculate_task_archive_progress
from cms.server.admin.handlers.utils import get_student_tags_by_participation
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission
from .contestranking import RankingCommonMixin


def _shift_task_nums(
    sql_session,
    filter_attr,
    filter_value,
    num_attr,
    threshold: int,
    delta: int
) -> None:
    """Shift task numbers after insertion or removal.

    This utility function handles the common pattern of incrementing or
    decrementing task numbers when a task is added or removed from a
    sequence (e.g., contest tasks or training day tasks).

    sql_session: The SQLAlchemy session.
    filter_attr: The attribute to filter by (e.g., Task.contest, Task.training_day).
    filter_value: The value to filter for.
    num_attr: The num attribute to shift (e.g., Task.num, Task.training_day_num).
    threshold: The threshold value - tasks with num > threshold will be shifted.
    delta: The amount to shift by (+1 for insertion, -1 for removal).
    """
    if delta > 0:
        # For insertion, process in descending order to avoid conflicts
        order = num_attr.desc()
        condition = num_attr >= threshold
    else:
        # For removal, process in ascending order
        order = num_attr
        condition = num_attr > threshold

    for t in sql_session.query(Task)\
                 .filter(filter_attr == filter_value)\
                 .filter(condition)\
                 .order_by(order)\
                 .all():
        setattr(t, num_attr.key, getattr(t, num_attr.key) + delta)
        sql_session.flush()


class TrainingProgramTasksHandler(BaseHandler):
    """Manage tasks in a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        self.render_params_for_training_program(training_program)
        self.r_params["unassigned_tasks"] = \
            self.sql_session.query(Task)\
                .filter(Task.contest_id.is_(None))\
                .all()

        self.render("training_program_tasks.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "tasks")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            operation: str = self.get_argument("operation")

            # Handle detach operation for archived training day tasks
            if operation.startswith("detach_"):
                task_id = operation.split("_", 1)[1]
                task = self.safe_get_item(Task, task_id)
                # Validate task belongs to this training program
                if task.contest != managing_contest:
                    raise ValueError("Task does not belong to this training program")
                self._detach_task_from_training_day(task)
                if self.try_commit():
                    self.service.proxy_service.reinitialize()
                self.redirect(fallback_page)
                return

            # Handle reorder operation from drag-and-drop
            if operation == "reorder":
                reorder_data = self.get_argument("reorder_data", "")
                if reorder_data:
                    self._reorder_tasks(managing_contest, reorder_data)
                    if self.try_commit():
                        self.service.proxy_service.reinitialize()
                self.redirect(fallback_page)
                return

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.redirect(fallback_page)

    def _reorder_tasks(self, contest: Contest, reorder_data: str) -> None:
        """Reorder tasks based on drag-and-drop data.

        reorder_data: JSON string with list of {task_id, new_num} objects.
        """
        try:
            order_list = json.loads(reorder_data)
        except json.JSONDecodeError as e:
            logging.warning(
                "Failed to parse reorder data: %s. Payload: %s",
                e.msg,
                reorder_data[:500],
            )
            raise ValueError(f"Invalid JSON in reorder data: {e.msg}") from e

        if not isinstance(order_list, list):
            raise ValueError("Reorder data must be a list")

        expected_ids = {t.id for t in contest.tasks}
        received_ids = {int(item.get("task_id")) for item in order_list}
        if received_ids != expected_ids:
            raise ValueError("Reorder data must include each task exactly once")

        # Validate new_num for each entry (0-based indices)
        num_tasks = len(contest.tasks)
        expected_nums = set(range(0, num_tasks))
        received_nums = set()

        for item in order_list:
            if "new_num" not in item:
                raise ValueError("Missing 'new_num' in reorder data entry")
            raw_num = item["new_num"]
            try:
                new_num = int(raw_num)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid 'new_num' value: {raw_num!r} is not an integer"
                )
            if new_num < 0 or new_num >= num_tasks:
                raise ValueError(
                    f"'new_num' {new_num} is out of range [0, {num_tasks - 1}]"
                )
            received_nums.add(new_num)

        if received_nums != expected_nums:
            raise ValueError(
                "Reorder data must include each task number exactly once "
                f"(expected {sorted(expected_nums)}, got {sorted(received_nums)})"
            )

        # First, set all task nums to None to avoid unique constraint issues
        task_updates = []
        for item in order_list:
            task = self.safe_get_item(Task, item["task_id"])
            new_num = int(item["new_num"])
            if task.contest == contest:
                task_updates.append((task, new_num))
                task.num = None
        self.sql_session.flush()

        # Then set the new nums
        for task, new_num in task_updates:
            task.num = new_num
        self.sql_session.flush()

    def _detach_task_from_training_day(self, task: Task) -> None:
        """Detach a task from its training day.

        This removes the training_day association from the task, making it
        available for assignment to new training days. The task remains in
        the training program.

        task: the task to detach.
        """
        if task.training_day is None:
            return

        training_day = task.training_day
        training_day_num = task.training_day_num

        task.training_day = None
        task.training_day_num = None

        self.sql_session.flush()

        # Reorder remaining tasks in the training day (only if there was a valid position)
        if training_day_num is not None:
            _shift_task_nums(
                self.sql_session,
                Task.training_day,
                training_day,
                Task.training_day_num,
                training_day_num,
                -1,
            )


class AddTrainingProgramTaskHandler(BaseHandler):
    """Add a task to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "tasks")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            task_id: str = self.get_argument("task_id")
            if task_id is None or task_id == "null" or task_id.strip() == "":
                raise ValueError("Please select a valid task")
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

        # Verify task is either unassigned or already belongs to this contest
        if task.contest is not None and task.contest != managing_contest:
            self.service.add_notification(
                make_datetime(),
                "Invalid field(s)",
                "Task already assigned to another contest",
            )
            self.redirect(fallback_page)
            return

        task.num = len(managing_contest.tasks)
        task.contest = managing_contest

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class RemoveTrainingProgramTaskHandler(BaseHandler):
    """Remove a task from a training program.

    The confirmation is now handled via a modal in the tasks page.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str, task_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        task = self.safe_get_item(Task, task_id)
        task_num = task.num

        # Remove from training day if assigned
        if task.training_day is not None:
            training_day = task.training_day
            training_day_num = task.training_day_num
            task.training_day = None
            task.training_day_num = None

            self.sql_session.flush()

            # Reorder remaining tasks in the training day
            if training_day_num is not None:
                _shift_task_nums(
                    self.sql_session,
                    Task.training_day,
                    training_day,
                    Task.training_day_num,
                    training_day_num,
                    -1,
                )

        # Remove from training program
        task.contest = None
        task.num = None

        self.sql_session.flush()

        # Reorder remaining tasks in the training program
        if task_num is not None:
            _shift_task_nums(
                self.sql_session, Task.contest, managing_contest, Task.num, task_num, -1
            )

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        # Return absolute path to tasks page
        self.write(f"../../../training_program/{training_program_id}/tasks")


class TrainingProgramRankingHandler(RankingCommonMixin, BaseHandler):
    """Show ranking for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, format: str = "online"):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = self._load_contest_data(managing_contest.id)

        # Build a dict of (participation_id, task_id) -> bool for tasks that students can access
        # A student can access a task if they have a StudentTask record for it
        # Default is False since we're whitelisting access via StudentTask
        can_access_by_pt = {}
        for p in self.contest.participations:
            for task in self.contest.get_tasks():
                can_access_by_pt[(p.id, task.id)] = False

        participation_ids = [p.id for p in self.contest.participations]
        if participation_ids:
            rows = (
                self.sql_session.query(Student.participation_id, StudentTask.task_id)
                .join(StudentTask, Student.id == StudentTask.student_id)
                .filter(Student.training_program_id == training_program.id)
                .filter(Student.participation_id.in_(participation_ids))
                .all()
            )
            for participation_id, task_id in rows:
                can_access_by_pt[(participation_id, task_id)] = True

        show_teams = self._calculate_scores(self.contest, can_access_by_pt)

        # Store participation data before commit (SQLAlchemy expires attributes on commit)
        participation_data = {}
        for p in self.contest.participations:
            if hasattr(p, "task_statuses"):
                participation_data[p.id] = (p.task_statuses, p.total_score)

        # Build student tags lookup for each participation (batch query)
        student_tags_by_participation = get_student_tags_by_participation(
            self.sql_session,
            training_program,
            [p.id for p in self.contest.participations]
        )

        # Calculate task archive progress for this training program
        task_archive_progress_by_participation = {}
        students_query = (
            self.sql_session.query(Student)
            .filter(Student.training_program_id == training_program.id)
            .all()
        )
        student_by_participation_id = {s.participation_id: s for s in students_query}

        for p in self.contest.participations:
            student = student_by_participation_id.get(p.id)
            if student:
                progress = calculate_task_archive_progress(
                    student, p, self.contest, self.sql_session
                )
                task_archive_progress_by_participation[p.id] = progress

        # Commit to release any advisory locks taken during score calculation
        self.sql_session.commit()

        # Re-assign task_statuses after commit (SQLAlchemy expired them)
        for p in self.contest.participations:
            if p.id in participation_data:
                p.task_statuses, p.total_score = participation_data[p.id]

        self.render_params_for_training_program(training_program)
        self.r_params["show_teams"] = show_teams
        self.r_params["student_tags_by_participation"] = student_tags_by_participation
        self.r_params["main_groups_data"] = None  # Not used for training program ranking
        self.r_params["task_archive_progress_by_participation"] = (
            task_archive_progress_by_participation
        )

        if format == "txt":
            self.set_header("Content-Type", "text/plain")
            filename = f"{training_program.name}_home_ranking.txt".replace(
                " ", "_"
            ).replace("/", "_")
            self.set_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.render("ranking.txt", **self.r_params)
        elif format == "csv":
            self.set_header("Content-Type", "text/csv")
            filename = f"{training_program.name}_home_ranking.csv".replace(
                " ", "_"
            ).replace("/", "_")
            self.set_header("Content-Disposition", f'attachment; filename="{filename}"')

            export_participations = sorted(
                [p for p in self.contest.participations if not p.hidden],
                key=lambda p: p.total_score,
                reverse=True,
            )

            csv_content = self._write_csv(
                self.contest,
                export_participations,
                list(self.contest.get_tasks()),
                student_tags_by_participation,
                show_teams,
                include_partial=True,
                task_archive_progress_by_participation=task_archive_progress_by_participation,
            )
            self.finish(csv_content)
        else:
            self.render("ranking.html", **self.r_params)
