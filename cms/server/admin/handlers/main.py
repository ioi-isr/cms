#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2015 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2016 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
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

"""Non-categorized handlers for AWS.

"""

import io
import json
import logging
import re
import zipfile

from cms import ServiceCoord, get_service_shards, get_service_address
from cms.db import Admin, Contest, DelayRequest, Question, SessionGen, enumerate_files
from cms.server.jinja2_toolbox import markdown_filter
from cmscommon.crypto import validate_password
from cmscommon.datetime import make_datetime, make_timestamp
from .base import BaseHandler, SimpleHandler, require_permission


logger = logging.getLogger(__name__)

# Regex to validate file cacher digests (hex-only strings).
_DIGEST_RE = re.compile(r'^[a-f0-9]+$')


class LoginHandler(SimpleHandler("login.html", authenticated=False)):
    """Login handler.

    """
    def post(self):
        error_args = {"login_error": "true"}
        next_page: str = self.get_argument("next", None)
        if next_page is not None:
            error_args["next"] = next_page
            if next_page != "/":
                next_page = self.url(*next_page.strip("/").split("/"))
            else:
                next_page = self.url()
        else:
            next_page = self.url()
        error_page = self.url("login", **error_args)

        username: str = self.get_argument("username", "")
        password: str = self.get_argument("password", "")
        admin: Admin | None = (
            self.sql_session.query(Admin).filter(Admin.username == username).first()
        )

        if admin is None:
            logger.warning("Nonexistent admin account: %s", username)
            self.redirect(error_page)
            return

        try:
            allowed = validate_password(admin.authentication, password)
        except ValueError:
            logger.warning("Unable to validate password for admin %r", username,
                           exc_info=True)
            allowed = False

        if not allowed or not admin.enabled:
            if not allowed:
                logger.info("Login error for admin %r from IP %s.", username,
                            self.request.remote_ip)
            elif not admin.enabled:
                logger.info("Login successful for admin %r from IP %s, but "
                            "account is disabled.", username,
                            self.request.remote_ip)
            self.redirect(error_page)
            return

        logger.info("Admin logged in: %r from IP %s.", username,
                    self.request.remote_ip)
        self.service.auth_handler.set(admin.id)
        self.redirect(next_page)


class LogoutHandler(BaseHandler):
    """Logout handler.

    """
    def post(self):
        self.service.auth_handler.clear()
        self.redirect(self.url())


class ResourcesHandler(BaseHandler):
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, shard=None, contest_id=None):
        if contest_id is not None:
            self.contest = self.safe_get_item(Contest, contest_id)
            contest_address = [contest_id]
        else:
            contest_address = []

        if shard is None:
            shard = "all"

        self.r_params = self.render_params()
        self.r_params["resource_shards"] = \
            get_service_shards("ResourceService")

        # All addresses for the machine selector
        all_resource_addresses = {}
        for i in range(self.r_params["resource_shards"]):
            try:
                all_resource_addresses[i] = get_service_address(
                    ServiceCoord("ResourceService", i)
                ).ip
            except KeyError:
                logger.warning(f"Missing ResourceService shard {i}, skipping")
        self.r_params["all_resource_addresses"] = all_resource_addresses
        self.r_params["selected_shard"] = shard
        self.r_params["contest_address"] = contest_address

        # Active addresses (what to actually display)
        self.r_params["resource_addresses"] = {}
        if shard == "all":
            self.r_params["resource_addresses"] = dict(all_resource_addresses)
        else:
            shard = int(shard)
            try:
                address = get_service_address(
                    ServiceCoord("ResourceService", shard))
            except KeyError:
                self.redirect(
                    self.url(*(["resources", "all"] + contest_address)))
                return
            self.r_params["resource_addresses"][shard] = address.ip

        self.render("resources.html", **self.r_params)


