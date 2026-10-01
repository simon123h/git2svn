import unittest
from pathlib import Path

import git2svn


class TestParseRefArguments(unittest.TestCase):
    def test_single_commit(self):
        is_single, start, end = git2svn.parse_ref_arguments("abc1234")
        self.assertTrue(is_single)
        self.assertEqual(start, "abc1234")
        self.assertIsNone(end)

    def test_range_dot_notation(self):
        is_single, start, end = git2svn.parse_ref_arguments("main..feature")
        self.assertFalse(is_single)
        self.assertEqual(start, "main")
        self.assertEqual(end, "feature")

    def test_range_two_arguments(self):
        is_single, start, end = git2svn.parse_ref_arguments("main", "feature")
        self.assertFalse(is_single)
        self.assertEqual(start, "main")
        self.assertEqual(end, "feature")

    def test_empty_ref_error(self):
        with self.assertRaises(ValueError):
            git2svn.parse_ref_arguments(None)


class TestParseNameStatus(unittest.TestCase):
    def test_parse_name_status_tabular(self):
        output = """
A\tsrc/new_file.py
M\tsrc/existing.py
D\tsrc/old_file.py
R100\tsrc/legacy.py\tsrc/refactored.py
C090\tsrc/template.py\tsrc/instance.py
"""
        changes = git2svn.parse_name_status(output)
        self.assertEqual(len(changes), 5)

        self.assertTrue(changes[0].is_added)
        self.assertEqual(changes[0].path, Path("src/new_file.py"))

        self.assertTrue(changes[1].is_modified)
        self.assertEqual(changes[1].path, Path("src/existing.py"))

        self.assertTrue(changes[2].is_deleted)
        self.assertEqual(changes[2].path, Path("src/old_file.py"))

        self.assertTrue(changes[3].is_renamed)
        self.assertEqual(changes[3].old_path, Path("src/legacy.py"))
        self.assertEqual(changes[3].path, Path("src/refactored.py"))

        self.assertTrue(changes[4].is_copied)
        self.assertEqual(changes[4].old_path, Path("src/template.py"))
        self.assertEqual(changes[4].path, Path("src/instance.py"))

    def test_parse_name_status_z(self):
        z_output = (
            "A\0docs/guide.md\0"
            "D\0docs/deprecated.md\0"
            "M\0README.md\0"
            "R100\0src/file with space.txt\0src/renamed space.txt\0"
        )
        changes = git2svn.parse_name_status_z(z_output)
        self.assertEqual(len(changes), 4)

        self.assertEqual(changes[0].action, "A")
        self.assertEqual(changes[0].path, Path("docs/guide.md"))

        self.assertEqual(changes[1].action, "D")
        self.assertEqual(changes[1].path, Path("docs/deprecated.md"))

        self.assertEqual(changes[2].action, "M")
        self.assertEqual(changes[2].path, Path("README.md"))

        self.assertEqual(changes[3].action, "R")
        self.assertEqual(changes[3].old_path, Path("src/file with space.txt"))
        self.assertEqual(changes[3].path, Path("src/renamed space.txt"))


if __name__ == "__main__":
    unittest.main()
