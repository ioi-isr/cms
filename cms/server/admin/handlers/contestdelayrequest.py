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
import csv
import io
import logging
import re
from datetime import datetime, timedelta

import collections
try:
    collections.MutableMapping
except:
    collections.MutableMapping = collections.abc.MutableMapping

from sqlalchemy import not_
import tornado.web

from cms.db import Contest, DelayRequest, Participation
from cms.server.contest.phase_management import compute_actual_phase
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def compute_participation_status(contest, participation, timestamp):
    """Compute the status class and label for a participation.
    
    Args:
        contest: The Contest object
        participation: The Participation object
        timestamp: The current timestamp
    
    Returns:
        tuple: (status_class, status_label)
    """
    actual_phase, _, _, _, _ = compute_actual_phase(
        timestamp,
        contest.start,
        contest.stop,
        contest.analysis_start,
        contest.analysis_stop,
        contest.per_user_time,
        participation.starting_time,
        participation.delay_time,
        participation.extra_time,
    )
    
    if participation.starting_time is None:
        if actual_phase == -2:
            status_class = "pre-contest"
            status_label = "Pre contest"
        elif actual_phase <= 0:
            status_class = "can-start"
            status_label = "Can start"
        else:
            status_class = "missed"
            status_label = "Missed"
    elif actual_phase == 0:
        status_class = "in-contest"
        status_label = "In contest"
    else:
        status_class = "finished"
        status_label = "Finished"
    
    return status_class, status_label


class DelaysAndExtraTimesHandler(BaseHandler):
    """Page to see and manage delay requests and extra times for all contestants.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()

        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .filter(not_(Participation.hidden))\
            .order_by(Participation.id)\
            .all()
        
        # Compute status for each participation
        participation_statuses = []
        for participation in participations:
            status_class, status_label = compute_participation_status(
                self.contest, participation, self.timestamp
            )
            
            participation_statuses.append({
                'participation': participation,
                'status_class': status_class,
                'status_label': status_label,
            })
        
        self.r_params["participation_statuses"] = participation_statuses
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
            raise tornado.web.HTTPError(400, "Delay request is not pending")

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
            raise tornado.web.HTTPError(400, "Delay request is not pending")

        rejection_reason = self.get_argument("rejection_reason", "").strip()

        delay_request.status = 'rejected'
        delay_request.processed_timestamp = make_datetime()
        delay_request.admin = self.current_user
        delay_request.rejection_reason = rejection_reason if rejection_reason else None

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


class ExportDelaysAndExtraTimesHandler(BaseHandler):
    """Export delays and extra times table as CSV.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .filter(not_(Participation.hidden))\
            .order_by(Participation.id)\
            .all()

        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            'User',
            'Username',
            'Delay Time (seconds)',
            'Planned Start Time (UTC)',
            'Actual Start Time (UTC)',
            'IP Address',
            'Extra Time (seconds)',
            'Status'
        ])
        
        for participation in participations:
            starting_time = participation.starting_time.strftime('%Y-%m-%d %H:%M:%S') if participation.starting_time else '-'
            delay_seconds = int(participation.delay_time.total_seconds())
            
            if participation.delay_time.total_seconds() > 0:
                planned_start = self.contest.start + participation.delay_time
                planned_start_str = planned_start.strftime('%Y-%m-%d %H:%M:%S')
            else:
                planned_start_str = self.contest.start.strftime('%Y-%m-%d %H:%M:%S')
            
            extra_seconds = int(participation.extra_time.total_seconds())
            ip_addresses = participation.starting_ip_addresses if participation.starting_ip_addresses else '-'
            
            # Compute status for this participation
            _, status_label = compute_participation_status(
                self.contest, participation, self.timestamp
            )
            
            writer.writerow([
                f"{participation.user.first_name} {participation.user.last_name}",
                participation.user.username,
                delay_seconds,
                planned_start_str,
                starting_time,
                ip_addresses,
                extra_seconds,
                status_label
            ])
        
        start_date = self.contest.start.strftime('%Y%m%d')
        contest_slug = re.sub(r'[^A-Za-z0-9_-]+', '_', self.contest.name)
        filename = f"{start_date}_{contest_slug}_attendance.csv"
        
        self.set_header('Content-Type', 'text/csv')
        self.set_header('Content-Disposition', 
                       f'attachment; filename="{filename}"')
        self.write(output.getvalue())
        self.finish()


