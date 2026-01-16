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

"""Archived student ranking model for training days.

ArchivedStudentRanking stores ranking data for students after a training day
is archived. This includes the student's tags, task scores, and score history
for rendering ranking graphs.
"""

import typing

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, Index, UniqueConstraint
from sqlalchemy.types import Integer, Unicode

from . import Base

if typing.TYPE_CHECKING:
    from . import TrainingDay, Student


class ArchivedStudentRanking(Base):
    """Archived ranking data for a student in a training day.

    This stores immutable ranking information after a training day is
    archived, including the student's tags during the training day,
    their final scores for each task, and their score history in the
    format expected by the JavaScript ranking graph renderer.
    """
    __tablename__ = "archived_student_rankings"
    __table_args__ = (
        UniqueConstraint("training_day_id", "student_id",
                         name="archived_student_rankings_training_day_id_student_id_key"),
        Index("ix_archived_student_rankings_student_tags_gin", "student_tags",
              postgresql_using="gin"),
    )

    id: int = Column(Integer, primary_key=True)

    training_day_id: int = Column(
        Integer,
        ForeignKey("training_days.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    student_id: int = Column(
        Integer,
        ForeignKey("students.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # All tags the student had during this training day (stored as array for efficient filtering)
    student_tags: list[str] = Column(
        ARRAY(Unicode),
        nullable=False,
        default=list,
    )

    # Final scores for each task: {task_id: score}
    # Includes all visible tasks (even with 0 score), not just non-zero scores.
    # The presence of a task_id key indicates the task was visible to this student.
    task_scores: dict | None = Column(
        JSONB,
        nullable=True,
    )

    # Submissions for each task: {task_id: [{task, time, score, token, extra}, ...]}
    # Format matches RWS submission format for rendering in UserDetail.js
    submissions: dict | None = Column(
        JSONB,
        nullable=True,
    )

    # Score history in RWS format: [[user_id, task_id, time, score], ...]
    # This is the format expected by HistoryStore.js for rendering graphs
    history: list | None = Column(
        JSONB,
        nullable=True,
    )

    training_day: "TrainingDay" = relationship(
        "TrainingDay",
        back_populates="archived_student_rankings",
    )

    student: "Student" = relationship(
        "Student",
    )
