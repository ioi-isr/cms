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
for storing details about why evaluation attempts failed, which helps admins
diagnose issues with checkers or managers.

"""


class Updater:

    def __init__(self, data):
        """
        Initialize the Updater with CMS dump objects and verify the dump version.
        
        Parameters:
            data (dict): Mapping of top-level CMS dump objects; must include a "_version" key equal to 47.
        
        Raises:
            AssertionError: If `data["_version"]` is not 47.
        """
        assert data["_version"] == 47
        self.objs = data

    def run(self):
        """
        Populate new evaluation-failure fields on SubmissionResult objects in the stored CMS dump.
        
        For each top-level object (keys starting with "_" are skipped), ensures SubmissionResult entries contain the following fields set to None: "last_evaluation_failure_text", "last_evaluation_failure_shard", "last_evaluation_failure_sandbox_paths", "last_evaluation_failure_sandbox_digests", and "last_evaluation_failure_details". This mutates the stored objects in place.
        
        Returns:
            dict: The updated objects mapping (the same dictionary provided at initialization) with the new fields added to SubmissionResult entries.
        """
        for k, v in self.objs.items():
            if k.startswith("_"):
                continue
            if v["_class"] == "SubmissionResult":
                v["last_evaluation_failure_text"] = None
                v["last_evaluation_failure_shard"] = None
                v["last_evaluation_failure_sandbox_paths"] = None
                v["last_evaluation_failure_sandbox_digests"] = None
                v["last_evaluation_failure_details"] = None

        return self.objs