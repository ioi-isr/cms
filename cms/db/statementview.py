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

"""Statement view tracking database interface for SQLAlchemy.

"""

from datetime import datetime
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer, DateTime

from cmscommon.datetime import make_datetime
from . import Base, Participation, Task


class StatementView(Base):
    """Class to store when a participant first viewed a task statement.

    """
    __tablename__ = 'statement_views'
    __table_args__ = (
        UniqueConstraint('participation_id', 'task_id',
                         name='participation_task_unique'),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Participation (id and object) that viewed the statement.
    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    participation: Participation = relationship(
        Participation,
        back_populates="statement_views")

    task_id: int = Column(
        Integer,
        ForeignKey(Task.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    task: Task = relationship(
        Task,
        back_populates="statement_views")

    timestamp: datetime = Column(
        DateTime,
        nullable=False,
        default=make_datetime)
