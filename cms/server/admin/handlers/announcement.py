#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
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

"""Announcement-related handlers for AWS for a specific contest.

"""

import collections
try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms.db import Contest, Announcement, TrainingProgram
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


class TrainingProgramAnnouncementsHandler(BaseHandler):
    """Show announcements for all contests in a training program."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, program_id: str):
        program = self.safe_get_item(TrainingProgram, program_id)
        self.training_program = program

        regular_announcements = (
            program.regular_contest.announcements if program.regular_contest else []
        )
        home_announcements = (
            program.home_contest.announcements if program.home_contest else []
        )

        self.r_params = self.render_params()
        self.r_params.update(
            {
                "training_program": program,
                "regular_contest": program.regular_contest,
                "home_contest": program.home_contest,
                "regular_announcements": regular_announcements,
                "home_announcements": home_announcements,
            }
        )
        self.render("training_program_announcements.html", **self.r_params)




class ContestAnnouncementsHandler(BaseHandler):
    """Show contest announcements, redirecting to training program if applicable."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id: str):
        self.contest = self.safe_get_item(Contest, contest_id)

        if self.contest.training_program is not None:
            self.redirect(
                self.url("training_program", self.contest.training_program.id, "announcements"),
            )
            return

        self.r_params = self.render_params()
        self.render("announcements.html", **self.r_params)

class AddAnnouncementHandler(BaseHandler):
    """Called to actually add an announcement

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id: str):
        self.contest = self.safe_get_item(Contest, contest_id)

        subject: str = self.get_argument("subject", "")
        text: str = self.get_argument("text", "")
        redirect_url = self.get_argument("next", None)
        if redirect_url:
            redirect_url = "/" + redirect_url.lstrip("/")

        if len(subject) > 0:
            ann = Announcement(
                make_datetime(),
                subject,
                text,
                contest=self.contest,
                admin=self.current_user,
            )
            self.sql_session.add(ann)
            self.try_commit()
        else:
            self.service.add_notification(
                make_datetime(),
                "Subject is mandatory.",
                "",
            )

        if redirect_url:
            self.redirect(redirect_url)
        else:
            self.redirect(self.url("contest", contest_id, "announcements"))

class AnnouncementHandler(BaseHandler):
    """Called to remove an announcement.

    """
    # No page to show a single attachment.

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def delete(self, contest_id: str, ann_id: str):
        ann = self.safe_get_item(Announcement, ann_id)
        self.contest = self.safe_get_item(Contest, contest_id)

        # Protect against URLs providing incompatible parameters.
        if self.contest is not ann.contest:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(ann)
        self.try_commit()

        # Page to redirect to.
        self.write("announcements")
