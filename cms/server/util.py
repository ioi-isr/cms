#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2016 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2016 Amir Keivan Mohtashami <akmohtashami97@gmail.com>
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

"""Random utilities for web servers and page templates.

"""

import logging
from datetime import date, timedelta
from functools import wraps
from urllib.parse import quote, urlencode

import collections
try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import typing

from tornado.web import RequestHandler

from cms.db import Session, Contest, Student, Task, Participation, StudentTask, Question, DelayRequest
from sqlalchemy.orm import joinedload
from cms.grading.scorecache import get_cached_score_entry
from cms.server.file_middleware import FileServerMiddleware
from cmscommon.datetime import make_datetime

if typing.TYPE_CHECKING:
    from cms.db import TrainingDay, TrainingDayGroup, TrainingProgram

logger = logging.getLogger(__name__)


def exclude_internal_contests(query):
    """Exclude internal/system contests from a query.

    This excludes:
    - Contests with names starting with '__' (legacy internal contests)
    - Contests that are managing contests for training programs

    query: SQLAlchemy query object for Contest queries

    return: Query object with internal contests filtered out
    """
    return query.filter(
        ~Contest.name.like(r'\_\_%', escape='\\')
    ).filter(
        ~Contest.training_program.has()
    )


def get_all_student_tags(training_program: "TrainingProgram") -> list[str]:
    """Get all unique student tags from a training program's students.

    This is a shared utility to avoid duplicating tag collection logic
    across multiple handlers.

    training_program: the training program to get tags from.

    return: sorted list of unique student tags.

    """
    all_tags_set: set[str] = set()
    for student in training_program.students:
        if student.student_tags:
            all_tags_set.update(student.student_tags)
    return sorted(all_tags_set)


def get_all_student_tags_with_historical(
    training_program: "TrainingProgram"
) -> list[str]:
    """Get all unique student tags including historical tags from archived rankings.

    This includes both current student tags and tags that students had during
    past training days (stored in ArchivedStudentRanking.student_tags).

    training_program: the training program to get tags from.

    return: sorted list of unique student tags (current + historical).

    """
    all_tags_set: set[str] = set()
    # Collect current tags
    for student in training_program.students:
        if student.student_tags:
            all_tags_set.update(student.student_tags)
    # Collect historical tags from archived rankings
    for training_day in training_program.training_days:
        for ranking in training_day.archived_student_rankings:
            if ranking.student_tags:
                all_tags_set.update(ranking.student_tags)
    return sorted(all_tags_set)


def get_all_training_day_types(training_program: "TrainingProgram") -> list[str]:
    """Get all unique training day types from a training program's training days.

    This is a shared utility to avoid duplicating tag collection logic
    across multiple handlers.

    training_program: the training program to get types from.

    return: sorted list of unique training day types.

    """
    all_types_set: set[str] = set()
    for training_day in training_program.training_days:
        if training_day.training_day_types:
            all_types_set.update(training_day.training_day_types)
    return sorted(all_types_set)


def get_student_for_training_day(
    sql_session: Session,
    participation: "Participation",
    training_day: "TrainingDay"
) -> "Student | None":
    """Get the student record for a participation in a training day.

    sql_session: the database session.
    participation: the participation to look up.
    training_day: the training day.

    return: the Student record, or None if not found.

    """
    # Single query with join instead of two separate queries
    managing_contest = training_day.training_program.managing_contest
    return sql_session.query(Student).join(
        Participation, Student.participation_id == Participation.id
    ).filter(
        Participation.contest_id == managing_contest.id,
        Participation.user_id == participation.user_id,
        Student.training_program_id == training_day.training_program_id
    ).first()


