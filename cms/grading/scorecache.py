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

"""Score cache utilities for AWS ranking performance.

This module provides functions to update and query the score cache,
which stores pre-computed task scores for each participation to speed
up ranking page loading.

"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from cms.db import (
    Participation, Task, Submission,
    ParticipationTaskScore, ScoreHistory
)
from cmscommon.constants import (
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST
)


__all__ = [
    "ensure_valid_history",
    "get_cached_score_entry",
    "invalidate_score_cache",
    "rebuild_score_cache",
    "rebuild_score_history",
    "update_score_cache",
]


@dataclass
class ScoreAccumulator:
    """Accumulates score data from submissions for computing final scores.

    This class encapsulates the common logic for tracking score-related state
    across submissions, used by both cache rebuilding and history rebuilding.
    It handles all score modes: MAX, MAX_SUBTASK, and MAX_TOKENED_LAST.

    Note on SCORE_MODE_MAX_TOKENED_LAST semantics:
    This class only processes scored submissions (unscored submissions are
    skipped by callers). For MAX_TOKENED_LAST, task_score() in scoring.py
    treats the chronologically last submission's score as 0.0 if it's unscored,
    which can cause the displayed score to drop. The cache instead tracks
    last_submission_score as the last *scored* submission's score. This means
    the cache may show a higher score than task_score() when the newest
    submission is unscored. This is intentional: the cache shows the "stable"
    score while the partial indicator (*) signals that scoring is in progress.
    """

    max_score: float = 0.0
    max_tokened_score: float = 0.0
    last_submission_score: float | None = None
    last_submission_timestamp: datetime | None = None
    subtask_max_scores: dict[str, float] = field(default_factory=dict)
    has_submissions: bool = False

    def process_submission(
        self,
        score: float,
        score_details: list,
        timestamp: datetime,
        tokened: bool,
        score_mode: str,
    ) -> None:
        """Process a single scored submission and update accumulated state.

        score: the submission's score (must not be None).
        score_details: the submission's score_details.
        timestamp: the submission's timestamp.
        tokened: whether the submission was tokened.
        score_mode: the task's score mode.
        """
        self.has_submissions = True
        self.max_score = max(self.max_score, score)
        self.last_submission_score = score
        self.last_submission_timestamp = timestamp

        if tokened:
            self.max_tokened_score = max(self.max_tokened_score, score)

        if score_mode == SCORE_MODE_MAX_SUBTASK:
            subtask_scores = _parse_subtask_scores(score_details, score)
            # Skip submissions with no subtask data (score_details=[] and score=0).
            # This indicates a compile failure - we can't extract per-subtask scores.
            # We still track max_score for fallback in compute_final_score.
            if subtask_scores is not None:
                for idx, st_score in subtask_scores.items():
                    self.subtask_max_scores[idx] = max(
                        self.subtask_max_scores.get(idx, 0.0), st_score
                    )

    def compute_final_score(self, task: "Task") -> float:
        """Compute the final score based on task score mode.

        task: the task (used for score_mode and score_precision).

        Returns the rounded final score.
        """
        if task.score_mode == SCORE_MODE_MAX:
            final_score = self.max_score
        elif task.score_mode == SCORE_MODE_MAX_SUBTASK:
            final_score = sum(self.subtask_max_scores.values()) if self.subtask_max_scores else 0.0
        elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
            last_score = self.last_submission_score if self.last_submission_score is not None else 0.0
            final_score = max(last_score, self.max_tokened_score)
        else:
            final_score = self.max_score

        return round(final_score, task.score_precision)


def _parse_subtask_scores(score_details, score: float) -> dict[str, float] | None:
    """Parse subtask scores from score_details.

    Returns a dict mapping subtask index (as string) to score,
    or None if score_details indicates no score (empty list with score 0).

    This is similar to the subtask parsing in cms/grading/scoring.py task_score().
    """
    if score_details == [] and score == 0.0:
        return None

    try:
        subtask_scores = dict(
            (str(subtask["idx"]), subtask["score"])
            for subtask in score_details
        )
    except (KeyError, TypeError):
        subtask_scores = None

    if subtask_scores is None or len(subtask_scores) == 0:
        subtask_scores = {"1": score}

    return subtask_scores


def _utc_now() -> datetime:
    """Return current UTC time as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _acquire_cache_lock(
    session: Session,
    participation_id: int,
    task_id: int,
) -> None:
    """Acquire an advisory lock for a (participation, task) pair.

    This uses PostgreSQL's pg_advisory_xact_lock to serialize all operations
    on a given (participation_id, task_id) pair. The lock is transaction-scoped
    and automatically released on commit or rollback.

    This solves the race condition where SELECT ... FOR UPDATE cannot lock
    a non-existent row, allowing two concurrent sessions to both try to
    create a new cache entry.

    session: the database session.
    participation_id: the participation ID.
    task_id: the task ID.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(:p_id, :t_id)"),
        {"p_id": participation_id, "t_id": task_id}
    )


def _invalidate(
    session: Session,
    pt_filter,
    history_filter,
) -> None:
    """Helper to invalidate cache entries and delete history.

    Sets invalidated_at to current timestamp via bulk UPDATE. Validity is
    determined by created_at > invalidated_at comparison.

    pt_filter: filter conditions for ParticipationTaskScore query.
    history_filter: filter conditions for ScoreHistory query.
    """
    now = _utc_now()

    # Bulk update invalidated_at - simple and fast
    session.query(ParticipationTaskScore).filter(
        *pt_filter
    ).update(
        {ParticipationTaskScore.invalidated_at: now},
        synchronize_session="fetch",
    )

    session.query(ScoreHistory).filter(
        *history_filter
    ).delete(synchronize_session=False)


def update_score_cache(
    session: Session,
    submission: Submission,
) -> None:
    """Update the score cache incrementally after a submission is scored.

    This function updates the cached score for the participation/task
    pair of the given submission using O(1) incremental updates instead
    of recomputing from all submissions.

    IMPORTANT - Locking and Transaction Behavior:
    This function acquires a PostgreSQL advisory lock (pg_advisory_xact_lock)
    for the (participation_id, task_id) pair. The lock is transaction-scoped
    and is released when the transaction ends (commit or rollback).

    Caller Responsibility:
    - The caller MUST commit or rollback the session to persist changes
      and release the lock. Without commit, changes are lost on session close.
    - Do NOT call this function or any other function that carries this
      warning with the same (participation, task) pair within the same
      transaction without committing in between, as the second call will block
      waiting for the lock (self-deadlock).
    - Do NOT call ensure_valid_history with a contest that contains the same
      (participation, task) pair whithin the same transaction without committing
      in between, as this might also cause a self-deadlock.

    session: the database session.
    submission: the submission that was just scored.

    """
    if not submission.official:
        return

    task = submission.task
    dataset = task.active_dataset
    if dataset is None:
        return

    submission_result = submission.get_result(dataset)
    if submission_result is None or not submission_result.scored():
        return

    score = submission_result.score
    if score is None:
        return

    # Acquire advisory lock to serialize concurrent updates
    participation = submission.participation
    _acquire_cache_lock(session, participation.id, task.id)

    cache_entry = _get_or_create_cache_entry(session, participation, task)
    old_score = cache_entry.score

    # Incremental update based on score mode
    _update_cache_entry_incremental(
        cache_entry, task, submission, submission_result
    )

    # Mark history as invalid if submission arrived out of order
    if (cache_entry.last_submission_timestamp is not None and
            submission.timestamp < cache_entry.last_submission_timestamp):
        cache_entry.history_valid = False

    # Update has_submissions flag (partial is computed at render time)
    cache_entry.has_submissions = True

    cache_entry.last_update = _utc_now()

    # Only add history entry if score changed and history is still valid
    if cache_entry.score != old_score and cache_entry.history_valid:
        _add_history_entry(
            session, participation, task, submission,
            cache_entry.score
        )


def invalidate_score_cache(
    session: Session,
    participation_id: int | None = None,
    task_id: int | None = None,
    contest_id: int | None = None,
) -> None:
    """Invalidate the score cache for the given scope.

    Marks cache entries as stale by setting `invalidated_at` to now and deleting
    associated history rows. Rebuilds are then triggered lazily by
    `get_cached_score_entry()`, which considers an entry valid only if
    `created_at > invalidated_at`. This timestamp-based approach makes
    invalidation safe even if it interleaves with a concurrent rebuild.

    At least one of participation_id, task_id, or contest_id must be provided.

    session: the database session.
    participation_id: if specified, only invalidate for this participation.
    task_id: if specified, only invalidate for this task.
    contest_id: if specified, only invalidate for this contest.

    Raises:
        ValueError: if no filter parameters are provided, or if contest_id
            is provided but the contest does not exist.

    """
    if participation_id is None and task_id is None and contest_id is None:
        raise ValueError(
            "At least one of participation_id, task_id, or contest_id must be provided"
        )

    if participation_id is not None and task_id is not None:
        _invalidate(
            session,
            pt_filter=(
                ParticipationTaskScore.participation_id == participation_id,
                ParticipationTaskScore.task_id == task_id,
            ),
            history_filter=(
                ScoreHistory.participation_id == participation_id,
                ScoreHistory.task_id == task_id,
            ),
        )
    elif participation_id is not None:
        _invalidate(
            session,
            pt_filter=(ParticipationTaskScore.participation_id == participation_id,),
            history_filter=(ScoreHistory.participation_id == participation_id,),
        )
    elif task_id is not None:
        _invalidate(
            session,
            pt_filter=(ParticipationTaskScore.task_id == task_id,),
            history_filter=(ScoreHistory.task_id == task_id,),
        )
    elif contest_id is not None:
        from cms.db import Contest
        contest = session.query(Contest).get(contest_id)
        if contest is None:
            raise ValueError(f"Contest with id {contest_id} not found")
        participation_ids = [p.id for p in contest.participations]
        if participation_ids:
            _invalidate(
                session,
                pt_filter=(
                    ParticipationTaskScore.participation_id.in_(participation_ids),
                ),
                history_filter=(
                    ScoreHistory.participation_id.in_(participation_ids),
                ),
            )


def rebuild_score_cache(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Rebuild the score cache for a participation/task pair.

    This function recalculates the cached score from all submissions
    and rebuilds the history. Unlike deletion-based rebuilds, this
    updates the cache entry in-place to preserve the invalidated_at
    timestamp for proper race condition handling.

    The created_at timestamp is set at the START of the rebuild. This
    ensures that if an invalidation occurs during the rebuild, the
    resulting cache entry will be correctly marked as stale (since
    created_at < invalidated_at).

    IMPORTANT - Locking and Transaction Behavior:
    This function acquires a PostgreSQL advisory lock (pg_advisory_xact_lock)
    for the (participation_id, task_id) pair. The lock is transaction-scoped
    and is released when the transaction ends (commit or rollback).

    Caller Responsibility:
    - The caller MUST commit or rollback the session to persist changes
      and release the lock. Without commit, changes are lost on session close.
    - Do NOT call this function or any other function that carries this
      warning with the same (participation, task) pair within the same
      transaction without committing in between, as the second call will block
      waiting for the lock (self-deadlock).
    - Do NOT call ensure_valid_history with a contest that contains the same
      (participation, task) pair whithin the same transaction without committing
      in between, as this might also cause a self-deadlock.

    session: the database session.
    participation: the participation.
    task: the task.

    returns: the updated cache entry.

    """
    # Acquire advisory lock to serialize concurrent operations
    _acquire_cache_lock(session, participation.id, task.id)

    # Set created_at at the START of rebuild - this is critical for
    # timestamp-based validity. If invalidation happens during rebuild,
    # created_at will be < invalidated_at, marking the result as stale.
    rebuild_start_time = _utc_now()

    # Delete history entries (we'll rebuild them)
    session.query(ScoreHistory).filter(
        ScoreHistory.participation_id == participation.id,
        ScoreHistory.task_id == task.id,
    ).delete(synchronize_session=False)

    # Get or create cache entry (don't delete - preserve invalidated_at)
    cache_entry = _get_or_create_cache_entry(session, participation, task)

    # Set created_at to the rebuild start time
    cache_entry.created_at = rebuild_start_time

    _update_cache_entry_from_submissions(session, cache_entry, participation, task)
    _rebuild_history(session, participation, task)

    return cache_entry


