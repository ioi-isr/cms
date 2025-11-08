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
from datetime import datetime

from cms.db import Contest, Participation, Submission, Task
from cms.grading.languagemanager import safe_get_lang_filename
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class DownloadTaskSubmissionsHandler(BaseHandler):
    """Download all submissions for a specific task as a zip file.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, task_id):
        task = self.safe_get_item(Task, task_id)
        self.contest = task.contest

        submissions = self.sql_session.query(Submission)\
            .filter(Submission.task_id == task_id)\
            .order_by(Submission.timestamp).all()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            user_submissions = {}
            for submission in submissions:
                username = submission.participation.user.username
                if username not in user_submissions:
                    user_submissions[username] = {'official': [], 'unofficial': []}
                
                if submission.official:
                    user_submissions[username]['official'].append(submission)
                else:
                    user_submissions[username]['unofficial'].append(submission)

            for username, subs in user_submissions.items():
                for official_type, submission_list in subs.items():
                    for submission in submission_list:
                        result = submission.get_result(task.active_dataset)
                        if result is None:
                            status = "compiling"
                        elif result.compilation_failed():
                            status = "CompilationFailed"
                        elif not result.evaluated():
                            status = "evaluating"
                        elif not result.scored():
                            status = "Scoring"
                        else:
                            score = result.score if result.score is not None else 0
                            status = f"{score:.0f}"

                        timestamp = submission.timestamp.strftime("%Y%m%d_%H%M%S")

                        for filename, file_obj in submission.files.items():
                            real_filename = safe_get_lang_filename(
                                submission.language, filename)
                            
                            file_path = f"{username}/{official_type}/{timestamp}_{status}_{real_filename}"
                            
                            try:
                                file_content = self.service.file_cacher.get_file_content(
                                    file_obj.digest)
                                zip_file.writestr(file_path, file_content)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to retrieve file {filename} for submission {submission.id}: {e}")

        zip_buffer.seek(0)
        
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
            self.write("Participation not found")
            return

        submissions = self.sql_session.query(Submission)\
            .filter(Submission.participation_id == participation.id)\
            .order_by(Submission.timestamp).all()

        username = participation.user.username
        contest_name = self.contest.name

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            task_submissions = {}
            for submission in submissions:
                task_name = submission.task.name
                if task_name not in task_submissions:
                    task_submissions[task_name] = {'official': [], 'unofficial': []}
                
                if submission.official:
                    task_submissions[task_name]['official'].append(submission)
                else:
                    task_submissions[task_name]['unofficial'].append(submission)

            for task_name, subs in task_submissions.items():
                for official_type, submission_list in subs.items():
                    for submission in submission_list:
                        result = submission.get_result(submission.task.active_dataset)
                        if result is None:
                            status = "compiling"
                        elif result.compilation_failed():
                            status = "CompilationFailed"
                        elif not result.evaluated():
                            status = "evaluating"
                        elif not result.scored():
                            status = "Scoring"
                        else:
                            score = result.score if result.score is not None else 0
                            status = f"{score:.0f}"

                        timestamp = submission.timestamp.strftime("%Y%m%d_%H%M%S")

                        for filename, file_obj in submission.files.items():
                            real_filename = safe_get_lang_filename(
                                submission.language, filename)
                            
                            file_path = f"{task_name}/{official_type}/{timestamp}_{status}_{real_filename}"
                            
                            try:
                                file_content = self.service.file_cacher.get_file_content(
                                    file_obj.digest)
                                zip_file.writestr(file_path, file_content)
                            except Exception as e:
                                logger.warning(
                                    f"Failed to retrieve file {filename} for submission {submission.id}: {e}")

        zip_buffer.seek(0)
        
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
            .order_by(Submission.timestamp).all()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            user_task_submissions = {}
            for submission in submissions:
                username = submission.participation.user.username
                task_name = submission.task.name
                
                if username not in user_task_submissions:
                    user_task_submissions[username] = {}
                if task_name not in user_task_submissions[username]:
                    user_task_submissions[username][task_name] = {'official': [], 'unofficial': []}
                
                if submission.official:
                    user_task_submissions[username][task_name]['official'].append(submission)
                else:
                    user_task_submissions[username][task_name]['unofficial'].append(submission)

            for username, tasks in user_task_submissions.items():
                for task_name, subs in tasks.items():
                    for official_type, submission_list in subs.items():
                        for submission in submission_list:
                            result = submission.get_result(submission.task.active_dataset)
                            if result is None:
                                status = "compiling"
                            elif result.compilation_failed():
                                status = "CompilationFailed"
                            elif not result.evaluated():
                                status = "evaluating"
                            elif not result.scored():
                                status = "Scoring"
                            else:
                                score = result.score if result.score is not None else 0
                                status = f"{score:.0f}"

                            timestamp = submission.timestamp.strftime("%Y%m%d_%H%M%S")

                            for filename, file_obj in submission.files.items():
                                real_filename = safe_get_lang_filename(
                                    submission.language, filename)
                                
                                file_path = f"{username}/{task_name}/{official_type}/{timestamp}_{status}_{real_filename}"
                                
                                try:
                                    file_content = self.service.file_cacher.get_file_content(
                                        file_obj.digest)
                                    zip_file.writestr(file_path, file_content)
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to retrieve file {filename} for submission {submission.id}: {e}")

        zip_buffer.seek(0)
        
        self.set_header("Content-Type", "application/zip")
        self.set_header("Content-Disposition",
                        f'attachment; filename="{self.contest.name}_all_submissions.zip"')
        self.write(zip_buffer.getvalue())
        self.finish()
