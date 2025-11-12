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

"""Model solution metadata and helper functions.

Model solutions are reference solutions uploaded by admins to verify that
task test data and scoring work correctly. They are implemented as regular
Submissions owned by a special hidden system Participation, with additional
metadata stored in ModelSolutionMeta.

"""

from sqlalchemy.orm import relationship, Session
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer, Float, Unicode

from . import Base


class ModelSolutionMeta(Base):
    """Metadata for model solutions.
    
    Model solutions are implemented as regular Submissions owned by a
    special hidden system Participation. This table stores the additional
    metadata like description and expected score range.
    """
    __tablename__ = 'model_solution_meta'
    __table_args__ = (
        UniqueConstraint('submission_id', 'dataset_id'),
    )

    id: int = Column(
        Integer,
        primary_key=True)

    submission_id: int = Column(
        Integer,
        ForeignKey('submissions.id',
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    submission = relationship(
        'Submission',
        foreign_keys=[submission_id],
        backref='model_solution_meta')

    dataset_id: int = Column(
        Integer,
        ForeignKey('datasets.id',
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    dataset = relationship(
        'Dataset',
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

    def is_score_in_range(self) -> bool | None:
        """Check if the submission's score is within the expected range.
        
        return: True if score is in range, False if not, None if not scored yet
        """
        result = self.submission.get_result(self.dataset)
        if result is None or result.score is None:
            return None
        return self.expected_score_min <= result.score <= self.expected_score_max


def get_or_create_model_solution_participation(session: Session):
    """Get or create the special system participation for model solutions.
    
    This creates a single global system contest and participation that is used
    for ALL model solutions regardless of which task they belong to. This ensures
    model solutions persist even if contests are deleted or tasks are moved.
    
    session: database session
    
    return: the system model solution participation
    """
    from . import Contest, User, Participation
    
    system_contest_name = "__model_solutions_system__"
    system_user_username = "__model_solutions__"
    
    contest = session.query(Contest).filter(
        Contest.name == system_contest_name
    ).first()
    
    if contest is None:
        from cmscommon.datetime import make_datetime
        contest = Contest(
            name=system_contest_name,
            description="System contest for model solutions",
            start=make_datetime(0),  # Epoch time
            stop=make_datetime(0),
            timezone="UTC",
            per_user_time=None,
            max_submission_number=None,
            max_user_test_number=None,
            min_submission_interval=None,
            min_user_test_interval=None,
            token_mode="disabled",
            score_precision=2
        )
        session.add(contest)
        session.flush()
    
    user = session.query(User).filter(
        User.username == system_user_username
    ).first()
    
    if user is None:
        user = User(
            username=system_user_username,
            first_name="Model",
            last_name="Solutions",
            password="!"  # Invalid password hash - cannot login
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
            hidden=True,  # Hidden from scoreboards
            unrestricted=True  # No time/submission limits
        )
        session.add(participation)
        session.flush()
    
    return participation
