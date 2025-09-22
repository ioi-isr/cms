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

from cms.db import Contest, Task, TrainingProgram
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
        self.r_params["managed_tasks"] = list(program.tasks)
        self.render("training_program_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)

        for contest in list(program.contests):
            contest.training_program = None
            contest.training_program_role = None

        for task in list(program.tasks):
            task.training_program = None
            task.num = None

        self.sql_session.delete(program)

        redirect_target = "../../training_programs"
        if self.try_commit():
            self.service.proxy_service.reinitialize()
        self.write(redirect_target)

class TrainingProgramTasksHandler(BaseHandler):
    """Manage tasks belonging to a training program."""

    REMOVE_FROM_PROGRAM = "Remove from training program"
    MOVE_UP = "up by 1"
    MOVE_DOWN = "down by 1"
    MOVE_TOP = "to the top"
    MOVE_BOTTOM = "to the bottom"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id):
        self.training_program = self.safe_get_item(TrainingProgram, program_id)

        self.r_params = self.render_params()
        self.r_params["training_program"] = self.training_program
        self.r_params["unassigned_tasks"] = (
            self.sql_session.query(Task)
            .filter(Task.contest_id.is_(None))
            .filter(Task.training_program_id.is_(None))
            .all()
        )
        self.render("training_program_tasks.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id):
        fallback_page = self.url("training_program", program_id, "tasks")

        self.training_program = self.safe_get_item(TrainingProgram, program_id)

        try:
            task_id: str = self.get_argument("task_id")
            operation: str = self.get_argument("operation")
            assert operation in (
                self.REMOVE_FROM_PROGRAM,
                self.MOVE_UP,
                self.MOVE_DOWN,
                self.MOVE_TOP,
                self.MOVE_BOTTOM,
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)
        if task.training_program is not self.training_program:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "Task does not belong to this training program.",
            )
            self.redirect(fallback_page)
            return

        task2 = None
        task_num = task.num

        if operation == self.REMOVE_FROM_PROGRAM:
            task.training_program = None
            task.num = None
            self.sql_session.flush()

            for t in (
                self.sql_session.query(Task)
                .filter(Task.training_program == self.training_program)
                .filter(Task.num > task_num)
                .order_by(Task.num)
                .all()
            ):
                t.num -= 1
                self.sql_session.flush()

        elif operation == self.MOVE_UP:
            task2 = (
                self.sql_session.query(Task)
                .filter(Task.training_program == self.training_program)
                .filter(Task.num == task.num - 1)
                .first()
            )

        elif operation == self.MOVE_DOWN:
            task2 = (
                self.sql_session.query(Task)
                .filter(Task.training_program == self.training_program)
                .filter(Task.num == task.num + 1)
                .first()
            )

        elif operation == self.MOVE_TOP:
            task.num = None
            self.sql_session.flush()

            for t in (
                self.sql_session.query(Task)
                .filter(Task.training_program == self.training_program)
                .filter(Task.num < task_num)
                .order_by(Task.num.desc())
                .all()
            ):
                t.num += 1
                self.sql_session.flush()

            task.num = 0

        elif operation == self.MOVE_BOTTOM:
            task.num = None
            self.sql_session.flush()

            for t in (
                self.sql_session.query(Task)
                .filter(Task.training_program == self.training_program)
                .filter(Task.num > task_num)
                .order_by(Task.num)
                .all()
            ):
                t.num -= 1
                self.sql_session.flush()

            task.num = len(self.training_program.tasks) - 1

        if task2 is not None:
            tmp_a, tmp_b = task.num, task2.num
            task.num, task2.num = None, None
            self.sql_session.flush()
            task.num, task2.num = tmp_b, tmp_a

        ordered = sorted(
            self.training_program.tasks,
            key=lambda t: t.num if t.num is not None else 0,
        )
        for idx, task in enumerate(ordered):
            task.num = idx

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class AddTrainingProgramTaskHandler(BaseHandler):
    """Assign an existing task to a training program."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id):
        fallback_page = self.url("training_program", program_id, "tasks")

        self.training_program = self.safe_get_item(TrainingProgram, program_id)

        try:
            task_id: str = self.get_argument("task_id")
            assert task_id != "null", "Please select a valid task"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

        if task.contest is not None or task.training_program is not None:
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                "Task is already assigned.",
            )
            self.redirect(fallback_page)
            return

        task.num = len(self.training_program.tasks)
        task.training_program = self.training_program

        ordered = sorted(
            self.training_program.tasks,
            key=lambda t: t.num if t.num is not None else 0,
        )
        for idx, task_obj in enumerate(ordered):
            task_obj.num = idx

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


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

        regular_contest = self.safe_get_item(Contest, regular_arg) if regular_arg else None
        home_contest = self.safe_get_item(Contest, home_arg) if home_arg else None

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

        pending_moves = []
        role_map = [
            ("Regular contest", regular_contest),
            ("Home contest", home_contest),
        ]
        for label, contest in role_map:
            if contest is None:
                continue
            contest_tasks = sorted(
                contest.tasks,
                key=lambda t: t.num if t.num is not None else 0,
            )
            if contest_tasks:
                pending_moves.append({
                    "label": label,
                    "contest": contest,
                    "tasks": contest_tasks,
                })

        confirm_move = self.get_argument("confirm_move", "no") == "yes"
        if pending_moves and not confirm_move:
            self.r_params = self.render_params()
            self.r_params["training_program"] = program
            self.r_params["pending_moves"] = pending_moves
            self.r_params["new_name"] = new_name
            self.r_params["new_title"] = new_title
            self.r_params["regular_contest_id"] = regular_arg
            self.r_params["home_contest_id"] = home_arg
            self.render("training_program_move_tasks.html", **self.r_params)
            return

        try:
            if pending_moves:
                existing_nums = [t.num for t in program.tasks if t.num is not None]
                next_num = max(existing_nums) + 1 if existing_nums else 0
                for move in pending_moves:
                    for task in move["tasks"]:
                        task.contest = None
                        task.training_program = program
                        task.num = next_num
                        next_num += 1
                ordered = sorted(
                    program.tasks,
                    key=lambda t: t.num if t.num is not None else 0,
                )
                for idx, task in enumerate(ordered):
                    task.num = idx

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

        if self.try_commit():
            self.service.proxy_service.reinitialize()
        self.redirect(self.url("training_program", program.id))


