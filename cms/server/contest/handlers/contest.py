#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015-2016 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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

"""Contest handler classes for CWS.

"""

from collections.abc import Callable
import functools
import ipaddress
import json
import logging
import typing

import collections

from cms.db.user import Participation
from cms.server.util import Url, can_access_task, check_training_day_eligibility

try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms import config, TOKEN_MODE_MIXED
from cms.db import Contest, Student, Submission, Task, TrainingDayGroup, TrainingProgram, UserTest, contest
from cms.locale import filter_language_codes
from cms.server import FileHandlerMixin
from cms.server.contest.authentication import authenticate_request
from cmscommon.datetime import get_timezone
from .base import BaseHandler, add_ip_to_list
from ..phase_management import compute_actual_phase, compute_effective_times


logger = logging.getLogger(__name__)


NOTIFICATION_ERROR = "error"
NOTIFICATION_WARNING = "warning"
NOTIFICATION_SUCCESS = "success"


class ContestHandler(BaseHandler):
    """A handler that has a contest attached.

    Most of the RequestHandler classes in this application will be a
    child of this class.

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.contest_url: Url = None
        self.contest: Contest
        self.training_program: TrainingProgram | None = None
        self.impersonated_by_admin = False
        # Cached eligibility check result to avoid duplicate queries
        self._eligibility_cache: tuple[bool, "TrainingDayGroup | None", list[str]] | None = None

    def prepare(self):
        self.choose_contest()

        if self.contest.allowed_localizations:
            lang_codes = filter_language_codes(
                list(self.available_translations.keys()),
                self.contest.allowed_localizations)
            self.available_translations = dict(
                (k, v) for k, v in self.available_translations.items()
                if k in lang_codes)

        super().prepare()

        if self.is_multi_contest():
            # Use training program name for URL if accessing via training program
            if self.training_program is not None:
                self.contest_url = self.url[self.training_program.name]
            else:
                self.contest_url = self.url[self.contest.name]
        else:
            self.contest_url = self.url

        # Run render_params() now, not at the beginning of the request,
        # because we need contest_name
        self.r_params = self.render_params()

        # Check eligibility for training day contests AFTER r_params is set
        # so that error pages can render properly
        if self.current_user is not None:
            training_day = self.contest.training_day
            is_eligible, _, _ = self.get_eligibility()
            if not is_eligible and training_day is not None:
                raise tornado.web.HTTPError(
                    403,
                    "You are not eligible for this training day. "
                    "Please contact an administrator to fix your group assignment."
                )

    def _raise_404_for_internal_contest(self):
        """Prepare error context and raise 404 for internal contests."""
        super().prepare()
        self.r_params = super().render_params()
        raise tornado.web.HTTPError(404)

    def choose_contest(self):
        """Fill self.contest using contest passed as argument or path.

        If a contest was specified as argument to CWS, fill
        self.contest with that; otherwise extract it from the URL path.

        Training programs can also be accessed by their name, which will
        resolve to their managing contest.

        """
        self.training_program = None

        if self.is_multi_contest():
            # Choose contest name from last path segment to support nested folders
            # see: https://github.com/tornadoweb/tornado/issues/1673
            raw_path = self.path_args[0]
            contest_name = raw_path.split('/')[-1]

            # Select the correct contest or return an error
            self.contest = self.sql_session.query(Contest)\
                .filter(Contest.name == contest_name).first()
            if self.contest is None:
                # Try to find a training program with this name
                training_program = self.sql_session.query(TrainingProgram)\
                    .filter(TrainingProgram.name == contest_name).first()
                if training_program is not None:
                    self.contest = training_program.managing_contest
                    self.training_program = training_program
                else:
                    self.contest = Contest(
                        name=contest_name, description=contest_name)
                    # render_params in this class assumes the contest is loaded,
                    # so we cannot call it without a fully defined contest. Luckily
                    # the one from the base class is enough to display a 404 page.
                    self._raise_404_for_internal_contest()
            if self.contest.name.startswith("__") and self.training_program is None:
                # Block direct access to managing contests, but allow access
                # via training program name
                self._raise_404_for_internal_contest()
        else:
            # Select the contest specified on the command line
            self.contest = Contest.get_from_id(
                self.service.contest_id, self.sql_session)
            if self.contest is not None:
                # Check if this contest is a managing contest for a training program
                if self.contest.training_program is not None:
                    self.training_program = self.contest.training_program
                elif self.contest.name.startswith("__"):
                    self._raise_404_for_internal_contest()

    def get_current_user(self) -> Participation | None:
        """Return the currently logged in participation.

        The name is get_current_user because tornado requires that
        name.

        The participation is obtained from one of the possible sources:
        - if IP autologin is enabled, the remote IP address is matched
          with the participation IP address; if a match is found, that
          participation is returned; in case of errors, None is returned;
        - if username/password authentication is enabled, and a
          "X-CMS-Authorization" header is present and valid, the
          corresponding participation is returned.
        - if username/password authentication is enabled, and the cookie
          is valid, the corresponding participation is returned, and the
          cookie is refreshed.
        - for training day contests: if the user is authenticated to the
          parent training program's managing contest, they are automatically
          authenticated to the training day contest as well.

        After finding the participation, IP login and hidden users
        restrictions are checked.

        In case of any error, or of a login by other sources, the
        cookie is deleted.

        return: the participation object for the
            user logged in for the running contest.

        """
        cookie_name = self.contest.name + "_login"
        cookie = self.get_secure_cookie(cookie_name)
        authorization_header = self.request.headers.get(
            "X-CMS-Authorization", None)
        if authorization_header is not None:
            authorization_header = tornado.web.decode_signed_value(self.application.settings["cookie_secret"],
                                                                   cookie_name, authorization_header)

        try:
            ip_address = ipaddress.ip_address(self.request.remote_ip)
        except ValueError:
            logger.warning("Invalid IP address provided by Tornado: %s",
                           self.request.remote_ip)
            return None

        participation, cookie, impersonated = authenticate_request(
            self.sql_session, self.contest,
            self.timestamp, cookie,
            authorization_header,
            ip_address)

        # For training day contests: if direct authentication failed,
        # try to authenticate via the parent training program's managing contest.
        # This allows users logged into the training program to automatically
        # access training day contests without re-authenticating.
        if participation is None and self.contest.training_day is not None:
            training_program = self.contest.training_day.training_program
            managing_contest = training_program.managing_contest

            # Try to authenticate using the managing contest's cookie
            managing_cookie_name = managing_contest.name + "_login"
            managing_cookie = self.get_secure_cookie(managing_cookie_name)

            if managing_cookie is not None:
                # Authenticate against the managing contest
                managing_participation, _, managing_impersonated = authenticate_request(
                    self.sql_session, managing_contest,
                    self.timestamp, managing_cookie,
                    None,  # No authorization header for fallback
                    ip_address)

                if managing_participation is not None:
                    # User is authenticated to the managing contest.
                    # Find their participation in this training day's contest.
                    participation = (
                        self.sql_session.query(Participation)
                        .filter(Participation.contest == self.contest)
                        .filter(Participation.user == managing_participation.user)
                        .first()
                    )
                    if participation is not None:
                        impersonated = managing_impersonated
                        # Don't set a cookie for the training day contest -
                        # authentication is always via the managing contest

        if cookie is None:
            self.clear_cookie(cookie_name)
        elif self.refresh_cookie:
            self.set_secure_cookie(
                cookie_name,
                cookie,
                expires_days=None,
                max_age=config.contest_web_server.cookie_duration,
            )

        self.impersonated_by_admin = impersonated
        return participation

    def render_params(self):
        ret = super().render_params()

        ret["contest"] = self.contest
        ret["training_program"] = self.training_program

        if self.contest_url is not None:
            ret["contest_url"] = self.contest_url

        ret["phase"] = self.contest.phase(self.timestamp)

        ret["printing_enabled"] = (config.printing.printer is not None)
        ret["questions_enabled"] = self.contest.allow_questions
        ret["testing_enabled"] = self.contest.allow_user_tests

        if self.current_user is not None:
            participation = self.current_user
            ret["participation"] = participation
            ret["user"] = participation.user

            # Check eligibility for training day contests with main groups
            _training_day = self.contest.training_day
            is_eligible, main_group, _matching_tags = self.get_eligibility()

            ret["main_group"] = main_group
            ret["ineligible_for_training_day"] = not is_eligible

            # Determine effective start/end times (per-group timing)
            # These are used by templates to show the correct times to users
            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None
            contest_start, contest_stop = compute_effective_times(
                self.contest.start, self.contest.stop,
                participation.delay_time,
                main_group_start, main_group_end)

            # Pass effective times to templates so they can display correct times
            # for training day contests with per-group timing
            ret["effective_start"] = contest_start
            ret["effective_stop"] = contest_stop

            res = compute_actual_phase(
                self.timestamp,
                self.contest.start,
                self.contest.stop,
                self.contest.analysis_start if self.contest.analysis_enabled else None,
                self.contest.analysis_stop if self.contest.analysis_enabled else None,
                self.contest.per_user_time,
                participation.starting_time,
                participation.delay_time,
                participation.extra_time,
                main_group_start,
                main_group_end,
            )

            ret["actual_phase"], ret["current_phase_begin"], \
                ret["current_phase_end"], ret["valid_phase_begin"], \
                ret["valid_phase_end"] = res

            if ret["actual_phase"] == 0:
                ret["phase"] = 0

                if participation.starting_time is not None:
                    client_ip = self.request.remote_ip
                    new_ip_list = add_ip_to_list(
                        participation.starting_ip_addresses, client_ip
                    )
                    if new_ip_list != participation.starting_ip_addresses:
                        participation.starting_ip_addresses = new_ip_list
                        self.sql_session.commit()

            # set the timezone used to format timestamps
            ret["timezone"] = get_timezone(participation.user, self.contest)

        # some information about token configuration
        ret["tokens_contest"] = self.contest.token_mode

        t_tokens = set(t.token_mode for t in self.contest.get_tasks())
        if len(t_tokens) == 1:
            ret["tokens_tasks"] = next(iter(t_tokens))
        else:
            ret["tokens_tasks"] = TOKEN_MODE_MIXED

        # For training day contests, filter tasks based on visibility tags
        ret["visible_tasks"] = self.get_visible_tasks()

        return ret

    def get_eligibility(self) -> tuple[bool, "TrainingDayGroup | None", list[str]]:
        """Get cached eligibility check result for the current user.

        Returns cached result if available, otherwise performs the check
        and caches it for subsequent calls.

        return: tuple of (is_eligible, main_group, matching_tags)

        """
        if self._eligibility_cache is not None:
            return self._eligibility_cache

        training_day = self.contest.training_day
        self._eligibility_cache = check_training_day_eligibility(
            self.sql_session, self.current_user, training_day
        )
        return self._eligibility_cache

    def get_login_url(self):
        """The login url depends on the contest name, so we can't just
        use the "login_url" application parameter.

        """
        return self.contest_url()

    def get_task(self, task_name: str) -> Task | None:
        """Return the task in the contest with the given name.

        task_name: the name of the task we are interested in.

        return: the corresponding task object, if found.

        """
        # For training day contests, tasks are linked via training_day_id
        # rather than contest_id. Use get_tasks() to get the correct task list.
        for task in self.contest.get_tasks():
            if task.name == task_name:
                return task
        return None

    def can_access_task(self, task: Task) -> bool:
        """Check if the current user can access the given task.

        For training day contests, tasks may have visibility restrictions
        based on student tags. A task is accessible if:
        - The task has no visible_to_tags (empty list = visible to all)
        - The student has at least one tag matching the task's visible_to_tags

        For non-training-day contests, all tasks are accessible.

        task: the task to check access for.

        return: True if the current user can access the task.

        """
        # Must be logged in to access restricted tasks
        if self.current_user is None:
            return not task.visible_to_tags

        return can_access_task(
            self.sql_session, task, self.current_user, self.contest.training_day
        )

    def get_visible_tasks(self) -> list[Task]:
        """Return the list of tasks visible to the current user.

        For training day contests, filters tasks based on visibility tags
        and sorts them based on the main group's task_order setting.
        For non-training-day contests, returns all tasks.

        return: list of tasks the current user can access.

        """
        tasks = [task for task in self.contest.get_tasks() if self.can_access_task(task)]

        # Apply per-group task ordering for training day contests
        training_day = self.contest.training_day
        if training_day is not None and self.current_user is not None:
            _is_eligible, main_group, _ = self.get_eligibility()
            if main_group is not None and main_group.alphabetical_task_order:
                tasks = sorted(tasks, key=lambda t: t.name)

        return tasks

    def get_submission(self, task: Task, opaque_id: str | int) -> Submission | None:
        """Return the num-th contestant's submission on the given task.

        task: a task for the contest that is being served.
        submission_num: a positive number, in decimal encoding.

        return: the submission_num-th submission
            (1-based), in chronological order, that was sent by the
            currently logged in contestant on the given task (None if
            not found).

        """
        from cms.db.training_day import get_managing_participation

        participation = self.current_user
        training_day = self.contest.training_day

        if training_day is not None:
            managing_participation = get_managing_participation(
                self.sql_session, training_day, participation.user
            )
            if managing_participation is not None:
                participation = managing_participation

        return (
            self.sql_session.query(Submission)
            .filter(Submission.participation == participation)
            .filter(Submission.task == task)
            .filter(Submission.opaque_id == int(opaque_id))
            .first()
        )

    def get_user_test(self, task: Task, user_test_num: int) -> UserTest | None:
        """Return the num-th contestant's test on the given task.

        task: a task for the contest that is being served.
        user_test_num: a positive number, in decimal encoding.

        return: the user_test_num-th user test, in
            chronological order, that was sent by the currently logged
            in contestant on the given task (None if not found).

        """
        return self.sql_session.query(UserTest) \
            .filter(UserTest.participation == self.current_user) \
            .filter(UserTest.task == task) \
            .order_by(UserTest.timestamp) \
            .offset(int(user_test_num) - 1) \
            .first()

    def add_notification(
        self, subject: str, text: str, level: str, text_params: object | None = None
    ):
        subject = self._(subject)
        text = self._(text)
        if text_params is not None:
            text %= text_params
        self.service.add_notification(self.current_user.user.username,
                                      self.timestamp, subject, text, level)

    def notify_success(
        self, subject: str, text: str, text_params: object | None = None
    ):
        self.add_notification(subject, text, NOTIFICATION_SUCCESS, text_params)

    def notify_warning(
        self, subject: str, text: str, text_params: object | None = None
    ):
        self.add_notification(subject, text, NOTIFICATION_WARNING, text_params)

    def notify_error(self, subject: str, text: str, text_params: object | None = None):
        self.add_notification(subject, text, NOTIFICATION_ERROR, text_params)

    def json(self, data, status_code=200):
        self.set_header("Content-type", "application/json; charset=utf-8")
        self.set_status(status_code)
        self.write(json.dumps(data))

    def check_xsrf_cookie(self):
        # We don't need to check for xsrf if the request came with a custom
        # header, as those are not set by the browser.
        if "X-CMS-Authorization" in self.request.headers:
            pass
        else:
            super().check_xsrf_cookie()


class FileHandler(ContestHandler, FileHandlerMixin):
    pass

_P = typing.ParamSpec("_P")
_R = typing.TypeVar("_R")
_Self = typing.TypeVar("_Self", bound="ContestHandler")

def api_login_required(
    func: Callable[typing.Concatenate[_Self, _P], _R],
) -> Callable[typing.Concatenate[_Self, _P], _R | None]:
    """A decorator filtering out unauthenticated requests.

    Unlike @tornado.web.authenticated, this returns a JSON error instead of
    redirecting.

    """

    @functools.wraps(func)
    def wrapped(self: _Self, *args: _P.args, **kwargs: _P.kwargs):
        if not self.current_user:
            self.json({"error": "An authenticated user is required"}, 403)
        else:
            return func(self, *args, **kwargs)

    return wrapped
