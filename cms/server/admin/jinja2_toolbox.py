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
from cms.grading.scoretypes import SCORE_TYPES
from cms.grading.tasktypes import TASK_TYPES
from cms.server.admin.formatting import format_dataset_attrs
from cms.server.jinja2_toolbox import GLOBAL_ENVIRONMENT
from cmscommon.crypto import get_hex_random_key, parse_authentication


def safe_parse_authentication(auth: str) -> tuple[str, str]:
    """
    Attempt to parse an authentication string and return its method and password, falling back to plaintext with an empty password on parse errors.
    
    Parameters:
        auth (str): Authentication string to parse.
    
    Returns:
        tuple[str, str]: (method, password). If parsing fails, returns ("plaintext", "").
    """
    try:
        method, password = parse_authentication(auth)
    except ValueError:
        method, password = "plaintext", ""
    return method, password


def format_signal(signum: int | str | None) -> str:
    """
    Format a POSIX signal value into a human-readable string.
    
    Parameters:
        signum (int | str | None): A signal number, a string value, or None.
    
    Returns:
        str: "N/A" if `signum` is None; `str(signum)` if `signum` cannot be interpreted as a known signal number; if the number corresponds to a known signal, "NAME - description (N)" when a system description is available, otherwise "NAME (N)".
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
        except (ValueError, OSError):
            pass
        return f"{name} ({signum})"
    except ValueError:
        return str(signum)


def instrument_cms_toolbox(env: Environment):
    """
    Injects CMS-related utilities and constants into a Jinja2 environment's globals.
    
    Adds the following globals to the provided Environment:
    - TASK_TYPES: available task type constants
    - SCORE_TYPES: available scoring type constants
    - LANGUAGES: supported language definitions
    - get_hex_random_key: callable that returns a hex random key
    - parse_authentication: safe authentication parser
    - question_quick_answers: quick-answer constants from Question
    
    Parameters:
        env (Environment): Jinja2 Environment to augment (mutated in place).
    """
    env.globals["TASK_TYPES"] = TASK_TYPES
    env.globals["SCORE_TYPES"] = SCORE_TYPES
    env.globals["LANGUAGES"] = LANGUAGES
    env.globals["get_hex_random_key"] = get_hex_random_key
    env.globals["parse_authentication"] = safe_parse_authentication
    env.globals["question_quick_answers"] = Question.QUICK_ANSWERS


def instrument_formatting_toolbox(env: Environment):
    """
    Register formatting filters on the given Jinja2 environment.
    
    Adds two filters to env.filters:
    - "format_dataset_attrs": formats dataset attribute dictionaries for presentation.
    - "format_signal": converts a signal value to a human-readable string.
    
    Parameters:
        env (Environment): Jinja2 environment to modify; filters are registered in-place.
    """
    env.filters["format_dataset_attrs"] = format_dataset_attrs
    env.filters["format_signal"] = format_signal


AWS_ENVIRONMENT = GLOBAL_ENVIRONMENT.overlay(
    # Load templates from AWS's package (use package rather than file
    # system as that works even in case of a compressed distribution).
    loader=PackageLoader('cms.server.admin', 'templates'))


instrument_cms_toolbox(AWS_ENVIRONMENT)
instrument_formatting_toolbox(AWS_ENVIRONMENT)