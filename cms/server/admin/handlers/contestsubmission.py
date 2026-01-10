#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
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

"""Submission-related handlers for AWS for a specific contest.

"""

from cms.db import Contest, Submission, UserTest, Task

from .base import BaseHandler, require_permission


class ContestSubmissionsHandler(BaseHandler):
    """Shows all submissions for this contest.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)
        self.contest = contest

        # Determine if this is a training program managing contest
        is_training_program = contest.training_program is not None

        # For training day contests, only show submissions made via that training day
        # (submissions now have training_day_id set when submitted via a training day)
        if contest.training_day is not None:
            query = self.sql_session.query(Submission)\
                .filter(Submission.training_day_id == contest.training_day.id)
        else:
            # For regular contests and training program managing contests,
            # show all submissions for tasks in this contest
            query = self.sql_session.query(Submission).join(Task)\
                .filter(Task.contest == contest)
        page = int(self.get_query_argument("page", 0))
        self.render_params_for_submissions(query, page)

        # Pass flag to template to show training day column for training programs
        self.r_params["is_training_program"] = is_training_program

        self.render("contest_submissions.html", **self.r_params)


class ContestUserTestsHandler(BaseHandler):
    """Shows all user tests for this contest.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)
        self.contest = contest

        # For training day contests, tasks have training_day_id set
        # but contest_id points to the managing contest.
        # We need to filter by training_day_id for training day contests.
        if contest.training_day is not None:
            query = self.sql_session.query(UserTest).join(Task)\
                .filter(Task.training_day_id == contest.training_day.id)
        else:
            query = self.sql_session.query(UserTest).join(Task)\
                .filter(Task.contest == contest)
        page = int(self.get_query_argument("page", 0))
        self.render_params_for_user_tests(query, page)

        self.render("contest_user_tests.html", **self.r_params)
