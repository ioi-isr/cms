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

"""Handlers for training program task management."""

from cms.db import Task, TrainingProgram
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


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
