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

"""Admin-only utilities for training programs and related handlers."""

import typing

from sqlalchemy import func, union

from cms.db import (
    Session,
    Student,
    Participation,
    Question,
    DelayRequest,
    ArchivedStudentRanking,
    TrainingDay,
)

if typing.TYPE_CHECKING:
    from cms.db import TrainingProgram


def get_all_student_tags(
    sql_session: Session,
    training_program: "TrainingProgram",
    include_historical: bool = False,
) -> list[str]:
    """Get all unique student tags from a training program's students.

    Uses GIN index on student_tags for efficient querying.

    sql_session: The database session.
    training_program: The training program to get tags from.
    include_historical: If True, also include tags from archived rankings.

    return: Sorted list of unique tags.
    """
    current_tags_query = (
        sql_session.query(func.unnest(Student.student_tags).label("tag"))
        .filter(Student.training_program_id == training_program.id)
    )

    if include_historical:
        training_day_ids = [td.id for td in training_program.training_days]
        if training_day_ids:
            historical_tags_query = (
                sql_session.query(
                    func.unnest(ArchivedStudentRanking.student_tags).label("tag")
                )
                .filter(ArchivedStudentRanking.training_day_id.in_(training_day_ids))
            )
            combined_query = union(current_tags_query, historical_tags_query)
            rows = sql_session.execute(combined_query).fetchall()
            return sorted({row[0] for row in rows if row[0]})

    rows = current_tags_query.distinct().all()
    return sorted([row.tag for row in rows if row.tag])


def get_all_training_day_types(training_program: "TrainingProgram") -> list[str]:
    """Get all unique training day types from a training program's training days."""
    all_types_set: set[str] = set()
    for training_day in training_program.training_days:
        if training_day.training_day_types:
            all_types_set.update(training_day.training_day_types)
    return sorted(all_types_set)


def build_user_to_student_map(
    training_program: "TrainingProgram",
) -> dict[int, "Student"]:
    """Build a mapping of user_id -> Student for efficient lookups."""
    user_to_student: dict[int, "Student"] = {}
    for student in training_program.students:
        user_to_student[student.participation.user_id] = student
    return user_to_student


def get_student_tags_by_participation(
    sql_session: Session,
    training_program: "TrainingProgram",
    participation_ids: list[int],
) -> dict[int, list[str]]:
    """Get student tags for multiple participations in a training program."""
    result = {pid: [] for pid in participation_ids}
    if not participation_ids:
        return result

    rows = (
        sql_session.query(Student.participation_id, Student.student_tags)
        .filter(Student.training_program_id == training_program.id)
        .filter(Student.participation_id.in_(participation_ids))
        .all()
    )
    for participation_id, tags in rows:
        result[participation_id] = tags or []

    return result


def count_unanswered_questions(sql_session: Session, contest_id: int) -> int:
    """Count unanswered questions for a contest."""
    return (
        sql_session.query(Question)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(Question.reply_timestamp.is_(None))
        .filter(Question.ignored.is_(False))
        .count()
    )


def count_pending_delay_requests(sql_session: Session, contest_id: int) -> int:
    """Count pending delay requests for a contest."""
    return (
        sql_session.query(DelayRequest)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(DelayRequest.status == "pending")
        .count()
    )


def get_training_day_notifications(
    sql_session: Session,
    training_day: "TrainingDay",
) -> dict:
    """Get notification counts for a training day."""
    if training_day.contest is None:
        return {}

    return {
        "unanswered_questions": count_unanswered_questions(
            sql_session, training_day.contest_id
        ),
        "pending_delay_requests": count_pending_delay_requests(
            sql_session, training_day.contest_id
        ),
    }


def get_all_training_day_notifications(
    sql_session: Session,
    training_program: "TrainingProgram",
) -> tuple[dict[int, dict], int, int]:
    """Get notification counts for all training days in a program."""
    notifications: dict[int, dict] = {}
    total_unanswered = 0
    total_pending = 0

    for td in training_program.training_days:
        if td.contest is None:
            continue

        td_notifications = get_training_day_notifications(sql_session, td)
        notifications[td.id] = td_notifications
        total_unanswered += td_notifications.get("unanswered_questions", 0)
        total_pending += td_notifications.get("pending_delay_requests", 0)

    return notifications, total_unanswered, total_pending


def deduplicate_preserving_order(items: list[str]) -> list[str]:
    """Remove duplicates from a list while preserving order."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_tags(tags_str: str) -> list[str]:
    """Parse a comma-separated string of tags into a list of normalized tags."""
    if not tags_str:
        return []

    tags = [tag.strip().lower() for tag in tags_str.split(",") if tag.strip()]
    return deduplicate_preserving_order(tags)


def parse_usernames_from_file(file_content: str) -> list[str]:
    """Parse whitespace-separated usernames from file content."""
    if not file_content:
        return []

    usernames = [u.strip() for u in file_content.split() if u.strip()]
    return deduplicate_preserving_order(usernames)
