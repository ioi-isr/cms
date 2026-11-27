#!/usr/bin/env python3

"""Tests for helpers of the Italian YAML loader."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import yaml

from cms.db import Editorial, Task
from cmscontrib.importing import update_task
from cmscontrib.loaders.base_loader import LoaderValidationError
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


class TestYamlTaskEditorials(unittest.TestCase):
    def load_task(self, folders):
        config = dict(name="example", title="Task",
                      task_type="OutputOnly", output_eval="diff",
                      score_type="Sum", score_type_parameters=100)
        file_cacher = Mock()
        file_cacher.put_file_from_path.side_effect = (
            lambda path, description: Path(path).read_text())
        with TemporaryDirectory() as path:
            (Path(path) / "task.yaml").write_text(yaml.safe_dump(config))
            for folder, files in folders.items():
                (Path(path) / folder).mkdir()
                for filename, content in files.items():
                    (Path(path) / folder / filename).write_text(content)
            return YamlLoader(path, file_cacher).get_task(get_statement=False)

    def test_all_folder_names_are_imported(self):
        for folder in ("editorial", "editorials", "Editorial", "Editorials"):
            with self.subTest(folder=folder):
                task = self.load_task({folder: {
                    "slides.pptx": "slides", "פתרון.md": "solution"}})
                self.assertEqual(
                    {name: e.digest for name, e in task.editorials.items()},
                    {"slides.pptx": "slides", "פתרון.md": "solution"})
                self.assertEqual(task.attachments, {})

    def test_hidden_files_and_subfolders_are_skipped(self):
        task = self.load_task({"editorial": {".DS_Store": "x", "a.pdf": "a"}})
        self.assertEqual(set(task.editorials), {"a.pdf"})

    def test_conflicting_folders_are_rejected(self):
        with self.assertRaises(LoaderValidationError):
            self.load_task({"editorial": {"a.pdf": "a"},
                            "Editorials": {"b.pdf": "b"}})

    def test_package_without_folder_keeps_admin_uploads(self):
        existing = Task(name="example", title="Old")
        Editorial("slides.pdf", "old", task=existing)
        update_task(existing, self.load_task({}))
        self.assertEqual(set(existing.editorials), {"slides.pdf"})

    def test_package_folder_replaces_editorials(self):
        existing = Task(name="example", title="Old")
        Editorial("slides.pdf", "old", task=existing)
        update_task(existing, self.load_task({"editorial": {"new.md": "new"}}))
        self.assertEqual(
            {name: e.digest for name, e in existing.editorials.items()},
            {"new.md": "new"})

    def test_sanitize_filename(self):
        self.assertEqual(Editorial.sanitize_filename("C:\\x\\slides.pptx"),
                         "slides.pptx")
        self.assertEqual(Editorial.sanitize_filename("../../a b.md"), "a b.md")
        for bad in ("", "..", "dir/", "  ", ".notes.md", "x/.hidden"):
            self.assertIsNone(Editorial.sanitize_filename(bad))


if __name__ == "__main__":
    unittest.main()
