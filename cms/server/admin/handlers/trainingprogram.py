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

"""Admin handlers for Training Programs.

Training programs organize year-long training with multiple sessions.
Each training program has a managing contest that handles all submissions.
"""

from datetime import datetime as dt, timedelta
from urllib.parse import urlencode

import tornado.web

from sqlalchemy import func

from cms.db import (
    Contest,
    TrainingProgram,
    Participation,
    Submission,
    User,
    Task,
    Question,
    Announcement,
    Student,
    StudentTask,
    Team,
    TrainingDay,
    TrainingDayGroup,
    ArchivedAttendance,
    ArchivedStudentRanking,
    ScoreHistory,
    DelayRequest,
)
from cms.db.training_day import get_managing_participation
from cms.server.util import (
    get_all_student_tags,
    get_all_student_tags_with_historical,
    get_all_training_day_types,
    calculate_task_archive_progress,
    can_access_task,
    check_training_day_eligibility,
    parse_tags,
)
from cmscommon.datetime import make_datetime, get_timezone, local_to_utc, get_timezone_name

from .base import BaseHandler, SimpleHandler, require_permission, parse_datetime_with_timezone


class TrainingProgramListHandler(SimpleHandler("training_programs.html")):
    """List all training programs.

    GET returns the list of all training programs.
    POST handles operations on a specific training program (e.g., removing).
    """
    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        self.r_params = self.render_params()
        self.r_params["training_programs"] = (
            self.sql_session.query(TrainingProgram)
            .order_by(TrainingProgram.name)
            .all()
        )
        self.render("training_programs.html", **self.r_params)

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        training_program_id: str = self.get_argument("training_program_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("training_programs", training_program_id, "remove")
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, ""
            )
            self.redirect(self.url("training_programs"))


class TrainingProgramHandler(BaseHandler):
    """View/edit a single training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.render("training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback = self.url("training_program", training_program_id)
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        contest = training_program.managing_contest

        try:
            # Update training program attributes
            attrs = training_program.get_attrs()
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")

            if not attrs["name"] or not attrs["name"].strip():
                raise ValueError("Name is required")

            if not attrs["description"] or not attrs["description"].strip():
                attrs["description"] = attrs["name"]

            training_program.set_attrs(attrs)

            # Sync description to managing contest
            contest.description = attrs["description"]

            # Update managing contest configuration fields
            contest_attrs = contest.get_attrs()

            # Allowed localizations (comma-separated list)
            allowed_localizations: str = self.get_argument("allowed_localizations", "")
            if allowed_localizations:
                contest_attrs["allowed_localizations"] = [
                    x.strip()
                    for x in allowed_localizations.split(",")
                    if len(x) > 0 and not x.isspace()
                ]
            else:
                contest_attrs["allowed_localizations"] = []

            # Programming languages
            contest_attrs["languages"] = self.get_arguments("languages")

            # Boolean settings
            self.get_bool(contest_attrs, "submissions_download_allowed")
            self.get_bool(contest_attrs, "allow_questions")
            self.get_bool(contest_attrs, "allow_user_tests")
            self.get_bool(contest_attrs, "allow_unofficial_submission_before_analysis_mode")
            self.get_bool(contest_attrs, "allow_delay_requests")

            # Login section boolean settings
            self.get_bool(contest_attrs, "block_hidden_participations")
            self.get_bool(contest_attrs, "allow_password_authentication")
            self.get_bool(contest_attrs, "allow_registration")
            self.get_bool(contest_attrs, "ip_restriction")
            self.get_bool(contest_attrs, "ip_autologin")

            # Score precision
            self.get_int(contest_attrs, "score_precision")

            # Times
            self.get_datetime(contest_attrs, "start")
            self.get_datetime(contest_attrs, "stop")
            self.get_string(contest_attrs, "timezone", empty=None)
            self.get_timedelta_sec(contest_attrs, "per_user_time")

            # Limits
            self.get_int(contest_attrs, "max_submission_number")
            self.get_int(contest_attrs, "max_user_test_number")
            self.get_timedelta_sec(contest_attrs, "min_submission_interval")
            self.get_timedelta_sec(contest_attrs, "min_submission_interval_grace_period")
            self.get_timedelta_sec(contest_attrs, "min_user_test_interval")

            # Token parameters
            self.get_string(contest_attrs, "token_mode")
            self.get_int(contest_attrs, "token_max_number")
            self.get_timedelta_sec(contest_attrs, "token_min_interval")
            self.get_int(contest_attrs, "token_gen_initial")
            self.get_int(contest_attrs, "token_gen_number")
            self.get_timedelta_min(contest_attrs, "token_gen_interval")
            self.get_int(contest_attrs, "token_gen_max")

            # Apply contest attributes
            contest.set_attrs(contest_attrs)

            # Validate that stop is not before start (only if both are set)
            if (
                contest.start is not None
                and contest.stop is not None
                and contest.stop < contest.start
            ):
                raise ValueError("End time must be after start time")

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error)
            )
            self.redirect(fallback)
            return

        if self.try_commit():
            # Update the contest on RWS.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback)


class AddTrainingProgramHandler(
    SimpleHandler("add_training_program.html", permission_all=True)
):
    """Add a new training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.render("add_training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback = self.url("training_programs", "add")
        operation = self.get_argument("operation", "Create")

        try:
            name = self.get_argument("name")
            if not name or not name.strip():
                raise ValueError("Name is required")

            description = self.get_argument("description", "")
            if not description or not description.strip():
                description = name

            # Parse optional start and stop times from datetime-local inputs
            start_str = self.get_argument("start", "")
            stop_str = self.get_argument("stop", "")

            contest_kwargs: dict = {
                "name": "__" + name,
                "description": description,
                "allow_delay_requests": False,
            }

            if start_str:
                contest_kwargs["start"] = dt.strptime(start_str, "%Y-%m-%dT%H:%M")

            if stop_str:
                contest_kwargs["stop"] = dt.strptime(stop_str, "%Y-%m-%dT%H:%M")

            # Validate that stop is not before start
            if "start" in contest_kwargs and "stop" in contest_kwargs:
                if contest_kwargs["stop"] < contest_kwargs["start"]:
                    raise ValueError("End time must be after start time")

            # Create the managing contest
            managing_contest = Contest(**contest_kwargs)
            self.sql_session.add(managing_contest)

            # Create the training program
            training_program = TrainingProgram(
                name=name,
                description=description,
                managing_contest=managing_contest,
            )
            self.sql_session.add(training_program)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback)
            return

        if self.try_commit():
            if operation == "Create and add another":
                self.redirect(fallback)
            else:
                self.redirect(self.url("training_programs"))
        else:
            self.redirect(fallback)


