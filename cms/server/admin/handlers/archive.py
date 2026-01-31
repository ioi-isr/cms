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

"""Admin handler for Training Day Archive.

This module contains the handler for archiving training days.
Analytics handlers (attendance, ranking) are in training_analytics.py.
Excel export handlers are in excel.py.
"""

from datetime import timedelta

import tornado.web

from cms.db import (
    Contest,
    TrainingProgram,
    Submission,
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
from cms.server.util import can_access_task, check_training_day_eligibility
from cms.server.admin.handlers.utils import build_user_to_student_map
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission
from .contestdelayrequest import (
    compute_participation_status,
    get_participation_main_group,
)

from .training_analytics import (
    build_attendance_data,
    build_ranking_data,
    TrainingProgramFilterMixin,
    TrainingProgramAttendanceHandler,
    TrainingProgramCombinedRankingHandler,
    TrainingProgramCombinedRankingHistoryHandler,
    TrainingProgramCombinedRankingDetailHandler,
    UpdateAttendanceHandler,
)
from .excel import (
    ExportAttendanceHandler,
    ExportCombinedRankingHandler,
    excel_build_filename,
    excel_setup_student_tags_headers,
    excel_build_training_day_title,
    excel_get_zebra_fills,
    excel_write_student_row,
    excel_write_training_day_header,
)

__all__ = [
    "ArchiveTrainingDayHandler",
    "build_attendance_data",
    "build_ranking_data",
    "TrainingProgramFilterMixin",
    "TrainingProgramAttendanceHandler",
    "TrainingProgramCombinedRankingHandler",
    "TrainingProgramCombinedRankingHistoryHandler",
    "TrainingProgramCombinedRankingDetailHandler",
    "UpdateAttendanceHandler",
    "ExportAttendanceHandler",
    "ExportCombinedRankingHandler",
    "excel_build_filename",
    "excel_setup_student_tags_headers",
    "excel_build_training_day_title",
    "excel_get_zebra_fills",
    "excel_write_student_row",
    "excel_write_training_day_header",
]


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

        # Build user_id -> Student map for O(1) lookups instead of repeated queries
        user_to_student = build_user_to_student_map(training_program)

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = user_to_student.get(participation.user_id)

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

        # Build user_id -> Student map for O(1) lookups instead of repeated queries
        user_to_student = build_user_to_student_map(training_program)

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = user_to_student.get(participation.user_id)

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
