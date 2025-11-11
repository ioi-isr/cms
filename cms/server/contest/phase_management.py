#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
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

from collections.abc import Callable
from datetime import datetime, timedelta
from functools import wraps
import typing

if typing.TYPE_CHECKING:
    from cms.server.contest.handlers.contest import ContestHandler


def compute_actual_phase(
    timestamp: datetime,
    contest_start: datetime,
    contest_stop: datetime,
    analysis_start: datetime | None,
    analysis_stop: datetime | None,
    per_user_time: timedelta | None,
    starting_time: datetime | None,
    delay_time: timedelta,
    extra_time: timedelta,
) -> tuple[int, datetime | None, datetime | None, datetime | None, datetime | None]:
    """Determine the current phase and when the active phase is.

    The "actual phase" of the contest for a certain user is the status
    in which the contest is presented to the user and determines the
    information the latter is allowed to see (and the actions he is
    allowed to perform). In general it may be different for each user.

    The phases, and their meaning, are the following:
    * -2: the user cannot compete because the contest hasn't started
          yet for them (contest_start + delay_time is not yet reached);
    * -1: the user cannot compete because, even if the contest has
          already started, its per-user time frame hasn't yet (this
          usually means the user still has to click on the "start!"
          button in USACO-like contests);
    * 0: the user can compete;
    * +1: the user cannot compete because, even if the contest hasn't
          stopped yet, its per-user time frame already has (again, this
          should normally happen only in USACO-like contests);
    * +2: the user cannot compete because the contest has already
          stopped and the analysis mode hasn't started yet.
    * +3: the user can take part in analysis mode.
    * +4: the user cannot compete because the contest has already
          stopped. analysis mode has already finished or has been
          disabled for this contest.
    A user is said to "compete" if he can read the tasks' statements,
    submit solutions, see their results, etc.

    This function returns the actual phase at the given timestamp, as
    well as its boundaries (i.e. when it started and will end, with
    None meaning +/- infinity) and the boundaries of the phase 0 (if it
    is defined, otherwise None).

    timestamp: the current time.
    contest_start: the contest's start.
    contest_stop: the contest's stop.
    per_user_time: the amount of time allocated to
        each user; if it's None the contest is traditional, otherwise
        it's USACO-like.
    starting_time: when the user started their time
        frame.
    delay_time: how much the user's start is delayed.
    extra_time: how much extra time is given to the user at
        the end.

    return: 5 items: an integer (in [-2, +2]) and two pairs of
        datetimes (or None) defining two intervals.

    """
    # Validate arguments.
    assert (isinstance(timestamp, datetime) and
            isinstance(contest_start, datetime) and
            isinstance(contest_stop, datetime) and
            (per_user_time is None or isinstance(per_user_time, timedelta)) and
            (starting_time is None or isinstance(starting_time, datetime)) and
            isinstance(delay_time, timedelta) and
            isinstance(extra_time, timedelta))

    assert contest_start <= contest_stop
    assert per_user_time is None or per_user_time >= timedelta()
    assert delay_time >= timedelta()
    assert extra_time >= timedelta()

    earliest_permitted_start = contest_start + delay_time
    latest_permitted_stop = contest_stop + delay_time + extra_time

    if per_user_time is not None and starting_time is None:
        # "USACO-like" contest, but we still don't know when the user
        # started/will start.
        actual_start = None
        actual_stop = None

        if earliest_permitted_start <= timestamp <= latest_permitted_stop:
            actual_phase = -1
            current_phase_begin = earliest_permitted_start
            current_phase_end = latest_permitted_stop
        elif timestamp < earliest_permitted_start:
            actual_phase = -2
            current_phase_begin = None
            current_phase_end = earliest_permitted_start
        elif latest_permitted_stop < timestamp:
            actual_phase = +2
            current_phase_begin = latest_permitted_stop
            current_phase_end = None
        else:
            raise RuntimeError("Logic doesn't seem to be working...")

    else:
        if per_user_time is None:
            # "Traditional" contest.
            actual_start = earliest_permitted_start
            actual_stop = latest_permitted_stop
        else:
            # "USACO-like" contest, and we already know when the user
            # started/will start.
            # Both values are lower- and upper-bounded to prevent the
            # ridiculous situations of starting_time being set by the
            # admin way before contest_start or after contest_stop.
            actual_start = min(max(starting_time,
                                   earliest_permitted_start), latest_permitted_stop)
            actual_stop = min(max(starting_time + per_user_time + extra_time,
                                    earliest_permitted_start), latest_permitted_stop)

        assert earliest_permitted_start <= actual_start <= actual_stop <= latest_permitted_stop

        if actual_start <= timestamp <= actual_stop:
            actual_phase = 0
            current_phase_begin = actual_start
            current_phase_end = actual_stop
        elif earliest_permitted_start <= timestamp < actual_start:
            # This also includes a funny corner case: the user's start
            # is known but is in the future (the admin either set it
            # that way or added some delay after the user started).
            actual_phase = -1
            current_phase_begin = earliest_permitted_start
            current_phase_end = actual_start
        elif timestamp < earliest_permitted_start:
            actual_phase = -2
            current_phase_begin = None
            current_phase_end = earliest_permitted_start
        elif actual_stop < timestamp <= latest_permitted_stop:
            actual_phase = +1
            current_phase_begin = actual_stop
            current_phase_end = latest_permitted_stop
        elif latest_permitted_stop < timestamp:
            actual_phase = +2
            current_phase_begin = latest_permitted_stop
            current_phase_end = None
        else:
            raise RuntimeError("Logic doesn't seem to be working...")

    if actual_phase == +2:
        # If the user didn't reach actual_stop yet, it shouldn't be phase +2
        assert actual_stop is None or actual_stop <= timestamp
        if analysis_start is not None:
            # For regular contests, analysis can only start after contest_stop
            if per_user_time is None:
                assert contest_stop <= analysis_start
            else:
                assert contest_start + per_user_time <= analysis_start
            assert analysis_stop is not None
            assert analysis_start <= analysis_stop
            if timestamp < analysis_start:
                current_phase_end = analysis_start
            elif analysis_start <= timestamp <= analysis_stop:
                if per_user_time is not None and starting_time is None and analysis_start <= latest_permitted_stop:
                    if timestamp <= latest_permitted_stop:
                        current_phase_begin = analysis_start
                    else:
                        current_phase_begin = latest_permitted_stop
                    current_phase_end = analysis_stop
                else:
                    current_phase_begin = analysis_start
                    # actual_stop might be greater than analysis_start in case
                    # of extra_time or delay_time.
                    if actual_stop is not None:
                        current_phase_begin = max(analysis_start, actual_stop)
                    current_phase_end = analysis_stop
                    actual_phase = +3
            elif analysis_stop < timestamp:
                # For USACO contests where user hasn't started
                if per_user_time is not None and starting_time is None and analysis_start <= latest_permitted_stop:
                    if timestamp <= latest_permitted_stop:
                        current_phase_begin = analysis_stop
                    else:
                        current_phase_begin = max(analysis_stop, latest_permitted_stop)
                else:
                    current_phase_begin = analysis_stop
                    if actual_stop is not None:
                        current_phase_begin = max(analysis_stop, actual_stop)
                current_phase_end = None
                actual_phase = +4
            else:
                raise RuntimeError("Logic doesn't seem to be working...")
        else:
            actual_phase = +4

    if (per_user_time is not None and starting_time is not None and
            analysis_start is not None and analysis_stop is not None):
        personal_analysis_begin = max(analysis_start, actual_stop)
        
        if actual_phase == +1:
            if timestamp < personal_analysis_begin <= latest_permitted_stop:
                current_phase_end = personal_analysis_begin
            elif personal_analysis_begin <= timestamp <= analysis_stop:
                actual_phase = +3
                current_phase_begin = personal_analysis_begin
                current_phase_end = analysis_stop
            elif timestamp > analysis_stop:
                actual_phase = +4
                if timestamp <= latest_permitted_stop:
                    current_phase_begin = max(analysis_stop, actual_stop)
                    current_phase_end = latest_permitted_stop
                else:
                    current_phase_begin = latest_permitted_stop
                    current_phase_end = None
        elif actual_phase == +2:
            if personal_analysis_begin <= timestamp <= analysis_stop:
                actual_phase = +3
                current_phase_begin = personal_analysis_begin
                current_phase_end = analysis_stop
            elif timestamp > analysis_stop:
                actual_phase = +4
                if timestamp <= latest_permitted_stop:
                    current_phase_begin = max(analysis_stop, actual_stop)
                    current_phase_end = latest_permitted_stop
                else:
                    current_phase_begin = latest_permitted_stop
                    current_phase_end = None
        elif actual_phase == +4:
            if timestamp > latest_permitted_stop and timestamp > analysis_stop:
                current_phase_begin = max(analysis_stop, latest_permitted_stop)
                current_phase_end = None

    return (actual_phase,
            current_phase_begin, current_phase_end,
            actual_start, actual_stop)


