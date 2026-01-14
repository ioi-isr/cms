#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2024 IOI-ISR
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

"""Subtask validator handlers for AWS.

This module contains the HTTP handlers for subtask validator operations.
The execution logic is in cms/grading/subtask_validation.py.
"""

import json
import logging
import re

import tornado.web

from cms.db import Dataset, Session, SubtaskValidator
from cms.grading.tasktypes.util import compile_manager_bytes
from cms.grading.languagemanager import filename_to_language
from cms.grading.language import CompiledLanguage
from cms.grading.subtask_validation import (
    is_validator_running,
    cancel_validator,
    run_validator_in_background,
)
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class AddSubtaskValidatorHandler(BaseHandler):
    """Add or replace a subtask validator.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, subtask_index):
        fallback_page = self.url("task", "0")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        fallback_page = self.url("task", task.id)

        subtask_index = int(subtask_index)

        # Validate subtask_index is non-negative
        if subtask_index < 0:
            self.service.add_notification(
                make_datetime(),
                "Invalid subtask index",
                "Subtask index must be non-negative, got %d." % subtask_index,
            )
            self.redirect(fallback_page)
            return

        # For Group score types, validate subtask_index is within range
        from cms.grading.scoretypes import ScoreTypeGroup

        score_type_obj = dataset.score_type_object
        if isinstance(score_type_obj, ScoreTypeGroup):
            targets = score_type_obj.retrieve_target_testcases()
            if subtask_index >= len(targets):
                self.service.add_notification(
                    make_datetime(),
                    "Invalid subtask index",
                    "Subtask index %d is out of range. Task has %d subtasks (0-%d)."
                    % (subtask_index, len(targets), len(targets) - 1),
                )
                self.redirect(fallback_page)
                return

        validator_file = self.request.files.get("validator")
        if not validator_file:
            self.service.add_notification(
                make_datetime(),
                "No validator file provided",
                "Please upload a validator source file.")
            self.redirect(fallback_page)
            return

        validator_file = validator_file[0]
        filename = validator_file["filename"]
        body = validator_file["body"]

        # Get testcases and convert to dict format before closing session
        testcase_data = [
            {"id": tc.id, "input": tc.input, "output": tc.output}
            for tc in dataset.testcases.values()
        ]

        self.sql_session.close()

        language = filename_to_language(filename)

        if language is None or not isinstance(language, CompiledLanguage):
            self.service.add_notification(
                make_datetime(),
                "Invalid validator file",
                "Validator must be a compilable source file.")
            self.redirect(fallback_page)
            return

        def notify(title, text):
            self.service.add_notification(make_datetime(), title, text)

        success, compiled_bytes, _stats = compile_manager_bytes(
            self.service.file_cacher,
            filename,
            body,
            "validator",
            sandbox_name="admin_compile_validator",
            for_evaluation=True,
            notify=notify
        )

        if not success:
            self.redirect(fallback_page)
            return

        try:
            source_digest = self.service.file_cacher.put_file_content(
                body, "Subtask validator source for %s" % task.name)
            executable_digest = None
            if compiled_bytes is not None:
                executable_digest = self.service.file_cacher.put_file_content(
                    compiled_bytes, "Subtask validator executable for %s" % task.name)
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Validator storage failed",
                repr(error))
            self.redirect(fallback_page)
            return

        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        existing_validator = dataset.subtask_validators.get(subtask_index)
        if existing_validator is not None:
            # Cancel any running validation for this validator
            cancel_validator(existing_validator.id)

            existing_validator.filename = filename
            existing_validator.digest = source_digest
            existing_validator.executable_digest = executable_digest
            for result in existing_validator.validation_results:
                self.sql_session.delete(result)
            validator = existing_validator
        else:
            validator = SubtaskValidator(
                dataset=dataset,
                subtask_index=subtask_index,
                filename=filename,
                digest=source_digest,
                executable_digest=executable_digest
            )
            self.sql_session.add(validator)

        if not self.try_commit():
            self.redirect(fallback_page)
            return

        # Auto-run validation if compilation succeeded
        if executable_digest is None:
            self.service.add_notification(
                make_datetime(),
                "Validator uploaded",
                "Validator for subtask %d uploaded but compilation failed." % subtask_index)
            self.redirect(self.url("task", task.id))
            return

        # Run validation in background (will cancel any existing run)
        run_validator_in_background(
            self.service,
            self.service.file_cacher,
            validator.id,
            int(dataset_id),
            filename,
            executable_digest,
            subtask_index,
            testcase_data
        )

        self.service.add_notification(
            make_datetime(),
            "Validator uploaded",
            "Validator for subtask %d uploaded. Running validation on %d testcases "
            "in background." % (subtask_index, len(testcase_data)))
        self.redirect(self.url("task", task.id))


class DeleteSubtaskValidatorHandler(BaseHandler):
    """Delete a subtask validator.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, dataset_id, validator_id):
        validator = self.safe_get_item(SubtaskValidator, validator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if validator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        # Cancel any running validation for this validator
        cancel_validator(int(validator_id))

        task_id = dataset.task_id
        self.sql_session.delete(validator)

        if self.try_commit():
            self.write("./%d" % task_id)


class SubtaskDetailsHandler(BaseHandler):
    """Show testcase details for a subtask, with optional validation results.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, dataset_id, subtask_index):
        dataset = self.safe_get_item(Dataset, dataset_id)
        subtask_index = int(subtask_index)

        task = dataset.task
        self.contest = task.contest

        from cms.grading.scoretypes import ScoreTypeGroup

        validator = dataset.subtask_validators.get(subtask_index)

        subtask_testcases = []
        other_testcases = []
        subtask_name = None
        subtask_regex = None
        uses_regex = False
        suggested_prefix = None

        try:
            score_type_obj = dataset.score_type_object
            if isinstance(score_type_obj, ScoreTypeGroup):
                targets = score_type_obj.retrieve_target_testcases()
                if subtask_index < len(targets):
                    subtask_tc_codenames = set(targets[subtask_index])
                else:
                    subtask_tc_codenames = set()

                # Get subtask name and regex from score type parameters
                if subtask_index < len(score_type_obj.parameters):
                    param = score_type_obj.parameters[subtask_index]
                    if len(param) >= 2:
                        # Check if the second parameter is a regex (string) or count (int)
                        if isinstance(param[1], str):
                            subtask_regex = param[1]
                            uses_regex = True
                            # Extract suggested prefix from regex patterns like ".*prefix(?#CMS)"
                            # Match anywhere in alternation (followed by | or end of string)
                            match = re.search(
                                r"\.\*(?P<term>[^|)]*?)\(\?#CMS\)(?:\||$)",
                                subtask_regex,
                            )
                            if match:
                                # Unescape the term using JSON decoding (consistent with task page)
                                term = match.group('term')
                                try:
                                    suggested_prefix = json.loads('"%s"' % term)
                                except (json.JSONDecodeError, ValueError):
                                    # If JSON decoding fails, don't suggest a prefix
                                    pass
                    if len(param) >= 3 and param[2]:
                        subtask_name = param[2]
            else:
                subtask_tc_codenames = set()
        except (KeyError, IndexError, TypeError, ValueError, re.error):
            # KeyError: score type doesn't exist
            # IndexError: subtask_index out of range
            # TypeError/ValueError: malformed parameters
            # re.error: invalid regex pattern
            subtask_tc_codenames = set()

        results_by_testcase = {}
        if validator:
            results_by_testcase = {r.testcase_id: r for r in validator.validation_results}

        for codename, testcase in sorted(dataset.testcases.items()):
            result = results_by_testcase.get(testcase.id)
            tc_info = {
                "codename": codename,
                "testcase": testcase,
                "result": result
            }
            if codename in subtask_tc_codenames:
                subtask_testcases.append(tc_info)
            else:
                other_testcases.append(tc_info)

        # Check if validator is currently running
        validator_running = False
        if validator:
            validator_running = is_validator_running(validator.id)

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["validator"] = validator
        self.r_params["validator_running"] = validator_running
        self.r_params["subtask_index"] = subtask_index
        self.r_params["subtask_name"] = subtask_name
        self.r_params["subtask_regex"] = subtask_regex
        self.r_params["uses_regex"] = uses_regex
        self.r_params["suggested_prefix"] = suggested_prefix
        self.r_params["subtask_testcases"] = subtask_testcases
        self.r_params["other_testcases"] = other_testcases
        self.render("subtask_details.html", **self.r_params)


class UpdateSubtaskRegexHandler(BaseHandler):
    """Update the regex pattern for a subtask in the score type parameters.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, subtask_index):
        dataset = self.safe_get_item(Dataset, dataset_id)
        subtask_index = int(subtask_index)

        fallback_page = self.url("dataset", dataset_id, "subtask", subtask_index, "details")

        # Parse the regex from JSON (consistent with task page approach)
        raw_regex = self.get_argument("regex", "")
        try:
            new_regex = json.loads(raw_regex)
        except (json.JSONDecodeError, ValueError):
            self.service.add_notification(
                make_datetime(),
                "Invalid regex",
                "Regex pattern is not valid JSON.")
            self.redirect(fallback_page)
            return

        if not new_regex:
            self.service.add_notification(
                make_datetime(),
                "Invalid regex",
                "Regex pattern cannot be empty.")
            self.redirect(fallback_page)
            return

        # Validate the regex
        try:
            re.compile(new_regex)
        except re.error as e:
            self.service.add_notification(
                make_datetime(),
                "Invalid regex",
                "The regex pattern is invalid: %s" % str(e))
            self.redirect(fallback_page)
            return

        # Update the score type parameters
        params = dataset.score_type_parameters
        if not isinstance(params, list) or subtask_index >= len(params):
            self.service.add_notification(
                make_datetime(),
                "Invalid subtask",
                "Subtask index %d is out of range." % subtask_index)
            self.redirect(fallback_page)
            return

        # Check that the current parameter uses regex (string), not count (int)
        if len(params[subtask_index]) < 2 or not isinstance(params[subtask_index][1], str):
            self.service.add_notification(
                make_datetime(),
                "Cannot update regex",
                "This subtask uses testcase count, not regex pattern.")
            self.redirect(fallback_page)
            return

        # Create a new list to trigger SQLAlchemy change detection
        new_params = [list(p) for p in params]
        new_params[subtask_index][1] = new_regex
        dataset.score_type_parameters = new_params

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Regex updated",
                "Subtask %d regex updated to: %s" % (subtask_index, new_regex))

            # Check if re-scoring was requested
            rescore = self.get_argument("rescore", "")
            if rescore:
                # Invalidate all submissions (including model solutions)
                self.service.scoring_service.invalidate_submission(
                    dataset_id=dataset.id
                )
                self.service.add_notification(
                    make_datetime(),
                    "Re-scoring triggered",
                    "Re-scoring has been triggered for all submissions.",
                )

        self.redirect(fallback_page)


