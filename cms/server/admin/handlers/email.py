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

"""Email-related handlers for AWS.

"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from cmscommon.datetime import make_datetime
from .base import BaseHandler, require_permission


logger = logging.getLogger(__name__)


class SendEmailHandler(BaseHandler):
    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def get(self):
        self.r_params = self.render_params()
        self.render("send_email.html", **self.r_params)

    @require_permission(BaseHandler.PERMISSION_MESSAGING)
    def post(self):
        fallback_page = self.url("admins", "send_email")

        try:
            smtp_server = self.get_argument("smtp_server", "")
            smtp_port = self.get_argument("smtp_port", "")
            from_address = self.get_argument("from_address", "")
            to_address = self.get_argument("to_address", "")
            subject = self.get_argument("subject", "")
            content = self.get_argument("content", "")

            assert smtp_server, "SMTP server is required."
            assert smtp_port, "SMTP port is required."
            assert from_address, "From address is required."
            assert to_address, "To address is required."
            assert subject, "Subject is required."
            assert content, "Content is required."

            smtp_port = int(smtp_port)

            msg = MIMEMultipart()
            msg['From'] = from_address
            msg['To'] = to_address
            msg['Subject'] = subject
            msg.attach(MIMEText(content, 'plain'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.sendmail(from_address, to_address, msg.as_string())

            self.service.add_notification(
                make_datetime(), "Email sent successfully",
                f"Email sent to {to_address}")
            self.redirect(self.url("admins"))

        except ValueError as error:
            self.service.add_notification(
                make_datetime(), "Invalid port number", repr(error))
            self.redirect(fallback_page)
        except smtplib.SMTPException as error:
            self.service.add_notification(
                make_datetime(), "SMTP error", repr(error))
            self.redirect(fallback_page)
        except Exception as error:
            self.service.add_notification(
                make_datetime(), "Error sending email", repr(error))
            self.redirect(fallback_page)
