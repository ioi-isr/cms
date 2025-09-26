#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2015 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
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

"""User-related database interface for SQLAlchemy.

"""

from datetime import datetime, timedelta
from ipaddress import IPv4Network, IPv6Network

from sqlalchemy.dialects.postgresql import ARRAY, CIDR
from sqlalchemy.orm import relationship
from sqlalchemy.schema import Column, ForeignKey, CheckConstraint, \
    UniqueConstraint
from sqlalchemy.types import Boolean, Integer, String, Unicode, DateTime, Interval, Enum

from cmscommon.crypto import generate_random_password, build_password
from . import CastingArray, Codename, Base, Admin, Contest, TrainingProgram
import typing
if typing.TYPE_CHECKING:
    from . import PrintJob, Submission, UserTest
    from .training_program import TrainingProgram

class User(Base):
    """Class to store a user.

    """

    __tablename__ = 'users'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Real name (human readable) of the user.
    first_name: str = Column(
        Unicode,
        nullable=False)
    last_name: str = Column(
        Unicode,
        nullable=False)

    # Username and password to log in the CWS.
    username: str = Column(
        Codename,
        nullable=False,
        unique=True)
    password: str = Column(
        Unicode,
        nullable=False,
        default=lambda: build_password(generate_random_password()))

    # Email for any communications in case of remote contest.
    email: str | None = Column(
        Unicode,
        nullable=True)

    # Timezone for the user. All timestamps in CWS will be shown using
    # the timezone associated to the logged-in user or (if it's None
    # or an invalid string) the timezone associated to the contest or
    # (if it's None or an invalid string) the local timezone of the
    # server. This value has to be a string like "Europe/Rome",
    # "Australia/Sydney", "America/New_York", etc.
    timezone: str | None = Column(
        Unicode,
        nullable=True)

    # The language codes accepted by this user (from the "most
    # preferred" to the "least preferred"). If in a contest there is a
    # statement available in some of these languages, then the most
    # preferred of them will be highlighted.
    # FIXME: possibly move it to Participation and change it back to
    # primary_statements
    preferred_languages: list[str] = Column(
        ARRAY(String),
        nullable=False,
        default=[])

    # These one-to-many relationships are the reversed directions of
    # the ones defined in the "child" classes using foreign keys.

    participations: list["Participation"] = relationship(
        "Participation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="user")

    training_program_participations: list["TrainingProgramParticipation"] = (
        relationship(
            "TrainingProgramParticipation",
            cascade="all, delete-orphan",
            passive_deletes=True,
            back_populates="user",
        )
    )


class Team(Base):
    """Class to store a team.

    A team is a way of grouping the users participating in a contest.
    This grouping has no effect on the contest itself; it is only used
    for display purposes in RWS.

    """

    __tablename__ = 'teams'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Team code (e.g. the ISO 3166-1 code of a country)
    code: str = Column(
        Codename,
        nullable=False,
        unique=True)

    # Human readable team name (e.g. the ISO 3166-1 short name of a country)
    name: str = Column(
        Unicode,
        nullable=False)

    participations: list["Participation"] = relationship(
        "Participation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="team")

    # TODO: decide if the flag images will eventually be stored here.
    # TODO: (hopefully, the same will apply for faces in User).


class Participation(Base):
    """Class to store a single participation of a user in a contest.

    """
    __tablename__ = 'participations'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # If the IP lock is enabled the user can log into CWS only if their
    # requests come from an IP address that belongs to any of these
    # subnetworks. An empty list prevents the user from logging in,
    # None disables the IP lock for the user.
    ip: list[IPv4Network | IPv6Network] | None = Column(
        CastingArray(CIDR),
        nullable=True)

    # Starting time: for contests where every user has at most x hours
    # of the y > x hours totally available, this is the time the user
    # decided to start their time-frame.
    _starting_time: datetime | None = Column("starting_time", DateTime, nullable=True)

    # A shift in the time interval during which the user is allowed to
    # submit.
    _delay_time: timedelta = Column(
        "delay_time",
        Interval,
        CheckConstraint("delay_time >= '0 seconds'"),
        nullable=False,
        default=timedelta(),
    )

    # An extra amount of time allocated for this user.
    _extra_time: timedelta = Column(
        "extra_time",
        Interval,
        CheckConstraint("extra_time >= '0 seconds'"),
        nullable=False,
        default=timedelta(),
    )

    # Contest-specific password. If this password is not null then the
    # traditional user.password field will be "replaced" by this field's
    # value (only for this participation).
    password: str | None = Column(
        Unicode,
        nullable=True)

    # A hidden participation (e.g. does not appear in public rankings), can
    # also be used for debugging purposes.
    hidden: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # An unrestricted participation (e.g. contest time,
    # maximum number of submissions, minimum interval between submissions,
    # maximum number of user tests, minimum interval between user tests),
    # can also be used for debugging purposes.
    unrestricted: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # Contest (id and object) to which the user is participating.
    contest_id: int = Column(
        Integer,
        ForeignKey(Contest.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contest: Contest = relationship(Contest, back_populates="participations")

    training_program_participation_id: int | None = Column(
        Integer,
        ForeignKey(
            "training_program_participations.id",
            onupdate="CASCADE",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    training_program_role: str | None = Column(
        Enum("regular", "home", name="training_program_contest_role"), nullable=True
    )

    # User (id and object) which is participating.
    user_id: int = Column(
        Integer,
        ForeignKey(User.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    user: User = relationship(
        User,
        back_populates="participations")
    __table_args__ = (
        UniqueConstraint("contest_id", "user_id"),
        UniqueConstraint("training_program_participation_id", "training_program_role"),
        CheckConstraint(
            "(training_program_participation_id IS NULL) = (training_program_role IS NULL)",
            name="participations_program_fields_sync",
        ),
    )

    # Team (id and object) that the user is representing with this
    # participation.
    team_id: int | None = Column(
        Integer,
        ForeignKey(Team.id, onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
    )
    team: Team | None = relationship(
        Team,
        back_populates="participations")

    # These one-to-many relationships are the reversed directions of
    # the ones defined in the "child" classes using foreign keys.

    messages: list["Message"] = relationship(
        "Message",
        order_by="[Message.timestamp]",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="participation")

    questions: list["Question"] = relationship(
        "Question",
        order_by="[Question.question_timestamp, Question.reply_timestamp]",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="participation")

    submissions: list["Submission"] = relationship(
        "Submission",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="participation")

    user_tests: list["UserTest"] = relationship(
        "UserTest",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="participation")

    printjobs: list["PrintJob"] = relationship(
        "PrintJob",
        cascade="all, delete-orphan",
        passive_deletes=True,
        back_populates="participation")

    training_program_participation: "TrainingProgramParticipation | None" = (
        relationship(
            "TrainingProgramParticipation",
            back_populates="participations",
            foreign_keys=[training_program_participation_id],
        )
    )

    def is_training_program(self) -> bool:
        """Return whether this participation belongs to a training program."""

        return self.training_program_participation is not None

    @property
    def starting_time(self) -> datetime | None:
        if self.training_program_participation is not None:
            return self.training_program_participation.starting_time
        return self._starting_time

    @starting_time.setter
    def starting_time(self, value: datetime | None) -> None:
        if self.training_program_participation is not None:
            self.training_program_participation.starting_time = value
        else:
            self._starting_time = value

    @property
    def delay_time(self) -> timedelta:
        if self.training_program_participation is not None:
            return self.training_program_participation.delay_time
        return self._delay_time

    @delay_time.setter
    def delay_time(self, value: timedelta) -> None:
        if self.training_program_participation is not None:
            self.training_program_participation.delay_time = value
        else:
            self._delay_time = value

    @property
    def extra_time(self) -> timedelta:
        if self.training_program_participation is not None:
            return self.training_program_participation.extra_time
        return self._extra_time

    @extra_time.setter
    def extra_time(self, value: timedelta) -> None:
        if self.training_program_participation is not None:
            self.training_program_participation.extra_time = value
        else:
            self._extra_time = value

    def assert_valid(self) -> None:
        bound = self.is_training_program()
        if bound != (self.training_program_role is not None):
            raise ValueError(
                "Participation program link requires role and participation to be set together."
            )

        contest = self.contest

        if not bound:
            if contest is not None and contest.training_program is not None:
                raise ValueError(
                    "Contest participations for training-program contests must be linked to a training program participation."
                )
            return

        program_participation = self.training_program_participation
        assert program_participation is not None

        if program_participation.user is not self.user:
            raise ValueError(
                "Training program participation must reference the same user as contest participation."
            )

        program = program_participation.training_program
        if program is None:
            raise ValueError(
                "Training program participation missing training program reference."
            )

        if self.training_program_role == "regular":
            expected_contest = program.regular_contest
        elif self.training_program_role == "home":
            expected_contest = program.home_contest
        else:
            raise ValueError("Invalid training program contest role for participation.")

        if expected_contest is None:
            raise ValueError(
                "Training program participation assigned to a role without corresponding contest."
            )

        if contest is not expected_contest:
            raise ValueError(
                "Contest participation does not match its training program role."
            )


class TrainingProgramParticipation(Base):
    """Aggregate participation metadata for a user in a training program."""

    __tablename__ = "training_program_participations"

    id: int = Column(
        Integer,
        primary_key=True,
    )

    user_id: int = Column(
        Integer,
        ForeignKey(User.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    training_program_id: int = Column(
        Integer,
        ForeignKey(TrainingProgram.id, onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    starting_time: datetime | None = Column(
        DateTime,
        nullable=True,
    )

    delay_time: timedelta = Column(
        Interval,
        CheckConstraint("delay_time >= '0 seconds'"),
        nullable=False,
        default=timedelta(),
    )

    extra_time: timedelta = Column(
        Interval,
        CheckConstraint("extra_time >= '0 seconds'"),
        nullable=False,
        default=timedelta(),
    )

    __table_args__ = (UniqueConstraint("training_program_id", "user_id"),)

    user: User = relationship(
        User,
        back_populates="training_program_participations",
    )

    training_program: TrainingProgram = relationship(
        TrainingProgram,
        back_populates="training_program_participations",
    )

    participations: list[Participation] = relationship(
        Participation,
        back_populates="training_program_participation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def regular_participation(self) -> Participation | None:
        for participation in self.participations:
            if participation.training_program_role == "regular":
                return participation
        return None

    @property
    def home_participation(self) -> Participation | None:
        for participation in self.participations:
            if participation.training_program_role == "home":
                return participation
        return None

    @classmethod
    def ensure(
        cls,
        sql_session: "Session",
        training_program: TrainingProgram,
        user: User,
    ) -> "TrainingProgramParticipation":
        participation = (
            sql_session.query(cls)
            .filter(cls.training_program == training_program)
            .filter(cls.user == user)
            .one_or_none()
        )

        if participation is None:
            participation = cls(training_program=training_program, user=user)
            sql_session.add(participation)

        with participation.temporarily_invalid():
            participation._ensure_contest_participation(
                sql_session, training_program.regular_contest, user, "regular"
            )
            participation._ensure_contest_participation(
                sql_session, training_program.home_contest, user, "home"
            )

        sql_session.flush()

        return participation

    def _ensure_contest_participation(
        self,
        sql_session: "Session",
        contest: "Contest | None",
        user: User,
        role: str,
    ) -> None:
        existing = (
            self.regular_participation if role == "regular" else self.home_participation
        )
        if existing is not None and existing.contest != contest:
            existing.training_program_participation = None
            existing.training_program_role = None
            sql_session.delete(existing)

        if contest is None:
            return

        participation = (
            sql_session.query(Participation)
            .filter(Participation.contest == contest)
            .filter(Participation.user == user)
            .one_or_none()
        )

        if participation is None:
            participation = Participation(contest=contest, user=user)
            sql_session.add(participation)

        participation.training_program_participation = self
        participation.training_program_role = role

    def assert_valid(self) -> None:
        if self.training_program is None:
            raise ValueError(
                "Training program participation requires a training program."
            )
        if self.user is None:
            raise ValueError("Training program participation requires a user.")

        for participation in self.participations:
            if participation.user is not self.user:
                raise ValueError(
                    "Linked contest participation must belong to the same user."
                )
            if participation.training_program_participation is not self:
                raise ValueError(
                    "Contest participation does not reference this training program participation."
                )
            if participation.training_program_role not in {"regular", "home"}:
                raise ValueError(
                    "Contest participation linked to training program has invalid role."
                )

        if (
            sum(1 for p in self.participations if p.training_program_role == "regular")
            > 1
        ):
            raise ValueError(
                "Multiple contest participations assigned as regular for training program."
            )
        if sum(1 for p in self.participations if p.training_program_role == "home") > 1:
            raise ValueError(
                "Multiple contest participations assigned as home for training program."
            )

        regular_contest = self.training_program.regular_contest
        home_contest = self.training_program.home_contest

        regular = self.regular_participation
        home = self.home_participation

        if regular_contest is not None:
            if regular is None or regular.contest is not regular_contest:
                raise ValueError(
                    "Regular contest participation missing or mismatched for training program."
                )
        elif regular is not None:
            raise ValueError(
                "Regular participation set but training program has no regular contest."
            )

        if home_contest is not None:
            if home is None or home.contest is not home_contest:
                raise ValueError(
                    "Home contest participation missing or mismatched for training program."
                )
        elif home is not None:
            raise ValueError(
                "Home participation set but training program has no home contest."
            )


class Message(Base):
    """Class to store a private message from the managers to the
    user.

    """
    __tablename__ = 'messages'

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Time the message was sent.
    timestamp: datetime = Column(
        DateTime,
        nullable=False)

    # Subject and body of the message.
    subject: str = Column(
        Unicode,
        nullable=False)
    text: str = Column(
        Unicode,
        nullable=False)

    # Participation (id and object) owning the message.
    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    participation: Participation = relationship(
        Participation,
        back_populates="messages")

    contest_id: int | None = Column(
        Integer,
        ForeignKey(Contest.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
        index=True)
    contest: Contest | None = relationship(Contest)

    # Admin that sent the message (or null if the admin has been later
    # deleted). Admins only loosely "own" a message, so we do not back
    # populate any field in Admin, nor we delete the message when the admin
    # gets deleted.
    admin_id: int | None = Column(
        Integer,
        ForeignKey(Admin.id,
                   onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True)
    admin: Admin | None = relationship(Admin)

    def assert_valid(self):
        participation = self.participation
        if participation is None:
            return
        expects_contest = participation.is_training_program()
        has_contest = self.contest_id is not None
        if expects_contest != has_contest:
            raise ValueError("Message must set contest_id iff participation belongs to a training program.")


class Question(Base):
    """Class to store a private question from the user to the
    managers, and its answer.

    """
    __tablename__ = 'questions'

    MAX_SUBJECT_LENGTH = 50
    MAX_TEXT_LENGTH = 2000
    QUICK_ANSWERS = {
        "yes": "Yes",
        "no": "No",
        "invalid": "Invalid Question (not a Yes/No Question)",
        "nocomment": "No Comment/Please refer to task statement",
    }

    # Auto increment primary key.
    id: int = Column(
        Integer,
        primary_key=True)

    # Time the question was made.
    question_timestamp: datetime = Column(
        DateTime,
        nullable=False)

    # Subject and body of the question.
    subject: str = Column(
        Unicode,
        nullable=False)
    text: str = Column(
        Unicode,
        nullable=False)

    # Time the reply was sent.
    reply_timestamp: datetime | None = Column(
        DateTime,
        nullable=True)

    # Has this message been ignored by the admins?
    ignored: bool = Column(
        Boolean,
        nullable=False,
        default=False)

    # Short (as in 'chosen amongst some predetermined choices') and
    # long answer.
    reply_subject: str | None = Column(
        Unicode,
        nullable=True)
    reply_text: str | None = Column(
        Unicode,
        nullable=True)

    # Participation (id and object) owning the question.
    participation_id: int = Column(
        Integer,
        ForeignKey(Participation.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
        index=True)
    participation: Participation = relationship(
        Participation,
        back_populates="questions")

    contest_id: int | None = Column(
        Integer,
        ForeignKey(Contest.id,
                   onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
        index=True)
    contest: Contest | None = relationship(Contest)

    # Latest admin to interact with the question (null if no interactions
    # yet, or if the admin has been later deleted). Admins only loosely "own" a
    # question, so we do not back populate any field in Admin, nor delete the
    # question if the admin gets deleted.
    admin_id: int | None = Column(
        Integer,
        ForeignKey(Admin.id,
                   onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
        index=True)
    admin: Admin | None = relationship(Admin)

    def assert_valid(self):
        participation = self.participation
        if participation is None:
            return
        expects_contest = participation.is_training_program()
        has_contest = self.contest_id is not None
        if expects_contest != has_contest:
            raise ValueError("Question must set contest_id iff participation belongs to a training program.")
