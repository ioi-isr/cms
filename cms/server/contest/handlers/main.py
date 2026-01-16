#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2013 Bernard Blackham <bernard@largestprime.net>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015-2018 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2021 Grace Hawkins <amoomajid99@gmail.com>
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

"""Non-categorized handlers for CWS.

"""

import html
import ipaddress
import json
import logging
import os.path
import re
import secrets
import smtplib
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import collections

from deep_translator import GoogleTranslator

from cms.db.contest import Contest

try:
    collections.MutableMapping
except:
    # Monkey-patch: Tornado 4.5.3 does not work on Python 3.11 by default
    collections.MutableMapping = collections.abc.MutableMapping

import tornado.web
from sqlalchemy.orm.exc import NoResultFound

from cms import config
from cms.db import PrintJob, User, Participation, Team
from cms.grading.languagemanager import get_language
from cms.grading.steps import COMPILATION_MESSAGES, EVALUATION_MESSAGES
from cms.server import multi_contest
from cms.server.contest.authentication import validate_login
from cms.server.contest.communication import get_communications
from cms.server.contest.printing import accept_print_job, PrintingDisabled, \
    UnacceptablePrintJob
from cms.server.picture_utils import process_picture, PictureValidationError
from cmscommon.crypto import hash_password, validate_password, \
    validate_password_strength, WeakPasswordError
from cmscommon.datetime import make_datetime, make_timestamp
from .contest import ContestHandler, api_login_required
from ..phase_management import actual_phase_required
from .base import add_ip_to_list


logger = logging.getLogger(__name__)


# Dummy function to mark translatable strings.
def N_(msgid):
    return msgid


class RegistrationError(Exception):
    """Exception raised for registration validation errors."""

    def __init__(self, code: str, field: str | None = None):
        self.code = code
        self.field = field


class MainHandler(ContestHandler):
    """Home page handler.

    """
    @multi_contest
    def get(self):
        self.render("overview.html", **self.r_params)


