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

"""Admin handlers for Training Programs.

Training programs organize year-long training with multiple sessions.
Each training program has a managing contest that handles all submissions.

This module contains core training program handlers. Related handlers are
split into separate modules:
- trainingday.py: Training day management handlers
- student.py: Student management handlers
- archive.py: Archive, attendance, and combined ranking handlers
"""

from datetime import datetime as dt
import json
from typing import Optional

import tornado.web

from sqlalchemy import func

from cms.db import (
    Contest,
    TrainingProgram,
    Participation,
    Submission,
    Task,
    Question,
    Announcement,
    Student,
    StudentTask,
)
from cms.server.util import (
    get_all_student_tags,
    parse_tags,
    calculate_task_archive_progress,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, SimpleHandler, require_permission
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


class TrainingProgramListHandler(SimpleHandler("training_programs.html")):
    """List all training programs.

    GET returns the list of all training programs.
    POST handles operations on a specific training program (e.g., removing).
    """
    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        self.r_params = self.render_params()
        self.r_params["training_programs"] = (
            self.sql_session.query(TrainingProgram)
            .order_by(TrainingProgram.name)
            .all()
        )
        self.render("training_programs.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        training_program_id: str = self.get_argument("training_program_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("training_programs", training_program_id, "remove")
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, ""
            )
            self.redirect(self.url("training_programs"))


class TrainingProgramHandler(BaseHandler):
    """View/edit a single training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        self.render_params_for_training_program(training_program)
        self.render("training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback = self.url("training_program", training_program_id)
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        contest = training_program.managing_contest

        try:
            # Update training program attributes
            attrs = training_program.get_attrs()
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")

            if not attrs["name"] or not attrs["name"].strip():
                raise ValueError("Name is required")

            if not attrs["description"] or not attrs["description"].strip():
                attrs["description"] = attrs["name"]

            training_program.set_attrs(attrs)

            # Sync description to managing contest
            contest.description = attrs["description"]

            # Update managing contest configuration fields
            contest_attrs = contest.get_attrs()

            # Allowed localizations (comma-separated list)
            allowed_localizations: str = self.get_argument("allowed_localizations", "")
            if allowed_localizations:
                contest_attrs["allowed_localizations"] = [
                    x.strip()
                    for x in allowed_localizations.split(",")
                    if len(x) > 0 and not x.isspace()
                ]
            else:
                contest_attrs["allowed_localizations"] = []

            # Programming languages
            contest_attrs["languages"] = self.get_arguments("languages")

            # Boolean settings
            self.get_bool(contest_attrs, "submissions_download_allowed")
            self.get_bool(contest_attrs, "allow_questions")
            self.get_bool(contest_attrs, "allow_user_tests")
            self.get_bool(contest_attrs, "allow_unofficial_submission_before_analysis_mode")
            self.get_bool(contest_attrs, "allow_delay_requests")

            # Login section boolean settings
            self.get_bool(contest_attrs, "block_hidden_participations")
            self.get_bool(contest_attrs, "allow_password_authentication")
            self.get_bool(contest_attrs, "allow_registration")
            self.get_bool(contest_attrs, "ip_restriction")
            self.get_bool(contest_attrs, "ip_autologin")

            # Score precision
            self.get_int(contest_attrs, "score_precision")

            # Times
            self.get_datetime(contest_attrs, "start")
            self.get_datetime(contest_attrs, "stop")
            self.get_string(contest_attrs, "timezone", empty=None)
            self.get_timedelta_sec(contest_attrs, "per_user_time")

            # Limits
            self.get_int(contest_attrs, "max_submission_number")
            self.get_int(contest_attrs, "max_user_test_number")
            self.get_timedelta_sec(contest_attrs, "min_submission_interval")
            self.get_timedelta_sec(contest_attrs, "min_submission_interval_grace_period")
            self.get_timedelta_sec(contest_attrs, "min_user_test_interval")

            # Token parameters
            self.get_string(contest_attrs, "token_mode")
            self.get_int(contest_attrs, "token_max_number")
            self.get_timedelta_sec(contest_attrs, "token_min_interval")
            self.get_int(contest_attrs, "token_gen_initial")
            self.get_int(contest_attrs, "token_gen_number")
            self.get_timedelta_min(contest_attrs, "token_gen_interval")
            self.get_int(contest_attrs, "token_gen_max")

            # Apply contest attributes
            contest.set_attrs(contest_attrs)

            # Validate that stop is not before start (only if both are set)
            if (
                contest.start is not None
                and contest.stop is not None
                and contest.stop < contest.start
            ):
                raise ValueError("End time must be after start time")

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback)
            return

        if self.try_commit():
            # Update the contest on RWS.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback)


class AddTrainingProgramHandler(
    SimpleHandler("add_training_program.html", permission_all=True)
):
    """Add a new training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.render("add_training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback = self.url("training_programs", "add")
        operation = self.get_argument("operation", "Create")

        try:
            name = self.get_argument("name")
            if not name or not name.strip():
                raise ValueError("Name is required")

            description = self.get_argument("description", "")
            if not description or not description.strip():
                description = name

            # Parse optional start and stop times from datetime-local inputs
            start_str = self.get_argument("start", "")
            stop_str = self.get_argument("stop", "")

            contest_kwargs: dict = {
                "name": "__" + name,
                "description": description,
                "allow_delay_requests": False,
            }

            if start_str:
                contest_kwargs["start"] = dt.strptime(start_str, "%Y-%m-%dT%H:%M")

            if stop_str:
                contest_kwargs["stop"] = dt.strptime(stop_str, "%Y-%m-%dT%H:%M")

            # Validate that stop is not before start
            if "start" in contest_kwargs and "stop" in contest_kwargs:
                if contest_kwargs["stop"] < contest_kwargs["start"]:
                    raise ValueError("End time must be after start time")

            # Create the managing contest
            managing_contest = Contest(**contest_kwargs)
            self.sql_session.add(managing_contest)

            # Create the training program
            training_program = TrainingProgram(
                name=name,
                description=description,
                managing_contest=managing_contest,
            )
            self.sql_session.add(training_program)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback)
            return

        if self.try_commit():
            if operation == "Create and add another":
                self.redirect(fallback)
            else:
                self.redirect(self.url("training_programs"))
        else:
            self.redirect(fallback)


class RemoveTrainingProgramHandler(BaseHandler):
    """Confirm and remove a training program.

    On delete, the managing contest and all its data (participations,
    submissions, tasks) will also be deleted due to CASCADE.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.render_params_for_training_program(training_program)
        self.r_params["unanswered"] = 0  # Override for deletion confirmation page

        # Count related data that will be deleted
        self.r_params["participation_count"] = (
            self.sql_session.query(Participation)
            .filter(Participation.contest == managing_contest)
            .count()
        )
        training_day_contest_ids = [td.contest_id for td in training_program.training_days]
        self.r_params["training_day_count"] = len(training_day_contest_ids)
        self.r_params["training_day_participation_count"] = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id.in_(training_day_contest_ids))
            .count()
            if training_day_contest_ids else 0
        )
        self.r_params["submission_count"] = (
            self.sql_session.query(Submission)
            .join(Participation)
            .filter(Participation.contest == managing_contest)
            .count()
        )
        self.r_params["training_day_submission_count"] = (
            self.sql_session.query(Submission)
            .join(Participation)
            .filter(Participation.contest_id.in_(training_day_contest_ids))
            .count()
            if training_day_contest_ids else 0
        )
        self.r_params["task_count"] = len(managing_contest.tasks)

        # Other contests available to move tasks into (excluding training day contests)
        self.r_params["other_contests"] = (
            self.sql_session.query(Contest)
            .filter(Contest.id != managing_contest.id)
            .filter(~Contest.name.like(r'\_\_%', escape='\\'))
            .filter(~Contest.training_day.has())
            .order_by(Contest.name)
            .all()
        )

        self.render("training_program_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str):

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        action = self.get_argument("action", "delete_all")
        target_contest_id = self.get_argument("target_contest_id", None)

        # Handle tasks before deleting the training program
        tasks = (
            self.sql_session.query(Task)
            .filter(Task.contest == managing_contest)
            .order_by(Task.num)
            .all()
        )

        if action == "move":
            if not target_contest_id:
                raise tornado.web.HTTPError(400, "Target contest is required")
            target_contest = self.safe_get_item(Contest, target_contest_id)

            # Phase 1: clear nums on moving tasks (and detach training day links)
            # so we can reassign without violating the unique constraint.
            for task in tasks:
                task.num = None
                task.training_day = None
                task.training_day_num = None
            self.sql_session.flush()

            # Phase 2: append after current max num in target, preserving gaps.
            max_num = (
                self.sql_session.query(func.max(Task.num))
                .filter(Task.contest == target_contest)
                .scalar()
            )
            base_num = (max_num or -1) + 1

            for i, task in enumerate(tasks):
                task.contest = target_contest
                task.num = base_num + i
            self.sql_session.flush()

        elif action == "detach":
            for task in tasks:
                task.contest = None
                task.num = None
                task.training_day = None
                task.training_day_num = None
            self.sql_session.flush()

        elif action == "delete_all":
            for task in tasks:
                self.sql_session.delete(task)
            self.sql_session.flush()
        else:
            raise tornado.web.HTTPError(400, "Invalid action")

        # Delete all training days (and their contests/participations).
        for training_day in list(training_program.training_days):
            td_contest = training_day.contest
            self.sql_session.delete(training_day)
            self.sql_session.delete(td_contest)

        # Delete the training program (tasks already handled above)
        self.sql_session.delete(training_program)

        # Then delete the managing contest (this cascades to participations,
        # submissions, etc. - tasks already handled above)
        self.sql_session.delete(managing_contest)

        self.try_commit()
        self.write("../../training_programs")
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
        except json.JSONDecodeError:
            return

        # First, set all task nums to None to avoid unique constraint issues
        task_map = {}
        for item in order_list:
            task = self.safe_get_item(Task, item["task_id"])
            if task.contest == contest:
                task_map[item["task_id"]] = item["new_num"]
                task.num = None
        self.sql_session.flush()

        # Then set the new nums
        for task_id, new_num in task_map.items():
            task = self.safe_get_item(Task, task_id)
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

        # Reorder remaining tasks in the training day
        _shift_task_nums(
            self.sql_session, Task.training_day, training_day,
            Task.training_day_num, training_day_num, -1
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
            assert task_id != "null", "Please select a valid task"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

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
            _shift_task_nums(
                self.sql_session, Task.training_day, training_day,
                Task.training_day_num, training_day_num, -1
            )

        # Remove from training program
        task.contest = None
        task.num = None

        self.sql_session.flush()

        # Reorder remaining tasks in the training program
        _shift_task_nums(
            self.sql_session, Task.contest, managing_contest,
            Task.num, task_num, -1
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
        student_tags_by_participation = {p.id: [] for p in self.contest.participations}
        if student_tags_by_participation:
            rows = (
                self.sql_session.query(Student.participation_id, Student.student_tags)
                .filter(Student.training_program_id == training_program.id)
                .filter(
                    Student.participation_id.in_(
                        list(student_tags_by_participation.keys())
                    )
                )
                .all()
            )
            for participation_id, tags in rows:
                student_tags_by_participation[participation_id] = tags or []

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
        if "participation_data" in locals():
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


class TrainingProgramSubmissionsHandler(BaseHandler):
    """Show submissions for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = managing_contest
        self.render_params_for_training_program(training_program)

        query = self.sql_session.query(Submission).join(Task)\
            .filter(Task.contest == managing_contest)
        page = int(self.get_query_argument("page", "0"))
        self.render_params_for_submissions(query, page)

        # Show training day column for training program submissions
        self.r_params["is_training_program"] = True

        self.render("contest_submissions.html", **self.r_params)


class TrainingProgramAnnouncementsHandler(BaseHandler):
    """Manage announcements for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        self.contest = training_program.managing_contest
        self.render_params_for_training_program(training_program)
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)

        self.render("announcements.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        subject = self.get_argument("subject", "")
        text = self.get_argument("text", "")
        announcement_id = self.get_argument("announcement_id", None)

        # Parse visible_to_tags from comma-separated string
        visible_to_tags_str = self.get_argument("visible_to_tags", "")
        visible_to_tags = parse_tags(visible_to_tags_str)

        if subject and text:
            if announcement_id is not None:
                # Edit existing announcement
                announcement = self.safe_get_item(Announcement, announcement_id)
                if announcement.contest_id != managing_contest.id:
                    raise tornado.web.HTTPError(404)
                announcement.subject = subject
                announcement.text = text
                announcement.visible_to_tags = visible_to_tags
            else:
                # Add new announcement
                announcement = Announcement(
                    timestamp=make_datetime(),
                    subject=subject,
                    text=text,
                    contest=managing_contest,
                    admin=self.current_user,
                    visible_to_tags=visible_to_tags,
                )
                self.sql_session.add(announcement)
            self.try_commit()

        self.redirect(self.url("training_program", training_program_id, "announcements"))


class TrainingProgramAnnouncementHandler(BaseHandler):
    """Delete an announcement from a training program."""

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def delete(self, training_program_id: str, ann_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        announcement = self.safe_get_item(Announcement, ann_id)
        if announcement.contest_id != managing_contest.id:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(announcement)
        self.try_commit()

        # Return relative path for ajax_delete
        # Note: This is resolved relative to the current page URL (announcements list),
        # not the delete URL, so we just need "announcements" not "../announcements"
        self.write("announcements")


class TrainingProgramQuestionsHandler(BaseHandler):
    """Manage questions for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = managing_contest
        self.render_params_for_training_program(training_program)

        self.r_params["questions"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .order_by(Question.question_timestamp.desc())\
            .order_by(Question.id).all()

        # Build training days with unanswered questions from notification data
        training_days_with_unanswered: list[dict] = []
        td_notifications = self.r_params.get("training_day_notifications", {})
        for td in training_program.training_days:
            if td.contest is None:
                continue
            td_notif = td_notifications.get(td.id, {})
            unanswered_count = td_notif.get("unanswered_questions", 0)
            if unanswered_count > 0:
                training_days_with_unanswered.append({
                    "contest_id": td.contest_id,
                    "name": td.contest.name,
                    "unanswered_count": unanswered_count,
                })
        self.r_params["training_days_with_unanswered_questions"] = \
            training_days_with_unanswered

        self.render("questions.html", **self.r_params)


class TrainingProgramOverviewRedirectHandler(BaseHandler):
    """Redirect /training_program/{id}/overview to the managing contest's overview page."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        self.redirect(
            self.url("contest", training_program.managing_contest.id, "overview")
        )


class TrainingProgramResourcesListRedirectHandler(BaseHandler):
    """Redirect /training_program/{id}/resourceslist to the managing contest's resourceslist page."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        self.redirect(
            self.url("contest", training_program.managing_contest.id, "resourceslist")
        )
