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

"""Tests for admin theme-related changes: VALID_THEMES, Admin.preferred_theme
column, AdminThemeHandler, and route registration.
"""

import json
import unittest
from unittest.mock import MagicMock

from sqlalchemy.exc import SQLAlchemyError

from cms.db.admin import VALID_THEMES, Admin
from cms.server.admin.handlers.admin import AdminThemeHandler
from cms.server.admin.handlers import HANDLERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_handler(body: bytes, current_user=None, sql_session=None):
    """Build a minimal mock object that stands in for a BaseHandler instance.

    The mock exposes the subset of attributes that AdminThemeHandler.post()
    actually accesses, plus what tornado.web.authenticated and
    require_permission need in order to reach the real handler logic.
    """
    mock = MagicMock()

    # tornado.web.authenticated inspects current_user; a truthy value means
    # "authenticated", so require_permission(AUTHENTICATED) will proceed.
    mock.current_user = current_user if current_user is not None else MagicMock()

    # Request body supplied as raw bytes, just like a Tornado request.
    mock.request = MagicMock()
    mock.request.body = body

    # Provide a controllable SQLAlchemy session.
    if sql_session is not None:
        mock.sql_session = sql_session
    # (otherwise MagicMock auto-creates sql_session as another MagicMock)

    return mock


# ---------------------------------------------------------------------------
# VALID_THEMES
# ---------------------------------------------------------------------------

class TestValidThemes(unittest.TestCase):
    """Tests for the VALID_THEMES constant in cms.db.admin."""

    EXPECTED_THEMES = frozenset({
        "original",
        "granny-smith",
        "pink-lady",
        "arkansas-black",
        "golden",
        "mcintosh",
    })

    def test_valid_themes_is_frozenset(self):
        self.assertIsInstance(VALID_THEMES, frozenset)

    def test_valid_themes_contains_all_expected(self):
        self.assertEqual(VALID_THEMES, self.EXPECTED_THEMES)

    def test_valid_themes_has_correct_count(self):
        self.assertEqual(len(VALID_THEMES), 6)

    def test_valid_themes_contains_original(self):
        self.assertIn("original", VALID_THEMES)

    def test_valid_themes_contains_granny_smith(self):
        self.assertIn("granny-smith", VALID_THEMES)

    def test_valid_themes_contains_pink_lady(self):
        self.assertIn("pink-lady", VALID_THEMES)

    def test_valid_themes_contains_arkansas_black(self):
        self.assertIn("arkansas-black", VALID_THEMES)

    def test_valid_themes_contains_golden(self):
        self.assertIn("golden", VALID_THEMES)

    def test_valid_themes_contains_mcintosh(self):
        self.assertIn("mcintosh", VALID_THEMES)

    def test_invalid_theme_not_in_valid_themes(self):
        for invalid in ("", "dark", "light", "blue", "ORIGINAL", "Original"):
            with self.subTest(theme=invalid):
                self.assertNotIn(invalid, VALID_THEMES)

    def test_valid_themes_is_immutable(self):
        with self.assertRaises(AttributeError):
            VALID_THEMES.add("new-theme")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Admin model – preferred_theme column
# ---------------------------------------------------------------------------

class TestAdminPreferredThemeColumn(unittest.TestCase):
    """Tests for the preferred_theme column definition on the Admin model."""

    def _get_column(self):
        return Admin.__table__.columns["preferred_theme"]

    def test_preferred_theme_column_exists(self):
        self.assertIn("preferred_theme", Admin.__table__.columns)

    def test_preferred_theme_is_nullable(self):
        col = self._get_column()
        self.assertTrue(col.nullable)

    def test_preferred_theme_default_is_none(self):
        admin = Admin()
        self.assertIsNone(admin.preferred_theme)

    def test_preferred_theme_check_constraint_exists(self):
        col = self._get_column()
        constraint_names = {c.name for c in col.constraints}
        self.assertIn("admins_preferred_theme_check", constraint_names)

    def test_preferred_theme_check_constraint_covers_all_valid_themes(self):
        col = self._get_column()
        constraint = next(
            c for c in col.constraints
            if c.name == "admins_preferred_theme_check"
        )
        expr_text = str(constraint.sqltext)
        for theme in VALID_THEMES:
            self.assertIn(theme, expr_text)

    def test_preferred_theme_can_be_set_to_valid_value(self):
        admin = Admin()
        for theme in VALID_THEMES:
            with self.subTest(theme=theme):
                admin.preferred_theme = theme
                self.assertEqual(admin.preferred_theme, theme)

    def test_preferred_theme_can_be_cleared_to_none(self):
        admin = Admin()
        admin.preferred_theme = "original"
        admin.preferred_theme = None
        self.assertIsNone(admin.preferred_theme)


