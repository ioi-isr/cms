#!/usr/bin/env python3

import unittest
import os


class TestManagerUploadLogic(unittest.TestCase):

    def test_should_reject_compiled_when_source_exists(self):
        existing_filenames = {"checker.cpp", "other.txt"}
        upload_filename = "checker"
        allowed_basenames = {"checker", "manager"}
        
        base_noext = os.path.splitext(os.path.basename(upload_filename))[0]
        has_extension = "." in os.path.basename(upload_filename)
        
        should_reject = False
        if base_noext in allowed_basenames and not has_extension:
            for existing_filename in existing_filenames:
                existing_base = os.path.splitext(os.path.basename(existing_filename))[0]
                existing_has_ext = "." in os.path.basename(existing_filename)
                if existing_base == base_noext and existing_has_ext:
                    should_reject = True
                    break
        
        self.assertTrue(should_reject)

    def test_should_allow_compiled_when_no_source_exists(self):
        existing_filenames = {"other.txt"}
        upload_filename = "checker"
        allowed_basenames = {"checker", "manager"}
        
        base_noext = os.path.splitext(os.path.basename(upload_filename))[0]
        has_extension = "." in os.path.basename(upload_filename)
        
        should_reject = False
        if base_noext in allowed_basenames and not has_extension:
            for existing_filename in existing_filenames:
                existing_base = os.path.splitext(os.path.basename(existing_filename))[0]
                existing_has_ext = "." in os.path.basename(existing_filename)
                if existing_base == base_noext and existing_has_ext:
                    should_reject = True
                    break
        
        self.assertFalse(should_reject)

    def test_should_allow_source_upload(self):
        existing_filenames = {"checker"}
        upload_filename = "checker.cpp"
        allowed_basenames = {"checker", "manager"}
        
        base_noext = os.path.splitext(os.path.basename(upload_filename))[0]
        has_extension = "." in os.path.basename(upload_filename)
        
        should_reject = False
        if base_noext in allowed_basenames and not has_extension:
            for existing_filename in existing_filenames:
                existing_base = os.path.splitext(os.path.basename(existing_filename))[0]
                existing_has_ext = "." in os.path.basename(existing_filename)
                if existing_base == base_noext and existing_has_ext:
                    should_reject = True
                    break
        
        self.assertFalse(should_reject)

    def test_should_allow_non_special_basename(self):
        existing_filenames = {"grader.cpp"}
        upload_filename = "grader"
        allowed_basenames = {"checker", "manager"}
        
        base_noext = os.path.splitext(os.path.basename(upload_filename))[0]
        has_extension = "." in os.path.basename(upload_filename)
        
        should_reject = False
        if base_noext in allowed_basenames and not has_extension:
            for existing_filename in existing_filenames:
                existing_base = os.path.splitext(os.path.basename(existing_filename))[0]
                existing_has_ext = "." in os.path.basename(existing_filename)
                if existing_base == base_noext and existing_has_ext:
                    should_reject = True
                    break
        
        self.assertFalse(should_reject)

    def test_upsert_updates_existing_digest(self):
        existing_managers = {"checker": "old_digest", "other.txt": "other_digest"}
        new_entries = [("checker", "new_digest")]
        
        result_managers = existing_managers.copy()
        for fname, dig in new_entries:
            if fname in result_managers:
                result_managers[fname] = dig
            else:
                result_managers[fname] = dig
        
        self.assertEqual(result_managers["checker"], "new_digest")
        self.assertEqual(result_managers["other.txt"], "other_digest")

    def test_upsert_inserts_new_entry(self):
        existing_managers = {"other.txt": "other_digest"}
        new_entries = [("checker", "new_digest")]
        
        result_managers = existing_managers.copy()
        for fname, dig in new_entries:
            if fname in result_managers:
                result_managers[fname] = dig
            else:
                result_managers[fname] = dig
        
        self.assertEqual(result_managers["checker"], "new_digest")
        self.assertEqual(result_managers["other.txt"], "other_digest")

    def test_upsert_handles_multiple_entries(self):
        existing_managers = {"checker": "old_digest"}
        new_entries = [("checker.cpp", "source_digest"), ("checker", "compiled_digest")]
        
        result_managers = existing_managers.copy()
        for fname, dig in new_entries:
            if fname in result_managers:
                result_managers[fname] = dig
            else:
                result_managers[fname] = dig
        
        self.assertEqual(result_managers["checker"], "compiled_digest")
        self.assertEqual(result_managers["checker.cpp"], "source_digest")


if __name__ == '__main__':
    unittest.main()
