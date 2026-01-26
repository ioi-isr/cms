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


def calculate_group_times(
    start_str: str,
    duration_hours_str: str,
    duration_minutes_str: str,
    tz,
    context: str = "",
) -> tuple[dt | None, dt | None]:
    """Parse start time and duration to calculate start and end times.

    Args:
        start_str: String representation of start time
        duration_hours_str: String representation of duration hours
        duration_minutes_str: String representation of duration minutes
        tz: Timezone for parsing start time
        context: Optional context for error messages

    Returns:
        Tuple of (start_time, end_time). Both can be None.
    """
    start_time = None
    if start_str and start_str.strip():
        start_time = parse_datetime_with_timezone(start_str.strip(), tz)

    duration_hours, duration_minutes = parse_and_validate_duration(
        duration_hours_str, duration_minutes_str, context=context
    )

    end_time = None
    if duration_hours > 0 or duration_minutes > 0:
        if not start_time:
            prefix = f"{context} " if context else ""
            raise ValueError(
                f"{prefix}Duration cannot be specified without a start time"
            )

        duration = timedelta(hours=duration_hours, minutes=duration_minutes)
        end_time = start_time + duration

    return start_time, end_time


def validate_group_times_within_contest(
    group_start: dt | None,
    group_end: dt | None,
    contest_start: dt | None,
    contest_stop: dt | None,
    context: str = "Group",
):
    """Validate that group times are within contest bounds.

    Args:
        group_start: Group start datetime
        group_end: Group end datetime
        contest_start: Contest start datetime
        contest_stop: Contest stop datetime
        context: Context string for error messages (e.g. "Group 'A'")

    Raises:
        ValueError: If group times are outside contest bounds
    """
    if group_start and contest_start:
        if group_start < contest_start:
            raise ValueError(
                f"{context} start time cannot be before training day start"
            )
    if group_end and contest_stop:
        if group_end > contest_stop:
            raise ValueError(f"{context} end time cannot be after training day end")


