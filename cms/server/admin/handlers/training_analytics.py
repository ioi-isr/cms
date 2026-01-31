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

"""Admin handlers for Training Program Analytics.

This module contains handlers for displaying attendance and combined ranking
analytics across archived training days.

Functions:
- build_attendance_data: Build attendance data structure from archived days
- build_ranking_data: Build ranking data structure from archived days

Classes:
- TrainingProgramFilterMixin: Mixin for filtering training days
- TrainingProgramAttendanceHandler: Display attendance data
- TrainingProgramCombinedRankingHandler: Display combined ranking
- TrainingProgramCombinedRankingHistoryHandler: Return score history data
- TrainingProgramCombinedRankingDetailHandler: Show detailed progress
- UpdateAttendanceHandler: Update attendance records
"""

import json
from datetime import datetime as dt, timedelta
from typing import Any
from urllib.parse import urlencode

import tornado.web

from cms.db import (
    TrainingProgram,
    Student,
    Task,
    TrainingDay,
    ArchivedAttendance,
    ArchivedStudentRanking,
)
from cms.server.admin.handlers.utils import (
    get_all_student_tags,
    get_all_training_day_types,
    parse_tags,
)

from .base import BaseHandler, require_permission


def build_attendance_data(
    archived_training_days: list[Any],
    student_tags: list[str],
    current_tag_student_ids: set[int],
) -> tuple[dict[int, dict[int, ArchivedAttendance]], dict[int, Student], list[Student]]:
    """Build attendance data structure from archived training days.

    archived_training_days: list of archived TrainingDay objects.
    student_tags: list of student tags to filter by (empty = no filter).
    current_tag_student_ids: set of student IDs that have the filter tags.

    return: tuple of (attendance_data, all_students, sorted_students) where:
        - attendance_data: {student_id: {training_day_id: ArchivedAttendance}}
        - all_students: {student_id: Student}
        - sorted_students: list of Student objects sorted by username
    """
    attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
    all_students: dict[int, Student] = {}

    for td in archived_training_days:
        for attendance in td.archived_attendances:
            student_id = attendance.student_id
            if student_tags and student_id not in current_tag_student_ids:
                continue
            student = attendance.student
            if student.participation and student.participation.hidden:
                continue
            if student_id not in attendance_data:
                attendance_data[student_id] = {}
                all_students[student_id] = student
            attendance_data[student_id][td.id] = attendance

    sorted_students = sorted(
        all_students.values(),
        key=lambda s: s.participation.user.username if s.participation else ""
    )

    return attendance_data, all_students, sorted_students


