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

from cms.db import Contest, TrainingProgram, TrainingProgramParticipation
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

        self.training_program = program

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

        self.training_program = program

        self.r_params = self.render_params()
        self.r_params["training_program"] = program
        self.r_params["contests"] = contests
        self.render("training_program.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)

        self.training_program = program

        previous_regular_contest = program.regular_contest
        previous_home_contest = program.home_contest

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

        def collect_task_moves() -> list[dict]:
            moves: list[dict] = []
            for label, contest in (
                ("Regular contest", regular_contest),
                ("Home contest", home_contest),
            ):
                if contest is None:
                    continue
                contest_tasks = sorted(
                    contest.tasks,
                    key=lambda task: task.num if task.num is not None else 0,
                )
                if contest_tasks:
                    moves.append({
                        "label": label,
                        "contest": contest,
                        "tasks": contest_tasks,
                    })
            return moves

        def collect_participation_losses() -> tuple[list[dict], dict]:
            entries: list[dict] = []
            totals = {
                "submissions": 0,
                "user_tests": 0,
                "messages": 0,
                "questions": 0,
                "printjobs": 0,
                "total": 0,
            }

            def append_loss(participation, role, contest):
                counts = {
                    "submissions": len(participation.submissions),
                    "user_tests": len(participation.user_tests),
                    "messages": len(participation.messages),
                    "questions": len(participation.questions),
                    "printjobs": len(participation.printjobs),
                }
                total = sum(counts.values())
                if total == 0:
                    return
                entries.append({
                    "user": participation.user,
                    "role": role,
                    "contest": contest,
                    "counts": counts,
                    "total": total,
                })
                for key, value in counts.items():
                    totals[key] += value
                totals["total"] += total

            changes = [
                (
                    "regular",
                    previous_regular_contest,
                    regular_contest,
                    lambda pp: pp.regular_participation,
                ),
                (
                    "home",
                    previous_home_contest,
                    home_contest,
                    lambda pp: pp.home_participation,
                ),
            ]

            for role, old_contest, new_contest, getter in changes:
                if old_contest is None or old_contest is new_contest:
                    continue
                for program_participation in program.training_program_participations:
                    participation = getter(program_participation)
                    append_loss(participation, role, old_contest)

            return entries, totals

        pending_moves = collect_task_moves()
        data_loss_entries, data_loss_totals = collect_participation_losses()

        regular_changed = previous_regular_contest is not regular_contest
        home_changed = previous_home_contest is not home_contest

        if (pending_moves or data_loss_entries) and self.get_argument("confirm_change", "no") != "yes":
            self.r_params = self.render_params()
            self.r_params.update({
                "training_program": program,
                "pending_moves": pending_moves,
                "data_loss_entries": data_loss_entries,
                "data_loss_totals": data_loss_totals,
                "new_name": new_name,
                "new_title": new_title,
                "regular_contest_id": regular_arg,
                "home_contest_id": home_arg,
            })
            self.render("training_program_reassign_warning.html", **self.r_params)
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

            if regular_changed and previous_regular_contest is not None:
                program.regular_contest = None
            if home_changed and previous_home_contest is not None:
                program.home_contest = None

            self.sql_session.flush([program])

            program.regular_contest = regular_contest
            program.home_contest = home_contest

            if regular_changed or home_changed:
                for program_participation in program.training_program_participations:
                    TrainingProgramParticipation.ensure(
                        self.sql_session,
                        program,
                        program_participation.user,
                    )
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