def actual_phase_required(*actual_phases: int):
    """Return decorator filtering out requests in the wrong phase.

    actual_phases: the phases in which the request can pass.

    return: the decorator.

    """

    _P = typing.ParamSpec("_P")
    _R = typing.TypeVar("_R")
    _Self = typing.TypeVar("_Self", bound="ContestHandler")

    def decorator(
        func: Callable[typing.Concatenate[_Self, _P], _R],
    ) -> Callable[typing.Concatenate[_Self, _P], _R | None]:
        @wraps(func)
        def wrapped(self: _Self, *args: _P.args, **kwargs: _P.kwargs):
            unrestricted = self.current_user is not None and self.current_user.unrestricted
            if self.impersonated_by_admin:
                try:
                    unrestricted = self.get_boolean_argument("override_phase_check", unrestricted)
                except ValueError as err:
                    self.json({"error": str(err)}, 400)
                    return
            if self.r_params["actual_phase"] not in actual_phases and not unrestricted:
                if self.is_api():
                    if set(actual_phases) <= {0, 3}:
                        self.json({"error": "The contest is not open"}, 403)
                    else:
                        self.json({"error": "Wrong contest phase"}, 403)
                else:
                    # TODO maybe return some error code?
                    self.redirect(self.contest_url())
            else:
                return func(self, *args, **kwargs)

        return wrapped

    return decorator
