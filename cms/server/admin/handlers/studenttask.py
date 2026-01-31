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

"""Admin handlers for Student Task management.

This module contains handlers for managing task assignments to students
in training programs, including viewing, adding, and removing tasks
from student archives.

Handlers:
- StudentTasksHandler: View and manage tasks assigned to a student
- StudentTaskSubmissionsHandler: View submissions for a specific task
- AddStudentTaskHandler: Add a task to a student's archive
- RemoveStudentTaskHandler: Remove a task from a student's archive
- BulkAssignTaskHandler: Bulk assign a task to students with a tag
"""

import tornado.web

from cms.db import (
    TrainingProgram,
    Submission,
    Task,
    Student,
    StudentTask,
    ArchivedStudentRanking,
)
from cms.server.util import get_student_archive_scores, get_submission_counts_by_task
from cmscommon.datetime import make_datetime

from .base import BaseHandler, StudentBaseHandler, require_permission


class StudentTasksHandler(StudentBaseHandler):
    """View and manage tasks assigned to a student in a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, user_id: str):
        self.setup_student_context(training_program_id, user_id)

        # Get all tasks in the training program for the "add task" dropdown
        all_tasks = self.managing_contest.get_tasks()
        assigned_task_ids = {st.task_id for st in self.student.student_tasks}
        available_tasks = [t for t in all_tasks if t.id not in assigned_task_ids]

        # Build home scores using get_student_archive_scores for fresh cache values
        # This avoids stale entries in participation.task_scores
        home_scores = get_student_archive_scores(
            self.sql_session, self.student, self.participation, self.managing_contest
        )
        # Commit to release advisory locks from cache rebuilds
        self.sql_session.commit()

        # Build training scores from archived student rankings (batch query)
        training_scores = {}
        source_training_day_ids = {
            st.source_training_day_id
            for st in self.student.student_tasks
            if st.source_training_day_id is not None
        }
        archived_rankings = {}
        if source_training_day_ids:
            archived_rankings = {
                r.training_day_id: r
                for r in (
                    self.sql_session.query(ArchivedStudentRanking)
                    .filter(ArchivedStudentRanking.training_day_id.in_(source_training_day_ids))
                    .filter(ArchivedStudentRanking.student_id == self.student.id)
                    .all()
                )
            }

        for st in self.student.student_tasks:
            if st.source_training_day_id is None:
                continue
            archived_ranking = archived_rankings.get(st.source_training_day_id)
            if archived_ranking and archived_ranking.task_scores:
                task_id_str = str(st.task_id)
                if task_id_str in archived_ranking.task_scores:
                    training_scores[st.task_id] = archived_ranking.task_scores[task_id_str]

        # Get submission counts for each task (batch query for efficiency)
        submission_counts = get_submission_counts_by_task(
            self.sql_session, self.participation.id, assigned_task_ids
        )

        self.render_params_for_training_program(self.training_program)
        self.r_params["participation"] = self.participation
        self.r_params["student"] = self.student
        self.r_params["selected_user"] = self.participation.user
        self.r_params["student_tasks"] = sorted(
            self.student.student_tasks, key=lambda st: st.assigned_at, reverse=True
        )
        self.r_params["available_tasks"] = available_tasks
        self.r_params["home_scores"] = home_scores
        self.r_params["training_scores"] = training_scores
        self.r_params["submission_counts"] = submission_counts
        self.render("student_tasks.html", **self.r_params)


class StudentTaskSubmissionsHandler(StudentBaseHandler):
    """View submissions for a specific task in a student's archive."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, user_id: str, task_id: str):
        task = self.safe_get_item(Task, task_id)
        self.setup_student_context(training_program_id, user_id)

        # Validate task belongs to the training program
        if task.contest_id != self.managing_contest.id:
            raise tornado.web.HTTPError(404)

        # Verify student is assigned this specific task
        student_task = (
            self.sql_session.query(StudentTask)
            .filter(StudentTask.student == self.student)
            .filter(StudentTask.task == task)
            .first()
        )

        if student_task is None:
            raise tornado.web.HTTPError(404)

        # Filter submissions by task
        submission_query = (
            self.sql_session.query(Submission)
            .filter(Submission.participation == self.participation)
            .filter(Submission.task_id == task.id)
        )
        page = int(self.get_query_argument("page", "0"))

        self.render_params_for_training_program(self.training_program)
        self.render_params_for_submissions(submission_query, page)

        self.r_params["participation"] = self.participation
        self.r_params["student"] = self.student
        self.r_params["selected_user"] = self.participation.user
        self.r_params["task"] = task
        self.render("student_task_submissions.html", **self.r_params)