class RegistrationHandler(ContestHandler):
    """Registration handler.

    Used to create a participation when this is allowed.
    If `new_user` argument is true, it creates a new user too.

    """

    MAX_INPUT_LENGTH = 50
    MIN_PASSWORD_LENGTH = 6

    @multi_contest
    def post(self):
        if not self.contest.allow_registration:
            raise tornado.web.HTTPError(404)

        create_new_user = self.get_argument("new_user") == "true"

        try:
            # Get or create user
            if create_new_user:
                user = self._create_user()
            else:
                user = self._get_user()

                # Check if the participation exists
                contest = self.contest
                tot_participants = self.sql_session.query(Participation)\
                                       .filter(Participation.user == user)\
                                       .filter(Participation.contest == contest)\
                                       .count()
                if tot_participants > 0:
                    raise tornado.web.HTTPError(409)

            # Create participation
            team = self._get_team()
            participation = Participation(user=user, contest=self.contest,
                                          team=team)
            self.sql_session.add(participation)

            self.sql_session.commit()

            self.finish(user.username)
        except RegistrationError as e:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"code": e.code, "field": e.field}))

    @multi_contest
    def get(self):
        if not self.contest.allow_registration:
            raise tornado.web.HTTPError(404)

        self.r_params["MAX_INPUT_LENGTH"] = self.MAX_INPUT_LENGTH
        self.r_params["MIN_PASSWORD_LENGTH"] = self.MIN_PASSWORD_LENGTH
        self.r_params["teams"] = self.sql_session.query(Team)\
                                     .order_by(Team.name).all()

        self.render("register.html", **self.r_params)

    def _create_user(self) -> User:
        try:
            first_name = self.get_argument("first_name")
            last_name = self.get_argument("last_name")
            username = self.get_argument("username")
            password = self.get_argument("password")
            email = self.get_argument("email")
            date_of_birth_str = self.get_argument("date_of_birth")
        except tornado.web.MissingArgumentError:
            raise RegistrationError("missing_field")

        # Validate email - required and RFC 5322 compliant format check
        if not email or len(email) == 0:
            raise RegistrationError("missing_email", "email")
        # RFC 5322 compliant email regex (case-insensitive)
        email_regex = r'''^(?:[a-z0-9!#$%&'*+\x2f=?^_`\x7b-\x7d~\x2d]+(?:\.[a-z0-9!#$%&'*+\x2f=?^_`\x7b-\x7d~\x2d]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9\x2d]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9\x2d]*[a-z0-9])?|\[(?:(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9]))\.){3}(?:(2(5[0-5]|[0-4][0-9])|1[0-9][0-9]|[1-9]?[0-9])|[a-z0-9\x2d]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])$'''
        if not re.match(email_regex, email, re.IGNORECASE):
            raise RegistrationError("invalid_email", "email")
        if len(email) > self.MAX_INPUT_LENGTH:
            raise RegistrationError("invalid_email", "email")

        # Validate first name
        if not 1 <= len(first_name) <= self.MAX_INPUT_LENGTH:
            raise RegistrationError("invalid_first_name", "first_name")

        # Validate last name
        if not 1 <= len(last_name) <= self.MAX_INPUT_LENGTH:
            raise RegistrationError("invalid_last_name", "last_name")

        # Validate username length
        if not 1 <= len(username) <= self.MAX_INPUT_LENGTH:
            raise RegistrationError("invalid_username_length", "username")

        # Validate username characters
        if not re.match(r"^[A-Za-z0-9_-]+$", username):
            raise RegistrationError("invalid_username_chars", "username")

        if username.startswith("__"):
            raise RegistrationError("invalid_username_start", "username")

        # Validate password length
        if not self.MIN_PASSWORD_LENGTH <= len(password) \
                <= self.MAX_INPUT_LENGTH:
            raise RegistrationError("invalid_password_length", "password")

        # Validate password strength
        try:
            user_inputs = [username]
            if email:
                user_inputs.append(email)
            validate_password_strength(password, user_inputs)
        except WeakPasswordError:
            raise RegistrationError("weak_password", "password")

        # Override password with its hash
        password = hash_password(password)

        # Validate date of birth (required)
        if not date_of_birth_str:
            raise RegistrationError("missing_date_of_birth", "date_of_birth")
        try:
            date_of_birth = date.fromisoformat(date_of_birth_str)
        except ValueError:
            raise RegistrationError("invalid_date_of_birth", "date_of_birth")

        # Process picture (optional)
        picture_digest = None
        if "picture" in self.request.files:
            picture_file = self.request.files["picture"][0]
            try:
                processed_data, _ = process_picture(
                    picture_file["body"],
                    picture_file["content_type"],
                    square_mode="crop"
                )
                picture_digest = self.service.file_cacher.put_file_content(
                    processed_data,
                    "Profile picture for %s" % username
                )
            except PictureValidationError as e:
                raise RegistrationError(e.code, "picture")

        # Check if the username is available
        tot_users = self.sql_session.query(User)\
                        .filter(User.username == username).count()
        if tot_users != 0:
            # HTTP 409: Conflict
            raise tornado.web.HTTPError(409)

        # Store new user
        user = User(first_name, last_name, username, password, email=email,
                    date_of_birth=date_of_birth, picture=picture_digest)
        self.sql_session.add(user)

        return user

    def _get_user(self) -> User:
        username: str = self.get_argument("username")
        password: str = self.get_argument("password")

        # Find user if it exists
        user: User | None = (
            self.sql_session.query(User).filter(
                User.username == username).first()
        )
        if user is None:
            raise tornado.web.HTTPError(404)

        # Check if password is correct
        if not validate_password(user.password, password):
            raise tornado.web.HTTPError(403)

        return user

    def _get_team(self) -> Team | None:
        # If we have teams, we assume that the 'team' field is mandatory
        if self.sql_session.query(Team).count() > 0:
            try:
                team_code: str = self.get_argument("team")
                team: Team | None = (
                    self.sql_session.query(Team).filter(
                        Team.code == team_code).one()
                )
            except (tornado.web.MissingArgumentError, NoResultFound):
                raise RegistrationError("invalid_team", "team")
        else:
            team = None

        return team


class LoginHandler(ContestHandler):
    """Login handler.

    """
    @multi_contest
    def post(self):
        error_args = {"login_error": "true"}
        next_page: str | None = self.get_argument("next", None)
        if next_page is not None:
            error_args["next"] = next_page
            if next_page != "/":
                next_page = self.url(*next_page.strip("/").split("/"))
            else:
                next_page = self.url()
        else:
            next_page = self.contest_url()
        error_page = self.contest_url(**error_args)

        username: str = self.get_argument("username", "")
        password: str = self.get_argument("password", "")

        try:
            ip_address = ipaddress.ip_address(self.request.remote_ip)
        except ValueError:
            logger.warning("Invalid IP address provided by Tornado: %s",
                           self.request.remote_ip)
            return None

        participation, cookie = validate_login(
            self.sql_session, self.contest, self.timestamp, username, password,
            ip_address)

        cookie_name = self.contest.name + "_login"
        if cookie is None:
            self.clear_cookie(cookie_name)
        else:
            self.set_secure_cookie(
                cookie_name,
                cookie,
                expires_days=None,
                max_age=config.contest_web_server.cookie_duration,
            )

        if participation is None:
            self.redirect(error_page)
        else:
            self.redirect(next_page)


