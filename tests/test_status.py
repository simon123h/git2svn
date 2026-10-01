import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()
        self.git_path = self.path / "git_repo"
        self.svn_path = self.path / "svn_repo"
        self.git_path.mkdir()
        self.svn_path.mkdir()
        (self.svn_path / ".svn").mkdir()

        subprocess.run(["git", "init"], cwd=self.git_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.git_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.git_path, check=True)

        self.git_repo = git2svn.GitRepo(self.git_path)
        self.svn_workspace = git2svn.SvnWorkspace(self.svn_path)
        self.patcher = git2svn.Patcher(self.svn_path)
        self.sync_mgr = git2svn.Synchronizer(self.git_repo, self.svn_workspace, self.patcher)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_status_clean_no_commits(self):
        """Status succeeds when both repos are clean and range has no pending commits."""
        f = self.git_path / "init.txt"
        f.write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "init commit"], cwd=self.git_path, check=True)
        head = self.git_repo.get_head_commit()

        self.git_repo.set_config("git2svn.defaultRange", f"{head}..{head}")

        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)

    def test_status_pending_commits(self):
        """Status succeeds and reports pending commits."""
        f = self.git_path / "base.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base = self.git_repo.get_head_commit()

        f2 = self.git_path / "feat.txt"
        f2.write_text("feat\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: new feature"], cwd=self.git_path, check=True)
        head = self.git_repo.get_head_commit()

        self.git_repo.set_config("git2svn.defaultRange", f"{base}..{head}")

        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)

    def test_status_detects_merge_commits_as_error(self):
        """Status exits with code 1 if merge commits exist in the range."""
        f = self.git_path / "base.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base = self.git_repo.get_head_commit()

        init_branch = self.git_repo.get_current_branch()
        subprocess.run(["git", "checkout", "-b", "side"], cwd=self.git_path, check=True, capture_output=True)
        f_side = self.git_path / "side.txt"
        f_side.write_text("side\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "side commit"], cwd=self.git_path, check=True)

        subprocess.run(["git", "checkout", init_branch], cwd=self.git_path, check=True, capture_output=True)
        f_main = self.git_path / "main.txt"
        f_main.write_text("main\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "main commit"], cwd=self.git_path, check=True)

        subprocess.run(["git", "merge", "--no-ff", "side", "-m", "merge side"], cwd=self.git_path, check=True)
        head = self.git_repo.get_head_commit()

        self.git_repo.set_config("git2svn.defaultRange", f"{base}..{head}")

        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 1)

    def test_status_detects_paused_replay_state(self):
        """Status exits with code 1 when an in-progress replay is paused."""
        git2svn.save_replay_state(
            self.svn_path,
            {
                "state": "CONFLICT_PAUSED",
                "current_commit": "12345678",
                "current_commit_msg": "conflict commit",
                "remaining_commits": ["abcdef12"],
                "completed_commits": 1,
                "total_commits": 3,
            },
        )
        try:
            with (
                patch.object(self.svn_workspace, "is_clean", return_value=True),
                patch.object(
                    self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}
                ),
            ):
                code = self.sync_mgr.status()
                self.assertEqual(code, 1)
        finally:
            git2svn.clear_replay_state(self.svn_path)

    def test_status_cli_dispatch(self):
        """Verify git2svn status CLI command executes correctly."""
        self.git_repo.set_config("git2svn.svnDir", str(self.svn_path))
        with patch.object(git2svn.Synchronizer, "status", return_value=0) as mock_status:
            code = git2svn.main(["--git-dir", str(self.git_path), "status"])
            self.assertEqual(code, 0)
            mock_status.assert_called_once()

    def test_status_missing_start_ref(self):
        """Status warns when start_ref of range does not exist in Git."""
        self.git_repo.set_config("git2svn.defaultRange", "nonexistent_branch..main")
        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
            patch("sys.stdout") as mock_stdout,
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)
            self.assertTrue(any("nonexistent_branch" in str(c) for c in mock_stdout.mock_calls))

    def test_status_missing_end_ref(self):
        """Status warns when end_ref of range does not exist in Git."""
        f = self.git_path / "base.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        head = self.git_repo.get_head_commit()

        self.git_repo.set_config("git2svn.defaultRange", f"{head}..nonexistent_end")
        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
            patch("sys.stdout"),
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)

    def test_status_more_than_ten_commits(self):
        """Status truncates list and prints '... and X more' when > 10 commits are pending."""
        f = self.git_path / "file.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base = self.git_repo.get_head_commit()

        for i in range(12):
            f.write_text(f"commit {i}\n")
            subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
            subprocess.run(["git", "commit", "-m", f"commit {i}"], cwd=self.git_path, check=True)
        head = self.git_repo.get_head_commit()

        self.git_repo.set_config("git2svn.defaultRange", f"{base}..{head}")
        with (
            patch.object(self.svn_workspace, "is_clean", return_value=True),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
            patch("builtins.print") as mock_print,
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)
            printed_lines = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("and 2 more" in line for line in printed_lines))

    def test_status_dirty_svn_workspace_with_summary(self):
        """Status displays dirty status and listed uncommitted files."""
        f = self.git_path / "init.txt"
        f.write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.git_path, check=True)

        uncommitted_files = [f"M file_{i}.txt" for i in range(7)]
        with (
            patch.object(self.svn_workspace, "is_clean", return_value=False),
            patch.object(self.svn_workspace, "get_status_summary", return_value=uncommitted_files),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
            patch("builtins.print") as mock_print,
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 0)
            printed_lines = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("Dirty (7 uncommitted changes)" in line for line in printed_lines))
            self.assertTrue(any("and 2 more" in line for line in printed_lines))

    def test_status_svn_error_handling(self):
        """Status handles SvnError when checking svn status."""
        with (
            patch.object(
                self.svn_workspace, "is_clean", side_effect=git2svn.SvnError("Working copy locked", returncode=1)
            ),
            patch.object(self.svn_workspace, "get_info", return_value={"URL": "file:///svn/trunk", "Revision": "10"}),
            patch("builtins.print") as mock_print,
        ):
            code = self.sync_mgr.status()
            self.assertEqual(code, 1)
            printed_lines = [call[0][0] for call in mock_print.call_args_list if call[0]]
            self.assertTrue(any("Error: Working copy locked" in line for line in printed_lines))


if __name__ == "__main__":
    unittest.main()
