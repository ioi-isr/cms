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

"""Handlers for model solution management in AdminWebServer.

"""

import logging

from cms import config
from cms.db import Dataset, Submission, File, ModelSolutionMeta, \
    get_or_create_model_solution_participation
from cms.server.contest.submission.file_retrieval import \
    extract_files_from_tornado, InvalidArchive
from cms.server.contest.submission.file_matching import \
    match_files_and_language, InvalidFilesOrLanguage
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class AddModelSolutionHandler(BaseHandler):
    """Handler for adding a new model solution to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        try:
            attrs = {}
            self.get_string(attrs, "description")
            self.get_string(attrs, "language", empty=None)

            expected_score_min = self.get_argument(
                "expected_score_min", "0.0")
            expected_score_max = self.get_argument(
                "expected_score_max", "100.0")

            try:
                expected_score_min = float(expected_score_min)
                expected_score_max = float(expected_score_max)
            except ValueError:
                raise ValueError("Invalid score range values")

            if expected_score_min > expected_score_max:
                raise ValueError(
                    "Minimum score cannot be greater than maximum score")

            required_codenames = set(task.submission_format)
            task_type = dataset.task_type_object
            required_codenames.update(task_type.get_user_managers())

            archive_size_limit = config.contest_web_server.max_submission_length * len(
                required_codenames
            )
            archive_max_files = 2 * len(required_codenames)

            try:
                received_files = extract_files_from_tornado(
                    self.request.files, archive_size_limit, archive_max_files
                )
            except InvalidArchive:
                raise ValueError("Invalid archive format")

            try:
                files, language = match_files_and_language(
                    received_files,
                    attrs.get("language"),
                    required_codenames,
                    task.get_allowed_languages(),
                )
            except InvalidFilesOrLanguage as err:
                logger.info(f'Model solution rejected: {err}')
                raise ValueError("Invalid files or language")

            timestamp = make_datetime()
            digests = {}
            for codename, content in files.items():
                digest = self.service.file_cacher.put_file_content(
                    content,
                    "Model solution file %s sent by %s at %s." % (
                        codename,
                        self.current_user.username,
                        timestamp))
                digests[codename] = digest

            participation = get_or_create_model_solution_participation(
                self.sql_session)

            opaque_id = Submission.generate_opaque_id(
                self.sql_session, participation.id)
            submission = Submission(
                opaque_id=opaque_id,
                timestamp=timestamp,
                language=language.name if language is not None else None,
                participation=participation,
                task=task,
                official=False
            )
            self.sql_session.add(submission)
            self.sql_session.flush()

            for codename, digest in digests.items():
                self.sql_session.add(File(
                    filename=codename,
                    digest=digest,
                    submission=submission))

            meta = ModelSolutionMeta(
                submission=submission,
                dataset=dataset,
                description=attrs["description"],
                expected_score_min=expected_score_min,
                expected_score_max=expected_score_max
            )
            self.sql_session.add(meta)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(self.url("task", task.id))
            return

        if self.try_commit():
            submission.get_result_or_create(dataset)
            self.sql_session.commit()

            self.service.add_notification(
                make_datetime(),
                "Model solution added",
                "Model solution %s added to task %s" % (
                    attrs["description"], task.name))

            self.service.evaluation_service.new_submission(
                submission_id=submission.id)

        self.redirect(self.url("task", task.id))


class ModelSolutionHandler(BaseHandler):
    """Handler for viewing a model solution (redirects to submission view).

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, meta_id, dataset_id=None):
        meta = self.safe_get_item(ModelSolutionMeta, meta_id)
        
        if dataset_id is None:
            dataset_id = meta.dataset_id
        else:
            dataset_id = int(dataset_id)
        
        self.redirect(self.url("submission", meta.submission_id, dataset_id))


class DeleteModelSolutionHandler(BaseHandler):
    """Handler for deleting a model solution.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, meta_id):
        meta = self.safe_get_item(ModelSolutionMeta, meta_id)
        task = meta.dataset.task
        
        submission = meta.submission
        
        self.sql_session.delete(meta)
        
        if self.try_commit():
            self.sql_session.delete(submission)
            self.sql_session.commit()
            
            self.service.add_notification(
                make_datetime(),
                "Model solution deleted",
                "Model solution deleted from task %s" % task.name)
        
        self.redirect(self.url("task", task.id))

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, meta_id):
        """Support DELETE method by delegating to POST."""
        return self.post(meta_id)
