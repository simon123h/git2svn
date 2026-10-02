import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import git2svn
from git2svn.state import load_replay_session


class TestInteractiveReplay(unittest.TestCase):
    def setUp(self):
        self.git_dir = tempfile.TemporaryDirectory()
        self.svn_dir = tempfile.TemporaryDirectory()
        self.git_path = Path(self.git_dir.name).resolve()
        self.svn_path = Path(self.svn_dir.name).resolve()
        (self.svn_path / ".svn").mkdir()

        # Initialize real git repository
        subprocess.run(["git", "init", "-b", "main"], cwd=self.git_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.git_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.git_path, check=True)

        self.git_repo = git2svn.GitRepo(self.git_path)
        self.svn_ws = git2svn.SvnWorkspace(self.svn_path)
        self.patcher = git2svn.Patcher(self.svn_path)
        self.sync_mgr = git2svn.Synchronizer(self.git_repo, self.svn_ws, self.patcher)

        # Create base commit
        (self.git_path / "base.txt").write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base commit"], cwd=self.git_path, check=True)
        self.base_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True, check=True
        ).stdout.strip()

    def tearDown(self):
        self.git_dir.cleanup()
        self.svn_dir.cleanup()

    def _create_git_commits(self, count: int = 3) -> list[str]:
        hashes = []
        for i in range(count):
            p = self.git_path / f"file_{i}.txt"
            p.write_text(f"content {i}\n")
            subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
            subprocess.run(["git", "commit", "-m", f"commit {i}"], cwd=self.git_path, check=True)
            h = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True, check=True
            ).stdout.strip()
            hashes.append(h)
        return hashes

    def test_interactive_replay_fails_on_non_tty(self):
        """Verify interactive mode raises RuntimeError when stdin is not a tty."""
        commits = self._create_git_commits(2)
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                self.sync_mgr.replay(commits[0], commits[1], interactive=True)
            self.assertIn("Interactive replay requires an interactive terminal", str(ctx.exception))

            with self.assertRaises(RuntimeError) as ctx:
                self.sync_mgr.replay_continue(interactive=True)
            self.assertIn("Interactive replay requires an interactive terminal", str(ctx.exception))

            with self.assertRaises(RuntimeError) as ctx:
                self.sync_mgr.replay_skip(interactive=True)
            self.assertIn("Interactive replay requires an interactive terminal", str(ctx.exception))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_accept_all_with_yes(self, mock_svn_cmd):
        """Verify prompt answers with 'y' sequentially apply each commit."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(2)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["y", "yes"]),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[1]}", interactive=True, force=True)
            self.assertEqual(mock_stage.call_count, 2)
            self.assertEqual(mock_commit.call_count, 2)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_skip_commit(self, mock_svn_cmd):
        """Verify prompt answer 's' skips the commit without staging/committing it."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(2)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["s", "y"]),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[1]}", interactive=True, force=True)
            # Only second commit applied
            self.assertEqual(mock_stage.call_count, 1)
            mock_stage.assert_called_once_with(commits[1])
            self.assertEqual(mock_commit.call_count, 1)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_all_flag(self, mock_svn_cmd):
        """Verify prompt answer 'a' turns off interactivity for remaining commits."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(3)

        mock_input = MagicMock(side_effect=["a"])
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", mock_input),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[2]}", interactive=True, force=True)
            # Prompt called only once (for the first commit)
            self.assertEqual(mock_input.call_count, 1)
            # All 3 commits applied
            self.assertEqual(mock_stage.call_count, 3)
            self.assertEqual(mock_commit.call_count, 3)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_diff_and_help(self, mock_svn_cmd):
        """Verify prompt answers 'd' (diff) and '?' (help) print info and re-prompt."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(1)

        captured_stdout = io.StringIO()
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["d", "?", "y"]),
            patch("sys.stdout", captured_stdout),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay(commits[0], interactive=True, force=True)
            self.assertEqual(mock_stage.call_count, 1)
            self.assertEqual(mock_commit.call_count, 1)

        output = captured_stdout.getvalue()
        self.assertIn("Available commands:", output)
        self.assertIn("diffstat", output)
        self.assertIn("file_0.txt", output)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_quit_and_continue(self, mock_svn_cmd):
        """Verify 'q' pauses replay, saves state as PAUSED, and continue resumes."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(3)

        captured_stdout = io.StringIO()
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["y", "q"]),
            patch("sys.stdout", captured_stdout),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[2]}", interactive=True, force=True)
            # Only first commit applied before quit on commit 2
            self.assertEqual(mock_stage.call_count, 1)
            self.assertEqual(mock_commit.call_count, 1)

        # Check replay state saved as PAUSED
        session = load_replay_session(self.svn_path)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, "PAUSED")
        self.assertEqual(session.current_commit, commits[1])
        self.assertEqual(session.remaining_commits, [commits[2]])
        self.assertEqual(session.completed_commits, 1)
        self.assertEqual(session.total_commits, 3)

        # Now resume via replay_continue with interactive=True
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["y", "y"]),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage_continue,
            patch.object(self.svn_ws, "commit") as mock_commit_continue,
        ):
            self.sync_mgr.replay_continue(interactive=True)
            # Remaining two commits applied
            self.assertEqual(mock_stage_continue.call_count, 2)
            self.assertEqual(mock_commit_continue.call_count, 2)

        # Session should now be cleared
        self.assertIsNone(load_replay_session(self.svn_path))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_quit_and_skip(self, mock_svn_cmd):
        """Verify 'q' pauses replay, and replay_skip skips paused commit."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(3)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=["y", "q"]),
            patch.object(self.sync_mgr, "_patch_and_stage_commit"),
            patch.object(self.svn_ws, "commit"),
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[2]}", interactive=True, force=True)

        session = load_replay_session(self.svn_path)
        self.assertIsNotNone(session)

        # Skip commit 2 and apply commit 3
        with (
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
            patch.object(self.svn_ws, "commit") as mock_commit,
        ):
            self.sync_mgr.replay_skip()
            self.assertEqual(mock_stage.call_count, 1)
            mock_stage.assert_called_once_with(commits[2])
            self.assertEqual(mock_commit.call_count, 1)

        self.assertIsNone(load_replay_session(self.svn_path))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_interactive_replay_keyboard_interrupt_treated_as_quit(self, mock_svn_cmd):
        """Verify KeyboardInterrupt is caught and treated as 'q' (pause)."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        commits = self._create_git_commits(2)

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("builtins.input", side_effect=KeyboardInterrupt),
            patch.object(self.sync_mgr, "_patch_and_stage_commit") as mock_stage,
        ):
            self.sync_mgr.replay(f"{self.base_commit}..{commits[1]}", interactive=True, force=True)
            self.assertEqual(mock_stage.call_count, 0)

        session = load_replay_session(self.svn_path)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, "PAUSED")
        self.assertEqual(session.current_commit, commits[0])


if __name__ == "__main__":
    unittest.main()
