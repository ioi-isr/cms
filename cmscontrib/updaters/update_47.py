#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright Ac 2025 CMS developers <dev@cms>
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

"""Update dumps from schema version 46 to 47.

Ensures contest entities expose the newly introduced training-program
fields while leaving every contest detached from any program by default.
"""


class Updater:
    """Populate the fields required by schema version 47."""

    def __init__(self, data):
        assert data["_version"] == 46
        self.objs = data

    def run(self):
        for key, obj in self.objs.items():
            if key.startswith("_"):
                continue
            if obj.get("_class") != "Contest":
                continue

            # Contests gained two optional fields describing training-program
            # membership. Pre-existing contests should default to having no
            # training program, which we represent with explicit ``None``
            # values. Using ``setdefault`` keeps any data already present when
            # updating a dump produced by a newer version.
            obj.setdefault("training_program_id", None)
            obj.setdefault("training_program_role", None)

        return self.objs