def check_training_day_eligibility(
    sql_session: Session,
    participation: "Participation",
    training_day: "TrainingDay | None"
) -> tuple[bool, "TrainingDayGroup | None", list[str]]:
    """Check if a participation is eligible for a training day.

    A student is eligible if:
    - The training day has no main groups configured (all students eligible), OR
    - The student has exactly one main group tag

    sql_session: the database session.
    participation: the participation to check.
    training_day: the training day to check, or None for non-training-day contests.

    return: tuple of (is_eligible, main_group, matching_tags)
        - is_eligible: True if the student can participate
        - main_group: the TrainingDayGroup if exactly one match, else None
        - matching_tags: list of main group tags the student has

    """
    if training_day is None:
        return True, None, []

    # If no main groups configured, all students are eligible
    if not training_day.groups:
        return True, None, []

    # Find the student record
    student = get_student_for_training_day(sql_session, participation, training_day)

    if student is None:
        # No student record means they're not in the training program
        return False, None, []

    # Build dict for O(1) lookup of groups by tag name
    groups_by_tag = {g.tag_name.lower(): g for g in training_day.groups}

    # Find which main group tags the student has
    student_tags = {tag.lower() for tag in (student.student_tags or [])}
    matching_tags = sorted(student_tags & groups_by_tag.keys())

    # Eligible only if exactly one main group tag
    if len(matching_tags) == 1:
        # O(1) lookup instead of O(n) scan
        return True, groups_by_tag[matching_tags[0]], matching_tags

    return False, None, matching_tags


def get_training_day_timing_info(
    sql_session: Session,
    td_contest: "Contest",
    user: "User",
    training_day: "TrainingDay",
    timestamp: datetime
) -> dict | None:
    """Get participation and timing info for a user in a training day contest.

    This is a common pattern used to check if a user can access a training day
    and compute the effective timing information.

    sql_session: the database session.
    td_contest: the training day's contest.
    user: the user to check.
    training_day: the training day.
    timestamp: current timestamp for computing actual phase.

    return: dict with timing info, or None if user is not eligible.
        - participation: the user's Participation in the training day contest
        - main_group: the TrainingDayGroup if applicable
        - contest_start: effective contest start time
        - contest_stop: effective contest stop time
        - actual_phase: the computed actual phase
        - user_start_time: user-specific start time (contest_start + delay)
        - duration: contest duration

    """
    from cms.server.contest.phase_management import (
        compute_actual_phase, compute_effective_times
    )

    td_participation = (
        sql_session.query(Participation)
        .filter(Participation.contest == td_contest)
        .filter(Participation.user == user)
        .first()
    )

    if td_participation is None:
        return None

    is_eligible, main_group, _ = check_training_day_eligibility(
        sql_session, td_participation, training_day
    )
    if not is_eligible:
        return None

    main_group_start = main_group.start_time if main_group else None
    main_group_end = main_group.end_time if main_group else None
    contest_start, contest_stop = compute_effective_times(
        td_contest.start, td_contest.stop,
        td_participation.delay_time,
        main_group_start, main_group_end
    )

    actual_phase, _, _, _, _ = compute_actual_phase(
        timestamp,
        contest_start,
        contest_stop,
        td_contest.analysis_start if td_contest.analysis_enabled else None,
        td_contest.analysis_stop if td_contest.analysis_enabled else None,
        td_contest.per_user_time,
        td_participation.starting_time,
        td_participation.delay_time,
        td_participation.extra_time,
    )

    user_start_time = contest_start + td_participation.delay_time

    duration = td_contest.per_user_time \
        if td_contest.per_user_time is not None else \
        contest_stop - contest_start

    return {
        "participation": td_participation,
        "main_group": main_group,
        "contest_start": contest_start,
        "contest_stop": contest_stop,
        "actual_phase": actual_phase,
        "user_start_time": user_start_time,
        "duration": duration,
    }


def can_access_task(sql_session: Session, task: "Task", participation: "Participation",
                    training_day: "TrainingDay | None") -> bool:
    """Check if a participation can access the given task.

    For training day contests, tasks may have visibility restrictions
    based on student tags. A task is accessible if:
    - The task has no visible_to_tags (empty list = visible to all)
    - The student has at least one tag matching the task's visible_to_tags

    For non-training-day contests, all tasks are accessible.

    sql_session: the database session.
    task: the task to check access for.
    participation: the participation to check access for.
    training_day: the training day if this is a training day contest, else None.

    return: True if the participation can access the task.

    """
    # Only apply visibility filtering for training day contests
    if training_day is None:
        return True

    # If task has no visibility restrictions, it's visible to all
    if not task.visible_to_tags:
        return True

    # Find the student record for this participation
    student = get_student_for_training_day(sql_session, participation, training_day)

    if student is None:
        return False

    # Check if student has any matching tag
    student_tags_set = {tag.lower() for tag in (student.student_tags or [])}
    task_tags_set = {tag.lower() for tag in task.visible_to_tags}
    return bool(student_tags_set & task_tags_set)


