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
    get_or_create_model_solution_participation, create_model_solution, \
    validate_model_solution_name
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

            # Validate name using centralized validation
            name = attrs.get("name", "").strip()
            validate_model_solution_name(name)

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
                _received_files, files, language = _extract_and_match_files(
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

            submission, _meta = create_model_solution(
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
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")

            # Validate name using centralized validation
            name = attrs.get("name", "").strip()
            validate_model_solution_name(name)

            # Check for duplicate name in the same dataset (if name changed)
            if name != meta.name:
                existing = self.sql_session.query(ModelSolutionMeta).filter(
                    ModelSolutionMeta.dataset_id == meta.dataset_id,
                    ModelSolutionMeta.name == name,
                    ModelSolutionMeta.id != meta.id
                ).first()
                if existing:
                    raise ValueError(
                        f"A model solution with name '{name}' already exists")
            
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

            meta.name = name
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
        task_name = task.name

        submission = meta.submission

        # Delete submission - ModelSolutionMeta will be cascade-deleted
        self.sql_session.delete(submission)

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Model solution deleted",
                "Model solution deleted from task %s" % task_name)

        self.write("./%d" % task_id)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, meta_id):
        """Support DELETE method by delegating to POST."""
        return self.post(meta_id)


class ConfigureImportedModelSolutionsHandler(BaseHandler):
    """Handler for configuring model solutions after import.

    This handler allows bulk configuration of expected score ranges for
    model solutions that were imported without metadata. It operates on
    existing ModelSolutionMeta rows in the database.
    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, task_id):
        from cms.db import Task
        task = self.safe_get_item(Task, task_id)
        dataset = task.active_dataset
        self.contest = task.contest

        # Get model solution IDs from query string, or show all that need config
        ids_str = self.get_argument("ids", "")
        if ids_str:
            meta_ids = [int(x) for x in ids_str.split(",") if x.strip()]
            model_solutions = [
                meta for meta in dataset.model_solution_metas
                if meta.id in meta_ids
            ]
        else:
            # Show all model solutions that might need configuration
            # (those with default values or missing subtask scores)
            model_solutions = list(dataset.model_solution_metas)

        # Convert to template-friendly format
        solutions_data = []
        for meta in model_solutions:
            # Determine if this solution needs configuration
            # (has default values or missing subtask scores)
            needs_config = (
                meta.expected_score_min == 0.0 and
                meta.expected_score_max == 100.0 and
                meta.subtask_expected_scores is None
            )
            solutions_data.append({
                "id": meta.id,
                "name": meta.name,
                "description": meta.description or "",
                "language": meta.submission.language if meta.submission else None,
                "expected_score_min": meta.expected_score_min,
                "expected_score_max": meta.expected_score_max,
                "subtask_expected_scores": meta.subtask_expected_scores,
                "needs_config": needs_config,
            })

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["model_solutions"] = solutions_data
        self.r_params["subtasks"] = get_subtask_info(dataset)
        self.render("configure_model_solutions.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        from cms.db import Task
        task = self.safe_get_item(Task, task_id)
        dataset = task.active_dataset
        fallback_page = self.url("task", task_id, "model_solutions", "configure")

        try:
            # Get all model solution metas for this dataset
            metas_by_id = {meta.id: meta for meta in dataset.model_solution_metas}
            subtasks = get_subtask_info(dataset)

            # Parse form data for each model solution
            updated_count = 0
            for meta_id, meta in metas_by_id.items():
                # Check if this meta has form data
                description = self.get_argument(f"sol_{meta_id}_description", None)
                if description is None:
                    continue  # No form data for this solution

                score_min_str = self.get_argument(
                    f"sol_{meta_id}_score_min", "0.0")
                score_max_str = self.get_argument(
                    f"sol_{meta_id}_score_max", "100.0")

                try:
                    score_min = float(score_min_str)
                    score_max = float(score_max_str)
                except ValueError:
                    raise ValueError(
                        f"Invalid score range for solution {meta.name}")

                if score_min > score_max:
                    raise ValueError(
                        f"Min score cannot be greater than max score "
                        f"for solution {meta.name}")

                # Parse subtask scores if present
                subtask_scores = None
                if subtasks:
                    subtask_scores = {}
                    for st in subtasks:
                        if st["max_score"] == 0:
                            continue
                        idx = st["idx"]
                        st_min = self.get_argument(
                            f"sol_{meta_id}_st_{idx}_min", None)
                        st_max = self.get_argument(
                            f"sol_{meta_id}_st_{idx}_max", None)
                        if st_min is not None and st_max is not None:
                            try:
                                st_min = float(st_min)
                                st_max = float(st_max)
                            except ValueError:
                                raise ValueError(
                                    f"Invalid score range for subtask {idx} "
                                    f"of solution {meta.name}")
                            if st_min > st_max:
                                raise ValueError(
                                    f"Min score cannot be greater than max "
                                    f"for subtask {idx} of solution {meta.name}")
                            subtask_scores[str(idx)] = {
                                "min": st_min,
                                "max": st_max
                            }

                # Update the meta
                meta.description = description
                meta.expected_score_min = score_min
                meta.expected_score_max = score_max
                meta.subtask_expected_scores = subtask_scores
                updated_count += 1

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
                "Model solutions configured",
                f"Updated {updated_count} model solution(s) for task {task.name}")
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)
