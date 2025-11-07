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

"""Task exporter for CMS.

This module exports a single task with all its related data (statements,
attachments, managers, testcases, scoring parameters) to a self-contained
archive that can be imported using TaskImporter with CMSTaskDumpLoader.

"""

import json
import logging
import os
import tarfile
import tempfile
from datetime import timedelta

from cms.db import SessionGen, Task
from cms.db.filecacher import FileCacher


logger = logging.getLogger(__name__)


class TaskExporter:
    """Export a single task to a self-contained archive."""

    def __init__(
        self,
        task_id: int,
        export_target: str,
        include_submissions: bool = False,
    ):
        """Initialize the TaskExporter.

        task_id: the ID of the task to export.
        export_target: path to the output .tar.gz file.
        include_submissions: whether to include submissions in the export.

        """
        self.task_id = task_id
        self.export_target = export_target
        self.include_submissions = include_submissions
        self.file_cacher = FileCacher()

    def do_export(self) -> bool:
        """Run the actual export code.

        return: True if successful, False otherwise.

        """
        logger.info("Starting task export for task ID %s.", self.task_id)

        if os.path.exists(self.export_target):
            logger.critical("The specified file already exists, "
                            "I won't overwrite it.")
            return False

        with SessionGen() as session:
            task = Task.get_from_id(self.task_id, session)
            if task is None:
                logger.critical("Task with ID %s not found.", self.task_id)
                return False

            with tempfile.TemporaryDirectory() as temp_dir:
                export_dir = os.path.join(temp_dir, task.name)
                os.mkdir(export_dir)
                files_dir = os.path.join(export_dir, "files")
                os.mkdir(files_dir)

                metadata = self._build_metadata(task, session)

                if not self._export_files(task, files_dir):
                    return False

                metadata_path = os.path.join(export_dir, "metadata.json")
                with open(metadata_path, "wt", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, sort_keys=True)

                with tarfile.open(self.export_target, "w:gz") as archive:
                    archive.add(export_dir, arcname=task.name)

        logger.info("Task export finished successfully.")
        return True

    def _build_metadata(self, task: Task, session) -> dict:
        """Build the metadata dictionary for the task.

        task: the Task object to export.
        session: SQLAlchemy session.

        return: metadata dictionary.

        """
        metadata = {
            "cms_task_dump_version": 1,
            "name": task.name,
            "title": task.title,
            "submission_format": task.submission_format,
            "primary_statements": task.primary_statements,
            "token_mode": task.token_mode,
            "token_max_number": task.token_max_number,
            "token_min_interval": (
                task.token_min_interval.total_seconds()
                if task.token_min_interval else 0
            ),
            "token_gen_initial": task.token_gen_initial,
            "token_gen_number": task.token_gen_number,
            "token_gen_interval": task.token_gen_interval.total_seconds(),
            "token_gen_max": task.token_gen_max,
            "max_submission_number": task.max_submission_number,
            "max_user_test_number": task.max_user_test_number,
            "min_submission_interval": (
                task.min_submission_interval.total_seconds()
                if task.min_submission_interval else None
            ),
            "min_user_test_interval": (
                task.min_user_test_interval.total_seconds()
                if task.min_user_test_interval else None
            ),
            "feedback_level": task.feedback_level,
            "score_precision": task.score_precision,
            "score_mode": task.score_mode,
            "allowed_languages": task.allowed_languages,
        }

        metadata["statements"] = {}
        for language, statement in task.statements.items():
            metadata["statements"][language] = {
                "language": language,
                "digest": statement.digest,
                "filename": f"statement_{language}.pdf",
            }

        metadata["attachments"] = {}
        for filename, attachment in task.attachments.items():
            metadata["attachments"][filename] = {
                "filename": filename,
                "digest": attachment.digest,
            }

        metadata["datasets"] = []
        active_dataset_description = None
        if task.active_dataset:
            active_dataset_description = task.active_dataset.description

        for dataset in task.datasets:
            dataset_data = {
                "description": dataset.description,
                "autojudge": dataset.autojudge,
                "time_limit": dataset.time_limit,
                "memory_limit": dataset.memory_limit,
                "task_type": dataset.task_type,
                "task_type_parameters": dataset.task_type_parameters,
                "score_type": dataset.score_type,
                "score_type_parameters": dataset.score_type_parameters,
            }

            dataset_data["managers"] = {}
            for filename, manager in dataset.managers.items():
                dataset_data["managers"][filename] = {
                    "filename": filename,
                    "digest": manager.digest,
                }

            dataset_data["testcases"] = {}
            for codename, testcase in dataset.testcases.items():
                dataset_data["testcases"][codename] = {
                    "codename": codename,
                    "public": testcase.public,
                    "input_digest": testcase.input,
                    "output_digest": testcase.output,
                    "input_filename": f"testcase_{codename}.in",
                    "output_filename": f"testcase_{codename}.out",
                }

            metadata["datasets"].append(dataset_data)

        metadata["active_dataset"] = {
            "by": "description",
            "value": active_dataset_description,
        }

        return metadata

    def _export_files(self, task: Task, files_dir: str) -> bool:
        """Export all files referenced by the task.

        task: the Task object.
        files_dir: directory to export files to.

        return: True if successful, False otherwise.

        """
        for language, statement in task.statements.items():
            filename = f"statement_{language}.pdf"
            path = os.path.join(files_dir, filename)
            if not self._export_file(statement.digest, path):
                return False

        for filename, attachment in task.attachments.items():
            path = os.path.join(files_dir, filename)
            if not self._export_file(attachment.digest, path):
                return False

        for dataset in task.datasets:
            for filename, manager in dataset.managers.items():
                path = os.path.join(files_dir, filename)
                if not self._export_file(manager.digest, path):
                    return False

            for codename, testcase in dataset.testcases.items():
                input_path = os.path.join(
                    files_dir, f"testcase_{codename}.in")
                output_path = os.path.join(
                    files_dir, f"testcase_{codename}.out")
                if not self._export_file(testcase.input, input_path):
                    return False
                if not self._export_file(testcase.output, output_path):
                    return False

        return True

    def _export_file(self, digest: str, path: str) -> bool:
        """Export a single file from FileCacher.

        digest: the digest of the file to export.
        path: the path to export the file to.

        return: True if successful, False otherwise.

        """
        try:
            self.file_cacher.get_file_to_path(digest, path)
            return True
        except Exception:
            logger.error("Failed to export file %s.", digest, exc_info=True)
            return False
