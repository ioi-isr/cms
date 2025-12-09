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

"""Tests for translation functions.

"""

import unittest
from unittest.mock import Mock, patch

from cms.server.contest.handlers.main import translate_text


SUPPORTED_LANGUAGES = {
    'en': 'English',
    'he': 'Hebrew',
    'ru': 'Russian',
    'ar': 'Arabic'
}


class TestTranslateText(unittest.TestCase):

    def test_empty_text(self):
        result, error = translate_text("", "en", "he", SUPPORTED_LANGUAGES)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("enter text", error.lower())

    def test_invalid_source_language(self):
        result, error = translate_text("Hello", "invalid", "he", SUPPORTED_LANGUAGES)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("invalid source", error.lower())

    def test_invalid_target_language(self):
        result, error = translate_text("Hello", "en", "invalid", SUPPORTED_LANGUAGES)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("invalid target", error.lower())

    def test_same_source_and_target(self):
        result, error = translate_text("Hello", "en", "en", SUPPORTED_LANGUAGES)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("must be different", error.lower())

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_successful_translation(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "שלום"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("Hello", "en", "he", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        self.assertEqual(result, "שלום")
        mock_translator_class.assert_called_once_with(source="en", target="iw")
        mock_translator.translate.assert_called_once_with("Hello")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_translation_exception(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.side_effect = Exception("Network error")
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("Hello", "en", "he", SUPPORTED_LANGUAGES)

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("failed", error.lower())

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_all_language_pairs(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "translated text"
        mock_translator_class.return_value = mock_translator

        language_codes = list(SUPPORTED_LANGUAGES.keys())
        for source in language_codes:
            for target in language_codes:
                if source != target:
                    result, error = translate_text(
                        "test", source, target, SUPPORTED_LANGUAGES)
                    self.assertIsNone(error)
                    self.assertEqual(result, "translated text")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_long_text_translation(self, mock_translator_class):
        mock_translator = Mock()
        long_text = "This is a very long text. " * 100
        mock_translator.translate.return_value = "translated long text"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text(long_text, "en", "ru", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        self.assertEqual(result, "translated long text")
        mock_translator.translate.assert_called_once_with(long_text)

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_special_characters(self, mock_translator_class):
        mock_translator = Mock()
        special_text = "Hello! @#$%^&*() 123 <html>"
        mock_translator.translate.return_value = "translated special"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text(special_text, "en", "ar", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        self.assertEqual(result, "translated special")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_hebrew_normalization_he_to_iw(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "שלום"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("Hello", "en", "he", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        mock_translator_class.assert_called_once_with(source="en", target="iw")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_hebrew_iw_alias_accepted(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "Hello"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("שלום", "iw", "en", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        mock_translator_class.assert_called_once_with(source="iw", target="en")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_auto_detect_source_language(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "Hello"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("שלום", "auto", "en", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        mock_translator_class.assert_called_once_with(source="auto", target="en")

    def test_auto_as_target_rejected(self):
        result, error = translate_text("Hello", "en", "auto", SUPPORTED_LANGUAGES)

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("cannot use auto-detect as target", error.lower())

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_auto_source_with_same_target_allowed(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "Hello"
        mock_translator_class.return_value = mock_translator

        result, error = translate_text("Hello", "auto", "en", SUPPORTED_LANGUAGES)

        self.assertIsNone(error)
        self.assertEqual(result, "Hello")

    @patch('cms.server.contest.handlers.main.GoogleTranslator')
    def test_russian_and_arabic_no_normalization(self, mock_translator_class):
        mock_translator = Mock()
        mock_translator.translate.return_value = "translated"
        mock_translator_class.return_value = mock_translator

        translate_text("Hello", "en", "ru", SUPPORTED_LANGUAGES)
        mock_translator_class.assert_called_with(source="en", target="ru")

        mock_translator_class.reset_mock()
        translate_text("Hello", "en", "ar", SUPPORTED_LANGUAGES)
        mock_translator_class.assert_called_with(source="en", target="ar")


if __name__ == "__main__":
    unittest.main()
