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

"""Training programs for organizing year-long training with multiple sessions.

A TrainingProgram has a name, description, and a managing contest that handles
all submissions (both from training sessions and from the task archive).
"""

import typing

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey
from sqlalchemy.types import Integer, Unicode

from . import Base, Codename

if typing.TYPE_CHECKING:
    from . import Contest


class TrainingProgram(Base):
    """A training program that manages multiple training sessions.

    The training program uses a "managing contest" to handle all submissions
    and evaluations. Users are added to the training program by adding them
    to the managing contest's participations.
    """
    __tablename__ = "training_programs"

    # Auto increment primary key.
    id: int = Column(Integer, primary_key=True)

    # Short name (codename) of the training program, unique across all programs.
    name: str = Column(Codename, nullable=False, unique=True)

    # Human-readable description for UI.
    description: str = Column(Unicode, nullable=False)

    # The managing contest that handles all submissions for this program.
    # Each training program has exactly one managing contest, and each
    # contest can manage at most one training program.
    managing_contest_id: int = Column(
        Integer,
        ForeignKey("contests.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    managing_contest: "Contest" = relationship(
        "Contest",
        back_populates="training_program",
    )
