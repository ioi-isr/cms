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
from collections import namedtuple

from sqlalchemy.orm import joinedload

from cms.db import Contest, StatementView
from cms.grading.scoring import task_score
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

        # This massive joined load gets all the information which we will need
        # to generating the rankings.
        self.contest: Contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == contest_id)
            .options(joinedload("participations"))
            .options(joinedload("participations.submissions"))
            .options(joinedload("participations.submissions.token"))
            .options(joinedload("participations.submissions.results"))
            .options(joinedload("participations.statement_views"))
            .first()
        )

        statement_views_set = set()
        for p in self.contest.participations:
            for sv in p.statement_views:
                statement_views_set.add((sv.participation_id, sv.task_id))

        # Preprocess participations: get data about teams, scores
        show_teams = False
        for p in self.contest.participations:
            show_teams = show_teams or p.team_id

            p.task_statuses = []  # status per task for rendering/export
            total_score = 0.0
            partial = False
            for task in self.contest.tasks:
                t_score, t_partial = task_score(p, task, rounded=True)
                
                has_submissions = any(s.task_id == task.id and s.official 
                                     for s in p.submissions)
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
