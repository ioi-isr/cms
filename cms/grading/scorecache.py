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

from datetime import datetime

from sqlalchemy.orm import Session

from cms.db import (
    Participation, Task, Submission, ParticipationTaskScore, ScoreHistory
)
from cmscommon.constants import (
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST
)


__all__ = [
    "update_score_cache",
    "invalidate_score_cache",
    "rebuild_score_cache",
    "rebuild_score_history",
    "get_cached_score",
]


def update_score_cache(
    session: Session,
    submission: Submission,
) -> None:
    """Update the score cache incrementally after a submission is scored.

    This function updates the cached score for the participation/task
    pair of the given submission using O(1) incremental updates instead
    of recomputing from all submissions.

    Uses row-level locking to ensure concurrent updates are serialized.

    session: the database session.
    submission: the submission that was just scored.

    """
    participation = submission.participation
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

    # Lock the cache row to serialize concurrent updates
    cache_entry = _get_or_create_cache_entry_locked(session, participation, task)
    old_score = cache_entry.score

    # Incremental update based on score mode
    _update_cache_entry_incremental(
        cache_entry, task, submission, submission_result
    )

    # Mark history as invalid if submission arrived out of order
    if (cache_entry.last_submission_timestamp is not None and
            submission.timestamp < cache_entry.last_submission_timestamp):
        cache_entry.history_valid = False

    cache_entry.last_update = datetime.utcnow()

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

    This function deletes cached scores and history entries for the
    specified scope. The cache will be lazily rebuilt when accessed
    via get_cached_score(), or incrementally updated as submissions
    are re-scored.

    This is more efficient than rebuilding immediately during mass
    invalidation, since the cache would just be rebuilt with partial
    data (most submissions are unscored at invalidation time).

    session: the database session.
    participation_id: if specified, only invalidate for this participation.
    task_id: if specified, only invalidate for this task.
    contest_id: if specified, only invalidate for this contest.

    """
    # Build filter conditions based on scope
    if participation_id is not None and task_id is not None:
        session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.participation_id == participation_id,
            ParticipationTaskScore.task_id == task_id,
        ).delete(synchronize_session=False)
        session.query(ScoreHistory).filter(
            ScoreHistory.participation_id == participation_id,
            ScoreHistory.task_id == task_id,
        ).delete(synchronize_session=False)
    elif participation_id is not None:
        session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.participation_id == participation_id,
        ).delete(synchronize_session=False)
        session.query(ScoreHistory).filter(
            ScoreHistory.participation_id == participation_id,
        ).delete(synchronize_session=False)
    elif task_id is not None:
        session.query(ParticipationTaskScore).filter(
            ParticipationTaskScore.task_id == task_id,
        ).delete(synchronize_session=False)
        session.query(ScoreHistory).filter(
            ScoreHistory.task_id == task_id,
        ).delete(synchronize_session=False)
    elif contest_id is not None:
        # Delete all cache entries for participations in this contest
        from cms.db import Contest
        contest = session.query(Contest).get(contest_id)
        if contest is not None:
            participation_ids = [p.id for p in contest.participations]
            if participation_ids:
                session.query(ParticipationTaskScore).filter(
                    ParticipationTaskScore.participation_id.in_(participation_ids),
                ).delete(synchronize_session=False)
                session.query(ScoreHistory).filter(
                    ScoreHistory.participation_id.in_(participation_ids),
                ).delete(synchronize_session=False)


def rebuild_score_cache(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Rebuild the score cache for a participation/task pair.

    This function recalculates the cached score from all submissions
    and rebuilds the history.

    session: the database session.
    participation: the participation.
    task: the task.

    returns: the updated cache entry.

    """
    session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).delete(synchronize_session=False)

    session.query(ScoreHistory).filter(
        ScoreHistory.participation_id == participation.id,
        ScoreHistory.task_id == task.id,
    ).delete(synchronize_session=False)

    cache_entry = _get_or_create_cache_entry(session, participation, task)
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

    session: the database session.
    participation: the participation.
    task: the task.

    """
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


def get_cached_score(
    session: Session,
    participation: Participation,
    task: Task,
) -> tuple[float, bool]:
    """Get the cached score for a participation/task pair.

    If no cache entry exists, creates one and computes the score.

    session: the database session.
    participation: the participation.
    task: the task.

    returns: tuple of (score, partial) where partial is True if not all
        submissions have been scored.

    """
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is None:
        cache_entry = rebuild_score_cache(session, participation, task)

    return cache_entry.score, cache_entry.partial


def _get_or_create_cache_entry(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Get or create a cache entry for a participation/task pair."""
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is None:
        cache_entry = ParticipationTaskScore(
            participation=participation,
            task=task,
            score=0.0,
            partial=False,
            subtask_max_scores=None,
            max_tokened_score=0.0,
            last_submission_score=None,
            last_submission_timestamp=None,
            history_valid=True,
            last_update=datetime.utcnow(),
        )
        session.add(cache_entry)

    return cache_entry


