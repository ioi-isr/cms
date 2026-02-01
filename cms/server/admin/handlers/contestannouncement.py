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
except AttributeError:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web

from cms.db import Contest, Announcement, TrainingProgram
from cms.server.admin.handlers.utils import get_all_student_tags, parse_tags
from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


class ContestAnnouncementsHandler(BaseHandler):
    """Display announcements for a contest or training program.

    Supports both contest and training_program entity types via URL pattern:
    - /contest/{id}/announcements
    - /training_program/{id}/announcements

    For training day contests and training programs, also passes all_student_tags
    for the tagify box.
    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, entity_type: str, entity_id: str):
        training_program = self.setup_contest_or_training_program(
            entity_type, entity_id
        )

        # For training day contests, get training_program from the training day
        training_day = self.contest.training_day
        if training_day is not None and training_program is None:
            training_program = training_day.training_program

        if training_program is not None:
            self.r_params["all_student_tags"] = get_all_student_tags(
                self.sql_session, training_program
            )
        else:
            self.r_params["all_student_tags"] = []

        self.r_params["is_training_day"] = training_day is not None

        self.render("announcements.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, entity_type: str, entity_id: str):
        """Handle adding/editing announcements for training programs.

        For contests, use AddAnnouncementHandler and EditAnnouncementHandler instead.
        This POST method is only used for training programs which have a combined
        add/edit form.
        """
        if entity_type != "training_program":
            # Contests use separate add/edit handlers
            raise tornado.web.HTTPError(405)

        training_program = self.safe_get_item(TrainingProgram, entity_id)
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

        self.redirect(self.url("training_program", entity_id, "announcements"))


class AddAnnouncementHandler(BaseHandler):
    """Called to actually add an announcement

    """
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id: str):
        self.contest = self.safe_get_item(Contest, contest_id)

        subject: str = self.get_argument("subject", "")
        text: str = self.get_argument("text", "")

        # Parse visible_to_tags from comma-separated string
        visible_to_tags_str = self.get_argument("visible_to_tags", "")
        visible_to_tags = parse_tags(visible_to_tags_str)

        if len(subject) > 0:
            ann = Announcement(
                make_datetime(),
                subject,
                text,
                contest=self.contest,
                admin=self.current_user,
                visible_to_tags=visible_to_tags,
            )
            self.sql_session.add(ann)
            self.try_commit()
        else:
            self.service.add_notification(
                make_datetime(), "Subject is mandatory.", "")
        self.redirect(self.url("contest", contest_id, "announcements"))


class EditAnnouncementHandler(BaseHandler):
    """Called to edit an announcement"""

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self, contest_id: str, ann_id: str):
        original_ann = self.safe_get_item(Announcement, ann_id)
        self.contest = self.safe_get_item(Contest, contest_id)

        # Protect against URLs providing incompatible parameters.
        if original_ann.contest_id != self.contest.id:
            raise tornado.web.HTTPError(404)

        subject: str = self.get_argument("subject", "")
        text: str = self.get_argument("text", "")

        # Parse visible_to_tags from comma-separated string
        visible_to_tags_str = self.get_argument("visible_to_tags", "")
        visible_to_tags = parse_tags(visible_to_tags_str)

        if len(subject) > 0:
            original_ann.subject = subject
            original_ann.text = text
            original_ann.visible_to_tags = visible_to_tags
            self.try_commit()
        else:
            self.service.add_notification(make_datetime(), "Subject is mandatory.", "")
        self.redirect(self.url("contest", contest_id, "announcements"))


class AnnouncementHandler(BaseHandler):
    """Called to remove an announcement.

    """
    # No page to show a single attachment.

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def delete(self, entity_type: str, entity_id: str, ann_id: str):
        ann = self.safe_get_item(Announcement, ann_id)
        self.setup_contest_or_training_program(
            entity_type, entity_id, set_r_params=False
        )

        # Protect against URLs providing incompatible parameters.
        if self.contest is not ann.contest:
            raise tornado.web.HTTPError(404)

        self.sql_session.delete(ann)
        self.try_commit()

        # Page to redirect to.
        self.write("announcements")
