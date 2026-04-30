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

"""Unit tests for cms/db/admin.py changes: VALID_THEMES and Admin.preferred_theme."""

import unittest

from cms.db.admin import VALID_THEMES


class TestValidThemes(unittest.TestCase):
    """Tests for the VALID_THEMES constant."""

    def test_valid_themes_is_frozenset(self):
        """VALID_THEMES must be a frozenset so it is immutable."""
        self.assertIsInstance(VALID_THEMES, frozenset)

    def test_valid_themes_contains_expected_values(self):
        """VALID_THEMES must contain exactly the six documented themes."""
        expected = {
            "original",
            "granny-smith",
            "pink-lady",
            "arkansas-black",
            "golden",
            "mcintosh",
        }
        self.assertEqual(VALID_THEMES, expected)

    def test_valid_themes_length(self):
        """VALID_THEMES must have exactly six entries."""
        self.assertEqual(len(VALID_THEMES), 6)

    def test_all_expected_themes_present(self):
        """Each individual expected theme name must be in VALID_THEMES."""
        expected_themes = [
            "original",
            "granny-smith",
            "pink-lady",
            "arkansas-black",
            "golden",
            "mcintosh",
        ]
        for theme in expected_themes:
            with self.subTest(theme=theme):
                self.assertIn(theme, VALID_THEMES)

    def test_unknown_theme_not_in_valid_themes(self):
        """A random string that is not a known theme must not appear in VALID_THEMES."""
        self.assertNotIn("dark", VALID_THEMES)
        self.assertNotIn("light", VALID_THEMES)
        self.assertNotIn("", VALID_THEMES)
        self.assertNotIn("ORIGINAL", VALID_THEMES)  # case-sensitive

    def test_valid_themes_is_immutable(self):
        """Attempting to modify VALID_THEMES must raise an AttributeError."""
        with self.assertRaises(AttributeError):
            VALID_THEMES.add("new-theme")

    def test_valid_themes_membership_check_is_o1(self):
        """Membership test on a frozenset must succeed (functional smoke-test)."""
        # frozenset.__contains__ is O(1); just verify it returns the right bool
        self.assertTrue("original" in VALID_THEMES)
        self.assertFalse("nonexistent" in VALID_THEMES)


class TestAdminPreferredThemeAttribute(unittest.TestCase):
    """Tests for the Admin.preferred_theme column definition."""

    def test_preferred_theme_column_exists(self):
        """Admin model must expose a preferred_theme attribute."""
        from cms.db.admin import Admin
        self.assertTrue(hasattr(Admin, "preferred_theme"))

    def test_preferred_theme_column_is_nullable(self):
        """The preferred_theme column must be nullable (None means default)."""
        from sqlalchemy import inspect as sa_inspect
        from cms.db.admin import Admin
        mapper = sa_inspect(Admin)
        col = mapper.columns["preferred_theme"]
        self.assertTrue(col.nullable)

    def test_preferred_theme_column_has_check_constraint(self):
        """The preferred_theme column must carry the named CHECK constraint."""
        from sqlalchemy import inspect as sa_inspect
        from cms.db.admin import Admin
        mapper = sa_inspect(Admin)
        col = mapper.columns["preferred_theme"]
        constraint_names = {c.name for c in col.constraints}
        self.assertIn("admins_preferred_theme_check", constraint_names)

    def test_preferred_theme_check_constraint_covers_all_themes(self):
        """The CHECK constraint SQL expression must mention every valid theme."""
        from sqlalchemy import inspect as sa_inspect
        from cms.db.admin import Admin
        mapper = sa_inspect(Admin)
        col = mapper.columns["preferred_theme"]
        for constraint in col.constraints:
            if constraint.name == "admins_preferred_theme_check":
                sqltext = str(constraint.sqltext)
                for theme in VALID_THEMES:
                    with self.subTest(theme=theme):
                        self.assertIn(theme, sqltext)
                break
        else:
            self.fail("admins_preferred_theme_check constraint not found")


if __name__ == "__main__":
    unittest.main()