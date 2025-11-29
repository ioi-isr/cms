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

"""Student model for training program participation.

A Student represents a user's participation in a training program.
It links a training program to a participation (in the managing contest)
and includes student tags for categorization.
"""

import typing

from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey
from sqlalchemy.types import Integer, Unicode

from . import Base

if typing.TYPE_CHECKING:
    from . import TrainingProgram, Participation


class Student(Base):
    """A student in a training program.

    Links a user's participation in the managing contest to the training
    program, and stores student tags for categorization (e.g., "beginner",
    "advanced").
    """
    __tablename__ = "students"

    id: int = Column(Integer, primary_key=True)

    training_program_id: int = Column(
        Integer,
        ForeignKey("training_programs.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    participation_id: int = Column(
        Integer,
        ForeignKey("participations.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    student_tags: list[str] = Column(
        ARRAY(Unicode),
        nullable=False,
        default=list,
    )

    training_program: "TrainingProgram" = relationship(
        "TrainingProgram",
        back_populates="students",
    )

    participation: "Participation" = relationship(
        "Participation",
        back_populates="student",
    )
