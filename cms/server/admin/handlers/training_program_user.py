#!/usr/bin/env python3

"""Handlers for training program participants management."""

from datetime import timedelta

import tornado.web
from sqlalchemy.orm import joinedload

from cms.db import Participation, Team, TrainingProgram, TrainingProgramParticipation, User
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission


class TrainingProgramParticipantsHandler(BaseHandler):
    """Manage training program participants."""

    ADD_OPERATION = "Add"
    REMOVE_OPERATION = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id: str):
        self.training_program = self.safe_get_item(TrainingProgram, program_id)

        program_participations = (
            self.sql_session.query(TrainingProgramParticipation)
            .join(TrainingProgramParticipation.user)
            .filter(
                TrainingProgramParticipation.training_program == self.training_program
            )
            .options(
                joinedload(TrainingProgramParticipation.user),
                joinedload(TrainingProgramParticipation.participations).joinedload(
                    Participation.contest
                ),
            )
            .order_by(User.username)
            .all()
        )

        assigned_ids = [pp.user_id for pp in program_participations]
        unassigned_query = self.sql_session.query(User).order_by(User.username)
        if assigned_ids:
            unassigned_query = unassigned_query.filter(~User.id.in_(assigned_ids))
        unassigned_users = unassigned_query.all()

        self.r_params = self.render_params()
        self.r_params["training_program"] = self.training_program
        self.r_params["program_participations"] = program_participations
        self.r_params["unassigned_users"] = unassigned_users
        self.render("training_program_participants.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id: str):
        self.training_program = self.safe_get_item(TrainingProgram, program_id)

        operation = self.get_argument("operation", "").strip()
        fallback_page = self.url("training_program", program_id, "participants")

        try:
            if operation == self.ADD_OPERATION:
                user_id = self.get_argument("user_id")
                user = self.safe_get_item(User, user_id)
                TrainingProgramParticipation.ensure(
                    self.sql_session,
                    self.training_program,
                    user,
                )
                message = "Participant added"
                description = f"{user.username} is now part of the training program."

            elif operation == self.REMOVE_OPERATION:
                participation_id = self.get_argument("participation_id")
                participation = self.safe_get_item(
                    TrainingProgramParticipation,
                    participation_id,
                )
                if participation.training_program is not self.training_program:
                    raise tornado.web.HTTPError(404)
                user = participation.user

                for contest_participation in list(participation.participations):
                    self.sql_session.delete(contest_participation)

                self.sql_session.delete(participation)
                message = "Participant removed"
                description = f"{user.username} removed from the training program."
            else:
                raise ValueError("Please select a valid operation")

        except Exception as error:
            self.sql_session.rollback()
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                str(error),
            )
            self.redirect(fallback_page)
            raise
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()
            self.service.add_notification(make_datetime(), message, description)

        self.redirect(fallback_page)


class TrainingProgramParticipationHandler(BaseHandler):
    """View and edit a user's participation within a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id: str, user_id: str):
        self.training_program = self.safe_get_item(TrainingProgram, program_id)
        user = self.safe_get_item(User, user_id)

        program_participation: TrainingProgramParticipation | None = (
            self.sql_session.query(TrainingProgramParticipation)
            .options(
                joinedload(TrainingProgramParticipation.user),
                joinedload(TrainingProgramParticipation.participations).joinedload(
                    Participation.contest
                ),
                joinedload(TrainingProgramParticipation.participations).joinedload(
                    Participation.team
                ),
            )
            .filter(TrainingProgramParticipation.training_program == self.training_program)
            .filter(TrainingProgramParticipation.user == user)
            .one_or_none()
        )

        if program_participation is None:
            raise tornado.web.HTTPError(404)

        self.r_params = self.render_params()
        self.r_params.update(
            {
                "training_program": self.training_program,
                "selected_user": user,
                "program_participation": program_participation,
                "regular_participation": program_participation.regular_participation,
                "home_participation": program_participation.home_participation,
                "teams": self.sql_session.query(Team).order_by(Team.name).all(),
            }
        )
        self.render("training_program_participation.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, program_id: str, user_id: str):
        fallback_page = self.url("training_program", program_id, "user", user_id, "edit")

        self.training_program = self.safe_get_item(TrainingProgram, program_id)
        user = self.safe_get_item(User, user_id)
        program_participation: TrainingProgramParticipation | None = (
            self.sql_session.query(TrainingProgramParticipation)
            .filter(TrainingProgramParticipation.training_program == self.training_program)
            .filter(TrainingProgramParticipation.user == user)
            .one_or_none()
        )

        if program_participation is None:
            raise tornado.web.HTTPError(404)

        section = self.get_argument("section", "").strip()

        try:
            if section == "program":
                updates: dict[str, object] = {}
                self.get_datetime(updates, "starting_time", empty=None)
                self.get_timedelta_sec(updates, "delay_time", empty=timedelta())
                self.get_timedelta_sec(updates, "extra_time", empty=timedelta())

                if "starting_time" in updates:
                    program_participation.starting_time = updates["starting_time"]
                if "delay_time" in updates:
                    program_participation.delay_time = updates["delay_time"]
                if "extra_time" in updates:
                    program_participation.extra_time = updates["extra_time"]

            elif section in {"regular", "home"}:
                contest_participation = (
                    program_participation.regular_participation
                    if section == "regular"
                    else program_participation.home_participation
                )

                if contest_participation is None:
                    raise ValueError("No contest participation configured for this role.")

                attrs = contest_participation.get_attrs()
                self.get_password(attrs, contest_participation.password, True)
                self.get_ip_networks(attrs, "ip")
                self.get_bool(attrs, "hidden")
                self.get_bool(attrs, "unrestricted")

                contest_participation.set_attrs(attrs)

                team_data: dict[str, object] = {}
                self.get_string(team_data, "team")
                team_code = team_data.get("team", "").strip()
                if team_code:
                    team = (
                        self.sql_session.query(Team)
                        .filter(Team.code == team_code)
                        .first()
                    )
                    if team is None:
                        raise ValueError(f"Team with code '{team_code}' does not exist")
                    contest_participation.team = team
                else:
                    contest_participation.team = None

            else:
                raise ValueError("Please select a valid section")

        except Exception as error:
            self.sql_session.rollback()
            self.service.add_notification(
                make_datetime(),
                "Operation failed.",
                str(error),
            )
            self.redirect(fallback_page)
            raise
            return

        if self.try_commit():
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)
