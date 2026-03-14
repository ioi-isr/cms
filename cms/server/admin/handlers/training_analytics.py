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

"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime as dt, timedelta
from typing import Optional, Literal
from urllib.parse import urlencode

import tornado.web

from cms.db import (
    TrainingProgram,
    Student,
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

logger = logging.getLogger(__name__)


@dataclass
class FilterContext:
    """Encapsulates all filter criteria and pre-fetched DB objects."""

    start_date: Optional[dt]
    end_date: Optional[dt]
    training_day_types: list[str]
    student_tags: list[str]
    student_tags_mode: Literal["current", "historical"]

    # Pre-fetched data
    archived_training_days: list[TrainingDay]
    # IDs of students who currently possess the tags (used in 'current' mode)
    current_tag_student_ids: set[int]

    def is_visible(
        self, student_id: int, historical_tags: list[str] | None = None
    ) -> bool:
        """Determine if a student/record is visible based on filter mode."""
        if not self.student_tags:
            return True

        if self.student_tags_mode == "current":
            return student_id in self.current_tag_student_ids

        if historical_tags is None:
            return False

        return all(tag in historical_tags for tag in self.student_tags)


def _sort_students(students: dict[int, Student]) -> list[Student]:
    """Sort students by username safely."""
    return sorted(
        students.values(),
        key=lambda s: s.participation.user.username if s.participation else "",
    )


def get_attendance_view_data(ctx: FilterContext):
    """Build data structures for the Attendance view."""
    attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
    all_students: dict[int, Student] = {}

    for td in ctx.archived_training_days:
        td_rank_tags: dict[int, list[str] | None] = {}
        if ctx.student_tags and ctx.student_tags_mode == "historical":
            for rank in td.archived_student_rankings:
                if rank.student.participation and rank.student.participation.hidden:
                    continue
                td_rank_tags[rank.student_id] = rank.student_tags

        for att in td.archived_attendances:
            if att.student.participation and att.student.participation.hidden:
                continue

            if ctx.student_tags:
                if not ctx.is_visible(att.student_id, td_rank_tags.get(att.student_id)):
                    continue

            if att.student_id not in attendance_data:
                attendance_data[att.student_id] = {}
                all_students[att.student_id] = att.student

            attendance_data[att.student_id][td.id] = att

    return {
        "attendance_data": attendance_data,
        "sorted_students": _sort_students(all_students),
    }


def get_ranking_view_data(ctx: FilterContext):
    """Build data structures for the Combined Ranking view."""
    ranking_data: dict[int, dict[int, ArchivedStudentRanking]] = {}
    all_students: dict[int, Student] = {}
    training_day_tasks: dict[int, list[dict]] = {}
    filtered_training_days: list[TrainingDay] = []
    active_students_per_td: dict[int, set[int]] = {}

    for td in ctx.archived_training_days:
        active_in_td = set()
        visible_tasks_by_id = {}

        for rank in td.archived_student_rankings:
            if rank.student.participation and rank.student.participation.hidden:
                continue

            if not ctx.is_visible(rank.student_id, rank.student_tags):
                continue

            active_in_td.add(rank.student_id)

            if rank.student_id not in ranking_data:
                ranking_data[rank.student_id] = {}
                all_students[rank.student_id] = rank.student
            ranking_data[rank.student_id][td.id] = rank

            # Collect Task Metadata
            if rank.task_scores:
                archived_tasks = td.archived_tasks_data or {}
                for task_id_str in rank.task_scores.keys():
                    if task_id_str not in archived_tasks:
                        logger.warning(
                            "Missing archived task data: training_day_id=%s, task_id_str=%s, student_id=%s",
                            td.id,
                            task_id_str,
                            rank.student_id,
                        )
                        continue

                    task_id = int(task_id_str)
                    if task_id not in visible_tasks_by_id:
                        t_info = archived_tasks[task_id_str]
                        visible_tasks_by_id[task_id] = {
                            "id": task_id,
                            "name": t_info.get("short_name", ""),
                            "title": t_info.get("name", ""),
                            "training_day_num": t_info.get("training_day_num", 0),
                        }

        if active_in_td:
            active_students_per_td[td.id] = active_in_td
            filtered_training_days.append(td)
            training_day_tasks[td.id] = sorted(
                visible_tasks_by_id.values(),
                key=lambda t: (t.get("training_day_num", 0), t["id"]),
            )

    return {
        "ranking_data": ranking_data,
        "training_day_tasks": training_day_tasks,
        "filtered_training_days": filtered_training_days,
        "active_students_per_td": active_students_per_td,
        "sorted_students": _sort_students(all_students),
    }


def get_history_json_data(ctx: FilterContext) -> list[list]:
    """Build the JSON structure for the ranking history graph."""
    result = []
    for td in ctx.archived_training_days:
        for rank in td.archived_student_rankings:
            if ctx.is_visible(rank.student_id, rank.student_tags):
                if rank.history:
                    for entry in rank.history:
                        # [user_id, task_id, time, score]
                        result.append(
                            [str(entry[0]), str(entry[1]), int(entry[2]), entry[3]]
                        )
    return result


def get_student_detail_data(
    ctx: FilterContext, training_program: TrainingProgram, student: Student
):
    """Build data for the detailed student view (graphs and tables)."""

    # 1. Build User List (Left sidebar)
    # This logic is slightly complex: in historical mode, we show students
    # if they were active in ANY of the filtered training days.
    active_students_any_day = set()
    if ctx.student_tags_mode == "historical":
        for td in ctx.archived_training_days:
            for rank in td.archived_student_rankings:
                if ctx.is_visible(rank.student_id, rank.student_tags):
                    active_students_any_day.add(rank.student_id)

    users_data = {}
    for s in training_program.students:
        if not s.participation or s.participation.hidden:
            continue

        if ctx.student_tags:
            if ctx.student_tags_mode == "current":
                if s.id not in ctx.current_tag_student_ids:
                    continue
            elif s.id not in active_students_any_day:
                continue

        users_data[str(s.participation.user_id)] = {
            "f_name": s.participation.user.first_name or "",
            "l_name": s.participation.user.last_name or "",
        }

    # 2. Build Contest/Task Graph Data
    # Pre-fetch this student's specific rankings to avoid O(N^2) lookups
    student_rankings_map = {
        r.training_day_id: r
        for td in ctx.archived_training_days
        for r in td.archived_student_rankings
        if r.student_id == student.id
    }

    graph_data = _build_contest_graph_data(ctx, student_rankings_map)

    return {
        "users_data": users_data,
        "user_count": len(users_data),
        "contests_data": graph_data["contests"],
        "tasks_data": graph_data["tasks"],
        "submissions_data": graph_data["submissions"],
        "total_max_score": graph_data["total_max"],
        "contest_list": graph_data["contest_list"],
    }


def _build_contest_graph_data(
    ctx: FilterContext, student_rankings_map: dict[int, ArchivedStudentRanking]
) -> dict:
    """Helper to construct the complex contest/task dictionaries for frontend graphs."""
    contests_data = {}
    tasks_data = {}
    submissions_data = {}
    contest_list = []
    total_max_score = 0.0

    for td in ctx.archived_training_days:
        contest_key = f"td_{td.id}"
        visible_task_ids = set()

        # Find all tasks active for *any* visible student in this TD
        for rank in td.archived_student_rankings:
            if ctx.is_visible(rank.student_id, rank.student_tags):
                if rank.task_scores:
                    visible_task_ids.update(int(k) for k in rank.task_scores.keys())

        if not visible_task_ids:
            continue

        archived_tasks = td.archived_tasks_data or {}

        # Sort tasks by training_day_num
        def _get_sort_key(t_id, archived_tasks=archived_tasks):
            return (archived_tasks.get(str(t_id), {}).get("training_day_num", 0), t_id)

        sorted_ids = sorted(visible_task_ids, key=_get_sort_key)

        contest_tasks = []
        contest_max = 0.0

        for t_id in sorted_ids:
            t_key = str(t_id)
            if t_key not in archived_tasks:
                continue

            t_info = archived_tasks[t_key]
            tasks_data[t_key] = {
                **t_info,
                "key": t_key,
                "contest": contest_key,
            }
            contest_tasks.append(tasks_data[t_key])
            contest_max += t_info.get("max_score", 0)

            # Get student specific submissions
            rank = student_rankings_map.get(td.id)
            if rank and rank.submissions:
                submissions_data[t_key] = rank.submissions.get(t_key, [])

        duration = int(td.duration.total_seconds()) if td.duration else 18000
        td_name = td.description or td.name or "Training Day"
        if td.start_time:
            td_name += f" ({td.start_time.strftime('%Y-%m-%d')})"

        c_data = {
            "key": contest_key,
            "name": td_name,
            "begin": 0,
            "end": duration,
            "max_score": contest_max,
            "score_precision": 2,
            "tasks": contest_tasks,
        }
        contests_data[contest_key] = c_data
        contest_list.append(c_data)
        total_max_score += contest_max

    return {
        "contests": contests_data,
        "tasks": tasks_data,
        "submissions": submissions_data,
        "contest_list": contest_list,
        "total_max": total_max_score,
    }


class TrainingProgramFilterMixin:
    """Mixin for parsing analytics filters and context."""

    def get_filter_context(self, training_program: TrainingProgram) -> FilterContext:
        """Parse request args and build the FilterContext."""

        start_date, end_date = None, None
        if s_str := self.get_argument("start_date", None):
            try:
                start_date = dt.fromisoformat(s_str)
            except ValueError:
                pass
        if e_str := self.get_argument("end_date", None):
            try:
                end_date = dt.fromisoformat(e_str)
            except ValueError:
                pass

        types_str = self.get_argument("training_day_types", "")
        td_types = parse_tags(types_str) if types_str else []

        tags_str = self.get_argument("student_tags", "")
        s_tags = parse_tags(tags_str) if tags_str else []

        mode = self.get_argument("student_tags_mode", "current")
        if mode not in ("current", "historical"):
            mode = "current"

        query = (
            self.sql_session.query(TrainingDay)
            .filter(TrainingDay.training_program_id == training_program.id)
            .filter(TrainingDay.contest_id.is_(None))
        )
        if start_date:
            query = query.filter(TrainingDay.start_time >= start_date)
        if end_date:
            query = query.filter(TrainingDay.start_time < end_date + timedelta(days=1))
        if td_types:
            query = query.filter(TrainingDay.training_day_types.contains(td_types))

        archived_days = query.order_by(TrainingDay.start_time).all()

        current_tag_ids = set()
        if s_tags:
            sq = (
                self.sql_session.query(Student.id)
                .filter(Student.training_program_id == training_program.id)
                .filter(Student.student_tags.contains(s_tags))
            )
            current_tag_ids = {row[0] for row in sq.all()}

        return FilterContext(
            start_date=start_date,
            end_date=end_date,
            training_day_types=td_types,
            student_tags=s_tags,
            student_tags_mode=mode,
            archived_training_days=archived_days,
            current_tag_student_ids=current_tag_ids,
        )

    def set_common_params(
        self,
        training_program: TrainingProgram,
        ctx: FilterContext,
        include_historical_tags: bool = False,
        training_days_override: list[TrainingDay] | None = None,
    ):
        """Set standard render parameters from context."""
        self.render_params_for_training_program(training_program)

        # Mirror context to template
        self.r_params.update(
            {
                "start_date": ctx.start_date,
                "end_date": ctx.end_date,
                "training_day_types": ctx.training_day_types,
                "student_tags": ctx.student_tags,
                "student_tags_mode": ctx.student_tags_mode,
                "archived_training_days": training_days_override
                or ctx.archived_training_days,
                # Helper lists for dropdowns
                "all_training_day_types": get_all_training_day_types(training_program),
                "all_student_tags": get_all_student_tags(
                    self.sql_session,
                    training_program,
                    include_historical=include_historical_tags,
                ),
            }
        )


class TrainingProgramAttendanceHandler(TrainingProgramFilterMixin, BaseHandler):
    """Display attendance data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        tp = self.safe_get_item(TrainingProgram, training_program_id)
        ctx = self.get_filter_context(tp)

        view_data = get_attendance_view_data(ctx)

        self.set_common_params(tp, ctx, include_historical_tags=False)
        self.r_params.update(view_data)

        # Pending delays logic (Notification check)
        # Note: This relies on training_day_notifications set by base/decorators
        pending_list = []
        td_notif = self.r_params.get("training_day_notifications", {})
        for td in tp.training_days:
            if (
                td.contest
                and td_notif.get(td.id, {}).get("pending_delay_requests", 0) > 0
            ):
                pending_list.append(
                    {
                        "contest_id": td.contest_id,
                        "name": td.contest.name,
                        "pending_count": td_notif[td.id]["pending_delay_requests"],
                    }
                )
        self.r_params["training_days_with_pending_delays"] = pending_list

        self.render("training_program_attendance.html", **self.r_params)


class TrainingProgramCombinedRankingHandler(TrainingProgramFilterMixin, BaseHandler):
    """Display combined ranking data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        tp = self.safe_get_item(TrainingProgram, training_program_id)
        ctx = self.get_filter_context(tp)

        # We need ranking data AND basic attendance data (for student list cross-ref)
        ranking_view = get_ranking_view_data(ctx)
        attendance_view = get_attendance_view_data(ctx)

        self.set_common_params(
            tp,
            ctx,
            include_historical_tags=True,
            training_days_override=ranking_view["filtered_training_days"],
        )
        self.r_params.update(ranking_view)
        # Merge attendance data (overwrite sorted_students with the one from ranking if desired,
        # but usually they should be similar. Ranking view prioritizes ranking students).
        self.r_params["attendance_data"] = attendance_view["attendance_data"]

        self.render("training_program_combined_ranking.html", **self.r_params)


class TrainingProgramCombinedRankingHistoryHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Return score history data for combined ranking graph (JSON)."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        tp = self.safe_get_item(TrainingProgram, training_program_id)
        ctx = self.get_filter_context(tp)

        data = get_history_json_data(ctx)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(data))


class TrainingProgramCombinedRankingDetailHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Show detailed score/rank progress for a student."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, student_id: str):
        tp = self.safe_get_item(TrainingProgram, training_program_id)
        student = self.safe_get_item(Student, student_id)

        if student.training_program_id != tp.id or (
            student.participation and student.participation.hidden
        ):
            raise tornado.web.HTTPError(404)

        ctx = self.get_filter_context(tp)
        detail_data = get_student_detail_data(ctx, tp, student)

        # Build History URL
        history_url = self.url("training_program", tp.id, "combined_ranking", "history")
        params = {}
        if ctx.start_date:
            params["start_date"] = ctx.start_date.isoformat()
        if ctx.end_date:
            params["end_date"] = ctx.end_date.isoformat()
        if ctx.training_day_types:
            params["training_day_types"] = ",".join(ctx.training_day_types)
        if ctx.student_tags:
            params["student_tags"] = ",".join(ctx.student_tags)
            params["student_tags_mode"] = ctx.student_tags_mode
        if params:
            history_url += "?" + urlencode(params)

        self.set_common_params(tp, ctx, include_historical_tags=True)
        self.r_params.update(detail_data)
        self.r_params["student"] = student
        self.r_params["user_id"] = (
            str(student.participation.user_id) if student.participation else "0"
        )
        self.r_params["history_url"] = history_url

        self.render("training_program_combined_ranking_detail.html", **self.r_params)


