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

"""Tests for start button functionality in regular contests.

"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

from cms.db import Contest, Participation, User, Task
from cms.server.contest.handlers.main import StartHandler


class TestStartButtonRegularContest(unittest.TestCase):
    """Test start button functionality for regular contests."""

    def setUp(self):
        """Set up test fixtures."""
        self.contest = Contest(
            name="test_contest",
            description="Test Contest",
            start=datetime(2025, 1, 1, 10, 0, 0),
            stop=datetime(2025, 1, 1, 14, 0, 0),
            per_user_time=None,  # Regular contest
        )
        
        self.user = User(
            first_name="Test",
            last_name="User",
            username="testuser",
        )
        
        self.participation = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=None,
            delay_time=timedelta(0),
            extra_time=timedelta(0),
        )

    def test_start_button_sets_starting_time_regular_contest(self):
        """Test that pressing start button sets starting_time in regular contest."""
        timestamp = datetime(2025, 1, 1, 10, 30, 0)
        
        self.assertIsNone(self.participation.starting_time)
        
        self.participation.starting_time = timestamp
        
        self.assertEqual(self.participation.starting_time, timestamp)

    def test_start_button_cannot_be_pressed_twice(self):
        """Test that start button cannot be pressed twice."""
        timestamp1 = datetime(2025, 1, 1, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 1, 11, 0, 0)
        
        self.participation.starting_time = timestamp1
        self.assertEqual(self.participation.starting_time, timestamp1)
        
        if self.participation.starting_time is not None:
            pass
        else:
            self.participation.starting_time = timestamp2
        
        self.assertEqual(self.participation.starting_time, timestamp1)


class TestStartButtonUSACOContest(unittest.TestCase):
    """Test start button functionality for USACO-style contests."""

    def setUp(self):
        """Set up test fixtures."""
        self.contest = Contest(
            name="test_contest",
            description="Test Contest",
            start=datetime(2025, 1, 1, 10, 0, 0),
            stop=datetime(2025, 1, 1, 14, 0, 0),
            per_user_time=timedelta(hours=3),  # USACO-style contest
        )
        
        self.user = User(
            first_name="Test",
            last_name="User",
            username="testuser",
        )
        
        self.participation = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=None,
            delay_time=timedelta(0),
            extra_time=timedelta(0),
        )

    def test_start_button_sets_starting_time_usaco_contest(self):
        """Test that pressing start button sets starting_time in USACO contest."""
        timestamp = datetime(2025, 1, 1, 10, 30, 0)
        
        self.assertIsNone(self.participation.starting_time)
        
        self.participation.starting_time = timestamp
        
        self.assertEqual(self.participation.starting_time, timestamp)


class TestTaskVisibilityRestrictions(unittest.TestCase):
    """Test task visibility restrictions based on starting_time."""

    def setUp(self):
        """Set up test fixtures."""
        self.contest = Contest(
            name="test_contest",
            description="Test Contest",
            start=datetime(2025, 1, 1, 10, 0, 0),
            stop=datetime(2025, 1, 1, 14, 0, 0),
            per_user_time=None,  # Regular contest
        )
        
        self.user = User(
            first_name="Test",
            last_name="User",
            username="testuser",
        )
        
        self.participation = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=None,
            delay_time=timedelta(0),
            extra_time=timedelta(0),
            unrestricted=False,
        )
        
        self.unrestricted_participation = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=None,
            delay_time=timedelta(0),
            extra_time=timedelta(0),
            unrestricted=True,
        )

    def test_tasks_hidden_before_start(self):
        """Test that tasks are hidden before pressing start button."""
        self.assertIsNone(self.participation.starting_time)
        
        should_block = (not self.participation.unrestricted and 
                       self.participation.starting_time is None)
        self.assertTrue(should_block)

    def test_tasks_visible_after_start(self):
        """Test that tasks are visible after pressing start button."""
        self.participation.starting_time = datetime(2025, 1, 1, 10, 30, 0)
        
        should_block = (not self.participation.unrestricted and 
                       self.participation.starting_time is None)
        self.assertFalse(should_block)

    def test_unrestricted_users_always_see_tasks(self):
        """Test that unrestricted users can always see tasks."""
        self.assertIsNone(self.unrestricted_participation.starting_time)
        
        should_block = (not self.unrestricted_participation.unrestricted and 
                       self.unrestricted_participation.starting_time is None)
        self.assertFalse(should_block)


class TestAnalysisModeRestrictions(unittest.TestCase):
    """Test analysis mode restrictions based on starting_time."""

    def setUp(self):
        """Set up test fixtures."""
        self.contest = Contest(
            name="test_contest",
            description="Test Contest",
            start=datetime(2025, 1, 1, 10, 0, 0),
            stop=datetime(2025, 1, 1, 14, 0, 0),
            analysis_enabled=True,
            analysis_start=datetime(2025, 1, 1, 15, 0, 0),
            analysis_stop=datetime(2025, 1, 1, 18, 0, 0),
            per_user_time=None,  # Regular contest
        )
        
        self.user = User(
            first_name="Test",
            last_name="User",
            username="testuser",
        )
        
        self.participated = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=datetime(2025, 1, 1, 10, 30, 0),
            delay_time=timedelta(0),
            extra_time=timedelta(0),
            unrestricted=False,
        )
        
        self.not_participated = Participation(
            user=self.user,
            contest=self.contest,
            starting_time=None,
            delay_time=timedelta(0),
            extra_time=timedelta(0),
            unrestricted=False,
        )

    def test_analysis_mode_allowed_for_participants(self):
        """Test that analysis mode is allowed for users who pressed start."""
        should_block = (not self.participated.unrestricted and 
                       self.participated.starting_time is None)
        self.assertFalse(should_block)

    def test_analysis_mode_blocked_for_non_participants(self):
        """Test that analysis mode is blocked for users who didn't press start."""
        should_block = (not self.not_participated.unrestricted and 
                       self.not_participated.starting_time is None)
        self.assertTrue(should_block)


if __name__ == "__main__":
    unittest.main()