# ---------------------------------------------------------------------------
# AdminThemeHandler
# ---------------------------------------------------------------------------

class TestAdminThemeHandlerPost(unittest.TestCase):
    """Unit tests for AdminThemeHandler.post().

    Rather than spinning up a Tornado IOLoop, we call the handler method
    with a mock 'self' whose current_user is truthy so that
    @tornado.web.authenticated (used by require_permission) proceeds
    straight to the handler body.
    """

    # -- Helper -----------------------------------------------------------------

    def _call_post(self, body: bytes, current_user=None, sql_session=None):
        """Call AdminThemeHandler.post() with a mock handler and return it."""
        mock = _make_handler(body, current_user=current_user,
                             sql_session=sql_session)
        AdminThemeHandler.post(mock)
        return mock

    # -- Happy path -------------------------------------------------------------

    def test_valid_theme_returns_ok(self):
        body = json.dumps({"theme": "original"}).encode()
        mock = self._call_post(body)
        mock.write.assert_called_once_with({"ok": True})

    def test_each_valid_theme_is_accepted(self):
        for theme in VALID_THEMES:
            with self.subTest(theme=theme):
                body = json.dumps({"theme": theme}).encode()
                mock = self._call_post(body)
                mock.write.assert_called_once_with({"ok": True})

    def test_valid_theme_is_persisted_on_current_user(self):
        current_user = MagicMock()
        body = json.dumps({"theme": "golden"}).encode()
        self._call_post(body, current_user=current_user)
        self.assertEqual(current_user.preferred_theme, "golden")

    def test_null_theme_clears_preference(self):
        """Sending theme=None should set preferred_theme to None."""
        current_user = MagicMock()
        body = json.dumps({"theme": None}).encode()
        mock = self._call_post(body, current_user=current_user)
        self.assertIsNone(current_user.preferred_theme)
        mock.write.assert_called_once_with({"ok": True})

    def test_null_theme_commits_session(self):
        sql_session = MagicMock()
        body = json.dumps({"theme": None}).encode()
        self._call_post(body, sql_session=sql_session)
        sql_session.commit.assert_called_once()

    def test_valid_theme_commits_session(self):
        sql_session = MagicMock()
        body = json.dumps({"theme": "mcintosh"}).encode()
        self._call_post(body, sql_session=sql_session)
        sql_session.commit.assert_called_once()

    def test_successful_post_does_not_set_error_status(self):
        body = json.dumps({"theme": "original"}).encode()
        mock = self._call_post(body)
        mock.set_status.assert_not_called()

    # -- Invalid theme ----------------------------------------------------------

    def test_invalid_theme_string_returns_400(self):
        body = json.dumps({"theme": "dark"}).encode()
        mock = self._call_post(body)
        mock.set_status.assert_called_once_with(400)

    def test_invalid_theme_string_writes_error(self):
        body = json.dumps({"theme": "dark"}).encode()
        mock = self._call_post(body)
        mock.write.assert_called_once_with({"error": "Invalid theme"})

    def test_invalid_theme_does_not_commit(self):
        sql_session = MagicMock()
        body = json.dumps({"theme": "dark"}).encode()
        self._call_post(body, sql_session=sql_session)
        sql_session.commit.assert_not_called()

    def test_empty_string_theme_is_rejected(self):
        body = json.dumps({"theme": ""}).encode()
        mock = self._call_post(body)
        mock.set_status.assert_called_once_with(400)
        mock.write.assert_called_once_with({"error": "Invalid theme"})

    def test_uppercase_theme_is_rejected(self):
        body = json.dumps({"theme": "ORIGINAL"}).encode()
        mock = self._call_post(body)
        mock.set_status.assert_called_once_with(400)
        mock.write.assert_called_once_with({"error": "Invalid theme"})

    def test_numeric_theme_is_rejected(self):
        body = json.dumps({"theme": 42}).encode()
        mock = self._call_post(body)
        mock.set_status.assert_called_once_with(400)
        mock.write.assert_called_once_with({"error": "Invalid theme"})

    def test_invalid_theme_does_not_modify_current_user(self):
        current_user = MagicMock()
        body = json.dumps({"theme": "nonexistent"}).encode()
        self._call_post(body, current_user=current_user)
        # preferred_theme attribute should never have been set
        self.assertNotIn(
            "preferred_theme",
            current_user.__dict__,
            msg="preferred_theme must not be written for an invalid theme",
        )

    # -- Invalid JSON -----------------------------------------------------------

    def test_invalid_json_body_returns_400(self):
        mock = self._call_post(b"not-json")
        mock.set_status.assert_called_once_with(400)

    def test_invalid_json_body_writes_error(self):
        mock = self._call_post(b"not-json")
        mock.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_empty_body_returns_400(self):
        mock = self._call_post(b"")
        mock.set_status.assert_called_once_with(400)
        mock.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_json_array_body_returns_400(self):
        # The body must be a JSON object; an array has no .get() method.
        mock = self._call_post(b'["original"]')
        mock.set_status.assert_called_once_with(400)
        mock.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_invalid_json_does_not_commit(self):
        sql_session = MagicMock()
        self._call_post(b"{{broken", sql_session=sql_session)
        sql_session.commit.assert_not_called()

    # -- Missing 'theme' key ----------------------------------------------------

    def test_missing_theme_key_treated_as_none(self):
        """An omitted 'theme' key defaults to None, which clears the preference."""
        current_user = MagicMock()
        body = json.dumps({}).encode()
        mock = self._call_post(body, current_user=current_user)
        self.assertIsNone(current_user.preferred_theme)
        mock.write.assert_called_once_with({"ok": True})

    # -- Database failure -------------------------------------------------------

    def test_db_commit_failure_returns_500(self):
        sql_session = MagicMock()
        sql_session.commit.side_effect = SQLAlchemyError("DB down")
        body = json.dumps({"theme": "original"}).encode()
        mock = self._call_post(body, sql_session=sql_session)
        mock.set_status.assert_called_once_with(500)

    def test_db_commit_failure_writes_error(self):
        sql_session = MagicMock()
        sql_session.commit.side_effect = SQLAlchemyError("DB down")
        body = json.dumps({"theme": "original"}).encode()
        mock = self._call_post(body, sql_session=sql_session)
        mock.write.assert_called_once_with({"error": "Failed to save theme"})

    def test_db_commit_failure_rolls_back(self):
        sql_session = MagicMock()
        sql_session.commit.side_effect = SQLAlchemyError("DB down")
        body = json.dumps({"theme": "original"}).encode()
        self._call_post(body, sql_session=sql_session)
        sql_session.rollback.assert_called_once()

    def test_db_commit_failure_does_not_rollback_twice(self):
        sql_session = MagicMock()
        sql_session.commit.side_effect = SQLAlchemyError("DB down")
        body = json.dumps({"theme": "granny-smith"}).encode()
        self._call_post(body, sql_session=sql_session)
        self.assertEqual(sql_session.rollback.call_count, 1)

    # -- Regression: boundary themes --------------------------------------------

    def test_first_theme_in_set_is_accepted(self):
        """Regression: ensure set iteration order doesn't drop the first theme."""
        theme = sorted(VALID_THEMES)[0]
        body = json.dumps({"theme": theme}).encode()
        mock = self._call_post(body)
        mock.write.assert_called_once_with({"ok": True})

    def test_last_theme_in_set_is_accepted(self):
        """Regression: ensure set iteration order doesn't drop the last theme."""
        theme = sorted(VALID_THEMES)[-1]
        body = json.dumps({"theme": theme}).encode()
        mock = self._call_post(body)
        mock.write.assert_called_once_with({"ok": True})


