#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2016 Peyman Jabbarzade Ganje <peyman.jabarzade@gmail.com>
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

"""Shared utilities for testcase collection and pairing.

This module provides pure, reusable helpers for matching and pairing
testcase input/output files from various sources (directories, zip files).
These utilities are used by CLI tools (AddTestcases), loaders (italy_yaml),
and admin handlers.

"""

import os
import re
import zipfile
from typing import Iterable, Pattern


def compile_template_regex(template: str) -> Pattern:
    """Compile a testcase filename template into a regex pattern.
    
    template: template string with exactly one '*' placeholder (e.g., "input.*", "*.in")
    
    return: compiled regex pattern that matches filenames and captures the codename
    
    raise: ValueError if template doesn't have exactly one '*' placeholder
    
    The '*' placeholder is replaced with a capturing group that matches any characters.
    This is the canonical implementation used by both the admin UI and loaders.
    
    """
    if template.count('*') != 1:
        raise ValueError(
            "Template must have exactly one '*' placeholder, got: %s" % template)
    
    return re.compile(re.escape(template).replace("\\*", "(.*)") + "$")


def pair_names(
    names: Iterable[str],
    input_re: Pattern,
    output_re: Pattern,
    *,
    match_on_basename: bool = False
) -> dict[str, tuple[str, str]]:
    """Core pairing algorithm for matching input and output files by codename.
    
    names: iterable of file names (paths or basenames) to pair
    input_re: compiled regex pattern for matching input files
    output_re: compiled regex pattern for matching output files
    match_on_basename: if True, apply regex to os.path.basename(name) but return original names
    
    return: dict mapping codename to (input_name, output_name) tuples
    
    raise: ValueError if pairs are incomplete (missing inputs or outputs)
    
    This is a pure function that doesn't touch the filesystem or database.
    It only performs name matching and validation.
    
    """
    inputs = {}
    outputs = {}
    
    for name in names:
        match_target = os.path.basename(name) if match_on_basename else name
        
        input_match = input_re.match(match_target)
        if input_match:
            codename = input_match.group(1)
            inputs[codename] = name
        
        output_match = output_re.match(match_target)
        if output_match:
            codename = output_match.group(1)
            outputs[codename] = name
    
    input_codenames = set(inputs.keys())
    output_codenames = set(outputs.keys())
    
    if input_codenames != output_codenames:
        missing_outputs = input_codenames - output_codenames
        missing_inputs = output_codenames - input_codenames
        error_parts = []
        if missing_outputs:
            error_parts.append("Missing outputs for: %s" % ", ".join(sorted(missing_outputs)))
        if missing_inputs:
            error_parts.append("Missing inputs for: %s" % ", ".join(sorted(missing_inputs)))
        raise ValueError("Testcase pairing failed. %s" % "; ".join(error_parts))
    
    return {codename: (inputs[codename], outputs[codename])
            for codename in sorted(inputs.keys())}


def pair_testcases_in_directory(
    dir_path: str,
    input_re: Pattern,
    output_re: Pattern
) -> dict[str, tuple[str, str]]:
    """Pair input and output files from a directory using regex patterns.
    
    dir_path: path to directory containing testcase files
    input_re: compiled regex pattern for matching input files
    output_re: compiled regex pattern for matching output files
    
    return: dict mapping codename to (abs_input_path, abs_output_path) tuples
    
    raise: ValueError if pairs are incomplete or template validation fails
    
    This is a thin wrapper around pair_names that handles directory listing
    and converts relative filenames to absolute paths.
    
    """
    filenames = os.listdir(dir_path)
    
    paired = pair_names(filenames, input_re, output_re, match_on_basename=True)
    
    return {codename: (os.path.join(dir_path, input_name),
                       os.path.join(dir_path, output_name))
            for codename, (input_name, output_name) in paired.items()}


def pair_testcases_in_zip(
    zfp: zipfile.ZipFile,
    input_re: Pattern,
    output_re: Pattern
) -> dict[str, tuple[str, str]]:
    """Pair input and output files from a zip archive using regex patterns.
    
    zfp: open ZipFile object
    input_re: compiled regex pattern for matching input files
    output_re: compiled regex pattern for matching output files
    
    return: dict mapping codename to (name_in_zip, name_in_zip) tuples
    
    raise: ValueError if pairs are incomplete or template validation fails
    
    This is a thin wrapper around pair_names that handles zip file listing.
    Names are matched against full paths in the zip (not basenames) to preserve
    current semantics of import_testcases_from_zipfile.
    
    """
    names = zfp.namelist()
    
    return pair_names(names, input_re, output_re, match_on_basename=False)
