#!/usr/bin/env python3

"""Functional flow test for contest folders via AWS.

Starts AdminWebServer and exercises:
- creating folders (root and nested)
- assigning a contest to a folder via the contest edit form
- deleting a folder and verifying subtree preservation and contest move
"""

import re
import unittest

from cmstestsuite.programstarter import ProgramStarter
from cmstestsuite.functionaltestframework import FunctionalTestFramework
from cmstestsuite import CONFIG
from cms.db import SessionGen, Contest
from cms.db.contest_folder import ContestFolder


class TestFolderFunctional(unittest.TestCase):
    def setUp(self):
        # Resolve cms.toml; if unavailable, skip functional test
        from cmstestsuite import CONFIG
        import os
        self.framework = FunctionalTestFramework()
        config_path = CONFIG.get("CONFIG_PATH")
        if not config_path:
            # Try env override first
            env_cfg = os.environ.get("CMS_CONFIG")
            if env_cfg and os.path.exists(env_cfg):
                CONFIG["CONFIG_PATH"] = env_cfg
            else:
                # Common installation path; skip if missing
                default_cfg = "/usr/local/etc/cms.toml"
                if os.path.exists(default_cfg):
                    CONFIG["CONFIG_PATH"] = default_cfg
                else:
                    self.skipTest("cms.toml not available; skipping functional folder test")
        # Ensure TEST_DIR is set so ProgramStarter uses local scripts/
        if "TEST_DIR" not in CONFIG or CONFIG.get("TEST_DIR") is None:
            CONFIG["TEST_DIR"] = os.getcwd()

        # Start required services
        self.ps = ProgramStarter(None)
        self.ps.start("LogService")
        self.ps.start("ResourceService")
        self.ps.start("Checker")
        self.ps.start("ScoringService")
        self.ps.start("AdminWebServer")
        self.ps.start("RankingWebServer", shard=None)
        self.ps.wait()

        self.framework.initialize_aws()

        # Create a minimal contest
        self.contest_id = self.framework.add_contest(
            name="folders_func_test", description="Folders functional test"
        )

    def tearDown(self):
        self.ps.stop_all()

    def test_folder_flow(self):
        # Create A and B(A)
        a_id = self.framework.add_folder("A", "Folder A")
        b_id = self.framework.add_folder("B", "Folder B", parent_id=a_id)

        # Assign contest to B
        self.framework.set_contest_folder(self.contest_id, b_id)

        with SessionGen() as s:
            c = s.query(Contest).get(self.contest_id)
            A = s.query(ContestFolder).get(a_id)
            B = s.query(ContestFolder).get(b_id)
            self.assertIsNotNone(A)
            self.assertIsNotNone(B)
            self.assertIs(B.parent, A)
            self.assertIs(c.folder, B)

        # Delete A, B should become root and contest should still be under B
        self.framework.delete_folder(a_id)
        with SessionGen() as s:
            c = s.query(Contest).get(self.contest_id)
            B = s.query(ContestFolder).get(b_id)
            self.assertIsNotNone(B)
            self.assertIsNone(B.parent)
            self.assertIs(c.folder, B)

        # Delete B, contest should move to root
        self.framework.delete_folder(b_id)
        with SessionGen() as s:
            c = s.query(Contest).get(self.contest_id)
            self.assertIsNone(c.folder)


if __name__ == "__main__":
    unittest.main()
