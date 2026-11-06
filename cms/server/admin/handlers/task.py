#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2025 Pasit Sangprachathanarak <ouipingpasit@gmail.com>
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

"""Task-related handlers for AWS.

"""

import json
import logging
import os.path
import traceback

import collections
try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web
from sqlalchemy.orm import contains_eager, selectinload

from cms.db import (
    Attachment, Dataset, Session, Statement, Submission, Task, TrainingDay,
    TrainingProgram,
)
from cms.grading.scoring import compute_changes_for_dataset
from cms.grading.scoretypes import ScoreTypeGroup
from cms.server.admin.handlers.utils import (
    deduplicate_preserving_order, parse_tags,
)
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission
from cms.grading.subtask_validation import get_running_validator_ids


logger = logging.getLogger(__name__)


def _parse_task_tags(value: str) -> list[str]:
    """Read the JSON editor value or legacy comma-separated form input."""
    try:
        tag_values = json.loads(value)
    except ValueError:
        tag_values = None
    if isinstance(tag_values, list):
        if not all(isinstance(tag, str) for tag in tag_values):
            raise ValueError("Tags must be a JSON array of strings.")
    else:
        tag_values = parse_tags(value)
    tags = deduplicate_preserving_order([
        tag.strip().lower() for tag in tag_values if tag.strip()
    ])
    return [tag for tag in tags if tag not in Task.AUTOMATIC_TAG_NAMES]


def _archived_training_days_query(sql_session, task_id: int | None = None):
    """Query archived training days, optionally only those containing a task."""
    query = sql_session.query(TrainingDay)\
        .join(TrainingDay.training_program)\
        .options(contains_eager(TrainingDay.training_program))\
        .filter(TrainingDay.contest_id.is_(None))
    if task_id is None:
        query = query.filter(TrainingDay.archived_tasks_data.isnot(None))
    else:
        query = query.filter(
            TrainingDay.archived_tasks_data.has_key(str(task_id)))
    return query.order_by(
        TrainingProgram.name, TrainingDay.start_time, TrainingDay.id)


def _training_day_label(training_day: TrainingDay) -> tuple[int, str, str]:
    return (training_day.id, training_day.training_program.name,
            training_day.description or training_day.name or "")


