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

"""Subtask validator handlers and execution logic for AWS.

This module contains all the logic for running subtask validators,
including background execution with proper cancellation support.
"""

import logging
import re

import gevent
from gevent.lock import RLock

import tornado.web

from cms.db import Dataset, Session, SubtaskValidationResult, SubtaskValidator
from cms.grading.tasktypes.util import compile_manager_bytes, create_sandbox
from cms.grading.languagemanager import filename_to_language
from cms.grading.language import CompiledLanguage
from cms.grading.steps import safe_get_str
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission
from .dataset import set_sandbox_resource_limits


logger = logging.getLogger(__name__)


# Track running validation jobs by validator_id
# Each entry contains:
#   "status": "running"|"completed"|"error"|"cancelled"
#   "progress": str - current progress message
#   "result": str - final result message
#   "greenlet": gevent.Greenlet - the greenlet object for cancellation
_running_validations: dict[int, dict] = {}
_running_validations_lock = RLock()


def get_running_validator_ids() -> set[int]:
    """Return set of validator IDs that are currently running.

    This is used by the UI to show "Validating..." status.
    """
    with _running_validations_lock:
        return {
            vid for vid, info in _running_validations.items()
            if info.get("status") == "running"
        }


def is_validator_running(validator_id: int) -> bool:
    """Check if a specific validator is currently running."""
    with _running_validations_lock:
        return (validator_id in _running_validations and
                _running_validations[validator_id].get("status") == "running")


def cancel_validator(validator_id: int) -> bool:
    """Cancel a running validator by marking it as cancelled.

    This function does NOT wait for the greenlet to finish. Instead, it:
    1. Marks the validator as cancelled
    2. Sets greenlet to None so a new greenlet can take over
    3. Calls kill() to schedule GreenletExit (will be raised at next yield)

    The old greenlet will check _check_if_cancelled() and stop processing.
    It will also be unable to update status since its greenlet doesn't match.

    Returns True if the validator was running and was cancelled,
    False if it wasn't running.
    """
    with _running_validations_lock:
        if validator_id not in _running_validations:
            return False

        info = _running_validations[validator_id]
        if info.get("status") != "running":
            return False

        greenlet = info.get("greenlet")
        if greenlet is not None and not greenlet.dead:
            info["status"] = "cancelled"
            info["progress"] = "Cancelled"
            info["greenlet"] = None

            # Schedule GreenletExit to be raised in the old greenlet
            # This is non-blocking - the exception will be raised at the next yield
            greenlet.kill(block=False)
            return True

    return False