class NotificationsHandler(BaseHandler):
    """Displays notifications.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self):
        res = []
        last_notification = make_datetime(
            float(self.get_argument("last_notification", "0")))

        questions: list[Question] = (
            self.sql_session.query(Question)
            .filter(Question.reply_timestamp.is_(None))
            .filter(Question.question_timestamp > last_notification)
            .all()
        )

        for question in questions:
            res.append({
                "type": "new_question",
                "timestamp": make_timestamp(question.question_timestamp),
                "subject": question.subject,
                "text": question.text,
                "contest_id": question.participation.contest_id
            })

        delay_requests: list[DelayRequest] = (
            self.sql_session.query(DelayRequest)
            .filter(DelayRequest.status == 'pending')
            .filter(DelayRequest.request_timestamp > last_notification)
            .all()
        )

        for delay_request in delay_requests:
            res.append({
                "type": "new_delay_request",
                "timestamp": make_timestamp(delay_request.request_timestamp),
                "subject": f"Delay request from {delay_request.participation.user.username}",
                "text": delay_request.reason,
                "contest_id": delay_request.participation.contest_id
            })

        # Simple notifications
        for notification in self.service.notifications:
            res.append({"type": "notification",
                        "timestamp": make_timestamp(notification[0]),
                        "subject": notification[1],
                        "text": notification[2]})
        self.service.notifications = []

        self.write(json.dumps(res))

def _get_orphan_digests(file_cacher):
    """Return (all_files, orphan_digests) from the file cacher.

    Compares files present in the file store against those referenced
    in the database. Files not referenced by any task or contest are
    considered orphans.
    """
    files = {digest for digest, _ in file_cacher.list()}
    with SessionGen() as session:
        referenced = enumerate_files(session)
    return files, files - referenced


def _get_orphan_size(file_cacher, orphan_digests):
    """Return the total size in bytes of the given orphan digests."""
    total = 0
    for digest in orphan_digests:
        try:
            total += file_cacher.get_size(digest)
        except KeyError:
            pass
    return total


class FileCacherStatsHandler(BaseHandler):
    """Returns file cacher statistics as JSON.

    This is an expensive operation that scans the entire file store
    and database, so it is only triggered on demand.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        try:
            files, orphan_digests = _get_orphan_digests(
                self.service.file_cacher)
            self.write(json.dumps({
                "total_files": len(files),
                "referenced_files": len(files) - len(orphan_digests),
                "orphan_files": len(orphan_digests),
                "orphan_size": _get_orphan_size(
                    self.service.file_cacher, orphan_digests),
            }))
        except Exception as error:
            logger.error("Error computing file cacher stats: %s", error,
                         exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(error)}))


class FileCacherDeleteOrphansHandler(BaseHandler):
    """Delete orphan files from the file cacher."""

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        try:
            fc = self.service.file_cacher
            _, orphan_digests = _get_orphan_digests(fc)
            deleted_size = _get_orphan_size(fc, orphan_digests)

            for digest in orphan_digests:
                fc.delete(digest)

            self.write(json.dumps({
                "deleted_count": len(orphan_digests),
                "deleted_size": deleted_size,
            }))
        except Exception as error:
            logger.error("Error deleting orphan files: %s", error,
                         exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(error)}))


class FileCacherSearchHandler(BaseHandler):
    """Search file contents in the file cacher."""

    MAX_FILE_SIZE_DEFAULT = 10 * 1024 * 1024  # 10 MB
    MAX_RESULTS_DEFAULT = 100

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        search_term = self.get_argument("search_term", "")
        orphans_only = self.get_argument("orphans_only", "true").lower() == "true"
        try:
            max_results = int(self.get_argument(
                "max_results", str(self.MAX_RESULTS_DEFAULT)))
            max_file_size = int(self.get_argument(
                "max_file_size", str(self.MAX_FILE_SIZE_DEFAULT)))
        except ValueError:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid numeric parameter"}))
            return

        if not search_term:
            self.write(json.dumps([]))
            return

        try:
            fc = self.service.file_cacher
            all_files = fc.list()
            logger.info("FileCacher content search: %d total files, "
                        "search_term=%r, orphans_only=%s",
                        len(all_files), search_term, orphans_only)

            if orphans_only:
                _, orphan_digests = _get_orphan_digests(fc)
                digests_to_search = {
                    digest: desc for digest, desc in all_files
                    if digest in orphan_digests
                }
            else:
                digests_to_search = {
                    digest: desc for digest, desc in all_files
                }

            search_bytes = search_term.encode("utf-8")
            results = []
            skipped = 0

            for digest, desc in digests_to_search.items():
                if len(results) >= max_results:
                    break
                try:
                    size = fc.get_size(digest)
                    if size > max_file_size:
                        skipped += 1
                        continue
                    content = fc.get_file_content(digest)
                    if search_bytes in content:
                        results.append({
                            "digest": digest,
                            "description": desc or "",
                            "size": size,
                        })
                except (KeyError, Exception) as e:
                    logger.debug("Skipping digest %s during search: %s",
                                 digest, e)
                    continue

            logger.info("FileCacher content search complete: "
                        "%d matches found, %d skipped (too large)",
                        len(results), skipped)
            self.write(json.dumps(results))
        except Exception as error:
            logger.error("Error searching file contents: %s", error,
                         exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(error)}))


