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
    """List all folders and allow removing via POST -> remove confirmation."""

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        folder_id: str = self.get_argument("folder_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            self.redirect(self.url("folders", folder_id, "remove"))
        else:
            self.service.add_notification(
                make_datetime(), f"Invalid operation {operation}", "")
            self.redirect(self.url("folders"))


class FolderHandler(BaseHandler):
    """View/edit a single folder."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        self.r_params = self.render_params()
        self.r_params["folder"] = folder
        # Potential parents: all except self and descendants
        all_folders = self.sql_session.query(ContestFolder).order_by(ContestFolder.name).all()
        # Exclude self descendants to prevent cycles
        def is_descendant(candidate: ContestFolder) -> bool:
            cur = candidate
            while cur is not None:
                if cur is folder:
                    return True
                cur = cur.parent
            return False
        self.r_params["possible_parents"] = [f for f in all_folders if f is not folder and not is_descendant(f)]
        self.render("folder.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, folder_id: str):
        fallback = self.url("folder", folder_id)
        folder = self.safe_get_item(ContestFolder, folder_id)

        try:
            attrs = folder.get_attrs()
            self.get_string(attrs, "name")
            self.get_string(attrs, "description")

            parent_id_str = self.get_argument("parent_id", None)
            if parent_id_str is None or parent_id_str == "" or parent_id_str == "none":
                parent = None
            else:
                parent = self.safe_get_item(ContestFolder, int(parent_id_str))

            folder.set_attrs(attrs)
            folder.parent = parent
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
        try:
            name = self.get_argument("name")
            description = self.get_argument("description", name)
            parent_id_str = self.get_argument("parent_id", None)
            if parent_id_str is None or parent_id_str == "" or parent_id_str == "none":
                parent = None
            else:
                parent = self.safe_get_item(ContestFolder, int(parent_id_str))
            folder = ContestFolder(name=name, description=description, parent=parent)
            self.sql_session.add(folder)
        except Exception as error:
            self.service.add_notification(make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback)
            return

        if self.try_commit():
            self.redirect(self.url("folder", folder.id))
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
        # Persist reparenting before deleting folder
        self.sql_session.flush()
        # Delete the folder itself; contests will be detached via FK SET NULL
        self.sql_session.delete(folder)
        self.try_commit()
        self.write("../../folders")
