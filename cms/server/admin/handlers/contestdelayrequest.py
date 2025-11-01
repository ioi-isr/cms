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

"""Delay request-related handlers for AWS for a specific contest.

"""

from abc import ABCMeta, abstractmethod
import logging
from datetime import timedelta

import collections
try:
    collections.MutableMapping
except:
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms.db import Contest, DelayRequest, Participation
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class DelaysAndExtraTimesHandler(BaseHandler):
    """Page to see and manage delay requests and extra times for all contestants.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["participations"] = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .order_by(Participation.id)\
            .all()
        self.r_params["delay_requests"] = self.sql_session.query(DelayRequest)\
            .join(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .order_by(DelayRequest.request_timestamp.desc())\
            .all()
        self.render("delays_and_extra_times.html", **self.r_params)


class DelayRequestActionHandler(BaseHandler, metaclass=ABCMeta):
    """Base class for handlers for actions on delay requests."""

    @abstractmethod
    def process_delay_request(self, delay_request: DelayRequest):
        """Called on POST requests. Perform the appropriate action on the
        delay request."""
        pass

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id, delay_request_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")

        delay_request = self.safe_get_item(DelayRequest, delay_request_id)
        self.contest = self.safe_get_item(Contest, contest_id)

        if self.contest is not delay_request.participation.contest:
            raise tornado.web.HTTPError(404)

        self.process_delay_request(delay_request)
        self.redirect(ref)


class DelayRequestApproveHandler(DelayRequestActionHandler):
    """Called when the admin approves a delay request.

    """
    def process_delay_request(self, delay_request):
        if delay_request.status != 'pending':
            logger.warning("Attempt to approve non-pending delay request %s", delay_request.id)
            return

        participation = delay_request.participation
        contest_start = participation.contest.start
        requested_start = delay_request.requested_start_time
        
        delay_seconds = (requested_start - contest_start).total_seconds()
        
        if delay_seconds > 0:
            participation.delay_time = timedelta(seconds=delay_seconds)
        
        delay_request.status = 'approved'
        delay_request.processed_timestamp = make_datetime()
        delay_request.admin = self.current_user

        if self.try_commit():
            logger.info("Delay request %s approved by admin %s for user %s in contest %s",
                       delay_request.id,
                       self.current_user.name,
                       participation.user.username,
                       participation.contest.name)


class DelayRequestRejectHandler(DelayRequestActionHandler):
    """Called when the admin rejects a delay request.

    """
    def process_delay_request(self, delay_request):
        if delay_request.status != 'pending':
            logger.warning("Attempt to reject non-pending delay request %s", delay_request.id)
            return

        delay_request.status = 'rejected'
        delay_request.processed_timestamp = make_datetime()
        delay_request.admin = self.current_user

        if self.try_commit():
            logger.info("Delay request %s rejected by admin %s for user %s in contest %s",
                       delay_request.id,
                       self.current_user.name,
                       delay_request.participation.user.username,
                       delay_request.participation.contest.name)


class RemoveDelayAndExtraTimeHandler(BaseHandler):
    """Called when the admin removes delay and extra time for a participation.

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id, participation_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")

        participation = self.safe_get_item(Participation, participation_id)
        self.contest = self.safe_get_item(Contest, contest_id)

        if self.contest is not participation.contest:
            raise tornado.web.HTTPError(404)

        participation.delay_time = timedelta()
        participation.extra_time = timedelta()

        if self.try_commit():
            logger.info("Delay and extra time removed for user %s in contest %s by admin %s",
                       participation.user.username,
                       participation.contest.name,
                       self.current_user.name)

        self.redirect(ref)