class UpdateAttendanceHandler(BaseHandler):
    """Update attendance record (justified status, comment, and recorded)."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, attendance_id: str):
        tp = self.safe_get_item(TrainingProgram, training_program_id)
        att = self.safe_get_item(ArchivedAttendance, attendance_id)

        if att.training_day.training_program_id != tp.id:
            self.write_error_json(403, "Attendance not in this program")
            return

        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError:
            self.write_error_json(400, "Invalid JSON")
            return

        # Validate and Apply
        try:
            if "justified" in data:
                justified = bool(data["justified"])
                if justified and att.status != "missed":
                    raise ValueError("Only missed attendances can be justified")
                att.justified = justified

            if "comment" in data:
                comment = data["comment"]
                att.comment = str(comment).strip() if comment else None

            if "recorded" in data:
                recorded = bool(data["recorded"])
                if recorded and att.status == "missed":
                    raise ValueError("Only non-missed attendances can be recorded")
                att.recorded = recorded

        except ValueError as e:
            self.write_error_json(400, str(e))
            return

        if self.try_commit():
            self.write(
                {
                    "success": True,
                    "justified": att.justified,
                    "comment": att.comment,
                    "recorded": att.recorded,
                }
            )

    def write_error_json(self, status_code: int, message: str):
        self.set_status(status_code)
        self.write({"success": False, "error": message})
