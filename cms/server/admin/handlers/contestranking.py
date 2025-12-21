#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015 William Di Luigi <williamdiluigi@gmail.com>
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

"""Ranking-related handlers for AWS for a specific contest.

"""

import csv
import io
import json
from collections import namedtuple

import tornado.web
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import joinedload

from cms.db import Contest, Participation, ParticipationTaskScore, ScoreHistory, \
    Submission, SubmissionResult, Task
from cms.grading.scorecache import get_cached_score_entry, rebuild_score_history
from .base import BaseHandler, require_permission


TaskStatus = namedtuple(
    "TaskStatus", ["score", "partial", "has_submissions", "has_opened"]
)


class RankingHandler(BaseHandler):
    """Shows the ranking for a contest.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, format="online"):
        # This validates the contest id.
        self.safe_get_item(Contest, contest_id)

        # Load contest with tasks, participations, and statement views.
        # We use the score cache to get score and has_submissions.
        # partial is computed at render time via SQL aggregation for correctness.
        self.contest: Contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == contest_id)
            .options(joinedload("tasks"))
            .options(joinedload("tasks.active_dataset"))
            .options(joinedload("participations"))
            .options(joinedload("participations.user"))
            .options(joinedload("participations.team"))
            .options(joinedload("participations.statement_views"))
            .first()
        )

        # Get participation IDs for the SQL aggregation query
        participation_ids = [p.id for p in self.contest.participations]

        # SQL aggregation to compute t_partial for all participation/task pairs.
        # t_partial is True when there's an official submission that is not yet scored.
        # has_submissions is retrieved from the cache instead.
        partial_flags_query = (
            self.sql_session.query(
                Submission.participation_id,
                Submission.task_id,
                func.bool_or(
                    and_(
                        Task.active_dataset_id.isnot(None),
                        or_(
                            SubmissionResult.submission_id.is_(None),
                            SubmissionResult.score.is_(None),
                            SubmissionResult.score_details.is_(None),
                            SubmissionResult.public_score.is_(None),
                            SubmissionResult.public_score_details.is_(None),
                            SubmissionResult.ranking_score_details.is_(None),
                        )
                    )
                ).label('t_partial')
            )
            .join(Task, Submission.task_id == Task.id)
            .outerjoin(
                SubmissionResult,
                and_(
                    SubmissionResult.submission_id == Submission.id,
                    SubmissionResult.dataset_id == Task.active_dataset_id
                )
            )
            .filter(Submission.participation_id.in_(participation_ids))
            .filter(Submission.official.is_(True))
            .group_by(Submission.participation_id, Submission.task_id)
        ) if participation_ids else []

        # Build lookup dict: (participation_id, task_id) -> t_partial
        partial_by_pt = {}
        if participation_ids:
            for row in partial_flags_query.all():
                partial_by_pt[(row.participation_id, row.task_id)] = (
                    row.t_partial or False
                )

        statement_views_set = set()
        for p in self.contest.participations:
            for sv in p.statement_views:
                statement_views_set.add((sv.participation_id, sv.task_id))

        # Preprocess participations: get data about teams, scores
        # Use the score cache to get score and has_submissions.
        # partial is computed via SQL aggregation above for correctness.
        show_teams = False
        for p in self.contest.participations:
            show_teams = show_teams or p.team_id

            p.task_statuses = []  # status per task for rendering/export
            total_score = 0.0
            partial = False
            for task in self.contest.tasks:
                # Get the cache entry with score and has_submissions
                cache_entry = get_cached_score_entry(self.sql_session, p, task)
                t_score = round(cache_entry.score, task.score_precision)
                has_submissions = cache_entry.has_submissions
                # Get t_partial from SQL aggregation (not from cache)
                t_partial = partial_by_pt.get((p.id, task.id), False)

                has_opened = (p.id, task.id) in statement_views_set
                p.task_statuses.append(
                    TaskStatus(
                        score=t_score,
                        partial=t_partial,
                        has_submissions=has_submissions,
                        has_opened=has_opened,
                    )
                )
                total_score += t_score
                partial = partial or t_partial
            total_score = round(total_score, self.contest.score_precision)
            p.total_score = (total_score, partial)

        self.r_params = self.render_params()
        self.r_params["show_teams"] = show_teams

        date_str = self.contest.start.strftime("%Y%m%d")
        contest_name = self.contest.name.replace(" ", "_")

        if format == "txt":
            filename = f"{date_str}_{contest_name}_ranking.txt"
            self.set_header("Content-Type", "text/plain")
            self.set_header("Content-Disposition",
                            f"attachment; filename=\"{filename}\"")
            self.render("ranking.txt", **self.r_params)
        elif format == "csv":
            filename = f"{date_str}_{contest_name}_ranking.csv"
            self.set_header("Content-Type", "text/csv")
            self.set_header("Content-Disposition",
                            f"attachment; filename=\"{filename}\"")

            output = io.StringIO()  # untested
            writer = csv.writer(output)

            include_partial = True

            contest: Contest = self.r_params["contest"]

            row = ["Username", "User"]
            if show_teams:
                row.append("Team")
            for task in contest.tasks:
                row.append(task.name)
                if include_partial:
                    row.append("P")

            row.append("Global")
            if include_partial:
                row.append("P")

            writer.writerow(row)

            for p in sorted(contest.participations,
                            key=lambda p: p.total_score, reverse=True):
                if p.hidden:
                    continue

                row = [p.user.username,
                       "%s %s" % (p.user.first_name, p.user.last_name)]
                if show_teams:
                    row.append(p.team.name if p.team else "")
                assert len(contest.tasks) == len(p.task_statuses)
                for status in p.task_statuses:
                    row.append(status.score)
                    if include_partial:
                        row.append(self._status_indicator(status))

                total_score, partial = p.total_score
                row.append(total_score)
                if include_partial:
                    row.append("*" if partial else "")

                writer.writerow(row)

            self.finish(output.getvalue())
        else:
            self.render("ranking.html", **self.r_params)

    @staticmethod
    def _status_indicator(status: TaskStatus) -> str:
        star = "*" if status.partial else ""
        if not status.has_submissions:
            return "X" if not status.has_opened else "-"
        if not status.has_opened:
            return "!" + star
        return star


class ScoreHistoryHandler(BaseHandler):
    """Returns the score history for a contest as JSON.

    This endpoint provides score history data in RWS format:
    [[user_id, task_id, time, score], ...]

    This matches the format expected by RWS's HistoryStore.js for
    computing score and rank histories.

    By default, excludes hidden participations to match ranking page behavior.
    Use ?include_hidden=1 to include hidden participations.

    Before returning history data, this handler checks for any cache entries
    with history_valid=False and rebuilds their history to ensure correctness.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)

        include_hidden = self.get_argument("include_hidden", "0") == "1"

        # Check for any invalid history entries and rebuild them
        # We need to rebuild all invalid entries for the contest since
        # rank computation uses the full contest history stream
        invalid_entries = (
            self.sql_session.query(ParticipationTaskScore)
            .join(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(ParticipationTaskScore.history_valid.is_(False))
            .options(joinedload(ParticipationTaskScore.participation))
            .options(joinedload(ParticipationTaskScore.task))
            .all()
        )

        for entry in invalid_entries:
            rebuild_score_history(
                self.sql_session, entry.participation, entry.task
            )

        # Commit the rebuilt history before querying
        if invalid_entries:
            self.sql_session.commit()

        query = (
            self.sql_session.query(ScoreHistory)
            .join(Participation)
            .filter(Participation.contest_id == contest_id)
            .options(joinedload(ScoreHistory.participation).joinedload(Participation.user))
        )

        if not include_hidden:
            query = query.filter(Participation.hidden.is_(False))

        history = query.order_by(ScoreHistory.timestamp).all()

        result = [
            [
                str(h.participation.user_id),
                str(h.task_id),
                int(h.timestamp.timestamp()),
                h.score,
            ]
            for h in history
        ]

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))


