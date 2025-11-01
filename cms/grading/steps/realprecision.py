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


# Fixed-format decimals only (bytes regex).
_FIXED_DEC_RE = re.compile(rb'[+-]?(?:\d+(?:\.\d*)?|\.\d+)')

_WHITES = [b' ', b'\t', b'\n', b'\x0b', b'\x0c', b'\r']
# default precision is 10^-6
_DEFAULT_EXP = 6

def _compare_real_pair(a: float, b: float, eps: float) -> bool:
    """Return True if a and b match within absolute/relative tolerance."""
    diff = abs(a - b)
    tol = eps * max(1.0, abs(a), abs(b))
    return diff <= tol


def _parse_fixed(token: bytes) -> float | None:
    """Parse a fixed-format decimal token into float; return None on failure."""
    # The regex already excludes exponents/inf/nan; this is defensive.
    try:
        # Decode strictly ASCII; reject weird Unicode digits.
        s = token.decode("ascii", errors="strict")
        # float() accepts exponent, but regex guarantees none are present.
        return float(s)
    except Exception:
        return None


def _white_diff_canonicalize(string: bytes) -> bytes:
    """Canonicalize text for white-diff comparison.
    
    Strips leading/trailing whitespace and collapses runs of whitespace
    into single spaces, matching white_diff behavior.
    
    string: the bytes string to canonicalize.
    return: the canonicalized string.
    """
    for char in _WHITES[1:]:
        string = string.replace(char, _WHITES[0])
    
    string = _WHITES[0].join([x for x in string.split(_WHITES[0]) if len(x) > 0])
    return string


def _tokenize_stream(data: bytes) -> list[tuple[str, bytes | float]]:
    """Tokenize a byte stream into alternating text and number tokens.
    
    Returns a list of (type, value) tuples where type is either 'text' or 'number'.
    For 'text' tokens, value is the canonicalized bytes.
    For 'number' tokens, value is the parsed float.
    
    data: the byte stream data.
    return: list of (type, value) tuples.
    """
    tokens = []
    pos = 0
    
    for match in _FIXED_DEC_RE.finditer(data):
        if match.start() > pos:
            text = data[pos:match.start()]
            canonical = _white_diff_canonicalize(text)
            if len(canonical) > 0:
                tokens.append(('text', canonical))
        
        num_bytes = match.group(0)
        num_val = _parse_fixed(num_bytes)
        if num_val is not None:
            tokens.append(('number', num_val))
        
        pos = match.end()
    
    if pos < len(data):
        text = data[pos:]
        canonical = _white_diff_canonicalize(text)
        if len(canonical) > 0:
            tokens.append(('text', canonical))
    
    return tokens


def _real_numbers_compare(
    output: typing.BinaryIO, correct: typing.BinaryIO, exponent: int = _DEFAULT_EXP
) -> bool:
    """Compare two output files using white-diff for text and tolerance for numbers.
    
    Two files are equal if:
    1. They have the same sequence of text/number token types.
    2. Text tokens match up to whitespace differences (white-diff semantics).
    3. Numeric tokens match within absolute/relative tolerance.
    
    This matches the behavior of a C++ program reading numbers with cin >> double,
    where non-numeric text differences would cause the comparison to fail.

    output: the user output file to compare.
    correct: the correct output file to compare.
    exponent: optional precision exponent X for tolerance 1e-X (default: 6).
    return: True if the files match as explained above.
    """
    output_data = output.read()
    correct_data = correct.read()
    
    output_tokens = _tokenize_stream(output_data)
    correct_tokens = _tokenize_stream(correct_data)
    
    if len(output_tokens) != len(correct_tokens):
        return False
    
    eps = 10 ** (-(int(exponent)))
    for (out_type, out_val), (cor_type, cor_val) in zip(output_tokens, correct_tokens):
        if out_type != cor_type:
            return False
        
        if out_type == 'text':
            if out_val != cor_val:
                return False
        else:  # out_type == 'number'
            # Number tokens must match within tolerance.
            if not _compare_real_pair(out_val, cor_val, eps):
                return False
    
    return True


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