# ---------------------------------------------------------------------------
# Route registration in HANDLERS
# ---------------------------------------------------------------------------

class TestHandlerRouteRegistration(unittest.TestCase):
    """Tests that AdminThemeHandler is wired to the correct URL pattern."""

    def _find_route(self, handler_class):
        return [(pattern, cls) for pattern, cls in HANDLERS
                if cls is handler_class]

    def test_admin_theme_handler_is_registered(self):
        routes = self._find_route(AdminThemeHandler)
        self.assertEqual(len(routes), 1,
                         msg="AdminThemeHandler should appear exactly once in HANDLERS")

    def test_admin_theme_handler_url_pattern(self):
        routes = self._find_route(AdminThemeHandler)
        pattern, _ = routes[0]
        self.assertEqual(pattern, r"/admin/theme")

    def test_admin_theme_handler_is_importable(self):
        from cms.server.admin.handlers import AdminThemeHandler as imported
        self.assertIs(imported, AdminThemeHandler)

    def test_admin_theme_route_does_not_clash_with_admin_by_id(self):
        """Ensure /admin/theme does not match the numeric admin-by-id route."""
        import re
        numeric_pattern = r"/admin/([0-9]+)"
        self.assertIsNone(re.fullmatch(numeric_pattern, "/admin/theme"))


if __name__ == "__main__":
    unittest.main()
