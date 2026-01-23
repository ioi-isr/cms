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

"""Admin handlers for Training Days.

Training days are individual training sessions within a training program.
Each training day has its own contest for submissions and can have main groups
with custom timing configurations.
"""

from datetime import datetime as dt, timedelta

import tornado.web

from sqlalchemy import func

from cms.db import (
    Contest,
    TrainingProgram,
    Participation,
    Submission,
    Question,
    Student,
    Task,
    TrainingDay,
    TrainingDayGroup,
)
from cms.server.util import (
    get_all_training_day_types,
    parse_tags,
)
from cmscommon.datetime import make_datetime, get_timezone, get_timezone_name

from .base import BaseHandler, require_permission, parse_datetime_with_timezone


def parse_and_validate_duration(
    hours_str: str,
    minutes_str: str,
    context: str = ""
) -> tuple[int, int]:
    """Parse and validate duration hours and minutes.

    Args:
        hours_str: String representation of hours (can be empty)
        minutes_str: String representation of minutes (can be empty)
        context: Optional context for error messages (e.g., "Group 'advanced'")

    Returns:
        Tuple of (hours, minutes) as integers

    Raises:
        ValueError: If validation fails
    """
    hours_str = hours_str.strip()
    minutes_str = minutes_str.strip()
    hours = int(hours_str) if hours_str else 0
    minutes = int(minutes_str) if minutes_str else 0
    provided = bool(hours_str or minutes_str)

    prefix = f"{context} " if context else ""

    if hours < 0:
        raise ValueError(f"{prefix}Duration hours cannot be negative")
    if minutes < 0 or minutes >= 60:
        raise ValueError(f"{prefix}Duration minutes must be between 0 and 59")
    if provided and hours == 0 and minutes == 0:
        raise ValueError(f"{prefix}Duration must be positive")

    return hours, minutes


