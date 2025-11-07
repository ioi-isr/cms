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

"""Task import/export handlers for AWS.

"""

import logging
import os
import tempfile
import traceback

import tornado.web

from cms.db import Session, Task
from cmscommon.archive import Archive
from cmscommon.datetime import make_datetime
from cmscontrib.DumpExporter import DumpExporter
from cmscontrib.DumpImporter import DumpImporter
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class ExportTaskHandler(BaseHandler):
    """Handler to export a task as a .zip file.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)
        self.contest = task.contest

        self.r_params = self.render_params()
        self.r_params["task"] = task
        self.render("export_task.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id):
        task = self.safe_get_item(Task, task_id)
        task_name = task.name

        include_submissions = self.get_argument("include_submissions", "false") == "true"

        self.sql_session.close()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                export_path = os.path.join(temp_dir, f"{task_name}.tar.gz")

                exporter = DumpExporter(
                    contest_ids=None,
                    export_target=export_path,
                    dump_files=True,
                    dump_model=True,
                    skip_generated=False,
                    skip_submissions=not include_submissions,
                    skip_user_tests=True,
                    skip_users=True,
                    skip_print_jobs=True,
                )

                exporter.tasks_ids = [int(task_id)]
                exporter.contests_ids = []
                exporter.users_ids = []

                success = exporter.do_export()

                if not success:
                    self.sql_session = Session()
                    task = self.safe_get_item(Task, task_id)
                    self.service.add_notification(
                        make_datetime(),
                        "Export failed",
                        "Failed to export task. Check logs for details.")
                    self.redirect(self.url("task", task_id))
                    return

                with open(export_path, 'rb') as f:
                    file_data = f.read()

                self.set_header('Content-Type', 'application/gzip')
                self.set_header('Content-Disposition',
                               f'attachment; filename="{task_name}.tar.gz"')
                self.write(file_data)
                self.finish()

        except Exception as error:
            logger.error("Error exporting task: %s" % traceback.format_exc())
            self.sql_session = Session()
            task = self.safe_get_item(Task, task_id)
            self.service.add_notification(
                make_datetime(),
                "Export failed",
                repr(error))
            self.redirect(self.url("task", task_id))


class ImportTaskHandler(BaseHandler):
    """Handler to import a task from a .zip file.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.render("import_task.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("tasks", "import")

        if "task_file" not in self.request.files:
            self.service.add_notification(
                make_datetime(),
                "No file uploaded",
                "Please select a task archive file to import.")
            self.redirect(fallback_page)
            return

        task_file = self.request.files["task_file"][0]
        filename = task_file["filename"]

        if not (filename.endswith(".tar.gz") or filename.endswith(".tar.bz2") or
                filename.endswith(".tar") or filename.endswith(".zip")):
            self.service.add_notification(
                make_datetime(),
                "Invalid file format",
                "Task archive must be a .tar.gz, .tar.bz2, .tar, or .zip file.")
            self.redirect(fallback_page)
            return

        self.sql_session.close()

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                archive_path = os.path.join(temp_dir, filename)

                with open(archive_path, 'wb') as f:
                    f.write(task_file["body"])

                importer = DumpImporter(
                    drop=False,
                    import_source=archive_path,
                    load_files=True,
                    load_model=True,
                    skip_generated=False,
                    skip_submissions=False,
                    skip_user_tests=True,
                    skip_users=True,
                    skip_print_jobs=True,
                )

                success = importer.do_import()

                if not success:
                    self.sql_session = Session()
                    self.service.add_notification(
                        make_datetime(),
                        "Import failed",
                        "Failed to import task. Check logs for details.")
                    self.redirect(fallback_page)
                    return

                self.sql_session = Session()
                self.service.add_notification(
                    make_datetime(),
                    "Import successful",
                    f"Task imported successfully from {filename}.")
                self.redirect(self.url("tasks"))

        except Exception as error:
            logger.error("Error importing task: %s" % traceback.format_exc())
            self.sql_session = Session()
            self.service.add_notification(
                make_datetime(),
                "Import failed",
                repr(error))
            self.redirect(fallback_page)
