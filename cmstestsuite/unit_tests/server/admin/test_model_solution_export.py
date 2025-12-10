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

"""Tests for model solution export functionality."""

import unittest

from cms.server.admin.handlers.export_handlers import _expand_codename_with_language
from cms.db import Task
from cms.grading.languagemanager import LANGUAGES


class TestExpandCodenameWithLanguage(unittest.TestCase):
    """Test the _expand_codename_with_language helper function.
    
    This tests the actual production code from export_handlers.py.
    """

    def test_expand_cpp_extension(self):
        """Test that .%l is expanded to .cpp for C++ language."""
        result = _expand_codename_with_language("solution.%l", "C++17 / g++")
        self.assertEqual(result, "solution.cpp")

    def test_expand_python_extension(self):
        """Test that .%l is expanded to .py for Python language."""
        result = _expand_codename_with_language("solution.%l", "Python 3 / CPython")
        self.assertEqual(result, "solution.py")

    def test_expand_java_extension(self):
        """Test that .%l is expanded to .java for Java language."""
        result = _expand_codename_with_language("solution.%l", "Java / JDK")
        self.assertEqual(result, "solution.java")

    def test_no_expansion_without_placeholder(self):
        """Test that filenames without .%l are not modified."""
        result = _expand_codename_with_language("output_1.txt", "C++17 / g++")
        self.assertEqual(result, "output_1.txt")

    def test_no_expansion_without_language(self):
        """Test that .%l is not expanded when language is None."""
        result = _expand_codename_with_language("solution.%l", None)
        self.assertEqual(result, "solution.%l")

    def test_no_expansion_with_invalid_language(self):
        """Test that .%l is not expanded for invalid language names."""
        result = _expand_codename_with_language("solution.%l", "InvalidLanguage")
        self.assertEqual(result, "solution.%l")

    def test_expand_preserves_basename(self):
        """Test that the basename is preserved during expansion."""
        result = _expand_codename_with_language("my_solution.%l", "C++17 / g++")
        self.assertEqual(result, "my_solution.cpp")

    def test_expand_complex_basename(self):
        """Test expansion with complex basenames like legacy filenames."""
        result = _expand_codename_with_language(
            "ST3_partitions_O(2^(N-2)N).%l", "C++17 / g++")
        self.assertEqual(result, "ST3_partitions_O(2^(N-2)N).cpp")

    def test_expand_empty_string(self):
        """Test that empty string is handled correctly."""
        result = _expand_codename_with_language("", "C++17 / g++")
        self.assertEqual(result, "")

    def test_expand_only_placeholder(self):
        """Test that a filename that is just .%l is handled."""
        result = _expand_codename_with_language(".%l", "C++17 / g++")
        self.assertEqual(result, ".cpp")


class TestTaskAllowedLanguagesWithoutContest(unittest.TestCase):
    """Test that Task.get_allowed_languages works for tasks not in a contest.
    
    This tests the actual Task.get_allowed_languages method.
    """

    def test_task_allowed_languages_without_contest(self):
        """Test that get_allowed_languages returns all languages when no contest.
        
        This verifies the fix that allows model solution submission for tasks
        not attached to a contest.
        """
        # Create a task not attached to any contest
        task = Task(name="test_task", title="Test Task")
        task.contest = None
        task.allowed_languages = None

        # get_allowed_languages should return all available languages
        allowed = task.get_allowed_languages()

        # Verify we get all languages
        all_language_names = [lang.name for lang in LANGUAGES]
        self.assertEqual(allowed, all_language_names)

        # Verify common languages are present
        self.assertIn("C++17 / g++", allowed)
        self.assertIn("Python 3 / CPython", allowed)

    def test_task_with_specific_languages(self):
        """Test that task-specific language restrictions are respected."""
        task = Task(name="test_task", title="Test Task")
        task.contest = None
        task.allowed_languages = ["C++17 / g++", "Python 3 / CPython"]

        allowed = task.get_allowed_languages()

        self.assertEqual(allowed, ["C++17 / g++", "Python 3 / CPython"])


if __name__ == '__main__':
    unittest.main()
