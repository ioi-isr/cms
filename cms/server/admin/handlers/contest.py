#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2016 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2015 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
# Copyright © 2018 William Di Luigi <williamdiluigi@gmail.com>
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

"""Contest-related handlers for AWS.

"""

from datetime import timedelta

import tornado.web

from cms import ServiceCoord, get_service_shards, get_service_address
from cms.db import Contest, Participation, Submission, Task, ContestFolder, TrainingDay, Student
from sqlalchemy.orm import joinedload
from sqlalchemy import func
from cmscommon.datetime import make_datetime

from .base import BaseHandler, SimpleContestHandler, SimpleHandler, \
    require_permission


def remove_contest_with_action(session, contest, action, target_contest=None):
    """Remove contest with specified action for tasks.
    
    This is a standalone helper function that can be called from tests.
    
    Args:
        session: SQLAlchemy session
        contest: Contest object to remove
        action: One of "move", "detach", or "delete_all"
        target_contest: Contest object (required if action is "move")
    """
    if action == "move":
        if target_contest is None:
            raise ValueError("Target contest must be specified when moving tasks")
        
        tasks = session.query(Task)\
            .filter(Task.contest == contest)\
            .order_by(Task.num, Task.id)\
            .all()

        # Phase 1: clear nums on moving tasks to avoid duplicate (contest_id, num).
        for task in tasks:
            task.num = None
        session.flush()

        # Phase 2: append after current max num in target, preserving gaps.
        max_num = session.query(func.max(Task.num))\
            .filter(Task.contest == target_contest)\
            .scalar()
        base_num = (max_num or -1) + 1

        for i, task in enumerate(tasks):
            task.contest = target_contest
            task.num = base_num + i
        session.flush()
        
    elif action == "detach":
        tasks = session.query(Task)\
            .filter(Task.contest == contest)\
            .all()
        
        for task in tasks:
            task.contest = None
            task.num = None
        session.flush()
    
    session.delete(contest)
    session.flush()


class AddContestHandler(
        SimpleHandler("add_contest.html", permission_all=True)):
    """Adds a new contest.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("contests", "add")

        try:
            attrs = dict()

            self.get_string(attrs, "name", empty=None)
            assert attrs.get("name") is not None, "No contest name specified."
            assert not attrs.get("name").startswith("__"), \
                "Contest name cannot start with '__' " \
                "(reserved for system contests)."
            attrs["description"] = attrs["name"]

            # Create the contest.
            contest = Contest(**attrs)
            self.sql_session.add(contest)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Create the contest on RWS.
            self.service.proxy_service.reinitialize()
            self.redirect(self.url("contest", contest.id))
        else:
            self.redirect(fallback_page)


class ContestHandler(SimpleContestHandler("contest.html")):
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id: str):
        self.contest = self.sql_session.query(Contest)\
            .options(
                joinedload(Contest.training_day)
                .joinedload(TrainingDay.groups)
            )\
            .filter(Contest.id == contest_id)\
            .one_or_none()
        
        if self.contest is None:
            raise tornado.web.HTTPError(404)

        self.r_params = self.render_params()
        self.r_params["all_folders"] = (
            self.sql_session.query(ContestFolder)
            .order_by(ContestFolder.name)
            .all()
        )

        all_student_tags: list[str] = []
        training_day = self.contest.training_day
        if training_day is not None:
            training_program = training_day.training_program
            tags_query = self.sql_session.query(
                func.unnest(Student.student_tags).label("tag")
            ).filter(
                Student.training_program_id == training_program.id
            ).distinct()
            all_student_tags = sorted([row.tag for row in tags_query.all()])
        self.r_params["all_student_tags"] = all_student_tags

        self.render("contest.html", **self.r_params)
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)

        old_start = contest.start

        try:
            attrs = contest.get_attrs()

            self.get_string(attrs, "name", empty=None)
            self.get_string(attrs, "description")

            assert attrs.get("name") is not None, "No contest name specified."
            assert not attrs.get("name").startswith("__"), \
                "Contest name cannot start with '__' " \
                "(reserved for system contests)."

            allowed_localizations: str = self.get_argument("allowed_localizations", "")
            if allowed_localizations:
                attrs["allowed_localizations"] = \
                    [x.strip() for x in allowed_localizations.split(",")
                     if len(x) > 0 and not x.isspace()]
            else:
                attrs["allowed_localizations"] = []

            attrs["languages"] = self.get_arguments("languages")

            self.get_bool(attrs, "submissions_download_allowed")
            self.get_bool(attrs, "allow_questions")
            self.get_bool(attrs, "allow_user_tests")
            self.get_bool(attrs, "allow_unofficial_submission_before_analysis_mode")
            self.get_bool(attrs, "block_hidden_participations")
            self.get_bool(attrs, "allow_password_authentication")
            self.get_bool(attrs, "allow_registration")
            self.get_bool(attrs, "allow_delay_requests")
            self.get_bool(attrs, "ip_restriction")
            self.get_bool(attrs, "ip_autologin")

            self.get_string(attrs, "token_mode")
            self.get_int(attrs, "token_max_number")
            self.get_timedelta_sec(attrs, "token_min_interval")
            self.get_int(attrs, "token_gen_initial")
            self.get_int(attrs, "token_gen_number")
            self.get_timedelta_min(attrs, "token_gen_interval")
            self.get_int(attrs, "token_gen_max")

            self.get_int(attrs, "max_submission_number")
            self.get_int(attrs, "max_user_test_number")
            self.get_timedelta_sec(attrs, "min_submission_interval")
            self.get_timedelta_sec(attrs, "min_submission_interval_grace_period")
            self.get_timedelta_sec(attrs, "min_user_test_interval")

            self.get_datetime(attrs, "start")
            self.get_datetime(attrs, "stop")

            self.get_string(attrs, "timezone", empty=None)
            self.get_timedelta_sec(attrs, "per_user_time")
            self.get_int(attrs, "score_precision")

            self.get_bool(attrs, "analysis_enabled")
            self.get_datetime(attrs, "analysis_start")
            self.get_datetime(attrs, "analysis_stop")

            # Update the contest first
            contest.set_attrs(attrs)

            # Folder assignment (relationship)
            folder_id_str = self.get_argument("folder_id", None)
            if folder_id_str is None or folder_id_str == "" or folder_id_str == "none":
                contest.folder = None
            else:
                contest.folder = self.safe_get_item(ContestFolder, int(folder_id_str))

            new_start = attrs.get("start")
            if new_start is not None and new_start != old_start:
                time_diff = old_start - new_start
                for participation in contest.participations:
                    if participation.delay_time > timedelta():
                        new_delay = participation.delay_time + time_diff
                        participation.delay_time = max(new_delay, timedelta())

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s).", repr(error))
            self.redirect(self.url("contest", contest_id))
            return

        if self.try_commit():
            # Update the contest on RWS.
            self.service.proxy_service.reinitialize()
        self.redirect(self.url("contest", contest_id))


class OverviewHandler(BaseHandler):
    """Home page handler, with queue and workers statuses.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id: str | None = None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.render("overview.html", **self.r_params)


