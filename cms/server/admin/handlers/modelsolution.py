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
from datetime import datetime

from cms.db import Dataset, ModelSolution, ModelSolutionFile, \
    ModelSolutionResult
from cms.grading.languagemanager import safe_get_lang_filename
from cmscommon.datetime import make_datetime
from .base import BaseHandler, FileHandler, require_permission


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
            
            expected_score_min = self.get_argument("expected_score_min", "0.0")
            expected_score_max = self.get_argument("expected_score_max", "100.0")
            
            try:
                attrs["expected_score_min"] = float(expected_score_min)
                attrs["expected_score_max"] = float(expected_score_max)
            except ValueError:
                raise ValueError("Invalid score range values")
            
            if attrs["expected_score_min"] > attrs["expected_score_max"]:
                raise ValueError("Minimum score cannot be greater than maximum score")
            
            attrs["timestamp"] = make_datetime()
            attrs["dataset"] = dataset

            model_solution = ModelSolution(**attrs)
            self.sql_session.add(model_solution)

            if self.request.files:
                for filename, file_list in self.request.files.items():
                    for uploaded_file in file_list:
                        digest = self.service.file_cacher.put_file_content(
                            uploaded_file["body"],
                            "Model solution file %s sent by %s at %s." % (
                                uploaded_file["filename"],
                                self.current_user.username,
                                make_datetime()))
                        
                        self.sql_session.add(ModelSolutionFile(
                            filename=uploaded_file["filename"],
                            digest=digest,
                            model_solution=model_solution))

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(self.url("task", task.id))
            return

        if self.try_commit():
            model_solution_result = model_solution.get_result_or_create(dataset)
            self.sql_session.commit()
            
            
            self.service.add_notification(
                make_datetime(),
                "Model solution added",
                "Model solution %s added to task %s" % (
                    model_solution.description, task.name))

        self.redirect(self.url("task", task.id))


class ModelSolutionHandler(BaseHandler):
    """Shows the details of a model solution.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, model_solution_id, dataset_id=None):
        model_solution = self.safe_get_item(ModelSolution, model_solution_id)
        dataset_obj = model_solution.dataset
        task = dataset_obj.task
        self.contest = task.contest

        if dataset_id is not None:
            dataset = self.safe_get_item(Dataset, dataset_id)
        else:
            dataset = dataset_obj
        assert dataset.task is task

        self.r_params = self.render_params()
        self.r_params["ms"] = model_solution
        self.r_params["active_dataset"] = task.active_dataset
        self.r_params["shown_dataset"] = dataset
        self.r_params["datasets"] = \
            self.sql_session.query(Dataset)\
                            .filter(Dataset.task == task)\
                            .order_by(Dataset.description).all()
        self.render("modelsolution.html", **self.r_params)


class ModelSolutionFileHandler(FileHandler):
    """Shows a model solution file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, file_id):
        ms_file = self.safe_get_item(ModelSolutionFile, file_id)
        model_solution = ms_file.model_solution

        real_filename = safe_get_lang_filename(
            model_solution.language, ms_file.filename)
        digest = ms_file.digest

        self.sql_session.close()
        self.fetch(digest, "text/plain", real_filename)


class DeleteModelSolutionHandler(BaseHandler):
    """Handler for deleting a model solution.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, model_solution_id):
        model_solution = self.safe_get_item(ModelSolution, model_solution_id)
        task = model_solution.dataset.task
        self.contest = task.contest

        description = model_solution.description
        
        self.sql_session.delete(model_solution)
        
        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Model solution deleted",
                "Model solution %s deleted from task %s" % (
                    description, task.name))

        self.redirect(self.url("task", task.id))
