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

Ensure participations expose the optional training-program participation and add the
contest pointers on artefacts such as submissions, questions, messages and
user tests.
"""


def _iter_class(objs, cls_name):
    for key, value in objs.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and value.get("_class") == cls_name:
            yield key, value


def _get_obj(objs, ref):
    if ref is None:
        return None
    return objs.get(str(ref))


def _resolve_pk(objs, ref):
    if ref is None:
        return None
    target = _get_obj(objs, ref)
    if isinstance(target, dict):
        pk = target.get("id")
        if pk is not None:
            return pk
    try:
        return int(ref)
    except (TypeError, ValueError):
        return None


class Updater:
    """Populate the new fields introduced in schema version 49."""

    def __init__(self, data):
        assert data["_version"] == 48
        self.objs = data
        numeric_keys = [int(key) for key in data.keys() if key.isdigit()]
        self.next_id = max(numeric_keys, default=0) + 1

    def get_id(self) -> str:
        while str(self.next_id) in self.objs:
            self.next_id += 1
        return str(self.next_id)

    def run(self):
        participation_meta: dict[str, dict[str, object]] = {}
        contest_participations: dict[str, list[str]] = {}

        for key, participation in _iter_class(self.objs, "Participation"):
            participation["training_program_participation"] = None
            participation.pop("training_program_participation_id", None)
            participation["training_program_role"] = None
            participation.pop("training_program", None)
            participation.pop("training_program_id", None)

            contest_ref = participation.get("contest")
            participation_meta[str(key)] = {"contest": contest_ref}
            if contest_ref is not None:
                contest_participations.setdefault(str(contest_ref), []).append(str(key))

        contest_meta: dict[str, dict[str, object]] = {}
        for contest_key, contest in _iter_class(self.objs, "Contest"):
            contest_meta[str(contest_key)] = {
                "program": contest.get("training_program"),
                "role": contest.get("training_program_role"),
            }
            contest.setdefault("training_program_participations", [])

        user_objects: dict[str, dict] = {
            str(key): obj for key, obj in _iter_class(self.objs, "User")
        }
        for user in user_objects.values():
            user.setdefault("training_program_participations", [])

        tpp_by_key: dict[tuple[str, str], str] = {}

        def ensure_program_participation(program_ref: str, user_ref: str) -> str:
            key = (str(program_ref), str(user_ref))
            if key in tpp_by_key:
                return tpp_by_key[key]

            tpp_id = self.get_id()
            program_obj = _get_obj(self.objs, program_ref)
            user_obj = user_objects.get(str(user_ref))

            tpp_obj = {
                "_class": "TrainingProgramParticipation",
                "id": int(tpp_id),
                "training_program": program_ref,
                "training_program_id": _resolve_pk(self.objs, program_ref),
                "user": user_ref,
                "user_id": _resolve_pk(self.objs, user_ref),
                "starting_time": None,
                "delay_time": "0:00:00",
                "extra_time": "0:00:00",
                "participations": [],
            }
            self.objs[tpp_id] = tpp_obj
            tpp_by_key[key] = tpp_id

            if program_obj is not None:
                program_obj.setdefault("training_program_participations", [])
                if tpp_id not in program_obj["training_program_participations"]:
                    program_obj["training_program_participations"].append(tpp_id)
            if user_obj is not None:
                if tpp_id not in user_obj["training_program_participations"]:
                    user_obj["training_program_participations"].append(tpp_id)

            return tpp_id

        def update_time_fields(tpp_id: str, participation: dict) -> None:
            tpp_obj = self.objs[tpp_id]
            starting_time = participation.get("starting_time")
            if tpp_obj.get("starting_time") is None and starting_time is not None:
                tpp_obj["starting_time"] = starting_time
            for field in ("delay_time", "extra_time"):
                value = participation.get(field)
                if value not in (None, "0:00:00") and tpp_obj.get(field) in (
                    None,
                    "0:00:00",
                ):
                    tpp_obj[field] = value

        for contest_ref, info in contest_meta.items():
            program_ref = info.get("program")
            role = info.get("role")
            if program_ref is None or role not in {"regular", "home"}:
                continue

            for participation_id in contest_participations.get(str(contest_ref), []):
                participation = self.objs.get(participation_id)
                if participation is None:
                    continue
                user_ref = participation.get("user")
                if user_ref is None:
                    continue

                tpp_id = ensure_program_participation(program_ref, user_ref)
                update_time_fields(tpp_id, participation)

                participation["training_program_participation"] = str(tpp_id)
                participation["training_program_participation_id"] = int(tpp_id)
                participation["training_program_role"] = role

                tpp_obj = self.objs[tpp_id]
                participation_ref = str(participation_id)
                if participation_ref not in tpp_obj["participations"]:
                    tpp_obj["participations"].append(participation_ref)

                contest_obj = _get_obj(self.objs, contest_ref)
                if contest_obj is not None:
                    contest_obj.setdefault("training_program_participations", [])
                    if (
                        str(tpp_id)
                        not in contest_obj["training_program_participations"]
                    ):
                        contest_obj["training_program_participations"].append(
                            str(tpp_id)
                        )

        def context_info(participation_ref):
            if participation_ref is None:
                return None
            return participation_meta.get(str(participation_ref), {}).get("contest")

        for _, submission in _iter_class(self.objs, "Submission"):
            contest_ref = context_info(submission.get("participation"))
            submission.setdefault("contest_id", contest_ref)
            submission.setdefault("contest", contest_ref)

        for _, usertest in _iter_class(self.objs, "UserTest"):
            contest_ref = context_info(usertest.get("participation"))
            usertest.setdefault("contest_id", contest_ref)
            usertest.setdefault("contest", contest_ref)

        for _, question in _iter_class(self.objs, "Question"):
            contest_ref = context_info(question.get("participation"))
            question.setdefault("contest_id", contest_ref)
            question.setdefault("contest", contest_ref)

        for _, message in _iter_class(self.objs, "Message"):
            contest_ref = context_info(message.get("participation"))
            message.setdefault("contest_id", contest_ref)
            message.setdefault("contest", contest_ref)

        return self.objs
