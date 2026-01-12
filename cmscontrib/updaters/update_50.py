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

"""A class to update a dump created by CMS.

Used by DumpImporter and DumpUpdater.

This version adds the StudentTask table for tracking which tasks each
student has access to in the task archive. Tasks can be assigned
automatically when a student starts a training day, or manually by an admin.

"""


class Updater:

    def __init__(self, data):
        assert data["_version"] == 49
        self.objs = data

    def run(self):
        # No data migration needed - StudentTask is a new table
        # that starts empty. Existing students will have no tasks
        # assigned until they start a training day or are manually assigned.
        return self.objs
