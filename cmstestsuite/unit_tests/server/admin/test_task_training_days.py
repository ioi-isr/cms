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

"""Tests for the archived training day data shown on task pages."""

import unittest
from datetime import datetime

from cms.db import (
    ArchivedAttendance,
    ArchivedStudentRanking,
    Student,
    TrainingDay,
    TrainingProgram,
)
from cms.server.admin.handlers.task import (
    get_task_training_day_scores,
    get_task_training_days,
)
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


class TestTaskTrainingDays(DatabaseMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        contest = self.add_contest(name="tp_contest")
        self.program = TrainingProgram(
            name="program", description="A program",
            managing_contest=contest)
        self.session.add(self.program)
        self.task_a = self.add_task(contest=contest, name="task_a", num=0)
        self.task_b = self.add_task(contest=contest, name="task_b", num=1)
        self.task_c = self.add_task(contest=contest, name="task_c", num=2)

        self.students = {}
        for username in ("alice", "bob", "carol"):
            user = self.add_user(username=username)
            participation = self.add_participation(user=user, contest=contest)
            student = Student(training_program=self.program,
                              participation=participation, student_tags=[])
            self.session.add(student)
            self.students[username] = student
        self.session.flush()

        a, b = str(self.task_a.id), str(self.task_b.id)
        self.day_one = TrainingDay(
            training_program=self.program, position=0, name="day_one",
            description="First day", start_time=datetime(2024, 1, 10),
            training_day_types=[],
            archived_tasks_data={a: {"name": "task_a", "max_score": 50},
                                 b: {"name": "task_b"}})
        self.day_two = TrainingDay(
            training_program=self.program, position=1, name="day_two",
            start_time=datetime(2024, 1, 11), training_day_types=[],
            archived_tasks_data={a: {"name": "task_a"}})
        # Active training days are not archived even if they carry data.
        self.active_day = TrainingDay(
            training_program=self.program, position=2,
            contest=self.add_contest(name="active"), training_day_types=[],
            archived_tasks_data={a: {"name": "task_a"}})
        self.session.add_all([self.day_one, self.day_two, self.active_day])
        self.session.flush()

        alice, bob, carol = (self.students[name]
                             for name in ("alice", "bob", "carol"))
        self.session.add_all([
            ArchivedStudentRanking(
                training_day=self.day_one, student=alice,
                student_tags=["advanced"], task_scores={a: 40.0, b: None}),
            ArchivedStudentRanking(
                training_day=self.day_one, student=bob,
                student_tags=["beginners", "online"], task_scores={a: 10.0}),
            ArchivedStudentRanking(
                training_day=self.day_one, student=carol,
                student_tags=["beginners"], task_scores={a: 5.0}),
            ArchivedAttendance(
                training_day=self.day_one, student=alice,
                status="participated", justified=False,
                declared_bad_day=False, recorded=True),
            ArchivedAttendance(
                training_day=self.day_one, student=bob,
                status="participated", justified=False,
                declared_bad_day=False, recorded=True),
            ArchivedAttendance(
                training_day=self.day_one, student=carol, status="missed",
                justified=False, declared_bad_day=False, recorded=True),
        ])
        self.session.flush()

    def tearDown(self):
        self.session.rollback()
        self.delete_data()
        super().tearDown()

    def test_maps_tasks_to_archived_days(self):
        tasks = [self.task_a.id, self.task_b.id, self.task_c.id]
        self.assertEqual(get_task_training_days(self.session, tasks), {
            self.task_a.id: [(self.day_one.id, "program", "First day"),
                             (self.day_two.id, "program", "day_two")],
            self.task_b.id: [(self.day_one.id, "program", "First day")],
            self.task_c.id: [],
        })

    def test_single_task_uses_same_mapping(self):
        self.assertEqual(
            get_task_training_days(self.session, [self.task_b.id]),
            {self.task_b.id: [(self.day_one.id, "program", "First day")]})
        self.assertEqual(get_task_training_days(self.session, []), {})

    def test_scores_include_only_participants(self):
        alice, bob, carol = (self.students[name].id
                             for name in ("alice", "bob", "carol"))
        result = get_task_training_day_scores(self.session, self.task_a.id)
        self.assertEqual(result, [
            {
                "training_day_id": self.day_one.id,
                "program_name": "program",
                "day_name": "First day",
                "max_score": 50,
                "tags": ["advanced", "beginners", "online"],
                "student_tags": {alice: ["advanced"],
                                 bob: ["beginners", "online"],
                                 carol: ["beginners"]},
                "scores": [{"studentId": alice, "score": 40.0},
                           {"studentId": bob, "score": 10.0}],
            },
            {
                "training_day_id": self.day_two.id,
                "program_name": "program",
                "day_name": "day_two",
                "max_score": 100,
                "tags": [],
                "student_tags": {},
                "scores": [],
            },
        ])

    def test_missing_scores_default_to_zero(self):
        result = get_task_training_day_scores(self.session, self.task_b.id)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["max_score"], 100)
        self.assertEqual(result[0]["scores"], [
            {"studentId": self.students["alice"].id, "score": 0}])
        self.assertEqual(
            get_task_training_day_scores(self.session, self.task_c.id), [])


if __name__ == "__main__":
    unittest.main()
