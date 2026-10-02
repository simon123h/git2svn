import io
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
        self.assertEqual(args[0][:5], [self.svn.svn_bin, "commit", "--encoding", "utf-8", "-F"])
        msg_file = Path(args[0][5])
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

    @patch("subprocess.run")
    def test_property_operations(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="*\n", stderr="")

        # Test get_property
        val = self.svn.get_property("svn:executable", Path("script.sh"))
        self.assertEqual(val, "*")
        mock_run.assert_called_with(
            [self.svn.svn_bin, "propget", "svn:executable", "script.sh"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Test set_property
        self.svn.set_property("svn:executable", "*", Path("script.sh"))
        mock_run.assert_called_with(
            [self.svn.svn_bin, "propset", "svn:executable", "*", "script.sh"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # Test del_property
        self.svn.del_property("svn:executable", Path("script.sh"))
        mock_run.assert_called_with(
            [self.svn.svn_bin, "propdel", "svn:executable", "script.sh"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_sync_file_executable_property(self):
        with (
            patch.object(self.svn, "get_property") as mock_get,
            patch.object(self.svn, "set_property") as mock_set,
            patch.object(self.svn, "del_property") as mock_del,
        ):
            # When executable and prop missing -> propset
            mock_get.return_value = None
            self.svn.sync_file_executable_property("run.sh", is_executable=True)
            mock_set.assert_called_once_with("svn:executable", "*", Path("run.sh"))
            mock_del.assert_not_called()

            # When executable and prop already present -> no-op
            mock_set.reset_mock()
            mock_get.return_value = "*"
            self.svn.sync_file_executable_property("run.sh", is_executable=True)
            mock_set.assert_not_called()
            mock_del.assert_not_called()

            # When non-executable and prop present -> propdel
            mock_get.return_value = "*"
            self.svn.sync_file_executable_property("run.sh", is_executable=False)
            mock_del.assert_called_once_with("svn:executable", Path("run.sh"))

            # When non-executable and prop missing -> no-op
            mock_del.reset_mock()
            mock_get.return_value = None
            self.svn.sync_file_executable_property("run.sh", is_executable=False)
            mock_del.assert_not_called()


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

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_apply_structural_changes_rename(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        change = git2svn.FileChange("R", Path("new_name.txt"), old_path=Path("old_name.txt"))
        self.workspace.apply_structural_changes([change])
        calls = [call[0][0] for call in mock_cmd.call_args_list]
        self.assertIn(["rm", "old_name.txt"], calls)
        self.assertIn(["add", "new_name.txt", "--parents"], calls)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_apply_structural_changes_copy(self, mock_cmd):
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        change = git2svn.FileChange("C", Path("copied.txt"), old_path=Path("source.txt"))
        self.workspace.apply_structural_changes([change])
        calls = [call[0][0] for call in mock_cmd.call_args_list]
        self.assertIn(["add", "copied.txt", "--parents"], calls)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_is_valid_workspace_via_info(self, mock_cmd):
        """Verify is_valid_workspace succeeds when .svn is absent but 'svn info' returns 0."""
        # Use an empty directory without .svn
        empty_dir = self.path / "no_svn_subdir"
        empty_dir.mkdir()
        ws = git2svn.SvnWorkspace(empty_dir)

        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="URL: https://...\n", stderr="")
        self.assertTrue(ws.is_valid_workspace())

        mock_cmd.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="Not a working copy")
        self.assertFalse(ws.is_valid_workspace())

    def test_is_svn_url_or_repo(self):
        """Verify detection of SVN URLs and repository stores."""
        self.assertTrue(git2svn.svn.is_svn_url_or_repo("http://svn.example.com/repo"))
        self.assertTrue(git2svn.svn.is_svn_url_or_repo("https://svn.example.com/repo"))
        self.assertTrue(git2svn.svn.is_svn_url_or_repo("svn://svn.example.com/repo"))
        self.assertTrue(git2svn.svn.is_svn_url_or_repo("svn+ssh://user@host/repo"))
        self.assertTrue(git2svn.svn.is_svn_url_or_repo("file:///var/svn/repo"))

        # Local repo store (with format file and db dir)
        repo_dir = self.path / "svn_repo_store"
        repo_dir.mkdir()
        (repo_dir / "format").write_text("12\n")
        (repo_dir / "db").mkdir()
        self.assertTrue(git2svn.svn.is_svn_url_or_repo(repo_dir))
        self.assertTrue(git2svn.svn.is_svn_url_or_repo(str(repo_dir)))

        # Standard non-repo directories
        normal_dir = self.path / "normal_dir"
        normal_dir.mkdir()
        self.assertFalse(git2svn.svn.is_svn_url_or_repo(normal_dir))
        self.assertFalse(git2svn.svn.is_svn_url_or_repo("some/relative/path"))

    def test_get_default_managed_svn_dir(self):
        """Verify canonical managed SVN working copy path inside .git."""
        git_dir = Path("/path/to/myrepo")
        managed = git2svn.svn.get_default_managed_svn_dir(git_dir)
        self.assertEqual(managed, git_dir / ".git" / "git2svn" / "svn_wc")

    @patch("subprocess.run")
    def test_checkout_working_copy_success(self, mock_run):
        """Verify checkout_working_copy executes svn checkout."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="Checked out revision 1.\n", stderr="")
        dest = self.path / "managed_wc"
        git2svn.svn.checkout_working_copy("https://svn.example.com/trunk", dest)

        svn_bin = git2svn.svn.find_svn_binary()
        mock_run.assert_called_once_with(
            [svn_bin, "checkout", "https://svn.example.com/trunk", str(dest)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    @patch("subprocess.run")
    def test_checkout_working_copy_failure_raises(self, mock_run):
        """Verify checkout_working_copy raises SvnError on failure."""
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="svn: E170013: Unable to connect")
        dest = self.path / "fail_wc"
        with self.assertRaises(git2svn.SvnError):
            git2svn.svn.checkout_working_copy("https://invalid.example.com", dest)

    def test_checkout_working_copy_dry_run(self):
        """Verify dry run prints command without running subprocess."""
        dest = self.path / "dry_wc"
        with patch("builtins.print") as mock_print, patch("subprocess.run") as mock_run:
            git2svn.svn.checkout_working_copy("https://svn.example.com/trunk", dest, dry_run=True)
            mock_run.assert_not_called()
            mock_print.assert_called_once()
            self.assertIn("[DRY-RUN]", mock_print.call_args[0][0])

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_cleanup_success_and_failure(self, mock_cmd):
        """Verify SvnWorkspace.cleanup runs svn cleanup and raises on failure."""
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        self.workspace.cleanup()
        mock_cmd.assert_called_with(["cleanup"], check=False)

        mock_cmd.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="svn: E155004: cleanup error")
        with self.assertRaises(git2svn.SvnError):
            self.workspace.cleanup()

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_get_unversioned_items(self, mock_cmd):
        """Verify get_unversioned_items parses unversioned lines from svn status."""
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 0, stdout="?      unversioned.txt\nM      modified.txt\n?      dir/nested.txt\n", stderr=""
        )
        items = self.workspace.get_unversioned_items()
        self.assertEqual(items, [Path("unversioned.txt"), Path("dir/nested.txt")])

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_get_recent_log_messages(self, mock_cmd):
        """Verify get_recent_log_messages parses commit messages from svn log --xml and normalizes CRLF."""
        xml_output = """<?xml version="1.0" encoding="UTF-8"?>
<log>
<logentry revision="42">
<author>simon</author>
<date>2026-10-02T00:00:00.000000Z</date>
<msg>feat: latest commit\r\n\r\nDetailed body with CRLF.</msg>
</logentry>
<logentry revision="41">
<author>simon</author>
<date>2026-10-01T23:00:00.000000Z</date>
<msg>feat: earlier commit</msg>
</logentry>
</log>
"""
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout=xml_output, stderr="")
        msgs = self.workspace.get_recent_log_messages(limit=2)
        self.assertEqual(msgs, ["feat: latest commit\n\nDetailed body with CRLF.", "feat: earlier commit"])
        mock_cmd.assert_called_with(["log", "--xml", "-l", "2"], check=False)

    def test_resolve_branch_url(self):
        """Verify resolve_branch_url converts shorthand branch names to SVN URL targets."""
        # Standard root branch aliases
        self.assertEqual(self.workspace.resolve_branch_url("trunk"), "^/trunk")
        self.assertEqual(self.workspace.resolve_branch_url("main"), "^/trunk")
        self.assertEqual(self.workspace.resolve_branch_url("master"), "^/trunk")

        # Feature / custom branch shorthand
        self.assertEqual(self.workspace.resolve_branch_url("feature-x"), "^/branches/feature-x")
        self.assertEqual(self.workspace.resolve_branch_url("bugfix/issue-12"), "^/branches/bugfix/issue-12")

        # Explicit prefixes
        self.assertEqual(self.workspace.resolve_branch_url("branches/rel-1"), "^/branches/rel-1")
        self.assertEqual(self.workspace.resolve_branch_url("tags/v1.0.0"), "^/tags/v1.0.0")

        # Full or repository-relative URLs
        self.assertEqual(self.workspace.resolve_branch_url("^/custom/dir"), "^/custom/dir")
        self.assertEqual(
            self.workspace.resolve_branch_url("https://svn.example.com/trunk"), "https://svn.example.com/trunk"
        )
        self.assertEqual(self.workspace.resolve_branch_url("svn://svn.example.com/repo"), "svn://svn.example.com/repo")

        # Empty branch name raises ValueError
        with self.assertRaises(ValueError):
            self.workspace.resolve_branch_url("")
        with self.assertRaises(ValueError):
            self.workspace.resolve_branch_url("   ")

    def test_get_current_branch_name(self):
        """Verify get_current_branch_name extracts branch names from svn info."""
        with patch.object(self.workspace, "get_info", return_value={"Relative URL": "^/trunk"}):
            self.assertEqual(self.workspace.get_current_branch_name(), "trunk")

        with patch.object(self.workspace, "get_info", return_value={"Relative URL": "^/branches/feature-login"}):
            self.assertEqual(self.workspace.get_current_branch_name(), "feature-login")

        with patch.object(self.workspace, "get_info", return_value={"Relative URL": "^/branches/team/feature-ui"}):
            self.assertEqual(self.workspace.get_current_branch_name(), "team/feature-ui")

        with patch.object(self.workspace, "get_info", return_value={"Relative URL": "^/tags/v1.0.0"}):
            self.assertEqual(self.workspace.get_current_branch_name(), "tags/v1.0.0")

        with patch.object(self.workspace, "get_info", return_value={"URL": "https://svn.example.com/svn/repo/trunk"}):
            self.assertEqual(self.workspace.get_current_branch_name(), "trunk")

        with patch.object(self.workspace, "get_info", return_value={}):
            self.assertEqual(self.workspace.get_current_branch_name(), "trunk")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_switch(self, mock_cmd):
        """Verify switch executes svn switch or checks dirty workspace."""
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="At revision 5.\n", stderr="")

        with patch.object(self.workspace, "is_clean", return_value=True):
            target = self.workspace.switch("feature-abc")
            self.assertEqual(target, "^/branches/feature-abc")
            mock_cmd.assert_called_with(["switch", "^/branches/feature-abc"], check=False)

        # Dirty workspace raises RuntimeError
        with patch.object(self.workspace, "is_clean", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                self.workspace.switch("trunk")
            self.assertIn("has uncommitted changes", str(cm.exception))

    @patch("builtins.print")
    def test_switch_dry_run(self, mock_print):
        """Verify switch in dry_run mode outputs command without running svn."""
        dry_svn = git2svn.SvnWorkspace(self.workspace.workspace_dir, dry_run=True)
        with patch.object(dry_svn, "is_clean", return_value=True):
            with patch.object(dry_svn, "run_cmd") as mock_cmd:
                target = dry_svn.switch("trunk")
                self.assertEqual(target, "^/trunk")
                mock_cmd.assert_not_called()
                mock_print.assert_called_once()
                self.assertIn("[DRY-RUN]", mock_print.call_args[0][0])

    def test_svn_properties_dry_run(self):
        """Verify property operations in dry_run mode print without invoking svn."""
        dry_svn = git2svn.SvnWorkspace(self.workspace.workspace_dir, dry_run=True)
        fake_stdout = io.StringIO()
        with patch("sys.stdout", fake_stdout), patch.object(dry_svn, "run_cmd") as mock_cmd:
            dry_svn.set_property("custom:prop", "val", "file.txt")
            dry_svn.del_property("custom:prop", "file.txt")
            self.assertIsNone(dry_svn.get_property("custom:prop", "file.txt"))
            dry_svn.sync_file_executable_property("script.sh", is_executable=True)
            dry_svn.sync_file_executable_property("script.sh", is_executable=False)
            mock_cmd.assert_not_called()

        out = fake_stdout.getvalue()
        self.assertIn("[DRY-RUN]", out)
        self.assertIn("propset custom:prop val file.txt", out)
        self.assertIn("propdel custom:prop file.txt", out)
        self.assertIn("propset svn:executable * script.sh", out)
        self.assertIn("propdel svn:executable script.sh", out)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_svn_properties_execution_and_warnings(self, mock_cmd):
        """Verify property commands log warning on non-zero return code."""
        mock_cmd.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="property error")
        with self.assertLogs("git2svn", level="WARNING") as cm:
            self.workspace.set_property("custom:prop", "val", "file.txt")
            self.workspace.del_property("custom:prop", "file.txt")
        self.assertTrue(any("Failed to set property" in msg for msg in cm.output))
        self.assertTrue(any("Failed to delete property" in msg for msg in cm.output))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_rm_not_under_version_control(self, mock_cmd):
        """Verify stage_rm logs warning instead of raising when file is not under version control."""
        mock_cmd.return_value = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="svn: warning: W155010: 'foo.txt' is not under version control"
        )
        with self.assertLogs("git2svn", level="WARNING") as cm:
            self.workspace.stage_rm(Path("foo.txt"))
        self.assertTrue(any("not under SVN control to remove" in msg for msg in cm.output))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_diff_with_stat_and_target(self, mock_cmd):
        """Verify diff forwards stat and target arguments."""
        mock_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="diff output", stderr="")
        out = self.workspace.diff(stat=True, target="sub/file.txt")
        self.assertEqual(out, "diff output")
        mock_cmd.assert_called_with(["diff", "--stat", "sub/file.txt"], check=False)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_get_status_summary_error(self, mock_cmd):
        """Verify get_status_summary raises SvnError when svn status fails."""
        mock_cmd.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="svn: E155004: cleanup error")
        with self.assertRaises(git2svn.SvnError):
            self.workspace.get_status_summary()


if __name__ == "__main__":
    unittest.main()
