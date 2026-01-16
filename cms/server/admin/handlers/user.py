#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2016 Myungwoo Chun <mc.tamaki@gmail.com>
# Copyright © 2017 Valentin Rosca <rosca.valentin2012@gmail.com>
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

"""User-related handlers for AWS.

"""

import csv
import io
import re
from datetime import date

from sqlalchemy import and_, exists
from cms.db import Contest, Participation, Submission, Team, User
from cms.server.picture_utils import process_picture, PictureValidationError
from cms.server.util import exclude_internal_contests
from cmscommon.crypto import (parse_authentication,
                              hash_password, validate_password_strength)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, SimpleHandler, require_permission


class UserHandler(BaseHandler):
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, user_id):
        user = self.safe_get_item(User, user_id)

        self.r_params = self.render_params()
        self.r_params["user"] = user
        self.r_params["participations"] = \
            self.sql_session.query(Participation)\
                .filter(Participation.user == user)\
                .all()
        self.r_params["unassigned_contests"] = exclude_internal_contests(
            self.sql_session.query(Contest).filter(
                ~exists().where(
                    and_(
                        Participation.contest_id == Contest.id,
                        Participation.user == user
                    )
                )
            )
        ).all()
        self.render("user.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        try:
            attrs = user.get_attrs()

            self.get_string(attrs, "first_name")
            self.get_string(attrs, "last_name")
            self.get_string(attrs, "username", empty=None)
            self.get_string(attrs, "email", empty=None)

            # Validate password strength unless explicitly bypassed
            # (e.g., for imports or tests)
            password = self.get_argument("password", "")
            allow_weak = self.get_argument("allow_weak_password", None)
            if len(password) > 0 and allow_weak is None:
                user_inputs = []
                if attrs.get("username"):
                    user_inputs.append(attrs["username"])
                if attrs.get("email"):
                    user_inputs.append(attrs["email"])
                validate_password_strength(password, user_inputs)

            self.get_password(attrs, user.password, False)
            self.get_string_list(attrs, "preferred_languages")
            self.get_string(attrs, "timezone", empty=None)

            # Handle date of birth
            date_of_birth_str = self.get_argument("date_of_birth", "")
            if date_of_birth_str:
                try:
                    attrs["date_of_birth"] = date.fromisoformat(date_of_birth_str)
                except ValueError:
                    raise ValueError("Invalid date of birth format")
            else:
                attrs["date_of_birth"] = None

            # Handle picture upload and removal
            # If a new picture is uploaded, use it (ignore remove checkbox)
            # Otherwise, if remove checkbox is checked, remove the picture
            old_picture_digest = user.picture
            new_picture_uploaded = False

            if "picture" in self.request.files:
                picture_file = self.request.files["picture"][0]
                if picture_file["body"]:
                    try:
                        processed_data, _ = process_picture(
                            picture_file["body"],
                            picture_file["content_type"]
                        )
                        attrs["picture"] = self.service.file_cacher.put_file_content(
                            processed_data,
                            "Profile picture for %s" % attrs.get("username", "user")
                        )
                        new_picture_uploaded = True
                    except PictureValidationError as e:
                        raise ValueError(e.message)

            # Only process remove checkbox if no new picture was uploaded
            if not new_picture_uploaded:
                remove_picture = self.get_argument("remove_picture", None)
                if remove_picture == "1":
                    attrs["picture"] = None

            # Delete old picture from file cacher if it's being replaced or removed
            if old_picture_digest is not None and attrs.get("picture") != old_picture_digest:
                try:
                    self.service.file_cacher.delete(old_picture_digest)
                except Exception:
                    pass

            assert attrs.get("username") is not None, \
                "No username specified."
            assert not attrs.get("username").startswith("__"), \
                "Username cannot start with '__' (reserved for system users)."

            # Update the user.
            user.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Update the user on RWS.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class UserListHandler(SimpleHandler("users.html")):
    """Get returns the list of all users, post perform operations on
    a specific user (removing them from CMS).

    """

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        user_id: str = self.get_argument("user_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("users", user_id, "remove")
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, "")
            self.redirect(self.url("contests"))


class ExportUsersHandler(BaseHandler):
    """Export all users to a CSV file.

    """

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        users = self.sql_session.query(User).order_by(User.username).all()

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "First name",
            "Last name",
            "Username",
            "Password",
            "Plain text / Hash",
            "E-mail",
            "Date of birth",
            "Timezone",
            "Preferred languages"
        ])

        for user in users:
            try:
                method, payload = parse_authentication(user.password)
                password_type = "Plain text" if method == "plaintext" else "Hash"
                password_value = payload
            except (ValueError, AttributeError):
                password_type = "Unknown"
                password_value = user.password

            preferred_languages = "; ".join(user.preferred_languages) if user.preferred_languages else ""

            date_of_birth_str = user.date_of_birth.isoformat() if user.date_of_birth else ""

            writer.writerow([
                user.first_name or "",
                user.last_name or "",
                user.username or "",
                password_value or "",
                password_type,
                user.email or "",
                date_of_birth_str,
                user.timezone or "",
                preferred_languages
            ])

        self.set_header("Content-Type", "text/csv")
        self.set_header("Content-Disposition", "attachment; filename=users.csv")
        self.write(output.getvalue())


