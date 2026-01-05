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

"""Training day group model for per-group configuration.

A TrainingDayGroup represents a main group (student tag) configuration
for a training day, with optional custom start/end times and task ordering.
"""

import typing
from datetime import datetime

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Boolean, DateTime, Integer, Unicode

from . import Base

if typing.TYPE_CHECKING:
    from . import TrainingDay


class TrainingDayGroup(Base):
    """A main group configuration for a training day.

    Each group is identified by a tag name and can have custom timing
    and task ordering settings.
    """
    __tablename__ = "training_day_groups"
    __table_args__ = (
        UniqueConstraint("training_day_id", "tag_name",
                         name="training_day_groups_training_day_id_tag_name_key"),
    )

    id: int = Column(Integer, primary_key=True)

    training_day_id: int = Column(
        Integer,
        ForeignKey("training_days.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tag_name: str = Column(
        Unicode,
        nullable=False,
    )

    start_time: datetime | None = Column(
        DateTime,
        nullable=True,
    )

    end_time: datetime | None = Column(
        DateTime,
        nullable=True,
    )

    alphabetical_task_order: bool = Column(
        Boolean,
        nullable=False,
        default=False,
    )

    training_day: "TrainingDay" = relationship(
        "TrainingDay",
        back_populates="groups",
    )
