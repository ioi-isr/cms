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

"""Submission download handlers for AWS.

"""

import io
import logging
import zipfile

import tornado.web

from cms.db import Contest, Participation, Submission, Task
from cms.grading.languagemanager import safe_get_lang_filename
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


def get_submission_status(submission, dataset):
    """Get the status string for a submission.
    
    submission: the Submission object
    dataset: the Dataset to evaluate against
    
    return: status string (e.g., "compiling", "95.0pts", "compilationFailed")
    
    """
    result = submission.get_result(dataset)
    if result is None:
        return "compiling"
    elif result.compilation_failed():
        return "compilationFailed"
    elif not result.evaluated():
        return "evaluating"
    elif not result.scored():
        return "scoring"
    else:
        score = result.score if result.score is not None else 0.0
        task = submission.task
        precision = task.score_precision if task.score_precision is not None else 0
        return f"{score:.{precision}f}pts"


def write_submission_files(zip_file, submission, base_path_parts, file_cacher):
    """Write all files from a submission to the zip file.
    
    zip_file: ZipFile object to write to
    submission: the Submission object
    base_path_parts: list of path components (e.g., ["username", "taskname"])
    file_cacher: FileCacher instance to retrieve file content
    
    """
    dataset = submission.task.active_dataset
    status = get_submission_status(submission, dataset)
    timestamp = submission.timestamp.strftime("%Y%m%d_%H%M%S")
    official_folder = "official" if submission.official else "unofficial"
    
    path_parts = base_path_parts + [official_folder]
    
    for filename, file_obj in submission.files.items():
        real_filename = safe_get_lang_filename(submission.language, filename)
        prefixed_filename = f"{timestamp}_{status}_{real_filename}"
        file_path = "/".join(path_parts + [prefixed_filename])
        
        try:
            file_content = file_cacher.get_file_content(file_obj.digest)
            zip_file.writestr(file_path, file_content)
        except Exception as e:
            logger.warning(
                f"Failed to retrieve file {filename} for submission {submission.id}: {e}")


def build_zip(submissions, base_path_builder, file_cacher):
    """Build a zip file containing all submissions.
    
    submissions: list of Submission objects
    base_path_builder: function that takes a submission and returns list of path parts
    file_cacher: FileCacher instance to retrieve file content
    
    return: BytesIO object containing the zip file
    
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for submission in sorted(submissions, key=lambda s: s.timestamp):
            base_path_parts = base_path_builder(submission)
            write_submission_files(zip_file, submission, base_path_parts, file_cacher)
    
    return zip_buffer


class DownloadTaskSubmissionsHandler(BaseHandler):
    """Download all submissions for a specific task as a zip file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)
        self.contest = task.contest

        submissions = self.sql_session.query(Submission)\
            .filter(Submission.task_id == task_id)\
            .all()

        def base_path_builder(submission):
            return [submission.participation.user.username]

        zip_buffer = build_zip(submissions, base_path_builder, self.service.file_cacher)
        
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition",
                        f'attachment; filename="{task.name}_submissions.zip"')
        self.write(zip_buffer.getvalue())
        self.finish()


class DownloadUserContestSubmissionsHandler(BaseHandler):
    """Download all submissions for a specific user in a contest as a zip file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, user_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        participation = self.sql_session.query(Participation)\
            .filter(Participation.contest_id == contest_id)\
            .filter(Participation.user_id == user_id)\
            .first()

        if participation is None:
            raise tornado.web.HTTPError(404)

        submissions = self.sql_session.query(Submission)\
            .filter(Submission.participation_id == participation.id)\
            .all()

        username = participation.user.username
        contest_name = self.contest.name

        def base_path_builder(submission):
            return [submission.task.name]

        zip_buffer = build_zip(submissions, base_path_builder, self.service.file_cacher)
        
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition",
                        f'attachment; filename="{username}_{contest_name}_submissions.zip"')
        self.write(zip_buffer.getvalue())
        self.finish()


class DownloadContestSubmissionsHandler(BaseHandler):
    """Download all submissions for a contest as a zip file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        submissions = self.sql_session.query(Submission)\
            .join(Task)\
            .filter(Task.contest_id == contest_id)\
            .all()

        def base_path_builder(submission):
            return [submission.participation.user.username, submission.task.name]

        zip_buffer = build_zip(submissions, base_path_builder, self.service.file_cacher)
        
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition",
                        f'attachment; filename="{self.contest.name}_all_submissions.zip"')
        self.write(zip_buffer.getvalue())
        self.finish()