def rebuild_score_history(
    session: Session,
    participation: Participation,
    task: Task,
) -> None:
    """Rebuild only the score history for a participation/task pair.

    This function rebuilds the history without recalculating the cached
    score. Use this when history_valid is False but the score itself
    is still correct (e.g., when submissions arrived out of order).

    IMPORTANT - Locking and Transaction Behavior:
    This function acquires a PostgreSQL advisory lock (pg_advisory_xact_lock)
    for the (participation_id, task_id) pair. The lock is transaction-scoped
    and is released when the transaction ends (commit or rollback).

    Caller Responsibility:
    - The caller MUST commit or rollback the session to persist changes
      and release the lock. Without commit, changes are lost on session close.
    - Do NOT call this function or any other function that carries this
      warning with the same (participation, task) pair within the same
      transaction without committing in between, as the second call will block
      waiting for the lock (self-deadlock).
    - Do NOT call ensure_valid_history with a contest that contains the same
      (participation, task) pair whithin the same transaction without committing
      in between, as this might also cause a self-deadlock.

    session: the database session.
    participation: the participation.
    task: the task.

    """
    # Acquire advisory lock to serialize concurrent operations
    _acquire_cache_lock(session, participation.id, task.id)

    # Delete existing history entries
    session.query(ScoreHistory).filter(
        ScoreHistory.participation_id == participation.id,
        ScoreHistory.task_id == task.id,
    ).delete(synchronize_session=False)

    # Rebuild history from submissions
    _rebuild_history(session, participation, task)

    # Mark history as valid
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is not None:
        cache_entry.history_valid = True


