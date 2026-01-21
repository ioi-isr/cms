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

"""Training day model for training programs.

A TrainingDay represents a single training session within a training program.
It wraps a Contest and includes its position within the training program.
"""

import typing

from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship, Session
from sqlalchemy.schema import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import DateTime, Integer, Interval, Unicode

from . import Base

if typing.TYPE_CHECKING:
    from datetime import datetime, timedelta
    from . import Contest, TrainingProgram, Task, TrainingDayGroup, Submission, Participation, User
    from . import ArchivedAttendance, ArchivedStudentRanking


def get_managing_participation(
    session: Session,
    training_day: "TrainingDay",
    user: "User",
) -> "Participation | None":
    """Get the managing contest participation for a user in a training day.

    Training day submissions are stored with the managing contest's participation,
    not the training day's participation. This helper finds the managing contest
    participation for a given user.

    session: the database session.
    training_day: the training day.
    user: the user to look up.

    return: the Participation in the managing contest, or None if not found.
    """
    from . import Participation
    managing_contest = training_day.training_program.managing_contest
    return (
        session.query(Participation)
        .filter(Participation.contest_id == managing_contest.id)
        .filter(Participation.user_id == user.id)
        .first()
    )


class TrainingDay(Base):
    """A training day in a training program.

    Each training day wraps a Contest and belongs to exactly one TrainingProgram.
    The position field determines the order of training days within the program.
    Training day types are tags for categorization (e.g., "online", "competition").
    """
    __tablename__ = "training_days"
    __table_args__ = (
        UniqueConstraint("training_program_id", "position",
                         name="training_days_training_program_id_position_key"),
        Index("ix_training_days_training_day_types_gin", "training_day_types",
              postgresql_using="gin"),
    )

    id: int = Column(Integer, primary_key=True)

    training_program_id: int = Column(
        Integer,
        ForeignKey("training_programs.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contest_id: int | None = Column(
        Integer,
        ForeignKey("contests.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )

    position: int | None = Column(
        Integer,
        nullable=True,
    )

    # Name and description are synced with contest while contest exists.
    # After archiving (when contest is deleted), these fields preserve the values.
    name: str | None = Column(
        Unicode,
        nullable=True,
    )

    description: str | None = Column(
        Unicode,
        nullable=True,
    )

    # Start time is synced with contest while contest exists.
    # After archiving (when contest is deleted), this field preserves the value.
    start_time: "datetime | None" = Column(
        DateTime,
        nullable=True,
    )

    # Task metadata at archive time: {task_id: {name, short_name, max_score, score_precision, extra_headers}}
    # Preserves the scoring scheme as it was during the training day.
    # Stored at training day level (not per-student) since it's the same for all students.
    archived_tasks_data: dict | None = Column(
        JSONB,
        nullable=True,
    )

    # Duration of the training day at archive time.
    # Calculated as the max training duration among main groups (if any),
    # or the training day duration (if no main groups).
    duration: "timedelta | None" = Column(
        Interval,
        nullable=True,
    )

    # Training day types for categorization (e.g., "online", "onsite", "competition").
    # Used for filtering in attendance and combined ranking views.
    training_day_types: list[str] = Column(
        ARRAY(Unicode),
        nullable=False,
        default=list,
    )

    training_program: "TrainingProgram" = relationship(
        "TrainingProgram",
        back_populates="training_days",
    )

    contest: "Contest | None" = relationship(
        "Contest",
        back_populates="training_day",
    )

    tasks: list["Task"] = relationship(
        "Task",
        back_populates="training_day",
        order_by="Task.training_day_num",
    )

    groups: list["TrainingDayGroup"] = relationship(
        "TrainingDayGroup",
        back_populates="training_day",
        cascade="all, delete-orphan",
    )

    submissions: list["Submission"] = relationship(
        "Submission",
        back_populates="training_day",
        passive_deletes=True,
    )

    archived_attendances: list["ArchivedAttendance"] = relationship(
        "ArchivedAttendance",
        back_populates="training_day",
        cascade="all, delete-orphan",
    )

    archived_student_rankings: list["ArchivedStudentRanking"] = relationship(
        "ArchivedStudentRanking",
        back_populates="training_day",
        cascade="all, delete-orphan",
    )
