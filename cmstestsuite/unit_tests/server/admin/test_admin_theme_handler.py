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

"""Unit tests for AdminThemeHandler.post() in cms/server/admin/handlers/admin.py."""

import json
import unittest
from unittest.mock import MagicMock, patch, call

from sqlalchemy.exc import SQLAlchemyError

from cms.db.admin import VALID_THEMES
from cms.server.admin.handlers.admin import AdminThemeHandler


def _get_raw_post():
    """Return the original post() function, bypassing require_permission/authenticated."""
    # functools.wraps sets __wrapped__ to the original wrapped function.
    fn = AdminThemeHandler.post
    # Unwrap all decorator layers to reach the raw function body.
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _make_handler(body: bytes) -> MagicMock:
    """Create a minimal mock that satisfies AdminThemeHandler.post()'s API."""
    handler = MagicMock(spec=AdminThemeHandler)
    handler.request = MagicMock()
    handler.request.body = body
    handler.current_user = MagicMock()
    handler.current_user.preferred_theme = None
    handler.current_user.id = 42
    handler.sql_session = MagicMock()
    handler.sql_session.commit.return_value = None
    handler.sql_session.rollback.return_value = None
    return handler


class TestAdminThemeHandlerSuccess(unittest.TestCase):
    """Tests for successful theme-update requests."""

    def setUp(self):
        self.post = _get_raw_post()

    def test_valid_theme_returns_ok(self):
        """A valid JSON payload with a known theme must respond with {"ok": True}."""
        handler = _make_handler(json.dumps({"theme": "original"}).encode())
        self.post(handler)
        handler.write.assert_called_once_with({"ok": True})

    def test_valid_theme_sets_preferred_theme_on_current_user(self):
        """The handler must assign the supplied theme to current_user.preferred_theme."""
        handler = _make_handler(json.dumps({"theme": "golden"}).encode())
        self.post(handler)
        self.assertEqual(handler.current_user.preferred_theme, "golden")

    def test_valid_theme_calls_commit(self):
        """A successful request must commit the session exactly once."""
        handler = _make_handler(json.dumps({"theme": "mcintosh"}).encode())
        self.post(handler)
        handler.sql_session.commit.assert_called_once()

    def test_valid_theme_does_not_set_error_status(self):
        """A successful request must not call set_status (defaults to 200)."""
        handler = _make_handler(json.dumps({"theme": "granny-smith"}).encode())
        self.post(handler)
        handler.set_status.assert_not_called()

    def test_none_theme_resets_preferred_theme(self):
        """Sending null as the theme value must store None on the admin."""
        handler = _make_handler(json.dumps({"theme": None}).encode())
        self.post(handler)
        self.assertIsNone(handler.current_user.preferred_theme)
        handler.write.assert_called_once_with({"ok": True})

    def test_missing_theme_key_treated_as_none(self):
        """If the JSON body has no 'theme' key the value defaults to None."""
        handler = _make_handler(json.dumps({"other_key": "value"}).encode())
        self.post(handler)
        # theme = data.get("theme") → None; None is not in VALID_THEMES check
        # but the guard is: "if theme is not None and theme not in VALID_THEMES"
        # so None passes through as a reset
        self.assertIsNone(handler.current_user.preferred_theme)
        handler.write.assert_called_once_with({"ok": True})

    def test_empty_json_object_treated_as_theme_none(self):
        """An empty JSON object results in theme=None (preference reset)."""
        handler = _make_handler(b"{}")
        self.post(handler)
        self.assertIsNone(handler.current_user.preferred_theme)
        handler.write.assert_called_once_with({"ok": True})

    def test_each_valid_theme_is_accepted(self):
        """Every theme in VALID_THEMES must be accepted without a 400 response."""
        for theme in sorted(VALID_THEMES):
            with self.subTest(theme=theme):
                handler = _make_handler(json.dumps({"theme": theme}).encode())
                self.post(handler)
                handler.set_status.assert_not_called()
                handler.write.assert_called_with({"ok": True})
                self.assertEqual(handler.current_user.preferred_theme, theme)


