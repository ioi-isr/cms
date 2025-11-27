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
showing total score, percentage, and task archive.
"""

import tornado.web

from cms.db import Participation, Submission, SubmissionResult
from cms.server import multi_contest
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

        for task in contest.tasks:
            max_task_score = task.active_dataset.score_type_object.max_score \
                if task.active_dataset else 100.0
            max_score += max_task_score

            # Get best submission score for this task
            best_score = 0.0
            submissions = (
                self.sql_session.query(Submission)
                .filter(Submission.participation == participation)
                .filter(Submission.task == task)
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

        self.render(
            "training_program_overview.html",
            total_score=total_score,
            max_score=max_score,
            percentage=percentage,
            task_scores=task_scores,
            **self.r_params
        )