def build_ranking_data(
    sql_session: Any,
    archived_training_days: list[Any],
    student_tags: list[str],
    student_tags_mode: str,
    current_tag_student_ids: set[int],
    tags_match_fn: Any,
) -> tuple[
    dict[int, dict[int, ArchivedStudentRanking]],
    dict[int, Student],
    dict[int, list[dict]],
    list[Any],
    dict[int, set[int]],
]:
    """Build ranking data structure from archived training days.

    sql_session: the database session.
    archived_training_days: list of archived TrainingDay objects.
    student_tags: list of student tags to filter by (empty = no filter).
    student_tags_mode: "current" or "historical" for tag filtering.
    current_tag_student_ids: set of student IDs that have the filter tags.
    tags_match_fn: function to check if item_tags contains all filter_tags.

    return: tuple of (ranking_data, all_students, training_day_tasks,
                      filtered_training_days, active_students_per_td) where:
        - ranking_data: {student_id: {training_day_id: ArchivedStudentRanking}}
        - all_students: {student_id: Student}
        - training_day_tasks: {training_day_id: [task_info_dict, ...]}
        - filtered_training_days: list of TrainingDay objects with data
        - active_students_per_td: {training_day_id: set of active student IDs}
    """
    ranking_data: dict[int, dict[int, ArchivedStudentRanking]] = {}
    all_students: dict[int, Student] = {}
    training_day_tasks: dict[int, list[dict]] = {}
    filtered_training_days: list[Any] = []
    active_students_per_td: dict[int, set[int]] = {}

    for td in archived_training_days:
        active_students_per_td[td.id] = set()
        visible_tasks_by_id: dict[int, dict] = {}

        for ranking in td.archived_student_rankings:
            student_id = ranking.student_id
            student = ranking.student

            if student.participation and student.participation.hidden:
                continue

            if student_tags:
                if student_tags_mode == "current":
                    if student_id not in current_tag_student_ids:
                        continue
                else:
                    if not tags_match_fn(ranking.student_tags, student_tags):
                        continue

            active_students_per_td[td.id].add(student_id)

            if student_id not in ranking_data:
                ranking_data[student_id] = {}
                all_students[student_id] = student
            ranking_data[student_id][td.id] = ranking

            if ranking.task_scores:
                for task_id_str in ranking.task_scores.keys():
                    task_id = int(task_id_str)
                    if task_id not in visible_tasks_by_id:
                        if (td.archived_tasks_data and
                                task_id_str in td.archived_tasks_data):
                            task_info = td.archived_tasks_data[task_id_str]
                            visible_tasks_by_id[task_id] = {
                                "id": task_id,
                                "name": task_info.get("short_name", ""),
                                "title": task_info.get("name", ""),
                                "training_day_num": task_info.get(
                                    "training_day_num"
                                ),
                            }
                        else:
                            task = sql_session.query(Task).get(task_id)
                            if task:
                                visible_tasks_by_id[task_id] = {
                                    "id": task_id,
                                    "name": task.name,
                                    "title": task.title,
                                    "training_day_num": task.training_day_num,
                                }

        if not active_students_per_td[td.id]:
            continue

        filtered_training_days.append(td)
        sorted_tasks = sorted(
            visible_tasks_by_id.values(),
            key=lambda t: (t.get("training_day_num") or 0, t["id"])
        )
        training_day_tasks[td.id] = sorted_tasks

    return (
        ranking_data,
        all_students,
        training_day_tasks,
        filtered_training_days,
        active_students_per_td,
    )


class TrainingProgramFilterMixin:
    """Mixin for filtering training days by date range, types, and student tags."""

    def _parse_date_range(self) -> tuple[dt | None, dt | None]:
        """Parse start_date and end_date query arguments."""
        start_date = None
        end_date = None
        start_str = self.get_argument("start_date", None)
        end_str = self.get_argument("end_date", None)

        if start_str:
            try:
                start_date = dt.fromisoformat(start_str)
            except ValueError:
                pass

        if end_str:
            try:
                end_date = dt.fromisoformat(end_str)
            except ValueError:
                pass

        return start_date, end_date

    def _parse_training_day_types(self) -> list[str]:
        """Parse training_day_types query argument."""
        types_str = self.get_argument("training_day_types", "")
        if not types_str:
            return []
        return parse_tags(types_str)

    def _parse_student_tags_filter(self) -> tuple[list[str], str]:
        """Parse student_tags and student_tags_mode query arguments.

        Returns:
            tuple of (student_tags list, filter_mode string)
            filter_mode is either "current" or "historical"
        """
        tags_str = self.get_argument("student_tags", "")
        mode = self.get_argument("student_tags_mode", "current")
        if mode not in ("current", "historical"):
            mode = "current"
        if not tags_str:
            return [], mode
        return parse_tags(tags_str), mode

    def _get_archived_training_days(
        self,
        training_program_id: int,
        start_date: dt | None,
        end_date: dt | None,
        training_day_types: list[str] | None = None,
    ) -> list[TrainingDay]:
        """Query archived training days with optional date and type filtering."""
        query = (
            self.sql_session.query(TrainingDay)
            .filter(TrainingDay.training_program_id == training_program_id)
            .filter(TrainingDay.contest_id.is_(None))
        )
        if start_date:
            query = query.filter(TrainingDay.start_time >= start_date)
        if end_date:
            # Add one day to end_date to include the entire end day
            query = query.filter(TrainingDay.start_time < end_date + timedelta(days=1))
        if training_day_types:
            # Filter training days that have ALL specified types
            query = query.filter(
                TrainingDay.training_day_types.contains(training_day_types)
            )
        return query.order_by(TrainingDay.start_time).all()

    def _tags_match(self, item_tags: list[str] | None, filter_tags: list[str]) -> bool:
        """Check if item_tags contains all filter_tags."""
        return all(tag in (item_tags or []) for tag in filter_tags)

    def _get_student_ids_with_tags(
        self, training_program_id: int, filter_tags: list[str]
    ) -> set[int]:
        """Return IDs of students that have all filter_tags.

        Uses GIN index on student_tags for efficient querying.
        """
        if not filter_tags:
            return set()

        query = (
            self.sql_session.query(Student.id)
            .filter(Student.training_program_id == training_program_id)
            .filter(Student.student_tags.contains(filter_tags))
        )
        return {row[0] for row in query.all()}

    def _get_filtered_context(self, training_program):
        """Parse common arguments and retrieve archived training days."""
        start_date, end_date = self._parse_date_range()
        training_day_types = self._parse_training_day_types()
        student_tags, student_tags_mode = self._parse_student_tags_filter()

        archived_training_days = self._get_archived_training_days(
            training_program.id, start_date, end_date, training_day_types
        )

        # Build a set of students with matching current tags using GIN index
        current_tag_student_ids = self._get_student_ids_with_tags(
            training_program.id, student_tags
        )

        return (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        )


