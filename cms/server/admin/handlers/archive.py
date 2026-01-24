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

"""Admin handlers for Training Day Archive, Attendance, and Combined Ranking.

These handlers manage the archiving of training days and display of
attendance and combined ranking data across archived training days.
"""

import json
from datetime import datetime as dt, timedelta
from urllib.parse import urlencode

import tornado.web

from cms.db import (
    Contest,
    TrainingProgram,
    Participation,
    Submission,
    Question,
    Student,
    StudentTask,
    Task,
    TrainingDay,
    ArchivedAttendance,
    ArchivedStudentRanking,
    ScoreHistory,
    DelayRequest,
)
from cms.db.training_day import get_managing_participation
from cms.server.util import (
    get_all_student_tags,
    get_all_training_day_types,
    can_access_task,
    check_training_day_eligibility,
    parse_tags,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission
from .contestdelayrequest import (
    compute_participation_status,
    get_participation_main_group,
)


class ArchiveTrainingDayHandler(BaseHandler):
    """Archive a training day, extracting attendance and ranking data."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, training_day_id: str):
        """Show the archive confirmation page with IP selection."""
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404, "Training day not in this program")

        if training_day.contest is None:
            raise tornado.web.HTTPError(400, "Training day is already archived")

        contest = training_day.contest

        # Get all participations with their starting IPs
        # Count students per IP (only IPs with more than one student)
        ip_counts: dict[str, int] = {}
        for participation in contest.participations:
            if participation.starting_ip_addresses:
                # Parse comma-separated IP addresses
                ips = [ip.strip() for ip in participation.starting_ip_addresses.split(",") if ip.strip()]
                for ip in ips:
                    ip_counts[ip] = ip_counts.get(ip, 0) + 1

        # Filter to only IPs with more than one student
        shared_ips = {ip: count for ip, count in ip_counts.items() if count > 1}

        # Check if any participants can still start or are currently in contest
        # This is used to show a warning on the archive confirmation page
        users_not_finished = []
        for participation in contest.participations:
            if participation.hidden:
                continue
            is_eligible, _, _ = check_training_day_eligibility(
                self.sql_session, participation, training_day
            )
            if not is_eligible:
                continue
            main_group = get_participation_main_group(contest, participation)
            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None
            status_class, status_label = compute_participation_status(
                contest, participation, self.timestamp,
                main_group_start, main_group_end
            )
            if status_class not in ('finished', 'missed'):
                users_not_finished.append({
                    'participation': participation,
                    'status_class': status_class,
                    'status_label': status_label,
                })

        self.render_params_for_training_program(training_program)
        self.r_params["training_day"] = training_day
        self.r_params["contest"] = contest
        self.r_params["shared_ips"] = shared_ips
        self.r_params["users_not_finished"] = users_not_finished
        self.render("archive_training_day.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, training_day_id: str):
        """Perform the archiving operation."""
        fallback_page = self.url(
            "training_program", training_program_id, "training_days"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404, "Training day not in this program")

        if training_day.contest is None:
            self.service.add_notification(
                make_datetime(), "Error", "Training day is already archived"
            )
            self.redirect(fallback_page)
            return

        contest = training_day.contest

        # Get selected class IPs from form
        class_ips = set(self.get_arguments("class_ips"))

        try:
            # Save name, description, and start_time from contest before archiving
            training_day.name = contest.name
            training_day.description = contest.description
            training_day.start_time = contest.start

            # Calculate and store the training day duration
            # Use max duration among main groups (if any), or training day duration
            training_day.duration = self._calculate_training_day_duration(
                training_day, contest
            )

            # Archive attendance data for each student
            self._archive_attendance_data(training_day, contest, class_ips)

            # Archive ranking data for each student
            self._archive_ranking_data(training_day, contest)

            # Delete the contest (this will cascade delete participations)
            self.sql_session.delete(contest)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Archive failed", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Training day archived",
                f"Training day '{training_day.name}' has been archived successfully"
            )

        self.redirect(fallback_page)

    def _calculate_training_day_duration(
        self,
        training_day: TrainingDay,
        contest: Contest
    ) -> timedelta | None:
        """Calculate the training day duration for archiving.

        Returns the max training duration among main groups (if any),
        or the training day duration (if no main groups).

        training_day: the training day being archived.
        contest: the contest associated with the training day.

        return: the duration as a timedelta, or None if not calculable.
        """
        # Check if there are main groups with custom timing
        main_groups = training_day.groups
        if main_groups:
            # Calculate max duration among main groups
            max_duration: timedelta | None = None
            for group in main_groups:
                if group.start_time is not None and group.end_time is not None:
                    group_duration = group.end_time - group.start_time
                    if max_duration is None or group_duration > max_duration:
                        max_duration = group_duration
            if max_duration is not None:
                return max_duration

        # Fall back to training day (contest) duration
        if contest.start is not None and contest.stop is not None:
            return contest.stop - contest.start

        return None

    def _archive_attendance_data(
        self,
        training_day: TrainingDay,
        contest: Contest,
        class_ips: set[str]
    ) -> None:
        """Extract and store attendance data for all students."""
        training_program = training_day.training_program

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = (
                self.sql_session.query(Student)
                .join(Participation)
                .filter(Participation.user_id == participation.user_id)
                .filter(Student.training_program_id == training_program.id)
                .first()
            )

            if student is None:
                continue

            # Skip ineligible students (not in any main group)
            # These students were never supposed to participate in this training day
            is_eligible, _, _ = check_training_day_eligibility(
                self.sql_session, participation, training_day
            )
            if not is_eligible:
                continue

            # Determine status
            if participation.starting_time is None:
                status = "missed"
            else:
                status = "participated"

            # Determine location based on starting IPs
            # If no class IPs were selected, everyone who participated is considered "home"
            location = None
            if status == "participated":
                if not class_ips:
                    # No class IPs selected means everyone is at home
                    location = "home"
                elif participation.starting_ip_addresses:
                    # Parse comma-separated IP addresses
                    ips = [ip.strip() for ip in participation.starting_ip_addresses.split(",") if ip.strip()]
                    if ips:
                        has_class_ip = any(ip in class_ips for ip in ips)
                        has_home_ip = any(ip not in class_ips for ip in ips)

                        if has_class_ip and has_home_ip:
                            location = "both"
                        elif has_class_ip:
                            location = "class"
                        elif has_home_ip:
                            location = "home"
                    else:
                        # Participated but no IP recorded - assume home
                        location = "home"
                else:
                    # Participated but no IP recorded - assume home
                    location = "home"

            # Get delay time
            delay_time = participation.delay_time

            # Concatenate delay reasons from all delay requests
            delay_requests = (
                self.sql_session.query(DelayRequest)
                .filter(DelayRequest.participation_id == participation.id)
                .order_by(DelayRequest.request_timestamp)
                .all()
            )
            delay_reasons = None
            if delay_requests:
                reasons = [dr.reason for dr in delay_requests if dr.reason]
                if reasons:
                    delay_reasons = "; ".join(reasons)

            # Create archived attendance record
            archived_attendance = ArchivedAttendance(
                status=status,
                location=location,
                delay_time=delay_time,
                delay_reasons=delay_reasons,
            )
            archived_attendance.training_day_id = training_day.id
            archived_attendance.student_id = student.id
            self.sql_session.add(archived_attendance)

    def _archive_ranking_data(
        self,
        training_day: TrainingDay,
        contest: Contest
    ) -> None:
        """Extract and store ranking data for all students.

        Stores on TrainingDay:
        - archived_tasks_data: task metadata including extra_headers for submission table

        Stores on ArchivedStudentRanking (per student):
        - task_scores: scores for ALL visible tasks (including 0 scores)
          The presence of a task_id key indicates the task was visible.
        - submissions: submission data for each task in RWS format
        - history: score history in RWS format
        """
        from cms.grading.scorecache import get_cached_score_entry

        training_program = training_day.training_program

        # Get the tasks assigned to this training day
        training_day_tasks = training_day.tasks
        training_day_task_ids = {task.id for task in training_day_tasks}

        # Build and store tasks_data on the training day (same for all students)
        # This preserves the scoring scheme as it was during the training day
        archived_tasks_data: dict[str, dict] = {}
        for task in training_day_tasks:
            max_score = 100.0
            extra_headers: list[str] = []
            score_precision = task.score_precision
            if task.active_dataset:
                try:
                    score_type = task.active_dataset.score_type_object
                    max_score = score_type.max_score
                    extra_headers = score_type.ranking_headers
                except (KeyError, TypeError, AttributeError):
                    pass

            archived_tasks_data[str(task.id)] = {
                "name": task.title,
                "short_name": task.name,
                "max_score": max_score,
                "score_precision": score_precision,
                "extra_headers": extra_headers,
                "training_day_num": task.training_day_num,
            }
        training_day.archived_tasks_data = archived_tasks_data

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = (
                self.sql_session.query(Student)
                .join(Participation)
                .filter(Participation.user_id == participation.user_id)
                .filter(Student.training_program_id == training_program.id)
                .first()
            )

            if student is None:
                continue

            # Skip ineligible students (not in any main group)
            # These students were never supposed to participate in this training day
            is_eligible, _, _ = check_training_day_eligibility(
                self.sql_session, participation, training_day
            )
            if not is_eligible:
                continue

            # Get all student tags (as list for array storage)
            student_tags = list(student.student_tags) if student.student_tags else []

            # Determine which tasks should be visible to this student based on their tags
            # This uses the same logic as _add_training_day_tasks_to_student in StartHandler
            # A task is visible if:
            # - The task has no visible_to_tags (empty list = visible to all)
            # - The student has at least one tag matching the task's visible_to_tags
            visible_tasks: list[Task] = []
            for task in training_day_tasks:
                if can_access_task(self.sql_session, task, participation, training_day):
                    visible_tasks.append(task)

            # Add visible tasks to student's StudentTask records if not already present
            # This allows students who missed the training to still submit from home
            existing_task_ids = {st.task_id for st in student.student_tasks}
            for task in visible_tasks:
                if task.id not in existing_task_ids:
                    student_task = StudentTask(assigned_at=make_datetime())
                    student_task.student_id = student.id
                    student_task.task_id = task.id
                    student_task.source_training_day_id = training_day.id
                    self.sql_session.add(student_task)

            # Get the managing participation for this user
            # Submissions are stored with the managing contest participation, not the
            # training day participation
            managing_participation = get_managing_participation(
                self.sql_session, training_day, participation.user
            )
            if managing_participation is None:
                raise ValueError(
                    f"User {participation.user.username} (id={participation.user_id}) "
                    f"does not have a participation in the managing contest "
                    f"'{training_day.training_program.managing_contest.name}' "
                    f"for training day '{training_day.name}'"
                )

            # Check if student missed the training (no starting_time)
            student_missed = participation.starting_time is None

            # Get task scores for ALL visible tasks (including 0 scores)
            # The presence of a task_id key indicates the task was visible
            task_scores: dict[str, float] = {}
            submissions: dict[str, list[dict]] = {}

            for task in visible_tasks:
                task_id = task.id

                if student_missed:
                    # Student missed the training - set score to 0
                    task_scores[str(task_id)] = 0.0
                else:
                    # Get score from the training day participation (for cache lookup)
                    cache_entry = get_cached_score_entry(
                        self.sql_session, participation, task
                    )
                    task_scores[str(task_id)] = cache_entry.score

                # Get official submissions for this task from the managing participation
                task_submissions = (
                    self.sql_session.query(Submission)
                    .filter(Submission.participation_id == managing_participation.id)
                    .filter(Submission.task_id == task_id)
                    .filter(Submission.training_day_id == training_day.id)
                    .filter(Submission.official.is_(True))
                    .order_by(Submission.timestamp)
                    .all()
                )

                # If student missed but has submissions, this is an error
                if student_missed and task_submissions:
                    raise ValueError(
                        f"User {participation.user.username} (id={participation.user_id}) "
                        f"has no starting_time but has {len(task_submissions)} submission(s) "
                        f"for task '{task.name}' in training day '{training_day.name}'"
                    )

                submissions[str(task_id)] = []
                for sub in task_submissions:
                    result = sub.get_result()
                    if result is None or not result.scored():
                        continue

                    if sub.timestamp is not None:
                        time_offset = int(
                            (
                                sub.timestamp - participation.starting_time
                            ).total_seconds()
                        )
                    else:
                        time_offset = 0

                    submissions[str(task_id)].append({
                        "task": str(task_id),
                        "time": time_offset,
                        "score": result.score,
                        "token": sub.tokened(),
                        "extra": result.ranking_score_details or [],
                    })

            # Get score history in RWS format: [[user_id, task_id, time, score], ...]
            # Score history is stored on the training day participation
            history: list[list] = []
            score_histories = (
                self.sql_session.query(ScoreHistory)
                .filter(ScoreHistory.participation_id == participation.id)
                .filter(ScoreHistory.task_id.in_(training_day_task_ids))
                .order_by(ScoreHistory.timestamp)
                .all()
            )

            # If student missed but has score history, this is an error
            if student_missed and score_histories:
                raise ValueError(
                    f"User {participation.user.username} (id={participation.user_id}) "
                    f"has no starting_time but has {len(score_histories)} score history "
                    f"record(s) in training day '{training_day.name}'"
                )

            for sh in score_histories:
                if sh.timestamp is not None:
                    time_offset = (
                        sh.timestamp - participation.starting_time
                    ).total_seconds()
                else:
                    time_offset = 0
                history.append([
                    participation.user_id,
                    sh.task_id,
                    time_offset,
                    sh.score
                ])

            # Create archived ranking record
            archived_ranking = ArchivedStudentRanking(
                student_tags=student_tags,
                task_scores=task_scores if task_scores else None,
                submissions=submissions if submissions else None,
                history=history if history else None,
            )
            archived_ranking.training_day_id = training_day.id
            archived_ranking.student_id = student.id
            self.sql_session.add(archived_ranking)


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

    def _get_student_ids_with_tags(self, students, filter_tags: list[str]) -> set[int]:
        """Return IDs of students that have all filter_tags."""
        return {s.id for s in students if self._tags_match(s.student_tags, filter_tags)}

    def _get_filtered_context(self, training_program):
        """Parse common arguments and retrieve archived training days."""
        start_date, end_date = self._parse_date_range()
        training_day_types = self._parse_training_day_types()
        student_tags, student_tags_mode = self._parse_student_tags_filter()

        archived_training_days = self._get_archived_training_days(
            training_program.id, start_date, end_date, training_day_types
        )

        # Build a set of students with matching current tags
        current_tag_student_ids = self._get_student_ids_with_tags(
            training_program.students, student_tags
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

        # Build attendance data structure
        # {student_id: {training_day_id: attendance_record}}
        attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
        all_students: dict[int, Student] = {}

        for td in archived_training_days:
            for attendance in td.archived_attendances:
                student_id = attendance.student_id
                # Apply student tag filter (current tags only)
                if student_tags and student_id not in current_tag_student_ids:
                    continue
                # Skip hidden users
                student = attendance.student
                if student.participation and student.participation.hidden:
                    continue
                if student_id not in attendance_data:
                    attendance_data[student_id] = {}
                    all_students[student_id] = student
                attendance_data[student_id][td.id] = attendance

        # Sort students by username
        sorted_students = sorted(
            all_students.values(),
            key=lambda s: s.participation.user.username if s.participation else ""
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
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)

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

        ranking_data: dict[int, dict[int, ArchivedStudentRanking]] = {}
        all_students: dict[int, Student] = {}
        training_day_tasks: dict[int, list[dict]] = {}
        # Attendance data: {student_id: {training_day_id: ArchivedAttendance}}
        attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
        # Track which students are "active" (have matching tags) for each training day
        # For historical mode: student had matching tags during that training
        # For current mode: student has matching tags now AND participated in that training
        active_students_per_td: dict[int, set[int]] = {}

        filtered_training_days: list[TrainingDay] = []

        for td in archived_training_days:
            active_students_per_td[td.id] = set()

            # Build attendance lookup for this training day
            for attendance in td.archived_attendances:
                student_id = attendance.student_id
                if student_id not in attendance_data:
                    attendance_data[student_id] = {}
                attendance_data[student_id][td.id] = attendance

            # Collect all tasks that were visible to at least one filtered student
            # Use archived_tasks_data from training day (preserves original scoring scheme)
            visible_tasks_by_id: dict[int, dict] = {}
            for ranking in td.archived_student_rankings:
                student_id = ranking.student_id

                # Skip hidden users
                student = ranking.student
                if student.participation and student.participation.hidden:
                    continue

                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        # Filter by current tags: student must have matching tags now
                        if student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        # Filter by historical tags: student must have had matching tags
                        # during this specific training day
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue

                # Student passes the filter for this training day
                active_students_per_td[td.id].add(student_id)

                if student_id not in ranking_data:
                    ranking_data[student_id] = {}
                    all_students[student_id] = student
                ranking_data[student_id][td.id] = ranking

                # Collect all visible tasks from this student's task_scores keys
                if ranking.task_scores:
                    for task_id_str in ranking.task_scores.keys():
                        task_id = int(task_id_str)
                        if task_id not in visible_tasks_by_id:
                            # Get task info from archived_tasks_data on training day
                            if td.archived_tasks_data and task_id_str in td.archived_tasks_data:
                                task_info = td.archived_tasks_data[task_id_str]
                                visible_tasks_by_id[task_id] = {
                                    "id": task_id,
                                    "name": task_info.get("short_name", ""),
                                    "title": task_info.get("name", ""),
                                    "training_day_num": task_info.get("training_day_num"),
                                }
                            else:
                                # Fallback to live task data
                                task = self.sql_session.query(Task).get(task_id)
                                if task:
                                    visible_tasks_by_id[task_id] = {
                                        "id": task_id,
                                        "name": task.name,
                                        "title": task.title,
                                        "training_day_num": task.training_day_num,
                                    }

            # Omit training days where no filtered students were eligible
            if not active_students_per_td[td.id]:
                continue

            filtered_training_days.append(td)

            # Sort tasks by training_day_num for stable ordering
            sorted_tasks = sorted(
                visible_tasks_by_id.values(),
                key=lambda t: (t.get("training_day_num") or 0, t["id"])
            )
            training_day_tasks[td.id] = sorted_tasks

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
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)
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
