#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""In this file there is the basic infrastructure from which we can
build a task type.

Basically, a task type is a class that receives a submission and knows
how to compile and evaluate it. A worker creates a task type to work
on a submission, and all low-level details on how to implement the
compilation and the evaluation are contained in the task type class.

"""

import logging
import os
import shutil
from typing import Callable, Optional

from cms import config
from cms.db.filecacher import FileCacher
from cms.grading import JobException
from cms.grading.Job import CompilationJob, EvaluationJob, Job
from cms.grading.Sandbox import Sandbox
from cms.grading.language import Language
from cms.grading.steps import EVALUATION_MESSAGES, checker_step, \
    white_diff_fobj_step, realprecision_diff_fobj_step, _DEFAULT_EXP


logger = logging.getLogger(__name__)


EVAL_USER_OUTPUT_FILENAME = "user_output.txt"


def create_sandbox(file_cacher: FileCacher, name: Optional[str] = None) -> Sandbox:
    """Create a sandbox, and return it.

    file_cacher: a file cacher instance.
    name: name to include in the path of the sandbox.

    return: a sandbox.

    raise (JobException): if the sandbox cannot be created.

    """
    try:
        sandbox = Sandbox(file_cacher, name=name)
    except OSError:
        err_msg = "Couldn't create sandbox."
        logger.error(err_msg, exc_info=True)
        raise JobException(err_msg)
    return sandbox


def delete_sandbox(sandbox: Sandbox, job: Job, success: bool | None = None):
    """Delete the sandbox, if the configuration and job was ok.

    sandbox: the sandbox to delete.
    job: the job currently running.
    success: if the job succeeded (no system errors). If not provided,
        job.success is used.

    """
    if success is None:
        success = job.success

    # Archive the sandbox if required
    if job.archive_sandbox:
        sandbox_digest = sandbox.archive()
        if sandbox_digest is not None:
            job.sandbox_digests[sandbox.get_root_path()] = sandbox_digest

    # If the job was not successful, we keep the sandbox around.
    if not success:
        logger.warning("Sandbox %s kept around because job did not succeed.",
                       sandbox.get_root_path())

    delete = success and not config.worker.keep_sandbox and not job.keep_sandbox
    try:
        sandbox.cleanup(delete=delete)
    except OSError:
        err_msg = "Couldn't delete sandbox."
        logger.warning(err_msg, exc_info=True)


def is_manager_for_compilation(filename: str, language: Language) -> bool:
    """Return whether a manager should be copied in the compilation sandbox.

    Only return true for managers required by the language of the submission.

    filename: filename of the manager.
    language: the programming language of the submission.

    return: whether the manager is required for the compilation.

    """
    return (
        any(filename.endswith(source)
            for source in language.source_extensions)
        or any(filename.endswith(header)
               for header in language.header_extensions)
        or any(filename.endswith(obj)
               for obj in language.object_extensions))


def set_configuration_error(job: Job, msg: str, *args: object):
    """Log a configuration error and set the correct results in the job.

    job: the job currently executing
    msg: the message to log.
    args: formatting parameters for msg.

    """
    logger.error("Configuration error: " + msg, *args,
                 extra={"operation": job.info})

    job.success = False
    job.text = None
    if isinstance(job, CompilationJob):
        job.compilation_success = None
    elif isinstance(job, EvaluationJob):
        job.outcome = None
    else:
        raise ValueError("Unexpected type of job: %s.", job.__class__)


def check_executables_number(job: Job, n_executables: int) -> bool:
    """Check that the required number of executables were generated.

    Since it depends only on the task type being correct, a mismatch here
    should not happen. It might be caused (with a lot of effort) by compiling
    under one task type and evaluating under another.

    If there is a mismatch, log and store a configuration error in the job. In
    this case, callers should terminate immediately the current operation.

    job: the job currently running.
    n_executables: the required number of executables.

    return: whether there is the right number of executables in the job.

    """
    if len(job.executables) != n_executables:
        msg = "submission contains %d executables, exactly %d are expected; " \
              "consider invalidating compilations."
        set_configuration_error(job, msg, len(job.executables), n_executables)
        return False
    return True


def check_files_number(job: Job, n_files: int, or_more: bool = False) -> bool:
    """Check that the required number of files were provided by the user.

    A mismatch here is likely caused by having had, at submission time, a wrong
    submission format for the task type.

    If there is a mismatch, log and store a configuration error in the job. In
    this case, callers should terminate immediately the current operation.

    job: the job currently running.
    n_files: the required number of files.
    or_more: whether more than the required number is also fine.

    return: whether there is the right number of files in the job.

    """
    if or_more and len(job.files) < n_files:
        msg = "submission contains %d files, at least %d are required; " \
              "ensure the submission format is correct."
        set_configuration_error(job, msg, len(job.files), n_files)
        return False
    if not or_more and len(job.files) != n_files:
        msg = "submission contains %d files, exactly %d are required; " \
              "ensure the submission format is correct."
        set_configuration_error(job, msg, len(job.files), n_files)
        return False
    return True


def check_manager_present(job: Job, codename: str) -> bool:
    """Check that the required manager was provided in the dataset.

    If not provided, log and store a configuration error in the job. In this
    case, callers should terminate immediately the current operation.

    job: the job currently running.
    codename: the codename of the required manager.

    return: whether the required manager is in the job's managers.

    """
    if codename not in job.managers:
        msg = "dataset is missing manager '%s'."
        set_configuration_error(job, msg, codename)
        return False
    return True


def eval_output(
    file_cacher: FileCacher,
    job: Job,
    checker_codename: Optional[str],
    use_realprecision: bool = False,
    realprecision_exponent: Optional[int] = None,
    user_output_path: Optional[str] = None,
    user_output_digest: Optional[str] = None,
    user_output_filename: str = "",
    extra_args: Optional[list[str]] = None
) -> tuple[bool, Optional[float], Optional[list[str]]]:
    """Evaluate ("check") a user output using a white diff or a checker.

    file_cacher: file cacher to use to get files.
    job: the job triggering this checker run.
    checker_codename: codename of the checker amongst the manager,
        or None to use white diff / real number precision.
    use_realprecision: whether we should use real precision comparator.
    realprecision_exponent: exponent X for tolerance 1e-X when using real
        precision comparison; defaults to 6 (1e-6) if None.
    user_output_path: full path of the user output file, None if
        using the digest (exactly one must be non-None).
    user_output_digest: digest of the user output file, None if
        using the path (exactly one must be non-None).
    user_output_filename: the filename the user was expected to write to,
        or empty if stdout (used to return an error to the user).
    extra_args: additional arguments to pass to the checker

    return: tuple of success (true if the checker was
        able to check the solution successfully), outcome and text (both None
        if success is False).

    """
    if (user_output_path is None) == (user_output_digest is None):
        raise ValueError(
            "Exactly one of user_output_{path,digest} should be None.")
    
    if use_realprecision and realprecision_exponent is None:
        realprecision_exponent = 6
        logger.warning("Real precision exponent is None, defaulting to 6")

    if user_output_path is not None:
        # If a path was passed, it might not exist. First, check it does. We
        # also assume links are potential attacks, and therefore treat them
        # as if the file did not exist.
        if not os.path.exists(user_output_path) \
                or os.path.islink(user_output_path):
            return True, 0.0, [EVALUATION_MESSAGES.get("nooutput").message,
                               user_output_filename]

    if checker_codename is not None:
        if not check_manager_present(job, checker_codename):
            return False, None, None

        # Create a brand-new sandbox just for checking.
        sandbox = create_sandbox(file_cacher, name="check")
        job.sandboxes.append(sandbox.get_root_path())

        # Put user output in the sandbox.
        if user_output_path is not None:
            shutil.copyfile(user_output_path,
                            sandbox.relative_path(EVAL_USER_OUTPUT_FILENAME))
        else:
            # this assertion is verified by the check at the start of the
            # function, but the type checker isn't smart enough for that
            assert user_output_digest is not None
            sandbox.create_file_from_storage(EVAL_USER_OUTPUT_FILENAME,
                                             user_output_digest)

        checker_digest = job.managers[checker_codename].digest \
            if checker_codename in job.managers else None
        success, outcome, text = checker_step(
            sandbox, checker_digest, job.input, job.output,
            EVAL_USER_OUTPUT_FILENAME, extra_args)

        delete_sandbox(sandbox, job, success)
        return success, outcome, text

    else:
        if user_output_path is not None:
            user_output_fobj = open(user_output_path, "rb")
        else:
            user_output_fobj = file_cacher.get_file(user_output_digest)
        with user_output_fobj:
            with file_cacher.get_file(job.output) as correct_output_fobj:
                if use_realprecision:
                    outcome, text = realprecision_diff_fobj_step(
                        user_output_fobj, correct_output_fobj, realprecision_exponent)
                else:
                    outcome, text = white_diff_fobj_step(
                        user_output_fobj, correct_output_fobj)
        return True, outcome, text


def get_allowed_manager_basenames(task_type: Optional[str]) -> set[str]:
    """Get the set of manager basenames that should be auto-compiled.

    Uses the task type class to discover CHECKER_CODENAME and MANAGER_FILENAME
    attributes, falling back to a default set if the task type is unknown.

    task_type: the task type name (e.g., "Batch", "Communication"), or None.

    return: set of allowed manager basenames (e.g., {"checker", "manager"}).

    """
    from cms.grading.tasktypes import get_task_type_class
    
    allowed_basenames = set()
    if task_type:
        try:
            tt_cls = get_task_type_class(task_type)
            if hasattr(tt_cls, "CHECKER_CODENAME"):
                allowed_basenames.add(getattr(tt_cls, "CHECKER_CODENAME"))
            if hasattr(tt_cls, "MANAGER_FILENAME"):
                allowed_basenames.add(getattr(tt_cls, "MANAGER_FILENAME"))
        except Exception:
            pass
    
    if not allowed_basenames:
        allowed_basenames = {"checker", "manager"}
    
    return allowed_basenames


def compile_manager_bytes(
    file_cacher: FileCacher,
    source_filename: str,
    source_bytes: bytes,
    output_basename: str,
    sandbox_name: str = "compile",
    for_evaluation: bool = True,
    notify: Optional[Callable[[str, str], None]] = None
) -> tuple[bool, Optional[bytes], Optional[dict]]:
    """Compile a manager source file and return the compiled bytes.

    This is a shared helper for compiling managers (checkers, graders, etc.)
    used by both the admin interface and loaders.

    file_cacher: FileCacher instance for sandbox operations.
    source_filename: name of the source file (e.g., "checker.cpp").
    source_bytes: content of the source file.
    output_basename: name for the compiled binary (e.g., "checker").
    sandbox_name: name prefix for the sandbox (default: "compile").
    for_evaluation: whether to compile for evaluation (default: True).
    notify: optional callback(title: str, text: str) to report errors.

    return: tuple (success, compiled_bytes, stats) where:
        - success: True if compilation succeeded, False otherwise
        - compiled_bytes: the compiled binary bytes if successful, None otherwise
        - stats: compilation statistics dict if available, None otherwise

    """
    from cms.grading.languagemanager import filename_to_language
    from cms.grading.language import CompiledLanguage
    from cms.grading.steps.compilation import compilation_step
    
    try:
        language = filename_to_language(source_filename)
    except Exception:
        msg = f"Could not detect language for {source_filename}"
        logger.warning(msg)
        if notify:
            notify("Manager compilation skipped", msg)
        return False, None, None

    if not isinstance(language, CompiledLanguage):
        msg = f"{source_filename} is not a compiled language"
        logger.warning(msg)
        if notify:
            notify("Manager compilation skipped", msg)
        return False, None, None

    sandbox = None
    try:
        sandbox = create_sandbox(file_cacher, name=sandbox_name)
        
        safe_src = os.path.basename(source_filename)
        sandbox.create_file_from_string(safe_src, source_bytes)
        
        commands = language.get_compilation_commands(
            [safe_src], output_basename, for_evaluation=for_evaluation)
        
        box_success, compilation_success, text, stats = \
            compilation_step(sandbox, commands)
        
        if not box_success:
            msg = f"Sandbox error during compilation of {source_filename}"
            logger.error(msg)
            if notify:
                notify("Manager compilation failed", 
                       f"{msg}. See logs for details.")
            return False, None, stats
        
        if not compilation_success:
            stdout = stats.get("stdout", "") if stats else ""
            stderr = stats.get("stderr", "") if stats else ""
            
            error_details = f"Compilation failed for {source_filename}.\n"
            if commands:
                error_details += f"Command: {commands}\n"
            if stdout:
                error_details += f"Stdout:\n{stdout}\n"
            if stderr:
                error_details += f"Stderr:\n{stderr}"
            
            logger.error(error_details)
            if notify:
                notify("Manager compilation failed", error_details)
            return False, None, stats
        
        compiled_bytes = sandbox.get_file_to_string(output_basename, maxlen=None)
        return True, compiled_bytes, stats
        
    except Exception as error:
        msg = f"Error compiling {source_filename}: {error}"
        logger.error(msg, exc_info=True)
        if notify:
            notify("Manager compilation error", msg)
        return False, None, None
    finally:
        if sandbox:
            sandbox.cleanup(delete=True)
