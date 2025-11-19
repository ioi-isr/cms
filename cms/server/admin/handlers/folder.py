#!/usr/bin/env python3

"""Admin handlers for Contest Folders.

Minimal CRUD and parent assignment. Contests can be assigned to a folder from
the contest page (dropdown).
"""

from cms.db import ContestFolder, Contest
from cmscommon.datetime import make_datetime

from .base import BaseHandler, SimpleHandler, require_permission


class FolderListHandler(SimpleHandler("folders.html")):
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        self.r_params = self.render_params()
        self.r_params["folders"] = (
            self.sql_session.query(ContestFolder)
            .order_by(ContestFolder.name)
            .all()
        )
        self.r_params["root_contests"] = (
            self.sql_session.query(Contest)
            .filter(Contest.folder_id.is_(None))
            .order_by(Contest.name)
            .all()
        )
        self.render("folders.html", **self.r_params)


class FolderHandler(BaseHandler):
    """View/edit a single folder."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        self.r_params = self.render_params()
        self.r_params["folder"] = folder
        # Potential parents: all except self and descendants
        all_folders = self.sql_session.query(ContestFolder).order_by(ContestFolder.name).all()
        # Exclude self and descendants to prevent cycles
        self.r_params["possible_parents"] = [f for f in all_folders if f is not folder and not f.is_descendant_of(folder)]
        self.render("folder.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, folder_id: str):
        fallback = self.url("folder", folder_id)
        folder = self.safe_get_item(ContestFolder, folder_id)

        try:
            attrs = folder.get_attrs()
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")
            if not attrs["description"] or not attrs["description"].strip():
                attrs["description"] = attrs["name"]

            parent_id_str = self.get_argument("parent_id", None)
            if parent_id_str is None or parent_id_str == "" or parent_id_str == "none":
                parent = None
            else:
                parent = self.safe_get_item(ContestFolder, int(parent_id_str))

            hidden = self.get_argument("hidden", "0") == "1"

            folder.set_attrs(attrs)
            folder.parent = parent
            folder.hidden = hidden
        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback)
            return

        self.try_commit()
        self.redirect(fallback)


class AddFolderHandler(SimpleHandler("add_folder.html", permission_all=True)):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.r_params["all_folders"] = (
            self.sql_session.query(ContestFolder)
            .order_by(ContestFolder.name)
            .all()
        )
        self.render("add_folder.html", **self.r_params)
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback = self.url("folders", "add")
        operation = self.get_argument("operation", "Create")
        try:
            name = self.get_argument("name")
            description = self.get_argument("description", "")
            if not description or not description.strip():
                description = name
            parent_id_str = self.get_argument("parent_id", None)
            if parent_id_str is None or parent_id_str == "" or parent_id_str == "none":
                parent = None
            else:
                parent = self.safe_get_item(ContestFolder, int(parent_id_str))
            hidden = self.get_argument("hidden", "0") == "1"
            folder = ContestFolder(name=name, description=description, parent=parent, hidden=hidden)
            self.sql_session.add(folder)
        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback)
            return

        if self.try_commit():
            if operation == "Create and add another":
                self.redirect(fallback)
            else:
                self.redirect(self.url("folders"))
        else:
            self.redirect(fallback)


class RemoveFolderHandler(BaseHandler):
    """Confirm and remove a folder.

    On delete, move subfolders under the parent (or root) and detach contests
    (SET NULL). This preserves inner structure.
    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        self.r_params = self.render_params()
        self.r_params["folder"] = folder
        self.r_params["subfolder_count"] = len(folder.children)
        self.r_params["contest_count"] = self.sql_session.query(Contest).filter(Contest.folder == folder).count()
        self.render("folder_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        # Reparent subfolders to the parent (may be None for root)
        for child in list(folder.children):
            child.parent = folder.parent
        # Move contests under this folder to its parent (or root if None)
        parent = folder.parent
        for c in list(self.sql_session.query(Contest).filter(Contest.folder == folder).all()):
            c.folder = parent
        # Delete the folder itself; contests will be detached via FK SET NULL
        self.sql_session.delete(folder)
        self.try_commit()
        self.write("../../folders")