class AddStudentTaskHandler(StudentBaseHandler):
    """Add a task to a student's task archive."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "tasks"
        )

        self.setup_student_context(training_program_id, user_id)

        try:
            task_id = self.get_argument("task_id")
            if task_id in ("", "null"):
                raise ValueError("Please select a task")

            task = self.safe_get_item(Task, task_id)

            # Validate task belongs to the student's training program
            if task.contest_id != self.training_program.managing_contest_id:
                raise ValueError("Task does not belong to the student's contest")

            # Check if task is already assigned
            existing = (
                self.sql_session.query(StudentTask)
                .filter(StudentTask.student_id == self.student.id)
                .filter(StudentTask.task_id == task.id)
                .first()
            )
            if existing is not None:
                raise ValueError("Task is already assigned to this student")

            # Create the StudentTask record (manual assignment, no training day)
            # Note: CMS Base.__init__ skips foreign key columns, so we must
            # set them as attributes after creating the object
            student_task = StudentTask(assigned_at=make_datetime())
            student_task.student_id = self.student.id
            student_task.task_id = task.id
            student_task.source_training_day_id = None
            self.sql_session.add(student_task)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Task assigned",
                f"Task '{task.name}' has been assigned to {self.participation.user.username}"
            )

        self.redirect(fallback_page)


class RemoveStudentTaskHandler(StudentBaseHandler):
    """Remove a task from a student's task archive."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str, task_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "tasks"
        )

        self.setup_student_context(training_program_id, user_id)

        student_task: StudentTask | None = (
            self.sql_session.query(StudentTask)
            .filter(StudentTask.student_id == self.student.id)
            .filter(StudentTask.task_id == task_id)
            .first()
        )

        if student_task is None:
            raise tornado.web.HTTPError(404)

        task = student_task.task
        self.sql_session.delete(student_task)

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Task removed",
                f"Task '{task.name}' has been removed from {self.participation.user.username}'s archive"
            )

        self.redirect(fallback_page)


class BulkAssignTaskHandler(BaseHandler):
    """Bulk assign a task to all students with a given tag.

    Note: The GET method was removed as the bulk assign task functionality
    is now handled via a modal dialog on the students page.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        # Redirect to students page (modal is now on that page)
        fallback_page = self.url(
            "training_program", training_program_id, "students"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        try:
            task_id = self.get_argument("task_id")
            if task_id in ("", "null"):
                raise ValueError("Please select a task")

            tag_name = self.get_argument("tag", "").strip().lower()
            if not tag_name:
                raise ValueError("Please enter a tag")

            task = self.safe_get_item(Task, task_id)

            # Validate task belongs to the training program
            if task.contest_id != training_program.managing_contest_id:
                raise ValueError("Task does not belong to the student's contest")

            # Find all students with the given tag
            matching_students = (
                self.sql_session.query(Student)
                .filter(Student.training_program == training_program)
                .filter(Student.student_tags.any(tag_name))
                .all()
            )

            if not matching_students:
                raise ValueError(f"No students found with tag '{tag_name}'")

            # We want to know which of these specific students already have this task.
            student_ids = [s.id for s in matching_students]

            already_assigned_ids = set(
                row[0]
                for row in self.sql_session.query(StudentTask.student_id)
                .filter(StudentTask.task_id == task.id)
                .filter(StudentTask.student_id.in_(student_ids))
                .all()
            )

            # Assign task to each matching student (if not already assigned)
            assigned_count = 0
            for student_id in student_ids:
                if student_id not in already_assigned_ids:
                    # Note: CMS Base.__init__ skips foreign key columns, so we must
                    # set them as attributes after creating the object
                    student_task = StudentTask(assigned_at=make_datetime())
                    student_task.student_id = student_id
                    student_task.task_id = task.id
                    student_task.source_training_day_id = None
                    self.sql_session.add(student_task)
                    assigned_count += 1

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Bulk assignment complete",
                f"Task '{task.name}' assigned to {assigned_count} students with tag '{tag_name}'",
            )

        self.redirect(fallback_page)
