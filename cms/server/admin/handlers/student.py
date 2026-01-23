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

from sqlalchemy import func

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
    calculate_task_archive_progress,
    parse_tags,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


class TrainingProgramStudentsHandler(BaseHandler):
    """List and manage students in a training program."""
    REMOVE_FROM_PROGRAM = "Remove from training program"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.render_params_for_training_program(training_program)

        assigned_user_ids_q= self.sql_session.query(Participation.user_id).filter(
            Participation.contest == managing_contest
        )

        self.r_params["unassigned_users"] = (
            self.sql_session.query(User)
            .filter(~User.id.in_(assigned_user_ids_q))
            .filter(~User.username.like(r"\_\_%", escape="\\"))
            .all()
        )

        # Calculate task archive progress for each student using shared utility
        student_progress = {}
        for student in training_program.students:
            student_progress[student.id] = calculate_task_archive_progress(
                student, student.participation, managing_contest
            )

        self.r_params["student_progress"] = student_progress

        self.render("training_program_students.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "students")

        self.safe_get_item(TrainingProgram, training_program_id)

        try:
            user_id = self.get_argument("user_id")
            operation = self.get_argument("operation")
            assert operation in (
                self.REMOVE_FROM_PROGRAM,
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if operation == self.REMOVE_FROM_PROGRAM:
            asking_page = \
                self.url("training_program", training_program_id, "student", user_id, "remove")
            self.redirect(asking_page)
            return

        self.redirect(fallback_page)


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

        submission_query = self.sql_session.query(Submission)\
            .filter(Submission.participation == participation)
        self.render_params_for_remove_confirmation(submission_query)

        # Use the helper to set up training program params
        self.render_params_for_training_program(training_program)
        self.r_params["unanswered"] = 0  # Override for deletion confirmation page
        self.r_params["user"] = user

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


class StudentHandler(BaseHandler):
    """Shows and edits details of a single student in a training program.

    Similar to ParticipationHandler but includes student tags.
    """

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, user_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        self.contest = managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            raise tornado.web.HTTPError(404)

        submission_query = self.sql_session.query(Submission).filter(
            Submission.participation == participation
        )
        page = int(self.get_query_argument("page", "0"))
        self.render_params_for_submissions(submission_query, page)

        # render_params_for_training_program sets training_program, contest, unanswered
        self.render_params_for_training_program(training_program)
        self.r_params["participation"] = participation
        self.r_params["student"] = student
        self.r_params["selected_user"] = participation.user
        self.r_params["teams"] = self.sql_session.query(Team).all()
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)
        self.render("student.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "edit"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        self.contest = managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            student = Student(
                training_program=training_program,
                participation=participation,
                student_tags=[],
            )
            self.sql_session.add(student)

        try:
            attrs = participation.get_attrs()
            self.get_password(attrs, participation.password, True)
            self.get_ip_networks(attrs, "ip")
            self.get_datetime(attrs, "starting_time")
            self.get_timedelta_sec(attrs, "delay_time")
            self.get_timedelta_sec(attrs, "extra_time")
            self.get_bool(attrs, "hidden")
            self.get_bool(attrs, "unrestricted")

            # Get the new hidden status before applying
            new_hidden = attrs.get("hidden", False)

            participation.set_attrs(attrs)

            # Check if admin wants to apply hidden status to existing training days
            apply_to_existing = self.get_argument("apply_hidden_to_existing", None) is not None

            if apply_to_existing:
                # Update hidden status in all existing training day participations
                user = participation.user
                for training_day in training_program.training_days:
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
                participation.team = team
            else:
                participation.team = None

            tags_str = self.get_argument("student_tags", "")
            student.student_tags = parse_tags(tags_str)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class StudentTagsHandler(BaseHandler):
    """Handler for updating student tags via AJAX."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        # Set JSON content type for all responses
        self.set_header("Content-Type", "application/json")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            self.set_status(404)
            self.write({"error": "Participation not found"})
            return

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            student = Student(
                training_program=training_program,
                participation=participation,
                student_tags=[]
            )
            self.sql_session.add(student)

        try:
            tags_str = self.get_argument("student_tags", "")
            student.student_tags = parse_tags(tags_str)

            if self.try_commit():
                self.write({"success": True, "tags": student.student_tags})
            else:
                self.set_status(500)
                self.write({"error": "Failed to save"})

        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})


class StudentTasksHandler(BaseHandler):
    """View and manage tasks assigned to a student in a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, user_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            raise tornado.web.HTTPError(404)

        # Get all tasks in the training program for the "add task" dropdown
        all_tasks = managing_contest.get_tasks()
        assigned_task_ids = {st.task_id for st in student.student_tasks}
        available_tasks = [t for t in all_tasks if t.id not in assigned_task_ids]

        # Build home scores from participation task_scores cache
        home_scores = {}
        for pts in participation.task_scores:
            home_scores[pts.task_id] = pts.score

        # Build training scores from archived student rankings (batch query)
        training_scores = {}
        source_training_day_ids = {
            st.source_training_day_id
            for st in student.student_tasks
            if st.source_training_day_id is not None
        }
        archived_rankings = {}
        if source_training_day_ids:
            archived_rankings = {
                r.training_day_id: r
                for r in (
                    self.sql_session.query(ArchivedStudentRanking)
                    .filter(ArchivedStudentRanking.training_day_id.in_(source_training_day_ids))
                    .filter(ArchivedStudentRanking.student_id == student.id)
                    .all()
                )
            }

        for st in student.student_tasks:
            if st.source_training_day_id is None:
                continue
            archived_ranking = archived_rankings.get(st.source_training_day_id)
            if archived_ranking and archived_ranking.task_scores:
                task_id_str = str(st.task_id)
                if task_id_str in archived_ranking.task_scores:
                    training_scores[st.task_id] = archived_ranking.task_scores[task_id_str]

        # Get submission counts for each task (batch query for efficiency)
        submission_counts = {}
        if assigned_task_ids:
            counts = (
                self.sql_session.query(
                    Submission.task_id,
                    func.count(Submission.id)
                )
                .filter(Submission.participation_id == participation.id)
                .filter(Submission.task_id.in_(assigned_task_ids))
                .group_by(Submission.task_id)
                .all()
            )
            submission_counts = {task_id: count for task_id, count in counts}

        self.render_params_for_training_program(training_program)
        self.r_params["participation"] = participation
        self.r_params["student"] = student
        self.r_params["selected_user"] = participation.user
        self.r_params["student_tasks"] = sorted(
            student.student_tasks, key=lambda st: st.assigned_at, reverse=True
        )
        self.r_params["available_tasks"] = available_tasks
        self.r_params["home_scores"] = home_scores
        self.r_params["training_scores"] = training_scores
        self.r_params["submission_counts"] = submission_counts
        self.render("student_tasks.html", **self.r_params)


class AddStudentTaskHandler(BaseHandler):
    """Add a task to a student's task archive."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "tasks"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            raise tornado.web.HTTPError(404)

        try:
            task_id = self.get_argument("task_id")
            if task_id in ("", "null"):
                raise ValueError("Please select a task")

            task = self.safe_get_item(Task, task_id)

            # Validate task belongs to the student's training program
            if task.contest_id != training_program.managing_contest_id:
                raise ValueError("Task does not belong to the student's contest")

            # Check if task is already assigned
            existing = (
                self.sql_session.query(StudentTask)
                .filter(StudentTask.student_id == student.id)
                .filter(StudentTask.task_id == task.id)
                .first()
            )
            if existing is not None:
                raise ValueError("Task is already assigned to this student")

            # Create the StudentTask record (manual assignment, no training day)
            # Note: CMS Base.__init__ skips foreign key columns, so we must
            # set them as attributes after creating the object
            student_task = StudentTask(assigned_at=make_datetime())
            student_task.student_id = student.id
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
                f"Task '{task.name}' has been assigned to {participation.user.username}"
            )

        self.redirect(fallback_page)


class RemoveStudentTaskHandler(BaseHandler):
    """Remove a task from a student's task archive."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, user_id: str, task_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "student", user_id, "tasks"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        participation: Participation | None = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == managing_contest.id)
            .filter(Participation.user_id == user_id)
            .first()
        )

        if participation is None:
            raise tornado.web.HTTPError(404)

        student: Student | None = (
            self.sql_session.query(Student)
            .filter(Student.participation == participation)
            .filter(Student.training_program == training_program)
            .first()
        )

        if student is None:
            raise tornado.web.HTTPError(404)

        student_task: StudentTask | None = (
            self.sql_session.query(StudentTask)
            .filter(StudentTask.student_id == student.id)
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
                f"Task '{task.name}' has been removed from {participation.user.username}'s archive"
            )

        self.redirect(fallback_page)


class BulkAssignTaskHandler(BaseHandler):
    """Bulk assign a task to all students with a given tag."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        # Get all tasks in the training program
        all_tasks = managing_contest.get_tasks()

        # Get all unique student tags
        all_student_tags = get_all_student_tags(training_program)

        self.render_params_for_training_program(training_program)
        self.r_params["all_tasks"] = all_tasks
        self.r_params["all_student_tags"] = all_student_tags
        self.render("bulk_assign_task.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "bulk_assign_task"
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