def get_student_archive_scores(
    sql_session: Session,
    student: "Student",
    participation: "Participation",
    contest: "Contest",
) -> dict[int, float]:
    """Get fresh task scores for all tasks in a student's archive.
    This utility uses get_cached_score_entry to ensure scores are fresh
    and not stale. It returns a mapping of task_id -> score for all tasks
    that are both in the student's archive AND currently exist in the contest.
    IMPORTANT: This function may trigger cache rebuilds which acquire advisory
    locks. The caller MUST commit the session after calling this function to
    release the locks and persist any cache updates.
    sql_session: the database session.
    student: the Student object (with student_tasks relationship).
    participation: the Participation object for the managing contest.
    contest: the Contest object (managing contest for the training program).
    return: dict mapping task_id -> score for tasks in the student's archive.
    """

    student_task_ids = {st.task_id for st in student.student_tasks}
    scores = {}

    for task in contest.get_tasks():
        if task.id not in student_task_ids:
            continue
        cache_entry = get_cached_score_entry(sql_session, participation, task)
        scores[task.id] = cache_entry.score

    return scores


def calculate_task_archive_progress(
    student: "Student",
    participation: "Participation",
    contest: "Contest",
    sql_session: Session,
    include_task_details: bool = False,
    submission_counts: dict[int, int] | None = None,
) -> dict:
    """Calculate task archive progress for a student.

    This is a shared utility used by both the admin students page and
    the contest training program overview page.

    student: the Student object (with student_tasks relationship).
    participation: the Participation object.
    contest: the Contest object (managing contest for the training program).
    sql_session: SQLAlchemy session for using get_cached_score_entry.
    include_task_details: if True, include per-task breakdown in task_scores list.
    submission_counts: optional dict mapping task_id to submission count.
        If provided and include_task_details is True, each task will include
        a submission_count field.

    return: dict with total_score, max_score, percentage, task_count.
            If include_task_details is True, also includes task_scores list.

    """
    # Get the tasks in the student's archive
    student_tasks = (
        sql_session.query(StudentTask)
        .options(joinedload(StudentTask.task))
        .filter(StudentTask.student_id == student.id)
        .all()
    )
    cached_scores = get_student_archive_scores(
        sql_session, student, participation, contest
    )

    total_score = 0.0
    max_score = 0.0
    task_count = 0
    task_scores = [] if include_task_details else None

    contest_tasks = contest.get_tasks()
    # Iterate only over tasks in the student's archive (StudentTask entries)
    for student_task in student_tasks:
        task = student_task.task
        if task is None or task not in contest_tasks:
            continue
        task_count += 1
        max_task_score = task.active_dataset.score_type_object.max_score \
            if task.active_dataset else 100.0
        max_score += max_task_score
        best_score = cached_scores[task.id]
        total_score += best_score

        if include_task_details:
            task_info = {
                "task": task,
                "score": best_score,
                "max_score": max_task_score,
                "source_training_day": student_task.source_training_day,
                "assigned_at": student_task.assigned_at,
            }
            if submission_counts is not None:
                task_info["submission_count"] = submission_counts.get(task.id, 0)
            task_scores.append(task_info)

    percentage = (total_score / max_score * 100) if max_score > 0 else 0.0

    result = {
        "total_score": total_score,
        "max_score": max_score,
        "percentage": percentage,
        "task_count": task_count,
    }

    if include_task_details:
        result["task_scores"] = task_scores

    return result


def get_student_for_user_in_program(
    sql_session: Session,
    training_program: "TrainingProgram",
    user_id: int
) -> "Student | None":
    """Get the student record for a user in a training program.

    This is a common query pattern used across many handlers to find
    the Student record for a given user in a training program.

    sql_session: the database session.
    training_program: the training program to search in.
    user_id: the user ID to look up.

    return: the Student record, or None if not found.

    """
    managing_contest = training_program.managing_contest
    return sql_session.query(Student).join(
        Participation, Student.participation_id == Participation.id
    ).filter(
        Participation.contest_id == managing_contest.id,
        Participation.user_id == user_id,
        Student.training_program_id == training_program.id
    ).first()


