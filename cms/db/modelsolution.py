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

"""

from datetime import datetime
from sqlalchemy import Boolean
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.schema import Column, ForeignKey, ForeignKeyConstraint, \
    UniqueConstraint
from sqlalchemy.types import Integer, Float, String, Unicode, DateTime, \
    BigInteger

from . import Filename, FilenameSchema, Digest, Base, Dataset, Testcase


class ModelSolution(Base):
    """Class to store a model solution for a task dataset.

    Model solutions are reference solutions uploaded by admins to verify
    that the task's test data and scoring work correctly. Each model solution
    has an expected score range, and the system tracks whether the actual
    score falls within that range.
    """
    __tablename__ = 'model_solutions'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    dataset_id: int = Column(
        Integer,
        ForeignKey(Dataset.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    dataset: Dataset = relationship(
        Dataset,
        back_populates="model_solutions")

    description: str = Column(
        Unicode,
        nullable=False,
        default="")

    timestamp: datetime = Column(
        DateTime,
        nullable=False)

    language: str | None = Column(
        String,
        nullable=True)

    expected_score_min: float = Column(
        Float,
        nullable=False,
        default=0.0)
    expected_score_max: float = Column(
        Float,
        nullable=False,
        default=100.0)

    score_in_range: bool | None = Column(
        Boolean,
        nullable=True,
        default=None)

    # These one-to-many relationships are the reversed directions of
    # the ones defined in the "child" classes using foreign keys.

    files: dict[str, "ModelSolutionFile"] = relationship(
        "ModelSolutionFile",
        collection_class=attribute_mapped_collection("filename"),
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="model_solution")

    results: list["ModelSolutionResult"] = relationship(
        "ModelSolutionResult",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="model_solution")

    def get_result(self, dataset: Dataset | None = None) -> "ModelSolutionResult | None":
        """Return the result associated to a dataset.

        dataset: the dataset for which the caller wants
            the model solution result; if None, the dataset of this
            model solution is used.

        return: the model solution result
            associated to this model solution and the given dataset, if it
            exists in the database, otherwise None.

        """
        if dataset is not None:
            dataset_id = dataset.id
        else:
            dataset_id = self.dataset_id

        return ModelSolutionResult.get_from_id(
            (self.id, dataset_id), self.sa_session)

    def get_result_or_create(self, dataset: Dataset | None = None) -> "ModelSolutionResult":
        """Return and, if necessary, create the result for a dataset.

        dataset: the dataset for which the caller wants
            the model solution result; if None, the dataset of this
            model solution is used.

        return: the model solution result associated to
            the this model solution and the given dataset; if it
            does not exists, a new one is created.

        """
        if dataset is None:
            dataset = self.dataset

        model_solution_result = self.get_result(dataset)

        if model_solution_result is None:
            model_solution_result = ModelSolutionResult(
                model_solution=self,
                dataset=dataset)

        return model_solution_result


class ModelSolutionFile(Base):
    """Class to store information about one file submitted within a
    model solution.

    """
    __tablename__ = 'model_solution_files'
    __table_args__ = (
        UniqueConstraint('model_solution_id', 'filename'),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    model_solution_id: int = Column(
        Integer,
        ForeignKey(ModelSolution.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    model_solution: ModelSolution = relationship(
        ModelSolution,
        back_populates="files")

    # Filename and digest of the submitted file.
    filename: str = Column(
        FilenameSchema,
        nullable=False)
    digest: str = Column(
        Digest,
        nullable=False)


class ModelSolutionResult(Base):
    """Class to store the evaluation results of a model solution.

    This is similar to SubmissionResult but for model solutions.
    """
    COMPILING = 1
    COMPILATION_FAILED = 2
    EVALUATING = 3
    SCORING = 4
    SCORED = 5

    __tablename__ = 'model_solution_results'
    __table_args__ = (
        UniqueConstraint('model_solution_id', 'dataset_id'),
    )

    model_solution_id: int = Column(
        Integer,
        ForeignKey(ModelSolution.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True)
    model_solution: ModelSolution = relationship(
        ModelSolution,
        back_populates="results")

    dataset_id: int = Column(
        Integer,
        ForeignKey(Dataset.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True)
    dataset: Dataset = relationship(
        Dataset)

    # Now below follow the actual result fields (similar to SubmissionResult).

    # Compilation outcome (can be None = yet to compile, "ok" =
    # compilation successful and we can evaluate, "fail" =
    # compilation unsuccessful, throw it away).
    compilation_outcome: str | None = Column(
        String,
        nullable=True)

    # The output from the sandbox (to allow localization the first item
    # of the list is a format string, possibly containing some "%s",
    # that will be filled in using the remaining items of the list).
    compilation_text: list[str] = Column(
        ARRAY(String),
        nullable=False,
        default=[])

    # Number of failures during compilation.
    compilation_tries: int = Column(
        Integer,
        nullable=False,
        default=0)

    # The compiler stdout and stderr.
    compilation_stdout: str | None = Column(
        Unicode,
        nullable=True)
    compilation_stderr: str | None = Column(
        Unicode,
        nullable=True)

    # Other information about the compilation.
    compilation_time: float | None = Column(
        Float,
        nullable=True)
    compilation_wall_clock_time: float | None = Column(
        Float,
        nullable=True)
    compilation_memory: int | None = Column(
        BigInteger,
        nullable=True)

    # Worker shard and sandbox where the compilation was performed.
    compilation_shard: int | None = Column(
        Integer,
        nullable=True)
    compilation_sandbox_paths: list[str] | None = Column(
        ARRAY(Unicode),
        nullable=True)
    compilation_sandbox_digests: list[str] | None = Column(
        ARRAY(String),
        nullable=True)

    # Evaluation outcome (can be None = yet to evaluate, "ok" =
    evaluation_outcome: str | None = Column(
        String,
        nullable=True)

    # Number of failures during evaluation.
    evaluation_tries: int = Column(
        Integer,
        nullable=False,
        default=0)

    # Score as computed by ScoringService. Null means not yet scored.
    score: float | None = Column(
        Float,
        nullable=True)

    # Score details. It's a JSON-like structure containing information
    # that is given to ScoreType.get_html_details to generate an HTML
    score_details: object | None = Column(
        JSONB,
        nullable=True)

    scored_at: datetime | None = Column(
        DateTime,
        nullable=True)

    # The same as the last two fields, but only showing information
    # visible to the user (assuming they did not use a token on this
    # submission).
    public_score: float | None = Column(
        Float,
        nullable=True)
    public_score_details: object | None = Column(
        JSONB,
        nullable=True)

    # Ranking score details. It is a list of strings that are going to
    # be shown in a single row in the table of submission in RWS.
    ranking_score_details: list[str] | None = Column(
        ARRAY(String),
        nullable=True)

    # These one-to-many relationships are the reversed directions of
    # the ones defined in the "child" classes using foreign keys.

    executables: dict[str, "ModelSolutionExecutable"] = relationship(
        "ModelSolutionExecutable",
        collection_class=attribute_mapped_collection("filename"),
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="model_solution_result")

    evaluations: list["ModelSolutionEvaluation"] = relationship(
        "ModelSolutionEvaluation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="model_solution_result")

    def get_status(self) -> int:
        """Return the status of this object."""
        if not self.compiled():
            return ModelSolutionResult.COMPILING
        elif self.compilation_failed():
            return ModelSolutionResult.COMPILATION_FAILED
        elif not self.evaluated():
            return ModelSolutionResult.EVALUATING
        elif not self.scored():
            return ModelSolutionResult.SCORING
        else:
            return ModelSolutionResult.SCORED

    def get_evaluation(self, testcase: Testcase) -> "ModelSolutionEvaluation | None":
        """Return the Evaluation of this MSR on the given Testcase, if any.

        testcase: the testcase the returned evaluation will belong to.

        return: the (only!) evaluation of this model solution result on the
            given testcase, or None if there isn't any.

        """
        # Use IDs to avoid triggering a lazy-load query.
        assert self.dataset_id == testcase.dataset_id

        return self.sa_session.query(ModelSolutionEvaluation)\
            .filter(ModelSolutionEvaluation.model_solution_result == self)\
            .filter(ModelSolutionEvaluation.testcase == testcase)\
            .first()

    def get_max_evaluation_resources(self) -> tuple[float | None, int | None]:
        """Return the maximum time and memory used by this result.

        return: max used time in seconds and memory in bytes,
            or None if data is incomplete or unavailable.

        """
        t, m = None, None
        if self.evaluated() and self.evaluations:
            for ev in self.evaluations:
                if ev.execution_time is not None \
                        and (t is None or t < ev.execution_time):
                    t = ev.execution_time
                if ev.execution_memory is not None \
                        and (m is None or m < ev.execution_memory):
                    m = ev.execution_memory
        return (t, m)

    def compiled(self) -> bool:
        """Return whether the model solution result has been compiled."""
        return self.compilation_outcome is not None

    @staticmethod
    def filter_compiled():
        """Return a filtering expression for compiled model solution results."""
        return ModelSolutionResult.compilation_outcome.isnot(None)

    def compilation_failed(self) -> bool:
        """Return whether the model solution result did not compile."""
        return self.compilation_outcome == "fail"

    @staticmethod
    def filter_compilation_failed():
        """Return a filtering expression for model solution results failing
        compilation.
        """
        return ModelSolutionResult.compilation_outcome == "fail"

    def compilation_succeeded(self) -> bool:
        """Return whether the model solution compiled."""
        return self.compilation_outcome == "ok"

    @staticmethod
    def filter_compilation_succeeded():
        """Return a filtering expression for model solution results passing
        compilation.
        """
        return ModelSolutionResult.compilation_outcome == "ok"

    def evaluated(self) -> bool:
        """Return whether the model solution result has been evaluated."""
        return self.evaluation_outcome is not None

    @staticmethod
    def filter_evaluated():
        """Return a filtering lambda for evaluated model solution results."""
        return ModelSolutionResult.evaluation_outcome.isnot(None)

    def needs_scoring(self) -> bool:
        """Return whether the model solution result needs to be scored."""
        return (self.compilation_failed() or self.evaluated()) and \
            not self.scored()

    def scored(self) -> bool:
        """Return whether the model solution result has been scored."""
        return all(getattr(self, k) is not None for k in [
            "score", "score_details",
            "public_score", "public_score_details",
            "ranking_score_details"])

    @staticmethod
    def filter_scored():
        """Return a filtering lambda for scored model solution results."""
        return ((ModelSolutionResult.score.isnot(None))
                & (ModelSolutionResult.score_details.isnot(None))
                & (ModelSolutionResult.public_score.isnot(None))
                & (ModelSolutionResult.public_score_details.isnot(None))
                & (ModelSolutionResult.ranking_score_details.isnot(None)))

    def invalidate_compilation(self):
        """Blank all compilation and evaluation outcomes, and the score."""
        self.compilation_outcome = None
        self.compilation_text = []
        self.compilation_tries = 0
        self.compilation_stdout = None
        self.compilation_stderr = None
        self.compilation_time = None
        self.compilation_wall_clock_time = None
        self.compilation_memory = None
        self.compilation_shard = None
        self.compilation_sandbox_paths = None
        self.compilation_sandbox_digests = None
        self.invalidate_evaluation()

    def invalidate_evaluation(self):
        """Blank the evaluation outcome and the score."""
        self.evaluation_outcome = None
        self.evaluation_tries = 0
        self.invalidate_score()

    def invalidate_score(self):
        """Blank the score."""
        self.score = None
        self.score_details = None
        self.public_score = None
        self.public_score_details = None
        self.ranking_score_details = None
        self.scored_at = None

    def set_compilation_outcome(self, success: bool):
        """Set the compilation outcome based on success."""
        self.compilation_outcome = "ok" if success else "fail"

    def set_evaluation_outcome(self):
        """Set the evaluation outcome to ok."""
        self.evaluation_outcome = "ok"


class ModelSolutionExecutable(Base):
    """Class to store information about one file generated by the
    compilation of a model solution.

    """
    __tablename__ = 'model_solution_executables'
    __table_args__ = (
        ForeignKeyConstraint(
            ('model_solution_id', 'dataset_id'),
            (ModelSolutionResult.model_solution_id,
             ModelSolutionResult.dataset_id),
            onupdate="CASCADE", ondelete="CASCADE"),
        UniqueConstraint('model_solution_id', 'dataset_id', 'filename'),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    model_solution_id: int = Column(
        Integer,
        ForeignKey(ModelSolution.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    model_solution: ModelSolution = relationship(
        ModelSolution,
        viewonly=True)

    # Dataset (id and object) owning the executable.
    dataset_id: int = Column(
        Integer,
        ForeignKey(Dataset.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    dataset: Dataset = relationship(
        Dataset,
        viewonly=True)

    model_solution_result: ModelSolutionResult = relationship(
        ModelSolutionResult,
        back_populates="executables")

    # Filename and digest of the generated executable.
    filename: str = Column(
        Filename,
        nullable=False)
    digest: str = Column(
        Digest,
        nullable=False)


class ModelSolutionEvaluation(Base):
    """Class to store information about the outcome of the evaluation
    of a model solution against one testcase.

    """
    __tablename__ = 'model_solution_evaluations'
    __table_args__ = (
        ForeignKeyConstraint(
            ('model_solution_id', 'dataset_id'),
            (ModelSolutionResult.model_solution_id,
             ModelSolutionResult.dataset_id),
            onupdate="CASCADE", ondelete="CASCADE"),
        UniqueConstraint('model_solution_id', 'dataset_id', 'testcase_id'),
    )

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    model_solution_id: int = Column(
        Integer,
        ForeignKey(ModelSolution.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    model_solution: ModelSolution = relationship(
        ModelSolution,
        viewonly=True)

    # Dataset (id and object) owning the evaluation.
    dataset_id: int = Column(
        Integer,
        ForeignKey(Dataset.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    dataset: Dataset = relationship(
        Dataset,
        viewonly=True)

    model_solution_result: ModelSolutionResult = relationship(
        ModelSolutionResult,
        back_populates="evaluations")

    # Testcase (id and object) this evaluation was performed on.
    testcase_id: int = Column(
        Integer,
        ForeignKey(Testcase.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    testcase: Testcase = relationship(
        Testcase)

    # String containing the outcome of the evaluation (usually 1.0,
    # ...) not necessary the points awarded, that will be computed by
    # the score type.
    outcome: str | None = Column(
        Unicode,
        nullable=True)

    # The output from the grader, usually "Correct", "Time limit", ...
    # (to allow localization the first item of the list is a format
    # string, possibly containing some "%s", that will be filled in
    # using the remaining items of the list).
    text: list[str] = Column(
        ARRAY(String),
        nullable=False,
        default=[])

    # Evaluation's time and wall-clock time, in seconds.
    execution_time: float | None = Column(
        Float,
        nullable=True)
    execution_wall_clock_time: float | None = Column(
        Float,
        nullable=True)

    # Memory used by the evaluation, in bytes.
    execution_memory: int | None = Column(
        BigInteger,
        nullable=True)

    # Worker shard and sandbox where the evaluation was performed.
    evaluation_shard: int | None = Column(
        Integer,
        nullable=True)
    evaluation_sandbox_paths: list[str] | None = Column(
        ARRAY(Unicode),
        nullable=True)
    evaluation_sandbox_digests: list[str] | None = Column(
        ARRAY(String),
        nullable=True)

    @property
    def codename(self) -> str:
        """Return the codename of the testcase."""
        return self.testcase.codename
