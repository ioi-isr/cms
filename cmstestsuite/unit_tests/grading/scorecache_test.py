#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2024 Ron Ryvchin
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

"""Tests for score cache functions.

"""

import unittest
from datetime import timedelta

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db.scorecache import ParticipationTaskScore, ScoreHistory
from cms.grading.scorecache import (
    get_cached_score,
    rebuild_score_cache,
    invalidate_score_cache,
    update_score_cache,
)
from cmscommon.constants import (
    SCORE_MODE_MAX,
    SCORE_MODE_MAX_SUBTASK,
    SCORE_MODE_MAX_TOKENED_LAST,
)
from cmscommon.datetime import make_datetime


class ScoreCacheMixin(DatabaseMixin):
    """A mixin to test score cache functions."""

    def setUp(self):
        super().setUp()
        self.participation = self.add_participation()
        self.task = self.add_task(
            contest=self.participation.contest,
            score_precision=2,
        )
        dataset = self.add_dataset(task=self.task)
        self.task.active_dataset = dataset
        self.timestamp = make_datetime()

    def at(self, seconds):
        return self.timestamp + timedelta(seconds=seconds)

    def add_scored_submission(
        self,
        timestamp,
        score,
        tokened=False,
        score_details=None,
    ):
        """Add a submission with a scored result."""
        score_details = score_details if score_details is not None else []
        submission = self.add_submission(
            participation=self.participation,
            task=self.task,
            timestamp=timestamp,
        )
        self.add_submission_result(
            submission,
            self.task.active_dataset,
            score=score,
            public_score=score if score is not None else 0.0,
            score_details=score_details,
            public_score_details=score_details,
            ranking_score_details=[],
        )
        if tokened:
            self.add_token(timestamp=timestamp, submission=submission)
        return submission

    def add_unscored_submission(self, timestamp, tokened=False):
        """Add a submission without a scored result."""
        submission = self.add_submission(
            participation=self.participation,
            task=self.task,
            timestamp=timestamp,
        )
        self.add_submission_result(
            submission,
            self.task.active_dataset,
        )
        if tokened:
            self.add_token(timestamp=timestamp, submission=submission)
        return submission

    def get_cache_entry(self):
        """Get the cache entry for the current participation/task."""
        return self.session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.participation_id == self.participation.id,
            ParticipationTaskScore.task_id == self.task.id,
        ).first()

    def get_history_entries(self):
        """Get all history entries for the current participation/task."""
        return self.session.query(ScoreHistory).filter(
            ScoreHistory.participation_id == self.participation.id,
            ScoreHistory.task_id == self.task.id,
        ).order_by(ScoreHistory.timestamp).all()


