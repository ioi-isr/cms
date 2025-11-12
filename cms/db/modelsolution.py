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

"""Model solution-related database interface for SQLAlchemy.

Model solutions are reference solutions uploaded by admins to verify
that the task's test data and scoring work correctly. They are implemented
as regular Submissions with a special hidden Participation, with this
metadata table storing additional information like expected score range.

"""

from sqlalchemy.orm import relationship, Session
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer, Float, Unicode

from . import Base, Submission, Dataset, User, Participation, Contest


class ModelSolutionMeta(Base):
    """Metadata for model solutions.
    
    Model solutions are implemented as regular Submissions owned by a
    special hidden Participation. This table stores the additional metadata
    like description and expected score range.
    """
    __tablename__ = 'model_solution_meta'
    __table_args__ = (
        UniqueConstraint('submission_id', 'dataset_id'),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    submission_id: int = Column(
        Integer,
        ForeignKey(Submission.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    submission: Submission = relationship(
        Submission,
        foreign_keys=[submission_id])

    dataset_id: int = Column(
        Integer,
        ForeignKey(Dataset.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    dataset: Dataset = relationship(
        Dataset,
        back_populates="model_solution_metas")

    description: str = Column(
        Unicode,
        nullable=False,
        default="")

    expected_score_min: float = Column(
        Float,
        nullable=False,
        default=0.0)
    expected_score_max: float = Column(
        Float,
        nullable=False,
        default=100.0)

    def get_result(self):
        """Get the SubmissionResult for this model solution's dataset."""
        return self.submission.get_result(self.dataset)

    def is_score_in_range(self) -> bool | None:
        """Check if the score is within the expected range.
        
        Returns True if in range, False if out of range, None if not yet scored.
        """
        result = self.get_result()
        if result is None or result.score is None:
            return None
        return self.expected_score_min <= result.score <= self.expected_score_max


def get_or_create_model_solution_participation(session: Session, contest: Contest) -> Participation:
    """Get or create the special hidden participation for model solutions.
    
    This creates a special user and participation that is used for all model
    solutions in a contest. The participation is marked as hidden so it doesn't
    appear in rankings.
    
    session: database session
    contest: the contest to get/create the participation for
    
    return: the model solution participation
    """
    username = f"__model_solutions_{contest.id}__"
    
    user = session.query(User).filter(User.username == username).first()
    if user is None:
        user = User(
            username=username,
            first_name="Model",
            last_name="Solutions",
            password="!"
        )
        session.add(user)
        session.flush()
    
    participation = session.query(Participation).filter(
        Participation.user_id == user.id,
        Participation.contest_id == contest.id
    ).first()
    
    if participation is None:
        participation = Participation(
            user=user,
            contest=contest,
            hidden=True,
            unrestricted=True
        )
        session.add(participation)
        session.flush()
    
    return participation