class TrainingProgramTrainingDaysHandler(BaseHandler):
    """List and manage training days in a training program."""
    REORDER = "reorder"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        self.render_params_for_training_program(training_program)
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)

        self.render("training_program_training_days.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "training_days")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        operation: str = self.get_argument("operation", "")

        if operation == self.REORDER:
            import json
            try:
                reorder_data = self.get_argument("reorder_data", "")
                if not reorder_data:
                    raise ValueError("No reorder data provided")

                order_list = json.loads(reorder_data)

                active_training_days = [
                    td for td in training_program.training_days
                    if td.contest is not None
                ]
                td_by_id = {str(td.id): td for td in active_training_days}

                for td in active_training_days:
                    td.position = None
                self.sql_session.flush()

                for item in order_list:
                    td_id = str(item["training_day_id"])
                    new_pos = int(item["new_position"])
                    if td_id in td_by_id:
                        td_by_id[td_id].position = new_pos
                self.sql_session.flush()

            except Exception as error:
                self.service.add_notification(
                    make_datetime(), "Reorder failed", repr(error)
                )
                self.redirect(fallback_page)
                return

        self.try_commit()
        self.redirect(fallback_page)


class AddTrainingDayHandler(BaseHandler):
    """Add a new training day to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.render_params_for_training_program(training_program)

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

            contest_kwargs: dict = {
                "name": name,
                "description": description,
            }

            # Parse main group configuration (if any)
            group_tags = self.get_arguments("group_tag_name[]")
            group_starts = self.get_arguments("group_start_time[]")
            group_duration_hours = self.get_arguments("group_duration_hours[]")
            group_duration_minutes = self.get_arguments("group_duration_minutes[]")
            group_alphabeticals = self.get_arguments("group_alphabetical[]")

            # Collect valid groups and their times for defaulting
            groups_to_create = []
            earliest_group_start = None
            latest_group_end = None

            for i, tag in enumerate(group_tags):
                tag = tag.strip()
                if not tag:
                    continue

                start_s = group_starts[i] if i < len(group_starts) else ""
                hours_s = (
                    group_duration_hours[i] if i < len(group_duration_hours) else ""
                )
                mins_s = (
                    group_duration_minutes[i] if i < len(group_duration_minutes) else ""
                )

                group_start, group_end = calculate_group_times(
                    start_s, hours_s, mins_s, tz, context=f"Group '{tag}'"
                )

                if group_start:
                    if (
                        earliest_group_start is None
                        or group_start < earliest_group_start
                    ):
                        earliest_group_start = group_start
                if group_end:
                    if latest_group_end is None or group_end > latest_group_end:
                        latest_group_end = group_end

                alphabetical = str(i) in group_alphabeticals

                groups_to_create.append({
                    "tag_name": tag,
                    "start_time": group_start,
                    "end_time": group_end,
                    "alphabetical_task_order": alphabetical,
                })

                # Parse optional start time and duration from inputs
            # Times are in the managing contest timezone
            start_str = self.get_argument("start", "")
            duration_hours_str = self.get_argument("duration_hours", "")
            duration_minutes_str = self.get_argument("duration_minutes", "")

            s_time, e_time = calculate_group_times(
                start_str, duration_hours_str, duration_minutes_str, tz
            )

            if s_time:
                contest_kwargs["start"] = s_time
            else:
                # Default to after training program end year (so contestants can't start until configured)
                program_end_year = managing_contest.stop.year
                default_date = dt(program_end_year + 1, 1, 1, 0, 0)
                contest_kwargs["start"] = (
                    earliest_group_start if earliest_group_start else default_date
                )
                # Also set analysis_start/stop to satisfy Contest check constraints
                # (stop <= analysis_start and analysis_start <= analysis_stop)
                contest_kwargs["analysis_start"] = default_date
                contest_kwargs["analysis_stop"] = default_date

            if e_time:
                contest_kwargs["stop"] = e_time
            else:
                contest_kwargs["stop"] = (
                    latest_group_end if latest_group_end else contest_kwargs["start"]
                )

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
                validate_group_times_within_contest(
                    group_data["start_time"],
                    group_data["end_time"],
                    contest_kwargs.get("start"),
                    contest_kwargs.get("stop"),
                    context=f"Group '{group_data['tag_name']}'",
                )

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

        self.render_params_for_training_program(training_program)
        self.r_params["training_day"] = training_day
        self.r_params["unanswered"] = 0  # Override for deletion confirmation page

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

            # Calculate start and end times
            s_time, e_time = calculate_group_times(
                start_str, duration_hours_str, duration_minutes_str, tz
            )

            if s_time:
                group_kwargs["start_time"] = s_time
            if e_time:
                group_kwargs["end_time"] = e_time

            # Validate group times are within contest bounds
            validate_group_times_within_contest(
                group_kwargs.get("start_time"),
                group_kwargs.get("end_time"),
                contest.start,
                contest.stop,
                context="Group",
            )

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

                # Calculate start and end times
                hours_str = (
                    duration_hours_list[i] if i < len(duration_hours_list) else ""
                )
                mins_str = (
                    duration_minutes_list[i] if i < len(duration_minutes_list) else ""
                )

                group.start_time, group.end_time = calculate_group_times(
                    start_times[i],
                    hours_str,
                    mins_str,
                    tz,
                    context=f"Group '{group.tag_name}'",
                )

                # Validate group times are within contest bounds
                validate_group_times_within_contest(
                    group.start_time,
                    group.end_time,
                    contest.start,
                    contest.stop,
                    context=f"Group '{group.tag_name}'",
                )

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


class ScoreboardSharingHandler(BaseHandler):
    """Handler for updating scoreboard sharing settings for archived training days."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, training_day_id: str):
        self.set_header("Content-Type", "application/json")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            self.set_status(404)
            self.write({"error": "Training day does not belong to this program"})
            return

        # Only allow for archived training days
        if training_day.contest is not None:
            self.set_status(400)
            self.write({"error": "Scoreboard sharing is only available for archived training days"})
            return

        try:
            import json
            sharing_data_str = self.get_argument("scoreboard_sharing", "")

            if not sharing_data_str or sharing_data_str.strip() == "":
                # Clear sharing settings
                training_day.scoreboard_sharing = None
            else:
                sharing_data = json.loads(sharing_data_str)

                # Validate the format
                if not isinstance(sharing_data, dict):
                    raise ValueError("Invalid format: expected object")

                seen_tags: set[str] = set()
                for tag, settings in sharing_data.items():
                    normalized_tag = tag.strip()
                    if not normalized_tag:
                        raise ValueError("Tag cannot be empty")
                    if normalized_tag != tag:
                        raise ValueError(f"Invalid tag '{tag}': remove leading/trailing spaces")
                    normalized_key = normalized_tag.lower()
                    if normalized_key in seen_tags:
                        raise ValueError(f"Duplicate tag '{tag}'")
                    seen_tags.add(normalized_key)

                    if not isinstance(settings, dict):
                        raise ValueError(f"Invalid settings for tag '{tag}'")
                    if "top_names" not in settings:
                        raise ValueError(f"Missing 'top_names' for tag '{tag}'")
                    top_names = settings["top_names"]
                    if not isinstance(top_names, int) or top_names < 0:
                        raise ValueError(f"Invalid 'top_names' for tag '{tag}': must be non-negative integer")

                training_day.scoreboard_sharing = sharing_data

            if self.try_commit():
                self.write({
                    "success": True,
                    "scoreboard_sharing": training_day.scoreboard_sharing
                })
            else:
                self.set_status(500)
                self.write({"error": "Failed to save"})

        except json.JSONDecodeError as error:
            self.set_status(400)
            self.write({"error": f"Invalid JSON: {str(error)}"})
        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})
