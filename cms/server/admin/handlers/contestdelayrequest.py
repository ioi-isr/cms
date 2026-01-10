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
from datetime import timedelta

import collections
try:
    collections.MutableMapping
except:
    collections.MutableMapping = collections.abc.MutableMapping

from sqlalchemy import not_
import tornado.web

from cms.db import Contest, DelayRequest, Participation, Student
from cms.server.contest.phase_management import compute_actual_phase
from cms.server.util import check_training_day_eligibility, get_all_student_tags
from cmscommon.datetime import make_datetime, utc
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def get_participation_main_group(contest, participation):
    """Get the main group for a participation in a training day contest.

    Args:
        contest: The Contest object
        participation: The Participation object (in the training day contest)

    Returns:
        TrainingDayGroup or None: The main group if found, None otherwise
    """
    training_day = contest.training_day
    if training_day is None or len(training_day.groups) == 0:
        return None

    main_group_tags = {g.tag_name: g for g in training_day.groups}
    training_program = training_day.training_program

    # Student.participation_id refers to the managing contest participation,
    # not the training day contest participation. Match by user_id instead.
    for student in training_program.students:
        if student.participation.user_id == participation.user_id:
            student_tags = set(student.student_tags or [])
            matching_tags = student_tags & set(main_group_tags.keys())
            if len(matching_tags) == 1:
                return main_group_tags[next(iter(matching_tags))]
            break

    return None


def compute_participation_status(contest, participation, timestamp,
                                  main_group_start=None, main_group_end=None):
    """Compute the status class and label for a participation.

    Args:
        contest: The Contest object
        participation: The Participation object
        timestamp: The current timestamp
        main_group_start: Optional per-group start time for training days
        main_group_end: Optional per-group end time for training days

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
        main_group_start,
        main_group_end,
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
        self.r_params["timezone"] = utc

        participations = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .filter(not_(Participation.hidden))\
            .order_by(Participation.id)\
            .all()

        # Compute status for each participation
        participation_statuses = []
        for participation in participations:
            # For training day contests, check eligibility and skip ineligible students
            if self.contest.training_day is not None:
                is_eligible, _, _ = check_training_day_eligibility(
                    self.sql_session, participation, self.contest.training_day
                )
                if not is_eligible:
                    continue  # Skip ineligible students

            main_group = get_participation_main_group(self.contest, participation)
            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None

            status_class, status_label = compute_participation_status(
                self.contest, participation, self.timestamp,
                main_group_start, main_group_end
            )

            participation_statuses.append({
                'participation': participation,
                'status_class': status_class,
                'status_label': status_label,
                'main_group': main_group,
            })

        self.r_params["participation_statuses"] = participation_statuses
        delay_requests = self.sql_session.query(DelayRequest)\
            .join(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .order_by(DelayRequest.request_timestamp.desc())\
            .all()

        # Compute warnings for delay requests where requested start is earlier than group start
        delay_request_warnings = {}
        for req in delay_requests:
            if req.status == 'pending':
                main_group = get_participation_main_group(self.contest, req.participation)
                if main_group and main_group.start_time:
                    if req.requested_start_time < main_group.start_time:
                        delay_request_warnings[req.id] = {
                            'group_name': main_group.tag_name,
                            'group_start': main_group.start_time,
                        }

        self.r_params["delay_requests"] = delay_requests
        self.r_params["delay_request_warnings"] = delay_request_warnings

        # For training day contests, compute ineligible students
        # Note: We use "ineligible_training_program" instead of "training_program" to avoid
        # conflicting with base.html's sidebar logic which shows training program sidebar
        # when "training_program" is defined. We want to show the contest sidebar for
        # training day contests.
        self.r_params["ineligible_students"] = []
        self.r_params["all_student_tags"] = []
        self.r_params["ineligible_training_program"] = None
        training_day = self.contest.training_day
        if training_day is not None and len(training_day.groups) > 0:
            main_group_tags = {g.tag_name for g in training_day.groups}
            training_program = training_day.training_program
            self.r_params["ineligible_training_program"] = training_program

            # Collect all unique student tags for autocomplete (using shared utility)
            self.r_params["all_student_tags"] = get_all_student_tags(training_program)

            # Find students with 0 or >1 main group tags
            ineligible = []
            for student in training_program.students:
                student_tags = set(student.student_tags or [])
                matching_tags = student_tags & main_group_tags
                if len(matching_tags) != 1:
                    ineligible.append({
                        'student': student,
                        'matching_tags': sorted(matching_tags),
                        'reason': 'no main group' if len(matching_tags) == 0 else 'multiple main groups',
                    })
            self.r_params["ineligible_students"] = ineligible

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

            main_group = get_participation_main_group(self.contest, participation)
            main_group_start = main_group.start_time if main_group else None
            main_group_end = main_group.end_time if main_group else None

            if participation.delay_time.total_seconds() > 0:
                planned_start = self.contest.start + participation.delay_time
                planned_start_str = planned_start.strftime('%Y-%m-%d %H:%M:%S')
            elif main_group_start:
                planned_start_str = main_group_start.strftime('%Y-%m-%d %H:%M:%S')
            else:
                planned_start_str = self.contest.start.strftime('%Y-%m-%d %H:%M:%S')

            extra_seconds = int(participation.extra_time.total_seconds())
            ip_addresses = participation.starting_ip_addresses if participation.starting_ip_addresses else '-'

            # Compute status for this participation
            _, status_label = compute_participation_status(
                self.contest, participation, self.timestamp,
                main_group_start, main_group_end
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
