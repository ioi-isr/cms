#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
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

"""Task-related handlers for AWS for a specific contest.

"""

from cms.db import Contest, Task
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


class ContestTasksHandler(BaseHandler):
    REMOVE_FROM_CONTEST = "Remove from contest"
    REMOVE_FROM_TRAINING_DAY = "Remove from training day"
    MOVE_UP = "up by 1"
    MOVE_DOWN = "down by 1"
    MOVE_TOP = "to the top"
    MOVE_BOTTOM = "to the bottom"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest

        # Check if this contest is a training day
        training_day = self.contest.training_day
        self.r_params["is_training_day"] = training_day is not None

        if training_day is not None:
            # For training days, show tasks from the training program
            # that are not already assigned to this training day
            training_program = training_day.training_program
            assigned_task_ids = {t.id for t in training_day.tasks}
            self.r_params["unassigned_tasks"] = [
                t for t in training_program.managing_contest.tasks
                if t.id not in assigned_task_ids
            ]
        else:
            # For regular contests, show all unassigned tasks
            self.r_params["unassigned_tasks"] = \
                self.sql_session.query(Task)\
                    .filter(Task.contest_id.is_(None))\
                    .filter(Task.training_day_id.is_(None))\
                    .all()
        self.render("contest_tasks.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id):
        fallback_page = self.url("contest", contest_id, "tasks")

        self.contest = self.safe_get_item(Contest, contest_id)
        training_day = self.contest.training_day

        try:
            task_id: str = self.get_argument("task_id")
            operation: str = self.get_argument("operation")
            valid_operations = [
                self.REMOVE_FROM_CONTEST,
                self.REMOVE_FROM_TRAINING_DAY,
                self.MOVE_UP,
                self.MOVE_DOWN,
                self.MOVE_TOP,
                self.MOVE_BOTTOM
            ]
            assert operation in valid_operations, "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)
        task2 = None

        # Save the current task_num (position in the contest/training day).
        task_num = task.num

        if operation in (self.REMOVE_FROM_CONTEST, self.REMOVE_FROM_TRAINING_DAY):
            if training_day is not None:
                # Unassign the task from the training day.
                task.training_day = None
                task.num = None

                self.sql_session.flush()

                # Decrease by 1 the num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.num > task_num)\
                             .order_by(Task.num)\
                             .all():
                    t.num -= 1
                    self.sql_session.flush()
            else:
                # Unassign the task from the contest.
                task.contest = None
                task.num = None

                self.sql_session.flush()

                # Decrease by 1 the num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.contest == self.contest)\
                             .filter(Task.num > task_num)\
                             .order_by(Task.num)\
                             .all():
                    t.num -= 1
                    self.sql_session.flush()

        elif operation == self.MOVE_UP:
            if training_day is not None:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.training_day == training_day)\
                            .filter(Task.num == task.num - 1)\
                            .first()
            else:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.contest == self.contest)\
                            .filter(Task.num == task.num - 1)\
                            .first()

        elif operation == self.MOVE_DOWN:
            if training_day is not None:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.training_day == training_day)\
                            .filter(Task.num == task.num + 1)\
                            .first()
            else:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.contest == self.contest)\
                            .filter(Task.num == task.num + 1)\
                            .first()

        elif operation == self.MOVE_TOP:
            task.num = None
            self.sql_session.flush()

            if training_day is not None:
                # Increase by 1 the num of every previous task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.num < task_num)\
                             .order_by(Task.num.desc())\
                             .all():
                    t.num += 1
                    self.sql_session.flush()
            else:
                # Increase by 1 the num of every previous task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.contest == self.contest)\
                             .filter(Task.num < task_num)\
                             .order_by(Task.num.desc())\
                             .all():
                    t.num += 1
                    self.sql_session.flush()

            task.num = 0

        elif operation == self.MOVE_BOTTOM:
            task.num = None
            self.sql_session.flush()

            if training_day is not None:
                # Decrease by 1 the num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.num > task_num)\
                             .order_by(Task.num)\
                             .all():
                    t.num -= 1
                    self.sql_session.flush()

                self.sql_session.flush()
                task.num = len(training_day.tasks) - 1
            else:
                # Decrease by 1 the num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.contest == self.contest)\
                             .filter(Task.num > task_num)\
                             .order_by(Task.num)\
                             .all():
                    t.num -= 1
                    self.sql_session.flush()

                self.sql_session.flush()
                task.num = len(self.contest.tasks) - 1

        # Swap task.num and task2.num, if needed
        if task2 is not None:
            tmp_a, tmp_b = task.num, task2.num
            task.num, task2.num = None, None
            self.sql_session.flush()
            task.num, task2.num = tmp_b, tmp_a

        if self.try_commit():
            # Create the user on RWS.
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another task)
        self.redirect(fallback_page)


class AddContestTaskHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id):
        fallback_page = self.url("contest", contest_id, "tasks")

        self.contest = self.safe_get_item(Contest, contest_id)
        training_day = self.contest.training_day

        try:
            task_id: str = self.get_argument("task_id")
            # Check that the admin selected some task.
            assert task_id != "null", "Please select a valid task"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

        if training_day is not None:
            # Assign the task to the training day.
            task.num = len(training_day.tasks)
            task.training_day = training_day
        else:
            # Assign the task to the contest.
            task.num = len(self.contest.tasks)
            task.contest = self.contest

        if self.try_commit():
            # Create the user on RWS.
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another task)
        self.redirect(fallback_page)