class RemoveTrainingProgramHandler(BaseHandler):
    """Confirm and remove a training program.

    On delete, the managing contest and all its data (participations,
    submissions, tasks) will also be deleted due to CASCADE.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = 0

        # Count related data that will be deleted
        self.r_params["participation_count"] = (
            self.sql_session.query(Participation)
            .filter(Participation.contest == managing_contest)
            .count()
        )
        training_day_contest_ids = [td.contest_id for td in training_program.training_days]
        self.r_params["training_day_count"] = len(training_day_contest_ids)
        self.r_params["training_day_participation_count"] = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id.in_(training_day_contest_ids))
            .count()
            if training_day_contest_ids else 0
        )
        self.r_params["submission_count"] = (
            self.sql_session.query(Submission)
            .join(Participation)
            .filter(Participation.contest == managing_contest)
            .count()
        )
        self.r_params["training_day_submission_count"] = (
            self.sql_session.query(Submission)
            .join(Participation)
            .filter(Participation.contest_id.in_(training_day_contest_ids))
            .count()
            if training_day_contest_ids else 0
        )
        self.r_params["task_count"] = len(managing_contest.tasks)

        # Other contests available to move tasks into (excluding training day contests)
        self.r_params["other_contests"] = (
            self.sql_session.query(Contest)
            .filter(Contest.id != managing_contest.id)
            .filter(~Contest.name.like(r'\_\_%', escape='\\'))
            .filter(~Contest.training_day.has())
            .order_by(Contest.name)
            .all()
        )

        self.render("training_program_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str):

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        action = self.get_argument("action", "delete_all")
        target_contest_id = self.get_argument("target_contest_id", None)

        # Handle tasks before deleting the training program
        tasks = (
            self.sql_session.query(Task)
            .filter(Task.contest == managing_contest)
            .order_by(Task.num)
            .all()
        )

        if action == "move":
            if not target_contest_id:
                raise tornado.web.HTTPError(400, "Target contest is required")
            target_contest = self.safe_get_item(Contest, target_contest_id)

            # Phase 1: clear nums on moving tasks (and detach training day links)
            # so we can reassign without violating the unique constraint.
            for task in tasks:
                task.num = None
                task.training_day = None
                task.training_day_num = None
            self.sql_session.flush()

            # Phase 2: append after current max num in target, preserving gaps.
            max_num = (
                self.sql_session.query(func.max(Task.num))
                .filter(Task.contest == target_contest)
                .scalar()
            )
            base_num = (max_num or -1) + 1

            for i, task in enumerate(tasks):
                task.contest = target_contest
                task.num = base_num + i
            self.sql_session.flush()

        elif action == "detach":
            for task in tasks:
                task.contest = None
                task.num = None
                task.training_day = None
                task.training_day_num = None
            self.sql_session.flush()

        elif action == "delete_all":
            for task in tasks:
                self.sql_session.delete(task)
            self.sql_session.flush()
        else:
            raise tornado.web.HTTPError(400, "Invalid action")

        # Delete all training days (and their contests/participations).
        for training_day in list(training_program.training_days):
            td_contest = training_day.contest
            self.sql_session.delete(training_day)
            self.sql_session.delete(td_contest)

        # Delete the training program (tasks already handled above)
        self.sql_session.delete(training_program)

        # Then delete the managing contest (this cascades to participations,
        # submissions, etc. - tasks already handled above)
        self.sql_session.delete(managing_contest)

        self.try_commit()
        self.write("../../training_programs")


class TrainingProgramStudentsHandler(BaseHandler):
    """List and manage students in a training program."""
    REMOVE_FROM_PROGRAM = "Remove from training program"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        self.r_params["unassigned_users"] = \
            self.sql_session.query(User)\
                .filter(User.id.notin_(
                    self.sql_session.query(Participation.user_id)
                        .filter(Participation.contest == managing_contest)
                        .all()))\
                .filter(~User.username.like(r'\_\_%', escape='\\'))\
                .all()

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

        # Count submissions and participations from training days
        training_day_contest_ids = [td.contest_id for td in training_program.training_days]
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

        self.r_params["user"] = user
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = 0
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
            student = Student(
                training_program=training_program,
                participation=participation,
                student_tags=[]
            )
            self.sql_session.add(student)
            self.try_commit()

        submission_query = self.sql_session.query(Submission).filter(
            Submission.participation == participation
        )
        page = int(self.get_query_argument("page", "0"))
        self.render_params_for_submissions(submission_query, page)

        # Get all unique student tags from this training program for autocomplete
        self.r_params["training_program"] = training_program
        self.r_params["participation"] = participation
        self.r_params["student"] = student
        self.r_params["selected_user"] = participation.user
        self.r_params["teams"] = self.sql_session.query(Team).all()
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
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


class TrainingProgramTasksHandler(BaseHandler):
    """Manage tasks in a training program."""
    REMOVE_FROM_PROGRAM = "Remove from training program"
    MOVE_UP = "up by 1"
    MOVE_DOWN = "down by 1"
    MOVE_TOP = "to the top"
    MOVE_BOTTOM = "to the bottom"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        self.r_params["unassigned_tasks"] = \
            self.sql_session.query(Task)\
                .filter(Task.contest_id.is_(None))\
                .all()

        self.render("training_program_tasks.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "tasks")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            task_id: str = self.get_argument("task_id")
            operation: str = self.get_argument("operation")
            assert operation in (
                self.REMOVE_FROM_PROGRAM,
                self.MOVE_UP,
                self.MOVE_DOWN,
                self.MOVE_TOP,
                self.MOVE_BOTTOM
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)
        task2 = None

        task_num = task.num

        if operation == self.REMOVE_FROM_PROGRAM:
            # If the task is in a training day, redirect to confirmation page
            if task.training_day is not None:
                asking_page = self.url(
                    "training_program", training_program_id, "task", task_id, "remove"
                )
                self.redirect(asking_page)
                return

            task.contest = None
            task.num = None

            self.sql_session.flush()

            for t in self.sql_session.query(Task)\
                         .filter(Task.contest == managing_contest)\
                         .filter(Task.num > task_num)\
                         .order_by(Task.num)\
                         .all():
                t.num -= 1
                self.sql_session.flush()

        elif operation == self.MOVE_UP:
            task2 = self.sql_session.query(Task)\
                        .filter(Task.contest == managing_contest)\
                        .filter(Task.num == task.num - 1)\
                        .first()

        elif operation == self.MOVE_DOWN:
            task2 = self.sql_session.query(Task)\
                        .filter(Task.contest == managing_contest)\
                        .filter(Task.num == task.num + 1)\
                        .first()

        elif operation == self.MOVE_TOP:
            task.num = None
            self.sql_session.flush()

            for t in self.sql_session.query(Task)\
                         .filter(Task.contest == managing_contest)\
                         .filter(Task.num < task_num)\
                         .order_by(Task.num.desc())\
                         .all():
                t.num += 1
                self.sql_session.flush()

            task.num = 0

        elif operation == self.MOVE_BOTTOM:
            task.num = None
            self.sql_session.flush()

            for t in self.sql_session.query(Task)\
                         .filter(Task.contest == managing_contest)\
                         .filter(Task.num > task_num)\
                         .order_by(Task.num)\
                         .all():
                t.num -= 1
                self.sql_session.flush()

            self.sql_session.flush()
            task.num = len(managing_contest.tasks) - 1

        if task2 is not None:
            tmp_a, tmp_b = task.num, task2.num
            task.num, task2.num = None, None
            self.sql_session.flush()
            task.num, task2.num = tmp_b, tmp_a

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class AddTrainingProgramTaskHandler(BaseHandler):
    """Add a task to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "tasks")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        try:
            task_id: str = self.get_argument("task_id")
            assert task_id != "null", "Please select a valid task"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

        task.num = len(managing_contest.tasks)
        task.contest = managing_contest

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class RemoveTrainingProgramTaskHandler(BaseHandler):
    """Confirm and remove a task from a training program.

    This handler is used when a task is assigned to a training day,
    to warn the user that the task will also be removed from the training day.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, task_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        task = self.safe_get_item(Task, task_id)

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["task"] = task
        self.r_params["unanswered"] = 0

        self.render("training_program_task_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str, task_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest
        task = self.safe_get_item(Task, task_id)
        task_num = task.num

        # Remove from training day if assigned
        if task.training_day is not None:
            training_day = task.training_day
            training_day_num = task.training_day_num
            task.training_day = None
            task.training_day_num = None

            self.sql_session.flush()

            # Reorder remaining tasks in the training day
            for t in self.sql_session.query(Task)\
                         .filter(Task.training_day == training_day)\
                         .filter(Task.training_day_num > training_day_num)\
                         .order_by(Task.training_day_num)\
                         .all():
                t.training_day_num -= 1
                self.sql_session.flush()

        # Remove from training program
        task.contest = None
        task.num = None

        self.sql_session.flush()

        # Reorder remaining tasks in the training program
        for t in self.sql_session.query(Task)\
                     .filter(Task.contest == managing_contest)\
                     .filter(Task.num > task_num)\
                     .order_by(Task.num)\
                     .all():
            t.num -= 1
            self.sql_session.flush()

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.write("../../tasks")


class TrainingProgramRankingHandler(BaseHandler):
    """Show ranking for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, format: str = "online"):
        import csv
        import io
        from sqlalchemy.orm import joinedload
        from cms.grading.scoring import task_score
        from .contestranking import TaskStatus

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == managing_contest.id)
            .options(joinedload("participations"))
            .options(joinedload("participations.submissions"))
            .options(joinedload("participations.submissions.token"))
            .options(joinedload("participations.submissions.results"))
            .options(joinedload("participations.statement_views"))
            .first()
        )

        statement_views_set = set()
        for p in self.contest.participations:
            for sv in p.statement_views:
                statement_views_set.add((sv.participation_id, sv.task_id))

        show_teams = False
        for p in self.contest.participations:
            show_teams = show_teams or p.team_id

            p.task_statuses = []
            total_score = 0.0
            partial = False
            for task in self.contest.get_tasks():
                t_score, t_partial = task_score(p, task, rounded=True)
                has_submissions = any(s.task_id == task.id and s.official
                                     for s in p.submissions)
                has_opened = (p.id, task.id) in statement_views_set
                p.task_statuses.append(
                    TaskStatus(
                        score=t_score,
                        partial=t_partial,
                        has_submissions=has_submissions,
                        has_opened=has_opened,
                        can_access=True,
                    )
                )
                total_score += t_score
                partial = partial or t_partial

            # Ensure task_statuses align with template header order
            assert len(self.contest.get_tasks()) == len(p.task_statuses)
            total_score = round(total_score, self.contest.score_precision)
            p.total_score = (total_score, partial)

        # Build student tags lookup for each participation
        student_tags_by_participation = {}
        for p in self.contest.participations:
            # Find the student record for this participation
            student = (
                self.sql_session.query(Student)
                .filter(Student.training_program_id == training_program.id)
                .filter(Student.participation_id == p.id)
                .first()
            )
            if student:
                student_tags_by_participation[p.id] = student.student_tags or []
            else:
                student_tags_by_participation[p.id] = []

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = self.contest
        self.r_params["show_teams"] = show_teams
        self.r_params["student_tags_by_participation"] = student_tags_by_participation
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == self.contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        if format == "txt":
            self.set_header("Content-Type", "text/plain")
            self.set_header("Content-Disposition",
                            "attachment; filename=\"ranking.txt\"")
            self.render("ranking.txt", **self.r_params)
        elif format == "csv":
            self.set_header("Content-Type", "text/csv")
            self.set_header("Content-Disposition",
                            "attachment; filename=\"ranking.csv\"")

            output = io.StringIO()
            writer = csv.writer(output)

            include_partial = True

            row = ["Username", "User"]
            if student_tags_by_participation:
                row.append("Tags")
            if show_teams:
                row.append("Team")
            for task in self.contest.tasks:
                row.append(task.name)
                if include_partial:
                    row.append("P")

            row.append("Global")
            if include_partial:
                row.append("P")

            writer.writerow(row)

            for p in sorted(self.contest.participations,
                            key=lambda p: p.total_score, reverse=True):
                if p.hidden:
                    continue

                row = [p.user.username,
                       "%s %s" % (p.user.first_name, p.user.last_name)]
                if student_tags_by_participation:
                    tags = student_tags_by_participation.get(p.id, [])
                    row.append(", ".join(tags))
                if show_teams:
                    row.append(p.team.name if p.team else "")
                assert len(self.contest.tasks) == len(p.task_statuses)
                for status in p.task_statuses:
                    row.append(status.score)
                    if include_partial:
                        row.append(self._status_indicator(status))

                total_score, partial = p.total_score
                row.append(total_score)
                if include_partial:
                    row.append("*" if partial else "")

                writer.writerow(row)

            self.finish(output.getvalue())
        else:
            self.render("ranking.html", **self.r_params)

    @staticmethod
    def _status_indicator(status) -> str:
        """Return a status indicator string for CSV export.

        status: a TaskStatus namedtuple with score, partial, has_submissions,
            has_opened fields.

        return: a string indicator for the status.

        """
        star = "*" if status.partial else ""
        if not status.has_submissions:
            return "X" if not status.has_opened else "-"
        if not status.has_opened:
            return "!" + star
        return star