def _get_or_create_cache_entry_locked(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Get or create a cache entry with row-level locking for concurrency.

    Uses SELECT ... FOR UPDATE to serialize concurrent updates to the
    same participation/task pair.
    """
    # Try to get existing entry with lock
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).with_for_update().first()

    if cache_entry is None:
        # Create new entry
        cache_entry = ParticipationTaskScore(
            participation=participation,
            task=task,
            score=0.0,
            partial=False,
            subtask_max_scores=None,
            max_tokened_score=0.0,
            last_submission_score=None,
            last_submission_timestamp=None,
            history_valid=True,
            last_update=datetime.utcnow(),
        )
        session.add(cache_entry)
        # Flush to get the row into the database so we can lock it
        session.flush()

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

        if not (score_details == [] and score == 0.0):
            try:
                subtask_scores = dict(
                    (str(subtask["idx"]), subtask["score"])
                    for subtask in score_details
                )
            except Exception:
                subtask_scores = None

            if subtask_scores is None or len(subtask_scores) == 0:
                subtask_scores = {"1": score}

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

    # Mark as not partial since we just processed a scored submission
    # Note: This is optimistic - we assume if we're processing a submission,
    # most submissions are scored. A full rebuild will set this correctly.
    cache_entry.partial = False


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

    submissions = [s for s in participation.submissions
                   if s.task is task and s.official]

    if len(submissions) == 0:
        cache_entry.score = 0.0
        cache_entry.partial = False
        cache_entry.subtask_max_scores = None
        cache_entry.max_tokened_score = 0.0
        cache_entry.last_submission_score = None
        cache_entry.last_submission_timestamp = None
        cache_entry.history_valid = True
        cache_entry.last_update = datetime.utcnow()
        return

    submissions_sorted = sorted(submissions, key=lambda s: s.timestamp)

    partial = False
    subtask_max_scores: dict[str, float] = {}
    max_score = 0.0
    max_tokened_score = 0.0
    last_submission_score = None
    last_submission_timestamp = None

    for s in submissions_sorted:
        sr = s.get_result(dataset)
        if sr is None or not sr.scored():
            partial = True
            continue

        score = sr.score
        score_details = sr.score_details

        if score is None:
            partial = True
            continue

        max_score = max(max_score, score)
        last_submission_score = score
        last_submission_timestamp = s.timestamp

        if s.tokened():
            max_tokened_score = max(max_tokened_score, score)

        if task.score_mode == SCORE_MODE_MAX_SUBTASK:
            if score_details == [] and score == 0.0:
                continue

            try:
                subtask_scores = dict(
                    (str(subtask["idx"]), subtask["score"])
                    for subtask in score_details
                )
            except Exception:
                subtask_scores = None

            if subtask_scores is None or len(subtask_scores) == 0:
                subtask_scores = {"1": score}

            for idx, st_score in subtask_scores.items():
                subtask_max_scores[idx] = max(
                    subtask_max_scores.get(idx, 0.0), st_score
                )

    if task.score_mode == SCORE_MODE_MAX:
        final_score = max_score
    elif task.score_mode == SCORE_MODE_MAX_SUBTASK:
        final_score = sum(subtask_max_scores.values())
    elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
        last_score = last_submission_score if last_submission_score is not None else 0.0
        final_score = max(last_score, max_tokened_score)
    else:
        final_score = max_score

    final_score = round(final_score, task.score_precision)

    cache_entry.score = final_score
    cache_entry.partial = partial
    cache_entry.subtask_max_scores = subtask_max_scores if subtask_max_scores else None
    cache_entry.max_tokened_score = max_tokened_score
    cache_entry.last_submission_score = last_submission_score
    cache_entry.last_submission_timestamp = last_submission_timestamp
    cache_entry.history_valid = True
    cache_entry.last_update = datetime.utcnow()


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

    submissions = [s for s in participation.submissions
                   if s.task is task and s.official]

    if len(submissions) == 0:
        return

    submissions_sorted = sorted(submissions, key=lambda s: s.timestamp)

    subtask_max_scores: dict[str, float] = {}
    max_score = 0.0
    max_tokened_score = 0.0
    last_submission_score = None
    current_score = 0.0

    for s in submissions_sorted:
        sr = s.get_result(dataset)
        if sr is None or not sr.scored():
            continue

        score = sr.score
        score_details = sr.score_details

        if score is None:
            continue

        max_score = max(max_score, score)
        last_submission_score = score

        if s.tokened():
            max_tokened_score = max(max_tokened_score, score)

        if task.score_mode == SCORE_MODE_MAX_SUBTASK:
            if score_details == [] and score == 0.0:
                continue

            try:
                subtask_scores = dict(
                    (str(subtask["idx"]), subtask["score"])
                    for subtask in score_details
                )
            except Exception:
                subtask_scores = None

            if subtask_scores is None or len(subtask_scores) == 0:
                subtask_scores = {"1": score}

            for idx, st_score in subtask_scores.items():
                subtask_max_scores[idx] = max(
                    subtask_max_scores.get(idx, 0.0), st_score
                )

        if task.score_mode == SCORE_MODE_MAX:
            new_score = max_score
        elif task.score_mode == SCORE_MODE_MAX_SUBTASK:
            new_score = sum(subtask_max_scores.values())
        elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
            last_score = last_submission_score if last_submission_score is not None else 0.0
            new_score = max(last_score, max_tokened_score)
        else:
            new_score = max_score

        new_score = round(new_score, task.score_precision)

        if new_score != current_score:
            _add_history_entry(session, participation, task, s, new_score)
            current_score = new_score
