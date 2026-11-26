"""Update CMS dumps created before coach materials were added."""


class Updater:
    def __init__(self, data):
        assert data["_version"] == 51
        self.data = data

    def run(self):
        for key, obj in self.data.items():
            if not key.startswith("_") and obj.get("_class") == "Task":
                obj.setdefault("coach_materials", [])
        return self.data
