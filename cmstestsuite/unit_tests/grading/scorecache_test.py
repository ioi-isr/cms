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
    get_cached_score_entry,
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


class TestGetCachedScoreEntry(ScoreCacheMixin, unittest.TestCase):
    """Tests for get_cached_score_entry()."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_no_submissions_creates_cache(self):
        """Test that get_cached_score_entry creates a cache entry if none exists."""
        self.session.flush()
        cache_entry = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertIsNotNone(cache_entry)
        self.assertEqual(cache_entry.score, 0.0)
        self.assertFalse(cache_entry.has_submissions)

    def test_returns_cached_entry_with_score(self):
        """Test that get_cached_score_entry returns the cached entry with score."""
        self.session.flush()
        cache_entry_before = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertFalse(cache_entry_before.has_submissions)
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        cache_entry = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 50.0)
        self.assertTrue(cache_entry.has_submissions)

    def test_uses_existing_cache(self):
        """Test that get_cached_score_entry uses existing cache entry."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        cache_entry1 = get_cached_score_entry(
            self.session, self.participation, self.task)
        original_id = cache_entry1.id
        cache_entry2 = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry2.score, 50.0)
        self.assertEqual(cache_entry2.id, original_id)
        self.assertTrue(cache_entry2.has_submissions)


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
        """Test invalidation by participation_id and task_id sets invalidated_at."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score_entry(self.session, self.participation, self.task)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNone(cache_entry.invalidated_at)
        self.assertIsNotNone(cache_entry.created_at)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNotNone(cache_entry.invalidated_at)
        self.assertGreater(cache_entry.invalidated_at, cache_entry.created_at)

    def test_invalidate_by_participation(self):
        """Test invalidation by participation_id only sets invalidated_at."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score_entry(self.session, self.participation, self.task)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNone(cache_entry.invalidated_at)
        self.assertIsNotNone(cache_entry.created_at)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
        )
        self.session.flush()
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNotNone(cache_entry.invalidated_at)
        self.assertGreater(cache_entry.invalidated_at, cache_entry.created_at)

    def test_invalidate_by_task(self):
        """Test invalidation by task_id only sets invalidated_at."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score_entry(self.session, self.participation, self.task)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNone(cache_entry.invalidated_at)
        self.assertIsNotNone(cache_entry.created_at)
        invalidate_score_cache(
            self.session,
            task_id=self.task.id,
        )
        self.session.flush()
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNotNone(cache_entry.invalidated_at)
        self.assertGreater(cache_entry.invalidated_at, cache_entry.created_at)

    def test_invalidate_by_contest(self):
        """Test invalidation by contest_id sets invalidated_at."""
        self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        get_cached_score_entry(self.session, self.participation, self.task)
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNone(cache_entry.invalidated_at)
        self.assertIsNotNone(cache_entry.created_at)
        invalidate_score_cache(
            self.session,
            contest_id=self.participation.contest.id,
        )
        self.session.flush()
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNotNone(cache_entry.invalidated_at)
        self.assertGreater(cache_entry.invalidated_at, cache_entry.created_at)

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
        get_cached_score_entry(self.session, self.participation, self.task)
        get_cached_score_entry(self.session, self.participation, task2)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        cache_entry = self.get_cache_entry()
        self.assertIsNotNone(cache_entry)
        self.assertIsNotNone(cache_entry.invalidated_at)
        task2_cache = self.session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.participation_id == self.participation.id,
            ParticipationTaskScore.task_id == task2.id,
        ).first()
        self.assertIsNotNone(task2_cache)
        self.assertIsNone(task2_cache.invalidated_at)


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
        cache_entry1 = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry1.score, 75.0)
        self.assertTrue(cache_entry1.has_submissions)
        invalidate_score_cache(
            self.session,
            participation_id=self.participation.id,
            task_id=self.task.id,
        )
        self.session.flush()
        cache_entry2 = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry2.score, 75.0)
        self.assertTrue(cache_entry2.has_submissions)

    def test_cache_reflects_removed_submission(self):
        """Test that cache reflects changes when submission is removed.

        This simulates the scenario where a submission's score is invalidated
        (e.g., due to recompilation failure) and the cache should reflect
        the next best score.
        """
        self.add_scored_submission(self.at(1), 50.0)
        submission2 = self.add_scored_submission(self.at(2), 75.0)
        self.session.flush()
        cache_entry1 = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry1.score, 75.0)
        self.assertTrue(cache_entry1.has_submissions)
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
        cache_entry2 = get_cached_score_entry(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry2.score, 50.0)
        self.assertTrue(cache_entry2.has_submissions)


class TestScorePrecisionHandling(ScoreCacheMixin, unittest.TestCase):
    """Tests for score precision handling in cache operations."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_score_precision_rounding_on_rebuild(self):
        """Test that scores are correctly rounded according to task precision."""
        self.task.score_precision = 2
        self.add_scored_submission(self.at(1), 75.666666)
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 75.67)

    def test_score_precision_zero_decimals(self):
        """Test score precision with zero decimal places."""
        self.task.score_precision = 0
        self.add_scored_submission(self.at(1), 75.666666)
        self.session.flush()
        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 76.0)

    def test_incremental_update_respects_precision(self):
        """Test that incremental updates respect score precision."""
        self.task.score_precision = 1
        submission = self.add_scored_submission(self.at(1), 88.8888)
        self.session.flush()
        update_score_cache(self.session, submission)
        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 88.9)


