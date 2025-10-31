#!/usr/bin/env python3

"""Contest folders for organizing contests hierarchically.

Each folder has a codename `name`, a human-readable `description`, and an
optional `parent` forming a tree. Contests can be assigned to a single folder.
"""

from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey
from sqlalchemy.types import Integer, Unicode

from . import Base, Codename


class ContestFolder(Base):
    __tablename__ = "contest_folders"

    # Auto increment primary key.
    id: int = Column(Integer, primary_key=True)

    # Short name (codename) of the folder, unique across all folders.
    name: str = Column(Codename, nullable=False, unique=True)

    # Human-readable description for UI.
    description: str = Column(Unicode, nullable=False)

    # Parent folder (nullable for root folders).
    parent_id: int | None = Column(
        Integer,
        ForeignKey("contest_folders.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    parent: "ContestFolder | None" = relationship(
        "ContestFolder", remote_side="ContestFolder.id", back_populates="children"
    )

    # Child folders (do not cascade delete or delete-orphan; we want to be
    # able to reparent children when a folder is removed)
    children: list["ContestFolder"] = relationship(
        "ContestFolder", back_populates="parent"
    )

    # Contests in this folder. Back-populated from Contest.folder
    contests: list["Contest"] = relationship(
        "Contest", back_populates="folder", cascade="save-update"
    )

    # Utility: compute full path by walking parents
    def full_path_parts(self) -> list[str]:
        cur = self
        parts: list[str] = []
        while cur is not None:
            parts.append(cur.name)
            cur = cur.parent
        parts.reverse()
        return parts