class RemoveAllDelaysAndExtraTimesHandler(BaseHandler):
    """Remove all delays and extra times for all participations in a contest.

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")
        
        self.contest = self.safe_get_item(Contest, contest_id)
        
        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .all()
        
        count = 0
        for participation in participations:
            if participation.delay_time.total_seconds() > 0 or participation.extra_time.total_seconds() > 0:
                participation.delay_time = timedelta()
                participation.extra_time = timedelta()
                count += 1
        
        if self.try_commit():
            logger.info("All delays and extra times removed for contest %s by admin %s (%d participations affected)",
                       self.contest.name,
                       self.current_user.name,
                       count)
        
        self.redirect(ref)


class EraseAllStartTimesHandler(BaseHandler):
    """Erase all starting times for all participations in a contest.

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")
        
        self.contest = self.safe_get_item(Contest, contest_id)
        
        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .all()
        
        count = 0
        for participation in participations:
            if participation.starting_time is not None:
                participation.starting_time = None
                count += 1
        
        if self.try_commit():
            logger.info("All starting times erased for contest %s by admin %s (%d participations affected)",
                       self.contest.name,
                       self.current_user.name,
                       count)
        
        self.redirect(ref)


class ResetAllIPAddressesHandler(BaseHandler):
    """Reset all IP addresses for all participations in a contest.

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")
        
        self.contest = self.safe_get_item(Contest, contest_id)
        
        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .all()
        
        count = 0
        for participation in participations:
            if participation.starting_ip_addresses is not None:
                participation.starting_ip_addresses = None
                count += 1
        
        if self.try_commit():
            logger.info("All IP addresses reset for contest %s by admin %s (%d participations affected)",
                       self.contest.name,
                       self.current_user.name,
                       count)
        
        self.redirect(ref)


class AdminConfiguredDelayHandler(BaseHandler):
    """Handler for admin to create and approve a delay request for a user.

    This allows admins to set a delay for a student based on a configured
    start time, creating a delay request with 'admin_configured' status.
    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id):
        ref = self.url("contest", contest_id, "delays_and_extra_times")

        self.contest = self.safe_get_item(Contest, contest_id)

        participation_id = self.get_argument("participation_id", "")
        requested_start_time_str = self.get_argument("requested_start_time", "")
        reason = self.get_argument("reason", "").strip()

        if not participation_id or not requested_start_time_str or not reason:
            self.service.add_notification(
                make_datetime(),
                "Missing fields",
                "Please fill in all required fields: user, start time, and reason."
            )
            self.redirect(ref)
            return

        participation = self.safe_get_item(Participation, participation_id)

        if participation.contest_id != self.contest.id:
            raise tornado.web.HTTPError(404)

        try:
            # Parse HTML5 datetime-local format: YYYY-MM-DDTHH:MM
            requested_start_time = datetime.strptime(
                requested_start_time_str, "%Y-%m-%dT%H:%M"
            )
        except (ValueError, TypeError):
            self.service.add_notification(
                make_datetime(),
                "Invalid date",
                "The start time format is invalid."
            )
            self.redirect(ref)
            return

        contest_start = self.contest.start
        delay_seconds = (requested_start_time - contest_start).total_seconds()

        if delay_seconds < 0:
            self.service.add_notification(
                make_datetime(),
                "Invalid start time",
                "The requested start time cannot be before the contest start time."
            )
            self.redirect(ref)
            return

        now = make_datetime()

        delay_request = DelayRequest(
            request_timestamp=now,
            requested_start_time=requested_start_time,
            reason=reason,
            status='admin_configured',
            processed_timestamp=now,
            participation=participation,
            admin=self.current_user
        )
        self.sql_session.add(delay_request)

        participation.delay_time = timedelta(seconds=delay_seconds)

        if self.try_commit():
            logger.info(
                "Admin %s configured delay for user %s in contest %s: "
                "start time %s, delay %d seconds, reason: %s",
                self.current_user.name,
                participation.user.username,
                self.contest.name,
                requested_start_time,
                delay_seconds,
                reason
            )

        self.redirect(ref)