class TrainingProgramSubmissionsHandler(BaseHandler):
    """Show submissions for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = managing_contest
        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        query = self.sql_session.query(Submission).join(Task)\
            .filter(Task.contest == managing_contest)
        page = int(self.get_query_argument("page", "0"))
        self.render_params_for_submissions(query, page)

        # Show training day column for training program submissions
        self.r_params["is_training_program"] = True

        self.render("contest_submissions.html", **self.r_params)


class TrainingProgramAnnouncementsHandler(BaseHandler):
    """Manage announcements for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = managing_contest
        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        self.render("announcements.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        subject = self.get_argument("subject", "")
        text = self.get_argument("text", "")
        announcement_id = self.get_argument("announcement_id", None)

        # Parse visible_to_tags from comma-separated string
        visible_to_tags_str = self.get_argument("visible_to_tags", "")
        visible_to_tags = parse_tags(visible_to_tags_str)

        if subject and text:
            if announcement_id is not None:
                # Edit existing announcement
                announcement = self.safe_get_item(Announcement, announcement_id)
                if announcement.contest_id != managing_contest.id:
                    raise tornado.web.HTTPError(404)
                announcement.subject = subject
                announcement.text = text
                announcement.visible_to_tags = visible_to_tags
            else:
                # Add new announcement
                announcement = Announcement(
                    timestamp=make_datetime(),
                    subject=subject,
                    text=text,
                    contest=managing_contest,
                    admin=self.current_user,
                    visible_to_tags=visible_to_tags,
                )
                self.sql_session.add(announcement)
            self.try_commit()

        self.redirect(self.url("training_program", training_program_id, "announcements"))


class TrainingProgramAnnouncementHandler(BaseHandler):
    """Delete an announcement from a training program."""

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def delete(self, training_program_id: str, ann_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        announcement = self.safe_get_item(Announcement, ann_id)
        if announcement.contest_id != managing_contest.id:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(announcement)
        self.try_commit()

        # Return relative path for ajax_delete
        # Note: This is resolved relative to the current page URL (announcements list),
        # not the delete URL, so we just need "announcements" not "../announcements"
        self.write("announcements")


class TrainingProgramQuestionsHandler(BaseHandler):
    """Manage questions for a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.contest = managing_contest
        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest

        self.r_params["questions"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .order_by(Question.question_timestamp.desc())\
            .order_by(Question.id).all()

        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        self.render("questions.html", **self.r_params)


class TrainingProgramTrainingDaysHandler(BaseHandler):
    """List and manage training days in a training program."""
    REMOVE = "Remove"
    MOVE_UP = "up by 1"
    MOVE_DOWN = "down by 1"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)

        self.render("training_program_training_days.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "training_days")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        try:
            training_day_id: str = self.get_argument("training_day_id")
            operation: str = self.get_argument("operation")
            assert operation in (
                self.REMOVE,
                self.MOVE_UP,
                self.MOVE_DOWN,
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            self.service.add_notification(
                make_datetime(), "Invalid training day", "Training day does not belong to this program")
            self.redirect(fallback_page)
            return

        if operation == self.REMOVE:
            asking_page = self.url(
                "training_program", training_program_id,
                "training_day", training_day_id, "remove"
            )
            self.redirect(asking_page)
            return

        elif operation == self.MOVE_UP:
            training_day2 = self.sql_session.query(TrainingDay)\
                .filter(TrainingDay.training_program == training_program)\
                .filter(TrainingDay.position == training_day.position - 1)\
                .first()

            if training_day2 is not None:
                tmp_a, tmp_b = training_day.position, training_day2.position
                training_day.position, training_day2.position = None, None
                self.sql_session.flush()
                training_day.position, training_day2.position = tmp_b, tmp_a

        elif operation == self.MOVE_DOWN:
            training_day2 = self.sql_session.query(TrainingDay)\
                .filter(TrainingDay.training_program == training_program)\
                .filter(TrainingDay.position == training_day.position + 1)\
                .first()

            if training_day2 is not None:
                tmp_a, tmp_b = training_day.position, training_day2.position
                training_day.position, training_day2.position = None, None
                self.sql_session.flush()
                training_day.position, training_day2.position = tmp_b, tmp_a

        self.try_commit()
        self.redirect(fallback_page)


class AddTrainingDayHandler(BaseHandler):
    """Add a new training day to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()

        # Get all student tags for the tagify select dropdown
        tags_query = self.sql_session.query(
            func.unnest(Student.student_tags).label("tag")
        ).filter(
            Student.training_program_id == training_program.id
        ).distinct()
        self.r_params["all_student_tags"] = sorted([row.tag for row in tags_query.all()])

        # Add timezone info for the form (use managing contest timezone)
        tz = get_timezone(None, managing_contest)
        self.r_params["timezone"] = tz
        self.r_params["timezone_name"] = get_timezone_name(tz)

        self.render("add_training_day.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url("training_program", training_program_id, "training_days", "add")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        managing_contest = training_program.managing_contest

        # Get timezone for parsing datetime inputs
        tz = get_timezone(None, managing_contest)

        try:
            name = self.get_argument("name")
            if not name or not name.strip():
                raise ValueError("Name is required")

            description = self.get_argument("description", "")
            if not description or not description.strip():
                description = name

            # Parse optional start time and duration from inputs
            # Times are in the managing contest timezone
            start_str = self.get_argument("start", "")
            duration_hours_str = self.get_argument("duration_hours", "")
            duration_minutes_str = self.get_argument("duration_minutes", "")

            contest_kwargs: dict = {
                "name": name,
                "description": description,
            }

            if start_str:
                # Parse datetime in timezone and convert to UTC
                local_start = dt.strptime(start_str, "%Y-%m-%dT%H:%M")
                contest_kwargs["start"] = local_to_utc(local_start, tz)
            else:
                # Default to after training program end year (so contestants can't start until configured)
                program_end_year = managing_contest.stop.year
                default_date = dt(program_end_year + 1, 1, 1, 0, 0)
                contest_kwargs["start"] = default_date
                # Also set analysis_start/stop to satisfy Contest check constraints
                # (stop <= analysis_start and analysis_start <= analysis_stop)
                contest_kwargs["analysis_start"] = default_date
                contest_kwargs["analysis_stop"] = default_date

            # Calculate stop time from start + duration
            duration_hours = int(duration_hours_str) if duration_hours_str.strip() else 0
            duration_minutes = int(duration_minutes_str) if duration_minutes_str.strip() else 0

            if duration_hours > 0 or duration_minutes > 0:
                duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                contest_kwargs["stop"] = contest_kwargs["start"] + duration
            else:
                # Default stop to same as start when no duration specified
                contest_kwargs["stop"] = contest_kwargs["start"]

            # Parse main group configuration (if any)
            group_tags = self.get_arguments("group_tag_name[]")
            group_starts = self.get_arguments("group_start_time[]")
            group_duration_hours = self.get_arguments("group_duration_hours[]")
            group_duration_minutes = self.get_arguments("group_duration_minutes[]")
            group_alphabeticals = self.get_arguments("group_alphabetical[]")

            # Collect valid groups and their times for defaulting
            groups_to_create = []
            group_start_times = []
            group_end_times = []

            for i, tag in enumerate(group_tags):
                tag = tag.strip()
                if not tag:
                    continue

                group_start = None
                group_end = None

                if i < len(group_starts) and group_starts[i].strip():
                    local_group_start = dt.strptime(group_starts[i].strip(), "%Y-%m-%dT%H:%M")
                    group_start = local_to_utc(local_group_start, tz)
                    group_start_times.append(group_start)

                # Calculate group end from start + duration
                g_duration_hours = 0
                g_duration_minutes = 0
                if i < len(group_duration_hours) and group_duration_hours[i].strip():
                    g_duration_hours = int(group_duration_hours[i].strip())
                if i < len(group_duration_minutes) and group_duration_minutes[i].strip():
                    g_duration_minutes = int(group_duration_minutes[i].strip())

                if group_start and (g_duration_hours > 0 or g_duration_minutes > 0):
                    group_duration = timedelta(hours=g_duration_hours, minutes=g_duration_minutes)
                    group_end = group_start + group_duration
                    group_end_times.append(group_end)

                alphabetical = str(i) in group_alphabeticals

                groups_to_create.append({
                    "tag_name": tag,
                    "start_time": group_start,
                    "end_time": group_end,
                    "alphabetical_task_order": alphabetical,
                })

            # Default training start/end from group times if not specified
            if not start_str and group_start_times:
                contest_kwargs["start"] = min(group_start_times)
            if (duration_hours == 0 and duration_minutes == 0) and group_end_times:
                contest_kwargs["stop"] = max(group_end_times)

            contest = Contest(**contest_kwargs)
            self.sql_session.add(contest)
            self.sql_session.flush()

            position = len(training_program.training_days)
            training_day = TrainingDay(
                training_program=training_program,
                contest=contest,
                position=position,
            )
            self.sql_session.add(training_day)

            # Create main groups
            seen_tags = set()
            for group_data in groups_to_create:
                if group_data["tag_name"] in seen_tags:
                    raise ValueError(f"Duplicate tag '{group_data['tag_name']}'")
                seen_tags.add(group_data["tag_name"])

                # Validate group times are within contest bounds
                if group_data["start_time"] and contest_kwargs.get("start"):
                    if group_data["start_time"] < contest_kwargs["start"]:
                        raise ValueError(f"Group '{group_data['tag_name']}' start time cannot be before training day start")
                if group_data["end_time"] and contest_kwargs.get("stop"):
                    if group_data["end_time"] > contest_kwargs["stop"]:
                        raise ValueError(f"Group '{group_data['tag_name']}' end time cannot be after training day end")

                group = TrainingDayGroup(
                    training_day=training_day,
                    **group_data
                )
                self.sql_session.add(group)

            # Auto-add participations for all students in the training program
            # Training days are for all students, so we create participations
            # in the training day's contest for each student
            # Pass the hidden property from the managing contest participation
            for student in training_program.students:
                user = student.participation.user
                hidden = student.participation.hidden
                participation = Participation(contest=contest, user=user, hidden=hidden)
                self.sql_session.add(participation)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.redirect(self.url("training_program", training_program_id, "training_days"))
        else:
            self.redirect(fallback_page)


class RemoveTrainingDayHandler(BaseHandler):
    """Confirm and remove a training day from a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, training_day_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)
        managing_contest = training_program.managing_contest

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404)

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["training_day"] = training_day
        self.r_params["contest"] = managing_contest
        self.r_params["unanswered"] = 0

        # Stats for warning message
        self.r_params["task_count"] = len(training_day.tasks)
        # For archived training days, contest_id is None so counts are 0
        if training_day.contest_id is not None:
            self.r_params["participation_count"] = (
                self.sql_session.query(Participation)
                .filter(Participation.contest_id == training_day.contest_id)
                .count()
            )
            self.r_params["submission_count"] = (
                self.sql_session.query(Submission)
                .join(Participation)
                .filter(Participation.contest_id == training_day.contest_id)
                .count()
            )
        else:
            self.r_params["participation_count"] = 0
            self.r_params["submission_count"] = 0

        self.render("training_day_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, training_program_id: str, training_day_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404)

        contest = training_day.contest
        position = training_day.position

        # Always detach tasks from the training day - they stay in the training program.
        # The database FK has ON DELETE SET NULL, but we also clear training_day_num
        # explicitly to remove stale ordering metadata.
        tasks = (
            self.sql_session.query(Task)
            .filter(Task.training_day == training_day)
            .order_by(Task.training_day_num)
            .all()
        )

        for task in tasks:
            task.training_day = None
            task.training_day_num = None

        self.sql_session.flush()

        self.sql_session.delete(training_day)
        if contest is not None:
            self.sql_session.delete(contest)

        self.sql_session.flush()

        for td in training_program.training_days:
            if td.position is not None and position is not None and td.position > position:
                td.position -= 1

        self.try_commit()
        self.write("../../training_days")


class AddTrainingDayGroupHandler(BaseHandler):
    """Add a main group to a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        fallback_page = self.url("contest", contest_id)

        # Get timezone for parsing datetime inputs (use contest timezone)
        tz = get_timezone(None, contest)

        try:
            tag_name = self.get_argument("tag_name")
            if not tag_name or not tag_name.strip():
                raise ValueError("Tag name is required")

            # Strip whitespace before duplicate check to avoid bypass
            tag_name = tag_name.strip()

            # Check if tag is already used
            existing = self.sql_session.query(TrainingDayGroup)\
                .filter(TrainingDayGroup.training_day == training_day)\
                .filter(TrainingDayGroup.tag_name == tag_name)\
                .first()
            if existing:
                raise ValueError(f"Tag '{tag_name}' is already a main group")

            # Parse optional start time and duration
            start_str = self.get_argument("start_time", "")
            duration_hours_str = self.get_argument("duration_hours", "")
            duration_minutes_str = self.get_argument("duration_minutes", "")

            group_kwargs: dict = {
                "training_day": training_day,
                "tag_name": tag_name,
                "alphabetical_task_order": self.get_argument("alphabetical_task_order", None) is not None,
            }

            if start_str:
                local_start = dt.strptime(start_str, "%Y-%m-%dT%H:%M")
                group_kwargs["start_time"] = local_to_utc(local_start, tz)

            # Calculate end time from start + duration
            duration_hours = int(duration_hours_str) if duration_hours_str.strip() else 0
            duration_minutes = int(duration_minutes_str) if duration_minutes_str.strip() else 0

            if "start_time" in group_kwargs and (duration_hours > 0 or duration_minutes > 0):
                duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                group_kwargs["end_time"] = group_kwargs["start_time"] + duration

            # Validate group times are within contest bounds
            if "start_time" in group_kwargs and contest.start:
                if group_kwargs["start_time"] < contest.start:
                    raise ValueError(f"Group start time cannot be before training day start")
            if "end_time" in group_kwargs and contest.stop:
                if group_kwargs["end_time"] > contest.stop:
                    raise ValueError(f"Group end time cannot be after training day end")

            group = TrainingDayGroup(**group_kwargs)
            self.sql_session.add(group)

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.try_commit()
        self.redirect(fallback_page)


class UpdateTrainingDayGroupsHandler(BaseHandler):
    """Update all main groups for a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        fallback_page = self.url("contest", contest_id)

        # Get timezone for parsing datetime inputs (use contest timezone)
        tz = get_timezone(None, contest)

        try:
            group_ids = self.get_arguments("group_id[]")
            start_times = self.get_arguments("start_time[]")
            duration_hours_list = self.get_arguments("duration_hours[]")
            duration_minutes_list = self.get_arguments("duration_minutes[]")

            if len(group_ids) != len(start_times):
                raise ValueError("Mismatched form data")

            for i, group_id in enumerate(group_ids):
                group = self.safe_get_item(TrainingDayGroup, group_id)
                if group.training_day_id != training_day.id:
                    raise ValueError(f"Group {group_id} does not belong to this training day")

                # Parse start time in timezone and convert to UTC
                start_str = start_times[i].strip()
                if start_str:
                    local_start = dt.strptime(start_str, "%Y-%m-%dT%H:%M")
                    group.start_time = local_to_utc(local_start, tz)
                else:
                    group.start_time = None

                # Calculate end time from start + duration
                duration_hours = 0
                duration_minutes = 0
                if i < len(duration_hours_list) and duration_hours_list[i].strip():
                    duration_hours = int(duration_hours_list[i].strip())
                if i < len(duration_minutes_list) and duration_minutes_list[i].strip():
                    duration_minutes = int(duration_minutes_list[i].strip())

                if group.start_time and (duration_hours > 0 or duration_minutes > 0):
                    duration = timedelta(hours=duration_hours, minutes=duration_minutes)
                    group.end_time = group.start_time + duration
                else:
                    group.end_time = None

                # Validate group times are within contest bounds
                if group.start_time and contest.start:
                    if group.start_time < contest.start:
                        raise ValueError(f"Group '{group.tag_name}' start time cannot be before training day start")
                if group.end_time and contest.stop:
                    if group.end_time > contest.stop:
                        raise ValueError(f"Group '{group.tag_name}' end time cannot be after training day end")

                # Update alphabetical task order (checkbox - present means checked)
                group.alphabetical_task_order = self.get_argument(f"alphabetical_{group_id}", None) is not None

        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.try_commit()
        self.redirect(fallback_page)


class RemoveTrainingDayGroupHandler(BaseHandler):
    """Remove a main group from a training day."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id: str, group_id: str):
        contest = self.safe_get_item(Contest, contest_id)
        training_day = contest.training_day

        if training_day is None:
            raise tornado.web.HTTPError(404, "Not a training day contest")

        group = self.safe_get_item(TrainingDayGroup, group_id)

        if group.training_day_id != training_day.id:
            raise tornado.web.HTTPError(404, "Group does not belong to this training day")

        self.sql_session.delete(group)
        self.try_commit()
        self.redirect(self.url("contest", contest_id))


class TrainingDayTypesHandler(BaseHandler):
    """Handler for updating training day types via AJAX."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, training_day_id: str):
        self.set_header("Content-Type", "application/json")

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            self.set_status(404)
            self.write({"error": "Training day does not belong to this program"})
            return

        try:
            types_str = self.get_argument("training_day_types", "")
            training_day.training_day_types = parse_tags(types_str)

            if self.try_commit():
                self.write({
                    "success": True,
                    "types": training_day.training_day_types
                })
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

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["participation"] = participation
        self.r_params["student"] = student
        self.r_params["selected_user"] = participation.user
        self.r_params["student_tasks"] = sorted(
            student.student_tasks, key=lambda st: st.assigned_at, reverse=True
        )
        self.r_params["available_tasks"] = available_tasks
        self.r_params["home_scores"] = home_scores
        self.r_params["training_scores"] = training_scores
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
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

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["all_tasks"] = all_tasks
        self.r_params["all_student_tags"] = all_student_tags
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
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


class ArchiveTrainingDayHandler(BaseHandler):
    """Archive a training day, extracting attendance and ranking data."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, training_program_id: str, training_day_id: str):
        """Show the archive confirmation page with IP selection."""
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404, "Training day not in this program")

        if training_day.contest is None:
            raise tornado.web.HTTPError(400, "Training day is already archived")

        contest = training_day.contest

        # Get all participations with their starting IPs
        # Count students per IP (only IPs with more than one student)
        ip_counts: dict[str, int] = {}
        for participation in contest.participations:
            if participation.starting_ip_addresses:
                # Parse comma-separated IP addresses
                ips = [ip.strip() for ip in participation.starting_ip_addresses.split(",") if ip.strip()]
                for ip in ips:
                    ip_counts[ip] = ip_counts.get(ip, 0) + 1

        # Filter to only IPs with more than one student
        shared_ips = {ip: count for ip, count in ip_counts.items() if count > 1}

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["training_day"] = training_day
        self.r_params["contest"] = contest
        self.r_params["shared_ips"] = shared_ips
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == training_program.managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.render("archive_training_day.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str, training_day_id: str):
        """Perform the archiving operation."""
        fallback_page = self.url(
            "training_program", training_program_id, "training_days"
        )

        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        training_day = self.safe_get_item(TrainingDay, training_day_id)

        if training_day.training_program_id != training_program.id:
            raise tornado.web.HTTPError(404, "Training day not in this program")

        if training_day.contest is None:
            self.service.add_notification(
                make_datetime(), "Error", "Training day is already archived"
            )
            self.redirect(fallback_page)
            return

        contest = training_day.contest

        # Get selected class IPs from form
        class_ips = set(self.get_arguments("class_ips"))

        try:
            # Save name, description, and start_time from contest before archiving
            training_day.name = contest.name
            training_day.description = contest.description
            training_day.start_time = contest.start

            # Calculate and store the training day duration
            # Use max duration among main groups (if any), or training day duration
            training_day.duration = self._calculate_training_day_duration(
                training_day, contest
            )

            # Archive attendance data for each student
            self._archive_attendance_data(training_day, contest, class_ips)

            # Archive ranking data for each student
            self._archive_ranking_data(training_day, contest)

            # Delete the contest (this will cascade delete participations)
            self.sql_session.delete(contest)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Archive failed", repr(error)
            )
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Training day archived",
                f"Training day '{training_day.name}' has been archived successfully"
            )

        self.redirect(fallback_page)

    def _calculate_training_day_duration(
        self,
        training_day: TrainingDay,
        contest: Contest
    ) -> timedelta | None:
        """Calculate the training day duration for archiving.

        Returns the max training duration among main groups (if any),
        or the training day duration (if no main groups).

        training_day: the training day being archived.
        contest: the contest associated with the training day.

        return: the duration as a timedelta, or None if not calculable.
        """
        # Check if there are main groups with custom timing
        main_groups = training_day.groups
        if main_groups:
            # Calculate max duration among main groups
            max_duration: timedelta | None = None
            for group in main_groups:
                if group.start_time is not None and group.end_time is not None:
                    group_duration = group.end_time - group.start_time
                    if max_duration is None or group_duration > max_duration:
                        max_duration = group_duration
            if max_duration is not None:
                return max_duration

        # Fall back to training day (contest) duration
        if contest.start is not None and contest.stop is not None:
            return contest.stop - contest.start

        return None

    def _archive_attendance_data(
        self,
        training_day: TrainingDay,
        contest: Contest,
        class_ips: set[str]
    ) -> None:
        """Extract and store attendance data for all students."""
        training_program = training_day.training_program

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = (
                self.sql_session.query(Student)
                .join(Participation)
                .filter(Participation.user_id == participation.user_id)
                .filter(Student.training_program_id == training_program.id)
                .first()
            )

            if student is None:
                continue

            # Skip ineligible students (not in any main group)
            # These students were never supposed to participate in this training day
            is_eligible, _, _ = check_training_day_eligibility(
                self.sql_session, participation, training_day
            )
            if not is_eligible:
                continue

            # Determine status
            if participation.starting_time is None:
                status = "missed"
            else:
                status = "participated"

            # Determine location based on starting IPs
            # If no class IPs were selected, everyone who participated is considered "home"
            location = None
            if status == "participated":
                if not class_ips:
                    # No class IPs selected means everyone is at home
                    location = "home"
                elif participation.starting_ip_addresses:
                    # Parse comma-separated IP addresses
                    ips = [ip.strip() for ip in participation.starting_ip_addresses.split(",") if ip.strip()]
                    if ips:
                        has_class_ip = any(ip in class_ips for ip in ips)
                        has_home_ip = any(ip not in class_ips for ip in ips)

                        if has_class_ip and has_home_ip:
                            location = "both"
                        elif has_class_ip:
                            location = "class"
                        elif has_home_ip:
                            location = "home"
                    else:
                        # Participated but no IP recorded - assume home
                        location = "home"
                else:
                    # Participated but no IP recorded - assume home
                    location = "home"

            # Get delay time
            delay_time = participation.delay_time

            # Concatenate delay reasons from all delay requests
            delay_requests = (
                self.sql_session.query(DelayRequest)
                .filter(DelayRequest.participation_id == participation.id)
                .order_by(DelayRequest.request_timestamp)
                .all()
            )
            delay_reasons = None
            if delay_requests:
                reasons = [dr.reason for dr in delay_requests if dr.reason]
                if reasons:
                    delay_reasons = "; ".join(reasons)

            # Create archived attendance record
            archived_attendance = ArchivedAttendance(
                status=status,
                location=location,
                delay_time=delay_time,
                delay_reasons=delay_reasons,
            )
            archived_attendance.training_day_id = training_day.id
            archived_attendance.student_id = student.id
            self.sql_session.add(archived_attendance)

    def _archive_ranking_data(
        self,
        training_day: TrainingDay,
        contest: Contest
    ) -> None:
        """Extract and store ranking data for all students.

        Stores on TrainingDay:
        - archived_tasks_data: task metadata including extra_headers for submission table

        Stores on ArchivedStudentRanking (per student):
        - task_scores: scores for ALL visible tasks (including 0 scores)
          The presence of a task_id key indicates the task was visible.
        - submissions: submission data for each task in RWS format
        - history: score history in RWS format
        """
        from cms.grading.scorecache import get_cached_score_entry

        training_program = training_day.training_program

        # Get the tasks assigned to this training day
        training_day_tasks = training_day.tasks
        training_day_task_ids = {task.id for task in training_day_tasks}

        # Build and store tasks_data on the training day (same for all students)
        # This preserves the scoring scheme as it was during the training day
        archived_tasks_data: dict[str, dict] = {}
        for task in training_day_tasks:
            max_score = 100.0
            extra_headers: list[str] = []
            score_precision = task.score_precision
            if task.active_dataset:
                try:
                    score_type = task.active_dataset.score_type_object
                    max_score = score_type.max_score
                    extra_headers = score_type.ranking_headers
                except (KeyError, TypeError, AttributeError):
                    pass

            archived_tasks_data[str(task.id)] = {
                "name": task.title,
                "short_name": task.name,
                "max_score": max_score,
                "score_precision": score_precision,
                "extra_headers": extra_headers,
                "training_day_num": task.training_day_num,
            }
        training_day.archived_tasks_data = archived_tasks_data

        for participation in contest.participations:
            # Find the student for this user in the training program
            # Note: Student.participation_id points to the managing contest participation,
            # not the training day participation, so we need to look up by user_id
            student = (
                self.sql_session.query(Student)
                .join(Participation)
                .filter(Participation.user_id == participation.user_id)
                .filter(Student.training_program_id == training_program.id)
                .first()
            )

            if student is None:
                continue

            # Skip ineligible students (not in any main group)
            # These students were never supposed to participate in this training day
            is_eligible, _, _ = check_training_day_eligibility(
                self.sql_session, participation, training_day
            )
            if not is_eligible:
                continue

            # Get all student tags (as list for array storage)
            student_tags = list(student.student_tags) if student.student_tags else []

            # Determine which tasks should be visible to this student based on their tags
            # This uses the same logic as _add_training_day_tasks_to_student in StartHandler
            # A task is visible if:
            # - The task has no visible_to_tags (empty list = visible to all)
            # - The student has at least one tag matching the task's visible_to_tags
            visible_tasks: list[Task] = []
            for task in training_day_tasks:
                if can_access_task(self.sql_session, task, participation, training_day):
                    visible_tasks.append(task)

            # Add visible tasks to student's StudentTask records if not already present
            # This allows students who missed the training to still submit from home
            existing_task_ids = {st.task_id for st in student.student_tasks}
            for task in visible_tasks:
                if task.id not in existing_task_ids:
                    student_task = StudentTask(assigned_at=make_datetime())
                    student_task.student_id = student.id
                    student_task.task_id = task.id
                    student_task.source_training_day_id = training_day.id
                    self.sql_session.add(student_task)

            # Get the managing participation for this user
            # Submissions are stored with the managing contest participation, not the
            # training day participation
            managing_participation = get_managing_participation(
                self.sql_session, training_day, participation.user
            )
            if managing_participation is None:
                raise ValueError(
                    f"User {participation.user.username} (id={participation.user_id}) "
                    f"does not have a participation in the managing contest "
                    f"'{training_day.training_program.managing_contest.name}' "
                    f"for training day '{training_day.name}'"
                )

            # Check if student missed the training (no starting_time)
            student_missed = participation.starting_time is None

            # Get task scores for ALL visible tasks (including 0 scores)
            # The presence of a task_id key indicates the task was visible
            task_scores: dict[str, float] = {}
            submissions: dict[str, list[dict]] = {}

            for task in visible_tasks:
                task_id = task.id

                if student_missed:
                    # Student missed the training - set score to 0
                    task_scores[str(task_id)] = 0.0
                else:
                    # Get score from the training day participation (for cache lookup)
                    cache_entry = get_cached_score_entry(
                        self.sql_session, participation, task
                    )
                    task_scores[str(task_id)] = cache_entry.score

                # Get official submissions for this task from the managing participation
                task_submissions = (
                    self.sql_session.query(Submission)
                    .filter(Submission.participation_id == managing_participation.id)
                    .filter(Submission.task_id == task_id)
                    .filter(Submission.training_day_id == training_day.id)
                    .filter(Submission.official.is_(True))
                    .order_by(Submission.timestamp)
                    .all()
                )

                # If student missed but has submissions, this is an error
                if student_missed and task_submissions:
                    raise ValueError(
                        f"User {participation.user.username} (id={participation.user_id}) "
                        f"has no starting_time but has {len(task_submissions)} submission(s) "
                        f"for task '{task.name}' in training day '{training_day.name}'"
                    )

                submissions[str(task_id)] = []
                for sub in task_submissions:
                    result = sub.get_result()
                    if result is None or not result.scored():
                        continue

                    if sub.timestamp is not None:
                        time_offset = int(
                            (
                                sub.timestamp - participation.starting_time
                            ).total_seconds()
                        )
                    else:
                        time_offset = 0

                    submissions[str(task_id)].append({
                        "task": str(task_id),
                        "time": time_offset,
                        "score": result.score,
                        "token": sub.tokened(),
                        "extra": result.ranking_score_details or [],
                    })

            # Get score history in RWS format: [[user_id, task_id, time, score], ...]
            # Score history is stored on the training day participation
            history: list[list] = []
            score_histories = (
                self.sql_session.query(ScoreHistory)
                .filter(ScoreHistory.participation_id == participation.id)
                .filter(ScoreHistory.task_id.in_(training_day_task_ids))
                .order_by(ScoreHistory.timestamp)
                .all()
            )

            # If student missed but has score history, this is an error
            if student_missed and score_histories:
                raise ValueError(
                    f"User {participation.user.username} (id={participation.user_id}) "
                    f"has no starting_time but has {len(score_histories)} score history "
                    f"record(s) in training day '{training_day.name}'"
                )

            for sh in score_histories:
                if sh.timestamp is not None:
                    time_offset = (
                        sh.timestamp - participation.starting_time
                    ).total_seconds()
                else:
                    time_offset = 0
                history.append([
                    participation.user_id,
                    sh.task_id,
                    time_offset,
                    sh.score
                ])

            # Create archived ranking record
            archived_ranking = ArchivedStudentRanking(
                student_tags=student_tags,
                task_scores=task_scores if task_scores else None,
                submissions=submissions if submissions else None,
                history=history if history else None,
            )
            archived_ranking.training_day_id = training_day.id
            archived_ranking.student_id = student.id
            self.sql_session.add(archived_ranking)


class TrainingProgramFilterMixin:
    """Mixin for filtering training days by date range, types, and student tags."""

    def _parse_date_range(self) -> tuple[dt | None, dt | None]:
        """Parse start_date and end_date query arguments."""
        start_date = None
        end_date = None
        start_str = self.get_argument("start_date", None)
        end_str = self.get_argument("end_date", None)

        if start_str:
            try:
                start_date = dt.fromisoformat(start_str)
            except ValueError:
                pass

        if end_str:
            try:
                end_date = dt.fromisoformat(end_str)
            except ValueError:
                pass

        return start_date, end_date

    def _parse_training_day_types(self) -> list[str]:
        """Parse training_day_types query argument."""
        types_str = self.get_argument("training_day_types", "")
        if not types_str:
            return []
        return parse_tags(types_str)

    def _parse_student_tags_filter(self) -> tuple[list[str], str]:
        """Parse student_tags and student_tags_mode query arguments.

        Returns:
            tuple of (student_tags list, filter_mode string)
            filter_mode is either "current" or "historical"
        """
        tags_str = self.get_argument("student_tags", "")
        mode = self.get_argument("student_tags_mode", "current")
        if mode not in ("current", "historical"):
            mode = "current"
        if not tags_str:
            return [], mode
        return parse_tags(tags_str), mode

    def _get_archived_training_days(
        self,
        training_program_id: int,
        start_date: dt | None,
        end_date: dt | None,
        training_day_types: list[str] | None = None,
    ) -> list[TrainingDay]:
        """Query archived training days with optional date and type filtering."""
        query = (
            self.sql_session.query(TrainingDay)
            .filter(TrainingDay.training_program_id == training_program_id)
            .filter(TrainingDay.contest_id.is_(None))
        )
        if start_date:
            query = query.filter(TrainingDay.start_time >= start_date)
        if end_date:
            # Add one day to end_date to include the entire end day
            query = query.filter(TrainingDay.start_time < end_date + timedelta(days=1))
        if training_day_types:
            # Filter training days that have ALL specified types
            query = query.filter(
                TrainingDay.training_day_types.contains(training_day_types)
            )
        return query.order_by(TrainingDay.start_time).all()

    def _tags_match(self, item_tags: list[str] | None, filter_tags: list[str]) -> bool:
        """Check if item_tags contains all filter_tags."""
        return all(tag in (item_tags or []) for tag in filter_tags)

    def _get_student_ids_with_tags(self, students, filter_tags: list[str]) -> set[int]:
        """Return IDs of students that have all filter_tags."""
        return {s.id for s in students if self._tags_match(s.student_tags, filter_tags)}

    def _get_filtered_context(self, training_program):
        """Parse common arguments and retrieve archived training days."""
        start_date, end_date = self._parse_date_range()
        training_day_types = self._parse_training_day_types()
        student_tags, student_tags_mode = self._parse_student_tags_filter()

        archived_training_days = self._get_archived_training_days(
            training_program.id, start_date, end_date, training_day_types
        )

        # Build a set of students with matching current tags
        current_tag_student_ids = self._get_student_ids_with_tags(
            training_program.students, student_tags
        )

        return (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        )


class TrainingProgramAttendanceHandler(TrainingProgramFilterMixin, BaseHandler):
    """Display attendance data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            _,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        # Build attendance data structure
        # {student_id: {training_day_id: attendance_record}}
        attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
        all_students: dict[int, Student] = {}

        for td in archived_training_days:
            for attendance in td.archived_attendances:
                student_id = attendance.student_id
                # Apply student tag filter (current tags only)
                if student_tags and student_id not in current_tag_student_ids:
                    continue
                # Skip hidden users
                student = attendance.student
                if student.participation and student.participation.hidden:
                    continue
                if student_id not in attendance_data:
                    attendance_data[student_id] = {}
                    all_students[student_id] = student
                attendance_data[student_id][td.id] = attendance

        # Sort students by username
        sorted_students = sorted(
            all_students.values(),
            key=lambda s: s.participation.user.username if s.participation else ""
        )

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["archived_training_days"] = archived_training_days
        self.r_params["attendance_data"] = attendance_data
        self.r_params["sorted_students"] = sorted_students
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)
        self.r_params["all_student_tags"] = get_all_student_tags(training_program)
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == training_program.managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.render("training_program_attendance.html", **self.r_params)


class TrainingProgramCombinedRankingHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Display combined ranking data for all archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        ranking_data: dict[int, dict[int, ArchivedStudentRanking]] = {}
        all_students: dict[int, Student] = {}
        training_day_tasks: dict[int, list[dict]] = {}
        # Attendance data: {student_id: {training_day_id: ArchivedAttendance}}
        attendance_data: dict[int, dict[int, ArchivedAttendance]] = {}
        # Track which students are "active" (have matching tags) for each training day
        # For historical mode: student had matching tags during that training
        # For current mode: student has matching tags now AND participated in that training
        active_students_per_td: dict[int, set[int]] = {}

        filtered_training_days: list[TrainingDay] = []

        for td in archived_training_days:
            active_students_per_td[td.id] = set()

            # Build attendance lookup for this training day
            for attendance in td.archived_attendances:
                student_id = attendance.student_id
                if student_id not in attendance_data:
                    attendance_data[student_id] = {}
                attendance_data[student_id][td.id] = attendance

            # Collect all tasks that were visible to at least one filtered student
            # Use archived_tasks_data from training day (preserves original scoring scheme)
            visible_tasks_by_id: dict[int, dict] = {}
            for ranking in td.archived_student_rankings:
                student_id = ranking.student_id

                # Skip hidden users
                student = ranking.student
                if student.participation and student.participation.hidden:
                    continue

                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        # Filter by current tags: student must have matching tags now
                        if student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        # Filter by historical tags: student must have had matching tags
                        # during this specific training day
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue

                # Student passes the filter for this training day
                active_students_per_td[td.id].add(student_id)

                if student_id not in ranking_data:
                    ranking_data[student_id] = {}
                    all_students[student_id] = student
                ranking_data[student_id][td.id] = ranking

                # Collect all visible tasks from this student's task_scores keys
                if ranking.task_scores:
                    for task_id_str in ranking.task_scores.keys():
                        task_id = int(task_id_str)
                        if task_id not in visible_tasks_by_id:
                            # Get task info from archived_tasks_data on training day
                            if td.archived_tasks_data and task_id_str in td.archived_tasks_data:
                                task_info = td.archived_tasks_data[task_id_str]
                                visible_tasks_by_id[task_id] = {
                                    "id": task_id,
                                    "name": task_info.get("short_name", ""),
                                    "title": task_info.get("name", ""),
                                    "training_day_num": task_info.get("training_day_num"),
                                }
                            else:
                                # Fallback to live task data
                                task = self.sql_session.query(Task).get(task_id)
                                if task:
                                    visible_tasks_by_id[task_id] = {
                                        "id": task_id,
                                        "name": task.name,
                                        "title": task.title,
                                        "training_day_num": task.training_day_num,
                                    }

            # Omit training days where no filtered students were eligible
            if not active_students_per_td[td.id]:
                continue

            filtered_training_days.append(td)

            # Sort tasks by training_day_num for stable ordering
            sorted_tasks = sorted(
                visible_tasks_by_id.values(),
                key=lambda t: (t.get("training_day_num") or 0, t["id"])
            )
            training_day_tasks[td.id] = sorted_tasks

        sorted_students = sorted(
            all_students.values(),
            key=lambda s: s.participation.user.username if s.participation else ""
        )

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["archived_training_days"] = filtered_training_days
        self.r_params["ranking_data"] = ranking_data
        self.r_params["sorted_students"] = sorted_students
        self.r_params["training_day_tasks"] = training_day_tasks
        self.r_params["attendance_data"] = attendance_data
        self.r_params["active_students_per_td"] = active_students_per_td
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["student_tags_mode"] = student_tags_mode
        self.r_params["all_training_day_types"] = get_all_training_day_types(
            training_program)
        self.r_params["all_student_tags"] = get_all_student_tags_with_historical(
            training_program)
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == training_program.managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.render("training_program_combined_ranking.html", **self.r_params)


class TrainingProgramCombinedRankingHistoryHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Return score history for archived training days as JSON."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        import json

        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            _start_date,
            _end_date,
            _training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        result = []
        for td in archived_training_days:
            for ranking in td.archived_student_rankings:
                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        if ranking.student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue

                if ranking.history:
                    for entry in ranking.history:
                        result.append([
                            str(entry[0]),
                            str(entry[1]),
                            int(entry[2]),
                            entry[3]
                        ])

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))


class TrainingProgramCombinedRankingDetailHandler(
    TrainingProgramFilterMixin, BaseHandler
):
    """Show detailed score/rank progress for a student across archived training days."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str, student_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        student = self.safe_get_item(Student, student_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        # For historical mode, we need to track which students are active per training day
        # to compute the correct user_count for relative ranks
        active_students_per_td: dict[int, set[int]] = {}
        if student_tags and student_tags_mode == "historical":
            for td in archived_training_days:
                active_students_per_td[td.id] = set()
                for ranking in td.archived_student_rankings:
                    if self._tags_match(ranking.student_tags, student_tags):
                        active_students_per_td[td.id].add(ranking.student_id)

        # Build users_data for filtered students only
        users_data = {}
        filtered_student_ids: set[int] = set()
        for s in training_program.students:
            if s.participation and s.participation.user:
                # Apply student tag filter for current mode
                if student_tags and student_tags_mode == "current":
                    if s.id not in current_tag_student_ids:
                        continue
                # For historical mode, include student if they appear in any training day
                elif student_tags and student_tags_mode == "historical":
                    if not any(s.id in active_students_per_td.get(td.id, set())
                               for td in archived_training_days):
                        continue
                filtered_student_ids.add(s.id)
                users_data[str(s.participation.user_id)] = {
                    "f_name": s.participation.user.first_name or "",
                    "l_name": s.participation.user.last_name or "",
                }

        user_count = len(users_data)

        contests_data: dict[str, dict] = {}
        tasks_data: dict[str, dict] = {}
        submissions_data: dict[str, list] = {}
        total_max_score = 0.0

        # Find the student's ranking records to get their submissions
        student_rankings: dict[int, ArchivedStudentRanking] = {}
        for td in archived_training_days:
            for ranking in td.archived_student_rankings:
                if ranking.student_id == student.id:
                    student_rankings[td.id] = ranking
                    break

        for td in archived_training_days:
            contest_key = f"td_{td.id}"
            task_ids_in_contest: set[int] = set()

            # Collect all visible task IDs from filtered students' task_scores keys
            for ranking in td.archived_student_rankings:
                # Apply student tag filter
                if student_tags:
                    if student_tags_mode == "current":
                        if ranking.student_id not in current_tag_student_ids:
                            continue
                    else:  # historical mode
                        if not self._tags_match(ranking.student_tags, student_tags):
                            continue
                if ranking.task_scores:
                    task_ids_in_contest.update(int(k) for k in ranking.task_scores.keys())

            # Get archived_tasks_data from training day
            archived_tasks_data = td.archived_tasks_data or {}

            # Sort task IDs by training_day_num for stable ordering
            def get_training_day_num(task_id: int) -> tuple[int, int]:
                task_key = str(task_id)
                if task_key in archived_tasks_data:
                    num = archived_tasks_data[task_key].get("training_day_num")
                    return (num if num is not None else 0, task_id)
                return (0, task_id)

            sorted_task_ids = sorted(task_ids_in_contest, key=get_training_day_num)

            contest_tasks = []
            contest_max_score = 0.0
            for task_id in sorted_task_ids:
                task_key = str(task_id)

                # Use archived_tasks_data if available (preserves original scoring scheme)
                if task_key in archived_tasks_data:
                    task_info = archived_tasks_data[task_key]
                    max_score = task_info.get("max_score", 100.0)
                    extra_headers = task_info.get("extra_headers", [])
                    score_precision = task_info.get("score_precision", 2)
                    task_name = task_info.get("name", "")
                    task_short_name = task_info.get("short_name", "")
                else:
                    # Fallback to live task data
                    task = self.sql_session.query(Task).get(task_id)
                    if not task:
                        continue
                    max_score = 100.0
                    extra_headers = []
                    score_precision = task.score_precision
                    task_name = task.title
                    task_short_name = task.name
                    if task.active_dataset:
                        try:
                            score_type = task.active_dataset.score_type_object
                            max_score = score_type.max_score
                            extra_headers = score_type.ranking_headers
                        except (KeyError, TypeError, AttributeError):
                            pass

                tasks_data[task_key] = {
                    "key": task_key,
                    "name": task_name,
                    "short_name": task_short_name,
                    "contest": contest_key,
                    "max_score": max_score,
                    "score_precision": score_precision,
                    "extra_headers": extra_headers,
                }
                contest_tasks.append(tasks_data[task_key])
                contest_max_score += max_score

                # Get submissions for this task from the student's ranking
                student_ranking = student_rankings.get(td.id)
                if student_ranking and student_ranking.submissions:
                    task_submissions = student_ranking.submissions.get(task_key, [])
                    submissions_data[task_key] = task_submissions

            td_name = td.description or td.name or "Training Day"
            if td.start_time:
                td_name += f" ({td.start_time.strftime('%Y-%m-%d')})"

            # Calculate contest duration
            # History times are stored as offsets from contest start, so we need
            # begin=0 and end=duration for the graph scale to be correct
            if td.duration:
                end_time = int(td.duration.total_seconds())
            else:
                end_time = 18000  # Default 5 hours

            contests_data[contest_key] = {
                "key": contest_key,
                "name": td_name,
                "begin": 0,
                "end": end_time,
                "max_score": contest_max_score,
                "score_precision": 2,
                "tasks": contest_tasks,
            }
            total_max_score += contest_max_score

        contest_list = [contests_data[f"td_{td.id}"] for td in archived_training_days
                        if f"td_{td.id}" in contests_data]

        history_url = self.url(
            "training_program", training_program_id, "combined_ranking", "history"
        )
        if start_date or end_date or training_day_types or student_tags:
            params = {}
            if start_date:
                params["start_date"] = start_date.isoformat()
            if end_date:
                params["end_date"] = end_date.isoformat()
            if training_day_types:
                params["training_day_types"] = ",".join(training_day_types)
            if student_tags:
                params["student_tags"] = ",".join(student_tags)
                params["student_tags_mode"] = student_tags_mode
            history_url += "?" + urlencode(params)

        self.r_params = self.render_params()
        self.r_params["training_program"] = training_program
        self.r_params["student"] = student
        self.r_params["user_id"] = str(student.participation.user_id) if student.participation else "0"
        self.r_params["user_count"] = user_count
        self.r_params["users_data"] = users_data
        self.r_params["tasks_data"] = tasks_data
        self.r_params["submissions_data"] = submissions_data
        self.r_params["contests_data"] = contests_data
        self.r_params["contest_list"] = contest_list
        self.r_params["total_max_score"] = total_max_score
        self.r_params["history_url"] = history_url
        self.r_params["start_date"] = start_date
        self.r_params["end_date"] = end_date
        self.r_params["training_day_types"] = training_day_types
        self.r_params["student_tags"] = student_tags
        self.r_params["student_tags_mode"] = student_tags_mode
        self.r_params["unanswered"] = self.sql_session.query(Question)\
            .join(Participation)\
            .filter(Participation.contest_id == training_program.managing_contest.id)\
            .filter(Question.reply_timestamp.is_(None))\
            .filter(Question.ignored.is_(False))\
            .count()
        self.render("training_program_combined_ranking_detail.html", **self.r_params)


class TrainingProgramOverviewRedirectHandler(BaseHandler):
    """Redirect /training_program/{id}/overview to the managing contest's overview page."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        self.redirect(
            self.url("contest", training_program.managing_contest.id, "overview")
        )


class TrainingProgramResourcesListRedirectHandler(BaseHandler):
    """Redirect /training_program/{id}/resourceslist to the managing contest's resourceslist page."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        training_program = self.safe_get_item(TrainingProgram, training_program_id)
        self.redirect(
            self.url("contest", training_program.managing_contest.id, "resourceslist")
        )