class StartHandler(ContestHandler):
    """Start handler.

    Used by a user who wants to start their per_user_time (USACO-style contests)
    or to register their participation in a regular contest.

    """
    @tornado.web.authenticated
    @actual_phase_required(-1, 0)
    @multi_contest
    def post(self):
        participation: Participation = self.current_user

        if participation.starting_time is not None:
            logger.warning("User %s tried to start again", participation.user.username)
            self.redirect(self.contest_url())
            return

        logger.info("Starting now for user %s", participation.user.username)
        participation.starting_time = self.timestamp

        client_ip = self.request.remote_ip
        participation.starting_ip_addresses = add_ip_to_list(
            participation.starting_ip_addresses, client_ip
        )

        self.sql_session.commit()

        self.redirect(self.contest_url())


class LogoutHandler(ContestHandler):
    """Logout handler.

    """
    @multi_contest
    def post(self):
        self.clear_cookie(self.contest.name + "_login")
        self.redirect(self.contest_url())


class NotificationsHandler(ContestHandler):
    """Displays notifications.

    """

    refresh_cookie = False

    @api_login_required
    @multi_contest
    def get(self):
        participation: Participation = self.current_user

        last_notification: str | None = self.get_argument(
            "last_notification", None)
        if last_notification is not None:
            last_notification = make_datetime(float(last_notification))

        res = get_communications(self.sql_session, participation,
                                 self.timestamp, after=last_notification)

        # Simple notifications
        notifications = self.service.notifications
        username = participation.user.username
        if username in notifications:
            for notification in notifications[username]:
                res.append({"type": "notification",
                            "timestamp": make_timestamp(notification[0]),
                            "subject": notification[1],
                            "text": notification[2],
                            "level": notification[3]})
            del notifications[username]

        self.write(json.dumps(res))


class PrintingHandler(ContestHandler):
    """Serve the interface to print and handle submitted print jobs.

    """
    @tornado.web.authenticated
    @actual_phase_required(0)
    @multi_contest
    def get(self):
        participation: Participation = self.current_user

        if not self.r_params["printing_enabled"]:
            raise tornado.web.HTTPError(404)

        printjobs: list[PrintJob] = (
            self.sql_session.query(PrintJob)
            .filter(PrintJob.participation == participation)
            .all()
        )

        remaining_jobs = max(0, config.printing.max_jobs_per_user - len(printjobs))

        self.render("printing.html",
                    printjobs=printjobs,
                    remaining_jobs=remaining_jobs,
                    max_pages=config.printing.max_pages_per_job,
                    pdf_printing_allowed=config.printing.pdf_printing_allowed,
                    **self.r_params)

    @tornado.web.authenticated
    @actual_phase_required(0)
    @multi_contest
    def post(self):
        try:
            printjob = accept_print_job(
                self.sql_session, self.service.file_cacher, self.current_user,
                self.timestamp, self.request.files)
            self.sql_session.commit()
        except PrintingDisabled:
            raise tornado.web.HTTPError(404)
        except UnacceptablePrintJob as e:
            self.notify_error(e.subject, e.text, e.text_params)
        else:
            self.service.printing_service.new_printjob(printjob_id=printjob.id)
            self.notify_success(N_("Print job received"),
                                N_("Your print job has been received."))

        self.redirect(self.contest_url("printing"))


class DocumentationHandler(ContestHandler):
    """Displays the instruction (compilation lines, documentation,
    ...) of the contest.

    """
    @tornado.web.authenticated
    @multi_contest
    def get(self):
        contest: Contest = self.r_params.get("contest")
        languages = [get_language(lang) for lang in contest.languages]

        language_docs = []
        if config.contest_web_server.docs_path is not None:
            for language in languages:
                ext = language.source_extensions[0][1:]  # remove dot
                path = os.path.join(config.contest_web_server.docs_path, ext)
                if os.path.exists(path):
                    language_docs.append((language.name, ext))
        else:
            language_docs.append(("C++", "en"))

        self.render("documentation.html",
                    COMPILATION_MESSAGES=COMPILATION_MESSAGES,
                    EVALUATION_MESSAGES=EVALUATION_MESSAGES,
                    language_docs=language_docs,
                    **self.r_params)