class ImportUsersHandler(BaseHandler):
    """Import users from a CSV file.

    GET shows the upload form.
    POST processes the CSV and shows results with conflicts.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        self.r_params = self.render_params()
        self.render("import_users.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("users", "import")

        if "csv_file" not in self.request.files:
            self.service.add_notification(
                make_datetime(), "No file uploaded", "Please select a CSV file to upload.")
            self.redirect(fallback_page)
            return

        csv_file = self.request.files["csv_file"][0]
        filename = csv_file["filename"]

        if not filename.lower().endswith('.csv'):
            self.service.add_notification(
                make_datetime(), "Invalid file type", "Only CSV files are accepted.")
            self.redirect(fallback_page)
            return

        try:
            content = csv_file["body"].decode("utf-8")
        except UnicodeDecodeError:
            self.service.add_notification(
                make_datetime(), "Invalid file encoding", "CSV file must be UTF-8 encoded.")
            self.redirect(fallback_page)
            return

        reader = csv.DictReader(io.StringIO(content))

        expected_headers = {
            "First name", "Last name", "Username", "Password",
            "Plain text / Hash", "E-mail", "Date of birth", "Timezone", "Preferred languages"
        }

        if not reader.fieldnames or not expected_headers.issubset(set(reader.fieldnames)):
            self.service.add_notification(
                make_datetime(),
                "Invalid CSV format",
                f"CSV must have headers: {', '.join(expected_headers)}")
            self.redirect(fallback_page)
            return

        new_users = []
        failed_users = []
        existing_users = []
        row_num = 1

        username_pattern = re.compile(r'^[A-Za-z0-9_-]+$')

        for row in reader:
            row_num += 1
            errors = []

            username = row.get("Username", "").strip()
            first_name = row.get("First name", "").strip()
            last_name = row.get("Last name", "").strip()
            password = row.get("Password", "").strip()
            password_type = row.get("Plain text / Hash", "").strip()
            email = row.get("E-mail", "").strip()
            date_of_birth_str = row.get("Date of birth", "").strip()
            timezone = row.get("Timezone", "").strip()
            preferred_languages_str = row.get("Preferred languages", "").strip()

            if not username:
                errors.append("Username is required")
            elif not username_pattern.match(username):
                errors.append("Username must contain only letters, numbers, hyphens, and underscores")

            if not first_name:
                errors.append("First name is required")

            if not last_name:
                errors.append("Last name is required")

            if not password:
                errors.append("Password is required")

            if password_type and password_type.lower() not in ["plain text", "hash"]:
                errors.append(f"Invalid password type '{password_type}'. Must be 'Plain text' or 'Hash'")

            # Parse date of birth (optional, but validate format if provided)
            date_of_birth = None
            if date_of_birth_str:
                try:
                    date_of_birth = date.fromisoformat(date_of_birth_str)
                except ValueError:
                    errors.append(f"Invalid date of birth format '{date_of_birth_str}'. Use YYYY-MM-DD format.")

            if errors:
                failed_users.append({
                    "row": row_num,
                    "username": username,
                    "first_name": first_name,
                    "last_name": last_name,
                    "errors": errors
                })
                continue

            preferred_languages = [lang.strip() for lang in re.split(r"[;,]", preferred_languages_str) if lang.strip()]

            if password_type.lower() == "plain text":
                password_value = hash_password(password, "bcrypt")
            else:
                if password.startswith("bcrypt:"):
                    password_value = password
                else:
                    password_value = f"bcrypt:{password}"

            existing_user = self.sql_session.query(User).filter(User.username == username).first()

            user_data = {
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "password": password_value,
                "email": email if email else None,
                "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
                "timezone": timezone if timezone else None,
                "preferred_languages": preferred_languages,
                "row": row_num
            }

            if existing_user:
                user_data["existing_id"] = existing_user.id
                user_data["existing_first_name"] = existing_user.first_name
                user_data["existing_last_name"] = existing_user.last_name
                user_data["existing_email"] = existing_user.email
                user_data["existing_timezone"] = existing_user.timezone
                existing_users.append(user_data)
            else:
                new_users.append(user_data)

        self.r_params = self.render_params()
        self.r_params["new_users"] = new_users
        self.r_params["failed_users"] = failed_users
        self.r_params["existing_users"] = existing_users
        self.render("import_users_confirm.html", **self.r_params)


class ImportUsersConfirmHandler(BaseHandler):
    """Confirm and execute the user import.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        import json

        new_users_json = self.get_argument("new_users", "[]")
        existing_users_json = self.get_argument("existing_users", "[]")

        try:
            new_users = json.loads(new_users_json)
            existing_users = json.loads(existing_users_json)
        except json.JSONDecodeError:
            self.service.add_notification(
                make_datetime(), "Invalid data", "Failed to parse user data.")
            self.redirect(self.url("users"))
            return

        created_count = 0
        updated_count = 0
        errors = []

        for user_data in new_users:
            try:
                date_of_birth = None
                if user_data.get("date_of_birth"):
                    date_of_birth = date.fromisoformat(user_data["date_of_birth"])
                user = User(
                    username=user_data["username"],
                    first_name=user_data["first_name"],
                    last_name=user_data["last_name"],
                    password=user_data["password"],
                    email=user_data.get("email"),
                    date_of_birth=date_of_birth,
                    timezone=user_data.get("timezone"),
                    preferred_languages=user_data.get("preferred_languages", [])
                )
                self.sql_session.add(user)
                created_count += 1
            except Exception as error:
                errors.append(f"Failed to create user {user_data['username']}: {str(error)}")

        update_user_ids = self.get_arguments("update_user")

        for user_data in existing_users:
            user_id = str(user_data["existing_id"])
            if user_id in update_user_ids:
                try:
                    user = self.sql_session.query(User).filter(User.id == user_data["existing_id"]).first()
                    if user:
                        user.first_name = user_data["first_name"]
                        user.last_name = user_data["last_name"]
                        user.password = user_data["password"]
                        user.email = user_data.get("email")
                        if user_data.get("date_of_birth"):
                            user.date_of_birth = date.fromisoformat(user_data["date_of_birth"])
                        else:
                            user.date_of_birth = None
                        user.timezone = user_data.get("timezone")
                        user.preferred_languages = user_data.get("preferred_languages", [])
                        updated_count += 1
                except Exception as error:
                    errors.append(f"Failed to update user {user_data['username']}: {str(error)}")

        if self.try_commit():
            self.service.proxy_service.reinitialize()
            message = f"Successfully created {created_count} user(s) and updated {updated_count} user(s)."
            if errors:
                message += f" Errors: {'; '.join(errors)}"
            self.service.add_notification(make_datetime(), "Import completed", message)
        else:
            self.service.add_notification(
                make_datetime(), "Import failed", "Failed to commit changes to database.")

        self.redirect(self.url("users"))


