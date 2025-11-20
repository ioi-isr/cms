#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2014 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2013-2018 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014-2018 William Di Luigi <williamdiluigi@gmail.com>
# Copyright © 2015-2019 Luca Chiodini <luca@chiodini.org>
# Copyright © 2016 Andrea Cracco <guilucand@gmail.com>
# Copyright © 2018 Edoardo Morassutto <edoardo.morassutto@gmail.com>
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

import logging
import os
import os.path
import re
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from copy import deepcopy

import yaml

from cms import TOKEN_MODE_DISABLED, TOKEN_MODE_FINITE, TOKEN_MODE_INFINITE, \
    FEEDBACK_LEVEL_FULL, FEEDBACK_LEVEL_RESTRICTED, \
    FEEDBACK_LEVEL_OI_RESTRICTED
from cms.db import Contest, User, Task, Statement, Attachment, Team, Dataset, \
    Manager, Testcase
from cms.grading.languagemanager import LANGUAGES, HEADER_EXTS, \
    filename_to_language
from cms.grading.language import CompiledLanguage
from cms.grading.tasktypes import get_task_type_class
from cms.grading.tasktypes.util import create_sandbox
from cms.grading.steps.compilation import compilation_step
from cmscommon.constants import \
    SCORE_MODE_MAX, SCORE_MODE_MAX_SUBTASK, SCORE_MODE_MAX_TOKENED_LAST
from cmscommon.crypto import build_password
from cmscontrib import touch
from .base_loader import ContestLoader, TaskLoader, UserLoader, TeamLoader, \
    LANGUAGE_MAP, LoaderValidationError


logger = logging.getLogger(__name__)


def find_first_existing_dir(base_path, folder_names):
    """Find the first existing directory from a list of alternatives.

    base_path: the base directory to search in.
    folder_names: list of folder names to try.

    return: the name of the first existing folder, or None if none exist.

    Raises a critical error if multiple folders exist.

    """
    found_folders = []
    for folder_name in folder_names:
        folder_path = os.path.join(base_path, folder_name)
        if os.path.exists(folder_path):
            found_folders.append(folder_name)

    if len(found_folders) > 1:
        raise LoaderValidationError(
            "Multiple alternative folders found: %s. "
            "Please keep only one." % ", ".join(found_folders))

    return found_folders[0] if found_folders else None


def detect_testcase_sources(task_path):
    """Detect and validate testcase sources in a task directory.
    
    task_path: path to the task directory.
    
    return: tuple (source_type, source_path) where source_type is one of:
        'legacy' - legacy input/output folders
        'zip' - tests.zip or testcases.zip
        'folder' - tests or testcases folder
        None - no testcase source found
    
    Raises LoaderValidationError if multiple conflicting sources are found.
    """
    has_legacy = (os.path.exists(os.path.join(task_path, "input")) and
                  os.path.exists(os.path.join(task_path, "output")))
    
    zip_sources = []
    for zip_name in ["tests.zip", "testcases.zip"]:
        zip_path = os.path.join(task_path, zip_name)
        if os.path.exists(zip_path):
            zip_sources.append((zip_name, zip_path))
    
    folder_sources = []
    for folder_name in ["tests", "testcases"]:
        folder_path = os.path.join(task_path, folder_name)
        if os.path.isdir(folder_path):
            folder_sources.append((folder_name, folder_path))
    
    if len(zip_sources) > 1:
        raise LoaderValidationError(
            "Multiple testcase zip files found: %s. Please keep only one." %
            ", ".join([name for name, _ in zip_sources]))
    
    if len(folder_sources) > 1:
        raise LoaderValidationError(
            "Multiple testcase folders found: %s. Please keep only one." %
            ", ".join([name for name, _ in folder_sources]))
    
    if len(zip_sources) > 0 and len(folder_sources) > 0:
        raise LoaderValidationError(
            "Both testcase zip (%s) and folder (%s) found. Please keep only one." %
            (zip_sources[0][0], folder_sources[0][0]))
    
    if has_legacy:
        if zip_sources or folder_sources:
            logger.warning(
                "Both legacy (input/output) and new-style testcase sources found. "
                "Using legacy input/output folders.")
        return ('legacy', task_path)
    elif zip_sources:
        return ('zip', zip_sources[0][1])
    elif folder_sources:
        return ('folder', folder_sources[0][1])
    else:
        return (None, None)


def pair_testcases_from_directory(directory, input_template, output_template):
    """Pair input and output files from a directory using templates.

    directory: path to directory containing testcase files.
    input_template: template for input files (e.g., "input.*").
    output_template: template for output files (e.g., "output.*").

    return: dict mapping codename to (input_path, output_path) tuples.

    """
    if input_template.count('*') != 1 or output_template.count('*') != 1:
        raise LoaderValidationError(
            "Templates must have exactly one '*' placeholder. "
            "Got input_template='%s', output_template='%s'" % 
            (input_template, output_template))

    input_re = re.compile(re.escape(input_template).replace("\\*", "(.*)") + "$")
    output_re = re.compile(re.escape(output_template).replace("\\*", "(.*)") + "$")

    inputs = {}
    outputs = {}

    for filename in os.listdir(directory):
        input_match = input_re.match(filename)
        if input_match:
            codename = input_match.group(1)
            inputs[codename] = os.path.join(directory, filename)

        output_match = output_re.match(filename)
        if output_match:
            codename = output_match.group(1)
            outputs[codename] = os.path.join(directory, filename)

    input_codenames = set(inputs.keys())
    output_codenames = set(outputs.keys())

    if input_codenames != output_codenames:
        missing_outputs = input_codenames - output_codenames
        missing_inputs = output_codenames - input_codenames
        error_msg = []
        if missing_outputs:
            error_msg.append("Missing outputs for: %s" % ", ".join(sorted(missing_outputs)))
        if missing_inputs:
            error_msg.append("Missing inputs for: %s" % ", ".join(sorted(missing_inputs)))
        raise LoaderValidationError("Testcase pairing failed. %s" % "; ".join(error_msg))

    return {codename: (inputs[codename], outputs[codename])
            for codename in sorted(inputs.keys())}


