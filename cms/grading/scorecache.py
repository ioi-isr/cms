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

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from cms.db import (
    Participation, Task, Submission, ParticipationTaskScore, ScoreHistory
)
from cmscommon.constants import (
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST
)


logger = logging.getLogger(__name__)


__all__ = [
    "update_score_cache",
    "invalidate_score_cache",
    "rebuild_score_cache",
    "get_cached_score",
]


def update_score_cache(
    session: Session,
    submission: Submission,
) -> None:
    """Update the score cache after a submission is scored.

    This function updates the cached score for the participation/task
    pair of the given submission. It also adds a history entry if the
    score changed.

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

    cache_entry = _get_or_create_cache_entry(session, participation, task)
    old_score = cache_entry.score

    _update_cache_entry_from_submissions(session, cache_entry, participation, task)

    if cache_entry.score != old_score:
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
    """Invalidate and rebuild the score cache for the given scope.

    This function deletes cached scores and history entries for the
    specified scope, then rebuilds them from scratch. Unlike simply
    deleting, this ensures history is preserved by recomputing it
    from the submission data under the current scoring parameters.

    session: the database session.
    participation_id: if specified, only invalidate for this participation.
    task_id: if specified, only invalidate for this task.
    contest_id: if specified, only invalidate for this contest.

    """
    from cms.db import Contest

    participations_to_rebuild: list[tuple[Participation, Task]] = []

    if participation_id is not None and task_id is not None:
        participation = session.query(Participation).get(participation_id)
        task = session.query(Task).get(task_id)
        if participation is not None and task is not None:
            participations_to_rebuild.append((participation, task))
    elif participation_id is not None:
        participation = session.query(Participation).get(participation_id)
        if participation is not None:
            for task in participation.contest.tasks:
                participations_to_rebuild.append((participation, task))
    elif task_id is not None:
        task = session.query(Task).get(task_id)
        if task is not None:
            contest = task.contest
            for participation in contest.participations:
                participations_to_rebuild.append((participation, task))
    elif contest_id is not None:
        contest = session.query(Contest).get(contest_id)
        if contest is not None:
            for participation in contest.participations:
                for task in contest.tasks:
                    participations_to_rebuild.append((participation, task))

    for participation, task in participations_to_rebuild:
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
            last_update=datetime.utcnow(),
        )
        session.add(cache_entry)

    return cache_entry


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
        cache_entry.last_update = datetime.utcnow()
        return

    submissions_sorted = sorted(submissions, key=lambda s: s.timestamp)

    partial = False
    subtask_max_scores: dict[int, float] = {}
    max_score = 0.0
    max_tokened_score = 0.0
    last_submission_score = None

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

        if s.tokened():
            max_tokened_score = max(max_tokened_score, score)

        if task.score_mode == SCORE_MODE_MAX_SUBTASK:
            if score_details == [] and score == 0.0:
                continue

            try:
                subtask_scores = dict(
                    (subtask["idx"], subtask["score"])
                    for subtask in score_details
                )
            except Exception:
                subtask_scores = None

            if subtask_scores is None or len(subtask_scores) == 0:
                subtask_scores = {1: score}

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

    subtask_max_scores: dict[int, float] = {}
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
                    (subtask["idx"], subtask["score"])
                    for subtask in score_details
                )
            except Exception:
                subtask_scores = None

            if subtask_scores is None or len(subtask_scores) == 0:
                subtask_scores = {1: score}

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
