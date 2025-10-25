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

from cms.db import Dataset, Manager, Message, Participation, \
    Session, Submission, Task, Testcase
from cms.grading.tasktypes import get_task_type_class
from cms.grading.tasktypes.util import create_sandbox
from cms.grading.languagemanager import filename_to_language
from cms.grading.language import CompiledLanguage
from cms.grading.steps.compilation import compilation_step
from cms.grading.scoring import compute_changes_for_dataset
from cmscommon.datetime import make_datetime
from cmscommon.importers import import_testcases_from_zipfile
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


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

        manager = self.request.files["manager"][0]
        task_name = task.name

        filename = manager["filename"]
        body = manager["body"]

        # Decide which auto-compiled basenames are allowed for this task type.
        # Use TaskType constants to avoid hardcoding names and to avoid
        # compiling unintended files (e.g., manager.%l for TwoSteps).
        allowed_compile_basenames: set[str] = set()
        try:
            tt_cls = get_task_type_class(dataset.task_type)
            # Many task types (Batch, OutputOnly, TwoSteps, BatchAndOutput)
            # define CHECKER_CODENAME = "checker"; only compile that.
            if hasattr(tt_cls, "CHECKER_CODENAME"):
                allowed_compile_basenames.add(getattr(tt_cls, "CHECKER_CODENAME"))
            # Communication defines MANAGER_FILENAME = "manager"; allow that.
            if hasattr(tt_cls, "MANAGER_FILENAME"):
                allowed_compile_basenames.add(getattr(tt_cls, "MANAGER_FILENAME"))
        except Exception:
            # If anything goes wrong, fall back to not auto-compiling.
            allowed_compile_basenames = set()
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

        # If a source file for a known compiled language is uploaded,
        # compile it into an executable manager.
        compiled_filename = None
        compiled_bytes = None
        try:
            language = filename_to_language(filename)
        except Exception:
            language = None

        if (language is not None
                and isinstance(language, CompiledLanguage)
                and base_noext in allowed_compile_basenames):
            safe_src = os.path.basename(filename)
            compiled_filename = base_noext
            
            sandbox = None
            try:
                sandbox = create_sandbox(self.service.file_cacher, name="admin_compile")
                
                sandbox.create_file_from_string(safe_src, body)
                
                commands = language.get_compilation_commands(
                    [safe_src], compiled_filename, for_evaluation=True)
                
                box_success, compilation_success, text, stats = \
                    compilation_step(sandbox, commands)
                
                if not box_success:
                    self.service.add_notification(
                        make_datetime(),
                        "Manager compilation failed",
                        "Sandbox error during compilation. See logs for details.")
                    self.redirect(fallback_page)
                    return
                
                if not compilation_success:
                    stdout = stats.get("stdout", "") if stats else ""
                    stderr = stats.get("stderr", "") if stats else ""
                    self.service.add_notification(
                        make_datetime(),
                        "Manager compilation failed",
                        ("Compilation failed. Command:%r\nStdout:\n%s\nStderr:\n%s" %
                         (commands, stdout, stderr)))
                    self.redirect(fallback_page)
                    return
                
                compiled_bytes = sandbox.get_file_to_string(compiled_filename, maxlen=None)
                
            except Exception as error:
                self.service.add_notification(
                    make_datetime(),
                    "Manager compilation error",
                    repr(error))
                self.redirect(fallback_page)
                return
            finally:
                if sandbox:
                    sandbox.cleanup(delete=True)

        # Store the appropriate content(s) into the file cache.
        stored_entries: list[tuple[str, str]] = []  # (filename, digest)
        try:
            # Always store the original upload.
            orig_digest = self.service.file_cacher.put_file_content(
                body, "Task manager for %s" % task_name)
            stored_entries.append((filename, orig_digest))
            # If compilation happened, also store compiled executable.
            if compiled_bytes is not None and compiled_filename is not None:
                comp_digest = self.service.file_cacher.put_file_content(
                    compiled_bytes, "Compiled task manager for %s" % task_name)
                stored_entries.append((compiled_filename, comp_digest))
        except Exception as error:
            self.service.add_notification(
                make_datetime(),
                "Manager storage failed",
                repr(error))
            self.redirect(fallback_page)
            return

        self.sql_session = Session()
        dataset = self.safe_get_item(Dataset, dataset_id)
        task = dataset.task

        for fname, dig in stored_entries:
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
        input_re = re.compile(re.escape(input_template).replace("\\*",
                              "(.*)") + "$")
        output_re = re.compile(re.escape(output_template).replace("\\*",
                               "(.*)") + "$")

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

        self.sql_session.delete(testcase)

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
        if input_template.count('*') != 1 or output_template.count('*') != 1:
            self.service.add_notification(
                make_datetime(),
                "Invalid template format",
                "You must have exactly one '*' in input/output template.")
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
