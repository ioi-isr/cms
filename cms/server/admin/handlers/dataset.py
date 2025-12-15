#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 Peyman Jabbarzade Ganje <peyman.jabarzade@gmail.com>
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

"""Dataset-related handlers for AWS.

"""

import io
import os
import logging
import re
import zipfile

import collections

try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms.db import Dataset, Generator, Manager, Message, Participation, \
    Session, Submission, SubtaskValidationResult, SubtaskValidator, Task, \
    Testcase
from cms.grading.tasktypes import get_task_type_class
from cms.grading.tasktypes.util import create_sandbox, \
    get_allowed_manager_basenames, compile_manager_bytes
from cms.grading.languagemanager import filename_to_language
from cms.grading.language import CompiledLanguage
from cms.grading.scoring import compute_changes_for_dataset
from cmscommon.datetime import make_datetime
from cmscommon.importers import import_testcases_from_zipfile, compile_template_regex
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def validate_template(template: str, name: str) -> str | None:
    """Validate a filename template contains exactly one '*'.

    Return an error message if invalid, None if valid.
    """
    if template.count('*') != 1:
        return "%s template must contain exactly one '*'." % name.capitalize()
    return None


class DatasetSubmissionsHandler(BaseHandler):
    """Shows all submissions for this dataset, allowing the admin to
    view the results under different datasets.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        submission_query = self.sql_session.query(Submission)\
            .filter(Submission.task == task)
        page = int(self.get_query_argument("page", 0))
        self.render_params_for_submissions(submission_query, page)

        self.r_params["task"] = task
        self.r_params["active_dataset"] = task.active_dataset
        self.r_params["shown_dataset"] = dataset
        self.r_params["datasets"] = \
            self.sql_session.query(Dataset)\
                            .filter(Dataset.task == task)\
                            .order_by(Dataset.description).all()
        self.render("dataset.html", **self.r_params)


class CloneDatasetHandler(BaseHandler):
    """Clone a dataset by duplicating it (on the same task).

    It's equivalent to the old behavior of AddDatasetHandler when the
    dataset_id_to_copy given was the ID of an existing dataset.

    If referred by GET, this handler will return a HTML form.
    If referred by POST, this handler will create the dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id_to_copy):
        dataset = self.safe_get_item(Dataset, dataset_id_to_copy)
        task = self.safe_get_item(Task, dataset.task_id)
        self.contest = task.contest

        try:
            original_dataset = \
                self.safe_get_item(Dataset, dataset_id_to_copy)
            description = "Copy of %s" % original_dataset.description
        except ValueError:
            raise tornado.web.HTTPError(404)

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["clone_id"] = dataset_id_to_copy
        self.r_params["original_dataset"] = original_dataset
        self.r_params["original_dataset_task_type_parameters"] = \
            original_dataset.task_type_parameters
        self.r_params["default_description"] = description
        self.render("add_dataset.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id_to_copy):
        fallback_page = self.url("dataset", dataset_id_to_copy, "clone")

        dataset = self.safe_get_item(Dataset, dataset_id_to_copy)
        task = self.safe_get_item(Task, dataset.task_id)
        task_id = task.id

        try:
            original_dataset = \
                self.safe_get_item(Dataset, dataset_id_to_copy)
        except ValueError:
            raise tornado.web.HTTPError(404)

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
            self.get_task_type(attrs, "task_type", "TaskTypeOptions_")
            self.get_score_type(attrs, "score_type", "score_type_parameters")

            # Create the dataset.
            attrs["autojudge"] = False
            attrs["task"] = task
            dataset = Dataset(**attrs)
            self.sql_session.add(dataset)

        except Exception as error:
            logger.warning("Invalid field.", exc_info=True)
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if original_dataset is not None:
            # If we were cloning the dataset, copy all managers and
            # testcases across too. If the user insists, clone all
            # evaluation information too.
            clone_results = bool(self.get_argument("clone_results", False))
            dataset.clone_from(original_dataset, True, True, clone_results)

        # If the task does not yet have an active dataset, make this
        # one active.
        if task.active_dataset is None:
            task.active_dataset = dataset

        if self.try_commit():
            self.redirect(self.url("task", task_id))
        else:
            self.redirect(fallback_page)


class RenameDatasetHandler(BaseHandler):
    """Rename the descripton of a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("rename_dataset.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        fallback_page = self.url("dataset", dataset_id, "rename")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        description: str = self.get_argument("description", "")

        # Ensure description is unique.
        if any(description == d.description
               for d in task.datasets if d is not dataset):
            self.service.add_notification(
                make_datetime(),
                "Dataset name \"%s\" is already taken." % description,
                "Please choose a unique name for this dataset.")
            self.redirect(fallback_page)
            return

        dataset.description = description

        if self.try_commit():
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class DeleteDatasetHandler(BaseHandler):
    """Delete a dataset from a task.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("delete_dataset.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        self.sql_session.delete(dataset)

        if self.try_commit():
            # self.service.scoring_service.reinitialize()
            pass
        self.redirect(self.url("task", task.id))


class ActivateDatasetHandler(BaseHandler):
    """Set a given dataset to be the active one for a task.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        changes = compute_changes_for_dataset(task.active_dataset, dataset)
        notify_participations = set()

        # By default, we will notify users who's public scores have changed, or
        # their non-public scores have changed but they have used a token.
        for c in changes:
            score_changed = c.old_score is not None or c.new_score is not None
            public_score_changed = c.old_public_score is not None or \
                c.new_public_score is not None
            if public_score_changed or \
                    (c.submission.tokened() and score_changed):
                notify_participations.add(c.submission.participation.id)

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["changes"] = changes
        self.r_params["default_notify_participations"] = notify_participations
        self.render("activate_dataset.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        task.active_dataset = dataset

        if dataset.task_type == 'OutputOnly':
            try:
                task.set_default_output_only_submission_format()
            except Exception as e:
                raise RuntimeError(
                    f"Couldn't create default submission format for task {task.id}, "
                    f"dataset {dataset.id}") from e

        if self.try_commit():
            self.service.proxy_service.dataset_updated(
                task_id=task.id)

            # This kicks off judging of any submissions which were previously
            # unloved, but are now part of an autojudged taskset.
            self.service\
                .evaluation_service.search_operations_not_done()
            self.service\
                .scoring_service.search_operations_not_done()

        # Now send notifications to contestants.
        datetime = make_datetime()

        r = re.compile('notify_([0-9]+)$')
        count = 0
        for k in self.request.arguments:
            m = r.match(k)
            if not m:
                continue
            participation = self.safe_get_item(Participation, m.group(1))
            message = Message(datetime,
                              self.get_argument("message_subject", ""),
                              self.get_argument("message_text", ""),
                              participation=participation)
            self.sql_session.add(message)
            count += 1

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Messages sent to %d users." % count, "")

        self.redirect(self.url("task", task.id))


class ToggleAutojudgeDatasetHandler(BaseHandler):
    """Toggle whether a given dataset is judged automatically or not.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)

        dataset.autojudge = not dataset.autojudge

        if self.try_commit():
            # self.service.scoring_service.reinitialize()

            # This kicks off judging of any submissions which were previously
            # unloved, but are now part of an autojudged taskset.
            self.service\
                .evaluation_service.search_operations_not_done()
            self.service\
                .scoring_service.search_operations_not_done()

        self.write("./%d" % dataset.task_id)


class AddManagerHandler(BaseHandler):
    """Add a manager to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("add_manager.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        fallback_page = self.url("dataset", dataset_id, "managers", "add")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        managers = self.request.files["manager"]
        task_name = task.name

        # Decide which auto-compiled basenames are allowed for this task type.
        # Use TaskType constants to avoid hardcoding names and to avoid
        # compiling unintended files (e.g., manager.%l for TwoSteps).
        allowed_compile_basenames = get_allowed_manager_basenames(dataset.task_type)

        # Check all files for compiled file conflicts before processing
        for manager in managers:
            filename = manager["filename"]
            base_noext = os.path.splitext(os.path.basename(filename))[0]

            # compiled files (no extension) when a source file already exists.
            has_extension = "." in os.path.basename(filename)
            if (base_noext in allowed_compile_basenames and not has_extension):
                for existing_filename in dataset.managers.keys():
                    existing_base = os.path.splitext(os.path.basename(existing_filename))[0]
                    existing_has_ext = "." in os.path.basename(existing_filename)
                    if existing_base == base_noext and existing_has_ext:
                        self.service.add_notification(
                            make_datetime(),
                            "Cannot upload compiled manager",
                            ("A source file '%s' already exists for '%s'. "
                             "Compiled files are auto-generated from source. "
                             "Please upload the source file instead, or delete "
                             "the existing source first." %
                             (existing_filename, base_noext)))
                        self.redirect(fallback_page)
                        return

        self.sql_session.close()

        # Phase 1: Compile all files first, collecting results in memory.
        # This ensures no files are stored in file_cacher if any compilation fails.
        planned_entries: list[tuple[str, bytes]] = []  # (filename, content_bytes)
        for manager in managers:
            filename = manager["filename"]
            body = manager["body"]
            base_noext = os.path.splitext(os.path.basename(filename))[0]

            # Always plan to store the original upload.
            planned_entries.append((filename, body))

            # If a source file for a known compiled language is uploaded,
            # compile it into an executable manager.
            try:
                language = filename_to_language(filename)
            except Exception:
                language = None

            if (language is not None
                    and isinstance(language, CompiledLanguage)
                    and base_noext in allowed_compile_basenames):
                compiled_filename = base_noext
                
                def notify(title, text):
                    self.service.add_notification(make_datetime(), title, text)
                
                success, compiled_bytes, stats = compile_manager_bytes(
                    self.service.file_cacher,
                    filename,
                    body,
                    compiled_filename,
                    sandbox_name="admin_compile",
                    for_evaluation=True,
                    notify=notify
                )
                
                if not success:
                    self.redirect(fallback_page)
                    return

                # Plan to store the compiled executable.
                if compiled_bytes is not None:
                    planned_entries.append((compiled_filename, compiled_bytes))

        # Phase 2: All compilations succeeded, now store all files in file_cacher.
        all_stored_entries: list[tuple[str, str]] = []  # (filename, digest)
        for filename, content in planned_entries:
            try:
                digest = self.service.file_cacher.put_file_content(
                    content, "Task manager for %s" % task_name)
                all_stored_entries.append((filename, digest))
            except Exception as error:
                self.service.add_notification(
                    make_datetime(),
                    "Manager storage failed",
                    "Error storing '%s': %s" % (filename, repr(error)))
                self.redirect(fallback_page)
                return

        # Phase 3: Update database with all manager records.
        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        for fname, dig in all_stored_entries:
            existing_manager = dataset.managers.get(fname)
            if existing_manager is not None:
                existing_manager.digest = dig
            else:
                manager = Manager(fname, dig, dataset=dataset)
                self.sql_session.add(manager)

        if self.try_commit():
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class DeleteManagerHandler(BaseHandler):
    """Delete a manager.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, dataset_id, manager_id):
        manager = self.safe_get_item(Manager, manager_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        # Protect against URLs providing incompatible parameters.
        if manager.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task_id = dataset.task_id

        # If deleting a source manager for checker/manager, also delete the compiled counterpart.
        filename = manager.filename
        base_noext = os.path.splitext(os.path.basename(filename))[0]
        # Determine if this is a source file (has an extension) for special basenames.
        if base_noext in ("checker", "manager") and "." in filename:
            # compiled counterpart has exactly the basename with no extension
            counterpart_name = base_noext
            # Need to re-fetch dataset.managers in this session scope
            try:
                counterpart = dataset.managers.get(counterpart_name)
            except Exception:
                counterpart = None
            if counterpart is not None:
                self.sql_session.delete(counterpart)

        self.sql_session.delete(manager)

        self.try_commit()
        self.write("./%d" % task_id)


class AddTestcaseHandler(BaseHandler):
    """Add a testcase to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("add_testcase.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        fallback_page = self.url("dataset", dataset_id, "testcases", "add")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        codename = self.get_argument("codename")

        try:
            input_ = self.request.files["input"][0]
            output = self.request.files["output"][0]
        except KeyError:
            self.service.add_notification(
                make_datetime(),
                "Invalid data",
                "Please fill both input and output.")
            self.redirect(fallback_page)
            return

        public = self.get_argument("public", None) is not None
        task_name = task.name
        self.sql_session.close()

        try:
            input_digest = \
                self.service.file_cacher.put_file_content(
                    input_["body"],
                    "Testcase input for task %s" % task_name)
            output_digest = \
                self.service.file_cacher.put_file_content(
                    output["body"],
                    "Testcase output for task %s" % task_name)
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Testcase storage failed",
                repr(error))
            self.redirect(fallback_page)
            return

        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        testcase = Testcase(
            codename, public, input_digest, output_digest, dataset=dataset)
        self.sql_session.add(testcase)

        if dataset.active and dataset.task_type == "OutputOnly":
            try:
                task.set_default_output_only_submission_format()
            except Exception as e:
                raise RuntimeError(
                    f"Couldn't create default submission format for task {task.id}, "
                    f"dataset {dataset.id}") from e

        if self.try_commit():
            # max_score and/or extra_headers might have changed.
            self.service.proxy_service.reinitialize()
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class AddTestcasesHandler(BaseHandler):
    """Add several testcases to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("add_testcases.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        fallback_page = \
            self.url("dataset", dataset_id, "testcases", "add_multiple")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        try:
            archive = self.request.files["archive"][0]
        except KeyError:
            self.service.add_notification(
                make_datetime(),
                "Invalid data",
                "Please choose tests archive.")
            self.redirect(fallback_page)
            return

        public = self.get_argument("public", None) is not None
        overwrite = self.get_argument("overwrite", None) is not None

        # Get input/output file names templates, or use default ones.
        input_template: str = self.get_argument("input_template", "input.*")
        output_template: str = self.get_argument("output_template", "output.*")
        
        try:
            input_re = compile_template_regex(input_template)
            output_re = compile_template_regex(output_template)
        except ValueError as e:
            self.service.add_notification(
                make_datetime(), "Invalid template", str(e))
            self.redirect(fallback_page)
            return

        fp = io.BytesIO(archive["body"])
        try:
            successful_subject, successful_text = \
                import_testcases_from_zipfile(
                    self.sql_session,
                    self.service.file_cacher, dataset,
                    fp, input_re, output_re, overwrite, public)
        except Exception as error:
            self.service.add_notification(
                make_datetime(), str(error), repr(error))
            self.redirect(fallback_page)
            return

        self.service.add_notification(
            make_datetime(), successful_subject, successful_text)
        self.service.proxy_service.reinitialize()
        self.redirect(self.url("task", task.id))


class DeleteTestcaseHandler(BaseHandler):
    """Delete a testcase.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, dataset_id, testcase_id):
        testcase = self.safe_get_item(Testcase, testcase_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        # Protect against URLs providing incompatible parameters.
        if dataset is not testcase.dataset:
            raise tornado.web.HTTPError(404)

        task_id = testcase.dataset.task_id
        task = dataset.task

        self.sql_session.delete(testcase)

        if dataset.active and dataset.task_type == "OutputOnly":
            dataset.testcases.pop(testcase.codename, None)
            try:
                task.set_default_output_only_submission_format()
            except Exception as e:
                raise RuntimeError(
                    f"Couldn't create default submission format for task {task.id}, "
                    f"dataset {dataset.id}") from e

        if self.try_commit():
            # max_score and/or extra_headers might have changed.
            self.service.proxy_service.reinitialize()
        self.write("./%d" % task_id)


class DeleteSelectedTestcasesHandler(BaseHandler):
    """Delete multiple selected testcases from a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        task_id = task.id

        # Collect selected testcase IDs from the request.
        id_strings = self.get_arguments("testcase_id")

        # If nothing was selected, just redirect back without doing anything.
        if not id_strings:
            self.write("./%d" % task_id)
            return

        testcases = []
        for id_str in id_strings:
            try:
                tid = int(id_str)
            except ValueError:
                raise tornado.web.HTTPError(400)
            tc = self.safe_get_item(Testcase, tid)

            # Protect against mixing datasets.
            if tc.dataset is not dataset:
                raise tornado.web.HTTPError(400)

            testcases.append(tc)

        # Delete all selected testcases.
        for tc in testcases:
            self.sql_session.delete(tc)

        # Handle OutputOnly tasks
        if dataset.active and dataset.task_type == "OutputOnly":
            for tc in testcases:
                dataset.testcases.pop(tc.codename, None)
            try:
                task.set_default_output_only_submission_format()
            except Exception as e:
                raise RuntimeError(
                    f"Couldn't create default submission format for task {task.id}, "
                    f"dataset {dataset.id}") from e

        if self.try_commit():
            # max_score and/or extra_headers might have changed.
            self.service.proxy_service.reinitialize()
        self.write("./%d" % task_id)


class DownloadTestcasesHandler(BaseHandler):
    """Download all testcases in a zip file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("download_testcases.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self, dataset_id):
        fallback_page = \
            self.url("dataset", dataset_id, "testcases", "download")

        dataset = self.safe_get_item(Dataset, dataset_id)

        # Get zip file name, input/output file names templates,
        # or use default ones.
        zip_filename: str = self.get_argument("zip_filename", "testcases.zip")
        input_template: str = self.get_argument("input_template", "input.*")
        output_template: str = self.get_argument("output_template", "output.*")

        # Template validations
        error = validate_template(input_template, "input")
        if error is None:
            error = validate_template(output_template, "output")
        if error is not None:
            self.service.add_notification(
                make_datetime(),
                "Invalid template format",
                error)
            self.redirect(fallback_page)
            return

        # Replace input/output template placeholder with the python format.
        input_template = input_template.strip().replace("*", "%s")
        output_template = output_template.strip().replace("*", "%s")

        # FIXME When Tornado will stop having the WSGI adapter buffer
        # the whole response, we could use a tempfile.TemporaryFile so
        # to avoid having the whole ZIP file in memory at once.
        temp_file = io.BytesIO()
        with zipfile.ZipFile(temp_file, "w") as zip_file:
            for testcase in dataset.testcases.values():
                # Copy input file
                with zip_file.open(input_template % testcase.codename, 'w') as fout:
                    self.service.file_cacher.get_file_to_fobj(testcase.input, fout)
                # Copy output file
                with zip_file.open(output_template % testcase.codename, 'w') as fout:
                    self.service.file_cacher.get_file_to_fobj(testcase.output, fout)

        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition",
                        "attachment; filename=\"%s\"" % zip_filename)

        self.write(temp_file.getvalue())


class AddGeneratorHandler(BaseHandler):
    """Add a generator to a dataset.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id):
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.render("add_generator.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id):
        fallback_page = self.url("dataset", dataset_id, "generators", "add")

        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task
        task_name = task.name

        generator_file = self.request.files.get("generator")
        if not generator_file:
            self.service.add_notification(
                make_datetime(),
                "No generator file",
                "Please upload a generator source file.")
            self.redirect(fallback_page)
            return

        generator_file = generator_file[0]
        filename = generator_file["filename"]
        body = generator_file["body"]

        input_filename_template = self.get_argument(
            "input_filename_template", "input.*").strip()
        output_filename_template = self.get_argument(
            "output_filename_template", "output.*").strip()

        error = validate_template(input_filename_template, "input")
        if error is None:
            error = validate_template(output_filename_template, "output")
        if error is not None:
            self.service.add_notification(
                make_datetime(),
                "Invalid template",
                error)
            self.redirect(fallback_page)
            return

        try:
            language = filename_to_language(filename)
        except Exception:
            language = None

        if language is None:
            self.service.add_notification(
                make_datetime(),
                "Unknown language",
                "Could not detect language for file '%s'." % filename)
            self.redirect(fallback_page)
            return

        if not isinstance(language, CompiledLanguage):
            self.service.add_notification(
                make_datetime(),
                "Invalid language",
                "Generator must be a compiled language, not '%s'." %
                language.name)
            self.redirect(fallback_page)
            return

        self.sql_session.close()

        compiled_filename = "generator"

        def notify(title, text):
            self.service.add_notification(make_datetime(), title, text)

        success, compiled_bytes, stats = compile_manager_bytes(
            self.service.file_cacher,
            filename,
            body,
            compiled_filename,
            sandbox_name="admin_compile",
            for_evaluation=True,
            notify=notify
        )

        if not success:
            self.redirect(fallback_page)
            return

        try:
            source_digest = self.service.file_cacher.put_file_content(
                body, "Generator source for %s" % task_name)
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Generator storage failed",
                "Error storing source: %s" % repr(error))
            self.redirect(fallback_page)
            return

        executable_digest = None
        if compiled_bytes is not None:
            try:
                executable_digest = self.service.file_cacher.put_file_content(
                    compiled_bytes, "Compiled generator for %s" % task_name)
            except Exception as error:
                self.service.add_notification(
                    make_datetime(),
                    "Generator storage failed",
                    "Error storing executable: %s" % repr(error))
                self.redirect(fallback_page)
                return

        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        existing_generator = dataset.generators.get(filename)
        if existing_generator is not None:
            existing_generator.digest = source_digest
            existing_generator.executable_digest = executable_digest
            existing_generator.input_filename_template = input_filename_template
            existing_generator.output_filename_template = output_filename_template
        else:
            generator = Generator(
                filename=filename,
                digest=source_digest,
                executable_digest=executable_digest,
                input_filename_template=input_filename_template,
                output_filename_template=output_filename_template,
                dataset=dataset)
            self.sql_session.add(generator)

        if self.try_commit():
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class EditGeneratorHandler(BaseHandler):
    """Edit a generator's filename templates.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id, generator_id):
        generator = self.safe_get_item(Generator, generator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if generator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["generator"] = generator
        self.render("edit_generator.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, generator_id):
        fallback_page = self.url("dataset", dataset_id, "generator",
                                 generator_id, "edit")

        generator = self.safe_get_item(Generator, generator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if generator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task = dataset.task

        input_filename_template = self.get_argument(
            "input_filename_template", "input.*").strip()
        output_filename_template = self.get_argument(
            "output_filename_template", "output.*").strip()

        error = validate_template(input_filename_template, "input")
        if error is None:
            error = validate_template(output_filename_template, "output")
        if error is not None:
            self.service.add_notification(
                make_datetime(),
                "Invalid template",
                error)
            self.redirect(fallback_page)
            return

        generator.input_filename_template = input_filename_template
        generator.output_filename_template = output_filename_template

        if self.try_commit():
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class DeleteGeneratorHandler(BaseHandler):
    """Delete a generator.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, dataset_id, generator_id):
        generator = self.safe_get_item(Generator, generator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if generator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task_id = dataset.task_id
        self.sql_session.delete(generator)

        if self.try_commit():
            self.write("./%d" % task_id)


class GenerateTestcasesHandler(BaseHandler):
    """Generate testcases using a generator.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, dataset_id, generator_id):
        generator = self.safe_get_item(Generator, generator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if generator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        if generator.executable_digest is None:
            self.service.add_notification(
                make_datetime(),
                "Generator not compiled",
                "The generator has not been compiled successfully.")
            self.redirect(self.url("task", dataset.task.id))
            return

        task = dataset.task
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["generator"] = generator
        self.render("generate_testcases.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, generator_id):
        fallback_page = self.url("dataset", dataset_id, "generator",
                                 generator_id, "generate")

        generator = self.safe_get_item(Generator, generator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if generator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        if generator.executable_digest is None:
            self.service.add_notification(
                make_datetime(),
                "Generator not compiled",
                "The generator has not been compiled successfully.")
            self.redirect(self.url("task", dataset.task.id))
            return

        task = dataset.task

        overwrite = self.get_argument("overwrite", "") == "on"
        public = self.get_argument("public", "") == "on"

        input_template = generator.input_filename_template
        output_template = generator.output_filename_template

        self.sql_session.close()

        try:
            language = filename_to_language(generator.filename)
        except Exception:
            language = None

        exe_name = "generator"
        if language is not None and isinstance(language, CompiledLanguage):
            exe_name += language.executable_extension

        sandbox = None
        try:
            sandbox = create_sandbox(self.service.file_cacher,
                                     name="admin_generate")

            sandbox.create_file_from_storage(exe_name,
                                             generator.executable_digest,
                                             executable=True)

            cmd = ["./" + exe_name]
            if language is not None:
                try:
                    cmds = language.get_evaluation_commands(exe_name)
                    if cmds:
                        cmd = cmds[0]
                except Exception:
                    pass

            # Set stdout/stderr files so they are created during execution
            sandbox.stdout_file = "stdout.txt"
            sandbox.stderr_file = "stderr.txt"

            box_success = sandbox.execute_without_std(cmd, wait=True)

            # Read stdout/stderr (best-effort, may not exist)
            stdout = ""
            stderr = ""
            try:
                stdout = sandbox.get_file_to_string("stdout.txt", maxlen=65536)
            except FileNotFoundError:
                pass
            try:
                stderr = sandbox.get_file_to_string("stderr.txt", maxlen=65536)
            except FileNotFoundError:
                pass

            if not box_success:
                self.service.add_notification(
                    make_datetime(),
                    "Generator execution failed",
                    "Sandbox error during execution.\nStdout:\n%s\nStderr:\n%s"
                    % (stdout, stderr))
                self.redirect(fallback_page)
                return

            exit_status = sandbox.get_exit_status()
            if exit_status != sandbox.EXIT_OK:
                self.service.add_notification(
                    make_datetime(),
                    "Generator execution failed",
                    "Exit status: %s\nStdout:\n%s\nStderr:\n%s" %
                    (exit_status, stdout, stderr))
                self.redirect(fallback_page)
                return

            input_re = compile_template_regex(input_template)
            output_re = compile_template_regex(output_template)

            temp_zip = io.BytesIO()
            with zipfile.ZipFile(temp_zip, "w") as zf:
                sandbox_home = sandbox.relative_path("")
                for root, dirs, files in os.walk(sandbox_home):
                    for filename in files:
                        if filename in [exe_name, "stdout.txt", "stderr.txt"]:
                            continue
                        rel_path = os.path.relpath(
                            os.path.join(root, filename), sandbox_home)
                        content = sandbox.get_file_to_string(rel_path, maxlen=None)
                        zf.writestr(rel_path, content)

            temp_zip.seek(0)

        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Generator execution error",
                repr(error))
            self.redirect(fallback_page)
            return
        finally:
            if sandbox:
                sandbox.cleanup(delete=True)

        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        try:
            successful_subject, successful_text = import_testcases_from_zipfile(
                self.sql_session,
                self.service.file_cacher,
                dataset,
                temp_zip,
                input_re,
                output_re,
                overwrite,
                public)
            self.service.add_notification(
                make_datetime(),
                successful_subject,
                successful_text)
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Testcase import failed",
                repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


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

        self.sql_session.close()

        try:
            language = filename_to_language(filename)
        except Exception:
            language = None

        if language is None or not isinstance(language, CompiledLanguage):
            self.service.add_notification(
                make_datetime(),
                "Invalid validator file",
                "Validator must be a compilable source file.")
            self.redirect(fallback_page)
            return

        def notify(title, text):
            self.service.add_notification(make_datetime(), title, text)

        success, compiled_bytes, stats = compile_manager_bytes(
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
            existing_validator.filename = filename
            existing_validator.digest = source_digest
            existing_validator.executable_digest = executable_digest
            for result in existing_validator.validation_results:
                self.sql_session.delete(result)
        else:
            validator = SubtaskValidator(
                dataset=dataset,
                subtask_index=subtask_index,
                filename=filename,
                digest=source_digest,
                executable_digest=executable_digest
            )
            self.sql_session.add(validator)

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Validator uploaded",
                "Validator for subtask %d uploaded successfully. "
                "Run validation to check testcases." % subtask_index)
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class DeleteSubtaskValidatorHandler(BaseHandler):
    """Delete a subtask validator.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, dataset_id, validator_id):
        validator = self.safe_get_item(SubtaskValidator, validator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if validator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task_id = dataset.task_id
        self.sql_session.delete(validator)

        if self.try_commit():
            self.write("./%d" % task_id)


class RunSubtaskValidationHandler(BaseHandler):
    """Run validation for a subtask validator against all testcases.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, dataset_id, validator_id):
        validator = self.safe_get_item(SubtaskValidator, validator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if validator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task = dataset.task
        fallback_page = self.url("task", task.id)

        if validator.executable_digest is None:
            self.service.add_notification(
                make_datetime(),
                "Validator not compiled",
                "The validator has not been compiled successfully.")
            self.redirect(fallback_page)
            return

        testcases = list(dataset.testcases.values())
        validator_id_local = validator.id

        self.sql_session.close()

        try:
            language = filename_to_language(validator.filename)
        except Exception:
            language = None

        exe_name = "validator"
        if language is not None and isinstance(language, CompiledLanguage):
            exe_name += language.executable_extension

        validation_results = []
        sandbox = None

        try:
            for testcase in testcases:
                sandbox = create_sandbox(self.service.file_cacher,
                                         name="admin_validate")

                sandbox.create_file_from_storage(exe_name,
                                                 validator.executable_digest,
                                                 executable=True)
                sandbox.create_file_from_storage("input.txt",
                                                 testcase.input)
                sandbox.create_file_from_storage("output.txt",
                                                 testcase.output)

                cmd = ["./" + exe_name, "input.txt", "output.txt"]
                if language is not None:
                    try:
                        cmds = language.get_evaluation_commands(exe_name)
                        if cmds:
                            cmd = cmds[0] + ["input.txt", "output.txt"]
                    except Exception:
                        pass

                sandbox.stdout_file = "stdout.txt"
                sandbox.stderr_file = "stderr.txt"

                box_success = sandbox.execute_without_std(cmd, wait=True)

                stderr = ""
                try:
                    stderr = sandbox.get_file_to_string("stderr.txt", maxlen=65536)
                except FileNotFoundError:
                    pass

                passed = False
                if box_success:
                    exit_status = sandbox.get_exit_status()
                    if exit_status == sandbox.EXIT_OK:
                        exit_code = sandbox.get_exit_code()
                        passed = (exit_code == 0)

                validation_results.append({
                    "testcase_id": testcase.id,
                    "passed": passed,
                    "stderr": stderr[:4096] if stderr else None
                })

                sandbox.cleanup(delete=True)
                sandbox = None

        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Validation execution error",
                repr(error))
            if sandbox:
                sandbox.cleanup(delete=True)
            self.redirect(fallback_page)
            return

        self.sql_session = Session()
        validator = self.safe_get_item(SubtaskValidator, validator_id_local)
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        for result in validator.validation_results:
            self.sql_session.delete(result)
        self.sql_session.flush()

        for result_data in validation_results:
            result = SubtaskValidationResult(
                validator=validator,
                testcase_id=result_data["testcase_id"],
                passed=result_data["passed"],
                stderr=result_data["stderr"]
            )
            self.sql_session.add(result)

        passed_count = sum(1 for r in validation_results if r["passed"])
        failed_count = len(validation_results) - passed_count

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Validation complete",
                "Validated %d testcases: %d passed, %d failed." %
                (len(validation_results), passed_count, failed_count))
            self.redirect(self.url("task", task.id))
        else:
            self.redirect(fallback_page)


class SubtaskValidatorDetailsHandler(BaseHandler):
    """Show validation details for a subtask validator.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, dataset_id, validator_id):
        validator = self.safe_get_item(SubtaskValidator, validator_id)
        dataset = self.safe_get_item(Dataset, dataset_id)

        if validator.dataset is not dataset:
            raise tornado.web.HTTPError(404)

        task = dataset.task
        self.contest = task.contest

        from cms.grading.scoretypes.ScoreTypeGroup import ScoreTypeGroup

        subtask_testcases = []
        other_testcases = []

        try:
            score_type_obj = dataset.score_type_object
            if isinstance(score_type_obj, ScoreTypeGroup):
                targets = score_type_obj.retrieve_target_testcases()
                if validator.subtask_index < len(targets):
                    subtask_tc_codenames = set(targets[validator.subtask_index])
                else:
                    subtask_tc_codenames = set()
            else:
                subtask_tc_codenames = set()
        except Exception:
            subtask_tc_codenames = set()

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

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.r_params["dataset"] = dataset
        self.r_params["validator"] = validator
        self.r_params["subtask_testcases"] = subtask_testcases
        self.r_params["other_testcases"] = other_testcases
        self.render("subtask_validator_details.html", **self.r_params)
