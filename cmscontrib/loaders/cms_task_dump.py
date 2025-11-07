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

"""Loader for CMS task dump format.

This loader imports tasks from the format exported by TaskExporter.

"""

import json
import logging
import os
from datetime import timedelta

from cms.db import Attachment, Dataset, Manager, Statement, Task, Testcase
from cms.db.filecacher import FileCacher
from .base_loader import TaskLoader


logger = logging.getLogger(__name__)


class CMSTaskDumpLoader(TaskLoader):
    """Loader for CMS task dump format."""

    short_name = "cms_task_dump"
    description = "CMS task dump format (exported by TaskExporter)"

    def __init__(self, path: str, file_cacher: FileCacher):
        """Initialize the loader.

        path: path to the extracted task dump directory.
        file_cacher: FileCacher instance to use for storing files.

        """
        super().__init__(path, file_cacher)
        self.metadata_path = os.path.join(path, "metadata.json")
        self.files_dir = os.path.join(path, "files")
        self.metadata = None

    @staticmethod
    def detect(path: str) -> bool:
        """Detect if the path contains a CMS task dump.

        path: path to check.

        return: True if the path contains a CMS task dump.

        """
        metadata_path = os.path.join(path, "metadata.json")
        if not os.path.exists(metadata_path):
            return False

        try:
            with open(metadata_path, "rt", encoding="utf-8") as f:
                metadata = json.load(f)
            return metadata.get("cms_task_dump_version") == 1
        except Exception:
            return False

    def get_task(self, get_statement: bool) -> Task | None:
        """Load and return the Task object.

        get_statement: whether to import statements.

        return: the Task object, or None on error.

        """
        try:
            with open(self.metadata_path, "rt", encoding="utf-8") as f:
                self.metadata = json.load(f)

            task = Task()
            task.name = self.metadata["name"]
            task.title = self.metadata["title"]
            task.submission_format = self.metadata["submission_format"]
            task.primary_statements = self.metadata["primary_statements"]
            task.token_mode = self.metadata["token_mode"]
            task.token_max_number = self.metadata.get("token_max_number")
            task.token_min_interval = timedelta(
                seconds=self.metadata.get("token_min_interval", 0))
            task.token_gen_initial = self.metadata["token_gen_initial"]
            task.token_gen_number = self.metadata["token_gen_number"]
            task.token_gen_interval = timedelta(
                seconds=self.metadata["token_gen_interval"])
            task.token_gen_max = self.metadata.get("token_gen_max")
            task.max_submission_number = self.metadata.get(
                "max_submission_number")
            task.max_user_test_number = self.metadata.get(
                "max_user_test_number")

            min_sub_interval = self.metadata.get("min_submission_interval")
            task.min_submission_interval = (
                timedelta(seconds=min_sub_interval)
                if min_sub_interval is not None else None
            )

            min_test_interval = self.metadata.get("min_user_test_interval")
            task.min_user_test_interval = (
                timedelta(seconds=min_test_interval)
                if min_test_interval is not None else None
            )

            task.feedback_level = self.metadata["feedback_level"]
            task.score_precision = self.metadata["score_precision"]
            task.score_mode = self.metadata["score_mode"]
            task.allowed_languages = self.metadata.get("allowed_languages")

            if get_statement:
                for lang, stmt_data in self.metadata["statements"].items():
                    statement = Statement()
                    statement.language = lang
                    statement.digest = self._import_file(
                        stmt_data["filename"])
                    task.statements[lang] = statement

            for filename, att_data in self.metadata["attachments"].items():
                attachment = Attachment()
                attachment.filename = filename
                attachment.digest = self._import_file(filename)
                task.attachments[filename] = attachment

            active_dataset_desc = self.metadata["active_dataset"]["value"]
            for dataset_data in self.metadata["datasets"]:
                dataset = Dataset()
                dataset.description = dataset_data["description"]
                dataset.autojudge = dataset_data["autojudge"]
                dataset.time_limit = dataset_data.get("time_limit")
                dataset.memory_limit = dataset_data.get("memory_limit")
                dataset.task_type = dataset_data["task_type"]
                dataset.task_type_parameters = dataset_data[
                    "task_type_parameters"]
                dataset.score_type = dataset_data["score_type"]
                dataset.score_type_parameters = dataset_data[
                    "score_type_parameters"]

                for filename, mgr_data in dataset_data["managers"].items():
                    manager = Manager()
                    manager.filename = filename
                    manager.digest = self._import_file(filename)
                    dataset.managers[filename] = manager

                for codename, tc_data in dataset_data["testcases"].items():
                    testcase = Testcase()
                    testcase.codename = codename
                    testcase.public = tc_data["public"]
                    testcase.input = self._import_file(
                        tc_data["input_filename"])
                    testcase.output = self._import_file(
                        tc_data["output_filename"])
                    dataset.testcases[codename] = testcase

                task.datasets.append(dataset)

                if dataset.description == active_dataset_desc:
                    task.active_dataset = dataset

            return task

        except Exception:
            logger.error("Failed to load task from dump.",
                         exc_info=True)
            return None

    def task_has_changed(self) -> bool:
        """Check if the task has changed.

        For new imports, always return True.

        return: True (always, for simplicity).

        """
        return True

    def _import_file(self, filename: str) -> str:
        """Import a file into FileCacher.

        filename: name of the file in the files/ directory.

        return: digest of the imported file.

        """
        file_path = os.path.join(self.files_dir, filename)
        digest = self.file_cacher.put_file_from_path(file_path)
        return digest
