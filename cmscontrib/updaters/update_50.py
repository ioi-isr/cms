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

"""Update dumps from schema version 49 to 50.

Drop the redundant contest pointers stored on submissions, user tests,
messages and questions. The contest remains available through the
related participation.
"""


_TARGET_CLASSES = {"Submission", "UserTest", "Message", "Question"}


class Updater:
    """Remove contest references from per-participation artefacts."""

    def __init__(self, data):
        assert data["_version"] == 49
        self.objs = data

    def run(self):
        for value in self.objs.values():
            if not isinstance(value, dict):
                continue
            if value.get("_class") not in _TARGET_CLASSES:
                continue
            value.pop("contest", None)
            value.pop("contest_id", None)
        return self.objs