class TrainingProgramAttendanceHandler(TrainingProgramFilterMixin, BaseHandler):
    """Display attendance data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            _,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        attendance_data, _, sorted_students = build_attendance_data(
            archived_training_days, student_tags, current_tag_student_ids
        )

        self.render_params_for_training_program(training_program)
        self.r_params["archived_training_days"] = archived_training_days
        self.r_params["attendance_data"] = attendance_data
        self.r_params["sorted_students"] = sorted_students
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)
        self.r_params["all_student_tags"] = get_all_student_tags(
            self.sql_session, training_program
        )

        # Build training days with pending delays from notification data
        training_days_with_pending_delays: list[dict] = []
        td_notifications = self.r_params.get("training_day_notifications", {})
        for td in training_program.training_days:
            if td.contest is None:
                continue
            td_notif = td_notifications.get(td.id, {})
            pending_count = td_notif.get("pending_delay_requests", 0)
            if pending_count > 0:
                training_days_with_pending_delays.append({
                    "contest_id": td.contest_id,
                    "name": td.contest.name,
                    "pending_count": pending_count,
                })
        self.r_params["training_days_with_pending_delays"] = \
            training_days_with_pending_delays

        self.render("training_program_attendance.html", **self.r_params)


class TrainingProgramCombinedRankingHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Display combined ranking data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        (
            ranking_data,
            all_students,
            training_day_tasks,
            filtered_training_days,
            active_students_per_td,
        ) = build_ranking_data(
            self.sql_session,
            archived_training_days,
            student_tags,
            student_tags_mode,
            current_tag_student_ids,
            self._tags_match,
        )

        # Build attendance lookup for all training days
        attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
        for td in archived_training_days:
            for attendance in td.archived_attendances:
                student_id = attendance.student_id
                if student_id not in attendance_data:
                    attendance_data[student_id] = {}
                attendance_data[student_id][td.id] = attendance

        sorted_students = sorted(
            all_students.values(),
            key=lambda s: s.participation.user.username if s.participation else ""
        )

        self.render_params_for_training_program(training_program)
        self.r_params["archived_training_days"] = filtered_training_days
        self.r_params["ranking_data"] = ranking_data
        self.r_params["sorted_students"] = sorted_students
        self.r_params["training_day_tasks"] = training_day_tasks
        self.r_params["attendance_data"] = attendance_data
        self.r_params["active_students_per_td"] = active_students_per_td
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["student_tags_mode"] = student_tags_mode
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)
        self.r_params["all_student_tags"] = get_all_student_tags(
            self.sql_session, training_program, include_historical=True
        )
        self.render("training_program_combined_ranking.html", **self.r_params)


class TrainingProgramCombinedRankingHistoryHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Return score history data for combined ranking graph."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            _,
            _,
            _,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        # Build history data in RWS format: [[user_id, task_id, time, score], ...]
        result: list[list] = []

        for td in archived_training_days:
            for ranking in td.archived_student_rankings:
                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        if ranking.student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue

                if ranking.history:
                    for entry in ranking.history:
                        result.append([
                            str(entry[0]),
                            str(entry[1]),
                            int(entry[2]),
                            entry[3]
                        ])

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))


class TrainingProgramCombinedRankingDetailHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Show detailed score/rank progress for a student across archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, student_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        student = self.safe_get_item(Student, student_id)
        if student.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404)
        if student.participation and student.participation.hidden:
            raise tornado.web.HTTPError(404)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        # For historical mode, we need to track which students are active per training day
        # to compute the correct user_count for relative ranks
        active_students_per_td: dict[int, set[int]] = {}
        if student_tags and student_tags_mode == "historical":
            for td in archived_training_days:
                active_students_per_td[td.id] = set()
                for ranking in td.archived_student_rankings:
                    student_obj = ranking.student
                    if (
                        student_obj
                        and student_obj.participation
                        and student_obj.participation.hidden
                    ):
                        continue
                    if self._tags_match(ranking.student_tags, student_tags):
                        active_students_per_td[td.id].add(ranking.student_id)

        # Build users_data for filtered students only
        users_data = {}
        filtered_student_ids: set[int] = set()
        for s in training_program.students:
            if s.participation and s.participation.user:
                if s.participation.hidden:
                    continue
                # Apply student tag filter for current mode
                if student_tags and student_tags_mode == "current":
                    if s.id not in current_tag_student_ids:
                        continue
                # For historical mode, include student if they appear in any training day
                elif student_tags and student_tags_mode == "historical":
                    if not any(s.id in active_students_per_td.get(td.id, set())
                               for td in archived_training_days):
                        continue
                filtered_student_ids.add(s.id)
                users_data[str(s.participation.user_id)] = {
                    "f_name": s.participation.user.first_name or "",
                    "l_name": s.participation.user.last_name or "",
                }

        user_count = len(users_data)

        contests_data: dict[str, dict] = {}
        tasks_data: dict[str, dict] = {}
        submissions_data: dict[str, list] = {}
        total_max_score = 0.0

        # Find the student's ranking records to get their submissions
        student_rankings: dict[int, ArchivedStudentRanking] = {}
        for td in archived_training_days:
            for ranking in td.archived_student_rankings:
                if ranking.student_id == student.id:
                    student_rankings[td.id] = ranking
                    break

        for td in archived_training_days:
            contest_key = f"td_{td.id}"
            task_ids_in_contest: set[int] = set()

            # Collect all visible task IDs from filtered students' task_scores keys
            for ranking in td.archived_student_rankings:
                student_obj = ranking.student
                if (
                    student_obj
                    and student_obj.participation
                    and student_obj.participation.hidden
                ):
                    continue
                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        if ranking.student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue
                if ranking.task_scores:
                    task_ids_in_contest.update(int(k) for k in ranking.task_scores.keys())

            # Get archived_tasks_data from training day
            archived_tasks_data = td.archived_tasks_data or {}

            # Sort task IDs by training_day_num for stable ordering
            # Use default argument to capture archived_tasks_data by value
            def get_training_day_num(
                task_id: int,
                _tasks_data: dict = archived_tasks_data
            ) -> tuple[int, int]:
                task_key = str(task_id)
                if task_key in _tasks_data:
                    num = _tasks_data[task_key].get("training_day_num")
                    return (num if num is not None else 0, task_id)
                return (0, task_id)

            sorted_task_ids = sorted(task_ids_in_contest, key=get_training_day_num)

            contest_tasks = []
            contest_max_score = 0.0
            for task_id in sorted_task_ids:
                task_key = str(task_id)

                # Use archived_tasks_data if available (preserves original scoring scheme)
                if task_key in archived_tasks_data:
                    task_info = archived_tasks_data[task_key]
                    max_score = task_info.get("max_score", 100.0)
                    extra_headers = task_info.get("extra_headers", [])
                    score_precision = task_info.get("score_precision", 2)
                    task_name = task_info.get("name", "")
                    task_short_name = task_info.get("short_name", "")
                else:
                    # Fallback to live task data
                    task = self.sql_session.query(Task).get(task_id)
                    if not task:
                        continue
                    max_score = 100.0
                    extra_headers = []
                    score_precision = task.score_precision
                    task_name = task.title
                    task_short_name = task.name
                    if task.active_dataset:
                        try:
                            score_type = task.active_dataset.score_type_object
                            max_score = score_type.max_score
                            extra_headers = score_type.ranking_headers
                        except (KeyError, TypeError, AttributeError):
                            pass

                tasks_data[task_key] = {
                    "key": task_key,
                    "name": task_name,
                    "short_name": task_short_name,
                    "contest": contest_key,
                    "max_score": max_score,
                    "score_precision": score_precision,
                    "extra_headers": extra_headers,
                }
                contest_tasks.append(tasks_data[task_key])
                contest_max_score += max_score

                # Get submissions for this task from the student's ranking
                student_ranking = student_rankings.get(td.id)
                if student_ranking and student_ranking.submissions:
                    task_submissions = student_ranking.submissions.get(task_key, [])
                    submissions_data[task_key] = task_submissions

            td_name = td.description or td.name or "Training Day"
            if td.start_time:
                td_name += f" ({td.start_time.strftime('%Y-%m-%d')})"

            # Calculate contest duration
            # History times are stored as offsets from contest start, so we need
            # begin=0 and end=duration for the graph scale to be correct
            if td.duration:
                end_time = int(td.duration.total_seconds())
            else:
                end_time = 18000  # Default 5 hours

            contests_data[contest_key] = {
                "key": contest_key,
                "name": td_name,
                "begin": 0,
                "end": end_time,
                "max_score": contest_max_score,
                "score_precision": 2,
                "tasks": contest_tasks,
            }
            total_max_score += contest_max_score

        contest_list = [contests_data[f"td_{td.id}"] for td in archived_training_days
                        if f"td_{td.id}" in contests_data]

        history_url = self.url(
            "training_program", training_program_id, "combined_ranking", "history"
        )
        if start_date or end_date or training_day_types or student_tags:
            params = {}
            if start_date:
                params["start_date"] = start_date.isoformat()
            if end_date:
                params["end_date"] = end_date.isoformat()
            if training_day_types:
                params["training_day_types"] = ",".join(training_day_types)
            if student_tags:
                params["student_tags"] = ",".join(student_tags)
                params["student_tags_mode"] = student_tags_mode
            history_url += "?" + urlencode(params)

        self.render_params_for_training_program(training_program)
        self.r_params["student"] = student
        self.r_params["user_id"] = str(student.participation.user_id) if student.participation else "0"
        self.r_params["user_count"] = user_count
        self.r_params["users_data"] = users_data
        self.r_params["tasks_data"] = tasks_data
        self.r_params["submissions_data"] = submissions_data
        self.r_params["contests_data"] = contests_data
        self.r_params["contest_list"] = contest_list
        self.r_params["total_max_score"] = total_max_score
        self.r_params["history_url"] = history_url
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["student_tags_mode"] = student_tags_mode
        self.render("training_program_combined_ranking_detail.html", **self.r_params)


class UpdateAttendanceHandler(BaseHandler):
    """Update attendance record (justified status, comment, and recorded)."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, attendance_id: str):
        """Update an attendance record's justified status, comment, and/or recorded."""
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        attendance = self.safe_get_item(ArchivedAttendance, attendance_id)

        # Verify the attendance belongs to this training program
        if attendance.training_day.training_program_id != training_program.id:
            self.set_status(403)
            self.write({"success": False, "error": "Attendance not in this program"})
            return

        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"success": False, "error": "Invalid JSON"})
            return

        try:
            if "justified" in data:
                justified = data["justified"]
                if not isinstance(justified, bool):
                    self.set_status(400)
                    self.write({"success": False, "error": "Invalid justified flag"})
                    return
                if justified and attendance.status != "missed":
                    self.set_status(400)
                    self.write(
                        {
                            "success": False,
                            "error": "Only missed attendances can be justified",
                        }
                    )
                    return
                attendance.justified = justified

            if "comment" in data:
                comment = data["comment"]
                if comment is not None:
                    comment = str(comment).strip()
                    if not comment:
                        comment = None
                attendance.comment = comment

            if "recorded" in data:
                recorded = data["recorded"]
                if not isinstance(recorded, bool):
                    self.set_status(400)
                    self.write({"success": False, "error": "Invalid recorded flag"})
                    return
                if recorded and attendance.status == "missed":
                    self.set_status(400)
                    self.write(
                        {
                            "success": False,
                            "error": "Only non-missed attendances can be marked as recorded",
                        }
                    )
                    return
                attendance.recorded = recorded

            if self.try_commit():
                self.write({
                    "success": True,
                    "justified": attendance.justified,
                    "comment": attendance.comment,
                    "recorded": attendance.recorded,
                })
            else:
                self.set_status(500)
                self.write({"success": False, "error": "Failed to save changes"})

        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})
