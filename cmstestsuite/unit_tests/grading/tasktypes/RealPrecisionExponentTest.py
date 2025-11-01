#!/usr/bin/env python3

"""Tests for deriving realprecision exponent at evaluation-time.
Covers eval_output() reading job.task_type_parameters to pick 1e-<X>.
"""

import unittest
from io import BytesIO
from unittest.mock import MagicMock

from cms.grading.Job import EvaluationJob
from cms.grading.tasktypes.util import eval_output


class TestRealPrecisionExponentDerivation(unittest.TestCase):
    def make_job(self, task_type_parameters):
        # Minimal EvaluationJob; only job.output and task_type_parameters are read
        job = EvaluationJob()
        job.output = "correct"
        job.task_type_parameters = task_type_parameters
        return job

    def make_file_cacher(self, user_text: str, correct_text: str):
        fc = MagicMock()
        def get_file_side(digest):
            if digest == "user":
                return BytesIO(user_text.encode("utf-8"))
            elif digest == "correct":
                return BytesIO(correct_text.encode("utf-8"))
            else:
                raise AssertionError("Unexpected digest %r" % digest)
        fc.get_file.side_effect = get_file_side
        return fc

    def test_uses_custom_exponent(self):
        # With exponent 4, tolerance 1e-4: a 5e-5 delta should pass
        job = self.make_job(["realprecision", 4])
        fc = self.make_file_cacher(user_text="0.00005", correct_text="0")
        success, outcome, text = eval_output(fc, job, None, use_realprecision=True, user_output_digest="user")
        self.assertTrue(success)
        self.assertEqual(outcome, 1.0)

    def test_defaults_to_6_when_missing(self):
        # No exponent given; default 1e-6: 5e-5 should fail
        job = self.make_job(["realprecision"])  # legacy single parameter
        fc = self.make_file_cacher(user_text="0.00005", correct_text="0")
        success, outcome, text = eval_output(fc, job, None, use_realprecision=True, user_output_digest="user")
        self.assertTrue(success)
        self.assertEqual(outcome, 0.0)

    def test_uses_custom_exponent_batch_style(self):
        # Batch-style parameters: [compilation, io, 'realprecision', X]
        job = self.make_job(["alone", ["", ""], "realprecision", 4])
        fc = self.make_file_cacher(user_text="0.00005", correct_text="0")
        success, outcome, text = eval_output(
            fc, job, None, use_realprecision=True, user_output_digest="user"
        )
        self.assertTrue(success)
        self.assertEqual(outcome, 1.0)

    def test_defaults_to_6_when_missing_batch_style(self):
        # Batch-style parameters without exponent should default to 1e-6
        job = self.make_job(["alone", ["", ""], "realprecision"])  # missing exponent
        fc = self.make_file_cacher(user_text="0.00005", correct_text="0")
        success, outcome, text = eval_output(
            fc, job, None, use_realprecision=True, user_output_digest="user"
        )
        self.assertTrue(success)
        self.assertEqual(outcome, 0.0)


if __name__ == "__main__":
    unittest.main()
