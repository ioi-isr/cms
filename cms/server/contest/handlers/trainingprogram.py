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

"""Training program handlers for CWS.

This module provides handlers for training programs in the contest web server,
including the overview page and training days page.
"""

from datetime import timedelta

import tornado.web
from sqlalchemy import func

from cms.db import Participation, Student, ArchivedStudentRanking, Submission, Task, TrainingDay
from cms.grading.scorecache import get_cached_score_entry
from cms.server import multi_contest
from cms.server.contest.phase_management import compute_actual_phase, compute_effective_times
from cms.server.util import check_training_day_eligibility, calculate_task_archive_progress
from .contest import ContestHandler


class TrainingProgramOverviewHandler(ContestHandler):
    """Training program overview page handler.

    Shows the training program overview with total score, percentage,
    and task list. This is a minimal implementation for Phase 1.
    """

    @tornado.web.authenticated
    @multi_contest
    def get(self):
        participation: Participation = self.current_user
        contest = self.contest

        # Use self.training_program which was set by choose_contest() during routing.
        # This is the canonical source of truth for whether we're accessing via a
        # training program URL. Don't redirect to contest_url() as that would cause
        # a redirect loop with MainHandler.
        training_program = self.training_program
        if training_program is None:
            # This URL only makes sense for training programs; return 404
            raise tornado.web.HTTPError(404)

        # Find the student record for this user in the training program
        student = (
            self.sql_session.query(Student)
            .join(Participation, Student.participation_id == Participation.id)
            .filter(Participation.contest_id == contest.id)
            .filter(Participation.user_id == participation.user_id)
            .filter(Student.training_program_id == training_program.id)
            .first()
        )

        # Calculate task archive progress using shared utility
        if student is not None:
            # Get submission counts for each task (batch query for efficiency)
            student_task_ids = [st.task_id for st in student.student_tasks]
            submission_counts = {}
            if student_task_ids:
                counts = (
                    self.sql_session.query(
                        Submission.task_id,
                        func.count(Submission.id)
                    )
                    .filter(Submission.participation_id == participation.id)
                    .filter(Submission.task_id.in_(student_task_ids))
                    .group_by(Submission.task_id)
                    .all()
                )
                submission_counts = {task_id: count for task_id, count in counts}

            progress = calculate_task_archive_progress(
                student,
                participation,
                contest,
                self.sql_session,
                include_task_details=True,
                submission_counts=submission_counts,
            )
            total_score = progress["total_score"]
            max_score = progress["max_score"]
            percentage = progress["percentage"]
            task_scores = progress["task_scores"]
            # Commit to release any advisory locks taken by get_cached_score_entry
            self.sql_session.commit()
        else:
            # No student record - show no tasks
            total_score = 0.0
            max_score = 0.0
            percentage = 0.0
            task_scores = []

        # Get upcoming training days for this user
        upcoming_training_days = []
        for training_day in training_program.training_days:
            td_contest = training_day.contest

            # Skip archived training days (contest is None)
            if td_contest is None:
                continue

            # Get user's participation in this training day's contest
            td_participation = (
                self.sql_session.query(Participation)
                .filter(Participation.contest == td_contest)
                .filter(Participation.user == participation.user)
                .first()
            )

            if td_participation is None:
                continue

            # Check eligibility - skip training days the student is ineligible for
            is_eligible, main_group, _ = check_training_day_eligibility(
                self.sql_session, td_participation, training_day
            )
            if not is_eligible:
                continue

            # Determine effective start/end times (per-group timing)
            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None
            contest_start, contest_stop = compute_effective_times(
                td_contest.start, td_contest.stop,
                td_participation.delay_time,
                main_group_start, main_group_end)

            # Compute actual phase for this training day
            actual_phase, _, _, _, _ = compute_actual_phase(
                self.timestamp,
                contest_start,
                contest_stop,
                td_contest.analysis_start if td_contest.analysis_enabled else None,
                td_contest.analysis_stop if td_contest.analysis_enabled else None,
                td_contest.per_user_time,
                td_participation.starting_time,
                td_participation.delay_time,
                td_participation.extra_time,
            )

            # Only show training days with actual_phase < 1 (not yet completed)
            # actual_phase < 0 means not started yet, actual_phase == 0 means active
            if actual_phase >= 1:
                continue

            # Calculate user-specific start time (group start + delay)
            user_start_time = contest_start + td_participation.delay_time

            # Calculate duration
            duration = td_contest.per_user_time \
                if td_contest.per_user_time is not None else \
                contest_stop - contest_start

            # Check if training starts within 6 hours (21600 seconds)
            six_hours_from_now = self.timestamp + timedelta(hours=6)
            has_started = actual_phase >= -1
            can_enter_soon = not has_started and user_start_time <= six_hours_from_now

            upcoming_training_days.append({
                "training_day": training_day,
                "contest": td_contest,
                "participation": td_participation,
                "has_started": has_started,
                "user_start_time": user_start_time,
                "duration": duration,
                "can_enter_soon": can_enter_soon,
            })

        # Sort by proximity to start time (closest first)
        upcoming_training_days.sort(key=lambda x: x["user_start_time"])

        self.render(
            "training_program_overview.html",
            total_score=total_score,
            max_score=max_score,
            percentage=percentage,
            task_scores=task_scores,
            upcoming_training_days=upcoming_training_days,
            server_timestamp=self.timestamp,
            **self.r_params
        )


