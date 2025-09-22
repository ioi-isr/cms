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

"""Update dumps from schema version 48 to 49.

Ensure participations expose the optional training-program owner and add the
contest pointers on artefacts such as submissions, questions, messages and
user tests.
"""


def _iter_class(objs, cls_name):
    for key, value in objs.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and value.get("_class") == cls_name:
            yield key, value


class Updater:
    """Populate the new fields introduced in schema version 49."""

    def __init__(self, data):
        assert data["_version"] == 48
        self.objs = data

    def run(self):
        participation_meta: dict[str, dict[str, object]] = {}

        for key, participation in _iter_class(self.objs, "Participation"):
            participation.setdefault("training_program_id", None)
            participation.setdefault("training_program", None)
            participation_meta[str(key)] = {
                "contest": participation.get("contest"),
                "is_program": participation.get("training_program") is not None
                or participation.get("training_program_id") is not None,
            }

        def _context_info(participation_ref):
            if participation_ref is None:
                return {"contest": None, "is_program": False}
            return participation_meta.get(str(participation_ref), {"contest": None, "is_program": False})

        for _, submission in _iter_class(self.objs, "Submission"):
            info = _context_info(submission.get("participation"))
            contest_ref = info["contest"] if info["is_program"] else None
            submission.setdefault("contest_id", contest_ref)
            submission.setdefault("contest", contest_ref)

        for _, usertest in _iter_class(self.objs, "UserTest"):
            info = _context_info(usertest.get("participation"))
            contest_ref = info["contest"] if info["is_program"] else None
            usertest.setdefault("contest_id", contest_ref)
            usertest.setdefault("contest", contest_ref)

        for _, question in _iter_class(self.objs, "Question"):
            info = _context_info(question.get("participation"))
            contest_ref = info["contest"] if info["is_program"] else None
            question.setdefault("contest_id", contest_ref)
            question.setdefault("contest", contest_ref)

        for _, message in _iter_class(self.objs, "Message"):
            info = _context_info(message.get("participation"))
            contest_ref = info["contest"] if info["is_program"] else None
            message.setdefault("contest_id", contest_ref)
            message.setdefault("contest", contest_ref)

        return self.objs