class TrainingProgramTrainingDaysHandler(BaseHandler):
    """List and manage training days in a training program."""
    REMOVE = "Remove"
    MOVE_UP = "up by 1"
    MOVE_DOWN = "down by 1"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)

        self.render("training_program_training_days.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "training_days")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        try:
            training_day_id: str = self.get_argument("training_day_id")
            operation: str = self.get_argument("operation")
            assert operation in (
                self.REMOVE,
                self.MOVE_UP,
                self.MOVE_DOWN,
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            self.service.add_notification(
                make_datetime(), "Invalid training day", "Training day does not belong to this program")
            self.redirect(fallback_page)
            return

        if operation == self.REMOVE:
            asking_page = self.url(
                "training_program", training_program_id,
                "training_day", training_day_id, "remove"
            )
            self.redirect(asking_page)
            return

        elif operation == self.MOVE_UP:
            training_day2 = self.sql_session.query(TrainingDay)\
                .filter(TrainingDay.training_program == training_program)\
                .filter(TrainingDay.position == training_day.position - 1)\
                .first()

            if training_day2 is not None:
                tmp_a, tmp_b = training_day.position, training_day2.position
                training_day.position, training_day2.position = None, None
                self.sql_session.flush()
                training_day.position, training_day2.position = tmp_b, tmp_a

        elif operation == self.MOVE_DOWN:
            training_day2 = self.sql_session.query(TrainingDay)\
                .filter(TrainingDay.training_program == training_program)\
                .filter(TrainingDay.position == training_day.position + 1)\
                .first()

            if training_day2 is not None:
                tmp_a, tmp_b = training_day.position, training_day2.position
                training_day.position, training_day2.position = None, None
                self.sql_session.flush()
                training_day.position, training_day2.position = tmp_b, tmp_a

        self.try_commit()
        self.redirect(fallback_page)


class AddTrainingDayHandler(BaseHandler):
    """Add a new training day to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        # Get all student tags for the tagify select dropdown
        tags_query = self.sql_session.query(
            func.unnest(Student.student_tags).label("tag")
        ).filter(
            Student.training_program_id == training_program.id
        ).distinct()
        self.r_params["all_student_tags"] = sorted([row.tag for row in tags_query.all()])

        # Add timezone info for the form (use managing contest timezone)
        tz = get_timezone(None, managing_contest)
        self.r_params["timezone"] = tz
        self.r_params["timezone_name"] = get_timezone_name(tz)

        self.render("add_training_day.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "training_days", "add")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        # Get timezone for parsing datetime inputs
        tz = get_timezone(None, managing_contest)

        try:
            name = self.get_argument("name")
            if not name or not name.strip():
                raise ValueError("Name is required")

            description = self.get_argument("description", "")
            if not description or not description.strip():
                description = name

            # Parse optional start time and duration from inputs
            # Times are in the managing contest timezone
            start_str = self.get_argument("start", "")
            duration_hours_str = self.get_argument("duration_hours", "")
            duration_minutes_str = self.get_argument("duration_minutes", "")

            contest_kwargs: dict = {
                "name": name,
                "description": description,
            }

            if start_str:
                # Parse datetime in timezone and convert to UTC
                contest_kwargs["start"] = parse_datetime_with_timezone(start_str, tz)
            else:
                # Default to after training program end year (so contestants can't start until configured)
                program_end_year = managing_contest.stop.year
                default_date = dt(program_end_year + 1, 1, 1, 0, 0)
                contest_kwargs["start"] = default_date
                # Also set analysis_start/stop to satisfy Contest check constraints
                # (stop <= analysis_start and analysis_start <= analysis_stop)
                contest_kwargs["analysis_start"] = default_date
                contest_kwargs["analysis_stop"] = default_date

            # Calculate stop time from start + duration
            duration_hours, duration_minutes = parse_and_validate_duration(
                duration_hours_str, duration_minutes_str
            )

            if duration_hours > 0 or duration_minutes > 0:
                duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                contest_kwargs["stop"] = contest_kwargs["start"] + duration
            else:
                # Default stop to same as start when no duration specified
                contest_kwargs["stop"] = contest_kwargs["start"]

            # Parse main group configuration (if any)
            group_tags = self.get_arguments("group_tag_name[]")
            group_starts = self.get_arguments("group_start_time[]")
            group_duration_hours = self.get_arguments("group_duration_hours[]")
            group_duration_minutes = self.get_arguments("group_duration_minutes[]")
            group_alphabeticals = self.get_arguments("group_alphabetical[]")

            # Collect valid groups and their times for defaulting
            groups_to_create = []
            group_start_times = []
            group_end_times = []

            for i, tag in enumerate(group_tags):
                tag = tag.strip()
                if not tag:
                    continue

                group_start = None
                group_end = None

                if i < len(group_starts) and group_starts[i].strip():
                    group_start = parse_datetime_with_timezone(group_starts[i].strip(), tz)
                    group_start_times.append(group_start)

                # Calculate group end from start + duration
                g_hours_str = group_duration_hours[i].strip() if i < len(group_duration_hours) else ""
                g_mins_str = group_duration_minutes[i].strip() if i < len(group_duration_minutes) else ""
                g_duration_hours, g_duration_minutes = parse_and_validate_duration(
                    g_hours_str, g_mins_str, context=f"Group '{tag}'"
                )

                if group_start and (g_duration_hours > 0 or g_duration_minutes > 0):
                    group_duration = timedelta(
                        hours=g_duration_hours, minutes=g_duration_minutes
                    )
                    group_end = group_start + group_duration
                    group_end_times.append(group_end)

                alphabetical = str(i) in group_alphabeticals

                groups_to_create.append({
                    "tag_name": tag,
                    "start_time": group_start,
                    "end_time": group_end,
                    "alphabetical_task_order": alphabetical,
                })

            # Default training start/end from group times if not specified
            if not start_str and group_start_times:
                contest_kwargs["start"] = min(group_start_times)
            if (duration_hours == 0 and duration_minutes == 0) and group_end_times:
                contest_kwargs["stop"] = max(group_end_times)

            contest = Contest(**contest_kwargs)
            self.sql_session.add(contest)
            self.sql_session.flush()

            position = len(training_program.training_days)
            training_day = TrainingDay(
                training_program=training_program,
                contest=contest,
                position=position,
            )
            self.sql_session.add(training_day)

            # Create main groups
            seen_tags = set()
            for group_data in groups_to_create:
                if group_data["tag_name"] in seen_tags:
                    raise ValueError(f"Duplicate tag '{group_data['tag_name']}'")
                seen_tags.add(group_data["tag_name"])

                # Validate group times are within contest bounds
                if group_data["start_time"] and contest_kwargs.get("start"):
                    if group_data["start_time"] < contest_kwargs["start"]:
                        raise ValueError(f"Group '{group_data['tag_name']}' start time cannot be before training day start")
                if group_data["end_time"] and contest_kwargs.get("stop"):
                    if group_data["end_time"] > contest_kwargs["stop"]:
                        raise ValueError(f"Group '{group_data['tag_name']}' end time cannot be after training day end")

                group = TrainingDayGroup(
                    training_day=training_day,
                    **group_data
                )
                self.sql_session.add(group)

            # Auto-add participations for all students in the training program
            # Training days are for all students, so we create participations
            # in the training day's contest for each student
            # Pass the hidden property from the managing contest participation
            for student in training_program.students:
                user = student.participation.user
                hidden = student.participation.hidden
                participation = Participation(contest=contest, user=user, hidden=hidden)
                self.sql_session.add(participation)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.redirect(self.url("training_program", training_program_id, "training_days"))
        else:
            self.redirect(fallback_page)


class RemoveTrainingDayHandler(BaseHandler):
    """Confirm and remove a training day from a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, training_day_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)
        managing_contest = training_program.managing_contest

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404)

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["training_day"] = training_day
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = 0

        # Stats for warning message
        self.r_params["task_count"] = len(training_day.tasks)
        # For archived training days, contest_id is None so counts are 0
        if training_day.contest_id is not None:
            self.r_params["participation_count"] = (
                self.sql_session.query(Participation)
                .filter(Participation.contest_id == training_day.contest_id)
                .count()
            )
            self.r_params["submission_count"] = (
                self.sql_session.query(Submission)
                .join(Participation)
                .filter(Participation.contest_id == training_day.contest_id)
                .count()
            )
        else:
            self.r_params["participation_count"] = 0
            self.r_params["submission_count"] = 0

        self.render("training_day_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str, training_day_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404)

        contest = training_day.contest
        position = training_day.position

        # Always detach tasks from the training day - they stay in the training program.
        # The database FK has ON DELETE SET NULL, but we also clear training_day_num
        # explicitly to remove stale ordering metadata.
        tasks = (
            self.sql_session.query(Task)
            .filter(Task.training_day == training_day)
            .order_by(Task.training_day_num)
            .all()
        )

        for task in tasks:
            task.training_day = None
            task.training_day_num = None

        self.sql_session.flush()

        self.sql_session.delete(training_day)
        if contest is not None:
            self.sql_session.delete(contest)

        self.sql_session.flush()

        for td in training_program.training_days:
            if td.position is not None and position is not None and td.position > position:
                td.position -= 1

        self.try_commit()
        self.write("../../training_days")


class AddTrainingDayGroupHandler(BaseHandler):
    """Add a main group to a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        fallback_page = self.url("contest", contest_id)

        # Get timezone for parsing datetime inputs (use contest timezone)
        tz = get_timezone(None, contest)

        try:
            tag_name = self.get_argument("tag_name")
            if not tag_name or not tag_name.strip():
                raise ValueError("Tag name is required")

            # Strip whitespace before duplicate check to avoid bypass
            tag_name = tag_name.strip()

            # Check if tag is already used
            existing = self.sql_session.query(TrainingDayGroup)\
                .filter(TrainingDayGroup.training_day == training_day)\
                .filter(TrainingDayGroup.tag_name == tag_name)\
                .first()
            if existing:
                raise ValueError(f"Tag '{tag_name}' is already a main group")

            # Parse optional start time and duration
            start_str = self.get_argument("start_time", "")
            duration_hours_str = self.get_argument("duration_hours", "")
            duration_minutes_str = self.get_argument("duration_minutes", "")

            group_kwargs: dict = {
                "training_day": training_day,
                "tag_name": tag_name,
                "alphabetical_task_order": self.get_argument("alphabetical_task_order", None) is not None,
            }

            if start_str:
                group_kwargs["start_time"] = parse_datetime_with_timezone(start_str, tz)

            # Calculate end time from start + duration
            duration_hours, duration_minutes = parse_and_validate_duration(
                duration_hours_str, duration_minutes_str
            )

            if "start_time" in group_kwargs and (duration_hours > 0 or duration_minutes > 0):
                duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                group_kwargs["end_time"] = group_kwargs["start_time"] + duration

            # Validate group times are within contest bounds
            if "start_time" in group_kwargs and contest.start:
                if group_kwargs["start_time"] < contest.start:
                    raise ValueError("Group start time cannot be before training day start")
            if "end_time" in group_kwargs and contest.stop:
                if group_kwargs["end_time"] > contest.stop:
                    raise ValueError("Group end time cannot be after training day end")

            group = TrainingDayGroup(**group_kwargs)
            self.sql_session.add(group)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.try_commit()
        self.redirect(fallback_page)


class UpdateTrainingDayGroupsHandler(BaseHandler):
    """Update all main groups for a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        fallback_page = self.url("contest", contest_id)

        # Get timezone for parsing datetime inputs (use contest timezone)
        tz = get_timezone(None, contest)

        try:
            group_ids = self.get_arguments("group_id[]")
            start_times = self.get_arguments("start_time[]")
            duration_hours_list = self.get_arguments("duration_hours[]")
            duration_minutes_list = self.get_arguments("duration_minutes[]")

            if len(group_ids) != len(start_times):
                raise ValueError("Mismatched form data")

            for i, group_id in enumerate(group_ids):
                group = self.safe_get_item(TrainingDayGroup, group_id)
                if group.training_day_id != training_day.id:
                    raise ValueError(f"Group {group_id} does not belong to this training day")

                # Parse start time in timezone and convert to UTC
                start_str = start_times[i].strip()
                if start_str:
                    group.start_time = parse_datetime_with_timezone(start_str, tz)
                else:
                    group.start_time = None

                # Calculate end time from start + duration
                hours_str = duration_hours_list[i].strip() if i < len(duration_hours_list) else ""
                mins_str = duration_minutes_list[i].strip() if i < len(duration_minutes_list) else ""
                duration_hours, duration_minutes = parse_and_validate_duration(
                    hours_str, mins_str, context=f"Group '{group.tag_name}'"
                )

                if group.start_time and (duration_hours > 0 or duration_minutes > 0):
                    duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                    group.end_time = group.start_time + duration
                else:
                    group.end_time = None

                # Validate group times are within contest bounds
                if group.start_time and contest.start:
                    if group.start_time < contest.start:
                        raise ValueError(f"Group '{group.tag_name}' start time cannot be before training day start")
                if group.end_time and contest.stop:
                    if group.end_time > contest.stop:
                        raise ValueError(f"Group '{group.tag_name}' end time cannot be after training day end")

                # Update alphabetical task order (checkbox - present means checked)
                group.alphabetical_task_order = self.get_argument(f"alphabetical_{group_id}", None) is not None

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.try_commit()
        self.redirect(fallback_page)


class RemoveTrainingDayGroupHandler(BaseHandler):
    """Remove a main group from a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str, group_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        group = self.safe_get_item(TrainingDayGroup, group_id)

        if group.training_day_id != training_day.id:
            raise tornado.web.HTTPError(404, "Group does not belong to this training day")

        self.sql_session.delete(group)
        self.try_commit()
        self.redirect(self.url("contest", contest_id))


class TrainingDayTypesHandler(BaseHandler):
    """Handler for updating training day types via AJAX."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, training_day_id: str):
        self.set_header("Content-Type", "application/json")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            self.set_status(404)
            self.write({"error": "Training day does not belong to this program"})
            return

        try:
            types_str = self.get_argument("training_day_types", "")
            training_day.training_day_types = parse_tags(types_str)

            if self.try_commit():
                self.write({
                    "success": True,
                    "types": training_day.training_day_types
                })
            else:
                self.set_status(500)
                self.write({"error": "Failed to save"})

        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})
