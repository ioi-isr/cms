#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
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

"""Task type for output only tasks.

"""

import logging

from cms.grading.Job import Job
from cms.grading.ParameterTypes import ParameterTypeChoice, ParameterTypeInt
from . import TaskType, eval_output


logger = logging.getLogger(__name__)


# Dummy function to mark translatable string.
def N_(message):
    return message


class OutputOnly(TaskType):
    """Task type class for output only tasks, with submission composed
    of testcase_number text files, to be evaluated diffing or using a
    comparator.

    Parameters are a list of string with one element (for future
    possible expansions), which maybe 'diff' or 'comparator', meaning that
    the evaluation is done via white diff or via a comparator.

    """
    # Codename of the checker, if it is used.
    CHECKER_CODENAME = "checker"
    # Template for the filename of the output files provided by the user; %s
    # represent the testcase codename.
    USER_OUTPUT_FILENAME_TEMPLATE = "output_%s.txt"

    # Constants used in the parameter definition.
    OUTPUT_EVAL_DIFF = "diff"
    OUTPUT_EVAL_CHECKER = "comparator"
    OUTPUT_EVAL_REALPREC = "realprecision"

    # Other constants to specify the task type behaviour and parameters.
    ALLOW_PARTIAL_SUBMISSION = True

    _EVALUATION = ParameterTypeChoice(
        "Output evaluation",
        "output_eval",
        "",
        {OUTPUT_EVAL_DIFF: "Outputs compared with white diff",
         OUTPUT_EVAL_CHECKER: "Outputs are compared by a comparator",
         OUTPUT_EVAL_REALPREC: "Outputs compared as real numbers (with precision of 1e-X)"})

    _REALPREC_EXP = ParameterTypeInt(
        "Real precision exponent X (precision is 1e-X)",
        "realprec_exp",
        "If using real-number comparison, specify X in 1e-X (e.g., 6)")

    ACCEPTED_PARAMETERS = [_EVALUATION, _REALPREC_EXP]

    @classmethod
    def parse_handler(cls, handler, prefix):
        """Parse parameters from AWS forms with optional exponent.

        When output_eval == 'realprecision', an exponent may be provided; if
        missing, we omit it and default later. For other modes, return just
        the single parameter to preserve legacy shape.
        """
        out_eval = cls._EVALUATION.parse_handler(handler, prefix)
        exp = None
        try:
            exp = cls._REALPREC_EXP.parse_handler(handler, prefix)
        except Exception:
            exp = None
        if out_eval == cls.OUTPUT_EVAL_REALPREC:
            return [out_eval] if exp is None else [out_eval, exp]
        else:
            return [out_eval]

    @property
    def name(self):
        """See TaskType.name."""
        # TODO add some details if a comparator is used, etc...
        return "Output only"

    testable = False

    def __init__(self, parameters):
        # Accept legacy single-parameter lists by appending default exponent 6
        if isinstance(parameters, list) and len(parameters) == 1:
            parameters = list(parameters) + [6]
        super().__init__(parameters)
        self.output_eval: str = self.parameters[0]
        self.realprec_exp: int = int(self.parameters[1])

    def validate_parameters(self):
        # Override to allow backward compatibility: len 1 or 2
        if not isinstance(self.parameters, list):
            raise ValueError(
                "Task type parameters for %s are not a list" % self.__class__)
        if len(self.parameters) not in (1, 2):
            raise ValueError(
                "Task type %s should have 1 or 2 parameters, received %s" %
                (self.__class__, len(self.parameters)))
        # First param must be a valid choice
        OutputOnly._EVALUATION.validate(self.parameters[0])
        # Second param: default to 6 if missing, otherwise int
        if len(self.parameters) == 1:
            self.parameters.append(6)
        try:
            self.parameters[1] = int(self.parameters[1])
        except Exception:
            raise ValueError("Real precision exponent must be an integer")

    def get_compilation_commands(self, submission_format):
        """See TaskType.get_compilation_commands."""
        return None

    def get_user_managers(self):
        """See TaskType.get_user_managers."""
        return []

    def get_auto_managers(self):
        """See TaskType.get_auto_managers."""
        return []

    def _uses_checker(self) -> bool:
        return self.output_eval == OutputOnly.OUTPUT_EVAL_CHECKER

    def _uses_realprecision(self) -> bool:
        return self.output_eval == self.OUTPUT_EVAL_REALPREC

    @staticmethod
    def _get_user_output_filename(job: Job):
        return OutputOnly.USER_OUTPUT_FILENAME_TEMPLATE % \
            job.operation.testcase_codename

    def compile(self, job, file_cacher):
        """See TaskType.compile."""
        # No compilation needed.
        job.success = True
        job.compilation_success = True
        job.text = [N_("No compilation needed")]
        job.plus = {}

    def evaluate(self, job, file_cacher):
        """See TaskType.evaluate."""
        user_output_filename = self._get_user_output_filename(job)

        # Since we allow partial submission, if the file is not
        # present we report that the outcome is 0.
        if user_output_filename not in job.files:
            job.success = True
            job.outcome = "0.0"
            job.text = [N_("File not submitted")]
            job.plus = {}
            return

        # First and only step: eval the user output.
        box_success, outcome, text = eval_output(
            file_cacher, job,
            OutputOnly.CHECKER_CODENAME if self._uses_checker() else None,
            use_realprecision=self._uses_realprecision(),
            user_output_digest=job.files[user_output_filename].digest)

        # Fill in the job with the results.
        job.success = box_success
        job.outcome = str(outcome) if outcome is not None else None
        job.text = text
        # There is no actual evaluation, so no statistics.
        job.plus = {} if box_success else None