class FileCacherListByDescriptionHandler(BaseHandler):
    """List files from the file cacher filtered by description."""

    MAX_RESULTS_DEFAULT = 500

    @require_permission(BaseHandler.PERMISSION_ALL)
    def get(self):
        description_pattern = self.get_argument("description_pattern", "")
        orphans_only = self.get_argument(
            "orphans_only", "false").lower() == "true"
        try:
            max_results = int(self.get_argument(
                "max_results", str(self.MAX_RESULTS_DEFAULT)))
        except ValueError:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid numeric parameter"}))
            return

        if not description_pattern:
            self.write(json.dumps([]))
            return

        try:
            fc = self.service.file_cacher
            all_files = fc.list()

            if orphans_only:
                _, orphan_digests = _get_orphan_digests(fc)
                files_to_search = [
                    (digest, desc) for digest, desc in all_files
                    if digest in orphan_digests
                ]
            else:
                files_to_search = all_files

            pattern_lower = description_pattern.lower()
            results = []

            for digest, desc in files_to_search:
                if len(results) >= max_results:
                    break
                desc_str = desc or ""
                if pattern_lower in desc_str.lower():
                    try:
                        size = fc.get_size(digest)
                    except (KeyError, Exception):
                        size = -1
                    results.append({
                        "digest": digest,
                        "description": desc_str,
                        "size": size,
                    })

            self.write(json.dumps(results))
        except Exception as error:
            logger.error("Error listing files by description: %s", error,
                         exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(error)}))


class FileCacherDownloadHandler(BaseHandler):
    """Download multiple files from the file cacher as a ZIP archive."""

    MAX_DIGESTS = 200

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self):
        try:
            body = json.loads(self.request.body)
            digests = body.get("digests", [])
        except (json.JSONDecodeError, AttributeError):
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON body"}))
            return

        if not digests:
            self.set_status(400)
            self.write(json.dumps({"error": "No digests provided"}))
            return

        if len(digests) > self.MAX_DIGESTS:
            self.set_status(400)
            self.write(json.dumps({
                "error": "Too many digests (max %d)" % self.MAX_DIGESTS
            }))
            return

        # Validate all digests are hex-only to prevent path traversal.
        digests = [d for d in digests
                   if isinstance(d, str) and _DIGEST_RE.match(d)]
        if not digests:
            self.set_status(400)
            self.write(json.dumps({"error": "No valid digests provided"}))
            return

        try:
            fc = self.service.file_cacher
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w",
                                 zipfile.ZIP_DEFLATED) as zf:
                for digest in digests:
                    try:
                        content = fc.get_file_content(digest)
                    except (KeyError, Exception) as e:
                        logger.warning("Cannot read digest %s for "
                                       "download: %s", digest, e)
                        continue
                    try:
                        desc = fc.describe(digest)
                    except (KeyError, Exception):
                        desc = ""

                    if desc:
                        sanitized = re.sub(r'[^\w.\- ]', '_', desc[:30])
                        filename = "%s_%s" % (digest, sanitized)
                    else:
                        filename = digest

                    zf.writestr(filename, content)

            self.set_header("Content-Type", "application/zip")
            self.set_header(
                "Content-Disposition",
                "attachment; filename=\"filecacher_files.zip\"")
            self.write(buf.getvalue())
        except Exception as error:
            logger.error("Error creating ZIP download: %s", error,
                         exc_info=True)
            self.set_status(500)
            self.write(json.dumps({"error": str(error)}))


class MarkdownRenderHandler(BaseHandler):
    """Renders Markdown for AWS message previews."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def post(self):
        data = self.get_argument("input")
        rendered = markdown_filter(data)
        self.write(rendered)

