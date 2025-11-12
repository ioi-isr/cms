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

"""Model solution-related handlers for AWS.

"""

import logging

from cms.db import Dataset, Submission, File, ModelSolutionMeta, \
    get_or_create_model_solution_participation
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
        self.contest = task.contest

        try:
            attrs = {}
            self.get_string(attrs, "description")
            self.get_string(attrs, "language", empty=None)

            allowed_languages = task.get_allowed_languages() or []
            if allowed_languages and not attrs.get("language"):
                raise ValueError("Language is required")
            if allowed_languages and attrs.get("language") and \
                    attrs["language"] not in allowed_languages:
                raise ValueError("Invalid language selected")

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

            participation = get_or_create_model_solution_participation(
                self.sql_session, self.contest)

            timestamp = make_datetime()
            submission = Submission(
                timestamp=timestamp,
                language=attrs.get("language"),
                participation=participation,
                task=task,
                official=False
            )
            self.sql_session.add(submission)
            self.sql_session.flush()

            if self.request.files:
                for filename, file_list in self.request.files.items():
                    for uploaded_file in file_list:
                        digest = self.service.file_cacher.put_file_content(
                            uploaded_file["body"],
                            "Model solution file %s sent by %s at %s." % (
                                uploaded_file["filename"],
                                self.current_user.username,
                                timestamp))

                        self.sql_session.add(File(
                            filename=uploaded_file["filename"],
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

            self.service.evaluation_service.new_evaluation(
                submission_id=submission.id,
                dataset_id=dataset.id)

        self.redirect(self.url("task", task.id))


class ModelSolutionHandler(BaseHandler):
    """Shows the details of a model solution.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, model_solution_meta_id, dataset_id=None):
        meta = self.safe_get_item(ModelSolutionMeta, model_solution_meta_id)
        submission = meta.submission
        task = submission.task
        self.contest = task.contest

        if dataset_id is not None:
            dataset = self.safe_get_item(Dataset, dataset_id)
        else:
            dataset = meta.dataset
        assert dataset.task is task

        self.r_params = self.render_params()
        self.r_params["s"] = submission
        self.r_params["meta"] = meta
        self.r_params["active_dataset"] = task.active_dataset
        self.r_params["shown_dataset"] = dataset
        self.r_params["datasets"] = \
            self.sql_session.query(Dataset)\
                            .filter(Dataset.task == task)\
                            .order_by(Dataset.description).all()
        self.render("submission.html", **self.r_params)


class DeleteModelSolutionHandler(BaseHandler):
    """Handler for deleting a model solution.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, model_solution_meta_id):
        meta = self.safe_get_item(ModelSolutionMeta, model_solution_meta_id)
        submission = meta.submission
        task = submission.task
        self.contest = task.contest

        description = meta.description

        self.sql_session.delete(meta)
        self.sql_session.delete(submission)

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Model solution deleted",
                "Model solution %s deleted from task %s" % (
                    description, task.name))

        self.redirect(self.url("task", task.id))

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, model_solution_meta_id):
        """Support DELETE method by delegating to POST."""
        return self.post(model_solution_meta_id)