def get_task_training_days(
    sql_session, task_ids: list[int],
) -> dict[int, list[tuple[int, str, str]]]:
    """Find the archived training days that contained each task.

    sql_session (Session): the database session.
    task_ids ([int]): the ids of the tasks to look up.

    return ({int: [(int, str, str)]}): for each task id, the
        (training_day_id, program_name, day_name) of its archived days.

    """
    wanted = {str(task_id): task_id for task_id in task_ids}
    result: dict[int, list[tuple[int, str, str]]] = {
        task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    if len(task_ids) == 1:
        training_days = _archived_training_days_query(
            sql_session, task_ids[0]).all()
    else:
        training_days = _archived_training_days_query(sql_session).all()
    for training_day in training_days:
        if not isinstance(training_day.archived_tasks_data, dict):
            continue
        label = _training_day_label(training_day)
        for key in training_day.archived_tasks_data:
            if key in wanted:
                result[wanted[key]].append(label)
    return result


def get_task_training_day_scores(sql_session, task_id: int) -> list[dict]:
    """Build the histogram data of a task for its archived training days.

    Only students who participated in the training day are included in the
    scores, matching the training days page.

    sql_session (Session): the database session.
    task_id (int): the id of the task.

    return ([dict]): one entry per archived training day containing the task.

    """
    key = str(task_id)
    training_days = _archived_training_days_query(sql_session, task_id)\
        .options(selectinload(TrainingDay.archived_attendances),
                 selectinload(TrainingDay.archived_student_rankings))\
        .all()

    result = []
    for training_day in training_days:
        participated = {
            attendance.student_id
            for attendance in training_day.archived_attendances
            if attendance.status == "participated"
        }
        task_info = training_day.archived_tasks_data.get(key) or {}
        tags = set()
        student_tags = {}
        scores = []
        for ranking in training_day.archived_student_rankings:
            ranking_tags = ranking.student_tags or []
            tags.update(ranking_tags)
            student_tags[ranking.student_id] = ranking_tags
            task_scores = ranking.task_scores or {}
            if ranking.student_id in participated and key in task_scores:
                score = task_scores[key]
                scores.append({
                    "studentId": ranking.student_id,
                    "score": score if score is not None else 0,
                })
        training_day_id, program_name, day_name = \
            _training_day_label(training_day)
        result.append({
            "training_day_id": training_day_id,
            "program_name": program_name,
            "day_name": day_name,
            "max_score": task_info.get("max_score", 100),
            "tags": sorted(tags),
            "student_tags": student_tags,
            "scores": scores,
        })
    return result


class AddTaskHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        is_ajax = "application/json" in self.request.headers.get("Accept", "")

        try:
            attrs = dict()

            self.get_string(attrs, "name", empty=None)
            if not attrs.get("name"):
                raise ValueError("No task name specified.")
            attrs["title"] = attrs["name"]

            # Set default submission format as ["taskname.%l"]
            attrs["submission_format"] = ["%s.%%l" % attrs["name"]]

            # Create the task.
            task = Task(**attrs)
            self.sql_session.add(task)

        except Exception as error:
            if is_ajax:
                self.set_status(400)
                self.write({"error": str(error)})
                return
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(self.url("tasks"))
            return

        try:
            attrs = dict()

            # Create its first dataset.
            attrs["description"] = "Default"
            attrs["autojudge"] = True
            attrs["task_type"] = "Batch"
            attrs["task_type_parameters"] = ["alone", ["", ""], "diff"]
            attrs["score_type"] = "Sum"
            attrs["score_type_parameters"] = 100
            attrs["task"] = task
            dataset = Dataset(**attrs)
            self.sql_session.add(dataset)

            # Make the dataset active. Life works better that way.
            task.active_dataset = dataset

        except Exception as error:
            if is_ajax:
                self.set_status(400)
                self.write({"error": str(error)})
                return
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(self.url("tasks"))
            return

        if self.try_commit():
            # Create the task on RWS.
            self.service.proxy_service.reinitialize()
            if is_ajax:
                self.write({"ok": True, "id": task.id, "name": task.name})
                return
            self.redirect(self.url("task", task.id))
        else:
            if is_ajax:
                self.set_status(500)
                self.write(
                    {"error": "Failed to save task. The name may already exist."}
                )
                return
            self.redirect(self.url("tasks"))


class TaskHandler(BaseHandler):
    """Task handler, with a POST method to edit the task.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)
        # If the task is assigned to an active training day (not archived),
        # show the training day's contest sidebar instead of the training program sidebar
        if task.training_day is not None and task.training_day.contest is not None:
            self.contest = task.training_day.contest
        else:
            self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["all_task_tags"] = sorted({
            tag for t in self.r_params["task_list"] for tag in t.custom_tags
        })
        self.r_params["task_training_days"] = get_task_training_days(
            self.sql_session, [task.id])[task.id]
        self.r_params["primary_statements"] = task.primary_statements
        self.r_params["submissions"] = \
            self.sql_session.query(Submission)\
                .join(Task).filter(Task.id == task_id)\
                .order_by(Submission.timestamp.desc()).all()

        testcase_subtasks = {}
        subtask_names = {}
        subtask_info = {}
        for dataset in task.datasets:
            try:
                score_type_obj = dataset.score_type_object
                if isinstance(score_type_obj, ScoreTypeGroup):
                    # Extract subtask names and info from score type parameters first
                    # This should work even when there are no testcases
                    # Parameters format: [[score, pattern, optional_name], ...]
                    names = {}
                    subtasks = []
                    for idx, param in enumerate(score_type_obj.parameters):
                        max_score = param[0]
                        name = param[2] if len(param) >= 3 and param[2] else None
                        if name:
                            names[idx] = name
                        subtasks.append({
                            "idx": idx,
                            "name": name,
                            "display_name": name if name else f"Subtask {idx}",
                            "max_score": max_score
                        })
                    if names:
                        subtask_names[dataset.id] = names
                    if subtasks:
                        subtask_info[dataset.id] = subtasks

                    # Now try to get testcase-to-subtask mapping
                    # This may fail if there are no testcases, but subtask_info
                    # should still be populated from above
                    try:
                        from cms.server.util import build_tc_to_subtasks_mapping

                        tc_to_subtasks = build_tc_to_subtasks_mapping(score_type_obj)
                        testcase_subtasks[dataset.id] = tc_to_subtasks
                    except ValueError as e:
                        # If retrieve_target_testcases fails due to bad parameters/regexes,
                        # just skip the mapping but keep the subtask_info populated
                        logger.debug(
                            "Could not build testcase-to-subtask mapping for "
                            "dataset %d: %s", dataset.id, e)
            except Exception:
                pass

        self.r_params["testcase_subtasks"] = testcase_subtasks
        self.r_params["subtask_names"] = subtask_names
        self.r_params["subtask_info"] = subtask_info
        self.r_params["running_validator_ids"] = get_running_validator_ids()

        activate_data = {}
        for dataset in task.datasets:
            if dataset is task.active_dataset:
                continue
            if task.active_dataset is None:
                activate_data[dataset.id] = {
                    "changes": [],
                    "default_notify_participations": set(),
                }
                continue
            try:
                changes = compute_changes_for_dataset(task.active_dataset, dataset)
                notify_participations = set()
                for c in changes:
                    score_changed = c.old_score is not None or c.new_score is not None
                    public_score_changed = (
                        c.old_public_score is not None or c.new_public_score is not None
                    )
                    if public_score_changed or (
                        c.submission.tokened() and score_changed
                    ):
                        notify_participations.add(c.submission.participation.id)
                activate_data[dataset.id] = {
                    "changes": changes,
                    "default_notify_participations": notify_participations,
                }
            except Exception as error:
                logger.exception(
                    "Failed to compute activation changes for dataset %s",
                    dataset.id,
                    exc_info=error,
                )
                activate_data[dataset.id] = {
                    "changes": [],
                    "default_notify_participations": set(),
                }
        self.r_params["activate_data"] = activate_data
        self.render("task.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        task = self.safe_get_item(Task, task_id)

        try:
            attrs = task.get_attrs()

            self.get_string(attrs, "name", empty=None)
            self.get_string(attrs, "title")

            tags_str = self.get_argument("tags", None)
            if tags_str is not None:
                attrs["tags"] = _parse_task_tags(tags_str)

            assert attrs.get("name") is not None, "No task name specified."

            # Parsing of primary statements checkboxes. Their name is
            # primary_statement_XX, where XX is the language code.
            primary_statements = {}
            for statement in task.statements:
                self.get_bool(primary_statements,
                              "primary_statement_%s" % statement)
            attrs["primary_statements"] = list(sorted([
                k.replace("primary_statement_", "", 1)
                for k in primary_statements
                if primary_statements[k]
            ]))

            self.get_submission_format(attrs)
            self.get_string(attrs, "feedback_level")

            # Process allowed languages
            selected_languages = self.get_arguments("allowed_languages")
            if not selected_languages:
                # No languages selected means allow all contest languages (NULL)
                attrs["allowed_languages"] = None
            else:
                attrs["allowed_languages"] = selected_languages

            self.get_string(attrs, "token_mode")
            self.get_int(attrs, "token_max_number")
            self.get_timedelta_sec(attrs, "token_min_interval")
            self.get_int(attrs, "token_gen_initial")
            self.get_int(attrs, "token_gen_number")
            self.get_timedelta_min(attrs, "token_gen_interval")
            self.get_int(attrs, "token_gen_max")

            self.get_int(attrs, "max_submission_number")
            self.get_int(attrs, "max_user_test_number")
            self.get_timedelta_sec(attrs, "min_submission_interval")
            self.get_timedelta_sec(attrs, "min_user_test_interval")

            self.get_int(attrs, "score_precision")

            self.get_string(attrs, "score_mode")

            # Process visible_to_tags for training day tasks
            # Only update if the parameter is explicitly present in the request
            # (to avoid clobbering when editing from the general task page)
            visible_to_tags_str = self.get_argument("visible_to_tags", None)
            if visible_to_tags_str is not None:
                attrs["visible_to_tags"] = parse_tags(visible_to_tags_str)

            # Update the task.
            task.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(self.url("task", task_id))
            return

        for dataset in task.datasets:
            try:
                attrs = dataset.get_attrs()

                self.get_time_limit(attrs, "time_limit_%d" % dataset.id)
                self.get_memory_limit(attrs, "memory_limit_%d" % dataset.id)
                self.get_task_type(attrs, "task_type_%d" % dataset.id,
                                   "TaskTypeOptions_%d_" % dataset.id)
                self.get_score_type(attrs, "score_type_%d" % dataset.id,
                                    "score_type_parameters_%d" % dataset.id)

                # Update the dataset.
                dataset.set_attrs(attrs)

            except Exception as error:
                self.service.add_notification(
                    make_datetime(), "Invalid field(s)", repr(error))
                self.redirect(self.url("task", task_id))
                return

            for testcase in dataset.testcases.values():
                testcase.public = bool(self.get_argument(
                    "testcase_%s_public" % testcase.id, False))

            # Test that the score type parameters are valid.
            # Use no_autoflush to prevent premature flushing of
            # potentially invalid field values (e.g. invalid task name)
            # which would cause an IntegrityError before try_commit.
            try:
                with self.sql_session.no_autoflush:
                    _ = dataset.score_type_object
            except (AssertionError, ValueError) as error:
                self.application.service.add_notification(
                    make_datetime(), "Invalid score type parameters",
                    str(error))
                self.redirect(self.url("task", task_id))
                return

        if self.try_commit():
            # Update the task and score on RWS.
            self.service.proxy_service.dataset_updated(
                task_id=task.id)

            # Check if re-scoring was requested for changed score parameters
            rescore_datasets_str = self.get_argument("rescore_datasets", "")
            if rescore_datasets_str:
                # Parse dataset IDs and validate they belong to this task
                task_dataset_ids = {d.id for d in task.datasets}
                rescore_count = 0
                for dataset_id_str in rescore_datasets_str.split(","):
                    dataset_id_str = dataset_id_str.strip()
                    if not dataset_id_str:
                        continue
                    try:
                        dataset_id = int(dataset_id_str)
                    except ValueError:
                        continue
                    # Only re-score datasets that belong to this task
                    if dataset_id in task_dataset_ids:
                        # Invalidate all submissions (including model solutions)
                        self.service.scoring_service.invalidate_submission(
                            dataset_id=dataset_id)
                        rescore_count += 1

                if rescore_count > 0:
                    self.service.add_notification(
                        make_datetime(),
                        "Re-scoring triggered",
                        "Re-scoring has been triggered for %d dataset(s)." %
                        rescore_count)

        self.redirect(self.url("task", task_id))


class TaskTagsHandler(BaseHandler):
    """Update task labels without modifying the rest of the task form."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        task = self.safe_get_item(Task, task_id)
        try:
            tags = _parse_task_tags(self.get_argument("tags"))
        except ValueError as error:
            self.set_status(400)
            self.write({"error": str(error)})
            return

        task.tags = tags
        if self.try_commit():
            self.write({"success": True, "tags": task.tags})
        else:
            self.set_status(500)


class TaskTrainingDaysHandler(BaseHandler):
    """Return score data of the archived training days containing a task."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)
        result = get_task_training_day_scores(self.sql_session, task.id)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))


class StatementUploadMixin:
    """Shared validation and storage of uploaded statement files."""

    def _validate_statement_files(self, fallback_page: str) -> bool:
        """Check the uploaded statement (and optional source) files.

        On failure, a notification is added and the client is redirected;
        the caller must return immediately when False is returned.
        """
        if "statement" not in self.request.files or len(self.request.files["statement"]) == 0:
            self.service.add_notification(
                make_datetime(),
                "No statement file provided",
                "A PDF statement file is required.")
            self.redirect(fallback_page)
            return False
        statement = self.request.files["statement"][0]

        if len(statement["body"]) == 0:
            self.service.add_notification(
                make_datetime(),
                "Empty file",
                "The selected file is empty. Please select a non-empty PDF file.")
            self.redirect(fallback_page)
            return False
        if not statement["filename"].lower().endswith(".pdf"):
            self.service.add_notification(
                make_datetime(),
                "Invalid task statement",
                "The task statement must be a .pdf file.")
            self.redirect(fallback_page)
            return False
        return True

    def _store_statement_files(
        self, task_name: str, language: str, fallback_page: str
    ) -> tuple[str, str | None, str | None] | None:
        """Store the uploaded files in the file cacher.

        Must be called after _validate_statement_files succeeded and with
        the SQL session closed. Returns (digest, source_digest,
        source_extension), or None if storage failed (the client has
        already been redirected in that case).
        """
        statement = self.request.files["statement"][0]

        source_file = None
        source_digest = None
        source_extension = None
        if "source" in self.request.files and len(self.request.files["source"]) > 0:
            source_file = self.request.files["source"][0]
            _, source_extension = os.path.splitext(source_file["filename"].lower())

        try:
            digest = self.service.file_cacher.put_file_content(
                statement["body"],
                "Statement for task %s (lang: %s)" % (task_name, language))
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Task statement storage failed",
                repr(error))
            self.redirect(fallback_page)
            return None

        if source_file is not None:
            try:
                source_digest = self.service.file_cacher.put_file_content(
                    source_file["body"],
                    "Statement source for task %s (lang: %s)" % (task_name, language))
            except Exception as error:
                self.service.add_notification(
                    make_datetime(),
                    "Source file storage failed",
                    repr(error))
                self.redirect(fallback_page)
                return None

        return digest, source_digest, source_extension


class AddStatementHandler(StatementUploadMixin, BaseHandler):
    """Add a statement to a task."""
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        fallback_page = self.url("task", task_id)

        task = self.safe_get_item(Task, task_id)

        language: str = self.get_argument("language", "")
        if len(language) == 0:
            self.service.add_notification(
                make_datetime(),
                "No language code specified",
                "The language code can be any string.")
            self.redirect(fallback_page)
            return
        if language in task.statements:
            self.service.add_notification(
                make_datetime(),
                "Statement already exists",
                "A statement for language '%s' already exists. "
                "Use 'Replace' to upload a new version." % language)
            self.redirect(fallback_page)
            return
        if not self._validate_statement_files(fallback_page):
            return

        task_name = task.name
        self.sql_session.close()

        stored = self._store_statement_files(task_name, language, fallback_page)
        if stored is None:
            return
        digest, source_digest, source_extension = stored

        self.sql_session = Session()
        task = self.safe_get_item(Task, task_id)
        self.contest = task.contest

        statement = Statement(language, digest, task=task, source_digest=source_digest,
                               source_extension=source_extension)
        self.sql_session.add(statement)

        if self.try_commit():
            self.redirect(self.url("task", task_id))
        else:
            self.redirect(fallback_page)


class ReplaceStatementHandler(StatementUploadMixin, BaseHandler):
    """Replace the files of an existing statement, keeping its language."""
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id, statement_id):
        fallback_page = self.url("task", task_id)

        statement = self.safe_get_item(Statement, statement_id)
        task = self.safe_get_item(Task, task_id)
        if task is not statement.task:
            raise tornado.web.HTTPError(404)

        if not self._validate_statement_files(fallback_page):
            return

        task_name = task.name
        language = statement.language
        self.sql_session.close()

        stored = self._store_statement_files(task_name, language, fallback_page)
        if stored is None:
            return
        digest, source_digest, source_extension = stored

        self.sql_session = Session()
        statement = self.safe_get_item(Statement, statement_id)
        self.contest = statement.task.contest

        statement.digest = digest
        statement.source_digest = source_digest
        statement.source_extension = source_extension

        if self.try_commit():
            self.redirect(self.url("task", task_id))
        else:
            self.redirect(fallback_page)


class StatementHandler(BaseHandler):
    """Delete a statement.

    """
    # No page for single statements.

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, task_id, statement_id):
        statement = self.safe_get_item(Statement, statement_id)
        task = self.safe_get_item(Task, task_id)

        # Protect against URLs providing incompatible parameters.
        if task is not statement.task:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(statement)
        if not self.try_commit():
            self.set_status(500)
            return

        # Page to redirect to.
        self.write("%s" % task.id)


class AddAttachmentHandler(BaseHandler):
    """Add an attachment to a task."""
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        fallback_page = self.url("task", task_id)

        task = self.safe_get_item(Task, task_id)

        # Check if any files were uploaded
        if "attachment" not in self.request.files:
            self.service.add_notification(
                make_datetime(),
                "No file selected",
                "Please select at least one file to upload.")
            self.redirect(fallback_page)
            return

        attachments = self.request.files["attachment"]

        # Filter out empty files
        non_empty_attachments = [a for a in attachments if len(a["body"]) > 0]
        if not non_empty_attachments:
            self.service.add_notification(
                make_datetime(),
                "Empty file(s)",
                "The selected file(s) are empty. Please select non-empty files.")
            self.redirect(fallback_page)
            return

        attachments = non_empty_attachments

        # Check for conflicts with existing attachments before storing files
        filenames_in_batch = [a["filename"] for a in attachments]
        existing_filenames = set(task.attachments.keys())
        conflicts = [f for f in filenames_in_batch if f in existing_filenames]
        if conflicts:
            self.service.add_notification(
                make_datetime(),
                "Attachment filename conflict",
                "The following files already exist: %s" % ", ".join(conflicts))
            self.redirect(fallback_page)
            return

        task_name = task.name
        self.sql_session.close()

        # Store all attachments in file cacher
        stored_attachments = []
        for attachment in attachments:
            try:
                digest = self.service.file_cacher.put_file_content(
                    attachment["body"],
                    "Task attachment for %s" % task_name)
                stored_attachments.append((attachment["filename"], digest))
            except Exception as error:
                self.service.add_notification(
                    make_datetime(),
                    "Attachment storage failed",
                    repr(error))
                self.redirect(fallback_page)
                return

        self.sql_session = Session()
        task = self.safe_get_item(Task, task_id)

        for filename, digest in stored_attachments:
            attachment = Attachment(filename, digest, task=task)
            self.sql_session.add(attachment)

        if self.try_commit():
            self.redirect(self.url("task", task_id))
        else:
            self.redirect(fallback_page)


class AttachmentHandler(BaseHandler):
    """Delete an attachment.

    """
    # No page for single attachments.

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, task_id, attachment_id):
        attachment = self.safe_get_item(Attachment, attachment_id)
        task = self.safe_get_item(Task, task_id)

        # Protect against URLs providing incompatible parameters.
        if attachment.task is not task:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(attachment)
        if not self.try_commit():
            self.set_status(500)
            return

        # Page to redirect to.
        self.write("%s" % task.id)


class AddDatasetHandler(BaseHandler):
    """Add a new, clean dataset to a task.

    It's equivalent to the old behavior when the dataset_id_to_copy
    given was equal to the string "-".

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        fallback_page = self.url("task", task_id)

        task = self.safe_get_item(Task, task_id)

        try:
            attrs = dict()

            self.get_string(attrs, "description")

            # Ensure description is unique.
            if any(attrs["description"] == d.description
                   for d in task.datasets):
                self.service.add_notification(
                    make_datetime(),
                    "Dataset name %r is already taken." % attrs["description"],
                    "Please choose a unique name for this dataset.")
                self.redirect(fallback_page)
                return

            self.get_time_limit(attrs, "time_limit")
            self.get_memory_limit(attrs, "memory_limit")
            self.get_task_type(attrs, "task_type", "TaskTypeOptions_new_")
            self.get_score_type(attrs, "score_type", "score_type_parameters")

            # Create the dataset.
            attrs["autojudge"] = False
            attrs["task"] = task
            dataset = Dataset(**attrs)
            self.sql_session.add(dataset)

        except Exception as error:
            logger.warning("Invalid field: %s" % (traceback.format_exc()))
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        # If the task does not yet have an active dataset, make this
        # one active.
        if task.active_dataset is None:
            task.active_dataset = dataset

        if self.try_commit():
            self.redirect(self.url("task", task_id))
        else:
            self.redirect(fallback_page)


class TaskListHandler(BaseHandler):
    """Get returns the list of all tasks."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        self.r_params = self.render_params()
        self.r_params["task_tags"] = {
            task.id: task.all_tags for task in self.r_params["task_list"]
        }
        self.r_params["task_tag_counts"] = collections.Counter(
            tag for tags in self.r_params["task_tags"].values() for tag in tags
        )
        self.r_params["all_task_tags"] = sorted(self.r_params["task_tag_counts"])
        self.r_params["task_training_days"] = get_task_training_days(
            self.sql_session, [task.id for task in self.r_params["task_list"]])
        self.r_params["task_programs"] = {
            task_id: sorted({program for _, program, _ in training_days})
            for task_id, training_days
            in self.r_params["task_training_days"].items()
        }
        self.r_params["task_program_counts"] = collections.Counter(
            program for programs in self.r_params["task_programs"].values()
            for program in programs
        )
        self.r_params["all_task_programs"] = sorted(
            self.r_params["task_program_counts"])
        self.render("tasks.html", **self.r_params)


class RemoveTaskHandler(BaseHandler):
    """Delete removes the task from CMS."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, task_id):
        task = self.safe_get_item(Task, task_id)
        contest_id = task.contest_id
        num = task.num

        self.sql_session.delete(task)
        self.sql_session.flush()
        # Keeping the tasks' nums to the range 0... n - 1.
        if contest_id is not None:
            following_tasks: list[Task] = (
                self.sql_session.query(Task)
                .filter(Task.contest_id == contest_id)
                .filter(Task.num > num)
                .order_by(Task.num)
                .all()
            )
            for task in following_tasks:
                task.num -= 1
                self.sql_session.flush()
        if not self.try_commit():
            self.set_status(500)
        else:
            self.service.proxy_service.reinitialize()
            self.write(self.url("tasks"))


class DefaultSubmissionFormatHandler(BaseHandler):
    """
    Intended to be called for output only tasks.
    Replaces the submission format for the given task with the default one, consisting of all the test cases codenames.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        task = self.safe_get_item(Task, task_id)

        if task.active_dataset is None:
            raise tornado.web.HTTPError(400, "Task has no active dataset")
        if task.active_dataset.task_type != "OutputOnly":
            raise tornado.web.HTTPError(
                400, f"Task type must be OutputOnly, got {task.active_dataset.task_type}")

        try:
            task.set_default_output_only_submission_format()
        except Exception as e:
            logger.error(
                "Couldn't create default submission format for task %s "
                "(dataset %s, type %s)",
                task.id,
                task.active_dataset.id,
                task.active_dataset.task_type,
                exc_info=True,
            )
            raise tornado.web.HTTPError(
                500, f"Couldn't create default submission format for task {task.id}"
            ) from e

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        # Page to redirect to.
        self.write("%s" % task.id)
