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

"""Score cache database interface for SQLAlchemy.

This module provides caching for participation task scores to speed up
ranking page loading in AWS. It also stores score history for displaying
score/rank progress over time.

"""

from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer, Float, Boolean, DateTime

from . import Base, Participation, Task, Submission


class ParticipationTaskScore(Base):
    """Cached score for a participation on a task.

    This table caches the computed task score for each participation/task
    pair to avoid recomputing scores on every ranking page load.

    """
    __tablename__ = 'participation_task_scores'
    __table_args__ = (
        UniqueConstraint('participation_id', 'task_id'),
    )

    id: int = Column(
        Integer,
        primary_key=True)

    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    participation: Participation = relationship(
        Participation,
        back_populates="task_scores")

    task_id: int = Column(
        Integer,
        ForeignKey(Task.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    task: Task = relationship(
        Task,
        back_populates="participation_scores")

    score: float = Column(
        Float,
        nullable=False,
        default=0.0)

    subtask_max_scores: dict | None = Column(
        JSONB,
        nullable=True)

    max_tokened_score: float = Column(
        Float,
        nullable=False,
        default=0.0)

    last_submission_score: float | None = Column(
        Float,
        nullable=True)

    last_submission_timestamp: datetime | None = Column(
        DateTime,
        nullable=True)

    history_valid: bool = Column(
        Boolean,
        nullable=False,
        default=True)

    score_valid: bool = Column(
        Boolean,
        nullable=False,
        default=True)

    has_submissions: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    last_update: datetime = Column(
        DateTime,
        nullable=False)


class ScoreHistory(Base):
    """History of score changes for a participation on a task.

    This table stores the history of score changes to enable displaying
    score/rank progress over time in the user detail view.

    """
    __tablename__ = 'score_history'

    id: int = Column(
        Integer,
        primary_key=True)

    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    participation: Participation = relationship(
        Participation,
        back_populates="score_history")

    task_id: int = Column(
        Integer,
        ForeignKey(Task.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    task: Task = relationship(Task)

    timestamp: datetime = Column(
        DateTime,
        nullable=False,
        index=True)

    score: float = Column(
        Float,
        nullable=False)

    submission_id: int = Column(
        Integer,
        ForeignKey(Submission.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    submission: Submission = relationship(Submission)
