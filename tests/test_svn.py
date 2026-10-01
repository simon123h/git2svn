import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestSvnWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.temp_dir.name).resolve()
        (self.workspace_dir / ".svn").mkdir()
        self.svn = git2svn.SvnWorkspace(self.workspace_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_commit(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="Committed revision 42.\n", stderr="")
        msg = "feat: some feature\n\nDetailed body."
        self.svn.commit(msg)
        self.assertEqual(mock_run.call_count, 1)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0][:3], [self.svn.svn_bin, "commit", "-F"])
        msg_file = Path(args[0][3])
        self.assertEqual(kwargs["cwd"], self.workspace_dir)
        self.assertFalse(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")
        # Ensure temp file was cleaned up after commit
        self.assertFalse(msg_file.exists())

    @patch("subprocess.run")
    def test_stage_add(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        rel_path = Path("nested/folder/file.txt")

        self.svn.stage_add(rel_path)

        # Ensure parent directory was created on disk
        self.assertTrue((self.workspace_dir / "nested" / "folder").is_dir())
        mock_run.assert_called_once_with(
            [self.svn.svn_bin, "add", "nested/folder/file.txt", "--parents"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    @patch("subprocess.run")
    def test_stage_rm(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        rel_path = Path("file_to_remove.txt")

        self.svn.stage_rm(rel_path)

        mock_run.assert_called_once_with(
            [self.svn.svn_bin, "rm", "file_to_remove.txt"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


class TestSvnLockAndCollisionHandling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()
        self.workspace = git2svn.SvnWorkspace(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_svn_error_lock(self):
        stderr = "svn: E155004: Working copy '/tmp/svn_wc' locked.\nsvn: run 'svn cleanup' to remove locks"
        err = git2svn.svn.parse_svn_error(stderr, "commit", self.path)
        self.assertIsInstance(err, git2svn.SvnLockError)
        self.assertIn("is locked", str(err))
        self.assertIn("svn cleanup", str(err))

    def test_parse_svn_error_out_of_date(self):
        stderr = "svn: E155015: Aborting commit: 'file.txt' remains in conflict / item is out of date"
        err = git2svn.svn.parse_svn_error(stderr, "commit", self.path)
        self.assertIsInstance(err, git2svn.SvnOutOfDateError)
        self.assertIn("out of date", str(err))
        self.assertIn("svn update", str(err))

    def test_parse_svn_error_e160024_conflict(self):
        stderr = "svn: E160024: resource out of date; try updating"
        err = git2svn.svn.parse_svn_error(stderr, "commit", self.path)
        self.assertIsInstance(err, git2svn.SvnOutOfDateError)
        self.assertIn("svn update", str(err))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_commit_raises_lock_error_with_hints(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="svn: E155004: Working copy locked; please run 'svn cleanup'"
        )
        with self.assertRaises(git2svn.SvnLockError) as cm:
            self.workspace.commit("test msg")
        self.assertIn("svn cleanup", str(cm.exception))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_commit_raises_out_of_date_error_with_hints(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="svn: E155015: Commit failed because item is out of date"
        )
        with self.assertRaises(git2svn.SvnOutOfDateError) as cm:
            self.workspace.commit("test msg")
        self.assertIn("svn update", str(cm.exception))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_update_raises_lock_error(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="svn: E155004: Working copy locked."
        )
        with self.assertRaises(git2svn.SvnLockError):
            self.workspace.update()

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_is_clean_raises_on_lock(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="svn: E155004: Working copy locked."
        )
        with self.assertRaises(git2svn.SvnLockError):
            self.workspace.is_clean()

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_diff(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="Index: file.txt\n", stderr="")
        output = self.workspace.diff()
        self.assertEqual(output, "Index: file.txt\n")
        mock_cmd.assert_called_once_with(["diff"], check=False)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_diff_stat(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="file.txt | 2 +-\n", stderr="")
        output = self.workspace.diff(stat=True)
        self.assertEqual(output, "file.txt | 2 +-\n")
        mock_cmd.assert_called_once_with(["diff", "--stat"], check=False)


if __name__ == "__main__":
    unittest.main()
