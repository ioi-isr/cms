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

"""StudentTask model for tracking which tasks each student has access to.

A StudentTask represents a task that has been assigned to a student,
either automatically when they start a training day, or manually by an admin.
This controls which tasks appear in the student's task archive and are
included in their score calculations.
"""

import typing
from datetime import datetime

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import DateTime, Integer

from . import Base

if typing.TYPE_CHECKING:
    from . import Student, Task, TrainingDay


class StudentTask(Base):
    """A task assigned to a student.

    Tracks which tasks each student has access to in the task archive.
    Tasks can be assigned automatically when a student starts a training day
    (source_training_day_id is set), or manually by an admin
    (source_training_day_id is NULL).
    """
    __tablename__ = "student_tasks"
    __table_args__ = (
        UniqueConstraint("student_id", "task_id",
                         name="student_tasks_student_id_task_id_key"),
    )

    id: int = Column(Integer, primary_key=True)

    student_id: int = Column(
        Integer,
        ForeignKey("students.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id: int = Column(
        Integer,
        ForeignKey("tasks.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # If set, the task was assigned when the student started this training day.
    # If NULL, the task was manually assigned by an admin.
    source_training_day_id: int | None = Column(
        Integer,
        ForeignKey("training_days.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # When the task was assigned to the student.
    assigned_at: datetime = Column(
        DateTime,
        nullable=False,
    )

    student: "Student" = relationship(
        "Student",
        back_populates="student_tasks",
    )

    task: "Task" = relationship(
        "Task",
        back_populates="student_tasks",
    )

    source_training_day: "TrainingDay | None" = relationship(
        "TrainingDay",
    )