class TeamListHandler(SimpleHandler("teams.html")):
    """Get returns the list of all teams, post perform operations on
    a specific team (removing them from CMS).

    """

    REMOVE = "Remove"

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        team_id: str = self.get_argument("team_id")
        operation: str = self.get_argument("operation")

        if operation == self.REMOVE:
            asking_page = self.url("teams", team_id, "remove")
            self.redirect(asking_page)
        else:
            self.service.add_notification(
                make_datetime(), "Invalid operation %s" % operation, ""
            )
            self.redirect(self.url("contests"))


class RemoveUserHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually removes
    the user from CMS.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, user_id):
        user = self.safe_get_item(User, user_id)
        submission_query = self.sql_session.query(Submission)\
            .join(Submission.participation)\
            .filter(Participation.user == user)
        participation_query = self.sql_session.query(Participation)\
            .filter(Participation.user == user)

        self.render_params_for_remove_confirmation(submission_query)
        self.r_params["user"] = user
        self.r_params["participation_count"] = participation_query.count()
        self.render("user_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, user_id):
        user = self.safe_get_item(User, user_id)

        self.sql_session.delete(user)
        if self.try_commit():
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another user)
        self.write("../../users")


class RemoveTeamHandler(BaseHandler):
    """Get returns a page asking for confirmation, delete actually removes
    the team from CMS.

    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self, team_id):
        team = self.safe_get_item(Team, team_id)
        participation_query = self.sql_session.query(Participation).filter(
            Participation.team == team
        )

        self.r_params = self.render_params()
        self.r_params["team"] = team
        self.r_params["participation_count"] = participation_query.count()
        self.render("team_remove.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_ALL)
    def delete(self, team_id):
        team = self.safe_get_item(Team, team_id)
        try:

            # Remove associations
            self.sql_session.query(Participation).filter(
                Participation.team_id == team_id
            ).update({Participation.team_id: None})

            # delete the team
            self.sql_session.delete(team)
            if self.try_commit():
                self.service.proxy_service.reinitialize()
        except Exception as fallback_error:
            self.service.add_notification(
                make_datetime(), "Error removing team", repr(fallback_error)
            )

        # Maybe they'll want to do this again (for another team)
        self.write("../../teams")


class TeamHandler(BaseHandler):
    """Manage a single team.

    If referred by GET, this handler will return a pre-filled HTML form.
    If referred by POST, this handler will sync the team data with the form's.
    """
    def get(self, team_id):
        team = self.safe_get_item(Team, team_id)

        self.r_params = self.render_params()
        self.r_params["team"] = team
        self.render("team.html", **self.r_params)

    def post(self, team_id):
        fallback_page = self.url("team", team_id)

        team = self.safe_get_item(Team, team_id)

        try:
            attrs = team.get_attrs()

            self.get_string(attrs, "code")
            self.get_string(attrs, "name")

            assert attrs.get("code") is not None, \
                "No team code specified."

            # Update the team.
            team.set_attrs(attrs)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Update the team on RWS.
            self.service.proxy_service.reinitialize()
        self.redirect(fallback_page)


class AddTeamHandler(SimpleHandler("add_team.html", permission_all=True)):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("teams", "add")

        try:
            attrs = dict()

            self.get_string(attrs, "code")
            self.get_string(attrs, "name")

            assert attrs.get("code") is not None, \
                "No team code specified."

            # Create the team.
            team = Team(**attrs)
            self.sql_session.add(team)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Create the team on RWS.
            self.service.proxy_service.reinitialize()

        # In case other teams need to be added.
        self.redirect(fallback_page)


class AddUserHandler(SimpleHandler("add_user.html", permission_all=True)):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        fallback_page = self.url("users", "add")

        try:
            attrs = dict()

            self.get_string(attrs, "first_name")
            self.get_string(attrs, "last_name")
            self.get_string(attrs, "username", empty=None)

            self.get_string(attrs, "email", empty=None)

            assert attrs.get("username") is not None, \
                "No username specified."
            assert not attrs.get("username").startswith("__"), \
                "Username cannot start with '__' (reserved for system users)."

            # Validate password strength unless explicitly bypassed
            # (e.g., for imports or tests)
            password = self.get_argument("password", "")
            allow_weak = self.get_argument("allow_weak_password", None)
            if len(password) > 0 and allow_weak is None:
                user_inputs = [attrs["username"]]
                if attrs.get("email"):
                    user_inputs.append(attrs["email"])
                validate_password_strength(password, user_inputs)

            self.get_password(attrs, None, False)

            self.get_string(attrs, "timezone", empty=None)

            self.get_string_list(attrs, "preferred_languages")

            # Create the user.
            user = User(**attrs)
            self.sql_session.add(user)

        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            # Create the user on RWS.
            self.service.proxy_service.reinitialize()
            self.redirect(self.url("user", user.id))
        else:
            self.redirect(fallback_page)


class AddParticipationHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        try:
            contest_id: str = self.get_argument("contest_id")
            assert contest_id != "", "Please select a valid contest"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.contest = self.safe_get_item(Contest, contest_id)

        attrs = {}
        self.get_bool(attrs, "hidden")
        self.get_bool(attrs, "unrestricted")

        # Create the participation.
        participation = Participation(contest=self.contest,
                                      user=user,
                                      hidden=attrs["hidden"],
                                      unrestricted=attrs["unrestricted"])
        self.sql_session.add(participation)

        if self.try_commit():
            # Create the user on RWS.
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another contest).
        self.redirect(fallback_page)


class EditParticipationHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        try:
            contest_id: str = self.get_argument("contest_id")
            operation: str = self.get_argument("operation")
            assert contest_id != "", "Please select a valid contest"
            assert operation in (
                "Remove",
            ), "Please select a valid operation"
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Invalid field(s)", repr(error))
            self.redirect(fallback_page)
            return

        self.contest = self.safe_get_item(Contest, contest_id)

        if operation == "Remove":
            # Remove the participation.
            participation = self.sql_session.query(Participation)\
                .filter(Participation.user == user)\
                .filter(Participation.contest == self.contest)\
                .first()
            self.sql_session.delete(participation)

        if self.try_commit():
            # Create the user on RWS.
            self.service.proxy_service.reinitialize()

        # Maybe they'll want to do this again (for another contest).
        self.redirect(fallback_page)


class ClearResetTokenHandler(BaseHandler):
    """Clear a user's password reset token and any pending reset state.

    This allows admins to invalidate a password reset link and clear any
    pending approval state, effectively canceling the entire reset process.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        user.password_reset_token = None
        user.password_reset_token_expires = None
        user.password_reset_pending = False
        user.password_reset_new_hash = None

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Password reset cleared",
                "The password reset token and pending state for user %s has been cleared." % user.username
            )

        self.redirect(fallback_page)


