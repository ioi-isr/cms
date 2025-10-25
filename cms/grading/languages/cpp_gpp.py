#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2016 Stefano Maggiolo <s.maggiolo@gmail.com>
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

"""Base C++ programming language definition with configurable standard."""

from cms.grading import CompiledLanguage


__all__ = ["CppGppBase"]


class CppGppBase(CompiledLanguage):
    """Base class for C++ programming language compiled with g++.
    
    This class provides common functionality for all C++ standards,
    with the specific standard configured via the cpp_standard parameter.
    
    """

    def __init__(self, cpp_standard: str):
        """Initialize with a specific C++ standard.
        
        cpp_standard: the C++ standard version (e.g., "11", "14", "17", "20")
        
        """
        self.cpp_standard = cpp_standard

    @property
    def name(self):
        """See Language.name."""
        return f"C++{self.cpp_standard} / g++"

    @property
    def source_extensions(self):
        """See Language.source_extensions."""
        return [".cpp", ".cc", ".cxx", ".c++", ".C"]

    @property
    def header_extensions(self):
        """See Language.header_extensions."""
        return [".h"]

    @property
    def object_extensions(self):
        """See Language.object_extensions."""
        return [".o"]

    def get_compilation_commands(self,
                                 source_filenames, executable_filename,
                                 for_evaluation=True):
        """See Language.get_compilation_commands."""
        command = ["/usr/bin/g++"]
        if for_evaluation:
            command += ["-DEVAL"]
        command += [f"-std=gnu++{self.cpp_standard}", "-O2", "-pipe", "-static",
                    "-s", "-o", executable_filename]
        command += source_filenames
        return [command]
