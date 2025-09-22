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

"""Update dumps from schema version 47 to 48.

Populate the training program relationship on tasks and ensure
contests assigned to a training program no longer own tasks."""


def _find(obj, cls):
    for entry in obj.values():
        if isinstance(entry, dict) and entry.get("_class") == cls:
            yield entry


class Updater:
    """Expand task records with the training program foreign key."""

    def __init__(self, data):
        assert data["_version"] == 47
        self.objs = data
        for contest in _find(self.objs, "Contest"):
            contest.setdefault("tasks", [])

    def run(self):
        # First pass: ensure tasks carry the new fields.
        for task in _find(self.objs, "Task"):
            task.setdefault("training_program_id", None)
            task.setdefault("training_program", None)

        # Second pass: move tasks from contests that belong to a training program.
        for contest in _find(self.objs, "Contest"):
            tp_ref = contest.get("training_program")
            if tp_ref is None:
                continue
            task_ids = contest.get("tasks", []) or []
            moved: list[str] = []
            for task_id in task_ids:
                key = str(task_id)
                task = self.objs.get(key) or self.objs.get(task_id)
                if not isinstance(task, dict) or task.get("_class") != "Task":
                    continue
                task["contest"] = None
                task.pop("contest_id", None)
                task["training_program"] = tp_ref
                task.setdefault("training_program_id", None)
                moved.append(key)
            contest["tasks"] = []
            tp = self.objs.get(str(tp_ref)) or self.objs.get(tp_ref)
            if isinstance(tp, dict) and tp.get("_class") == "TrainingProgram":
                tp.setdefault("tasks", [])
                existing = set(tp["tasks"])
                for task_id in moved:
                    if task_id not in existing:
                        tp["tasks"].append(task_id)
                        existing.add(task_id)
        return self.objs