class ResourcesListHandler(BaseHandler):
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id: str | None = None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["resource_addresses"] = {}
        services = get_service_shards("ResourceService")
        for i in range(services):
            self.r_params["resource_addresses"][i] = get_service_address(
                ServiceCoord("ResourceService", i)).ip
        self.render("resourceslist.html", **self.r_params)


class ContestListHandler(SimpleHandler("contests.html")):
    """Get returns the list of all contests, post perform operations on
    a specific contest (removing them from CMS).

    """

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        contest_id = self.get_argument("contest_id")
        operation = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("contests", contest_id, "remove")
            # Open asking for remove page
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("contests"))


class RemoveContestHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually removes
    the contest from CMS.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)
        submission_query = self.sql_session.query(Submission)\
            .join(Submission.participation)\
            .filter(Participation.contest == contest)

        self.contest = contest
        self.render_params_for_remove_confirmation(submission_query)
        
        self.r_params["task_count"] = len(contest.tasks)
        self.r_params["other_contests"] = self.sql_session.query(Contest)\
            .filter(Contest.id != contest.id)\
            .filter(~Contest.name.like(r'\_\_%', escape='\\'))\
            .filter(~Contest.training_day.has())\
            .order_by(Contest.name)\
            .all()
        
        self.render("contest_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, contest_id):
        """Handle DELETE request with task handling options."""
        contest = self.safe_get_item(Contest, contest_id)
        
        try:
            action = self.get_argument("action", "detach")
            assert action in ["move", "detach", "delete_all"], \
                "Invalid action specified"
            
            target_contest_id = None
            if action == "move":
                target_contest_id = self.get_argument("target_contest_id", None)
                assert target_contest_id, \
                    "Target contest must be specified when moving tasks"
                assert target_contest_id != str(contest_id), \
                    "Target contest cannot be the same as the contest being deleted"
            
            self._remove_contest_with_action(contest, action, target_contest_id)
            
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Error removing contest", repr(error))
            self.write("error")
            return
        
        # Maybe they'll want to do this again (for another contest)
        self.write("../../contests")
    
    def _remove_contest_with_action(self, contest, action, target_contest_id):
        """Remove contest with specified action for tasks.
        
        This is a thin wrapper that calls the standalone helper function.
        
        contest: Contest object to remove
        action: One of "move", "detach", or "delete_all"
        target_contest_id: ID of target contest (required if action is "move")
        """
        target_contest = None
        if action == "move":
            target_contest = self.safe_get_item(Contest, target_contest_id)
        
        remove_contest_with_action(self.sql_session, contest, action, target_contest)
        
        if self.try_commit():
            self.service.proxy_service.reinitialize()
            self.service.add_notification(
                make_datetime(), 
                "Contest removed successfully",
                f"Contest removed with action: {action}")