class TestGetCachedScore(ScoreCacheMixin, unittest.TestCase):
    """Tests for get_cached_score()."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_no_submissions_creates_cache(self):
        """Test that get_cached_score creates a cache entry if none exists."""
        self.session.flush()
        score = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score, 0.0)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry.score, 0.0)

    def test_returns_cached_score(self):
        """Test that get_cached_score returns the cached score."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        score = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score, 50.0)

    def test_uses_existing_cache(self):
        """Test that get_cached_score uses existing cache entry."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        cache_entry = self.get_cache_entry()
        original_id = cache_entry.id
        score = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score, 50.0)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.id, original_id)


class TestRebuildScoreCache(ScoreCacheMixin, unittest.TestCase):
    """Tests for rebuild_score_cache()."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_no_submissions(self):
        """Test rebuild with no submissions."""
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 0.0)

    def test_single_submission(self):
        """Test rebuild with a single scored submission."""
        self.add_scored_submission(self.at(1), 75.0)
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 75.0)

    def test_multiple_submissions_max_mode(self):
        """Test rebuild with multiple submissions in SCORE_MODE_MAX."""
        self.add_scored_submission(self.at(1), 50.0)
        self.add_scored_submission(self.at(2), 75.0)
        self.add_scored_submission(self.at(3), 25.0)
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 75.0)

    def test_unscored_submissions_ignored(self):
        """Test that unscored submissions are ignored in rebuild."""
        self.add_scored_submission(self.at(1), 50.0)
        self.add_unscored_submission(self.at(2))
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 50.0)

    def test_max_tokened_last_mode(self):
        """Test rebuild with SCORE_MODE_MAX_TOKENED_LAST."""
        self.task.score_mode = SCORE_MODE_MAX_TOKENED_LAST
        self.add_scored_submission(self.at(1), 50.0, tokened=True)
        self.add_scored_submission(self.at(2), 75.0, tokened=False)
        self.add_scored_submission(self.at(3), 25.0, tokened=False)
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 50.0)
        self.assertEqual(cache_entry.max_tokened_score, 50.0)
        self.assertEqual(cache_entry.last_submission_score, 25.0)

    def test_max_subtask_mode(self):
        """Test rebuild with SCORE_MODE_MAX_SUBTASK."""
        self.task.score_mode = SCORE_MODE_MAX_SUBTASK
        self.add_scored_submission(
            self.at(1),
            30.0,
            score_details=[
                {"idx": "1", "max_score": 50, "score_fraction": 0.6, "score": 30.0},
                {"idx": "2", "max_score": 50, "score_fraction": 0.0, "score": 0.0},
            ],
        )
        self.add_scored_submission(
            self.at(2),
            25.0,
            score_details=[
                {"idx": "1", "max_score": 50, "score_fraction": 0.0, "score": 0.0},
                {"idx": "2", "max_score": 50, "score_fraction": 0.5, "score": 25.0},
            ],
        )
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 55.0)
        self.assertEqual(cache_entry.subtask_max_scores, {"1": 30.0, "2": 25.0})

    def test_creates_history(self):
        """Test that rebuild creates history entries."""
        self.add_scored_submission(self.at(1), 50.0)
        self.add_scored_submission(self.at(2), 75.0)
        self.session.flush()
        rebuild_score_cache(self.session, self.participation, self.task)
        history = self.get_history_entries()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].score, 50.0)
        self.assertEqual(history[1].score, 75.0)


