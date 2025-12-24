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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cms.db import (
    Participation, Task, Submission,
    ParticipationTaskScore, ScoreHistory
)
from cmscommon.constants import (
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST
)


__all__ = [
    "update_score_cache",
    "invalidate_score_cache",
    "rebuild_score_cache",
    "rebuild_score_history",
    "get_cached_score_entry",
    "ensure_valid_history",
]


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


def _compute_final_score(
    task: Task,
    max_score: float,
    subtask_max_scores: dict[str, float],
    last_submission_score: float | None,
    max_tokened_score: float,
) -> float:
    """Compute the final score based on task score mode.

    Returns the rounded final score.
    """
    if task.score_mode == SCORE_MODE_MAX:
        final_score = max_score
    elif task.score_mode == SCORE_MODE_MAX_SUBTASK:
        final_score = sum(subtask_max_scores.values())
    elif task.score_mode == SCORE_MODE_MAX_TOKENED_LAST:
        last_score = last_submission_score if last_submission_score is not None else 0.0
        final_score = max(last_score, max_tokened_score)
    else:
        final_score = max_score

    return round(final_score, task.score_precision)


def _invalidate(
    session: Session,
    pt_filter,
    history_filter,
) -> None:
    """Helper to invalidate cache entries and delete history.

    pt_filter: filter conditions for ParticipationTaskScore query.
    history_filter: filter conditions for ScoreHistory query.
    """
    session.query(ParticipationTaskScore).filter(
        *pt_filter
    ).update(
        {
            ParticipationTaskScore.score_valid: False,
            ParticipationTaskScore.history_valid: False,
        },
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
    cache_entry = _get_or_create_cache_entry(session, participation, task, lock=True)
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

    This function marks cached scores as invalid and deletes history entries
    for the specified scope. The cache will be lazily rebuilt when accessed
    via get_cached_score_entry().

    By marking as invalid instead of deleting, we ensure that endpoints can
    reliably detect when a rebuild is needed.

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
    and rebuilds the history. It deletes any existing cache entry and
    history, then creates a fresh cache entry.

    Uses synchronize_session="fetch" for the ParticipationTaskScore delete
    to ensure the session is updated and _get_or_create_cache_entry won't
    return a stale/deleted entry.

    session: the database session.
    participation: the participation.
    task: the task.

    returns: the updated cache entry.

    """
    session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).delete(synchronize_session="fetch")

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


def get_cached_score_entry(
    session: Session,
    participation: Participation,
    task: Task,
) -> ParticipationTaskScore:
    """Get the cached score entry for a participation/task pair.

    If no cache entry exists or if the cache is marked invalid (score_valid=False),
    rebuilds the cache. This returns the full cache entry including has_submissions.

    session: the database session.
    participation: the participation.
    task: the task.

    return: the cached score entry.

    """
    cache_entry = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    ).first()

    if cache_entry is None or not cache_entry.score_valid:
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

    session: the database session.
    contest_id: the contest ID to check.

    returns: True if any entries were rebuilt, False otherwise.

    """
    from sqlalchemy.orm import joinedload
    from cms.db import Participation

    # First rebuild any entries with invalid scores
    invalid_score_entries = (
        session.query(ParticipationTaskScore)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(ParticipationTaskScore.score_valid.is_(False))
        .options(joinedload(ParticipationTaskScore.participation))
        .options(joinedload(ParticipationTaskScore.task))
        .all()
    )

    for entry in invalid_score_entries:
        rebuild_score_cache(session, entry.participation, entry.task)

    # Then rebuild any entries with invalid history (but valid scores)
    invalid_history_entries = (
        session.query(ParticipationTaskScore)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(ParticipationTaskScore.score_valid.is_(True))
        .filter(ParticipationTaskScore.history_valid.is_(False))
        .options(joinedload(ParticipationTaskScore.participation))
        .options(joinedload(ParticipationTaskScore.task))
        .all()
    )

    for entry in invalid_history_entries:
        rebuild_score_history(session, entry.participation, entry.task)

    return bool(invalid_score_entries or invalid_history_entries)


def _get_or_create_cache_entry(
    session: Session,
    participation: Participation,
    task: Task,
    lock: bool = False,
) -> ParticipationTaskScore:
    """Get or create a cache entry for a participation/task pair.

    If lock=True, uses SELECT ... FOR UPDATE to serialize concurrent
    updates to the same participation/task pair.

    Handles race conditions where two concurrent requests both try to
    create a new entry - if an IntegrityError occurs on flush, we
    rollback and re-query to get the entry created by the other request.
    """
    query = session.query(ParticipationTaskScore).filter(
        ParticipationTaskScore.participation_id == participation.id,
        ParticipationTaskScore.task_id == task.id,
    )
    if lock:
        query = query.with_for_update()

    cache_entry = query.first()

    if cache_entry is None:
        cache_entry = ParticipationTaskScore(
            participation=participation,
            task=task,
            score=0.0,
            subtask_max_scores=None,
            max_tokened_score=0.0,
            last_submission_score=None,
            last_submission_timestamp=None,
            history_valid=True,
            score_valid=True,
            has_submissions=False,
            last_update=datetime.utcnow(),
        )
        session.add(cache_entry)
        if lock:
            try:
                session.flush()
            except IntegrityError:
                # Another request created the entry concurrently
                session.rollback()
                query = session.query(ParticipationTaskScore).filter(
                    ParticipationTaskScore.participation_id == participation.id,
                    ParticipationTaskScore.task_id == task.id,
                ).with_for_update()
                cache_entry = query.first()

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
        cache_entry.subtask_max_scores = None
        cache_entry.max_tokened_score = 0.0
        cache_entry.last_submission_score = None
        cache_entry.last_submission_timestamp = None
        cache_entry.history_valid = True
        cache_entry.score_valid = True
        cache_entry.has_submissions = False
        cache_entry.last_update = datetime.utcnow()
        return

    cache_entry.has_submissions = True
    submissions_sorted = sorted(submissions, key=lambda s: s.timestamp)

    subtask_max_scores: dict[str, float] = {}
    max_score = 0.0
    max_tokened_score = 0.0
    last_submission_score = None
    last_submission_timestamp = None

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
        last_submission_timestamp = s.timestamp

        if s.tokened():
            max_tokened_score = max(max_tokened_score, score)

        if task.score_mode == SCORE_MODE_MAX_SUBTASK:
            subtask_scores = _parse_subtask_scores(score_details, score)
            if subtask_scores is None:
                continue

            for idx, st_score in subtask_scores.items():
                subtask_max_scores[idx] = max(
                    subtask_max_scores.get(idx, 0.0), st_score
                )

    final_score = _compute_final_score(
        task, max_score, subtask_max_scores, last_submission_score, max_tokened_score
    )

    cache_entry.score = final_score
    cache_entry.subtask_max_scores = subtask_max_scores if subtask_max_scores else None
    cache_entry.max_tokened_score = max_tokened_score
    cache_entry.last_submission_score = last_submission_score
    cache_entry.last_submission_timestamp = last_submission_timestamp
    cache_entry.history_valid = True
    cache_entry.score_valid = True
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
            subtask_scores = _parse_subtask_scores(score_details, score)
            if subtask_scores is None:
                continue

            for idx, st_score in subtask_scores.items():
                subtask_max_scores[idx] = max(
                    subtask_max_scores.get(idx, 0.0), st_score
                )

        new_score = _compute_final_score(
            task, max_score, subtask_max_scores, last_submission_score, max_tokened_score
        )

        if new_score != current_score:
            _add_history_entry(session, participation, task, s, new_score)
            current_score = new_score
