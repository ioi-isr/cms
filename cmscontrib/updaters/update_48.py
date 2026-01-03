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

This version adds the last_evaluation_failure_* fields to SubmissionResult
to store details about why evaluation attempts failed, helping admins
diagnose issues with checkers or managers.

It also adds the ModelSolutionMeta table for storing metadata about
model solutions. Model solutions are implemented as regular Submissions
owned by a special hidden system Participation, requiring only a small
metadata table rather than parallel infrastructure.

Additionally, it adds the generators table for storing test generators
that can generate testcases programmatically.

It also adds the SubtaskValidator and SubtaskValidationResult tables
for storing subtask validators and their validation results. These allow
admins to validate that testcases meet specific subtask requirements.

Finally, it adds the source_digest and source_extension fields to Statement
objects, allowing storage of source files (DOC/DOCX/TEX) alongside PDF statements.

"""


class Updater:

    def __init__(self, data):
        assert data["_version"] == 47
        self.objs = data

    def run(self):
        for k, v in self.objs.items():
            if k.startswith("_"):
                continue
            if v["_class"] == "SubmissionResult":
                v["last_evaluation_failure_text"] = None
                v["last_evaluation_failure_shard"] = None
                v["last_evaluation_failure_sandbox_paths"] = None
                v["last_evaluation_failure_sandbox_digests"] = None
                v["last_evaluation_failure_details"] = None
            elif v["_class"] == "Statement":
                v["source_digest"] = None
                v["source_extension"] = None

        return self.objs
