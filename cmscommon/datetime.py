#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2012-2017 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Stefano Maggiolo <s.maggiolo@gmail.com>
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

import os
import sys
import time
from datetime import datetime, tzinfo
import typing

if typing.TYPE_CHECKING:
    from cms.db import User, Contest

import babel.dates


__all__ = [
    "make_datetime", "make_timestamp",
    "get_timezone", "get_system_timezone", "get_timezone_name",
    "utc_to_local", "local_to_utc", "format_datetime_for_input",

    "utc", "local_tz",
    ]


def make_datetime(timestamp: int | float | None = None) -> datetime:
    """Return the datetime object associated with the given timestamp.

    timestamp: a POSIX timestamp, or None to use now.

    return: the datetime representing the UTC time of the
        given timestamp.

    """
    if timestamp is None:
        return datetime.utcnow()
    else:
        return datetime.utcfromtimestamp(timestamp)


EPOCH = datetime(1970, 1, 1)


def make_timestamp(_datetime: datetime | None = None) -> float:
    """Return the timestamp associated with the given datetime object.

    _datetime: a datetime object, or None to use now.

    return: the POSIX timestamp corresponding to the given
        datetime ("read" in UTC).

    """
    if _datetime is None:
        return time.time()
    else:
        return (_datetime - EPOCH).total_seconds()


utc = babel.dates.UTC
local_tz = babel.dates.LOCALTZ


def get_timezone(user: "User | None", contest: "Contest | None") -> tzinfo:
    """Return the timezone for the given user and contest

    user: the user owning the timezone.
    contest: the contest in which the user is competing.

    return: the timezone information for the user.

    """
    if user and user.timezone is not None:
        try:
            return babel.dates.get_timezone(user.timezone)
        except LookupError:
            pass
    if contest and contest.timezone is not None:
        try:
            return babel.dates.get_timezone(contest.timezone)
        except LookupError:
            pass
    return local_tz


def get_system_timezone() -> str:
    """Return the name of the system timezone.

    return: the "best" description of the timezone of the
        local system clock that we were able to find, in a format like
        "Europe/Rome", "CET", etc.

    """
    if hasattr(local_tz, 'zone'):
        return local_tz.zone
    return local_tz.tzname(make_datetime())


def get_timezone_name(tz: tzinfo) -> str:
    """Return the name of the given timezone.

    tz: a timezone object.

    return: the "best" description of the timezone, in a format like
        "Europe/Rome", "CET", "UTC", etc.

    """
    if hasattr(tz, 'key'):
        return tz.key
    if hasattr(tz, 'zone'):
        return tz.zone
    return tz.tzname(make_datetime())


def utc_to_local(dt: datetime | None, tz: tzinfo) -> datetime | None:
    """Convert a naive UTC datetime to a naive local datetime in the given timezone.

    dt: a naive datetime in UTC, or None.
    tz: the target timezone.

    return: a naive datetime in the target timezone, or None if dt is None.

    """
    if dt is None:
        return None
    # Attach UTC timezone info, convert to target timezone, then strip tzinfo
    utc_dt = dt.replace(tzinfo=utc)
    local_dt = utc_dt.astimezone(tz)
    return local_dt.replace(tzinfo=None)


def local_to_utc(dt: datetime, tz: tzinfo) -> datetime:
    """Convert a naive local datetime to a naive UTC datetime.

    dt: a naive datetime in the given timezone.
    tz: the source timezone.

    return: a naive datetime in UTC.

    """
    # Attach timezone info, convert to UTC, then strip tzinfo
    if hasattr(tz, "localize"):
        # pytz timezones need localize() for proper DST handling
        local_dt = tz.localize(dt, is_dst=None)
    else:
        local_dt = dt.replace(tzinfo=tz)
    utc_dt = local_dt.astimezone(utc)
    return utc_dt.replace(tzinfo=None)


def format_datetime_for_input(dt: datetime | None, tz: tzinfo) -> str:
    """Format a UTC datetime for display in an input field, converted to local timezone.

    dt: a naive datetime in UTC, or None.
    tz: the target timezone for display.

    return: a string in "YYYY-MM-DD HH:MM:SS" format in the target timezone,
            or empty string if dt is None.

    """
    if dt is None:
        return ""
    local_dt = utc_to_local(dt, tz)
    if local_dt is None:
        return ""
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")
