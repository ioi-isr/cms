#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright Ac 2025 CMS developers <dev@cms>
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

"""Handlers for training program administration pages."""

from cms.db import Contest, TrainingProgram
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


class TrainingProgramListHandler(BaseHandler):
    """Display all training programs."""

    REMOVE = "Delete"

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        programs = (
            self.sql_session.query(TrainingProgram)
            .order_by(TrainingProgram.name)
            .all()
        )

        self.r_params = self.render_params()
        self.r_params["training_programs"] = programs
        self.render("training_programs.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        operation = self.get_argument("operation", "").strip()
        program_id = self.get_argument("training_program_id", None)

        if operation == self.REMOVE and program_id is not None:
            asking_page = self.url("training_programs", program_id, "remove")
            self.redirect(asking_page)
            return

        self.service.add_notification(
            make_datetime(),
            "Invalid operation %s" % operation,
            "",
        )
        self.redirect(self.url("training_programs"))




class RemoveTrainingProgramHandler(BaseHandler):
    """Ask confirmation and remove a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)

        self.r_params = self.render_params()
        self.r_params["training_program"] = program
        self.r_params["assigned_contests"] = list(program.contests)
        self.render("training_program_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)

        for contest in list(program.contests):
            contest.training_program = None
            contest.training_program_role = None

        self.sql_session.delete(program)

        redirect_target = "../../training_programs"
        self.try_commit()
        self.write(redirect_target)


class AddTrainingProgramHandler(BaseHandler):
    """Create a new training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.render("add_training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        name = self.get_argument("name", "").strip()
        title = self.get_argument("title", "").strip()

        if not name or not title:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "Name and title are required.",
            )
            self.redirect(self.url("training_programs", "add"))
            return

        existing = (
            self.sql_session.query(TrainingProgram)
            .filter(TrainingProgram.name == name)
            .first()
        )
        if existing is not None:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "A training program with this name already exists.",
            )
            self.redirect(self.url("training_programs", "add"))
            return

        program = TrainingProgram(name=name, title=title)
        self.sql_session.add(program)

        if self.try_commit():
            self.redirect(self.url("training_program", program.id))
        else:
            self.redirect(self.url("training_programs", "add"))


class TrainingProgramHandler(BaseHandler):
    """View and edit a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)
        contests = self.sql_session.query(Contest).order_by(Contest.name).all()

        self.r_params = self.render_params()
        self.r_params["training_program"] = program
        self.r_params["contests"] = contests
        self.render("training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)

        new_name = self.get_argument("name", "").strip()
        new_title = self.get_argument("title", "").strip()
        if not new_name or not new_title:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "Name and title are required.",
            )
            self.redirect(self.url("training_program", program.id))
            return

        existing = (
            self.sql_session.query(TrainingProgram)
            .filter(TrainingProgram.name == new_name)
            .filter(TrainingProgram.id != program.id)
            .first()
        )
        if existing is not None:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "A training program with this name already exists.",
            )
            self.redirect(self.url("training_program", program.id))
            return

        program.name = new_name
        program.title = new_title

        regular_arg = self.get_argument("regular_contest", "").strip()
        home_arg = self.get_argument("home_contest", "").strip()

        regular_contest = None
        home_contest = None

        if regular_arg:
            regular_contest = self.safe_get_item(Contest, regular_arg)
        if home_arg:
            home_contest = self.safe_get_item(Contest, home_arg)

        if (
            regular_contest is not None
            and home_contest is not None
            and regular_contest.id == home_contest.id
        ):
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "Regular and home contests must be different.",
            )
            self.redirect(self.url("training_program", program.id))
            return

        try:
            program.regular_contest = regular_contest
            program.home_contest = home_contest
        except ValueError as error:
            self.sql_session.rollback()
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                str(error),
            )
            self.redirect(self.url("training_program", program.id))
            return

        self.try_commit()
        self.redirect(self.url("training_program", program.id))
