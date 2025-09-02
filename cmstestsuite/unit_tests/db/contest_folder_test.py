#!/usr/bin/env python3

"""Tests for ContestFolder and folder-related behaviors.

Focus on:
- assigning contests to folders via set_attrs on folder_id
- deleting a folder preserves subtree by reparenting, and moves contests
  under the deleted folder's parent (simulating admin handler logic)
"""

import unittest

from cmstestsuite.unit_tests.databasemixin import DatabaseMixin

from cms.db import Contest
from cms.db.contest_folder import ContestFolder


class TestContestFolder(DatabaseMixin, unittest.TestCase):
    def test_assign_contest_folder_with_attrs(self):
        # Create a contest and a folder, assign via set_attrs on folder_id
        contest = self.add_contest()
        folder = ContestFolder(name="f1", description="Folder 1")
        self.session.add(folder)
        self.session.commit()

        # Assign by relationship
        contest.folder = folder
        self.session.commit()

        self.assertEqual(contest.folder_id, folder.id)
        self.assertIs(contest.folder, folder)

    def test_delete_folder_reparents_children_and_moves_contests(self):
        # Build a small tree: root -> A -> B
        root_parent = None
        A = ContestFolder(name="A", description="A", parent=root_parent)
        B = ContestFolder(name="B", description="B", parent=A)
        self.session.add_all([A, B])
        c_root = self.add_contest()  # at root
        c_A = self.add_contest()     # will move from A to root
        c_B = self.add_contest()     # remains under B (which moves to root)
        c_A.folder = A
        c_B.folder = B
        self.session.commit()

        # Simulate admin delete logic for folder A
        for child in list(A.children):
            child.parent = A.parent  # root
        parent = A.parent  # None
        for c in list(self.session.query(Contest).filter(Contest.folder == A).all()):
            c.folder = parent
        self.session.flush()
        self.session.delete(A)
        self.session.commit()

        # B should now be at root
        self.assertIsNone(B.parent)
        # c_A moved to root, c_B still under B, c_root unchanged
        self.assertIsNone(c_A.folder)
        self.assertIs(c_B.folder, B)
        self.assertIsNone(c_root.folder)