class TestAdminThemeHandlerInvalidInput(unittest.TestCase):
    """Tests for malformed or invalid request payloads."""

    def setUp(self):
        self.post = _get_raw_post()

    def test_plain_string_body_returns_400(self):
        """A body that is valid JSON but not an object must return 400."""
        # json.loads("\"hello\"") succeeds but .get("theme") raises AttributeError
        handler = _make_handler(b'"hello"')
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_malformed_json_returns_400(self):
        """A body that is not valid JSON at all must return 400."""
        handler = _make_handler(b"{not valid json}")
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_empty_body_returns_400(self):
        """An empty request body must return 400 with an 'Invalid JSON' error."""
        handler = _make_handler(b"")
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_json_array_body_returns_400(self):
        """A JSON array body causes AttributeError on .get(), returning 400."""
        handler = _make_handler(b'["original"]')
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid JSON"})

    def test_unknown_theme_name_returns_400(self):
        """An unrecognised theme name must return 400 with an 'Invalid theme' error."""
        handler = _make_handler(json.dumps({"theme": "dark-mode"}).encode())
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid theme"})

    def test_empty_string_theme_returns_400(self):
        """An empty string is not a valid theme and must return 400."""
        handler = _make_handler(json.dumps({"theme": ""}).encode())
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid theme"})

    def test_case_sensitive_theme_name_returns_400(self):
        """Theme names are case-sensitive; 'ORIGINAL' must be rejected."""
        handler = _make_handler(json.dumps({"theme": "ORIGINAL"}).encode())
        self.post(handler)
        handler.set_status.assert_called_once_with(400)
        handler.write.assert_called_once_with({"error": "Invalid theme"})

    def test_invalid_theme_does_not_commit(self):
        """An invalid theme must not trigger a database commit."""
        handler = _make_handler(json.dumps({"theme": "no-such-theme"}).encode())
        self.post(handler)
        handler.sql_session.commit.assert_not_called()

    def test_invalid_json_does_not_modify_current_user(self):
        """A parse failure must not touch current_user.preferred_theme."""
        handler = _make_handler(b"bad json")
        original_theme = handler.current_user.preferred_theme
        self.post(handler)
        # preferred_theme should not have been assigned
        self.assertEqual(handler.current_user.preferred_theme, original_theme)


class TestAdminThemeHandlerDatabaseError(unittest.TestCase):
    """Tests for SQLAlchemy errors during session commit."""

    def setUp(self):
        self.post = _get_raw_post()

    def test_sql_error_returns_500(self):
        """A SQLAlchemyError during commit must result in a 500 response."""
        handler = _make_handler(json.dumps({"theme": "original"}).encode())
        handler.sql_session.commit.side_effect = SQLAlchemyError("db failure")
        self.post(handler)
        handler.set_status.assert_called_once_with(500)
        handler.write.assert_called_once_with({"error": "Failed to save theme"})

    def test_sql_error_triggers_rollback(self):
        """A SQLAlchemyError during commit must trigger a session rollback."""
        handler = _make_handler(json.dumps({"theme": "golden"}).encode())
        handler.sql_session.commit.side_effect = SQLAlchemyError("db failure")
        self.post(handler)
        handler.sql_session.rollback.assert_called_once()

    def test_sql_error_does_not_write_ok(self):
        """On a database error the handler must not write {"ok": True}."""
        handler = _make_handler(json.dumps({"theme": "mcintosh"}).encode())
        handler.sql_session.commit.side_effect = SQLAlchemyError("db failure")
        self.post(handler)
        # write must have been called, but not with {"ok": True}
        for write_call in handler.write.call_args_list:
            self.assertNotEqual(write_call, call({"ok": True}))

    def test_sql_error_rollback_before_error_response(self):
        """Rollback must be called before the error response is written."""
        call_order = []
        handler = _make_handler(json.dumps({"theme": "granny-smith"}).encode())
        handler.sql_session.commit.side_effect = SQLAlchemyError("boom")
        handler.sql_session.rollback.side_effect = lambda: call_order.append("rollback")
        handler.write.side_effect = lambda _: call_order.append("write")
        self.post(handler)
        self.assertEqual(call_order, ["rollback", "write"])


class TestAdminThemeHandlerRouteRegistration(unittest.TestCase):
    """Smoke-test that AdminThemeHandler is exported and wired into the URL table."""

    def test_handler_importable_from_handlers_package(self):
        """AdminThemeHandler must be importable from the handlers __init__ package."""
        from cms.server.admin.handlers import AdminThemeHandler as ATH
        self.assertIs(ATH, AdminThemeHandler)

    def test_handler_route_present(self):
        """The /admin/theme route must appear in the HANDLERS list."""
        from cms.server.admin.handlers import HANDLERS
        routes = [pattern for pattern, _ in HANDLERS]
        self.assertIn(r"/admin/theme", routes)

    def test_handler_route_maps_to_correct_class(self):
        """The /admin/theme route must map to AdminThemeHandler."""
        from cms.server.admin.handlers import HANDLERS
        for pattern, handler_class in HANDLERS:
            if pattern == r"/admin/theme":
                self.assertIs(handler_class, AdminThemeHandler)
                break
        else:
            self.fail("Route /admin/theme not found in HANDLERS")


if __name__ == "__main__":
    unittest.main()