def _is_cache_valid(cache_entry: ParticipationTaskScore) -> bool:
    """Check if a cache entry is valid using timestamp-based validity.

    A cache entry is valid if created_at > invalidated_at (or invalidated_at
    is NULL). If created_at is NULL, the entry needs to be rebuilt.

    The timestamp check ensures that if an invalidation occurred during
    a rebuild, the rebuild result is correctly marked as stale.
    """
    # If created_at is not set, needs rebuild
    if cache_entry.created_at is None:
        return False

    # If never invalidated, it's valid
    if cache_entry.invalidated_at is None:
        return True

    # Valid if created after last invalidation
    return cache_entry.created_at > cache_entry.invalidated_at


def get_cached_score_entry(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Get the cached score entry for a participation/task pair.

    If no cache entry exists or if the cache is invalid, rebuilds the cache
    by calling rebuild_score_cache(). Validity is determined by timestamp
    comparison: created_at > invalidated_at.
    This returns the full cache entry including has_submissions.

    IMPORTANT - Locking and Transaction Behavior:
    If a rebuild is triggered, this function acquires a PostgreSQL advisory
    lock (pg_advisory_xact_lock) for the (participation_id, task_id) pair.
    The lock is transaction-scoped and is released when the transaction ends
    (commit or rollback).

    Caller Responsibility:
    - The caller MUST commit or rollback the session to persist possible
      changes and release the lock. Without commit, changes are lost.
    - Do NOT call this function or any other function that carries this
      warning with the same (participation, task) pair within the same
      transaction without committing in between, as the second call will block
      waiting for the lock (self-deadlock).
    - Do NOT call ensure_valid_history with a contest that contains the same
      (participation, task) pair whithin the same transaction without committing
      in between, as this might also cause a self-deadlock.

    session: the database session.
    participation: the participation.
    task: the task.

    return: the cached score entry.

    """
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is None or not _is_cache_valid(cache_entry):
        cache_entry = rebuild_score_cache(session, participation, task)

    return cache_entry


def ensure_valid_history(
    session: Session,
    contest_id: int,
) -> bool:
    """Ensure all score history for a contest is valid.

    This function finds all cache entries with invalid scores or history
    for the given contest and rebuilds them. This should be called before
    querying score history to ensure the data is up-to-date.

    Uses timestamp-based validity checking (created_at > invalidated_at).

    Entries are processed in (participation_id, task_id) order to prevent
    deadlocks when multiple concurrent calls try to rebuild different pairs.

    IMPORTANT - Locking and Transaction Behavior:
    This function may acquire PostgreSQL advisory locks (pg_advisory_xact_lock)
    for multiple (participation_id, task_id) pairs if rebuilds are needed.
    The locks are transaction-scoped and are released when the transaction
    ends (commit or rollback).

    Caller Responsibility if any entries were rebuilt (function returns true):
    - The caller MUST commit or rollback the session to persist changes
      and release the locks. Without commit, changes are lost on session close.
    - Do NOT call this function or any other function that carries this warning
      again within the same transaction for any (participation, training) pair
      belonging to the same contest without committing in between, as the second
      call will block waiting for the lock (self-deadlock), unless you have
      verified that no overlapping (participation, training) pairs are involved.

    session: the database session.
    contest_id: the contest ID to check.

    returns: True if any entries were rebuilt, False otherwise.

    """
    from sqlalchemy.orm import joinedload
    from cms.db import Participation

    # Get all entries for this contest
    all_entries = (
        session.query(ParticipationTaskScore)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .options(joinedload(ParticipationTaskScore.participation))
        .options(joinedload(ParticipationTaskScore.task))
        .order_by(
            ParticipationTaskScore.participation_id,
            ParticipationTaskScore.task_id,
        )
        .all()
    )

    # Filter entries with invalid scores using timestamp-based validity
    invalid_score_entries = [
        e for e in all_entries if not _is_cache_valid(e)
    ]

    for entry in invalid_score_entries:
        rebuild_score_cache(session, entry.participation, entry.task)

    # Then rebuild any entries with invalid history (but valid scores)
    # Filter from all_entries to use timestamp-based validity
    invalid_history_entries = [
        e for e in all_entries
        if _is_cache_valid(e) and not e.history_valid
    ]

    for entry in invalid_history_entries:
        rebuild_score_history(session, entry.participation, entry.task)

    return bool(invalid_score_entries or invalid_history_entries)


def _get_or_create_cache_entry(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Get or create a cache entry for a participation/task pair.

    This function assumes the caller has already acquired the advisory lock
    for this (participation, task) pair via _acquire_cache_lock(). This
    ensures no race conditions when creating new entries.

    Note: created_at is set by the caller (rebuild_score_cache) at the
    START of the rebuild operation, not here. This ensures proper
    timestamp-based validity checking.
    """
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is None:
        now = _utc_now()
        cache_entry = ParticipationTaskScore(
            participation=participation,
            task=task,
            score=0.0,
            subtask_max_scores=None,
            max_tokened_score=0.0,
            last_submission_score=None,
            last_submission_timestamp=None,
            history_valid=True,
            has_submissions=False,
            last_update=now,
            created_at=now,  # Set created_at for new entries
            invalidated_at=None,  # No invalidation yet
        )
        session.add(cache_entry)

    return cache_entry


def _update_cache_entry_incremental(
    cache_entry: ParticipationTaskScore,
    task: Task,
    submission: Submission,
    submission_result,
) -> None:
    """Update cache entry incrementally based on a single submission.

    This is O(1) for SCORE_MODE_MAX and O(subtasks) for SCORE_MODE_MAX_SUBTASK,
    much faster than recomputing from all submissions.
    """
    score = submission_result.score
    score_details = submission_result.score_details

    # Update max_tokened_score if this submission is tokened
    if submission.tokened():
        cache_entry.max_tokened_score = max(
            cache_entry.max_tokened_score or 0.0, score
        )

    # Update last_submission_score/timestamp if this is the latest submission
    if (cache_entry.last_submission_timestamp is None or
            submission.timestamp >= cache_entry.last_submission_timestamp):
        cache_entry.last_submission_score = score
        cache_entry.last_submission_timestamp = submission.timestamp

    # Update score based on score mode
    if task.score_mode == SCORE_MODE_MAX:
        # Simple max - just compare with current score
        new_score = max(cache_entry.score or 0.0, score)
        cache_entry.score = round(new_score, task.score_precision)

    elif task.score_mode == SCORE_MODE_MAX_SUBTASK:
        # Update per-subtask max scores
        # Normalize keys to strings since JSONB stores keys as strings
        subtask_max_scores = {
            str(k): v for k, v in (cache_entry.subtask_max_scores or {}).items()
        }

        subtask_scores = _parse_subtask_scores(score_details, score)
        if subtask_scores is not None:
            for idx, st_score in subtask_scores.items():
                subtask_max_scores[idx] = max(
                    subtask_max_scores.get(idx, 0.0), st_score
                )

        cache_entry.subtask_max_scores = subtask_max_scores if subtask_max_scores else None
        new_score = sum(subtask_max_scores.values()) if subtask_max_scores else 0.0
        cache_entry.score = round(new_score, task.score_precision)

    elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
        # Score is max of last submission score and max tokened score
        last_score = cache_entry.last_submission_score or 0.0
        tokened_score = cache_entry.max_tokened_score or 0.0
        new_score = max(last_score, tokened_score)
        cache_entry.score = round(new_score, task.score_precision)

    else:
        # Default to max mode
        new_score = max(cache_entry.score or 0.0, score)
        cache_entry.score = round(new_score, task.score_precision)


def _get_sorted_official_submissions(
    session: Session,
    participation: Participation,
    task: Task,
) -> list[Submission]:
    """Get official submissions for a task, sorted by timestamp.

    For training day participations, submissions are stored with the managing
    contest's participation, so we need to query from there and filter by
    training_day_id.

    Raises:
        ValueError: When managing participation is None for training days
    """
    from cms.db.training_day import get_managing_participation

    training_day = participation.contest.training_day
    if training_day is not None:
        # This is a training day participation - submissions are stored with
        # the managing contest's participation
        managing_participation = get_managing_participation(
            session, training_day, participation.user
        )

        if managing_participation is None:
            # User doesn't have a participation in the managing contest
            # This indicates a configuration or data integrity issue
            raise ValueError(
                f"User {participation.user_id} does not have participation in managing contest "
                f"{training_day.training_program.managing_contest_id} for training day {training_day.id}"
            )

        return session.query(Submission).filter(
            Submission.participation_id == managing_participation.id,
            Submission.task_id == task.id,
            Submission.training_day_id == training_day.id,
            Submission.official.is_(True)
        ).order_by(Submission.timestamp.asc()).all()

    # Regular contest - query submissions directly
    return session.query(Submission).filter(
        Submission.participation_id == participation.id,
        Submission.task_id == task.id,
        Submission.official.is_(True)
    ).order_by(Submission.timestamp.asc()).all()


def _update_cache_entry_from_submissions(
    session: Session,
    cache_entry: ParticipationTaskScore,
    participation: Participation,
    task: Task,
) -> None:
    """Update a cache entry by processing all submissions."""
    dataset = task.active_dataset
    if dataset is None:
        return

    submissions_sorted = _get_sorted_official_submissions(session, participation, task)

    if len(submissions_sorted) == 0:
        cache_entry.score = 0.0
        cache_entry.subtask_max_scores = None
        cache_entry.max_tokened_score = 0.0
        cache_entry.last_submission_score = None
        cache_entry.last_submission_timestamp = None
        cache_entry.history_valid = True
        cache_entry.has_submissions = False
        cache_entry.last_update = _utc_now()
        return

    accumulator = ScoreAccumulator()

    for s in submissions_sorted:
        sr = s.get_result(dataset)
        if sr is None or not sr.scored():
            continue

        score = sr.score
        if score is None:
            continue

        accumulator.process_submission(
            score=score,
            score_details=sr.score_details,
            timestamp=s.timestamp,
            tokened=s.tokened(),
            score_mode=task.score_mode,
        )

    cache_entry.score = accumulator.compute_final_score(task)
    cache_entry.subtask_max_scores = accumulator.subtask_max_scores if accumulator.subtask_max_scores else None
    cache_entry.max_tokened_score = accumulator.max_tokened_score
    cache_entry.last_submission_score = accumulator.last_submission_score
    cache_entry.last_submission_timestamp = accumulator.last_submission_timestamp
    cache_entry.history_valid = True
    cache_entry.has_submissions = accumulator.has_submissions
    cache_entry.last_update = _utc_now()


def _add_history_entry(
    session: Session,
    participation: Participation,
    task: Task,
    submission: Submission,
    score: float,
) -> None:
    """Add a history entry for a score change."""
    history_entry = ScoreHistory(
        participation=participation,
        task=task,
        timestamp=submission.timestamp,
        score=score,
        submission=submission,
    )
    session.add(history_entry)


def _rebuild_history(
    session: Session,
    participation: Participation,
    task: Task,
) -> None:
    """Rebuild the score history for a participation/task pair."""
    dataset = task.active_dataset
    if dataset is None:
        return

    submissions_sorted = _get_sorted_official_submissions(session, participation, task)

    if len(submissions_sorted) == 0:
        return

    accumulator = ScoreAccumulator()
    current_score = 0.0

    for s in submissions_sorted:
        sr = s.get_result(dataset)
        if sr is None or not sr.scored():
            continue

        score = sr.score
        if score is None:
            continue

        accumulator.process_submission(
            score=score,
            score_details=sr.score_details,
            timestamp=s.timestamp,
            tokened=s.tokened(),
            score_mode=task.score_mode,
        )

        new_score = accumulator.compute_final_score(task)

        if new_score != current_score:
            _add_history_entry(session, participation, task, s, new_score)
            current_score = new_score