class UpdateSubtaskNameHandler(BaseHandler):
    """Update the name for a subtask in the score type parameters."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, subtask_index):
        dataset = self.safe_get_item(Dataset, dataset_id)
        subtask_index = int(subtask_index)

        fallback_page = self.url("dataset", dataset_id, "subtask", subtask_index, "details")

        new_name = self.get_argument("name", "").strip()
        if not new_name:
            self.service.add_notification(
                make_datetime(),
                "Invalid name",
                "Subtask name cannot be empty.")
            self.redirect(fallback_page)
            return

        params = dataset.score_type_parameters
        if not isinstance(params, list) or subtask_index >= len(params):
            self.service.add_notification(
                make_datetime(),
                "Invalid subtask",
                "Subtask index %d is out of range." % subtask_index)
            self.redirect(fallback_page)
            return

        # Create a new list to trigger SQLAlchemy change detection and ensure space for the name
        new_params = [list(p) for p in params]
        while len(new_params[subtask_index]) < 3:
            new_params[subtask_index].append(None)
        new_params[subtask_index][2] = new_name
        dataset.score_type_parameters = new_params

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Subtask name updated",
                "Subtask %d name set to: %s" % (subtask_index, new_name))
        self.redirect(fallback_page)


class RerunSubtaskValidatorsHandler(BaseHandler):
    """Rerun all subtask validators for a dataset.

    This handler runs each validator individually using the same logic as
    AddSubtaskValidatorHandler. Each validator is tracked separately and
    can be cancelled independently.
    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset_id = int(dataset_id)
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        fallback_page = self.url("task", task.id)

        validators = list(dataset.subtask_validators.values())

        if not validators:
            self.service.add_notification(
                make_datetime(),
                "No validators",
                "No subtask validators found for this dataset.")
            self.redirect(fallback_page)
            return

        # Collect testcase data once (shared by all validators)
        testcase_data = [{"id": tc.id, "input": tc.input, "output": tc.output}
                         for tc in dataset.testcases.values()]

        # Run each validator that has an executable
        validators_started = 0
        for v in validators:
            if v.executable_digest is not None:
                # Run validation in background (will cancel any existing run)
                run_validator_in_background(
                    self.service,
                    self.service.file_cacher,
                    v.id,
                    dataset_id,
                    v.filename,
                    v.executable_digest,
                    v.subtask_index,
                    testcase_data,
                    send_notification=True
                )
                validators_started += 1

        if validators_started == 0:
            self.service.add_notification(
                make_datetime(),
                "No validators to run",
                "No validators have been compiled successfully.")
            self.redirect(fallback_page)
            return

        self.service.add_notification(
            make_datetime(),
            "Validation started",
            "Running %d validators on %d testcases in background." % (
                validators_started, len(testcase_data)))
        self.redirect(fallback_page)
