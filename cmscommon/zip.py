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

"""Utilities for safe zip file handling.

"""

import os
import zipfile


def safe_extract_zip(zip_ref, extract_dir):
    """Safely extract a zip file, preventing zip slip attacks.

    Validates that all extracted paths stay within the target directory.
    Also rejects symbolic links within the archive to prevent symlink-based
    attacks that could point outside the extraction directory.
    Raises ValueError if a malicious path or symlink is detected.

    zip_ref: an open zipfile.ZipFile object
    extract_dir: the directory to extract files into

    """
    extract_dir_real = os.path.realpath(extract_dir)

    for member_info in zip_ref.infolist():
        member = member_info.filename

        # Reject symbolic links (external_attr high bits indicate Unix mode)
        # Unix symlinks have mode 0o120000 (S_IFLNK)
        unix_mode = (member_info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise ValueError(f"Symbolic link not allowed in zip archive: {member}")

        # Normalize the member path (handle both / and \ separators)
        member_path = os.path.normpath(member)

        # Reject absolute paths
        if os.path.isabs(member_path):
            raise ValueError(f"Unsafe absolute path in zip archive: {member}")

        # Reject paths that try to escape (e.g., ../../../etc/passwd)
        if member_path.startswith('..') or member_path.startswith(os.sep + '..'):
            raise ValueError(f"Unsafe path in zip archive: {member}")

        # Compute the final extraction path and verify it's within extract_dir
        target_path = os.path.realpath(os.path.join(extract_dir, member_path))
        if not target_path.startswith(extract_dir_real + os.sep) and \
                target_path != extract_dir_real:
            raise ValueError(f"Unsafe path in zip archive: {member}")

    # All paths validated, now extract
    zip_ref.extractall(extract_dir)