def get_student_tags_by_participation(
    sql_session: Session,
    training_program: "TrainingProgram",
    participation_ids: list[int]
) -> dict[int, list[str]]:
    """Get student tags for multiple participations in a training program.

    This is a batch query utility that efficiently fetches student tags
    for multiple participations at once, avoiding N+1 query patterns.

    sql_session: the database session.
    training_program: the training program to search in.
    participation_ids: list of participation IDs to look up.

    return: dict mapping participation_id to list of student tags.

    """
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
    """Count unanswered questions for a contest.

    This counts questions that have not been replied to and are not ignored.

    sql_session: the database session.
    contest_id: the contest ID to count questions for.

    return: count of unanswered questions.

    """
    return (
        sql_session.query(Question)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(Question.reply_timestamp.is_(None))
        .filter(Question.ignored.is_(False))
        .count()
    )


def count_pending_delay_requests(sql_session: Session, contest_id: int) -> int:
    """Count pending delay requests for a contest.

    sql_session: the database session.
    contest_id: the contest ID to count delay requests for.

    return: count of pending delay requests.

    """
    return (
        sql_session.query(DelayRequest)
        .join(Participation)
        .filter(Participation.contest_id == contest_id)
        .filter(DelayRequest.status == "pending")
        .count()
    )


def get_training_day_notifications(
    sql_session: Session,
    training_day: "TrainingDay"
) -> dict:
    """Get notification counts for a training day.

    Returns a dict with unanswered_questions and pending_delay_requests counts.

    sql_session: the database session.
    training_day: the training day to get notifications for.

    return: dict with notification counts, or empty dict if training day has no contest.

    """
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
    training_program: "TrainingProgram"
) -> tuple[dict[int, dict], int, int]:
    """Get notification counts for all training days in a program.

    Returns notification counts for each active training day (those with a contest),
    plus totals across all training days.

    sql_session: the database session.
    training_program: the training program to get notifications for.

    return: tuple of (notifications_by_td_id, total_unanswered, total_pending)
        - notifications_by_td_id: dict mapping training_day.id to notification dict
        - total_unanswered: total unanswered questions across all training days
        - total_pending: total pending delay requests across all training days

    """
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


# TODO: multi_contest is only relevant for CWS
def multi_contest(f):
    """Return decorator swallowing the contest name if in multi contest mode.

    """
    @wraps(f)
    def wrapped_f(self, *args):
        if self.is_multi_contest():
            # Swallow the first argument (the contest name).
            f(self, *(args[1:]))
        else:
            # Otherwise, just forward all arguments.
            f(self, *args)
    return wrapped_f


def validate_date_of_birth(date_of_birth_str: str) -> date:
    """Validate date of birth string and return parsed date.

    Args:
        date_of_birth_str: Date string in ISO format (YYYY-MM-DD)

    Returns:
        Parsed date object

    Raises:
        ValueError: If date is invalid, in future, or more than 120 years ago
    """
    if not date_of_birth_str:
        raise ValueError("date_of_birth cannot be empty")

    try:
        parsed_date = date.fromisoformat(date_of_birth_str)
    except ValueError as e:
        raise ValueError("Invalid date of birth format") from e

    # Validate date is not in the future
    today = date.today()
    if parsed_date > today:
        raise ValueError("Date of birth cannot be in the future")

    # Add 120-year lower bound check using timedelta to handle leap years
    max_age_delta = timedelta(days=120 * 365.25)  # ~120 years accounting for leap years
    if today - parsed_date > max_age_delta:
        raise ValueError("Date of birth cannot be more than 120 years ago")

    return parsed_date


class FileHandlerMixin(RequestHandler):

    """Provide methods for serving files.

    Due to shortcomings of Tornado's WSGI support we need to resort to
    hack-ish solutions to achieve efficient file serving. For a more
    detailed explanation see the docstrings of FileServerMiddleware.

    """

    def fetch(self, digest: str, content_type: str, filename: str | None = None, disposition: str | None = None):
        """Serve the file with the given digest.

        This will just add the headers required to trigger
        FileServerMiddleware, which will do the real work.

        digest: the digest of the file that has to be served.
        content_type: the MIME type the file should be served as.
        filename: the name the file should be served as.
        disposition: value to set the Content-Disposition header to.

        """
        self.set_header(FileServerMiddleware.DIGEST_HEADER, digest)
        if filename is not None:
            self.set_header(FileServerMiddleware.FILENAME_HEADER, filename)
        if disposition is not None:
            self.set_header(FileServerMiddleware.DISPOSITION_HEADER, disposition)
        self.set_header("Content-Type", content_type)
        self.finish()