def _create_sandbox_with_retry(
    file_cacher, name="admin_validate", max_retries=3, retry_delay=0.5
):
    """Create a sandbox with retry logic to handle isolate state issues.

    When a greenlet is cancelled mid-execution, the isolate sandbox may be
    left in a bad state. This function retries sandbox creation with delays
    to allow isolate to recover.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            return create_sandbox(file_cacher, name=name)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                logger.debug(
                    "Sandbox creation failed (attempt %d/%d): %s, retrying...",
                    attempt + 1, max_retries, e)
                gevent.sleep(retry_delay)
            else:
                logger.warning(
                    "Sandbox creation failed after %d attempts: %s",
                    max_retries, e)
    raise last_error


def _run_validator(file_cacher, filename, executable_digest, testcase_data):
    """Run a validator against testcases and return validation results.

    This is the shared core logic for running validators in background.
    All validation runs through run_validator_in_background which spawns
    a greenlet to execute this function.

    Args:
        file_cacher: FileCacher instance for accessing stored files
        filename: Validator source filename (used to determine language)
        executable_digest: Digest of the compiled validator executable
        testcase_data: List of dicts with keys: id, input, output

    Returns:
        List of dicts with keys: testcase_id, passed, exit_status, exit_code, stderr
        - passed: True if validator ran successfully and returned exit code 0
        - exit_status: Sandbox exit status ('ok', 'timeout', 'signal', etc.)
        - exit_code: Exit code from validator (when exit_status is 'ok' or 'nonzero return')
        Raises Exception on error (caller handles notification/logging)
    """
    language = filename_to_language(filename)

    exe_name = "validator"
    if language is not None and isinstance(language, CompiledLanguage):
        exe_name += language.executable_extension

    # Build command once outside the loop - same for all testcases
    cmd = ["./" + exe_name, "input.txt", "output.txt"]
    if language is not None:
        try:
            cmds = language.get_evaluation_commands(exe_name)
            if cmds:
                cmd = cmds[0] + ["input.txt", "output.txt"]
        except Exception as e:
            logger.debug(
                "get_evaluation_commands failed for validator %s: %s, using default",
                filename, e)

    validation_results = []
    sandbox = None

    try:
        for tc_data in testcase_data:
            # Check for cancellation between testcases
            gevent.sleep(0)

            sandbox = _create_sandbox_with_retry(file_cacher)

            sandbox.create_file_from_storage(exe_name, executable_digest, executable=True)
            sandbox.create_file_from_storage("input.txt", tc_data["input"])
            sandbox.create_file_from_storage("output.txt", tc_data["output"])

            # Apply resource limits to prevent runaway validators
            set_sandbox_resource_limits(sandbox)

            # Provide input via stdin so validators can read from either
            # input.txt file or stdin (output is still read from output.txt)
            sandbox.stdin_file = "input.txt"
            sandbox.stdout_file = "stdout.txt"
            sandbox.stderr_file = "stderr.txt"

            box_success = sandbox.execute_without_std(cmd, wait=True)

            # Use safe_get_str for consistent stderr handling
            stderr = safe_get_str(sandbox, "stderr.txt")

            passed = False
            exit_status = None
            exit_code = None

            if box_success:
                exit_status = sandbox.get_exit_status()
                exit_code = sandbox.get_exit_code()
                # Passed only if validator ran to completion with exit code 0
                if exit_status == sandbox.EXIT_OK and exit_code == 0:
                    passed = True
                elif exit_status == sandbox.EXIT_NONZERO_RETURN:
                    # Validator ran to completion but returned non-zero (testcase failed)
                    passed = False
            else:
                # Sandbox itself failed - this is an error condition
                exit_status = "sandbox error"

            validation_results.append(
                {
                    "testcase_id": tc_data["id"],
                    "passed": passed,
                    "exit_status": exit_status,
                    "exit_code": exit_code,
                    "stderr": stderr or None,
                }
            )

            # Cleanup sandbox for this iteration
            try:
                sandbox.cleanup(delete=True)
            except Exception as cleanup_error:
                logger.debug("Sandbox cleanup error (non-fatal): %s", cleanup_error)
            sandbox = None

    except gevent.GreenletExit:
        # Re-raise; finally block handles cleanup
        raise

    finally:
        # Cleanup any remaining sandbox (e.g., if cancelled mid-loop)
        if sandbox:
            try:
                sandbox.cleanup(delete=True)
            except Exception:
                pass

    return validation_results


def _store_validation_results(sql_session, validator, dataset, validation_results):
    """Store validation results in the database, replacing any existing results.

    Args:
        sql_session: SQLAlchemy session
        validator: SubtaskValidator instance
        dataset: Dataset instance
        validation_results: List of dicts with keys: testcase_id, passed,
                           exit_status, exit_code, stderr

    Returns:
        Tuple of (passed_count, failed_count, error_count)
    """
    for result in validator.validation_results:
        sql_session.delete(result)
    sql_session.flush()

    testcase_ids = [r["testcase_id"] for r in validation_results]
    testcases_by_id = {tc.id: tc for tc in dataset.testcases.values()
                      if tc.id in testcase_ids}

    for result_data in validation_results:
        testcase = testcases_by_id.get(result_data["testcase_id"])
        if testcase is None:
            continue
        result = SubtaskValidationResult(
            validator=validator,
            testcase=testcase,
            passed=result_data["passed"],
            exit_status=result_data.get("exit_status"),
            exit_code=result_data.get("exit_code"),
            stderr=result_data["stderr"]
        )
        sql_session.add(result)

    passed_count = 0
    failed_count = 0
    error_count = 0
    for r in validation_results:
        exit_status = r.get("exit_status")
        if r["passed"]:
            passed_count += 1
        elif exit_status in ("ok", "nonzero return", None):
            # Validator ran to completion but returned non-zero
            failed_count += 1
        else:
            # Validator had an error (timeout, signal, sandbox error, etc.)
            error_count += 1
    return passed_count, failed_count, error_count


def _update_validation_status(validator_id, **kwargs):
    """Helper to update validation status with proper locking.

    Only updates if the current greenlet is the active one for this validator.
    This prevents a cancelled/replaced greenlet from overwriting the status
    of a newly started greenlet.
    """
    with _running_validations_lock:
        if validator_id not in _running_validations:
            return

        # Only allow the current greenlet to update the status.
        # If the greenlet in the dict is different, it means we have been
        # replaced/cancelled and a new job has started.
        current_record = _running_validations[validator_id]
        if current_record.get("greenlet") != gevent.getcurrent():
            return

        current_record.update(kwargs)


def _check_if_cancelled(validator_id):
    """Check if a validator has been cancelled. Returns True if cancelled.

    Also returns True if the current greenlet is not the active one for this
    validator (meaning we've been replaced by a new run).
    """
    with _running_validations_lock:
        if validator_id not in _running_validations:
            return True  # If record is gone, stop running

        current_record = _running_validations[validator_id]

        # If I am not the active greenlet, I am effectively cancelled
        if current_record.get("greenlet") != gevent.getcurrent():
            return True

        return current_record.get("status") == "cancelled"


def _run_single_validator_task(service, file_cacher, validator_id, dataset_id,
                               filename, executable_digest, subtask_index,
                               testcase_data, send_notification=True):
    """Run a single validator and store results.

    This function is the core validation task that runs in a greenlet.
    It handles validation, database storage, and error handling.

    Args:
        service: The service object for notifications
        file_cacher: FileCacher for accessing files
        validator_id: ID of the validator to run
        dataset_id: ID of the dataset
        filename: Validator filename
        executable_digest: Digest of the compiled validator
        subtask_index: Index of the subtask being validated
        testcase_data: List of testcase dicts with id, input, output
        send_notification: Whether to send notifications (default True)

    Returns:
        Tuple of (passed_count, failed_count, error_count, error_message)
        where error_message is None on success or a string on error.
    """
    try:
        # Check if cancelled before starting
        if _check_if_cancelled(validator_id):
            return (0, 0, 0, None)

        _update_validation_status(
            validator_id,
            progress="Running validator for subtask %d..." % subtask_index)

        validation_results = _run_validator(
            file_cacher, filename, executable_digest, testcase_data)

    except gevent.GreenletExit:
        # Greenlet was killed - this is expected for cancellation
        logger.info("Validator %d was cancelled", validator_id)
        _update_validation_status(validator_id, status="cancelled", progress="Cancelled")
        return (0, 0, 0, None)

    except Exception as error:
        logger.exception("Validation execution error for validator %d: %s",
                         validator_id, repr(error))
        _update_validation_status(
            validator_id, status="error", result=repr(error), progress="Error")
        if send_notification:
            service.add_notification(
                make_datetime(), "Validation error",
                "Validator for subtask %d failed: %s" % (subtask_index, repr(error)))
        return (0, 0, 0, "Validator %d: %s" % (subtask_index, repr(error)))

    # Check if cancelled before storing results
    if _check_if_cancelled(validator_id):
        return (0, 0, 0, None)

    sql_session = Session()
    try:
        validator = sql_session.query(SubtaskValidator).get(validator_id)
        if validator is None:
            _update_validation_status(
                validator_id, status="cancelled", progress="Validator deleted")
            return (0, 0, 0, None)

        dataset = sql_session.query(Dataset).get(dataset_id)
        if dataset is None:
            _update_validation_status(
                validator_id, status="error", progress="Dataset not found")
            return (0, 0, 0, "Validator %d: Dataset not found" % subtask_index)

        passed_count, failed_count, error_count = _store_validation_results(
            sql_session, validator, dataset, validation_results)

        sql_session.commit()

        msg = "Subtask %d: %d passed, %d failed" % (subtask_index, passed_count, failed_count)
        if error_count > 0:
            msg += ", %d errors" % error_count

        _update_validation_status(
            validator_id, status="completed", result=msg, progress="Completed")

        if send_notification:
            service.add_notification(make_datetime(), "Validation complete", msg)

        return (passed_count, failed_count, error_count, None)

    except gevent.GreenletExit:
        # Greenlet was killed during database operations
        logger.info("Validator %d was cancelled during DB operations", validator_id)
        sql_session.rollback()
        _update_validation_status(validator_id, status="cancelled", progress="Cancelled")
        return (0, 0, 0, None)

    except Exception as error:
        logger.exception("Database error for validator %d: %s",
                         validator_id, repr(error))
        _update_validation_status(
            validator_id, status="error", result=repr(error), progress="Database error")
        sql_session.rollback()
        if send_notification:
            service.add_notification(
                make_datetime(), "Validation error",
                "Database error for subtask %d: %s" % (subtask_index, repr(error)))
        return (0, 0, 0, "Validator %d DB error: %s" % (subtask_index, repr(error)))
    finally:
        sql_session.close()


def run_validator_in_background(service, file_cacher, validator_id, dataset_id,
                                filename, executable_digest, subtask_index,
                                testcase_data, send_notification=True):
    """Start a validator running in the background.

    If the validator is already running, it will be cancelled first and
    a new run will be started.

    Args:
        service: The service object for notifications
        file_cacher: FileCacher for accessing files
        validator_id: ID of the validator to run
        dataset_id: ID of the dataset
        filename: Validator filename
        executable_digest: Digest of the compiled validator
        subtask_index: Index of the subtask being validated
        testcase_data: List of testcase dicts with id, input, output
        send_notification: Whether to send notifications (default True)

    """
    # Cancel any existing run for this validator
    cancel_validator(validator_id)

    # Initialize the running status with lock and spawn greenlet atomically
    with _running_validations_lock:
        # Double-check that no other greenlet started while we were waiting
        if validator_id in _running_validations:
            existing = _running_validations[validator_id]
            if (
                existing.get("status") == "running"
                and existing.get("greenlet") is not None
            ):
                return

        greenlet = gevent.spawn(
            _run_single_validator_task,
            service,
            file_cacher,
            validator_id,
            dataset_id,
            filename,
            executable_digest,
            subtask_index,
            testcase_data,
            send_notification,
        )

        _running_validations[validator_id] = {
            "status": "running",
            "progress": "Starting validation...",
            "result": None,
            "greenlet": greenlet,
        }


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

        # Get testcases before closing session
        testcases = list(dataset.testcases.values())

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

        # Convert testcases to dict format for background validation
        testcase_data = [{"id": tc.id, "input": tc.input, "output": tc.output}
                         for tc in testcases]

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
                            # Extract suggested prefix from regex patterns like ".*prefix.*"
                            match = re.search(r'\.\*(?P<term>[^|)]*?)\.\*', subtask_regex)
                            if match:
                                # Unescape the term (in case it was escaped)
                                suggested_prefix = re.sub(r'\\(.)', r'\1', match.group('term'))
                    if len(param) >= 3 and param[2]:
                        subtask_name = param[2]
            else:
                subtask_tc_codenames = set()
        except Exception:
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

        new_regex = self.get_argument("regex", "")
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
