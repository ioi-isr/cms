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
import secrets
import shutil
import tempfile
import zipfile
from contextlib import contextmanager

import yaml

from cms.db.filecacher import FileCacher
from cmscommon.datetime import make_datetime
from cmscontrib.ImportTask import TaskImporter
from cmscontrib.ImportContest import ContestImporter
from cmscontrib.importing import ImportDataError
from cmscontrib.loaders import choose_loader
from cmscontrib.loaders.base_loader import LoaderValidationError

from .base import BaseHandler, SimpleHandler, require_permission
from .modelsolution import get_subtask_info


logger = logging.getLogger(__name__)

# Directory for storing pending import zip files
PENDING_IMPORTS_DIR = "/tmp/cms_pending_imports"


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


def _save_pending_import(uploaded_file):
    """Save an uploaded zip file for later processing.

    Returns a token that can be used to retrieve the file.
    """
    os.makedirs(PENDING_IMPORTS_DIR, exist_ok=True)
    token = secrets.token_hex(16)
    zip_path = os.path.join(PENDING_IMPORTS_DIR, f"{token}.zip")
    with open(zip_path, "wb") as f:
        f.write(uploaded_file["body"])
    return token


def _get_pending_import_path(token):
    """Get the path to a pending import zip file."""
    if not token or not token.isalnum():
        return None
    zip_path = os.path.join(PENDING_IMPORTS_DIR, f"{token}.zip")
    if os.path.exists(zip_path):
        return zip_path
    return None


def _delete_pending_import(token):
    """Delete a pending import zip file."""
    zip_path = _get_pending_import_path(token)
    if zip_path:
        try:
            os.remove(zip_path)
        except OSError:
            pass


@contextmanager
def _extract_pending_zip(token):
    """Extract a pending import zip file and yield the root path."""
    zip_path = _get_pending_import_path(token)
    if not zip_path:
        raise ValueError("Invalid or expired import token")

    temp_dir = tempfile.mkdtemp(prefix="cms_import_task_")
    try:
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


