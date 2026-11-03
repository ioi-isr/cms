"""Task labels follow grading settings and survive database round trips."""

import unittest

import pytest

from cms.db import Dataset, Manager, Task
from cmscontrib.updaters.update_51 import Updater
from cmstestsuite.unit_tests.databasemixin import DatabaseMixin


def dataset(task, task_type, parameters):
    return Dataset(
        task=task, description=task_type, task_type=task_type,
        task_type_parameters=parameters, score_type="Sum",
        score_type_parameters=100)


@pytest.mark.parametrize("task_type, parameters, expected", [
    ("Batch", ["alone", ["", ""], "diff"], ["batch"]),
    ("Batch", ["grader", ["in", "out"], "comparator"],
     ["batch", "checker"]),
    ("Batch", ["alone", ["", ""], "realprecision", 6], ["batch"]),
    ("Communication", [1, "stub", "std_io"], ["communication"]),
    ("OutputOnly", ["diff"], ["output only"]),
    ("OutputOnly", ["comparator"], ["checker", "output only"]),
    ("OutputOnly", ["realprecision", 6], ["output only"]),
    ("BatchAndOutput", ["alone", ["", ""], "comparator", 6, "000"],
     ["batch", "checker", "output only"]),
    ("TwoSteps", ["comparator"], ["checker"]),
])
def test_automatic_tags(task_type, parameters, expected):
    task = Task(name="example", title="Example")
    task.active_dataset = dataset(task, task_type, parameters)
    assert task.all_tags == expected
    assert task.tags == []


def test_tags_follow_active_dataset_and_comparator_changes():
    task = Task(name="example", title="Example", tags=["graphs"])
    batch = dataset(task, "Batch", ["alone", ["", ""], "diff"])
    output = dataset(task, "OutputOnly", ["comparator"])
    task.active_dataset = batch
    # An unused checker file alone does not make this a comparator task.
    Manager(dataset=batch, filename="checker.cpp", digest="a" * 40)
    assert task.all_tags == ["batch", "graphs"]
    task.active_dataset = output
    assert task.all_tags == ["checker", "graphs", "output only"]
    output.task_type_parameters = ["diff"]
    assert task.all_tags == ["graphs", "output only"]
    task.active_dataset = None
    assert task.all_tags == ["graphs"]


def test_custom_tags_cannot_override_automatic_classification():
    task = Task(name="example", title="Example",
                tags=["batch", "checker", "graphs", "graphs"])
    task.active_dataset = dataset(task, "OutputOnly", ["diff"])
    assert task.all_tags == ["graphs", "output only"]


def test_upgrade_old_dump_adds_custom_tags():
    old_dump = {"_version": 50, "1": {"_class": "Task"},
                "2": {"_class": "Task", "tags": ["graphs"]},
                "3": {"_class": "User"}}
    upgraded = Updater(old_dump).run()
    assert upgraded["1"]["tags"] == []
    assert upgraded["2"]["tags"] == ["graphs"]
    assert "tags" not in upgraded["3"]


class TestTaskTagPersistence(DatabaseMixin, unittest.TestCase):
    def tearDown(self):
        self.delete_data()
        super().tearDown()

    def test_custom_and_visibility_tags_are_independent(self):
        task = self.add_task(tags=["graphs", "שלום"],
                             visible_to_tags=["advanced"])
        self.session.commit()
        self.session.expire_all()
        self.assertEqual(task.all_tags, ["graphs", "שלום"])
        task.tags = ["dp"]
        self.session.commit()
        self.session.expire_all()
        self.assertEqual(task.tags, ["dp"])
        self.assertEqual(task.visible_to_tags, ["advanced"])
