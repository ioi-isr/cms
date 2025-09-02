#!/usr/bin/env python3

"""ImportContest should ignore folder assignment on update.

Ensures that update_contest() does not attempt to modify Contest.folder
and thus doesn't assert on missing spec, and leaves folder unchanged.
"""

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Contest, SessionGen
from cms.db.contest_folder import ContestFolder
from cmstestsuite.unit_tests.cmscontrib.ImportContestTest import fake_loader_factory
from cmscontrib.ImportContest import ContestImporter


class TestImportContestFolderIgnore(DatabaseMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Existing contest in DB with a folder
        self.existing = self.add_contest()
        self.folder1 = ContestFolder(name="F1", description="Folder 1")
        self.folder2 = ContestFolder(name="F2", description="Folder 2")
        self.session.add_all([self.folder1, self.folder2])
        self.existing.folder = self.folder1
        self.session.commit()

    def test_update_contest_ignores_folder(self):
        # Loader returns a contest object with same name/desc but a different folder
        new_contest = Contest(name=self.existing.name, description="new desc")
        # Simulate a loader that would set folder2 (ignored by importer)
        # We exercise that update_contest doesn't touch folder via spec.

        importer = ContestImporter(
            "path", True, False,
            False,  # import_tasks
            True,   # update_contest
            False,  # update_tasks
            False,  # no_statements
            False,  # delete_stale_participations
            fake_loader_factory(new_contest, contest_has_changed=True,
                                tasks=[], usernames=[]),
        )
        importer.do_import()

        with SessionGen() as s:
            c = s.query(Contest).filter(Contest.name == self.existing.name).one()
            # Folder must remain as folder1
            self.assertIsNotNone(c.folder)
            self.assertEqual(c.folder.name, "F1")
