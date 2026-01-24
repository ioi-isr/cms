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

This version adds the TrainingProgram table for organizing year-long
training programs with multiple training sessions, and the TrainingDay
table for organizing training days within a training program,
linking contests to training programs.
This includes TrainingDayGroup table for per-group configuration
of training days (main groups with custom timing and task ordering).
It also adds the training_day_id field to Submission to track which
training day a submission was made via. When submitting via a training day,
the submission's participation points to the managing contest's participation,
but the training_day_id records which training day interface was used.

"""


class Updater:

    def __init__(self, data):
        assert data["_version"] == 48
        self.objs = data

    def run(self):
        for k, v in self.objs.items():
            if k.startswith("_"):
                continue
            if v.get("_class") == "Submission":
                v.setdefault("training_day_id", None)

        return self.objs