class ParticipationDetailHandler(BaseHandler):
    """Shows detailed score/rank progress for a participation.

    This handler provides a user detail view similar to RWS's UserDetail,
    showing score and rank progress over time for a specific participation.
    It includes global and per-task score/rank charts, a navigator table,
    and a submission table for each task.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, user_id):
        self.contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == contest_id)
            .options(joinedload("tasks"))
            .options(joinedload("tasks.active_dataset"))
            .options(joinedload("participations"))
            .options(joinedload("participations.user"))
            .first()
        )
        if self.contest is None:
            raise tornado.web.HTTPError(404, "Contest not found")

        participation = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
            .first()
        )
        if participation is None:
            raise tornado.web.HTTPError(404, "Participation not found")

        visible_participations = [
            p for p in self.contest.participations if not p.hidden
        ]
        user_count = len(visible_participations)

        users_data = {}
        for p in visible_participations:
            users_data[str(p.user_id)] = {
                "f_name": p.user.first_name or "",
                "l_name": p.user.last_name or "",
            }

        tasks_data = {}
        total_max_score = 0.0
        for task in self.contest.tasks:
            max_score = 100.0
            extra_headers = []
            if task.active_dataset:
                try:
                    score_type = task.active_dataset.score_type_object
                    max_score = score_type.max_score
                    extra_headers = score_type.ranking_headers
                except Exception:
                    pass
            tasks_data[str(task.id)] = {
                "key": str(task.id),
                "name": task.title,
                "short_name": task.name,
                "contest": str(self.contest.id),
                "max_score": max_score,
                "score_precision": task.score_precision,
                "extra_headers": extra_headers,
            }
            total_max_score += max_score

        contest_data = {
            "key": str(self.contest.id),
            "name": self.contest.name,
            "begin": int(self.contest.start.timestamp()),
            "end": int(self.contest.stop.timestamp()),
            "max_score": total_max_score,
            "score_precision": self.contest.score_precision,
        }

        self.r_params = self.render_params()
        self.r_params["participation"] = participation
        self.r_params["user_id"] = str(user_id)
        self.r_params["user_count"] = user_count
        self.r_params["users_data"] = users_data
        self.r_params["tasks_data"] = tasks_data
        self.r_params["contest_data"] = contest_data
        self.r_params["history_url"] = self.url(
            "contest", contest_id, "ranking", "history"
        )
        self.r_params["submissions_url"] = self.url(
            "contest", contest_id, "user", user_id, "submissions"
        )
        self.render("participation_detail.html", **self.r_params)


class ParticipationSubmissionsHandler(BaseHandler):
    """Returns submissions for a participation as JSON in RWS format.

    This endpoint provides submission data in the format expected by
    RWS's UserDetail.js for displaying the submission table.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, user_id):
        from cms.db import Submission

        self.contest = self.safe_get_item(Contest, contest_id)

        participation = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
            .first()
        )
        if participation is None:
            raise tornado.web.HTTPError(404, "Participation not found")

        submissions = (
            self.sql_session.query(Submission)
            .filter(Submission.participation_id == participation.id)
            .filter(Submission.official.is_(True))
            .options(joinedload(Submission.token))
            .options(joinedload(Submission.results))
            .order_by(Submission.timestamp)
            .all()
        )

        dataset_by_task_id = {
            task.id: task.active_dataset for task in self.contest.tasks
        }

        result = []
        for s in submissions:
            score = 0.0
            extra = []
            dataset = dataset_by_task_id.get(s.task_id)
            if dataset is not None:
                sr = s.get_result(dataset)
                if sr is not None and sr.score is not None:
                    score = sr.score
                    if sr.ranking_score_details is not None:
                        extra = sr.ranking_score_details

            result.append({
                "task": str(s.task_id),
                "time": int(s.timestamp.timestamp()),
                "score": score,
                "token": s.token is not None,
                "extra": extra,
            })

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))
