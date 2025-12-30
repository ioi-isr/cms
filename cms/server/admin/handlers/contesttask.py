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
            # For training days, show tasks from the training program's
            # managing contest that are not already assigned to any training day
            training_program = training_day.training_program
            self.r_params["unassigned_tasks"] = \
                self.sql_session.query(Task)\
                    .filter(Task.contest_id == training_program.managing_contest_id)\
                    .filter(Task.training_day_id.is_(None))\
                    .order_by(Task.num)\
                    .all()
            
            # Get all student tags for autocomplete (for task visibility tags)
            all_tags_set: set[str] = set()
            for student in training_program.students:
                all_tags_set.update(student.student_tags)
            self.r_params["all_student_tags"] = sorted(all_tags_set)
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

        if training_day is not None:
            # For training days, use training_day_num for ordering
            # (task.num is used for contest ordering and should not be modified)
            task_num = task.training_day_num

            if operation in (self.REMOVE_FROM_CONTEST, self.REMOVE_FROM_TRAINING_DAY):
                # Unassign the task from the training day.
                task.training_day = None
                task.training_day_num = None

                self.sql_session.flush()

                # Decrease by 1 the training_day_num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.training_day_num > task_num)\
                             .order_by(Task.training_day_num)\
                             .all():
                    t.training_day_num -= 1
                    self.sql_session.flush()

            elif operation == self.MOVE_UP:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.training_day == training_day)\
                            .filter(Task.training_day_num == task.training_day_num - 1)\
                            .first()

            elif operation == self.MOVE_DOWN:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.training_day == training_day)\
                            .filter(Task.training_day_num == task.training_day_num + 1)\
                            .first()

            elif operation == self.MOVE_TOP:
                task.training_day_num = None
                self.sql_session.flush()

                # Increase by 1 the training_day_num of every previous task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.training_day_num < task_num)\
                             .order_by(Task.training_day_num.desc())\
                             .all():
                    t.training_day_num += 1
                    self.sql_session.flush()

                task.training_day_num = 0

            elif operation == self.MOVE_BOTTOM:
                task.training_day_num = None
                self.sql_session.flush()

                # Decrease by 1 the training_day_num of every subsequent task.
                for t in self.sql_session.query(Task)\
                             .filter(Task.training_day == training_day)\
                             .filter(Task.training_day_num > task_num)\
                             .order_by(Task.training_day_num)\
                             .all():
                    t.training_day_num -= 1
                    self.sql_session.flush()

                self.sql_session.flush()
                task.training_day_num = len(training_day.tasks) - 1

            # Swap training_day_num values, if needed
            if task2 is not None:
                tmp_a, tmp_b = task.training_day_num, task2.training_day_num
                task.training_day_num, task2.training_day_num = None, None
                self.sql_session.flush()
                task.training_day_num, task2.training_day_num = tmp_b, tmp_a
        else:
            # For regular contests, use task.num for ordering
            task_num = task.num

            if operation in (self.REMOVE_FROM_CONTEST, self.REMOVE_FROM_TRAINING_DAY):
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
                task2 = self.sql_session.query(Task)\
                            .filter(Task.contest == self.contest)\
                            .filter(Task.num == task.num - 1)\
                            .first()

            elif operation == self.MOVE_DOWN:
                task2 = self.sql_session.query(Task)\
                            .filter(Task.contest == self.contest)\
                            .filter(Task.num == task.num + 1)\
                            .first()

            elif operation == self.MOVE_TOP:
                task.num = None
                self.sql_session.flush()

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

            # Swap task.num values, if needed
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
            assert task_id != "", "Please select a valid task"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        task = self.safe_get_item(Task, task_id)

        if training_day is not None:
            # Assign the task to the training day.
            # Task keeps its contest_id (managing contest) and gets training_day_id set.
            # Use training_day_num for ordering within the training day.
            task.training_day_num = len(training_day.tasks)
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


class TaskVisibilityHandler(BaseHandler):
    """Handler for updating task visibility tags via AJAX."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, contest_id, task_id):
        self.contest = self.safe_get_item(Contest, contest_id)
        task = self.safe_get_item(Task, task_id)

        # Verify this contest is a training day
        training_day = self.contest.training_day
        if training_day is None:
            self.set_status(400)
            self.write({"error": "This contest is not a training day"})
            return

        # Verify the task belongs to this training day
        if task.training_day_id != training_day.id:
            self.set_status(400)
            self.write({"error": "Task does not belong to this training day"})
            return

        try:
            visible_to_tags_str = self.get_argument("visible_to_tags", "")
            visible_to_tags = [tag.strip() for tag in visible_to_tags_str.split(",") if tag.strip()]

            # Remove duplicates while preserving order
            seen: set[str] = set()
            unique_tags: list[str] = []
            for tag in visible_to_tags:
                if tag not in seen:
                    seen.add(tag)
                    unique_tags.append(tag)

            task.visible_to_tags = unique_tags

            if self.try_commit():
                self.write({"success": True, "tags": unique_tags})
            else:
                self.set_status(500)
                self.write({"error": "Failed to save"})

        except Exception as error:
            self.set_status(400)
            self.write({"error": str(error)})
