#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/

task_info = {
    "name": "communication_stdio_cpp",
    "title": "Test Communication Task with C++ manager",
    "official_language": "",
    "submission_format_choice": "other",
    "submission_format": "communication.%l",
    "time_limit_{{dataset_id}}": "1.0",
    "memory_limit_{{dataset_id}}": "128",
    "task_type_{{dataset_id}}": "Communication",
    "TaskTypeOptions_{{dataset_id}}_Communication_num_processes": "1",
    "TaskTypeOptions_{{dataset_id}}_Communication_compilation": "alone",
    "TaskTypeOptions_{{dataset_id}}_Communication_user_io": "std_io",
    "score_type_{{dataset_id}}": "Sum",
    "score_type_parameters_{{dataset_id}}": "50",
}

managers = [
    "manager.cpp",
]

test_cases = [
    ("1.in", "1.out", True),
    ("2.in", "2.out", False),
]
