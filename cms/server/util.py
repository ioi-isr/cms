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

from cms.db import Session, Student, Task, Participation
from cms.server.file_middleware import FileServerMiddleware
from cmscommon.datetime import make_datetime

if typing.TYPE_CHECKING:
    from cms.db import Contest, TrainingDay, TrainingDayGroup, TrainingProgram

logger = logging.getLogger(__name__)


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
    groups_by_tag = {g.tag_name: g for g in training_day.groups}

    # Find which main group tags the student has
    student_tags = set(student.student_tags or [])
    matching_tags = sorted(student_tags & groups_by_tag.keys())

    # Eligible only if exactly one main group tag
    if len(matching_tags) == 1:
        # O(1) lookup instead of O(n) scan
        return True, groups_by_tag[matching_tags[0]], matching_tags

    return False, None, matching_tags


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
    # Note: Student records are linked to the managing contest participation,
    # not the training day participation. So we need to find the user's
    # participation in the managing contest first.
    managing_contest = training_day.training_program.managing_contest
    managing_participation = sql_session.query(Participation).filter(
        Participation.contest_id == managing_contest.id,
        Participation.user_id == participation.user_id
    ).first()

    if managing_participation is None:
        return False

    student = sql_session.query(Student).filter(
        Student.participation_id == managing_participation.id,
        Student.training_program_id == training_day.training_program_id
    ).first()

    if student is None:
        return False

    # Check if student has any matching tag
    student_tags_set = set(student.student_tags or [])
    task_tags_set = set(task.visible_to_tags)
    return bool(student_tags_set & task_tags_set)


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
