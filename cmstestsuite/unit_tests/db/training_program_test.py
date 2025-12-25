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

"""Tests for TrainingProgram, TrainingDay, and Student models.

Focus on:
- TrainingProgram creation and relationships
- TrainingDay creation and position ordering
- Student creation and participation linking
- Task assignment to training days
- Contest methods for training days
- Cascade deletion behavior
- Constraint enforcement
"""

import unittest

from sqlalchemy.exc import IntegrityError

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Contest, Task, TrainingProgram, TrainingDay, Student


class TestTrainingProgram(DatabaseMixin, unittest.TestCase):
    """Tests for TrainingProgram model."""

    def test_create_training_program_with_managing_contest(self):
        """Test creating a training program with a managing contest."""
        managing_contest = self.add_contest(name="managing_contest")
        training_program = TrainingProgram(
            name="summer2024",
            description="Summer Training 2024",
            managing_contest=managing_contest,
        )
        self.session.add(training_program)
        self.session.commit()

        self.assertEqual(training_program.name, "summer2024")
        self.assertEqual(training_program.description, "Summer Training 2024")
        self.assertIs(training_program.managing_contest, managing_contest)
        self.assertEqual(training_program.managing_contest_id, managing_contest.id)

    def test_training_program_requires_managing_contest(self):
        """Test that training program requires a managing contest."""
        training_program = TrainingProgram(
            name="test_program",
            description="Test Program",
        )
        self.session.add(training_program)

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_training_program_name_is_unique(self):
        """Test that training program names must be unique."""
        managing_contest1 = self.add_contest(name="contest1")
        managing_contest2 = self.add_contest(name="contest2")
        
        program1 = TrainingProgram(
            name="duplicate_name",
            description="First Program",
            managing_contest=managing_contest1,
        )
        self.session.add(program1)
        self.session.commit()

        program2 = TrainingProgram(
            name="duplicate_name",
            description="Second Program",
            managing_contest=managing_contest2,
        )
        self.session.add(program2)

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_training_program_managing_contest_is_unique(self):
        """Test that each contest can manage at most one training program."""
        managing_contest = self.add_contest(name="shared_contest")
        
        program1 = TrainingProgram(
            name="program1",
            description="First Program",
            managing_contest=managing_contest,
        )
        self.session.add(program1)
        self.session.commit()

        program2 = TrainingProgram(
            name="program2",
            description="Second Program",
            managing_contest=managing_contest,
        )
        self.session.add(program2)

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_contest_training_program_relationship(self):
        """Test bidirectional relationship between contest and training program."""
        managing_contest = self.add_contest()
        training_program = TrainingProgram(
            name="program_relationship",
            description="Program",
            managing_contest=managing_contest,
        )
        self.session.add(training_program)
        self.session.commit()

        # Test reverse relationship
        self.assertIs(managing_contest.training_program, training_program)

    def test_delete_training_program_cascades_to_students(self):
        """Test that deleting a training program deletes its students."""
        managing_contest = self.add_contest()
        training_program = TrainingProgram(
            name="program_cascade_students",
            description="Program",
            managing_contest=managing_contest,
        )
        self.session.add(training_program)
        self.session.flush()

        user = self.add_user()
        participation = self.add_participation(user=user, contest=managing_contest)
        student = Student(
            training_program=training_program,
            participation=participation,
            student_tags=["beginner"],
        )
        self.session.add(student)
        self.session.commit()

        student_id = student.id

        # Delete training program
        self.session.delete(training_program)
        self.session.commit()

        # Student should be deleted
        deleted_student = self.session.query(Student).filter(
            Student.id == student_id
        ).first()
        self.assertIsNone(deleted_student)

    def test_delete_training_program_cascades_to_training_days(self):
        """Test that deleting a training program deletes its training days."""
        managing_contest = self.add_contest()
        training_program = TrainingProgram(
            name="program_cascade_days",
            description="Program",
            managing_contest=managing_contest,
        )
        self.session.add(training_program)
        self.session.flush()

        day_contest = self.add_contest()
        training_day = TrainingDay(
            training_program=training_program,
            contest=day_contest,
            position=0,
        )
        self.session.add(training_day)
        self.session.commit()

        training_day_id = training_day.id

        # Delete training program
        self.session.delete(training_program)
        self.session.commit()

        # Training day should be deleted
        deleted_day = self.session.query(TrainingDay).filter(
            TrainingDay.id == training_day_id
        ).first()
        self.assertIsNone(deleted_day)


if __name__ == "__main__":
    unittest.main()