class TrainingDaysHandler(ContestHandler):
    """Training days page handler.

    Shows all training days for a training program, including:
    - Ongoing and upcoming trainings (non-archived)
    - Past trainings with scores (training score, home score, total)
    """

    @tornado.web.authenticated
    @multi_contest
    def get(self):
        participation: Participation = self.current_user
        contest = self.contest

        training_program = self.training_program
        if training_program is None:
            raise tornado.web.HTTPError(404)

        student = (
            self.sql_session.query(Student)
            .join(Participation, Student.participation_id == Participation.id)
            .filter(Participation.contest_id == contest.id)
            .filter(Participation.user_id == participation.user_id)
            .filter(Student.training_program_id == training_program.id)
            .first()
        )

        ongoing_upcoming_trainings = []
        past_trainings = []

        # Collect past training day IDs for batch query
        past_training_day_ids = [
            td.id for td in training_program.training_days if td.contest is None
        ]

        # Batch fetch all ArchivedStudentRanking records for the student
        archived_rankings_map = {}
        if student is not None and past_training_day_ids:
            archived_rankings = (
                self.sql_session.query(ArchivedStudentRanking)
                .filter(ArchivedStudentRanking.training_day_id.in_(past_training_day_ids))
                .filter(ArchivedStudentRanking.student_id == student.id)
                .all()
            )
            archived_rankings_map = {r.training_day_id: r for r in archived_rankings}

        # Build task_by_id mapping for get_cached_score_entry in _build_past_training_info
        managing_contest = training_program.managing_contest
        task_by_id = {task.id: task for task in managing_contest.get_tasks()}

        for training_day in training_program.training_days:
            td_contest = training_day.contest

            if td_contest is None:
                past_trainings.append(self._build_past_training_info(
                    training_day, student, participation, archived_rankings_map,
                    task_by_id
                ))
                continue

            td_participation = (
                self.sql_session.query(Participation)
                .filter(Participation.contest == td_contest)
                .filter(Participation.user == participation.user)
                .first()
            )

            if td_participation is None:
                continue

            is_eligible, main_group, _ = check_training_day_eligibility(
                self.sql_session, td_participation, training_day
            )
            if not is_eligible:
                continue

            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None
            contest_start, contest_stop = compute_effective_times(
                td_contest.start, td_contest.stop,
                td_participation.delay_time,
                main_group_start, main_group_end)

            actual_phase, _, _, _, _ = compute_actual_phase(
                self.timestamp,
                contest_start,
                contest_stop,
                td_contest.analysis_start if td_contest.analysis_enabled else None,
                td_contest.analysis_stop if td_contest.analysis_enabled else None,
                td_contest.per_user_time,
                td_participation.starting_time,
                td_participation.delay_time,
                td_participation.extra_time,
            )

            user_start_time = contest_start + td_participation.delay_time

            duration = td_contest.per_user_time \
                if td_contest.per_user_time is not None else \
                contest_stop - contest_start

            six_hours_from_now = self.timestamp + timedelta(hours=6)
            has_started = actual_phase >= -1
            can_enter_soon = not has_started and user_start_time <= six_hours_from_now

            ongoing_upcoming_trainings.append({
                "training_day": training_day,
                "contest": td_contest,
                "participation": td_participation,
                "has_started": has_started,
                "has_ended": actual_phase >= 1,
                "user_start_time": user_start_time,
                "duration": duration,
                "can_enter_soon": can_enter_soon,
            })

        # Commit to release advisory locks from cache rebuilds in _build_past_training_info
        self.sql_session.commit()

        ongoing_upcoming_trainings.sort(key=lambda x: x["user_start_time"])
        past_trainings.sort(
            key=lambda x: x["start_time"] if x["start_time"] else self.timestamp,
            reverse=True
        )

        self.render(
            "training_days.html",
            ongoing_upcoming_trainings=ongoing_upcoming_trainings,
            past_trainings=past_trainings,
            server_timestamp=self.timestamp,
            **self.r_params
        )

    def _build_past_training_info(
        self,
        training_day,
        student: Student | None,
        participation: Participation,
        archived_rankings_map: dict,
        task_by_id: dict[int, Task],
    ) -> dict:
        """Build info dict for a past (archived) training day.

        task_by_id: mapping of task_id -> Task for tasks in the managing contest.
                    Used to get fresh home scores via get_cached_score_entry.
        """
        training_score = 0.0
        home_score = 0.0
        max_score = 0.0
        tasks_info = []

        archived_tasks_data = training_day.archived_tasks_data or {}

        if student is not None:
            archived_ranking = archived_rankings_map.get(training_day.id)

            archived_task_scores = {}
            if archived_ranking and archived_ranking.task_scores:
                archived_task_scores = archived_ranking.task_scores

            for task_id_str, task_data in archived_tasks_data.items():
                task_max_score = task_data.get("max_score", 100.0)
                max_score += task_max_score

                task_training_score = archived_task_scores.get(task_id_str, 0.0)
                training_score += task_training_score

                task_id = int(task_id_str)
                # Get fresh home score using get_cached_score_entry if task exists
                task = task_by_id.get(task_id)
                if task is not None:
                    cache_entry = get_cached_score_entry(
                        self.sql_session, participation, task
                    )
                    task_home_score = cache_entry.score
                else:
                    # Task no longer exists in managing contest
                    task_home_score = 0.0
                home_score += task_home_score

                tasks_info.append({
                    "task_id": task_id,
                    "name": task_data.get("short_name", ""),
                    "title": task_data.get("name", ""),
                    "training_score": task_training_score,
                    "home_score": task_home_score,
                    "max_score": task_max_score,
                })
        else:
            for task_id_str, task_data in archived_tasks_data.items():
                task_max_score = task_data.get("max_score", 100.0)
                max_score += task_max_score
                tasks_info.append({
                    "task_id": int(task_id_str),
                    "name": task_data.get("short_name", ""),
                    "title": task_data.get("name", ""),
                    "training_score": 0.0,
                    "home_score": 0.0,
                    "max_score": task_max_score,
                })

        # Determine eligible scoreboards based on student's tags during training
        eligible_scoreboards = []
        scoreboard_sharing = training_day.scoreboard_sharing or {}
        if student is not None and scoreboard_sharing:
            archived_ranking = archived_rankings_map.get(training_day.id)
            if archived_ranking and archived_ranking.student_tags:
                student_tags_during_training = set(archived_ranking.student_tags)
                for tag in scoreboard_sharing.keys():
                    if tag in student_tags_during_training:
                        eligible_scoreboards.append({
                            "tag": tag,
                            "top_names": scoreboard_sharing[tag].get("top_names", 5),
                        })

        return {
            "training_day": training_day,
            "name": training_day.name,
            "description": training_day.description,
            "start_time": training_day.start_time,
            "training_score": training_score,
            "home_score": home_score,
            "max_score": max_score,
            "tasks": tasks_info,
            "eligible_scoreboards": eligible_scoreboards,
        }


