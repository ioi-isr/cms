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

"""Archived attendance model for training days.

ArchivedAttendance stores attendance data for students after a training day
is archived. This includes participation status, location (class/home),
delay time, and delay reasons.
"""

import typing
from datetime import timedelta

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Boolean, Integer, Unicode, Interval

from . import Base

if typing.TYPE_CHECKING:
    from . import TrainingDay, Student


class ArchivedAttendance(Base):
    """Archived attendance data for a student in a training day.

    This stores immutable attendance information after a training day is
    archived, including whether the student participated, their location
    (class or home), delay time, and delay reasons.
    """
    __tablename__ = "archived_attendances"
    __table_args__ = (
        UniqueConstraint("training_day_id", "student_id",
                         name="archived_attendances_training_day_id_student_id_key"),
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

    # "participated" if starting_time exists, "missed" otherwise
    status: str = Column(
        Unicode,
        nullable=False,
    )

    # "class", "home", or "both"
    location: str | None = Column(
        Unicode,
        nullable=True,
    )

    # Delay time copied from participation
    delay_time: timedelta | None = Column(
        Interval,
        nullable=True,
    )

    # Concatenated reasons from all delay requests
    delay_reasons: str | None = Column(
        Unicode,
        nullable=True,
    )

    # Whether the absence was justified (e.g., sick leave)
    justified: bool = Column(
        Boolean,
        nullable=False,
    )

    # Admin comment for this attendance record
    comment: str | None = Column(
        Unicode,
        nullable=True,
    )

    # Whether this students room and / or screen was recorded during this training
    recorded: bool = Column(
        Boolean,
        nullable=False,
    )

    training_day: "TrainingDay" = relationship(
        "TrainingDay",
        back_populates="archived_attendances",
    )

    student: "Student" = relationship(
        "Student",
    )
