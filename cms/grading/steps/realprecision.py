#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2025 Ron Ryvchin <ryvchin@gmail.com>
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

"""High level functions to perform standardized real-number comparison.

Policy:
- Tokenization: streams are split into alternating text and fixed-format decimal tokens.
  Fixed-format decimals (no exponent, no inf/nan):
  Accepted examples: "12", "12.", "12.34", ".5", "-0.0", "+3.000"
  Rejected examples: "1e-3", "nan", "inf", "0x1.8p3"
- Text comparison: non-numeric text is compared using white-diff semantics
  (whitespace differences are ignored, but other text differences cause failure).
- Number comparison: numeric tokens are compared with absolute/relative tolerance.
- Both streams must have the same sequence of text/number token types and the same
  number of numeric tokens. Text tokens must match (up to whitespace), and numeric
  tokens must match within tolerance.
"""

import logging
import re
import typing

from cms.grading.Sandbox import Sandbox

from .evaluation import EVALUATION_MESSAGES


logger = logging.getLogger(__name__)


# Fixed-format decimals only (bytes regex, no exponents/inf/nan).
_FIXED_DEC_PATTERN = rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'

# default precision is 10^-6
_DEFAULT_EXP = 6

def _compare_real_pair(a: float, b: float, eps: float) -> bool:
    """Return True if a and b match within absolute/relative tolerance."""
    diff = abs(a - b)
    tol = eps * max(1.0, abs(a), abs(b))
    return diff <= tol


def _white_diff_canonicalize(string: bytes) -> bytes:
    """Canonicalize text for white-diff comparison.
    
    Strips leading/trailing whitespace and collapses runs of whitespace
    into single spaces, matching white_diff behavior.
    
    string: the bytes string to canonicalize.
    return: the canonicalized string.
    """
    string = re.sub(rb'\s+', b' ', string).strip()
    return string


def _real_numbers_compare(
    output: typing.BinaryIO, correct: typing.BinaryIO, exponent: int = _DEFAULT_EXP
) -> bool:
    """Compare two output files using white-diff for text and tolerance for numbers.

    Two files are equal if:
    1. They have the same sequence of text/number segments under the same splitting.
    2. Text segments match up to whitespace differences (white-diff semantics).
    3. Numeric segments match within absolute/relative tolerance.
    """
    output_data = output.read()
    correct_data = correct.read()

    # Split into [text, number, text, number, ...] parts;
    # even indices: text, odd indices: numbers.
    out_parts = re.split(rb'(' + _FIXED_DEC_PATTERN + rb')', output_data)
    cor_parts = re.split(rb'(' + _FIXED_DEC_PATTERN + rb')', correct_data)

    if len(out_parts) != len(cor_parts):
        return False

    eps = 10 ** (-(int(exponent)))

    return all(
        (
            _white_diff_canonicalize(out_part) == _white_diff_canonicalize(cor_part)
            if i % 2 == 0
            else _compare_real_pair(float(out_part), float(cor_part), eps)
        )
        for i, (out_part, cor_part) in enumerate(zip(out_parts, cor_parts))
    )


def realprecision_diff_fobj_step(
    output_fobj: typing.BinaryIO, correct_output_fobj: typing.BinaryIO, exponent: int = _DEFAULT_EXP
) -> tuple[float, list[str]]:
    """Compare user output and correct output by extracting the fixed
    floating point format number, and comparing their values.

    It gives an outcome 1.0 if the output and the reference output have
    an absoulte or a relative smaller or equal to 10^-6 and 0.0 if they don't.
    Calling this function means that the output file exists.

    output_fobj: file for the user output, opened in binary mode.
    correct_output_fobj: file for the correct output, opened in
        binary mode.

    return: the outcome as above and a description text.

    """
    if _real_numbers_compare(output_fobj, correct_output_fobj, exponent):
        return 1.0, [EVALUATION_MESSAGES.get("success").message]
    else:
        return 0.0, [EVALUATION_MESSAGES.get("wrong").message]


def realprecision_diff_step(
    sandbox: Sandbox, output_filename: str, correct_output_filename: str,
    exponent: int = _DEFAULT_EXP
) -> tuple[float, list[str]]:
    """Compare user output and correct output by extracting the fixed
    floating point format number, and comparing their values.

    It gives an outcome 1.0 if the output and the reference output have
    an absoulte or a relative smaller or equal to 10^-exponent and 0.0 
    if they don't (or if the output doesn't exist).

    sandbox: the sandbox we consider.
    output_filename: the filename of user's output in the sandbox.
    correct_output_filename: the same with reference output.
    exponent: optional precision exponent X for tolerance 1e-X (default: 6).

    return: the outcome as above and a description text.

    """
    if sandbox.file_exists(output_filename):
        with sandbox.get_file(output_filename) as out_file, \
             sandbox.get_file(correct_output_filename) as res_file:
            return realprecision_diff_fobj_step(out_file, res_file, exponent)
    else:
        return 0.0, [
            EVALUATION_MESSAGES.get("nooutput").message, output_filename]