GOOGLE_TRANSLATE_CODE_MAP = {
    'en': 'en',
    'he': 'iw',
    'iw': 'iw',
    'ru': 'ru',
    'ar': 'ar',
    'auto': 'auto'
}


def translate_text(source_text, source_lang, target_lang, supported_languages):
    """Translate text from source language to target language.

    Returns a tuple of (translation_result, error_message).
    If successful, translation_result is the translated text and error_message is None.
    If failed, translation_result is None and error_message contains the error.

    """
    if not source_text:
        return None, N_("Please enter text to translate.")
    
    supported_language_codes = set(supported_languages.keys())
    supported_language_codes |= {
        GOOGLE_TRANSLATE_CODE_MAP[lang]
        for lang in supported_languages
        if lang in GOOGLE_TRANSLATE_CODE_MAP
    }
    
    allowed_source_codes = supported_language_codes | {'auto'}
    allowed_target_codes = supported_language_codes

    if source_lang not in allowed_source_codes:
        return None, N_("Invalid source language.")
    if target_lang == 'auto':
        return None, N_("Cannot use auto-detect as target language.")
    if target_lang not in allowed_target_codes:
        return None, N_("Invalid target language.")
    if source_lang == target_lang and source_lang != 'auto':
        return None, N_("Source and target languages must be different.")
    
    normalized_source = GOOGLE_TRANSLATE_CODE_MAP.get(source_lang, source_lang)
    normalized_target = GOOGLE_TRANSLATE_CODE_MAP.get(target_lang, target_lang)

    try:
        translator = GoogleTranslator(source=normalized_source, target=normalized_target)
        translation_result = translator.translate(source_text)
        return translation_result, None
    except Exception as e:
        logger.error("Translation error: %s", str(e))
        return None, N_("Translation failed. Please try again.")


class TranslationHandler(ContestHandler):
    """Handles text translation for contestants.

    """
    SUPPORTED_LANGUAGES = {
        'en': 'English',
        'he': 'Hebrew',
        'ru': 'Russian',
        'ar': 'Arabic'
    }

    @tornado.web.authenticated
    @multi_contest
    def get(self):
        self.render("translation.html",
                    supported_languages=self.SUPPORTED_LANGUAGES,
                    error_message=None,
                    source_text="",
                    source_lang="auto",
                    target_lang="",
                    translation_result=None,
                    **self.r_params)

    @tornado.web.authenticated
    @multi_contest
    def post(self):
        source_text = self.get_argument("source_text", "")
        source_lang = self.get_argument("source_lang", "")
        target_lang = self.get_argument("target_lang", "")

        translation_result, error_message = translate_text(
            source_text, source_lang, target_lang, self.SUPPORTED_LANGUAGES)

        self.render("translation.html",
                    supported_languages=self.SUPPORTED_LANGUAGES,
                    source_text=source_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    translation_result=translation_result,
                    error_message=error_message,
                    **self.r_params)


