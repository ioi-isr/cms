#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
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

"""Tests for training program coaches."""

import ipaddress
import unittest
from datetime import datetime

from cms.db import (
    Announcement,
    Student,
    TrainingDay,
    TrainingDayGroup,
    TrainingProgram,
)
from cms.server.contest.authentication import validate_login
from cms.server.contest.communication import can_see_announcement
from cms.server.contest.handlers.trainingprogram import \
    get_coach_visible_archived_task_ids
from cms.server.util import (
    apply_coach_status,
    can_access_task,
    check_training_day_eligibility,
    get_training_program_group_tags,
)
from cmscommon.crypto import build_password
from cmscommon.datetime import make_datetime
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class TestCoaches(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.managing_contest = self.add_contest(name="tp_contest")
        self.program = TrainingProgram(
            name="program", description="A program",
            managing_contest=self.managing_contest)
        self.session.add(self.program)

        self.td_contest = self.add_contest(name="day_contest")
        self.training_day = TrainingDay(
            training_program=self.program, contest=self.td_contest,
            position=0, training_day_types=[])
        self.session.add(self.training_day)
        self.session.flush()
        for tag in ("beginners", "advanced"):
            self.session.add(TrainingDayGroup(
                training_day=self.training_day, tag_name=tag,
                start_time=datetime(2024, 1, 10, 9, 0),
                end_time=datetime(2024, 1, 10, 14, 0)))

        self.task_beginners = self.add_task(
            contest=self.td_contest, name="easy", num=0,
            visible_to_tags=["beginners"])
        self.task_advanced = self.add_task(
            contest=self.td_contest, name="hard", num=1,
            visible_to_tags=["advanced"])

        self.student, self.student_td = self._add_student(
            "student", ["beginners", "extra"])
        self.coach, self.coach_td = self._add_student("coach", ["advanced"])
        apply_coach_status(self.session, self.coach, True)
        self.session.flush()

    def tearDown(self):
        self.session.rollback()
        self.delete_data()
        super().tearDown()

    def _add_student(self, username, tags):
        user = self.add_user(username=username,
                             password=build_password("pass"))
        participation = self.add_participation(
            user=user, contest=self.managing_contest)
        student = Student(training_program=self.program,
                          participation=participation, student_tags=tags)
        self.session.add(student)
        td_participation = self.add_participation(
            user=user, contest=self.td_contest)
        self.session.flush()
        return student, td_participation

    def test_apply_coach_status(self):
        self.assertTrue(self.coach.is_coach)
        self.assertEqual(self.coach.student_tags, [])
        for p in (self.coach.participation, self.coach_td):
            self.assertTrue(p.hidden)
            self.assertTrue(p.unrestricted)

        apply_coach_status(self.session, self.coach, False)
        self.assertFalse(self.coach.is_coach)
        for p in (self.coach.participation, self.coach_td):
            self.assertFalse(p.hidden)
            self.assertFalse(p.unrestricted)

    def test_coach_login_with_blocked_hidden_participations(self):
        for contest in (self.managing_contest, self.td_contest):
            contest.allow_password_authentication = True
            contest.block_hidden_participations = True
        self.student_td.hidden = True
        self.session.flush()

        def login(contest, username):
            participation, _cookie = validate_login(
                self.session, contest, make_datetime(), username, "pass",
                ipaddress.ip_address("127.0.0.1"))
            return participation

        self.assertIs(login(self.managing_contest, "coach"),
                      self.coach.participation)
        self.assertIs(login(self.td_contest, "coach"), self.coach_td)
        self.assertIsNone(login(self.td_contest, "student"))

    def test_coach_visible_archived_tasks(self):
        class Ranking:
            def __init__(self, tags, task_ids):
                self.student_tags = tags
                self.task_scores = {t: 0.0 for t in task_ids}

        day = TrainingDay(archived_tasks_data={"1": {}, "2": {}, "3": {}})
        rankings = [Ranking(["beginners"], ["1"]),
                    Ranking(["advanced"], ["2", "3"])]
        self.assertEqual(
            get_coach_visible_archived_task_ids(day, rankings, None),
            {"1", "2", "3"})
        self.assertEqual(
            get_coach_visible_archived_task_ids(day, rankings, "advanced"),
            {"2", "3"})

    def test_regular_student_unaffected(self):
        self.student_td.hidden = True
        apply_coach_status(self.session, self.student, False)
        self.assertTrue(self.student_td.hidden)
        self.assertEqual(self.student.student_tags, ["beginners", "extra"])

    def test_eligibility(self):
        eligible, group, _ = check_training_day_eligibility(
            self.session, self.coach_td, self.training_day)
        self.assertTrue(eligible)
        self.assertIsNone(group)

        eligible, group, _ = check_training_day_eligibility(
            self.session, self.coach_td, self.training_day,
            view_as_tag="advanced")
        self.assertTrue(eligible)
        self.assertEqual(group.tag_name, "advanced")

        # A regular student is not affected by view_as_tag.
        eligible, group, _ = check_training_day_eligibility(
            self.session, self.student_td, self.training_day,
            view_as_tag="advanced")
        self.assertTrue(eligible)
        self.assertEqual(group.tag_name, "beginners")

    def test_task_access(self):
        def visible(participation, view_as_tag=None):
            return {
                t.name for t in (self.task_beginners, self.task_advanced)
                if can_access_task(self.session, t, participation,
                                   self.training_day, view_as_tag)
            }

        self.assertEqual(visible(self.coach_td), {"easy", "hard"})
        self.assertEqual(visible(self.coach_td, "beginners"), {"easy"})
        self.assertEqual(visible(self.student_td), {"easy"})
        self.assertEqual(visible(self.student_td, "advanced"), {"easy"})

    def test_announcements(self):
        announcement = Announcement(
            timestamp=datetime(2024, 1, 10, 9, 0), subject="s", text="t",
            contest=self.td_contest, visible_to_tags=["advanced"])
        self.session.add(announcement)
        self.session.flush()
        self.assertTrue(can_see_announcement(
            self.session, announcement, self.coach_td))
        self.assertFalse(can_see_announcement(
            self.session, announcement, self.coach_td, "beginners"))
        self.assertFalse(can_see_announcement(
            self.session, announcement, self.student_td))

    def test_group_tags(self):
        self.assertEqual(
            get_training_program_group_tags(self.session, self.program),
            ["advanced", "beginners", "extra"])


if __name__ == "__main__":
    unittest.main()