class ApprovePasswordResetHandler(BaseHandler):
    """Approve a pending password reset.

    This copies the pending password hash to the user's password field.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        if not user.password_reset_pending or not user.password_reset_new_hash:
            self.service.add_notification(
                make_datetime(),
                "No pending password reset",
                "User %s does not have a pending password reset." % user.username
            )
            self.redirect(fallback_page)
            return

        user.password = user.password_reset_new_hash
        user.password_reset_pending = False
        user.password_reset_new_hash = None

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Password reset approved",
                "The password reset for user %s has been approved." % user.username
            )
            self.service.proxy_service.reinitialize()

        self.redirect(fallback_page)


class DenyPasswordResetHandler(BaseHandler):
    """Deny a pending password reset.

    This clears the pending password reset without changing the user's password.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, user_id):
        fallback_page = self.url("user", user_id)

        user = self.safe_get_item(User, user_id)

        if not user.password_reset_pending:
            self.service.add_notification(
                make_datetime(),
                "No pending password reset",
                "User %s does not have a pending password reset." % user.username
            )
            self.redirect(fallback_page)
            return

        user.password_reset_pending = False
        user.password_reset_new_hash = None

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Password reset denied",
                "The password reset for user %s has been denied." % user.username
            )

        self.redirect(fallback_page)
