#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/

task_info = {
    "name": "outputonly_comparator_cpp",
    "title": "Test OutputOnly Task with C++ comparator",
    "official_language": "",
    "submission_format_choice": "other",
    "submission_format": "output_000.txt, output_001.txt",
    "time_limit_{{dataset_id}}": "0.5",
    "memory_limit_{{dataset_id}}": "128",
    "task_type_{{dataset_id}}": "OutputOnly",
    "TaskTypeOptions_{{dataset_id}}_OutputOnly_output_eval": "comparator",
    "score_type_{{dataset_id}}": "Sum",
    "score_type_parameters_{{dataset_id}}": "50",
}

# Upload a C++ checker source; the admin handler compiles it to 'checker'.
managers = [
    "checker.cpp",
]

test_cases = [
    ("input_000.txt", "output_000.txt", True),
    ("input_001.txt", "output_001.txt", False),
]

