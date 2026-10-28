import sys
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


class TestGitDiffRobustness(unittest.TestCase):
    def setUp(self):
        import subprocess
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name).resolve()
        self._run = lambda *a: subprocess.run(["git", *a], cwd=self.path, check=True, capture_output=True, text=True)
        self._run("init", "-q", "-b", "main")
        self._run("config", "user.email", "t@example.com")
        self._run("config", "user.name", "T")
        (self.path / "f.txt").write_text("a\n")
        self._run("add", ".")
        self._run("commit", "-qm", "base")
        self.repo = git2svn.GitRepo(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_diff_ignores_noprefix_config(self):
        self._run("config", "diff.noprefix", "true")
        (self.path / "f.txt").write_text("a\nb\n")
        self._run("commit", "-qam", "change")
        diff = self.repo.get_diff("HEAD~1..HEAD").decode()
        self.assertIn("diff --git a/f.txt b/f.txt", diff)

    def test_has_merge_base(self):
        self._run("branch", "feat")
        self.assertTrue(self.repo.has_merge_base("main", "feat"))
        self._run("checkout", "-q", "--orphan", "unrelated")
        self._run("commit", "-q", "--allow-empty", "-m", "other root")
        self.assertFalse(self.repo.has_merge_base("main", "unrelated"))
        self.assertFalse(self.repo.has_merge_base("4b825dc642cb6eb9a060e54bf8d69288fbee4904", "main"))  # empty tree

    def test_three_dot_diff_excludes_upstream_only_changes(self):
        self._run("branch", "feat")
        (self.path / "upstream.txt").write_text("u\n")
        self._run("add", ".")
        self._run("commit", "-qm", "upstream")
        self._run("checkout", "-q", "feat")
        (self.path / "f.txt").write_text("a\nb\n")
        self._run("commit", "-qam", "feat")

        names = [(c.action, c.path.name) for c in self.repo.get_name_status("main...feat")]
        self.assertEqual(names, [("M", "f.txt")])


class TestGitTreeModes(unittest.TestCase):
    def test_modes_include_exec_symlink_and_odd_names(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d).resolve()

            def run(*a, stdin=None):
                return subprocess.run(["git", *a], cwd=path, check=True, capture_output=True, input=stdin, text=True)

            run("init", "-q", "-b", "main")
            run("config", "user.email", "t@example.com")
            run("config", "user.name", "T")
            blob = run("hash-object", "-w", "--stdin", stdin="x\n").stdout.strip()
            # Build the index directly so the test does not depend on filesystem support for
            # exec bits, symlinks or tab characters in file names (CI also runs on Windows).
            entries = {
                "plain.txt": "100644",
                "run.sh": "100755",
                "link": "120000",
                "sp ace/name@2x.txt": "100644",
            }
            if sys.platform != "win32":  # git refuses tabs in paths on Windows (core.protectNTFS)
                entries["sp ace/tab\tname.txt"] = "100644"
            for name, mode in entries.items():
                run("update-index", "--add", "--cacheinfo", f"{mode},{blob},{name}")
            run("commit", "-qm", "x")

            modes = git2svn.GitRepo(path).get_tree_modes("HEAD")

        self.assertEqual(modes, {Path(name): mode for name, mode in entries.items()})