def _inject_templates_into_yaml(task_path, input_template, output_template):
    """Inject input/output templates into task.yaml if not already present."""
    if not input_template and not output_template:
        return

    task_yaml_path = os.path.join(task_path, "task.yaml")
    if not os.path.exists(task_yaml_path):
        return

    try:
        with open(task_yaml_path, "r", encoding="utf-8") as f:
            task_config = yaml.safe_load(f) or {}

        if input_template and "input_template" not in task_config:
            task_config["input_template"] = input_template
        if output_template and "output_template" not in task_config:
            task_config["output_template"] = output_template

        with open(task_yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(task_config, f, default_flow_style=False,
                      allow_unicode=True)
    except Exception as e:
        logger.warning("Failed to inject templates into task.yaml: %s", e)


def _inject_model_solutions_metadata(task_path, model_solutions_metadata):
    """Inject model solutions metadata into task.yaml.

    model_solutions_metadata: list of dicts with keys:
        - name: solution name
        - description: description
        - expected_score_min: min score
        - expected_score_max: max score
        - subtask_expected_scores: dict of subtask scores (optional)
    """
    if not model_solutions_metadata:
        return

    task_yaml_path = os.path.join(task_path, "task.yaml")
    if not os.path.exists(task_yaml_path):
        return

    try:
        with open(task_yaml_path, "r", encoding="utf-8") as f:
            task_config = yaml.safe_load(f) or {}

        # Get existing model_solutions or create empty list
        existing_solutions = task_config.get("model_solutions", []) or []
        existing_by_name = {s.get("name"): s for s in existing_solutions}

        # Merge new metadata
        for meta in model_solutions_metadata:
            name = meta["name"]
            if name in existing_by_name:
                # Update existing entry
                existing_by_name[name].update(meta)
            else:
                # Add new entry
                existing_solutions.append(meta)

        task_config["model_solutions"] = existing_solutions

        with open(task_yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(task_config, f, default_flow_style=False,
                      allow_unicode=True)
    except Exception as e:
        logger.warning(
            "Failed to inject model solutions metadata into task.yaml: %s", e)


class ImportTaskHandler(
        SimpleHandler("import_task.html", permission_all=True)):
    """Handler for importing a task from a zip file.

    Supports a two-step flow when model solutions are found without metadata:
    1. First POST: Upload zip, detect model solutions, show config page if needed
    2. Second POST (to ImportTaskModelSolutionsHandler): Complete import with metadata
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
                # Inject templates if provided
                _inject_templates_into_yaml(
                    task_path, input_template, output_template)

                def error_callback(msg):
                    raise ValueError(msg)

                loader_class = choose_loader(
                    loader_name, task_path, error_callback)

                # Create a temporary file cacher to discover model solutions
                file_cacher = FileCacher()
                loader = loader_class(task_path, file_cacher)

                if hasattr(loader, 'set_notifier'):
                    def notify(title, text):
                        self.service.add_notification(make_datetime(), title, text)
                    loader.set_notifier(notify)

                # Get the task to discover model solutions
                task = loader.get_task(get_statement=not no_statement)
                if task is None:
                    self.service.add_notification(
                        make_datetime(),
                        "Task import failed",
                        "Failed to parse task from archive.")
                    self.redirect(fallback_page)
                    return

                # Check if there are model solutions without metadata
                dataset = task.active_dataset
                model_solutions_data = []
                if dataset is not None and \
                        hasattr(dataset, '_model_solutions_import_data'):
                    model_solutions_data = dataset._model_solutions_import_data

                # Check if any model solutions are missing metadata
                missing_metadata = any(
                    not sol.get("has_metadata", True)
                    for sol in model_solutions_data
                )

                if missing_metadata and model_solutions_data:
                    # Save the zip for later and show config page
                    import_token = _save_pending_import(task_file)

                    # Render the configuration page with proper context
                    params = self.render_params()
                    params.update({
                        "import_token": import_token,
                        "model_solutions": model_solutions_data,
                        "update": update,
                        "no_statement": no_statement,
                        "contest_id": contest_id,
                        "loader_name": loader_name,
                        "input_template": input_template,
                        "output_template": output_template,
                        "subtasks": get_subtask_info(dataset) if dataset else None
                    })
                    self.render("import_task_model_solutions.html", **params)
                    return

                # No missing metadata, proceed with import directly
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
                    def notify2(title, text):
                        self.service.add_notification(make_datetime(), title, text)
                    importer.loader.set_notifier(notify2)

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


class ImportTaskModelSolutionsHandler(BaseHandler):
    """Handler for completing task import with model solution metadata.

    This is the second step of the two-step import flow when model solutions
    are found without metadata in task.yaml.
    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("tasks", "import")

        import_token = self.get_argument("import_token", None)
        if not import_token or not _get_pending_import_path(import_token):
            self.service.add_notification(
                make_datetime(),
                "Import failed",
                "Invalid or expired import token. Please upload the task again.")
            self.redirect(fallback_page)
            return

        # Get import options from hidden fields
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

        # Parse model solution metadata from form
        model_solutions_metadata = []
        idx = 0
        while True:
            name = self.get_argument(f"sol_{idx}_name", None)
            if name is None:
                break

            # Check if this solution needs metadata (has form fields)
            description = self.get_argument(f"sol_{idx}_description", None)
            if description is not None:
                # This solution was missing metadata, get the form values
                score_min = float(self.get_argument(
                    f"sol_{idx}_score_min", "0.0"))
                score_max = float(self.get_argument(
                    f"sol_{idx}_score_max", "100.0"))

                # Parse subtask scores if present
                subtask_scores = {}
                st_idx = 0
                while True:
                    st_min = self.get_argument(
                        f"sol_{idx}_st_{st_idx}_min", None)
                    st_max = self.get_argument(
                        f"sol_{idx}_st_{st_idx}_max", None)
                    if st_min is None and st_max is None:
                        break
                    if st_min is not None and st_max is not None:
                        subtask_scores[str(st_idx)] = {
                            "min": float(st_min),
                            "max": float(st_max),
                        }
                    st_idx += 1

                meta = {
                    "name": name,
                    "description": description,
                    "expected_score_min": score_min,
                    "expected_score_max": score_max,
                }
                if subtask_scores:
                    meta["subtask_expected_scores"] = subtask_scores

                model_solutions_metadata.append(meta)

            idx += 1

        try:
            with _extract_pending_zip(import_token) as task_path:
                # Inject templates if provided
                _inject_templates_into_yaml(
                    task_path, input_template, output_template)

                # Inject model solutions metadata into task.yaml
                _inject_model_solutions_metadata(
                    task_path, model_solutions_metadata)

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
                        _delete_pending_import(import_token)
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
        finally:
            # Clean up the pending import
            _delete_pending_import(import_token)


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