class PasswordResetRequestHandler(ContestHandler):
    """Handler for requesting a password reset.

    Accepts username/email from user, validates that the user exists and has
    an email address, generates a secure token, and sends an email with the
    reset link.

    Implements per-username rate limiting to prevent abuse (inbox flooding,
    SMTP load). Rate limiting is configurable via config.email.password_reset.
    """

    _rate_limit_cache: dict[str, list[float]] = {}

    @property
    def token_expiration_hours(self) -> int:
        """Get token expiration hours from config."""
        return config.email.password_reset.token_expiration_hours

    @property
    def rate_limit_max_requests(self) -> int:
        """Get rate limit max requests from config."""
        return config.email.password_reset.rate_limit_max_requests

    @property
    def rate_limit_window_seconds(self) -> int:
        """Get rate limit window in seconds from config."""
        return config.email.password_reset.rate_limit_window_seconds

    def _is_rate_limited(self, username: str) -> bool:
        """Check if the username has exceeded the rate limit.

        Returns True if rate limited, False otherwise.
        Cleans up expired entries from the cache.
        """
        now = make_timestamp(self.timestamp)
        window_start = now - self.rate_limit_window_seconds

        if username in self._rate_limit_cache:
            self._rate_limit_cache[username] = [
                ts for ts in self._rate_limit_cache[username]
                if ts > window_start
            ]
            if len(self._rate_limit_cache[username]) >= self.rate_limit_max_requests:
                return True

        return False

    def _record_request(self, username: str) -> None:
        """Record a password reset request for rate limiting."""
        now = make_timestamp(self.timestamp)
        if username not in self._rate_limit_cache:
            self._rate_limit_cache[username] = []
        self._rate_limit_cache[username].append(now)

    @multi_contest
    def get(self):
        self.render("password_reset_request.html", **self.r_params)

    @multi_contest
    def post(self):
        username_or_email = self.get_argument("username_or_email", "")

        if not username_or_email:
            self.render("password_reset_request.html",
                        error_message=N_("Please enter your username or email."),
                        **self.r_params)
            return

        # First try exact username match
        user = self.sql_session.query(User).filter(
            User.username == username_or_email
        ).first()

        # If no username match, try email
        if user is None:
            users_by_email = self.sql_session.query(User).filter(
                User.email == username_or_email
            ).all()

            if len(users_by_email) == 0:
                self.render("password_reset_request.html",
                            error_message=N_("No user found with that username or email."),
                            **self.r_params)
                return

            if len(users_by_email) > 1:
                self.render("password_reset_request.html",
                            error_message=N_("Multiple users share this email address. Please contact an administrator."),
                            **self.r_params)
                return

            user = users_by_email[0]

        # Check if user participates in this contest
        participation = self.sql_session.query(Participation).filter(
            Participation.user_id == user.id,
            Participation.contest_id == self.contest.id
        ).first()
        if participation is None:
            self.render("password_reset_request.html",
                        error_message=N_("No user found with that username or email."),
                        **self.r_params)
            return

        if not user.email:
            self.render("password_reset_request.html",
                        error_message=N_("This user does not have an email address configured. Please contact an administrator."),
                        **self.r_params)
            return

        if self._is_rate_limited(user.username):
            self.render("password_reset_request.html",
                        error_message=N_("Too many password reset requests. Please try again later."),
                        **self.r_params)
            return

        self._record_request(user.username)

        token = secrets.token_urlsafe(16)
        user.password_reset_token = token
        user.password_reset_token_expires = self.timestamp + timedelta(
            hours=self.token_expiration_hours)

        self.sql_session.commit()

        # Use absolute URL for reset URL - contest_url gives relative path, so get absolute version
        relative_path = self.contest_url("password_reset_confirm", token)
        reset_url = self.request.protocol + "://" + self.request.host + relative_path.lstrip(".")

        email_sent = self._send_reset_email(user.email, reset_url)

        if email_sent:
            self.render("password_reset_request_sent.html",
                        email=user.email,
                        token_expiration_hours=self.token_expiration_hours,
                        **self.r_params)
        else:
            self.render("password_reset_request.html",
                        error_message=N_("Failed to send reset email. Please contact an administrator."),
                        **self.r_params)

    def _send_reset_email(self, email: str, reset_url: str) -> bool:
        """Send password reset email via SMTP.

        Returns True if email was sent successfully, False otherwise.
        Uses configurable email templates from config.email.password_reset.
        Sends both plain text and HTML versions for maximum compatibility.
        """
        smtp_config = config.smtp
        if not smtp_config.server or not smtp_config.sender_address:
            logger.warning("SMTP not configured, cannot send password reset email")
            return False

        email_config = config.email
        pr_config = email_config.password_reset

        # Template placeholders
        placeholders = {
            "system_name": email_config.system_name,
            "reset_url": reset_url,
            "token_expiration_hours": self.token_expiration_hours,
        }

        try:
            # Format templates with placeholders
            subject = pr_config.subject.format_map(placeholders)
            text_body = pr_config.text.format_map(placeholders)

            # Create multipart message for text + HTML
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_config.sender_address
            msg["To"] = email

            # Attach plain text part first (lower priority)
            msg.attach(MIMEText(text_body, "plain"))

            # Attach HTML part if configured (higher priority)
            # Use html.escape() for security best practices
            if pr_config.html:
                html_placeholders = {
                    "system_name": html.escape(str(placeholders["system_name"])),
                    "reset_url": html.escape(str(placeholders["reset_url"])),
                    "token_expiration_hours": placeholders["token_expiration_hours"],
                }
                html_body = pr_config.html.format_map(html_placeholders)
                msg.attach(MIMEText(html_body, "html"))

            if smtp_config.use_tls:
                server = smtplib.SMTP(smtp_config.server, smtp_config.port)
                server.starttls()
            else:
                server = smtplib.SMTP(smtp_config.server, smtp_config.port)

            try:
                if smtp_config.username and smtp_config.password:
                    server.login(smtp_config.username, smtp_config.password)
                server.sendmail(smtp_config.sender_address, [email], msg.as_string())
            finally:
                server.quit()
            return True
        except KeyError as e:
            logger.error("Invalid placeholder in email template: %s", e)
            return False
        except (smtplib.SMTPException, OSError):
            logger.exception("Failed to send password reset email")
            return False


