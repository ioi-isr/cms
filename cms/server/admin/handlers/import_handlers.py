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

"""Import handlers for AWS - allows importing tasks and contests from
zip files.

"""

import logging
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager

from cmscommon.datetime import make_datetime
from cmscontrib.ImportTask import TaskImporter
from cmscontrib.ImportContest import ContestImporter
from cmscontrib.importing import ImportDataError
from cmscontrib.loaders import choose_loader
from cmscontrib.loaders.base_loader import LoaderValidationError

from .base import BaseHandler, SimpleHandler, require_permission


logger = logging.getLogger(__name__)


@contextmanager
def _extract_uploaded_zip(uploaded_file, temp_prefix, zip_filename):
    """Write an uploaded zip to disk, extract it, and yield the root path."""
    temp_dir = tempfile.mkdtemp(prefix=temp_prefix)
    try:
        zip_path = os.path.join(temp_dir, zip_filename)
        with open(zip_path, "wb") as f:
            f.write(uploaded_file["body"])

        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        contents = os.listdir(extract_dir)
        if len(contents) == 1 and \
                os.path.isdir(os.path.join(extract_dir, contents[0])):
            root_path = os.path.join(extract_dir, contents[0])
        else:
            root_path = extract_dir

        yield root_path
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class ImportTaskHandler(
        SimpleHandler("import_task.html", permission_all=True)):
    """Handler for importing a task from a zip file.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("tasks", "import")

        if "task_file" not in self.request.files:
            self.service.add_notification(
                make_datetime(),
                "No file uploaded",
                "Please select a zip file to upload.")
            self.redirect(fallback_page)
            return

        task_file = self.request.files["task_file"][0]

        if not task_file["filename"].endswith(".zip"):
            self.service.add_notification(
                make_datetime(),
                "Invalid file format",
                "The uploaded file must be a .zip file.")
            self.redirect(fallback_page)
            return

        update = bool(self.get_argument("update", False))
        no_statement = bool(self.get_argument("no_statement", False))
        contest_id_str = self.get_argument("contest_id", None)
        if contest_id_str and contest_id_str != "":
            contest_id = int(contest_id_str)
        else:
            contest_id = None
        loader_name = self.get_argument("loader", None)
        if loader_name == "":
            loader_name = None
        
        input_template = self.get_argument("input_template", "").strip()
        output_template = self.get_argument("output_template", "").strip()

        try:
            with _extract_uploaded_zip(
                    task_file, "cms_import_task_", "task.zip") as task_path:
                if input_template or output_template:
                    import yaml
                    task_yaml_path = os.path.join(task_path, "task.yaml")
                    if os.path.exists(task_yaml_path):
                        try:
                            with open(task_yaml_path, "r",
                                      encoding="utf-8") as f:
                                task_config = yaml.safe_load(f)

                            if task_config is None:
                                task_config = {}

                            if input_template and \
                                    "input_template" not in task_config:
                                task_config["input_template"] = input_template
                            if output_template and \
                                    "output_template" not in task_config:
                                task_config["output_template"] = (
                                    output_template)

                            with open(task_yaml_path, "w",
                                      encoding="utf-8") as f:
                                yaml.dump(
                                    task_config, f, default_flow_style=False,
                                    allow_unicode=True)
                        except Exception as e:
                            logger.warning(
                                "Failed to inject templates into task.yaml: %s",
                                e)

                def error_callback(msg):
                    raise ValueError(msg)

                loader_class = choose_loader(
                    loader_name, task_path, error_callback)

                importer = TaskImporter(
                    path=task_path,
                    update=update,
                    no_statement=no_statement,
                    contest_id=contest_id,
                    prefix=None,
                    override_name=None,
                    loader_class=loader_class,
                    raise_import_errors=True
                )

                if hasattr(importer.loader, 'set_notifier'):
                    def notify(title, text):
                        self.service.add_notification(make_datetime(), title, text)
                    importer.loader.set_notifier(notify)

                try:
                    success = importer.do_import()
                    if success:
                        self.service.add_notification(
                            make_datetime(),
                            "Task imported successfully",
                            "")
                        self.redirect(self.url("tasks"))
                    else:
                        self.service.add_notification(
                            make_datetime(),
                            "Task import failed",
                            "Import failed. Please check the logs for details.")
                        self.redirect(fallback_page)
                except (LoaderValidationError, ImportDataError) as e:
                    self.service.add_notification(
                        make_datetime(),
                        "Task import failed",
                        str(e))
                    self.redirect(fallback_page)

        except Exception as error:
            logger.error("Task import failed: %s", error, exc_info=True)
            self.service.add_notification(
                make_datetime(),
                "Task import failed",
                str(error))
            self.redirect(fallback_page)


class ImportContestHandler(
        SimpleHandler("import_contest.html", permission_all=True)):
    """Handler for importing a contest from a zip file.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("contests", "import")

        if "contest_file" not in self.request.files:
            self.service.add_notification(
                make_datetime(),
                "No file uploaded",
                "Please select a zip file to upload.")
            self.redirect(fallback_page)
            return

        contest_file = self.request.files["contest_file"][0]

        if not contest_file["filename"].endswith(".zip"):
            self.service.add_notification(
                make_datetime(),
                "Invalid file format",
                "The uploaded file must be a .zip file.")
            self.redirect(fallback_page)
            return

        import_tasks = bool(self.get_argument("import_tasks", False))
        update_contest = bool(self.get_argument("update_contest", False))
        update_tasks = bool(self.get_argument("update_tasks", False))
        no_statements = bool(self.get_argument("no_statements", False))
        loader_name = self.get_argument("loader", None)
        if loader_name == "":
            loader_name = None

        try:
            with _extract_uploaded_zip(
                    contest_file, "cms_import_contest_", "contest.zip") \
                    as contest_path:
                def error_callback(msg):
                    raise ValueError(msg)

                loader_class = choose_loader(
                    loader_name, contest_path, error_callback)

                importer = ContestImporter(
                    path=contest_path,
                    yes=True,
                    zero_time=False,
                    import_tasks=import_tasks,
                    update_contest=update_contest,
                    update_tasks=update_tasks,
                    no_statements=no_statements,
                    delete_stale_participations=False,
                    loader_class=loader_class,
                    raise_import_errors=True
                )

                if hasattr(importer.loader, 'set_notifier'):
                    def notify(title, text):
                        self.service.add_notification(make_datetime(), title, text)
                    importer.loader.set_notifier(notify)

                try:
                    success = importer.do_import()
                    if success:
                        self.service.add_notification(
                            make_datetime(),
                            "Contest imported successfully",
                            "")
                        self.redirect(self.url("contests"))
                    else:
                        self.service.add_notification(
                            make_datetime(),
                            "Contest import failed",
                            "Import failed. Please check the logs for details.")
                        self.redirect(fallback_page)
                except (LoaderValidationError, ImportDataError) as e:
                    self.service.add_notification(
                        make_datetime(),
                        "Contest import failed",
                        str(e))
                    self.redirect(fallback_page)

        except Exception as error:
            logger.error("Contest import failed: %s", error,
                         exc_info=True)
            self.service.add_notification(
                make_datetime(),
                "Contest import failed",
                str(error))
            self.redirect(fallback_page)
