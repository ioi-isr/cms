"""Manage editorial and written solution files in the Admin Web Server."""

import logging
from os.path import splitext

import tornado.web

from cms.db import Task, TaskCoachMaterial
from cmscommon.datetime import make_datetime
from cmscommon.mimetypes import get_type_for_file_name
from .base import BaseHandler, FileHandler, require_permission


logger = logging.getLogger(__name__)

MATERIAL_SECTIONS = {
    "editorial": ("Editorials", (".pptx", ".ppt", ".pdf")),
    "written_solution": ("Written solutions", (".md", ".pdf")),
}


class UploadCoachMaterialsHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, task_id, kind):
        task = self.safe_get_item(Task, task_id)
        if kind not in MATERIAL_SECTIONS:
            raise tornado.web.HTTPError(404)

        page = self.url("task", task_id) + "#coach-materials"
        extensions = MATERIAL_SECTIONS[kind][1]
        uploads = self.request.files.get("material", [])
        filenames = {material.filename for material in task.coach_materials
                     if material.kind == kind}

        error = None
        if not uploads:
            error = "Choose at least one file."
        for upload in uploads:
            filename = upload.filename
            if (not 0 < len(filename) <= 255 or "/" in filename or "\\" in filename
                    or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
                error = ("Use a filename of at most 255 characters without "
                         "path separators or control characters.")
            elif splitext(filename)[1].lower() not in extensions:
                error = "Supported formats are " + ", ".join(extensions) + "."
            elif not upload.body:
                error = f"{filename} is empty."
            elif filename in filenames:
                error = f"{filename} already exists in this section."
            if error:
                break
            filenames.add(filename)
        if error:
            self.service.add_notification(make_datetime(), "Upload failed", error)
            self.redirect(page)
            return

        task_name = task.name
        self.sql_session.close()

        stored = []
        try:
            for upload in uploads:
                digest = self.service.file_cacher.put_file_content(
                    upload.body, f"Coach material for task {task_name}")
                stored.append((upload.filename, digest))
        except Exception:
            logger.exception("Failed to store coach materials for task %s", task_id)
            self.service.add_notification(
                make_datetime(), "Upload failed", "The files could not be stored.")
            self.redirect(page)
            return

        task = self.safe_get_item(Task, task_id)
        self.sql_session.add_all(
            TaskCoachMaterial(task=task, kind=kind, filename=filename, digest=digest)
            for filename, digest in stored)
        self.try_commit()
        self.redirect(page)


class CoachMaterialHandler(FileHandler):
    def _get_material(self, task_id, material_id):
        material = self.safe_get_item(TaskCoachMaterial, material_id)
        if material.task_id != int(task_id):
            raise tornado.web.HTTPError(404)
        return material

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, task_id, material_id):
        material = self._get_material(task_id, material_id)
        filename, digest = material.filename, material.digest
        self.sql_session.close()
        mimetype = get_type_for_file_name(filename.lower())
        self.fetch(digest, mimetype or "application/octet-stream", filename)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, task_id, material_id):
        self.sql_session.delete(self._get_material(task_id, material_id))
        if not self.try_commit():
            self.set_status(500)