def get_url_root(request_path: str) -> str:
    """Return a relative URL pointing to the root of the website.

    request_path: the starting point of the relative path.

    return: relative URL from request_path to the root.

    """

    # Compute the number of levels we would need to ascend.
    path_depth = request_path.count("/") - 1

    if path_depth > 0:
        return "/".join([".."] * path_depth)
    else:
        return "."


class Url:
    """An object that helps in building a URL piece by piece.

    """

    def __init__(self, url_root: str):
        """Create a URL relative to the given root.

        url_root: the root of all paths that are generated.

        """
        assert not url_root.endswith("/") or url_root == "/"
        self.url_root = url_root

    def __call__(self, *args: object, **kwargs: object) -> str:
        """Generate a URL.

        Assemble a URL using the positional arguments as URL components
        and the keyword arguments as the query string. The URL will be
        relative to the root given to the constructor.

        args: the path components (will be cast to strings).
        kwargs: the query parameters (values will be cast to strings).

        return: the desired URL.

        """
        url = self.url_root
        for component in args:
            if not url.endswith("/"):
                url += "/"
            url += quote("%s" % component, safe="")
        if kwargs:
            url += "?" + urlencode(kwargs)
        return url

    def __getitem__(self, component: object) -> typing.Self:
        """Produce a new Url obtained by extending this instance.

        Return a new Url object that will generate paths based on this
        instance's URL root extended with the path component given as
        argument. That is, if url() is "/foo", then url["bar"]() is
        "/foo/bar".

        component: the path component (will be cast to string).

        return: the extended URL generator.

        """
        return self.__class__(self.__call__(component))


class CommonRequestHandler(RequestHandler):
    """Encapsulates shared RequestHandler functionality.

    """

    # Whether the login cookie duration has to be refreshed when
    # this handler is called. Useful to filter asynchronous
    # requests.
    refresh_cookie = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timestamp = make_datetime()
        self.sql_session = Session()
        self.r_params = None
        self.contest = None
        self.url: Url = None

    def prepare(self):
        """This method is executed at the beginning of each request.

        """
        super().prepare()
        self.url = Url(get_url_root(self.request.path))
        self.set_header("Cache-Control", "no-cache, must-revalidate")

    def finish(self, *args, **kwargs):
        """Finish this response, ending the HTTP request.

        We override this method in order to properly close the database.

        TODO - Now that we have greenlet support, this method could be
        refactored in terms of context manager or something like
        that. So far I'm leaving it to minimize changes.

        """
        if self.sql_session is not None:
            try:
                self.sql_session.close()
            except Exception as error:
                logger.warning("Couldn't close SQL connection: %r", error)
        try:
            super().finish(*args, **kwargs)
        except OSError:
            # When the client closes the connection before we reply,
            # Tornado raises an OSError exception, that would pollute
            # our log with unnecessarily critical messages
            logger.debug("Connection closed before our reply.")

    @property
    def service(self):
        return self.application.service


def deduplicate_preserving_order(items: list[str]) -> list[str]:
    """Remove duplicates from a list while preserving order.

    Args:
        items: List of strings that may contain duplicates

    Returns:
        List of strings with duplicates removed, preserving original order
    """
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse_tags(tags_str: str) -> list[str]:
    """Parse a comma-separated string of tags into a list of normalized tags.

    This utility handles:
    - Splitting by comma
    - Stripping whitespace
    - converting to lowercase
    - Removing empty tags
    - Deduplicating while preserving order

    Args:
        tags_str: Comma-separated string of tags

    Returns:
        List of unique, normalized tags
    """
    if not tags_str:
        return []

    tags = [tag.strip().lower() for tag in tags_str.split(",") if tag.strip()]
    return deduplicate_preserving_order(tags)


def parse_usernames_from_file(file_content: str) -> list[str]:
    """Parse whitespace-separated usernames from file content.

    This utility handles:
    - Splitting by whitespace (spaces, newlines, tabs)
    - Stripping whitespace from each username
    - Removing empty entries
    - Deduplicating while preserving order

    Args:
        file_content: String content of the uploaded file

    Returns:
        List of unique usernames in order of first appearance
    """
    if not file_content:
        return []

    usernames = [u.strip() for u in file_content.split() if u.strip()]
    return deduplicate_preserving_order(usernames)
