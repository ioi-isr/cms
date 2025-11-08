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

"""Delay request-related handlers for CWS.

"""

import logging
from datetime import datetime

import collections
try:
    collections.MutableMapping
except:
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms.db import DelayRequest
from cms.server import multi_contest
from cmscommon.datetime import make_datetime, get_timezone, utc
from .contest import ContestHandler


logger = logging.getLogger(__name__)


def N_(msgid):
    return msgid


class DelayRequestHandler(ContestHandler):
    """Called when the user submits a delay request.

    """
    @tornado.web.authenticated
    @multi_contest
    def post(self):
        requested_start_time_str = self.get_argument("requested_start_time", "")
        reason = self.get_argument("reason", "")

        if not requested_start_time_str or not reason:
            self.notify_error(N_("Invalid request"),
                            N_("Please provide both a requested start time and a reason."))
            self.redirect(self.contest_url("communication"))
            return

        if len(reason) > DelayRequest.MAX_REASON_LENGTH:
            self.notify_error(N_("Reason too long"),
                            N_("The reason must be at most %d characters long."),
                            DelayRequest.MAX_REASON_LENGTH)
            self.redirect(self.contest_url("communication"))
            return

        try:
            naive_dt = datetime.fromisoformat(requested_start_time_str)
        except (ValueError, TypeError):
            self.notify_error(N_("Invalid date"),
                            N_("The requested start time is not valid."))
            self.redirect(self.contest_url("communication"))
            return

        tz = get_timezone(self.current_user.user, self.contest)
        try:
            if hasattr(tz, "localize"):
                local_dt = tz.localize(naive_dt, is_dst=None)
            else:
                local_dt = naive_dt.replace(tzinfo=tz)
            
            utc_dt = local_dt.astimezone(utc)
            requested_start_time = utc_dt.replace(tzinfo=None)
        except Exception as e:
            logger.error("Timezone conversion failed for delay request from user %s "
                        "in contest %s. Input: %s, Timezone: %s. Error: %s",
                        self.current_user.user.username,
                        self.contest.name,
                        requested_start_time_str,
                        getattr(tz, "zone", str(tz)),
                        e)
            self.notify_error(N_("Couldn't interpret requested time"),
                            N_("We couldn't interpret the requested start time in your time zone. "
                               "This can happen around daylight saving time changes. "
                               "Please pick a different time or adjust the date."))
            self.redirect(self.contest_url("communication"))
            return

        delay_request = DelayRequest(
            request_timestamp=self.timestamp,
            requested_start_time=requested_start_time,
            reason=reason,
            status='pending',
            participation=self.current_user
        )
        self.sql_session.add(delay_request)

        try:
            self.sql_session.commit()
            self.notify_success(N_("Request received"),
                              N_("Your delay request has been received and is pending approval."))
        except Exception as e:
            logger.error("Error submitting delay request: %s", e)
            self.notify_error(N_("Error"),
                            N_("An error occurred while submitting your request."))

        self.redirect(self.contest_url("communication"))