def compile_manager_source(file_cacher, source_path, source_filename,
                           compiled_filename, task_name):
    """Compile a manager source file (checker.cpp or manager.cpp).

    file_cacher: FileCacher instance for storing files.
    source_path: path to the source file.
    source_filename: name of the source file.
    compiled_filename: name for the compiled binary.
    task_name: name of the task (for logging).

    return: tuple (source_digest, compiled_digest) or None if compilation fails.

    """
    with open(source_path, 'rb') as f:
        source_body = f.read()

    try:
        language = filename_to_language(source_filename)
    except Exception:
        logger.warning(
            "Could not detect language for %s, skipping compilation",
            source_filename)
        return None

    if not isinstance(language, CompiledLanguage):
        logger.warning(
            "%s is not a compiled language, skipping compilation",
            source_filename)
        return None

    sandbox = None
    try:
        sandbox = create_sandbox(file_cacher, name="loader_compile")
        sandbox.create_file_from_string(source_filename, source_body)

        commands = language.get_compilation_commands(
            [source_filename], compiled_filename, for_evaluation=True)

        box_success, compilation_success, text, stats = \
            compilation_step(sandbox, commands)

        if not box_success:
            logger.error(
                "Sandbox error during compilation of %s for task %s",
                source_filename, task_name)
            return None

        if not compilation_success:
            stdout = stats.get("stdout", "") if stats else ""
            stderr = stats.get("stderr", "") if stats else ""
            logger.error(
                "Compilation failed for %s in task %s.\nStdout:\n%s\nStderr:\n%s",
                source_filename, task_name, stdout, stderr)
            return None

        compiled_bytes = sandbox.get_file_to_string(compiled_filename, maxlen=None)

        source_digest = file_cacher.put_file_content(
            source_body, "Manager source %s for task %s" % (source_filename, task_name))
        compiled_digest = file_cacher.put_file_content(
            compiled_bytes, "Compiled manager %s for task %s" % (compiled_filename, task_name))

        return (source_digest, compiled_digest)

    except Exception as error:
        logger.error(
            "Error compiling %s for task %s: %s",
            source_filename, task_name, error)
        return None
    finally:
        if sandbox:
            sandbox.cleanup(delete=True)


# Patch PyYAML to make it load all strings as unicode instead of str
# (see http://stackoverflow.com/questions/2890146).
def construct_yaml_str(self, node):
    return self.construct_scalar(node)


yaml.Loader.add_constructor("tag:yaml.org,2002:str", construct_yaml_str)
yaml.SafeLoader.add_constructor("tag:yaml.org,2002:str", construct_yaml_str)


def getmtime(fname):
    return os.stat(fname).st_mtime


yaml_cache = {}

def load_yaml_from_path(path):
    if path in yaml_cache:
        return yaml_cache[path]
    with open(path, "rt", encoding="utf-8") as f:
        value = yaml.safe_load(f)
    yaml_cache[path] = value
    return deepcopy(value)


def load(src, dst, src_name, dst_name=None, conv=lambda i: i):
    """Execute:
      dst[dst_name] = conv(src[src_name])
    with the following features:

      * If src_name is a list, it tries each of its element as
        src_name, stopping when the first one succedes.

      * If dst_name is None, it is set to src_name; if src_name is a
        list, dst_name is set to src_name[0] (_not_ the one that
        succedes).

      * By default conv is the identity function.

      * If dst is None, instead of assigning the result to
        dst[dst_name] (which would cast an exception) it just returns
        it.

      * If src[src_name] doesn't exist, the behavior is different
        depending on whether dst is None or not: if dst is None,
        conv(None) is returned; if dst is not None, nothing is done
        (in particular, dst[dst_name] is _not_ assigned to conv(None);
        it is not assigned to anything!).

    """
    if dst is not None and dst_name is None:
        if isinstance(src_name, list):
            dst_name = src_name[0]
        else:
            dst_name = src_name
    res = None
    found = False
    if isinstance(src_name, list):
        for this_src_name in src_name:
            try:
                res = src[this_src_name]
            except KeyError:
                pass
            else:
                found = True
                break
    else:
        if src_name in src:
            found = True
            res = src[src_name]
    if dst is not None:
        if found:
            dst[dst_name] = conv(res)
    else:
        return conv(res)


def parse_datetime(val):
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, timezone.utc)
    raise ValueError("Invalid datetime format.")


def make_timedelta(t):
    return timedelta(seconds=t)


