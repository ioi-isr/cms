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

"""Admin handlers for Students in Training Programs.

Students are users enrolled in a training program with additional metadata
like student tags and task assignments.
"""

import tornado.web

from cms.db import (
    TrainingProgram,
    Participation,
    Submission,
    User,
    Task,
    Question,
    Student,
    StudentTask,
    Team,
    ArchivedStudentRanking,
)
from cms.server.util import (
    get_all_student_tags,
    get_student_archive_scores,
    get_submission_counts_by_task,
    parse_tags,
    parse_usernames_from_file,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, StudentBaseHandler, require_permission


class TrainingProgramStudentsHandler(BaseHandler):
    """List and manage students in a training program."""
    REMOVE_FROM_PROGRAM = "Remove from training program"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        self.render_params_for_training_program(training_program)
        self.render_params_for_students_page(training_program)
        self.r_params["bulk_add_results"] = None

        self.render("training_program_students.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "students")

        self.safe_get_item(TrainingProgram, training_program_id)

        try:
            operation = self.get_argument("operation")
            # Support both old format (radio button + "Remove from training program")
            # and new format (button with value "remove_<user_id>")
            if operation == self.REMOVE_FROM_PROGRAM:
                user_id = self.get_argument("user_id")
            elif operation.startswith("remove_"):
                user_id = operation.replace("remove_", "")
            else:
                raise ValueError("Please select a valid operation")
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        # Redirect to confirmation page
        asking_page = \
            self.url("training_program", training_program_id, "student", user_id, "remove")
        self.redirect(asking_page)


class AddTrainingProgramStudentHandler(BaseHandler):
    """Add a student to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "students")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            user_id: str = self.get_argument("user_id")
            assert user_id != "", "Please select a valid user"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        user = self.safe_get_item(User, user_id)

        # Set starting_time to now so the student can see everything immediately
        # (training programs don't have a start button)
        participation = Participation(
            contest=managing_contest,
            user=user,
            starting_time=make_datetime()
        )
        self.sql_session.add(participation)
        self.sql_session.flush()

        student = Student(
            training_program=training_program,
            participation=participation,
            student_tags=[]
        )
        self.sql_session.add(student)

        # Also add the student to all existing training days
        for training_day in training_program.training_days:
            # Skip training days that don't have a contest yet
            if training_day.contest is None:
                continue
            td_participation = Participation(
                contest=training_day.contest,
                user=user
            )
            self.sql_session.add(td_participation)

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class BulkAddTrainingProgramStudentsHandler(BaseHandler):
    """Bulk add students to a training program from a file."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            if "students_file" not in self.request.files:
                raise ValueError("No file uploaded")

            file_data = self.request.files["students_file"][0]
            file_content = file_data["body"].decode("utf-8")

            usernames = parse_usernames_from_file(file_content)

            if not usernames:
                raise ValueError("File is empty or contains no usernames")

            results = []
            students_added = 0

            for username in usernames:
                user = self.sql_session.query(User).filter(
                    User.username == username).first()

                if user is None:
                    results.append({
                        "username": username,
                        "status": "not_found",
                        "message": "Username does not exist in the system"
                    })
                else:
                    existing_participation = (
                        self.sql_session.query(Participation)
                        .filter(Participation.contest == managing_contest)
                        .filter(Participation.user == user)
                        .first()
                    )

                    if existing_participation is not None:
                        results.append({
                            "username": username,
                            "status": "already_exists",
                            "message": "User is already a student in this program"
                        })
                    else:
                        participation = Participation(
                            contest=managing_contest,
                            user=user,
                            starting_time=make_datetime()
                        )
                        self.sql_session.add(participation)
                        self.sql_session.flush()

                        student = Student(
                            training_program=training_program,
                            participation=participation,
                            student_tags=[]
                        )
                        self.sql_session.add(student)

                        for training_day in training_program.training_days:
                            if training_day.contest is None:
                                continue
                            td_participation = Participation(
                                contest=training_day.contest,
                                user=user
                            )
                            self.sql_session.add(td_participation)

                        results.append({
                            "username": username,
                            "status": "success",
                            "message": "Successfully added to training program"
                        })
                        students_added += 1

            if self.try_commit():
                if students_added > 0:
                    self.service.proxy_service.reinitialize()
            else:
                # Commit failed - redirect to avoid showing misleading results
                self.redirect(
                    self.url("training_program", training_program_id, "students")
                )
                return

            self.render_params_for_training_program(training_program)
            self.render_params_for_students_page(training_program)
            self.r_params["bulk_add_results"] = results
            self.r_params["students_added"] = students_added
            self.render("training_program_students.html", **self.r_params)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Error processing file", repr(error))
            self.redirect(self.url("training_program", training_program_id, "students"))


class RemoveTrainingProgramStudentHandler(BaseHandler):
    """Confirm and remove a student from a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, user_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        user = self.safe_get_item(User, user_id)

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest == managing_contest)
            .filter(Participation.user == user)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        # Use the helper to set up training program params first
        # (this initializes r_params, so it must come before render_params_for_remove_confirmation)
        self.render_params_for_training_program(training_program)
        self.r_params["unanswered"] = 0  # Override for deletion confirmation page
        self.r_params["user"] = user

        # Now add submission count (this adds to existing r_params)
        submission_query = self.sql_session.query(Submission)\
            .filter(Submission.participation == participation)
        self.render_params_for_remove_confirmation(submission_query)

        # Count submissions and participations from training days
        training_day_contest_ids = [td.contest_id for td in training_program.training_days]
        training_day_contest_ids = [
            cid for cid in training_day_contest_ids if cid is not None
        ]

        if training_day_contest_ids:
            training_day_participations = (
                self.sql_session.query(Participation)
                .filter(Participation.contest_id.in_(training_day_contest_ids))
                .filter(Participation.user == user)
                .count()
            )
            training_day_submissions = (
                self.sql_session.query(Submission)
                .join(Participation)
                .filter(Participation.contest_id.in_(training_day_contest_ids))
                .filter(Participation.user == user)
                .count()
            )
        else:
            training_day_participations = 0
            training_day_submissions = 0

        self.r_params["training_day_submissions"] = training_day_submissions
        self.r_params["training_day_participations"] = training_day_participations
        self.render("training_program_student_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str, user_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        user = self.safe_get_item(User, user_id)

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.user == user)
            .filter(Participation.contest == managing_contest)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        # Delete the Student record first (it has a NOT NULL FK to participation)
        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .first()
        )
        if student is not None:
            self.sql_session.delete(student)

        self.sql_session.delete(participation)

        # Also delete participations from all training days
        for training_day in training_program.training_days:
            # Skip training days that don't have a contest yet
            if training_day.contest is None:
                continue
            td_participation: Participation | None = (
                self.sql_session.query(Participation)
                .filter(Participation.contest == training_day.contest)
                .filter(Participation.user == user)
                .first()
            )
            if td_participation is not None:
                self.sql_session.delete(td_participation)

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.write("../../students")


class StudentHandler(StudentBaseHandler):
    """Shows and edits details of a single student in a training program.

    Similar to ParticipationHandler but includes student tags.
    """

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, user_id: str):
        self.setup_student_context(training_program_id, user_id)

        submission_query = self.sql_session.query(Submission).filter(
            Submission.participation == self.participation
        )
        page = int(self.get_query_argument("page", "0"))

        # render_params_for_training_program sets training_program, contest, unanswered
        self.render_params_for_training_program(self.training_program)

        self.render_params_for_submissions(submission_query, page)

        self.r_params["participation"] = self.participation
        self.r_params["student"] = self.student
        self.r_params["selected_user"] = self.participation.user
        self.r_params["teams"] = self.sql_session.query(Team).all()
        self.r_params["all_student_tags"] = get_all_student_tags(self.training_program)
        self.render("student.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "edit"
        )

        self.setup_student_context(training_program_id, user_id)

        try:
            attrs = self.participation.get_attrs()
            self.get_password(attrs, self.participation.password, True)
            self.get_ip_networks(attrs, "ip")
            self.get_datetime(attrs, "starting_time")
            self.get_timedelta_sec(attrs, "delay_time")
            self.get_timedelta_sec(attrs, "extra_time")
            self.get_bool(attrs, "hidden")
            self.get_bool(attrs, "unrestricted")

            # Get the new hidden status before applying
            new_hidden = attrs.get("hidden", False)

            self.participation.set_attrs(attrs)

            # Check if admin wants to apply hidden status to existing training days
            apply_to_existing = self.get_argument("apply_hidden_to_existing", None) is not None

            if apply_to_existing:
                # Update hidden status in all existing training day participations
                user = self.participation.user
                for training_day in self.training_program.training_days:
                    if training_day.contest is None:
                        continue
                    td_participation = self.sql_session.query(Participation)\
                        .filter(Participation.contest_id == training_day.contest_id)\
                        .filter(Participation.user_id == user.id)\
                        .first()
                    if td_participation:
                        td_participation.hidden = new_hidden

            self.get_string(attrs, "team")
            team_code = attrs["team"]
            if team_code:
                team: Team | None = (
                    self.sql_session.query(Team).filter(Team.code == team_code).first()
                )
                if team is None:
                    raise ValueError(f"Team with code '{team_code}' does not exist")
                self.participation.team = team
            else:
                self.participation.team = None

            tags_str = self.get_argument("student_tags", "")
            self.student.student_tags = parse_tags(tags_str)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class StudentTagsHandler(StudentBaseHandler):
    """Handler for updating student tags via AJAX."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        # Set JSON content type for all responses
        self.set_header("Content-Type", "application/json")

        try:
            self.setup_student_context(training_program_id, user_id)
        except tornado.web.HTTPError:
            self.set_status(404)
            self.write({"error": "Student not found"})
            return

        try:
            tags_str = self.get_argument("student_tags", "")
            self.student.student_tags = parse_tags(tags_str)

            if self.try_commit():
                self.write({"success": True, "tags": self.student.student_tags})
            else:
                self.set_status(500)
                self.write({"error": "Failed to save"})

        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})


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