class PasswordResetConfirmHandler(ContestHandler):
    """Handler for confirming a password reset.

    Validates the token from the URL parameter, shows a password reset form,
    and on submission stores the new password hash for admin approval.

    """

    MIN_PASSWORD_LENGTH = 6
    MAX_INPUT_LENGTH = 50

    @property
    def token_expiration_hours(self) -> int:
        """Get token expiration hours from config."""
        return config.email.password_reset.token_expiration_hours

    @multi_contest
    def get(self, token: str):
        user = self._validate_token(token)
        if user is None:
            self.render("password_reset_invalid.html",
                        token_expiration_hours=self.token_expiration_hours,
                        **self.r_params)
            return

        self.render("password_reset_confirm.html",
                    token=token,
                    username=user.username,
                    MIN_PASSWORD_LENGTH=self.MIN_PASSWORD_LENGTH,
                    **self.r_params)

    @multi_contest
    def post(self, token: str):
        user = self._validate_token(token)
        if user is None:
            self.render("password_reset_invalid.html",
                        token_expiration_hours=self.token_expiration_hours,
                        **self.r_params)
            return

        password = self.get_argument("password", "")
        password_confirm = self.get_argument("password_confirm", "")

        if not password:
            self.render("password_reset_confirm.html",
                        token=token,
                        username=user.username,
                        MIN_PASSWORD_LENGTH=self.MIN_PASSWORD_LENGTH,
                        error_message=N_("Please enter a password."),
                        **self.r_params)
            return

        if password != password_confirm:
            self.render("password_reset_confirm.html",
                        token=token,
                        username=user.username,
                        MIN_PASSWORD_LENGTH=self.MIN_PASSWORD_LENGTH,
                        error_message=N_("Passwords do not match."),
                        **self.r_params)
            return

        if not self.MIN_PASSWORD_LENGTH <= len(password) <= self.MAX_INPUT_LENGTH:
            self.render("password_reset_confirm.html",
                        token=token,
                        username=user.username,
                        MIN_PASSWORD_LENGTH=self.MIN_PASSWORD_LENGTH,
                        error_message=N_("Password must be between %d and %d characters.") % (
                            self.MIN_PASSWORD_LENGTH, self.MAX_INPUT_LENGTH),
                        **self.r_params)
            return

        try:
            user_inputs = [user.username]
            if user.email:
                user_inputs.append(user.email)
            validate_password_strength(password, user_inputs)
        except WeakPasswordError:
            self.render("password_reset_confirm.html",
                        token=token,
                        username=user.username,
                        MIN_PASSWORD_LENGTH=self.MIN_PASSWORD_LENGTH,
                        error_message=N_("Password is too weak. Please choose a stronger password."),
                        **self.r_params)
            return

        user.password_reset_new_hash = hash_password(password, method="bcrypt")
        user.password_reset_pending = True
        user.password_reset_token = None
        user.password_reset_token_expires = None

        self.sql_session.commit()

        self.render("password_reset_pending.html",
                    username=user.username,
                    **self.r_params)

    def _validate_token(self, token: str) -> User | None:
        """Validate the password reset token.

        Returns the user if the token is valid and not expired, None otherwise.
        Uses constant-time comparison for defense in depth against timing attacks.
        """
        user = self.sql_session.query(User).filter(
            User.password_reset_token == token
        ).first()

        if user is None:
            return None

        # Constant-time comparison for defense in depth
        # (high-entropy tokens already make timing attacks impractical)
        if not secrets.compare_digest(user.password_reset_token, token):
            return None

        if user.password_reset_token_expires is None:
            return None

        if user.password_reset_token_expires < self.timestamp:
            return None

        return user
