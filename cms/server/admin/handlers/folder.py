#!/usr/bin/env python3

"""Admin handlers for Contest Folders.

Minimal CRUD and parent assignment. Contests can be assigned to a folder from
the contest page (dropdown).
"""

from cms.db import ContestFolder, Contest
from cms.server.util import exclude_internal_contests
from cmscommon.datetime import make_datetime

from .base import (
    BaseHandler,
    get_folder_breadcrumb,
    require_permission,
    visible_contests,
)


class FolderListHandler(BaseHandler):
    """Root folders page – shows top-level folders and unassigned contests."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        self.r_params = self.render_params()
        # folder_list and root_contests are already in render_params;
        # derive root_folders from folder_list to avoid an extra query.
        self.r_params["root_folders"] = [
            f for f in self.r_params["folder_list"] if f.parent_id is None
        ]
        self.render("folders.html", **self.r_params)


class FolderHandler(BaseHandler):
    """View/edit a single folder."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        self.r_params = self.render_params()
        self.r_params["folder"] = folder
        # Subfolders ordered by name
        self.r_params["subfolders"] = sorted(
            folder.children, key=lambda f: f.name
        )
        # Contests in this folder (exclude internal training-day contests)
        self.r_params["folder_contests"] = visible_contests(
            self.sql_session, folder
        )
        # Breadcrumb: use the same dict format as base.html expects
        self.r_params["breadcrumbs"] = get_folder_breadcrumb(self, folder)
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
                # Prevent folder cycles even if an invalid parent option is submitted.
                if parent.id == folder.id or parent.is_descendant_of(folder):
                    raise ValueError("Invalid parent: cannot set folder parent to itself or one of its descendants.")

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


class AddFolderHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback = self.url("folders")
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
            # Redirect to parent folder page if created inside one,
            # otherwise to root folders list.
            if parent is not None:
                self.redirect(self.url("folder", parent.id))
            else:
                self.redirect(self.url("folders"))
        else:
            self.redirect(fallback)


class RemoveFolderHandler(BaseHandler):
    """Remove a folder via DELETE request.

    Subfolders and contests are reparented to the parent (or root).
    This preserves inner structure.
    """
    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, folder_id: str):
        folder = self.safe_get_item(ContestFolder, folder_id)
        # Reparent subfolders to the parent (may be None for root)
        for child in list(folder.children):
            child.parent = folder.parent
        # Move contests under this folder to its parent (or root if None)
        parent = folder.parent
        for c in self.sql_session.query(Contest).filter(Contest.folder == folder).all():
            c.folder = parent
        # Delete the folder itself after explicit reparenting.
        self.sql_session.delete(folder)
        if not self.try_commit():
            self.set_status(500)
            self.write("Failed to remove folder")
            return
        self.write(self.url("folders"))
