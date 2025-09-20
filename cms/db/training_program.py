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

"""Training program-related database interfaces for SQLAlchemy."""

import typing

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column
from sqlalchemy.types import Integer, Unicode

from . import Base, Codename

if typing.TYPE_CHECKING:
    from .contest import Contest


class TrainingProgram(Base):
    """Group contests that form a training program."""

    __tablename__ = "training_programs"

    id: int = Column(
        Integer,
        primary_key=True,
    )

    name: str = Column(
        Codename,
        nullable=False,
        unique=True,
    )

    title: str = Column(
        Unicode,
        nullable=False,
    )

    contests: list["Contest"] = relationship(
        "Contest",
        back_populates="training_program",
        passive_deletes=True,
    )

    def _get_contest_by_role(self, role: str) -> "Contest | None":
        for contest in self.contests:
            if contest.training_program_role == role:
                return contest
        return None

    @property
    def regular_contest(self) -> "Contest | None":
        """Contest assigned as the regular event."""

        return self._get_contest_by_role("regular")

    @regular_contest.setter
    def regular_contest(self, contest):
        existing = self._get_contest_by_role("regular")
        if existing is contest:
            return
        if existing is not None:
            existing.training_program = None
            existing.training_program_role = None
        if contest is not None:
            if contest.training_program is not None and contest.training_program is not self:
                raise ValueError("Contest already belongs to another training program.")
            contest.training_program = self
            contest.training_program_role = "regular"

    @property
    def home_contest(self) -> "Contest | None":
        """Contest assigned as the home event."""

        return self._get_contest_by_role("home")

    @home_contest.setter
    def home_contest(self, contest):
        existing = self._get_contest_by_role("home")
        if existing is contest:
            return
        if existing is not None:
            existing.training_program = None
            existing.training_program_role = None
        if contest is not None:
            if contest.training_program is not None and contest.training_program is not self:
                raise ValueError("Contest already belongs to another training program.")
            contest.training_program = self
            contest.training_program_role = "home"
