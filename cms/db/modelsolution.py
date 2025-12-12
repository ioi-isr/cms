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

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, Session
from sqlalchemy.schema import Column, ForeignKey, UniqueConstraint
from sqlalchemy.types import Integer, Float, Unicode

from . import Base


class ModelSolutionMeta(Base):
    """Metadata for model solutions.
    
    Model solutions are implemented as regular Submissions owned by a
    special hidden system Participation. This table stores the additional
    metadata like description and expected score range.
    
    The 'name' field is a short identifier used for export/import and must
    be unique per dataset. It should be a simple ASCII slug like 'intended',
    'bruteforce', 'slow_solution', etc.
    """
    __tablename__ = 'model_solution_meta'
    __table_args__ = (
        UniqueConstraint('submission_id', 'dataset_id'),
        UniqueConstraint('dataset_id', 'name'),
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

    name: str = Column(
        Unicode,
        nullable=False,
        doc="Short identifier for export/import (e.g., 'intended', 'bruteforce'). "
            "Must be unique per dataset and valid as a filename.")

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

    subtask_expected_scores = Column(
        JSONB,
        nullable=True,
        default=None,
        doc="Expected score ranges per subtask. Format: "
            '{"0": {"min": 0, "max": 10}, "1": {"min": 0, "max": 20}, ...}')

    def is_score_in_range(self) -> bool | None:
        """Check if the submission's score is within the expected range.
        
        return: True if score is in range, False if not, None if not scored yet
        """
        result = self.submission.get_result(self.dataset)
        if result is None or result.score is None:
            return None
        return self.expected_score_min <= result.score <= self.expected_score_max

    def get_subtask_score_status(self, subtask_idx: int) -> str | None:
        """Check if a subtask's score is within the expected range.
        
        subtask_idx: the index of the subtask to check
        
        return: "in_range" if score is in expected range,
                "out_of_range" if score is outside expected range,
                "not_configured" if no expected range was set for this subtask,
                None if not scored yet
        """
        result = self.submission.get_result(self.dataset)
        if result is None or result.score_details is None:
            return None
        
        subtask_key = str(subtask_idx)
        if self.subtask_expected_scores is None or \
                subtask_key not in self.subtask_expected_scores:
            return "not_configured"
        
        expected = self.subtask_expected_scores[subtask_key]
        expected_min = expected.get("min", 0)
        expected_max = expected.get("max", 0)
        
        score_details = result.score_details
        if not isinstance(score_details, list):
            return None
        
        for st in score_details:
            if st.get("idx") == subtask_idx:
                actual_score = st.get("score")
                if actual_score is None:
                    return None
                if expected_min <= actual_score <= expected_max:
                    return "in_range"
                else:
                    return "out_of_range"
        
        return None

    def get_subtask_actual_score(self, subtask_idx: int) -> float | None:
        """Get the actual score for a subtask.
        
        subtask_idx: the index of the subtask
        
        return: the actual score, or None if not scored yet
        """
        result = self.submission.get_result(self.dataset)
        if result is None or result.score_details is None:
            return None
        
        score_details = result.score_details
        if not isinstance(score_details, list):
            return None
        
        for st in score_details:
            if st.get("idx") == subtask_idx:
                return st.get("score")
        
        return None

    def get_subtask_testcase_outcomes(self, subtask_idx: int) -> str:
        """Get testcase outcome symbols as HTML for a subtask (used for 0-point subtasks).
        
        For 0-point subtasks (like sample subtasks), we show individual testcase
        outcomes instead of score ranges. Each testcase is represented by a colored symbol:
        - green ✓ for correct (outcome == 1.0)
        - red ✗ for wrong (outcome == 0.0)
        - orange ◐ for partial (0.0 < outcome < 1.0)
        - gray ? for not evaluated yet
        
        subtask_idx: the index of the subtask
        
        return: HTML string with colored symbols (e.g., "<span style='color:green'>✓</span>...")
        """
        from cms.grading.scoretypes import ScoreTypeGroup
        
        result = self.submission.get_result(self.dataset)
        if result is None:
            return ""
        
        try:
            score_type_obj = self.dataset.score_type_object
            if not isinstance(score_type_obj, ScoreTypeGroup):
                return ""
            
            targets = score_type_obj.retrieve_target_testcases()
            if subtask_idx >= len(targets):
                return ""
            
            target_codenames = targets[subtask_idx]
        except Exception:
            return ""
        
        outcomes_by_codename = {}
        for ev in result.evaluations:
            outcomes_by_codename[ev.codename] = ev.outcome
        
        symbols = []
        for codename in target_codenames:
            outcome_str = outcomes_by_codename.get(codename)
            symbols.append(self._outcome_to_colored_symbol(outcome_str))
        
        return "".join(symbols)

    @staticmethod
    def _outcome_to_colored_symbol(outcome_str: str | None) -> str:
        """Convert an evaluation outcome string to a colored HTML symbol.
        
        outcome_str: the outcome as a string (e.g., "1.0", "0.0", "0.5")
        
        return: HTML span with colored symbol
        """
        if outcome_str is None:
            return "<span style='color:gray'>?</span>"
        try:
            outcome = float(outcome_str)
        except (ValueError, TypeError):
            return "<span style='color:gray'>?</span>"
        
        if outcome >= 1.0:
            return "<span style='color:green'>✓</span>"
        if outcome <= 0.0:
            return "<span style='color:red'>✗</span>"
        return "<span style='color:orange'>◐</span>"


def create_model_solution(
    session: Session,
    *,
    task,
    dataset,
    participation,
    digests: dict[str, str],
    language_name: str | None,
    name: str,
    description: str,
    expected_score_min: float = 0.0,
    expected_score_max: float = 100.0,
    subtask_expected_scores: dict | None = None,
):
    """Create a model solution submission with metadata.
    
    This is the shared logic for creating model solutions, used by both
    the admin handler and the task importer.
    
    session: database session
    task: Task object
    dataset: Dataset object
    participation: the model solution participation (from get_or_create_model_solution_participation)
    digests: dict mapping filename to file digest
    language_name: the language name (e.g., "C++17 / g++"), or None
    name: short identifier for the model solution
    description: human-readable description
    expected_score_min: minimum expected score
    expected_score_max: maximum expected score
    subtask_expected_scores: optional dict of subtask score ranges
    
    return: tuple of (submission, meta)
    """
    from cmscommon.datetime import make_datetime
    from .submission import Submission, File
    
    timestamp = make_datetime()
    opaque_id = Submission.generate_opaque_id(session, participation.id)
    
    submission = Submission(
        opaque_id=opaque_id,
        timestamp=timestamp,
        language=language_name,
        participation=participation,
        task=task,
        official=False,
    )
    session.add(submission)
    session.flush()
    
    for codename, digest in digests.items():
        session.add(File(
            filename=codename,
            digest=digest,
            submission=submission,
        ))
    
    meta = ModelSolutionMeta(
        submission=submission,
        dataset=dataset,
        name=name,
        description=description,
        expected_score_min=expected_score_min,
        expected_score_max=expected_score_max,
        subtask_expected_scores=subtask_expected_scores,
    )
    session.add(meta)
    
    return submission, meta


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
