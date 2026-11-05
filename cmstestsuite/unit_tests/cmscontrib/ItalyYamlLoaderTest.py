#!/usr/bin/env python3

"""Tests for helpers of the Italian YAML loader."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import yaml

from cms.db import Task
from cmscontrib.importing import update_task
from cmscontrib.loaders.italy_yaml import YamlLoader, normalize_testcase_codename


class TestNormalizeTestcaseCodename(unittest.TestCase):
    def test_numeric_codenames_are_padded(self):
        self.assertEqual(normalize_testcase_codename("0"), "000")
        self.assertEqual(normalize_testcase_codename(" 12 "), "012")
        self.assertEqual(normalize_testcase_codename("1234"), "1234")

    def test_non_numeric_codenames_are_kept(self):
        self.assertEqual(normalize_testcase_codename("7-01"), "7-01")
        self.assertEqual(normalize_testcase_codename(" p1-n3-06 "), "p1-n3-06")


class TestYamlTaskTags(unittest.TestCase):
    def load_task(self, fields):
        config = dict(name="example", title="Updated task",
                      task_type="OutputOnly", output_eval="diff",
                      score_type="Sum", score_type_parameters=100)
        config.update(fields)
        with TemporaryDirectory() as path:
            (Path(path) / "task.yaml").write_text(yaml.safe_dump(config))
            return YamlLoader(path, Mock()).get_task(get_statement=False)

    def test_tagless_update_preserves_admin_tags(self):
        imported = self.load_task({})
        self.assertEqual(imported.tags, [])
        existing = Task(name="example", title="Original task", tags=["graphs"])
        update_task(existing, imported)
        self.assertEqual(existing.title, "Updated task")
        self.assertEqual(existing.tags, ["graphs"])

    def test_explicit_empty_tags_clear_saved_tags(self):
        existing = Task(name="example", title="Original task", tags=["graphs"])
        update_task(existing, self.load_task({"tags": []}))
        self.assertEqual(existing.tags, [])

    def test_import_normalizes_tags_and_excludes_reserved_names(self):
        imported = self.load_task({
            "tags": ["  DP  ", "dp", "Batch", "CHECKER", "Communication", ""],
        })
        self.assertEqual(imported.tags, ["dp"])
        self.assertEqual(imported.all_tags, ["dp", "output only"])
        existing = Task(name="example", title="Original task", tags=["graphs"])
        update_task(existing, imported)
        self.assertEqual(existing.tags, ["dp"])

    def test_other_loaders_without_tags_preserve_admin_tags(self):
        existing = Task(name="example", title="Original task", tags=["graphs"])
        update_task(existing, Task(name="example", title="New title"))
        self.assertEqual(existing.tags, ["graphs"])

    def test_explicit_empty_tags_from_task_constructor_clear_saved_tags(self):
        existing = Task(name="example", title="Original task", tags=["graphs"])
        update_task(existing, Task(name="example", title="New title", tags=[]))
        self.assertEqual(existing.tags, [])

    def test_tags_assigned_after_construction_still_update_saved_tags(self):
        existing = Task(name="example", title="Old", tags=["graphs"])
        imported = Task(name="example", title="New")
        imported.tags = ["dp"]
        update_task(existing, imported)
        self.assertEqual(existing.tags, ["dp"])

    def test_explicit_provided_marker_overrides_constructor(self):
        for values, provided, expected in [
            ([], False, ["graphs"]),
            ([], True, []),
            (["dp"], False, ["graphs"]),
            (["dp"], True, ["dp"]),
        ]:
            with self.subTest(values=values, provided=provided):
                existing = Task(name="example", title="Old", tags=["graphs"])
                imported = Task(name="example", title="New", tags=values)
                imported._tags_provided = provided
                update_task(existing, imported)
                self.assertEqual(existing.tags, expected)


if __name__ == "__main__":
    unittest.main()