class YamlLoader(ContestLoader, TaskLoader, UserLoader, TeamLoader):
    """Load a contest, task, user or team stored using the Italian IOI format.

    Given the filesystem location of a contest, task, user or team, stored
    using the Italian IOI format, parse those files and directories to produce
    data that can be consumed by CMS, i.e. the corresponding instances of the
    DB classes.

    """

    short_name = 'italy_yaml'
    description = 'Italian YAML-based format'

    @staticmethod
    def detect(path):
        """See docstring in class Loader."""
        # TODO - Not really refined...
        return os.path.exists(os.path.join(path, "contest.yaml")) or \
            os.path.exists(os.path.join(path, "task.yaml")) or \
            os.path.exists(os.path.join(os.path.dirname(path), "contest.yaml"))

    def get_task_loader(self, taskname):
        return YamlLoader(os.path.join(self.path, taskname), self.file_cacher)

    def get_contest(self):
        """See docstring in class ContestLoader."""
        if not os.path.exists(os.path.join(self.path, "contest.yaml")):
            logger.critical("File missing: \"contest.yaml\"")
            return None

        conf = load_yaml_from_path(os.path.join(self.path, "contest.yaml"))

        # Here we update the time of the last import
        touch(os.path.join(self.path, ".itime_contest"))
        # If this file is not deleted, then the import failed
        touch(os.path.join(self.path, ".import_error_contest"))

        args = {}

        # Contest information
        load(conf, args, ["name", "nome_breve"])
        load(conf, args, ["description", "nome"])
        load(conf, args, "allowed_localizations")
        load(conf, args, "languages")
        load(conf, args, "submissions_download_allowed")
        load(conf, args, "allow_questions")
        load(conf, args, "allow_user_tests")
        load(conf, args, "score_precision")

        logger.info("Loading parameters for contest %s.", args["name"])

        # Logging in
        load(conf, args, "block_hidden_participations")
        load(conf, args, "allow_password_authentication")
        load(conf, args, "allow_registration")
        load(conf, args, "ip_restriction")
        load(conf, args, "ip_autologin")

        # Token parameters
        # Use the new token settings format if detected.
        if "token_mode" in conf:
            load(conf, args, "token_mode")
            load(conf, args, "token_max_number")
            load(conf, args, "token_min_interval", conv=make_timedelta)
            load(conf, args, "token_gen_initial")
            load(conf, args, "token_gen_number")
            load(conf, args, "token_gen_interval", conv=make_timedelta)
            load(conf, args, "token_gen_max")
        # Otherwise fall back on the old one.
        else:
            logger.warning(
                "contest.yaml uses a deprecated format for token settings "
                "which will soon stop being supported, you're advised to "
                "update it.")
            # Determine the mode.
            if conf.get("token_initial", None) is None:
                args["token_mode"] = TOKEN_MODE_DISABLED
            elif conf.get("token_gen_number", 0) > 0 and \
                    conf.get("token_gen_time", 0) == 0:
                args["token_mode"] = TOKEN_MODE_INFINITE
            else:
                args["token_mode"] = TOKEN_MODE_FINITE
            # Set the old default values.
            args["token_gen_initial"] = 0
            args["token_gen_number"] = 0
            args["token_gen_interval"] = timedelta()
            # Copy the parameters to their new names.
            load(conf, args, "token_total", "token_max_number")
            load(conf, args, "token_min_interval", conv=make_timedelta)
            load(conf, args, "token_initial", "token_gen_initial")
            load(conf, args, "token_gen_number")
            load(conf, args, "token_gen_time", "token_gen_interval",
                 conv=make_timedelta)
            load(conf, args, "token_max", "token_gen_max")
            # Remove some corner cases.
            if args["token_gen_initial"] is None:
                args["token_gen_initial"] = 0
            if args["token_gen_interval"].total_seconds() == 0:
                args["token_gen_interval"] = timedelta(minutes=1)

        # Times
        load(conf, args, ["start", "inizio"], conv=parse_datetime)
        load(conf, args, ["stop", "fine"], conv=parse_datetime)
        load(conf, args, ["timezone"])
        load(conf, args, ["per_user_time"], conv=make_timedelta)

        # Limits
        load(conf, args, "max_submission_number")
        load(conf, args, "max_user_test_number")
        load(conf, args, "min_submission_interval", conv=make_timedelta)
        load(conf, args, "min_user_test_interval", conv=make_timedelta)

        # Analysis mode
        load(conf, args, "analysis_enabled")
        load(conf, args, "analysis_start", conv=parse_datetime)
        load(conf, args, "analysis_stop", conv=parse_datetime)

        tasks: list[str] | None = load(conf, None, ["tasks", "problemi"])
        participations: list[dict] | None = load(conf, None, ["users", "utenti"])
        participations = [] if participations is None else participations
        for p in participations:
            p["password"] = build_password(p["password"])

        # Import was successful
        os.remove(os.path.join(self.path, ".import_error_contest"))

        logger.info("Contest parameters loaded.")

        return Contest(**args), tasks, participations

    def get_user(self):
        """See docstring in class UserLoader."""

        if not os.path.exists(os.path.join(os.path.dirname(self.path),
                                           "contest.yaml")):
            logger.critical("File missing: \"contest.yaml\"")
            return None

        username = os.path.basename(self.path)
        logger.info("Loading parameters for user %s.", username)

        conf = load_yaml_from_path(
            os.path.join(os.path.dirname(self.path), "contest.yaml"))

        args = {}

        conf = load(conf, None, ["users", "utenti"])

        for user in conf:
            if user["username"] == username:
                conf = user
                break
        else:
            logger.critical("The specified user cannot be found.")
            return None

        load(conf, args, "username")
        load(conf, args, "password", conv=build_password)

        load(conf, args, ["first_name", "nome"])
        load(conf, args, ["last_name", "cognome"])

        if "first_name" not in args:
            args["first_name"] = ""
        if "last_name" not in args:
            args["last_name"] = args["username"]

        logger.info("User parameters loaded.")

        return User(**args)

    def get_team(self):
        """See docstring in class TeamLoader."""

        if not os.path.exists(os.path.join(os.path.dirname(self.path),
                                           "contest.yaml")):
            logger.critical("File missing: \"contest.yaml\"")
            return None

        team_code = os.path.basename(self.path)
        logger.info("Loading parameters for team %s.", team_code)

        conf = load_yaml_from_path(
            os.path.join(os.path.dirname(self.path), "contest.yaml"))

        args = {}

        conf = load(conf, None, "teams")

        for team in conf:
            if team["code"] == team_code:
                conf = team
                break
        else:
            logger.critical("The specified team cannot be found.")
            return None

        load(conf, args, "code")
        load(conf, args, "name")

        logger.info("Team parameters loaded.")

        return Team(**args)

    def get_task(self, get_statement=True) -> Task | None:
        """See docstring in class TaskLoader."""
        name = os.path.split(self.path)[1]

        if (not os.path.exists(os.path.join(self.path, "task.yaml"))) and \
           (not os.path.exists(os.path.join(self.path, "..", name + ".yaml"))):
            logger.critical("File missing: \"task.yaml\"")
            return None

        # We first look for the yaml file inside the task folder,
        # and eventually fallback to a yaml file in its parent folder.
        try:
            conf = load_yaml_from_path(os.path.join(self.path, "task.yaml"))
        except OSError as err:
            try:
                deprecated_path = os.path.join(self.path, "..", name + ".yaml")
                conf = load_yaml_from_path(deprecated_path)

                logger.warning("You're using a deprecated location for the "
                               "task.yaml file. You're advised to move %s to "
                               "%s.", deprecated_path,
                               os.path.join(self.path, "task.yaml"))
            except OSError:
                # Since both task.yaml and the (deprecated) "../taskname.yaml"
                # are missing, we will only warn the user that task.yaml is
                # missing (to avoid encouraging the use of the deprecated one)
                raise err

        # Here we update the time of the last import
        touch(os.path.join(self.path, ".itime"))
        # If this file is not deleted, then the import failed
        touch(os.path.join(self.path, ".import_error"))

        args = {}

        load(conf, args, ["name", "nome_breve"])
        load(conf, args, ["title", "nome"])

        if name != args["name"]:
            logger.info("The task name (%s) and the directory name (%s) are "
                        "different. The former will be used.", args["name"],
                        name)

        if args["name"] == args["title"]:
            logger.warning("Short name equals long name (title). "
                           "Please check.")

        name = args["name"]

        logger.info("Loading parameters for task %s.", name)

        if get_statement:
            # The language of testo.pdf / statement.pdf, defaulting to 'he'
            primary_language = load(conf, None, "primary_language")
            if primary_language is None:
                primary_language = "he"

            statement = find_first_existing_dir(
                self.path,
                ["statement", "statements", "Statement", "Statements", "testo"])

            if statement is None:
                raise LoaderValidationError("Statement folder not found.")

            single_statement_path = os.path.join(
                self.path, statement, "%s.pdf" % statement)
            if not os.path.exists(single_statement_path):
                single_statement_path = None

            multi_statement_paths = {}
            for lang, lang_code in LANGUAGE_MAP.items():
                path = os.path.join(self.path, statement, "%s.pdf" % lang)
                if os.path.exists(path):
                    multi_statement_paths[lang_code] = path

            if len(multi_statement_paths) > 0:
                # Ensure that either a statement.pdf or testo.pdf is specified,
                # or a list of <lang>.pdf files are specified, but not both,
                # unless statement.pdf or testo.pdf is a symlink, in which case
                # we let it slide.
                if single_statement_path is not None and not os.path.islink(
                    single_statement_path
                ):
                    logger.warning(
                        f"A statement (not a symlink!) is present at {single_statement_path} "
                        f"but {len(multi_statement_paths)} more multi-language statements "
                        "were found. This is likely an error. Proceeding with "
                        "importing the multi-language files only."
                    )
                statements_to_import = multi_statement_paths
            else:
                statements_to_import = {
                    primary_language: single_statement_path}

            if primary_language not in statements_to_import.keys():
                raise LoaderValidationError(
                    "Couldn't find statement for primary language %s, aborting." % primary_language)

            args["statements"] = dict()
            for lang_code, statement_path in statements_to_import.items():
                digest = self.file_cacher.put_file_from_path(
                    statement_path,
                    "Statement for task %s (lang: %s)" % (name, lang_code),
                )
                args["statements"][lang_code] = Statement(lang_code, digest)

            args["primary_statements"] = [primary_language]

        args["submission_format"] = ["%s.%%l" % name]

        # Import the feedback level when explicitly set
        # (default behaviour is restricted)
        if conf.get("feedback_level", None) == FEEDBACK_LEVEL_FULL:
            args["feedback_level"] = FEEDBACK_LEVEL_FULL
        elif conf.get("feedback_level", None) == FEEDBACK_LEVEL_RESTRICTED:
            args["feedback_level"] = FEEDBACK_LEVEL_RESTRICTED
        elif conf.get("feedback_level", None) == FEEDBACK_LEVEL_OI_RESTRICTED:
            args["feedback_level"] = FEEDBACK_LEVEL_OI_RESTRICTED

        if conf.get("score_mode", None) == SCORE_MODE_MAX:
            args["score_mode"] = SCORE_MODE_MAX
        elif conf.get("score_mode", None) == SCORE_MODE_MAX_SUBTASK:
            args["score_mode"] = SCORE_MODE_MAX_SUBTASK
        elif conf.get("score_mode", None) == SCORE_MODE_MAX_TOKENED_LAST:
            args["score_mode"] = SCORE_MODE_MAX_TOKENED_LAST

        # Use the new token settings format if detected.
        if "token_mode" in conf:
            load(conf, args, "token_mode")
            load(conf, args, "token_max_number")
            load(conf, args, "token_min_interval", conv=make_timedelta)
            load(conf, args, "token_gen_initial")
            load(conf, args, "token_gen_number")
            load(conf, args, "token_gen_interval", conv=make_timedelta)
            load(conf, args, "token_gen_max")
        # Otherwise fall back on the old one.
        else:
            logger.warning(
                "task.yaml uses a deprecated format for token settings which "
                "will soon stop being supported, you're advised to update it.")
            # Determine the mode.
            if conf.get("token_initial", None) is None:
                args["token_mode"] = TOKEN_MODE_DISABLED
            elif conf.get("token_gen_number", 0) > 0 and \
                    conf.get("token_gen_time", 0) == 0:
                args["token_mode"] = TOKEN_MODE_INFINITE
            else:
                args["token_mode"] = TOKEN_MODE_FINITE
            # Set the old default values.
            args["token_gen_initial"] = 0
            args["token_gen_number"] = 0
            args["token_gen_interval"] = timedelta()
            # Copy the parameters to their new names.
            load(conf, args, "token_total", "token_max_number")
            load(conf, args, "token_min_interval", conv=make_timedelta)
            load(conf, args, "token_initial", "token_gen_initial")
            load(conf, args, "token_gen_number")
            load(conf, args, "token_gen_time", "token_gen_interval",
                 conv=make_timedelta)
            load(conf, args, "token_max", "token_gen_max")
            # Remove some corner cases.
            if args["token_gen_initial"] is None:
                args["token_gen_initial"] = 0
            if args["token_gen_interval"].total_seconds() == 0:
                args["token_gen_interval"] = timedelta(minutes=1)

        load(conf, args, "max_submission_number")
        load(conf, args, "max_user_test_number")
        load(conf, args, "min_submission_interval", conv=make_timedelta)
        load(conf, args, "min_user_test_interval", conv=make_timedelta)

        # Attachments
        args["attachments"] = dict()
        attachments_folder = find_first_existing_dir(
            self.path, ["att", "attachements", "Attachements"])
        if attachments_folder is not None:
            for filename in os.listdir(
                    os.path.join(self.path, attachments_folder)):
                digest = self.file_cacher.put_file_from_path(
                    os.path.join(self.path, attachments_folder, filename),
                    "Attachment %s for task %s" % (filename, name))
                args["attachments"][filename] = Attachment(filename, digest)

        # Score precision.
        load(conf, args, "score_precision")

        task = Task(**args)

        args = {}
        args["task"] = task
        args["description"] = conf.get("version", "Default")
        args["autojudge"] = False

        load(conf, args, ["time_limit", "timeout"], conv=float)
        # The Italian YAML format specifies memory limits in MiB.
        load(conf, args, ["memory_limit", "memlimit"],
             conv=lambda mb: mb * 1024 * 1024)

        # Builds the parameters that depend on the task type
        args["managers"] = []
        infile_param = conf.get("infile", "")
        outfile_param = conf.get("outfile", "")

        # If there is sol/grader.%l for some language %l, then,
        # presuming that the task type is Batch, we retrieve graders
        # in the form sol/grader.%l
        graders = False
        for lang in LANGUAGES:
            if os.path.exists(os.path.join(
                    self.path, "sol", "grader%s" % lang.source_extension)):
                graders = True
                break
        if graders:
            # Read grader for each language
            for lang in LANGUAGES:
                extension = lang.source_extension
                grader_filename = os.path.join(
                    self.path, "sol", "grader%s" % extension)
                if os.path.exists(grader_filename):
                    digest = self.file_cacher.put_file_from_path(
                        grader_filename,
                        "Grader for task %s and language %s" %
                        (task.name, lang))
                    args["managers"] += [
                        Manager("grader%s" % extension, digest)]
                else:
                    logger.warning("Grader for language %s not found ", lang)
            # Read managers with other known file extensions
            for other_filename in os.listdir(os.path.join(self.path, "sol")):
                if any(other_filename.endswith(header)
                       for header in HEADER_EXTS):
                    digest = self.file_cacher.put_file_from_path(
                        os.path.join(self.path, "sol", other_filename),
                        "Manager %s for task %s" % (other_filename, task.name))
                    args["managers"] += [
                        Manager(other_filename, digest)]
            compilation_param = "grader"
        else:
            compilation_param = "alone"

        # If there is check/checker (or equivalent), then, presuming
        # that the task type is Batch or OutputOnly, we retrieve the
        # comparator
        paths = [os.path.join(self.path, "check", "checker"),
                 os.path.join(self.path, "cor", "correttore")]
        for path in paths:
            if os.path.exists(path):
                digest = self.file_cacher.put_file_from_path(
                    path,
                    "Manager for task %s" % task.name)
                args["managers"] += [
                    Manager("checker", digest)]
                evaluation_param = "comparator"
                break
        else:
            evaluation_param = "diff"

        managers_folder = find_first_existing_dir(
            self.path, ["managers", "Managers"])
        if managers_folder is not None:
            managers_path = os.path.join(self.path, managers_folder)

            # Determine allowed compile basenames from task type
            allowed_compile_basenames = set()
            if "task_type" in conf:
                try:
                    tt_cls = get_task_type_class(conf["task_type"])
                    if hasattr(tt_cls, "CHECKER_CODENAME"):
                        allowed_compile_basenames.add(
                            getattr(tt_cls, "CHECKER_CODENAME"))
                    if hasattr(tt_cls, "MANAGER_FILENAME"):
                        allowed_compile_basenames.add(
                            getattr(tt_cls, "MANAGER_FILENAME"))
                except Exception:
                    pass

            if not allowed_compile_basenames:
                allowed_compile_basenames = {"checker", "manager"}

            existing_manager_filenames = {m.filename for m in args["managers"]}

            for filename in os.listdir(managers_path):
                file_path = os.path.join(managers_path, filename)
                if not os.path.isfile(file_path):
                    continue

                base_noext = os.path.splitext(filename)[0]

                # Check if this is a source file that should be compiled
                should_compile = (base_noext in allowed_compile_basenames and
                                filename.endswith(('.cpp', '.c', '.cc', '.cxx')))

                if should_compile:
                    result = compile_manager_source(
                        self.file_cacher, file_path, filename,
                        base_noext, task.name)

                    if result is not None:
                        source_digest, compiled_digest = result

                        if filename not in existing_manager_filenames:
                            args["managers"] += [Manager(filename, source_digest)]
                            existing_manager_filenames.add(filename)

                        if base_noext not in existing_manager_filenames:
                            args["managers"] += [Manager(base_noext, compiled_digest)]
                            existing_manager_filenames.add(base_noext)

                            if base_noext == "checker":
                                evaluation_param = "comparator"
                    else:
                        logger.warning(
                            "Failed to compile %s from managers folder, skipping",
                            filename)
                else:
                    if filename not in existing_manager_filenames:
                        digest = self.file_cacher.put_file_from_path(
                            file_path,
                            "Manager %s for task %s" % (filename, task.name))
                        args["managers"] += [Manager(filename, digest)]
                        existing_manager_filenames.add(filename)

        # Override score_type if explicitly specified
        if "score_type" in conf and "score_type_parameters" in conf and "n_input" in conf:
            logger.info("Overriding 'score_type' and 'score_type_parameters' "
                        "as per task.yaml")
            n_input = conf["n_input"]
            load(conf, args, "score_type")
            load(conf, args, "score_type_parameters")
        else:
            if "score_type" in conf or "score_type_parameters" in conf:
                logger.warning("To override score type data, task.yaml must "
                            "specify all 'score_type', "
                            "'score_type_parameters' and "
                            "'n_input'.")

            # Detect subtasks by checking GEN
            gen_filename = os.path.join(self.path, 'gen', 'GEN')
            try:
                with open(gen_filename, "rt", encoding="utf-8") as gen_file:
                    subtasks = []
                    testcases = 0
                    points = None
                    for line in gen_file:
                        line = line.strip()
                        splitted = line.split('#', 1)

                        if len(splitted) == 1:
                            # This line represents a testcase, otherwise
                            # it's just a blank
                            if splitted[0] != '':
                                testcases += 1

                        else:
                            testcase, comment = splitted
                            testcase = testcase.strip()
                            comment = comment.strip()
                            testcase_detected = len(testcase) > 0
                            copy_testcase_detected = comment.startswith("COPY:")
                            subtask_detected = comment.startswith('ST:')

                            flags = [testcase_detected,
                                    copy_testcase_detected,
                                    subtask_detected]
                            if len([x for x in flags if x]) > 1:
                                raise Exception("No testcase and command in"
                                                " the same line allowed")

                            # This line represents a testcase and contains a
                            # comment, but the comment doesn't start a new
                            # subtask
                            if testcase_detected or copy_testcase_detected:
                                testcases += 1

                            # This line starts a new subtask
                            if subtask_detected:
                                # Close the previous subtask
                                if points is None:
                                    assert testcases == 0
                                else:
                                    subtasks.append([points, testcases])
                                # Open the new one
                                testcases = 0
                                points = int(comment[3:].strip())

                    # Close last subtask (if no subtasks were defined, just
                    # fallback to Sum)
                    if points is None:
                        args["score_type"] = "Sum"
                        total_value = float(conf.get("total_value", 100.0))
                        input_value = 0.0
                        n_input = testcases
                        if n_input != 0:
                            input_value = total_value / n_input
                        args["score_type_parameters"] = input_value
                    else:
                        subtasks.append([points, testcases])
                        assert 100 == sum([int(st[0]) for st in subtasks])
                        n_input = sum([int(st[1]) for st in subtasks])
                        args["score_type"] = "GroupMin"
                        args["score_type_parameters"] = subtasks

                    if "n_input" in conf:
                        assert int(conf['n_input']) == n_input

            # If gen/GEN doesn't exist, just fallback to Sum
            except OSError:
                args["score_type"] = "Sum"
                total_value = float(conf.get("total_value", 100.0))
                input_value = 0.0
                n_input = int(conf['n_input'])
                if n_input != 0:
                    input_value = total_value / n_input
                args["score_type_parameters"] = input_value

        # If output_only is set, then the task type is OutputOnly
        if conf.get('output_only', False):
            args["task_type"] = "OutputOnly"
            args["time_limit"] = None
            args["memory_limit"] = None
            args["task_type_parameters"] = [evaluation_param]
            task.submission_format = \
                ["output_%03d.txt" % i for i in range(n_input)]

        # If there is check/manager (or equivalent), then the task
        # type is Communication
        else:
            paths = [os.path.join(self.path, "check", "manager"),
                     os.path.join(self.path, "cor", "manager")]
            for path in paths:
                if os.path.exists(path):
                    num_processes = load(conf, None, "num_processes")
                    if num_processes is None:
                        num_processes = 1
                    io_type = load(conf, None, "user_io")
                    if io_type is not None:
                        if io_type not in ["std_io", "fifo_io"]:
                            logger.warning("user_io incorrect. Valid options "
                                           "are 'std_io' and 'fifo_io'. "
                                           "Ignored.")
                            io_type = None
                    logger.info("Task type Communication")
                    args["task_type"] = "Communication"
                    args["task_type_parameters"] = \
                        [num_processes, "alone", io_type or "std_io"]
                    digest = self.file_cacher.put_file_from_path(
                        path,
                        "Manager for task %s" % task.name)
                    args["managers"] += [
                        Manager("manager", digest)]
                    for lang in LANGUAGES:
                        stub_name = os.path.join(
                            self.path, "sol", "stub%s" % lang.source_extension)
                        if os.path.exists(stub_name):
                            digest = self.file_cacher.put_file_from_path(
                                stub_name,
                                "Stub for task %s and language %s" % (
                                    task.name, lang.name))
                            args["task_type_parameters"] = \
                                [num_processes, "stub", io_type or "fifo_io"]
                            args["managers"] += [
                                Manager(
                                    "stub%s" % lang.source_extension, digest)]
                        else:
                            logger.warning("Stub for language %s not "
                                           "found.", lang.name)
                    for other_filename in os.listdir(os.path.join(self.path,
                                                                  "sol")):
                        if any(other_filename.endswith(header)
                               for header in HEADER_EXTS):
                            digest = self.file_cacher.put_file_from_path(
                                os.path.join(self.path, "sol", other_filename),
                                "Stub %s for task %s" % (other_filename,
                                                         task.name))
                            args["managers"] += [
                                Manager(other_filename, digest)]
                    break

            # Otherwise, the task type is Batch or BatchAndOutput
            else:
                args["task_type"] = "Batch"
                args["task_type_parameters"] = [
                    compilation_param,
                    [infile_param, outfile_param],
                    evaluation_param,
                ]

                output_only_testcases = load(conf, None, "output_only_testcases",
                                             conv=lambda x: "" if x is None else x)
                output_optional_testcases = load(conf, None, "output_optional_testcases",
                                             conv=lambda x: "" if x is None else x)
                if len(output_only_testcases) > 0 or len(output_optional_testcases) > 0:
                    args["task_type"] = "BatchAndOutput"
                    output_only_codenames = set()
                    if len(output_only_testcases) > 0:
                        output_only_codenames = \
                            {"%03d" % int(x.strip()) for x in output_only_testcases.split(',')}
                        args["task_type_parameters"].append(','.join(output_only_codenames))
                    else:
                        args["task_type_parameters"].append("")
                    output_codenames = set()
                    if len(output_optional_testcases) > 0:
                        output_codenames = \
                            {"%03d" % int(x.strip()) for x in output_optional_testcases.split(',')}
                    output_codenames.update(output_only_codenames)
                    task.submission_format.extend(["output_%s.txt" % s for s in sorted(output_codenames)])

        args["testcases"] = []
        testcases_temp_dir = None

        source_type, source_path = detect_testcase_sources(self.path)

        if source_type == 'legacy':
            # Legacy input/output folders
            for i in range(n_input):
                input_digest = self.file_cacher.put_file_from_path(
                    os.path.join(self.path, "input", "input%d.txt" % i),
                    "Input %d for task %s" % (i, task.name))
                output_digest = self.file_cacher.put_file_from_path(
                    os.path.join(self.path, "output", "output%d.txt" % i),
                    "Output %d for task %s" % (i, task.name))
                test_codename = "%03d" % i
                args["testcases"] += [
                    Testcase(test_codename, True, input_digest, output_digest)]
                if args["task_type"] == "OutputOnly":
                    task.attachments.set(
                        Attachment("input_%s.txt" % test_codename, input_digest))
                elif args["task_type"] == "BatchAndOutput":
                    if output_codenames is not None and test_codename in output_codenames:
                        task.attachments.set(
                            Attachment("input_%s.txt" % test_codename, input_digest))
        elif source_type in ('zip', 'folder'):
            testcases_dir = None
            
            if source_type == 'zip':
                testcases_temp_dir = tempfile.mkdtemp(prefix="cms_testcases_")
                with zipfile.ZipFile(source_path, 'r') as zip_ref:
                    zip_ref.extractall(testcases_temp_dir)
                testcases_dir = testcases_temp_dir
                logger.info("Extracted testcases from %s", os.path.basename(source_path))
            else:
                testcases_dir = source_path

            input_template = load(conf, None, "input_template")
            if input_template is None:
                input_template = "input.*"
            output_template = load(conf, None, "output_template")
            if output_template is None:
                output_template = "output.*"

            paired_testcases = pair_testcases_from_directory(
                testcases_dir, input_template, output_template)

            if n_input == 0 and not os.path.exists(os.path.join(self.path, "gen", "GEN")):
                n_input = len(paired_testcases)
                logger.info("Discovered %d testcases from templates", n_input)

            if len(paired_testcases) != n_input:
                if testcases_temp_dir:
                    import shutil
                    shutil.rmtree(testcases_temp_dir)
                raise LoaderValidationError(
                    "Testcase count mismatch: found %d testcases but expected %d" %
                    (len(paired_testcases), n_input))

            # Load testcases
            for codename, (input_path, output_path) in paired_testcases.items():
                input_digest = self.file_cacher.put_file_from_path(
                    input_path,
                    "Input %s for task %s" % (codename, task.name))
                output_digest = self.file_cacher.put_file_from_path(
                    output_path,
                    "Output %s for task %s" % (codename, task.name))
                args["testcases"] += [
                    Testcase(codename, True, input_digest, output_digest)]
                if args["task_type"] == "OutputOnly":
                    task.attachments.set(
                        Attachment("input_%s.txt" % codename, input_digest))
                elif args["task_type"] == "BatchAndOutput":
                    if output_codenames is not None and codename in output_codenames:
                        task.attachments.set(
                            Attachment("input_%s.txt" % codename, input_digest))

            if testcases_temp_dir:
                import shutil
                shutil.rmtree(testcases_temp_dir)
        else:
            # No testcase source found
            raise LoaderValidationError(
                "No testcases found. Expected input/output folders or "
                "tests/testcases folder/zip.")

        public_testcases = load(conf, None, ["public_testcases", "risultati"],
                                conv=lambda x: "" if x is None else x)
        if public_testcases == "all":
            for t in args["testcases"]:
                t.public = True
        elif len(public_testcases) > 0:
            for t in args["testcases"]:
                t.public = False
            for x in public_testcases.split(","):
                args["testcases"][int(x.strip())].public = True
        args["testcases"] = dict((tc.codename, tc) for tc in args["testcases"])
        args["managers"] = dict((mg.filename, mg) for mg in args["managers"])

        dataset = Dataset(**args)
        task.active_dataset = dataset

        # Import was successful
        os.remove(os.path.join(self.path, ".import_error"))

        logger.info("Task parameters loaded.")

        return task

    def contest_has_changed(self):
        """See docstring in class ContestLoader."""
        name = os.path.split(self.path)[1]
        contest_yaml = os.path.join(self.path, "contest.yaml")

        if not os.path.exists(contest_yaml):
            raise LoaderValidationError("File missing: \"contest.yaml\"")

        # If there is no .itime file, we assume that the contest has changed
        if not os.path.exists(os.path.join(self.path, ".itime_contest")):
            return True

        itime = getmtime(os.path.join(self.path, ".itime_contest"))

        # Check if contest.yaml has changed
        if getmtime(contest_yaml) > itime:
            return True

        if os.path.exists(os.path.join(self.path, ".import_error_contest")):
            raise LoaderValidationError(
                "Last attempt to import contest %s failed. "
                "After fixing the error, delete the file .import_error_contest" % name)

        return False

    def user_has_changed(self):
        """See docstring in class UserLoader."""
        # This works as users are kept inside contest.yaml, so changing
        # them alters the last modified time of contest.yaml.
        # TODO Improve this.
        return self.contest_has_changed()

    def team_has_changed(self):
        """See docstring in class TeamLoader."""
        # This works as teams are kept inside contest.yaml, so changing
        # them alters the last modified time of contest.yaml.
        # TODO Improve this.
        return self.contest_has_changed()

    def task_has_changed(self):
        """See docstring in class TaskLoader."""
        name = os.path.split(self.path)[1]

        if (not os.path.exists(os.path.join(self.path, "task.yaml"))) and \
           (not os.path.exists(os.path.join(self.path, "..", name + ".yaml"))):
            raise LoaderValidationError("File missing: \"task.yaml\"")

        # We first look for the yaml file inside the task folder,
        # and eventually fallback to a yaml file in its parent folder.
        try:
            conf = load_yaml_from_path(os.path.join(self.path, "task.yaml"))
        except OSError:
            conf = load_yaml_from_path(
                os.path.join(self.path, "..", name + ".yaml"))

        # If there is no .itime file, we assume that the task has changed
        if not os.path.exists(os.path.join(self.path, ".itime")):
            return True

        itime = getmtime(os.path.join(self.path, ".itime"))

        # Generate a task's list of files
        files = []

        # Testcases (legacy input/output folders)
        if os.path.exists(os.path.join(self.path, "input")):
            for filename in os.listdir(os.path.join(self.path, "input")):
                files.append(os.path.join(self.path, "input", filename))
        if os.path.exists(os.path.join(self.path, "output")):
            for filename in os.listdir(os.path.join(self.path, "output")):
                files.append(os.path.join(self.path, "output", filename))

        # Testcases (new tests/testcases folders and zips)
        for testcases_name in ["tests", "testcases"]:
            testcases_path = os.path.join(self.path, testcases_name)
            if os.path.isdir(testcases_path):
                for filename in os.listdir(testcases_path):
                    files.append(os.path.join(testcases_path, filename))
            zip_path = os.path.join(self.path, testcases_name + ".zip")
            if os.path.exists(zip_path):
                files.append(zip_path)

        # Attachments (all variants)
        for att_name in ["att", "attachements", "Attachements"]:
            att_path = os.path.join(self.path, att_name)
            if os.path.exists(att_path):
                for filename in os.listdir(att_path):
                    files.append(os.path.join(att_path, filename))

        # Score file
        files.append(os.path.join(self.path, "gen", "GEN"))

        # Statement (all variants)
        for statement_name in ["statement", "statements", "Statement", "Statements", "testo"]:
            statement_path = os.path.join(self.path, statement_name)
            files.append(os.path.join(statement_path, "statement.pdf"))
            files.append(os.path.join(statement_path, "testo.pdf"))
            for lang in LANGUAGE_MAP:
                files.append(os.path.join(statement_path, "%s.pdf" % lang))

        # Managers (legacy check/cor folders)
        files.append(os.path.join(self.path, "check", "checker"))
        files.append(os.path.join(self.path, "cor", "correttore"))
        files.append(os.path.join(self.path, "check", "manager"))
        files.append(os.path.join(self.path, "cor", "manager"))

        # Managers (new managers folder)
        for managers_name in ["managers", "Managers"]:
            managers_path = os.path.join(self.path, managers_name)
            if os.path.isdir(managers_path):
                for filename in os.listdir(managers_path):
                    files.append(os.path.join(managers_path, filename))

        if not conf.get('output_only', False) and \
                os.path.isdir(os.path.join(self.path, "sol")):
            for lang in LANGUAGES:
                files.append(os.path.join(
                    self.path, "sol", "grader%s" % lang.source_extension))
            for other_filename in os.listdir(os.path.join(self.path, "sol")):
                if any(other_filename.endswith(header)
                       for header in HEADER_EXTS):
                    files.append(
                        os.path.join(self.path, "sol", other_filename))

        # Yaml
        files.append(os.path.join(self.path, "task.yaml"))
        files.append(os.path.join(self.path, "..", name + ".yaml"))

        # Check is any of the files have changed
        for fname in files:
            if os.path.exists(fname):
                if getmtime(fname) > itime:
                    return True

        if os.path.exists(os.path.join(self.path, ".import_error")):
            raise LoaderValidationError(
                "Last attempt to import task %s failed. "
                "After fixing the error, delete the file .import_error" % name)

        return False