class TestOutOfOrderSubmissions(ScoreCacheMixin, unittest.TestCase):
    """Tests for handling out-of-order submission arrivals."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_out_of_order_marks_history_invalid(self):
        """Test that out-of-order submission marks history as invalid."""
        submission1 = self.add_scored_submission(self.at(2), 60.0)
        self.session.flush()
        update_score_cache(self.session, submission1)

        # Add earlier submission after later one
        submission2 = self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission2)

        cache_entry = self.get_cache_entry()
        self.assertFalse(cache_entry.history_valid)

    def test_in_order_submissions_keep_history_valid(self):
        """Test that in-order submissions keep history valid."""
        submission1 = self.add_scored_submission(self.at(1), 50.0)
        self.session.flush()
        update_score_cache(self.session, submission1)

        submission2 = self.add_scored_submission(self.at(2), 60.0)
        self.session.flush()
        update_score_cache(self.session, submission2)

        cache_entry = self.get_cache_entry()
        self.assertTrue(cache_entry.history_valid)

    def test_rebuild_handles_out_of_order_correctly(self):
        """Test that rebuild correctly handles out-of-order submissions."""
        # Add submissions out of chronological order
        self.add_scored_submission(self.at(3), 70.0)
        self.add_scored_submission(self.at(1), 50.0)
        self.add_scored_submission(self.at(2), 60.0)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 70.0)
        self.assertEqual(cache_entry.last_submission_timestamp, self.at(3))


class TestScoreHistoryRebuild(ScoreCacheMixin, unittest.TestCase):
    """Tests for score history rebuild functionality."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_history_only_includes_score_changes(self):
        """Test that history only includes entries where score changed."""
        self.add_scored_submission(self.at(1), 50.0)
        self.add_scored_submission(self.at(2), 30.0)  # Worse score
        self.add_scored_submission(self.at(3), 40.0)  # Still worse
        self.add_scored_submission(self.at(4), 75.0)  # Better score
        self.session.flush()

        rebuild_score_cache(self.session, self.participation, self.task)
        history = self.get_history_entries()

        # Should only have 2 entries: initial 50.0 and improved 75.0
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].score, 50.0)
        self.assertEqual(history[1].score, 75.0)

    def test_history_respects_timestamp_order(self):
        """Test that history entries are ordered by timestamp."""
        self.add_scored_submission(self.at(1), 30.0)
        self.add_scored_submission(self.at(3), 70.0)
        self.add_scored_submission(self.at(2), 50.0)
        self.session.flush()

        rebuild_score_cache(self.session, self.participation, self.task)
        history = self.get_history_entries()

        # History should be ordered by timestamp
        self.assertEqual(history[0].timestamp, self.at(1))
        self.assertEqual(history[1].timestamp, self.at(2))
        self.assertEqual(history[2].timestamp, self.at(3))


