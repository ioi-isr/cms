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

"""Training program overview handler for CWS.

This handler provides a custom overview page for training programs,
showing total score, percentage, task archive, and upcoming training days.
"""

from datetime import timedelta

import tornado.web

from cms.db import Participation, Submission, SubmissionResult
from cms.server import multi_contest
from cms.server.contest.phase_management import compute_actual_phase, compute_effective_times
from cms.server.util import check_training_day_eligibility
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

        # Calculate total score and max score
        total_score = 0.0
        max_score = 0.0
        task_scores = []

        for task in contest.get_tasks():
            max_task_score = task.active_dataset.score_type_object.max_score \
                if task.active_dataset else 100.0
            max_score += max_task_score

            # Get best submission score for this task (only official submissions)
            best_score = 0.0
            submissions = (
                self.sql_session.query(Submission)
                .filter(Submission.participation == participation)
                .filter(Submission.task == task)
                .filter(Submission.official.is_(True))
                .all()
            )

            for submission in submissions:
                if task.active_dataset:
                    result = (
                        self.sql_session.query(SubmissionResult)
                        .filter(SubmissionResult.submission == submission)
                        .filter(SubmissionResult.dataset == task.active_dataset)
                        .first()
                    )
                    if result and result.score is not None:
                        best_score = max(best_score, result.score)

            total_score += best_score
            task_scores.append({
                "task": task,
                "score": best_score,
                "max_score": max_task_score,
            })

        # Calculate percentage
        percentage = (total_score / max_score * 100) if max_score > 0 else 0.0

        # Get upcoming training days for this user
        upcoming_training_days = []
        for training_day in training_program.training_days:
            td_contest = training_day.contest

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
