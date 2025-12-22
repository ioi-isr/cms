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

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer

from . import Base

if typing.TYPE_CHECKING:
    from . import Contest, TrainingProgram, Task


class TrainingDay(Base):
    """A training day in a training program.

    Each training day wraps a Contest and belongs to exactly one TrainingProgram.
    The position field determines the order of training days within the program.
    """
    __tablename__ = "training_days"
    __table_args__ = (
        UniqueConstraint("training_program_id", "position",
                         name="training_days_training_program_id_position_key"),
    )

    id: int = Column(Integer, primary_key=True)

    training_program_id: int = Column(
        Integer,
        ForeignKey("training_programs.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    contest_id: int = Column(
        Integer,
        ForeignKey("contests.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    position: int | None = Column(
        Integer,
        nullable=True,
    )

    training_program: "TrainingProgram" = relationship(
        "TrainingProgram",
        back_populates="training_days",
    )

    contest: "Contest" = relationship(
        "Contest",
        back_populates="training_day",
    )

    tasks: list["Task"] = relationship(
        "Task",
        back_populates="training_day",
        order_by="Task.num",
    )