class TestMaxSubtaskEdgeCases(ScoreCacheMixin, unittest.TestCase):
    """Tests for edge cases in SCORE_MODE_MAX_SUBTASK."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX_SUBTASK

    def test_empty_score_details_with_zero_score(self):
        """Test handling of empty score_details with zero score (compile failure)."""
        self.add_scored_submission(self.at(1), 0.0, score_details=[])
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 0.0)
        self.assertIsNone(cache_entry.subtask_max_scores)

    def test_empty_score_details_with_nonzero_score(self):
        """Test handling of empty score_details with non-zero score."""
        self.add_scored_submission(self.at(1), 50.0, score_details=[])
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        # Should create a single subtask with full score
        self.assertEqual(cache_entry.score, 50.0)
        self.assertEqual(cache_entry.subtask_max_scores, {"1": 50.0})

    def test_subtask_key_normalization(self):
        """Test that subtask keys are normalized to strings."""
        self.add_scored_submission(
            self.at(1), 30.0,
            score_details=[
                {"idx": 1, "score": 15.0},  # Integer idx
                {"idx": "2", "score": 15.0},  # String idx
            ]
        )
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        # Both keys should be strings
        self.assertIn("1", cache_entry.subtask_max_scores)
        self.assertIn("2", cache_entry.subtask_max_scores)

    def test_incremental_update_new_subtasks(self):
        """Test incremental update when new subtasks appear."""
        # First submission with subtasks 1 and 2
        submission1 = self.add_scored_submission(
            self.at(1), 30.0,
            score_details=[
                {"idx": "1", "score": 15.0},
                {"idx": "2", "score": 15.0},
            ]
        )
        self.session.flush()
        update_score_cache(self.session, submission1)

        # Second submission with subtasks 2 and 3
        submission2 = self.add_scored_submission(
            self.at(2), 35.0,
            score_details=[
                {"idx": "2", "score": 20.0},
                {"idx": "3", "score": 15.0},
            ]
        )
        self.session.flush()
        update_score_cache(self.session, submission2)

        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 50.0)  # 15 + 20 + 15
        self.assertEqual(cache_entry.subtask_max_scores, {
            "1": 15.0,
            "2": 20.0,
            "3": 15.0,
        })


class TestMaxTokenedLastEdgeCases(ScoreCacheMixin, unittest.TestCase):
    """Tests for edge cases in SCORE_MODE_MAX_TOKENED_LAST."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX_TOKENED_LAST

    def test_tokened_better_than_last_uses_tokened(self):
        """Test that tokened score is used when it's better than last."""
        self.add_scored_submission(self.at(1), 80.0, tokened=True)
        self.add_scored_submission(self.at(2), 60.0, tokened=False)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 80.0)
        self.assertEqual(cache_entry.max_tokened_score, 80.0)
        self.assertEqual(cache_entry.last_submission_score, 60.0)

    def test_last_better_than_tokened_uses_last(self):
        """Test that last score is used when it's better than max tokened."""
        self.add_scored_submission(self.at(1), 60.0, tokened=True)
        self.add_scored_submission(self.at(2), 90.0, tokened=False)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 90.0)
        self.assertEqual(cache_entry.max_tokened_score, 60.0)
        self.assertEqual(cache_entry.last_submission_score, 90.0)

    def test_incremental_update_tokened_last(self):
        """Test incremental update with SCORE_MODE_MAX_TOKENED_LAST."""
        submission1 = self.add_scored_submission(self.at(1), 60.0, tokened=True)
        self.session.flush()
        update_score_cache(self.session, submission1)

        submission2 = self.add_scored_submission(self.at(2), 80.0, tokened=False)
        self.session.flush()
        update_score_cache(self.session, submission2)

        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 80.0)

        submission3 = self.add_scored_submission(self.at(3), 50.0, tokened=False)
        self.session.flush()
        update_score_cache(self.session, submission3)

        cache_entry = self.get_cache_entry()
        self.assertEqual(cache_entry.score, 60.0)  # Falls back to tokened

    def test_no_tokened_submissions(self):
        """Test behavior when no submissions are tokened."""
        self.add_scored_submission(self.at(1), 50.0, tokened=False)
        self.add_scored_submission(self.at(2), 80.0, tokened=False)
        self.add_scored_submission(self.at(3), 60.0, tokened=False)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        # Score should be last submission score (no tokened to compare)
        self.assertEqual(cache_entry.score, 60.0)
        self.assertEqual(cache_entry.max_tokened_score, 0.0)
        self.assertEqual(cache_entry.last_submission_score, 60.0)


class TestZeroAndEdgeScores(ScoreCacheMixin, unittest.TestCase):
    """Tests for handling zero and edge case scores."""

    def setUp(self):
        super().setUp()
        self.task.score_mode = SCORE_MODE_MAX

    def test_zero_score_handling(self):
        """Test that zero scores are handled correctly."""
        self.add_scored_submission(self.at(1), 0.0)
        self.add_scored_submission(self.at(2), 25.0)
        self.add_scored_submission(self.at(3), 0.0)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 25.0)

    def test_all_zero_scores(self):
        """Test behavior when all scores are zero."""
        self.add_scored_submission(self.at(1), 0.0)
        self.add_scored_submission(self.at(2), 0.0)
        self.session.flush()

        cache_entry = rebuild_score_cache(
            self.session, self.participation, self.task)
        self.assertEqual(cache_entry.score, 0.0)
        self.assertTrue(cache_entry.has_submissions)


if __name__ == "__main__":
    unittest.main()