class TestInvalidateScoreCache(ScoreCacheMixin, unittest.TestCase):
    """Tests for invalidate_score_cache()."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_invalidate_by_participation_and_task(self):
        """Test invalidation by participation_id and task_id."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        self.assertIsNotNone(self.get_cache_entry())
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        self.assertIsNone(self.get_cache_entry())

    def test_invalidate_by_participation(self):
        """Test invalidation by participation_id only."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        self.assertIsNotNone(self.get_cache_entry())
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
        )
        self.session.flush()
        self.assertIsNone(self.get_cache_entry())

    def test_invalidate_by_task(self):
        """Test invalidation by task_id only."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        self.assertIsNotNone(self.get_cache_entry())
        invalidate_score_cache(
            self.session,
            task_id=self.task.id,
        )
        self.session.flush()
        self.assertIsNone(self.get_cache_entry())

    def test_invalidate_by_contest(self):
        """Test invalidation by contest_id."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        self.assertIsNotNone(self.get_cache_entry())
        invalidate_score_cache(
            self.session,
            contest_id=self.participation.contest.id,
        )
        self.session.flush()
        self.assertIsNone(self.get_cache_entry())

    def test_invalidate_deletes_history(self):
        """Test that invalidation also deletes history entries."""
        self.add_scored_submission(self.at(1), 50.0)
        self.add_scored_submission(self.at(2), 75.0)
        self.session.flush()
        rebuild_score_cache(self.session, self.participation, self.task)
        self.assertEqual(len(self.get_history_entries()), 2)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        self.assertEqual(len(self.get_history_entries()), 0)

    def test_invalidate_does_not_affect_other_entries(self):
        """Test that invalidation only affects the specified scope."""
        task2 = self.add_task(contest=self.participation.contest)
        dataset2 = self.add_dataset(task=task2)
        task2.active_dataset = dataset2
        task2.score_mode = SCORE_MODE_MAX
        submission2 = self.add_submission(
            participation=self.participation,
            task=task2,
            timestamp=self.at(1),
        )
        self.add_submission_result(
            submission2,
            task2.active_dataset,
            score=60.0,
            public_score=60.0,
            score_details=[],
            public_score_details=[],
            ranking_score_details=[],
        )
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score(self.session, self.participation, self.task)
        get_cached_score(self.session, self.participation, task2)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        self.assertIsNone(self.get_cache_entry())
        task2_cache = self.session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.participation_id == self.participation.id,
            ParticipationTaskScore.task_id == task2.id,
        ).first()
        self.assertIsNotNone(task2_cache)


class TestUpdateScoreCache(ScoreCacheMixin, unittest.TestCase):
    """Tests for update_score_cache()."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_update_creates_cache_if_missing(self):
        """Test that update creates cache entry if none exists."""
        submission = self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry.score, 50.0)

    def test_update_increments_score(self):
        """Test that update increments score when new submission is better."""
        submission1 = self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission1)
        submission2 = self.add_scored_submission(self.at(2), 75.0)
        self.session.flush()
        update_score_cache(self.session, submission2)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 75.0)

    def test_update_does_not_decrement_score(self):
        """Test that update does not decrement score for worse submission."""
        submission1 = self.add_scored_submission(self.at(1), 75.0)
        self.session.flush()
        update_score_cache(self.session, submission1)
        submission2 = self.add_scored_submission(self.at(2), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission2)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 75.0)

    def test_update_adds_history_entry(self):
        """Test that update adds a history entry."""
        submission = self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission)
        history = self.get_history_entries()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].score, 50.0)

    def test_update_max_subtask_mode(self):
        """Test incremental update with SCORE_MODE_MAX_SUBTASK."""
        self.task.score_mode = SCORE_MODE_MAX_SUBTASK
        submission1 = self.add_scored_submission(
            self.at(1),
            30.0,
            score_details=[
                {"idx": "1", "max_score": 50, "score_fraction": 0.6, "score": 30.0},
                {"idx": "2", "max_score": 50, "score_fraction": 0.0, "score": 0.0},
            ],
        )
        self.session.flush()
        update_score_cache(self.session, submission1)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 30.0)
        self.assertEqual(cache_entry.subtask_max_scores, {"1": 30.0, "2": 0.0})
        submission2 = self.add_scored_submission(
            self.at(2),
            25.0,
            score_details=[
                {"idx": "1", "max_score": 50, "score_fraction": 0.0, "score": 0.0},
                {"idx": "2", "max_score": 50, "score_fraction": 0.5, "score": 25.0},
            ],
        )
        self.session.flush()
        update_score_cache(self.session, submission2)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 55.0)
        self.assertEqual(cache_entry.subtask_max_scores, {"1": 30.0, "2": 25.0})


class TestScoreCacheAfterInvalidation(ScoreCacheMixin, unittest.TestCase):
    """Tests for score cache behavior after invalidation.

    These tests verify that the cache is correctly rebuilt after invalidation,
    which is the key fix for the recompilation issue.
    """

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_cache_rebuilt_after_invalidation(self):
        """Test that cache is rebuilt correctly after invalidation."""
        self.add_scored_submission(self.at(1), 75.0)
        self.add_scored_submission(self.at(2), 50.0)
        self.session.flush()
        score1 = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score1, 75.0)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        score2 = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score2, 75.0)

    def test_cache_reflects_removed_submission(self):
        """Test that cache reflects changes when submission is removed.

        This simulates the scenario where a submission's score is invalidated
        (e.g., due to recompilation failure) and the cache should reflect
        the next best score.
        """
        submission1 = self.add_scored_submission(self.at(1), 50.0)
        submission2 = self.add_scored_submission(self.at(2), 75.0)
        self.session.flush()
        score1 = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score1, 75.0)
        sr2 = submission2.get_result(self.task.active_dataset)
        sr2.score = None
        sr2.score_details = None
        sr2.public_score = None
        sr2.public_score_details = None
        sr2.ranking_score_details = None
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        score2 = get_cached_score(self.session, self.participation, self.task)
        self.assertEqual(score2, 50.0)


if __name__ == "__main__":
    unittest.main()
