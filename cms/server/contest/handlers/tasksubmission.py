#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2016 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Submission-related handlers for CWS for a specific task.

"""

import logging
import re

import collections

from cms.db.task import Task
from cms.db.user import Participation

try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web
from sqlalchemy.orm import joinedload

from cms import config, FEEDBACK_LEVEL_FULL
from cms.db import Submission, SubmissionResult
from cms.grading.languagemanager import get_language
from cms.grading.scoring import task_score
from cms.server import multi_contest
from cms.server.contest.submission import get_submission_count, \
    UnacceptableSubmission, accept_submission
from cms.server.contest.tokening import \
    UnacceptableToken, TokenAlreadyPlayed, accept_token, tokens_available
from cmscommon.crypto import encrypt_number
from cmscommon.mimetypes import get_type_for_file_name
from .contest import ContestHandler, FileHandler, api_login_required
from ..phase_management import actual_phase_required


logger = logging.getLogger(__name__)


# Dummy function to mark translatable strings.
def N_(msgid):
    return msgid


def _get_managing_participation(sql_session, training_day, user):
    """Get the managing contest participation for a user in a training day.

    training_day: the training day.
    user: the user to look up.

    return: the Participation in the managing contest, or None if not found.
    """
    managing_contest = training_day.training_program.managing_contest
    return (
        sql_session.query(Participation)
        .filter(Participation.contest == managing_contest)
        .filter(Participation.user == user)
        .first()
    )


class SubmitHandler(ContestHandler):
    """Handles the received submissions.

    """

    @tornado.web.authenticated
    @actual_phase_required(0, 1, 2, 3)
    @multi_contest
    def post(self, task_name):
        participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        # Reject submission if the contest disallow unofficial submission outside of official window or analysis mode
        if 0 < self.r_params["actual_phase"] < 3 and \
                not self.contest.allow_unofficial_submission_before_analysis_mode:
            self.redirect(self.contest_url())
            return

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        # Only set the official bit when the user can compete and we are not in
        # analysis mode.
        official = self.r_params["actual_phase"] == 0

        # Determine the participation and training day for the submission.
        # For training day submissions, we use the managing contest's participation
        # but record which training day the submission was made via.
        training_day = self.contest.training_day
        submission_participation = participation
        if training_day is not None:
            # This is a training day submission - use managing contest participation
            managing_participation = _get_managing_participation(
                self.sql_session, training_day, participation.user
            )
            if managing_participation is None:
                # User doesn't have a participation in the managing contest
                raise tornado.web.HTTPError(403)
            submission_participation = managing_participation

        query_args = dict()

        try:
            submission = accept_submission(
                self.sql_session, self.service.file_cacher, submission_participation,
                task, self.timestamp, self.request.files,
                self.get_argument("language", None), official)
            # Set the training day reference if submitting via a training day
            if training_day is not None:
                submission.training_day = training_day
            self.sql_session.commit()
        except UnacceptableSubmission as e:
            logger.info("Sent error: `%s' - `%s'", e.subject, e.formatted_text)
            self.notify_error(e.subject, e.text, e.text_params)
        else:
            self.service.evaluation_service.new_submission(
                submission_id=submission.id)
            self.notify_success(N_("Submission received"),
                                N_("Your submission has been received "
                                   "and is currently being evaluated."))
            # The argument (encrypted submission id) is not used by CWS
            # (nor it discloses information to the user), but it is
            # useful for automatic testing to obtain the submission id).
            query_args["submission_id"] = \
                encrypt_number(submission.id, config.web_server.secret_key)

        self.redirect(self.contest_url("tasks", task.name, "submissions",
                                       **query_args))


class TaskSubmissionsHandler(ContestHandler):
    """Shows the data of a task in the contest.

    """
    @tornado.web.authenticated
    @actual_phase_required(0, 1, 2, 3, 4)
    @multi_contest
    def get(self, task_name):
        participation: Participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        # Determine the context for filtering submissions
        training_day = self.contest.training_day
        is_task_archive = (
            self.training_program is not None and training_day is None
        )

        # For training day context: submissions are stored with managing contest
        # participation, so we need to find that participation and filter by
        # training_day_id
        managing_participation = None
        if training_day is not None:
            # Get the managing contest participation for this user
            managing_participation = _get_managing_participation(
                self.sql_session, training_day, participation.user
            )
            if managing_participation is None:
                submissions = []
            else:
                # Only show submissions made via this training day
                submissions: list[Submission] = (
                    self.sql_session.query(Submission)
                    .filter(Submission.participation == managing_participation)
                    .filter(Submission.task == task)
                    .filter(Submission.training_day_id == training_day.id)
                    .order_by(Submission.timestamp.desc())
                    .options(joinedload(Submission.token))
                    .options(joinedload(Submission.results))
                    .options(joinedload(Submission.training_day))
                    .all()
                )
        else:
            # Regular contest or task archive - show all submissions
            submissions: list[Submission] = (
                self.sql_session.query(Submission)
                .filter(Submission.participation == participation)
                .filter(Submission.task == task)
                .order_by(Submission.timestamp.desc())
                .options(joinedload(Submission.token))
                .options(joinedload(Submission.results))
                .options(joinedload(Submission.training_day))
                .all()
            )

        # For task archive, group submissions by source
        archive_submissions = []
        training_day_submissions = {}  # training_day_id -> (training_day, submissions)
        if is_task_archive:
            for s in submissions:
                if s.training_day_id is None:
                    archive_submissions.append(s)
                else:
                    if s.training_day_id not in training_day_submissions:
                        training_day_submissions[s.training_day_id] = (
                            s.training_day, []
                        )
                    training_day_submissions[s.training_day_id][1].append(s)

        # Use managing_participation for score/token/count calculations in
        # training-day context, since submissions are stored there
        score_participation = (
            managing_participation if managing_participation is not None
            else participation
        )

        public_score, is_public_score_partial = task_score(
            score_participation, task, public=True, rounded=True)
        tokened_score, is_tokened_score_partial = task_score(
            score_participation, task, only_tokened=True, rounded=True)
        # These two should be the same, anyway.
        is_score_partial = is_public_score_partial or is_tokened_score_partial

        submissions_left_contest = None
        if self.contest.max_submission_number is not None:
            submissions_c = \
                get_submission_count(self.sql_session, score_participation,
                                     contest=self.contest)
            submissions_left_contest = \
                self.contest.max_submission_number - submissions_c

        submissions_left_task = None
        if task.max_submission_number is not None:
            submissions_left_task = \
                task.max_submission_number - len(submissions)

        submissions_left = submissions_left_contest
        if submissions_left_task is not None and \
            (submissions_left_contest is None or
             submissions_left_contest > submissions_left_task):
            submissions_left = submissions_left_task

        # Make sure we do not show negative value if admins changed
        # the maximum
        if submissions_left is not None:
            submissions_left = max(0, submissions_left)

        tokens_info = tokens_available(score_participation, task, self.timestamp)

        download_allowed = self.contest.submissions_download_allowed
        self.render("task_submissions.html",
                    task=task, submissions=submissions,
                    archive_submissions=archive_submissions,
                    training_day_submissions=training_day_submissions,
                    is_task_archive=is_task_archive,
                    public_score=public_score,
                    tokened_score=tokened_score,
                    is_score_partial=is_score_partial,
                    tokens_task=task.token_mode,
                    tokens_info=tokens_info,
                    submissions_left=submissions_left,
                    submissions_download_allowed=download_allowed,
                    **self.r_params)


class SubmissionStatusHandler(ContestHandler):

    STATUS_TEXT = {
        SubmissionResult.COMPILING: N_("Compiling..."),
        SubmissionResult.COMPILATION_FAILED: N_("Compilation failed"),
        SubmissionResult.EVALUATING: N_("Evaluating..."),
        SubmissionResult.EVALUATION_FAILED: N_("Evaluation system error"),
        SubmissionResult.SCORING: N_("Scoring..."),
        SubmissionResult.SCORED: N_("Evaluated"),
    }

    refresh_cookie = False

    def add_task_score(self, participation: Participation, task: Task, data: dict):
        """Add the task score information to the dict to be returned.

        participation: user for which we want the score.
        task: task for which we want the score.
        data: where to put the data; all fields will start with "task",
            followed by "public" if referring to the public scores, or
            "tokened" if referring to the total score (always limited to
            tokened submissions); for both public and tokened, the fields are:
            "score" and "score_message"; in addition we have
            "task_is_score_partial" as partial info is the same for both.

        """
        # Just to preload all information required to compute the task score.
        self.sql_session.query(Submission)\
            .filter(Submission.participation == participation)\
            .filter(Submission.task == task)\
            .options(joinedload(Submission.token))\
            .options(joinedload(Submission.results))\
            .all()
        data["task_public_score"], public_score_is_partial = \
            task_score(participation, task, public=True, rounded=True)
        data["task_tokened_score"], tokened_score_is_partial = \
            task_score(participation, task, only_tokened=True, rounded=True)
        # These two should be the same, anyway.
        data["task_score_is_partial"] = \
            public_score_is_partial or tokened_score_is_partial

        score_type = task.active_dataset.score_type_object
        data["task_public_score_message"] = score_type.format_score(
            data["task_public_score"], score_type.max_public_score, None,
            task.score_precision, translation=self.translation)
        data["task_tokened_score_message"] = score_type.format_score(
            data["task_tokened_score"], score_type.max_score, None,
            task.score_precision, translation=self.translation)

    @api_login_required
    @actual_phase_required(0, 1, 2, 3, 4)
    @multi_contest
    def get(self, task_name, opaque_id):
        participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        submission = self.get_submission(task, opaque_id)
        if submission is None:
            raise tornado.web.HTTPError(404)

        sr = submission.get_result(task.active_dataset)

        data = {}

        if sr is None:
            # implicit compiling state while result is not created
            data["status"] = SubmissionResult.COMPILING
        else:
            data["status"] = sr.get_status()

        data["status_text"] = self._(self.STATUS_TEXT[data["status"]])

        # For terminal statuses we add the scores information to the payload.
        if data["status"] == SubmissionResult.COMPILATION_FAILED \
                or data["status"] == SubmissionResult.SCORED:
            self.add_task_score(submission.participation, task, data)

            score_type = task.active_dataset.score_type_object
            if score_type.max_public_score > 0:
                data["max_public_score"] = \
                    round(score_type.max_public_score, task.score_precision)
                if data["status"] == SubmissionResult.SCORED:
                    data["public_score"] = \
                        round(sr.public_score, task.score_precision)
                    data["public_score_message"] = score_type.format_score(
                        sr.public_score, score_type.max_public_score,
                        sr.public_score_details, task.score_precision,
                        translation=self.translation)
            if score_type.max_public_score < score_type.max_score:
                data["max_score"] = \
                    round(score_type.max_score, task.score_precision)
                if data["status"] == SubmissionResult.SCORED \
                        and (submission.token is not None
                             or self.r_params["actual_phase"] == 3):
                    data["score"] = \
                        round(sr.score, task.score_precision)
                    data["score_message"] = score_type.format_score(
                        sr.score, score_type.max_score,
                        sr.score_details, task.score_precision,
                        translation=self.translation)

        self.write(data)


class SubmissionDetailsHandler(ContestHandler):

    refresh_cookie = False

    @api_login_required
    @actual_phase_required(0, 1, 2, 3, 4)
    @multi_contest
    def get(self, task_name, opaque_id):
        participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        submission = self.get_submission(task, opaque_id)
        if submission is None:
            raise tornado.web.HTTPError(404)

        sr = submission.get_result(task.active_dataset)
        score_type = task.active_dataset.score_type_object

        details = None
        if sr is not None and sr.scored():
            # During analysis mode we show the full feedback regardless of
            # what the task says.
            is_analysis_mode = self.r_params["actual_phase"] == 3
            if submission.tokened() or is_analysis_mode:
                raw_details = sr.score_details
            else:
                raw_details = sr.public_score_details

            if is_analysis_mode:
                feedback_level = FEEDBACK_LEVEL_FULL
            else:
                feedback_level = task.feedback_level

            details = score_type.get_html_details(
                raw_details, feedback_level, translation=self.translation)

        self.render("submission_details.html", sr=sr, details=details,
                    **self.r_params)


class SubmissionFileHandler(FileHandler):
    """Send back a submission file.

    """
    @tornado.web.authenticated
    @actual_phase_required(0, 1, 2, 3, 4)
    @multi_contest
    def get(self, task_name, opaque_id, filename):
        participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        if not self.contest.submissions_download_allowed:
            raise tornado.web.HTTPError(404)

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        submission = self.get_submission(task, opaque_id)
        if submission is None:
            raise tornado.web.HTTPError(404)

        # The following code assumes that submission.files is a subset
        # of task.submission_format. CWS will always ensure that for new
        # submissions, yet, if the submission_format changes during the
        # competition, this may not hold anymore for old submissions.

        # filename is the name used by the browser, hence is something
        # like 'foo.c' (and the extension is CMS's preferred extension
        # for the language). To retrieve the right file, we need to
        # decode it to 'foo.%l'.
        stored_filename = filename
        if submission.language is not None:
            extension = get_language(submission.language).source_extension
            stored_filename = re.sub(r'%s$' % extension, '.%l', filename)

        if stored_filename not in submission.files:
            raise tornado.web.HTTPError(404)

        digest = submission.files[stored_filename].digest
        self.sql_session.close()

        mimetype = get_type_for_file_name(filename)
        if mimetype is None:
            mimetype = 'application/octet-stream'

        self.fetch(digest, mimetype, filename)


class UseTokenHandler(ContestHandler):
    """Called when the user try to use a token on a submission.

    """
    @tornado.web.authenticated
    @actual_phase_required(0)
    @multi_contest
    def post(self, task_name, opaque_id):
        participation = self.current_user

        if not participation.unrestricted:
            if self.training_program is None and participation.starting_time is None:
                raise tornado.web.HTTPError(403)

        task = self.get_task(task_name)
        if task is None:
            raise tornado.web.HTTPError(404)

        # Check task visibility for training day contests
        if not self.can_access_task(task):
            raise tornado.web.HTTPError(404)

        submission = self.get_submission(task, opaque_id)
        if submission is None:
            raise tornado.web.HTTPError(404)

        try:
            accept_token(self.sql_session, submission, self.timestamp)
            self.sql_session.commit()
        except UnacceptableToken as e:
            self.notify_error(e.subject, e.text)
        except TokenAlreadyPlayed as e:
            self.notify_warning(e.subject, e.text)
        else:
            # Inform ProxyService and eventually the ranking that the
            # token has been played.
            self.service.proxy_service.submission_tokened(
                submission_id=submission.id)

            logger.info("Token played by user %s on task %s.",
                        self.current_user.user.username, task.name)

            # Add "All ok" notification.
            self.notify_success(N_("Token request received"),
                                N_("Your request has been received "
                                   "and applied to the submission."))

        self.redirect(self.contest_url("tasks", task.name, "submissions"))
