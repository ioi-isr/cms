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

"""Export handlers for AWS - allows exporting tasks and contests to
zip files in YamlLoader format.

"""

import logging
import os
import shutil
import tempfile
import zipfile

import yaml

from cms.db import Contest, Task
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def _export_task_to_yaml_format(task, dataset, file_cacher, export_dir):
    """Export a task to YamlLoader (Italian YAML) format.

    task: Task object to export
    dataset: Dataset object to export (typically active_dataset)
    file_cacher: FileCacher instance for retrieving files
    export_dir: Directory to export to

    Creates the following structure:
    - task.yaml: Task configuration
    - statements/: Statement PDFs
    - attachments/: Task attachments
    - tests.zip: Testcases (input/output pairs)
    - managers/: Manager files (checker, grader, etc.)
    """

    statements_dir = os.path.join(export_dir, "statements")
    attachments_dir = os.path.join(export_dir, "attachments")
    managers_dir = os.path.join(export_dir, "managers")

    os.makedirs(statements_dir, exist_ok=True)
    os.makedirs(attachments_dir, exist_ok=True)
    os.makedirs(managers_dir, exist_ok=True)

    for lang_code, statement in task.statements.items():
        statement_path = os.path.join(statements_dir, f"{lang_code}.pdf")
        file_cacher.get_file_to_path(statement.digest, statement_path)

    for filename, attachment in task.attachments.items():
        attachment_path = os.path.join(attachments_dir, filename)
        file_cacher.get_file_to_path(attachment.digest, attachment_path)

    input_template = "input_*.txt"
    output_template = "output_*.txt"
    input_template_py = input_template.replace("*", "%s")
    output_template_py = output_template.replace("*", "%s")

    tests_zip_path = os.path.join(export_dir, "tests.zip")
    testcases = sorted(dataset.testcases.values(), key=lambda tc: tc.codename)
    with zipfile.ZipFile(tests_zip_path, 'w', zipfile.ZIP_DEFLATED) as tests_zip:
        for testcase in testcases:
            with tests_zip.open(input_template_py % testcase.codename, 'w') as fout:
                file_cacher.get_file_to_fobj(testcase.input, fout)
            with tests_zip.open(output_template_py % testcase.codename, 'w') as fout:
                file_cacher.get_file_to_fobj(testcase.output, fout)

    for filename, manager in dataset.managers.items():
        manager_path = os.path.join(managers_dir, filename)
        file_cacher.get_file_to_path(manager.digest, manager_path)

    task_config = {
        'name': task.name,
        'title': task.title,
        'primary_language': task.primary_statements[0] if task.primary_statements else 'he',
    }

    if dataset.description:
        task_config['version'] = dataset.description

    if task.submission_format:
        task_config['submission_format'] = task.submission_format

    if task.feedback_level:
        task_config['feedback_level'] = task.feedback_level

    if task.score_mode:
        task_config['score_mode'] = task.score_mode

    if task.token_mode:
        task_config['token_mode'] = task.token_mode
        if task.token_max_number is not None:
            task_config['token_max_number'] = task.token_max_number
        if task.token_min_interval is not None:
            task_config['token_min_interval'] = int(task.token_min_interval.total_seconds())
        if task.token_gen_initial is not None:
            task_config['token_gen_initial'] = task.token_gen_initial
        if task.token_gen_number is not None:
            task_config['token_gen_number'] = task.token_gen_number
        if task.token_gen_interval is not None:
            task_config['token_gen_interval'] = int(task.token_gen_interval.total_seconds())
        if task.token_gen_max is not None:
            task_config['token_gen_max'] = task.token_gen_max

    if task.max_submission_number is not None:
        task_config['max_submission_number'] = task.max_submission_number
    if task.max_user_test_number is not None:
        task_config['max_user_test_number'] = task.max_user_test_number
    if task.min_submission_interval is not None:
        task_config['min_submission_interval'] = int(task.min_submission_interval.total_seconds())
    if task.min_user_test_interval is not None:
        task_config['min_user_test_interval'] = int(task.min_user_test_interval.total_seconds())

    if task.score_precision is not None:
        task_config['score_precision'] = task.score_precision

    if dataset.time_limit is not None:
        task_config['time_limit'] = dataset.time_limit
    if dataset.memory_limit is not None:
        task_config['memory_limit'] = dataset.memory_limit // (1024 * 1024)

    if dataset.task_type:
        task_config['task_type'] = dataset.task_type
        if dataset.task_type_parameters:
            if dataset.task_type == "Batch" and len(dataset.task_type_parameters) >= 3:
                task_config['infile'] = dataset.task_type_parameters[1][0]
                task_config['outfile'] = dataset.task_type_parameters[1][1]
                if len(dataset.task_type_parameters) >= 4 and dataset.task_type_parameters[2] == "realprecision":
                    task_config['exponent'] = dataset.task_type_parameters[3]
            elif dataset.task_type == "OutputOnly":
                task_config['output_only'] = True

    if dataset.score_type:
        task_config['score_type'] = dataset.score_type
        if dataset.score_type_parameters is not None:
            task_config['score_type_parameters'] = dataset.score_type_parameters

    task_config['n_input'] = len(testcases)

    task_config['input_template'] = input_template
    task_config['output_template'] = output_template

    public_testcases = [tc.codename for tc in testcases if tc.public]
    if public_testcases:
        if len(public_testcases) == len(testcases):
            task_config['public_testcases'] = 'all'
        else:
            try:
                public_indices = [int(tc) for tc in public_testcases]
                task_config['public_testcases'] = ','.join(map(str, public_indices))
            except ValueError:
                task_config['public_testcases'] = ','.join(public_testcases)

    task_yaml_path = os.path.join(export_dir, "task.yaml")
    with open(task_yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(task_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _export_contest_to_yaml_format(contest, file_cacher, export_dir):
    """Export a contest to YamlLoader (Italian YAML) format.

    contest: Contest object to export
    file_cacher: FileCacher instance for retrieving files
    export_dir: Directory to export to

    Creates the following structure:
    - contest.yaml: Contest configuration
    - task1/: First task directory
    - task2/: Second task directory
    - ...
    """

    contest_config = {
        'name': contest.name,
        'description': contest.description,
    }

    if contest.allowed_localizations:
        contest_config['allowed_localizations'] = contest.allowed_localizations

    if contest.languages:
        contest_config['languages'] = contest.languages

    if contest.submissions_download_allowed is not None:
        contest_config['submissions_download_allowed'] = contest.submissions_download_allowed
    if contest.allow_questions is not None:
        contest_config['allow_questions'] = contest.allow_questions
    if contest.allow_user_tests is not None:
        contest_config['allow_user_tests'] = contest.allow_user_tests

    if contest.score_precision is not None:
        contest_config['score_precision'] = contest.score_precision

    if contest.block_hidden_participations is not None:
        contest_config['block_hidden_participations'] = contest.block_hidden_participations
    if contest.allow_password_authentication is not None:
        contest_config['allow_password_authentication'] = contest.allow_password_authentication
    if contest.allow_registration is not None:
        contest_config['allow_registration'] = contest.allow_registration
    if contest.ip_restriction is not None:
        contest_config['ip_restriction'] = contest.ip_restriction
    if contest.ip_autologin is not None:
        contest_config['ip_autologin'] = contest.ip_autologin

    if contest.token_mode:
        contest_config['token_mode'] = contest.token_mode
        if contest.token_max_number is not None:
            contest_config['token_max_number'] = contest.token_max_number
        if contest.token_min_interval is not None:
            contest_config['token_min_interval'] = int(contest.token_min_interval.total_seconds())
        if contest.token_gen_initial is not None:
            contest_config['token_gen_initial'] = contest.token_gen_initial
        if contest.token_gen_number is not None:
            contest_config['token_gen_number'] = contest.token_gen_number
        if contest.token_gen_interval is not None:
            contest_config['token_gen_interval'] = int(contest.token_gen_interval.total_seconds())
        if contest.token_gen_max is not None:
            contest_config['token_gen_max'] = contest.token_gen_max

    if contest.start is not None:
        contest_config['start'] = contest.start.timestamp()
    if contest.stop is not None:
        contest_config['stop'] = contest.stop.timestamp()
    if contest.timezone:
        contest_config['timezone'] = contest.timezone
    if contest.per_user_time is not None:
        contest_config['per_user_time'] = int(contest.per_user_time.total_seconds())

    if contest.max_submission_number is not None:
        contest_config['max_submission_number'] = contest.max_submission_number
    if contest.max_user_test_number is not None:
        contest_config['max_user_test_number'] = contest.max_user_test_number
    if contest.min_submission_interval is not None:
        contest_config['min_submission_interval'] = int(contest.min_submission_interval.total_seconds())
    if contest.min_user_test_interval is not None:
        contest_config['min_user_test_interval'] = int(contest.min_user_test_interval.total_seconds())

    if contest.analysis_enabled is not None:
        contest_config['analysis_enabled'] = contest.analysis_enabled
    if contest.analysis_start is not None:
        contest_config['analysis_start'] = contest.analysis_start.timestamp()
    if contest.analysis_stop is not None:
        contest_config['analysis_stop'] = contest.analysis_stop.timestamp()

    if contest.tasks:
        contest_config['tasks'] = [task.name for task in contest.tasks]

    contest_yaml_path = os.path.join(export_dir, "contest.yaml")
    with open(contest_yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(contest_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    for task in contest.tasks:
        task_dir = os.path.join(export_dir, task.name)
        os.makedirs(task_dir, exist_ok=True)

        dataset = task.active_dataset
        if dataset is None:
            logger.warning("Task %s has no active dataset, skipping", task.name)
            continue

        _export_task_to_yaml_format(task, dataset, file_cacher, task_dir)


class ExportTaskHandler(BaseHandler):
    """Handler for exporting a task to a zip file in YamlLoader format.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)

        if task.active_dataset is None:
            self.service.add_notification(
                make_datetime(),
                "Export failed",
                "Task has no active dataset to export.")
            self.redirect(self.url("task", task_id))
            return

        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="cms_export_task_")

            task_dir = os.path.join(temp_dir, task.name)
            os.makedirs(task_dir)

            _export_task_to_yaml_format(
                task,
                task.active_dataset,
                self.service.file_cacher,
                task_dir
            )

            zip_path = os.path.join(temp_dir, f"{task.name}.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(task_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)

            self.set_header('Content-Type', 'application/zip')
            self.set_header('Content-Disposition',
                          f'attachment; filename="{task.name}.zip"')

            with open(zip_path, 'rb') as f:
                self.write(f.read())

            self.finish()

        except Exception as error:
            logger.error("Task export failed: %s", error, exc_info=True)
            self.service.add_notification(
                make_datetime(),
                "Task export failed",
                str(error))
            self.redirect(self.url("task", task_id))

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


class ExportContestHandler(BaseHandler):
    """Handler for exporting a contest to a zip file in YamlLoader format.

    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, contest_id):
        contest = self.safe_get_item(Contest, contest_id)

        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="cms_export_contest_")

            contest_dir = os.path.join(temp_dir, contest.name)
            os.makedirs(contest_dir)

            _export_contest_to_yaml_format(
                contest,
                self.service.file_cacher,
                contest_dir
            )

            zip_path = os.path.join(temp_dir, f"{contest.name}.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(contest_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)

            self.set_header('Content-Type', 'application/zip')
            self.set_header('Content-Disposition',
                          f'attachment; filename="{contest.name}.zip"')

            with open(zip_path, 'rb') as f:
                self.write(f.read())

            self.finish()

        except Exception as error:
            logger.error("Contest export failed: %s", error, exc_info=True)
            self.service.add_notification(
                make_datetime(),
                "Contest export failed",
                str(error))
            self.redirect(self.url("contest", contest_id))

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
