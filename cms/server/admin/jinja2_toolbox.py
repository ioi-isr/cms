#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Provide a Jinja2 environment tailored to AWS.

Extend the global generic Jinja2 environment to inject tools that are
useful specifically to the use that AWS makes of it.

"""

import signal

from jinja2 import Environment, PackageLoader

from cms.db.user import Question
from cms.grading.languagemanager import LANGUAGES
from cms.grading.language import CompiledLanguage
from cms.grading.scoretypes import SCORE_TYPES
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.formatting import format_dataset_attrs
from cms.server.jinja2_toolbox import GLOBAL_ENVIRONMENT
from cmscommon.crypto import get_hex_random_key, parse_authentication


def safe_parse_authentication(auth: str) -> tuple[str, str]:
    try:
        method, password = parse_authentication(auth)
    except ValueError:
        method, password = "plaintext", ""
    return method, password


def format_signal(signum: int | str | None) -> str:
    """Convert a signal number to a human-readable name with description.

    signum: the signal number (e.g., 11 for SIGSEGV), may be int or str.

    return: the signal name with description and number
        (e.g., "SIGFPE - Floating-point exception (8)"), or just
        the number if the signal is unknown.

    """
    if signum is None:
        return "N/A"
    try:
        signum = int(signum)
    except (ValueError, TypeError):
        return str(signum)
    try:
        name = signal.Signals(signum).name
        # Try to get the signal description using strsignal (Python 3.8+)
        try:
            description = signal.strsignal(signum)
            if description:
                return f"{name} - {description} ({signum})"
            else:
                return f"{name} ({signum})"
        except (ValueError, OSError):
            return f"{name} ({signum})"
    except ValueError:
        return str(signum)


def instrument_cms_toolbox(env: Environment):
    env.globals["TASK_TYPES"] = TASK_TYPES
    env.globals["SCORE_TYPES"] = SCORE_TYPES
    env.globals["LANGUAGES"] = LANGUAGES
    env.globals["get_hex_random_key"] = get_hex_random_key
    env.globals["parse_authentication"] = safe_parse_authentication
    env.globals["question_quick_answers"] = Question.QUICK_ANSWERS


def is_compiled_language(lang) -> bool:
    """Check if a language is a compiled language (produces an executable)."""
    return isinstance(lang, CompiledLanguage)


def get_compiled_language_extensions() -> str:
    """Get a comma-separated list of all source file extensions for compiled languages.

    This is used for the 'accept' attribute of file inputs to help users select
    appropriate source files. The list is generated dynamically from the available
    compiled languages to prevent mismatches when new languages are added.

    return: comma-separated extensions (e.g., ".cpp,.c,.py,.java,.pas,.cs,.hs,.rs")

    """
    extensions = set()
    for lang in LANGUAGES:
        if isinstance(lang, CompiledLanguage):
            for ext in lang.source_extensions:
                extensions.add(ext)
    return ",".join(sorted(extensions))


def instrument_formatting_toolbox(env: Environment):
    env.filters["format_dataset_attrs"] = format_dataset_attrs
    env.filters["format_signal"] = format_signal
    env.filters["is_compiled_language"] = is_compiled_language
    env.globals["get_compiled_language_extensions"] = get_compiled_language_extensions


AWS_ENVIRONMENT = GLOBAL_ENVIRONMENT.overlay(
    # Load templates from AWS's package (use package rather than file
    # system as that works even in case of a compressed distribution).
    loader=PackageLoader('cms.server.admin', 'templates'))


instrument_cms_toolbox(AWS_ENVIRONMENT)
instrument_formatting_toolbox(AWS_ENVIRONMENT)