class ScoreboardDataHandler(ContestHandler):
    """Handler for fetching scoreboard data for a specific training day and tag.

    Returns JSON data for the scoreboard modal, filtered by tag and with
    appropriate anonymization based on the top_names setting.
    """

    @tornado.web.authenticated
    @multi_contest
    def get(self, training_day_id: str, tag: str):
        self.set_header("Content-Type", "application/json")

        participation: Participation = self.current_user
        training_program = self.training_program
        if training_program is None:
            self.set_status(404)
            self.write({"error": "Training program not found"})
            return

        # Get the training day
        training_day = (
            self.sql_session.query(TrainingDay)
            .filter(TrainingDay.id == int(training_day_id))
            .filter(TrainingDay.training_program_id == training_program.id)
            .first()
        )

        if training_day is None or training_day.contest is not None:
            self.set_status(404)
            self.write({"error": "Archived training day not found"})
            return

        # Check if scoreboard is shared for this tag
        scoreboard_sharing = training_day.scoreboard_sharing or {}
        if tag not in scoreboard_sharing:
            self.set_status(403)
            self.write({"error": "Scoreboard not shared for this tag"})
            return

        top_names = scoreboard_sharing[tag].get("top_names", 5)

        # Get the current student
        student = (
            self.sql_session.query(Student)
            .join(Participation, Student.participation_id == Participation.id)
            .filter(Participation.contest_id == self.contest.id)
            .filter(Participation.user_id == participation.user_id)
            .filter(Student.training_program_id == training_program.id)
            .first()
        )

        if student is None:
            self.set_status(403)
            self.write({"error": "Student not found"})
            return

        # Check if student had this tag during training
        student_archived_ranking = (
            self.sql_session.query(ArchivedStudentRanking)
            .filter(ArchivedStudentRanking.training_day_id == training_day.id)
            .filter(ArchivedStudentRanking.student_id == student.id)
            .first()
        )

        if student_archived_ranking is None:
            self.set_status(403)
            self.write({"error": "No ranking data for this student"})
            return

        student_tags_during_training = set(student_archived_ranking.student_tags or [])
        if tag not in student_tags_during_training:
            self.set_status(403)
            self.write({"error": "Not eligible to view this scoreboard"})
            return

        # Get all archived rankings for students with this tag during training
        all_rankings = (
            self.sql_session.query(ArchivedStudentRanking)
            .filter(ArchivedStudentRanking.training_day_id == training_day.id)
            .all()
        )

        # Filter to students who had this tag during training
        tag_rankings = [
            r for r in all_rankings
            if r.student_tags and tag in r.student_tags
        ]

        # Get archived tasks data to filter by tag accessibility
        archived_tasks_data = training_day.archived_tasks_data or {}

        # Filter tasks to those accessible to this tag during training
        accessible_tasks = {}
        for task_id_str, task_data in archived_tasks_data.items():
            task_tags = task_data.get("tags", [])
            if not task_tags or tag in task_tags:
                accessible_tasks[task_id_str] = task_data

        # Build scoreboard data
        sorted_accessible_tasks = sorted(
            accessible_tasks.items(),
            key=lambda kv: (kv[1].get("training_day_num") or 0, int(kv[0]))
        )
        scoreboard_entries = []
        for ranking in tag_rankings:
            task_scores = ranking.task_scores or {}
            total_score = 0.0
            task_score_list = []

            for task_id_str, task_data in sorted_accessible_tasks:
                score = task_scores.get(task_id_str, 0.0)
                total_score += score
                task_score_list.append({
                    "task_id": task_id_str,
                    "score": score,
                    "max_score": task_data.get("max_score", 100.0),
                })

            student_name = "Unknown"
            if ranking.student and ranking.student.participation and ranking.student.participation.user:
                student_name = ranking.student.participation.user.username

            scoreboard_entries.append({
                "student_id": ranking.student_id,
                "student_name": student_name,
                "total_score": total_score,
                "task_scores": task_score_list,
                "is_current_student": ranking.student_id == student.id,
            })

        # Sort by total score descending
        scoreboard_entries.sort(key=lambda x: x["total_score"], reverse=True)

        # Apply anonymization: only top N students show full names
        for i, entry in enumerate(scoreboard_entries):
            entry["rank"] = i + 1
            if i >= top_names and not entry["is_current_student"]:
                entry["student_name"] = f"#{i + 1}"

        # Build tasks list for header
        tasks_list = [
            {
                "task_id": task_id_str,
                "name": task_data.get("name", task_data.get("short_name", f"Task {task_id_str}")),
                "max_score": task_data.get("max_score", 100.0),
            }
            for task_id_str, task_data in sorted_accessible_tasks
        ]

        self.write({
            "success": True,
            "training_day_name": training_day.name,
            "tag": tag,
            "top_names": top_names,
            "tasks": tasks_list,
            "scoreboard": scoreboard_entries,
            "current_student_id": student.id,
        })
