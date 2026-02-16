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
from cms.server.admin.handlers.utils import get_all_student_tags, deduplicate_preserving_order
from cms.server.admin.handlers.trainingprogramtask import (
    reorder_tasks,
    _shift_task_nums,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


class ContestTasksHandler(BaseHandler):
    REORDER = "reorder"
    REMOVE = "remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        self.r_params = self.render_params()
        self.r_params["contest"] = self.contest

        training_day = self.contest.training_day
        self.r_params["is_training_day"] = training_day is not None

        if training_day is not None:
            training_program = training_day.training_program

            program_tasks = self.sql_session.query(Task)\
                .filter(Task.contest_id == training_program.managing_contest_id)\
                .filter(Task.training_day_id.is_(None))\
                .order_by(Task.num)\
                .all()

            other_tasks = self.sql_session.query(Task)\
                .filter(Task.contest_id.is_(None))\
                .filter(Task.training_day_id.is_(None))\
                .order_by(Task.name)\
                .all()

            self.r_params["unassigned_tasks"] = program_tasks + other_tasks
            self.r_params["program_task_ids"] = [t.id for t in program_tasks]

            self.r_params["all_student_tags"] = get_all_student_tags(
                self.sql_session, training_program
            )
        else:
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
            operation: str = self.get_argument("operation")

            if operation == self.REORDER:
                reorder_data = self.get_argument("reorder_data", "")
                if reorder_data:
                    if training_day is not None:
                        reorder_tasks(
                            self.sql_session,
                            list(training_day.tasks),
                            reorder_data,
                            "training_day_num",
                        )
                    else:
                        reorder_tasks(
                            self.sql_session,
                            list(self.contest.tasks),
                            reorder_data,
                            "num",
                        )
                    if self.try_commit():
                        self.service.proxy_service.reinitialize()
                self.redirect(fallback_page)
                return

            if operation == self.REMOVE:
                task_id: str = self.get_argument("task_id")
                task = self.safe_get_item(Task, task_id)

                if training_day is not None:
                    if task.training_day_id != training_day.id:
                        self.service.add_notification(
                            make_datetime(),
                            "Invalid task",
                            "Task does not belong to this training day",
                        )
                        self.redirect(fallback_page)
                        return
                    task_num = task.training_day_num
                    task.training_day = None
                    task.training_day_num = None
                    self.sql_session.flush()
                    if task_num is not None:
                        _shift_task_nums(
                            self.sql_session,
                            Task.training_day, training_day,
                            Task.training_day_num, task_num, -1,
                        )
                else:
                    if task.contest_id != self.contest.id:
                        self.service.add_notification(
                            make_datetime(),
                            "Invalid task",
                            "Task does not belong to this contest",
                        )
                        self.redirect(fallback_page)
                        return
                    task_num = task.num
                    task.contest = None
                    task.num = None
                    self.sql_session.flush()
                    if task_num is not None:
                        _shift_task_nums(
                            self.sql_session,
                            Task.contest, self.contest,
                            Task.num, task_num, -1,
                        )

                if self.try_commit():
                    self.service.proxy_service.reinitialize()
                self.redirect(fallback_page)
                return
            else:
                raise ValueError(f"Unknown operation: {operation}")

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return


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
            training_program = training_day.training_program

            # Check if task is not in the training program
            if task.contest_id != training_program.managing_contest_id:
                # Add the task to the training program's managing contest first
                managing_contest = training_program.managing_contest
                task.num = len(managing_contest.tasks)
                task.contest = managing_contest

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

        # Capture original tags before modifying to return correct state on error
        original_tags = task.visible_to_tags or []

        try:
            visible_to_tags_str = self.get_argument("visible_to_tags", "")
            incoming_tags = [
                tag.strip() for tag in visible_to_tags_str.split(",") if tag.strip()
            ]

            # Get allowed tags from training program
            training_program = training_day.training_program
            allowed_tags = set(get_all_student_tags(
                self.sql_session, training_program
            ))

            # Validate and filter tags against allowed set
            invalid_tags = [tag for tag in incoming_tags if tag not in allowed_tags]
            valid_tags = [tag for tag in incoming_tags if tag in allowed_tags]

            # Return error if there are invalid tags
            if invalid_tags:
                self.set_status(400)
                self.write(
                    {
                        "error": f"Invalid tags: {', '.join(invalid_tags)}",
                        "tags": task.visible_to_tags or [],
                        "invalid_tags": invalid_tags,
                    }
                )
                return

            # Remove duplicates while preserving order
            unique_tags = deduplicate_preserving_order(valid_tags)

            task.visible_to_tags = unique_tags

            if self.try_commit():
                response_data = {
                    "success": True,
                    "tags": unique_tags,
                }
                self.write(response_data)
            else:
                self.set_status(500)
                self.write(
                    {"error": "Failed to save", "tags": original_tags}
                )

        except (ValueError, KeyError) as error:
            self.set_status(400)
            self.write({"error": str(error), "tags": original_tags})
