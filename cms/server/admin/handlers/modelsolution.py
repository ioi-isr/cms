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

from cms.db import Dataset, Submission, File, ModelSolutionMeta, \
    get_or_create_model_solution_participation, create_model_solution
from cms.grading.scoretypes import ScoreTypeGroup
from cms.server.contest.submission import UnacceptableSubmission
from cms.server.contest.submission.workflow import _extract_and_match_files
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def get_subtask_info(dataset):
    """Get subtask information from a dataset if it uses a group-based score type.
    
    dataset: the dataset to get subtask info from
    
    return: a list of dicts with subtask info, or None if not a group-based score type.
            Each dict has: idx, name, max_score
    """
    try:
        score_type_obj = dataset.score_type_object
        if not isinstance(score_type_obj, ScoreTypeGroup):
            return None
        
        subtasks = []
        for idx, param in enumerate(score_type_obj.parameters):
            max_score = param[0]
            name = param[2] if len(param) >= 3 and param[2] else None
            subtasks.append({
                "idx": idx,
                "name": name,
                "display_name": name if name else f"Subtask {idx}",
                "max_score": max_score
            })
        return subtasks
    except Exception:
        return None


class AddModelSolutionHandler(BaseHandler):
    """Handler for adding a new model solution to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["subtasks"] = get_subtask_info(dataset)
        self.render("add_model_solution.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        try:
            attrs = {}
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")

            # Validate name is a valid identifier (alphanumeric + underscore)
            name = attrs.get("name", "").strip()
            if not name:
                raise ValueError("Name is required")
            # Only reject characters that are problematic for filenames
            invalid_chars = set('/\\*?<>|:"')
            if any(c in invalid_chars for c in name):
                raise ValueError(
                    "Name cannot contain: / \\ * ? < > | : \"")

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

            subtask_expected_scores = None
            subtasks = get_subtask_info(dataset)
            if subtasks:
                subtask_expected_scores = {}
                for st in subtasks:
                    if st["max_score"] == 0:
                        continue
                    idx = st["idx"]
                    st_min = self.get_argument(
                        f"subtask_{idx}_min", "0.0")
                    st_max = self.get_argument(
                        f"subtask_{idx}_max", str(st["max_score"]))
                    try:
                        st_min = float(st_min)
                        st_max = float(st_max)
                    except ValueError:
                        raise ValueError(
                            f"Invalid score range for subtask {idx}")
                    if st_min > st_max:
                        raise ValueError(
                            f"Min score cannot be greater than max score "
                            f"for subtask {idx}")
                    subtask_expected_scores[str(idx)] = {
                        "min": st_min,
                        "max": st_max
                    }

            # Use the shared submission file processing logic from accept_submission.
            # This handles archive extraction, file matching, language detection, etc.
            # Read language from form if provided (for tasks with language-dependent
            # submission formats). If not provided, auto-detect.
            language_name = self.get_argument("language", None)
            if language_name == "":
                language_name = None
            try:
                received_files, files, language = _extract_and_match_files(
                    self.request.files, task, language_name=language_name)
            except UnacceptableSubmission as err:
                raise ValueError(err.formatted_text)

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

            submission, meta = create_model_solution(
                self.sql_session,
                task=task,
                dataset=dataset,
                participation=participation,
                digests=digests,
                language_name=language.name if language is not None else None,
                name=name,
                description=attrs["description"],
                expected_score_min=expected_score_min,
                expected_score_max=expected_score_max,
                subtask_expected_scores=subtask_expected_scores,
            )

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


class EditModelSolutionHandler(BaseHandler):
    """Handler for editing a model solution's metadata.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, meta_id):
        meta = self.safe_get_item(ModelSolutionMeta, meta_id)
        task = meta.dataset.task
        dataset = meta.dataset
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["meta"] = meta
        self.r_params["subtasks"] = get_subtask_info(dataset)
        self.render("edit_model_solution.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, meta_id):
        fallback_page = self.url("model_solution", meta_id, "edit")
        
        meta = self.safe_get_item(ModelSolutionMeta, meta_id)
        task = meta.dataset.task
        dataset = meta.dataset

        try:
            attrs = {}
            self.get_string(attrs, "description")
            
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

            subtask_expected_scores = None
            subtasks = get_subtask_info(dataset)
            if subtasks:
                subtask_expected_scores = {}
                for st in subtasks:
                    if st["max_score"] == 0:
                        continue
                    idx = st["idx"]
                    st_min = self.get_argument(
                        f"subtask_{idx}_min", "0.0")
                    st_max = self.get_argument(
                        f"subtask_{idx}_max", str(st["max_score"]))
                    try:
                        st_min = float(st_min)
                        st_max = float(st_max)
                    except ValueError:
                        raise ValueError(
                            f"Invalid score range for subtask {idx}")
                    if st_min > st_max:
                        raise ValueError(
                            f"Min score cannot be greater than max score "
                            f"for subtask {idx}")
                    subtask_expected_scores[str(idx)] = {
                        "min": st_min,
                        "max": st_max
                    }

            meta.description = attrs["description"]
            meta.expected_score_min = expected_score_min
            meta.expected_score_max = expected_score_max
            meta.subtask_expected_scores = subtask_expected_scores

        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Invalid field(s)",
                repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Model solution updated",
                "Model solution updated for task %s" % task.name)
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class DeleteModelSolutionHandler(BaseHandler):
    """Handler for deleting a model solution.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, meta_id):
        meta = self.safe_get_item(ModelSolutionMeta, meta_id)
        task = meta.dataset.task
        task_id = task.id
        
        submission = meta.submission
        
        self.sql_session.delete(meta)
        
        if self.try_commit():
            self.sql_session.delete(submission)
            self.sql_session.commit()
            
            self.service.add_notification(
                make_datetime(),
                "Model solution deleted",
                "Model solution deleted from task %s" % task.name)
        
        self.write("./%d" % task_id)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, meta_id):
        """Support DELETE method by delegating to POST."""
        return self.post(meta_id)
