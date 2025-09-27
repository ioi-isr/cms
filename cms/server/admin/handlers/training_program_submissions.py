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

"""Submission and user-test listings for training programs."""

from cms.db import Contest, Participation, Submission, TrainingProgram, UserTest

from .base import BaseHandler, require_permission


class TrainingProgramSubmissionsHandler(BaseHandler):
    """Show consolidated submissions for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)
        self.training_program = program

        page_components = (
            "training_program",
            program.id,
            "submissions",
        )

        regular_listing = self._build_contest_listing(
            contest=program.regular_contest,
            page=self.get_page_argument("regular_page"),
            page_param="regular_page",
            url_components=page_components,
        )

        home_listing = self._build_contest_listing(
            contest=program.home_contest,
            page=self.get_page_argument("home_page"),
            page_param="home_page",
            url_components=page_components,
        )

        self.r_params = self.render_params()
        self.r_params.update(
            {
                "training_program": program,
                "regular_contest": program.regular_contest,
                "home_contest": program.home_contest,
                "regular_submission_data": regular_listing,
                "home_submission_data": home_listing,
            }
        )
        self.render("training_program_submissions.html", **self.r_params)

    def _build_contest_listing(
        self,
        *,
        contest: Contest | None,
        page: int,
        page_param: str,
        url_components: tuple[object, ...],
    ) -> dict[str, object] | None:
        """Return submission listing data for a contest in the program."""

        if contest is None:
            return None

        query = (
            self.sql_session.query(Submission)
            .join(Participation)
            .filter(Participation.contest == contest)
        )

        return self.build_submission_listing(
            query,
            page,
            page_param,
            url_components,
        )


class TrainingProgramUserTestsHandler(BaseHandler):
    """Show consolidated user tests for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)
        self.training_program = program

        page_components = (
            "training_program",
            program.id,
            "user_tests",
        )

        regular_tests = self._build_contest_user_tests(
            contest=program.regular_contest,
            page=self.get_page_argument("regular_page"),
            page_param="regular_page",
            url_components=page_components,
        )

        home_tests = self._build_contest_user_tests(
            contest=program.home_contest,
            page=self.get_page_argument("home_page"),
            page_param="home_page",
            url_components=page_components,
        )

        self.r_params = self.render_params()
        self.r_params.update(
            {
                "training_program": program,
                "regular_contest": program.regular_contest,
                "home_contest": program.home_contest,
                "regular_user_tests": regular_tests,
                "home_user_tests": home_tests,
            }
        )
        self.render("training_program_user_tests.html", **self.r_params)

    def _build_contest_user_tests(
        self,
        *,
        contest: Contest | None,
        page: int,
        page_param: str,
        url_components: tuple[object, ...],
    ) -> dict[str, object] | None:
        """Return paginated user test data for a contest."""

        if contest is None:
            return None

        query = (
            self.sql_session.query(UserTest)
            .join(Participation)
            .filter(Participation.contest == contest)
        )

        return self.build_user_test_listing(
            query,
            page,
            page_param,
            url_components,
        )
