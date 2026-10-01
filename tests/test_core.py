import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestSynchronizer(unittest.TestCase):
    def setUp(self):
        self.git_dir = tempfile.TemporaryDirectory()
        self.svn_dir = tempfile.TemporaryDirectory()
        self.git_path = Path(self.git_dir.name).resolve()
        self.svn_path = Path(self.svn_dir.name).resolve()
        (self.svn_path / ".svn").mkdir()

        # Initialize real git repository
        subprocess.run(["git", "init"], cwd=self.git_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.git_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.git_path, check=True)

        self.git_repo = git2svn.GitRepo(self.git_path)
        self.svn_ws = git2svn.SvnWorkspace(self.svn_path)
        self.patcher = git2svn.Patcher(self.svn_path)
        self.sync_mgr = git2svn.Synchronizer(self.git_repo, self.svn_ws, self.patcher)

    def tearDown(self):
        self.git_dir.cleanup()
        self.svn_dir.cleanup()

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_single_commit(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f = self.git_path / "app.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.git_path, check=True)

        (self.svn_path / "app.py").write_text("v1\n")

        # Commit to stage
        f.write_text("v2\n")
        new_file = self.git_path / "sub" / "util.py"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("util\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: update"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Run stage
        self.sync_mgr.stage(commit_hash)

        # Check content updated in SVN
        self.assertEqual((self.svn_path / "app.py").read_text(), "v2\n")
        self.assertEqual((self.svn_path / "sub" / "util.py").read_text(), "util\n")

        # Check SVN staging commands called (no commit)
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["add", "sub/util.py", "--parents"], called_args)
        self.assertFalse(any(cmd[0] == "commit" for cmd in called_args))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_range(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f = self.git_path / "code.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        (self.svn_path / "code.txt").write_text("base\n")

        # Commit 1
        f.write_text("step1\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "step1"], cwd=self.git_path, check=True)

        # Commit 2
        f.write_text("step2 finalized\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "step2"], cwd=self.git_path, check=True)
        head_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Stage range (squashed)
        self.sync_mgr.stage(base_hash, head_hash)

        self.assertEqual((self.svn_path / "code.txt").read_text(), "step2 finalized\n")
        # Ensure no commit was made
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertFalse(any(cmd[0] == "commit" for cmd in called_args))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_copy_mode(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        b = self.git_path / "binary.dat"
        b.write_bytes(b"\x01\x02\x03")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        (self.svn_path / "binary.dat").write_bytes(b"\x01\x02\x03")

        # Target commit
        b.write_bytes(b"\x09\x08\x07\x06")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "target"], cwd=self.git_path, check=True)
        target_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Intentionally corrupt the file on disk in Git repo to prove it extracts from Git object DB, NOT live disk!
        b.write_bytes(b"corrupted live disk content")

        # Stage with --copy (replaces old sync command)
        self.sync_mgr.stage(f"{base_hash}..{target_hash}", use_copy=True)

        # Content in SVN workspace must be the exact commit target content, NOT corrupted disk content!
        self.assertEqual((self.svn_path / "binary.dat").read_bytes(), b"\x09\x08\x07\x06")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_single_commit(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "single.txt"
        f.write_text("single content\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: single commit"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        self.sync_mgr.replay(commit_hash)

        self.assertEqual((self.svn_path / "single.txt").read_text(), "single content\n")
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["add", "single.txt", "--parents"], called_args)
        commit_calls = [arg for arg in called_args if len(arg) >= 2 and arg[0] == "commit" and arg[1] == "-F"]
        self.assertEqual(len(commit_calls), 1)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_range(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f = self.git_path / "file.txt"
        f.write_text("v0\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        (self.svn_path / "file.txt").write_text("v0\n")

        # Commit 1
        f.write_text("v1\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "commit 1"], cwd=self.git_path, check=True)

        # Commit 2
        f.write_text("v2\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "commit 2"], cwd=self.git_path, check=True)
        target_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        self.sync_mgr.replay(base_hash, target_hash)

        self.assertEqual((self.svn_path / "file.txt").read_text(), "v2\n")
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        commit_calls = [arg for arg in called_args if len(arg) >= 2 and arg[0] == "commit" and arg[1] == "-F"]
        self.assertEqual(len(commit_calls), 2)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_conflict_pause_and_continue(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f = self.git_path / "conflict_test.txt"
        f.write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        (self.svn_path / "conflict_test.txt").write_text("line DIFFERENT\nline B\n")

        # Git commit 1 (conflicts with SVN)
        f.write_text("line A MODIFIED\nline B\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "commit with conflict"], cwd=self.git_path, check=True)
        c1_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Git commit 2
        f2 = self.git_path / "next_file.txt"
        f2.write_text("next\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "commit 2"], cwd=self.git_path, check=True)
        c2_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Replay should fail on commit 1 with conflict
        with self.assertRaises(subprocess.CalledProcessError):
            self.sync_mgr.replay(base_hash, c2_hash)

        # State file exists
        state = git2svn.load_replay_state(self.svn_path)
        self.assertIsNotNone(state)
        self.assertEqual(state["current_commit"], c1_hash)

        # Clean artifacts, resolve conflict in SVN, and continue
        git2svn.clean_conflict_artifacts(self.svn_path)
        (self.svn_path / "conflict_test.txt").write_text("line A MODIFIED\nline B\n")

        self.sync_mgr.replay_continue()

        self.assertEqual((self.svn_path / "conflict_test.txt").read_text(), "line A MODIFIED\nline B\n")
        self.assertEqual((self.svn_path / "next_file.txt").read_text(), "next\n")
        self.assertIsNone(git2svn.load_replay_state(self.svn_path))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_abort(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        state_data = {
            "current_commit": "abc",
            "current_commit_msg": "test",
            "remaining_commits": [],
        }
        git2svn.save_replay_state(self.svn_path, state_data)
        rej = self.svn_path / "bad.txt.rej"
        rej.write_text("rej")

        self.sync_mgr.replay_abort()

        self.assertIsNone(git2svn.load_replay_state(self.svn_path))
        self.assertFalse(rej.exists())
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["revert", "-R", "."], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_identity_banner(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout="URL: https://svn.example.com/repo/branches/feature\nRelative URL: ^/branches/feature\nRevision: 1042\n",
            stderr="",
        )
        with patch("builtins.print") as mock_print:
            self.sync_mgr.show_identity_banner("master..feature")
            printed_banner = mock_print.call_args[0][0]
            self.assertIn("[TARGET] Git source :", printed_banner)
            self.assertIn("ref: master..feature", printed_banner)
            self.assertIn("^/branches/feature (r1042)", printed_banner)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_main_cli(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "cli_test.txt"
        f.write_text("cli test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "cli test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "stage",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.svn_path / "cli_test.txt").read_text(), "cli test\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_main_cli_git_config_fallback(self, mock_svn_cmd):
        """Verify git config git2svn.svnDir is used when --svn-dir is omitted."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Configure git2svn.svnDir in repository .git/config
        subprocess.run(["git", "config", "git2svn.svnDir", str(self.svn_path)], cwd=self.git_path, check=True)

        f = self.git_path / "cfg_test.txt"
        f.write_text("config test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "config test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Omit --svn-dir
        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "stage",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.svn_path / "cfg_test.txt").read_text(), "config test\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_invokes_svn_update_by_default(self, mock_svn_cmd):
        """Verify git2svn replay invokes svn update by default without any flag or config."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "default_update_test.txt"
        f.write_text("default update test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "default update test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "replay",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["update"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_always_runs_svn_update(self, mock_svn_cmd):
        """Verify git2svn replay always runs svn update before and after execution."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "mandatory_update_test.txt"
        f.write_text("mandatory update test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "mandatory update test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "replay",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        update_calls = [c for c in called_args if c == ["update"]]
        # Should be called before applying commits and after replay finishes
        self.assertGreaterEqual(len(update_calls), 2)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_runs_svn_update_before_staging(self, mock_svn_cmd):
        """Verify git2svn stage runs svn update before staging changes."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "stage_update_test.txt"
        f.write_text("stage update test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "stage update test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "stage",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["update"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_with_git_config_default_range(self, mock_svn_cmd):
        """Verify git2svn replay with no ref arguments falls back to git2svn.defaultRange."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Create base commit
        base_file = self.git_path / "base.txt"
        base_file.write_text("base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base commit"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Create branch 'trunk'
        subprocess.run(["git", "branch", "trunk"], cwd=self.git_path, check=True)
        subprocess.run(["git", "checkout", "trunk"], cwd=self.git_path, check=True)

        # Create commit on trunk
        trunk_file = self.git_path / "trunk_feat.txt"
        trunk_file.write_text("trunk feat\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: on trunk"], cwd=self.git_path, check=True)

        # Switch to another branch to ensure defaultRange is branch-independent
        subprocess.run(["git", "checkout", "-b", "feature/other"], cwd=self.git_path, check=True)

        # Configure defaultRange to base_hash..trunk
        subprocess.run(["git", "config", "git2svn.defaultRange", f"{base_hash}..trunk"], cwd=self.git_path, check=True)

        # Run replay without ref1 or ref2
        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "replay",
            ]
        )
        self.assertEqual(code, 0)
        # Verify the file on trunk was committed to SVN
        self.assertTrue((self.svn_path / "trunk_feat.txt").is_file())
        self.assertEqual((self.svn_path / "trunk_feat.txt").read_text(), "trunk feat\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_progress_output(self, mock_svn_cmd):
        """Verify git2svn replay outputs formatted progress indicators with commit title and OK status."""
        import io

        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        base_file = self.git_path / "prog_base.txt"
        base_file.write_text("prog base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "prog base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        commit_file = self.git_path / "prog_feat.txt"
        commit_file.write_text("prog feat\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: progress indicator"], cwd=self.git_path, check=True)
        feat_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            self.sync_mgr.replay(base_hash, feat_hash)
            output = mock_stdout.getvalue()
            self.assertIn(f"[1/1] Applying {feat_hash[:8]}: feat: progress indicator... OK (", output)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_with_git_config_default_range(self, mock_svn_cmd):
        """Verify git2svn stage with no ref arguments falls back to git2svn.defaultRange."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        base_file = self.git_path / "stage_base.txt"
        base_file.write_text("stage base\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "stage base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        feat_file = self.git_path / "stage_default.txt"
        feat_file.write_text("staged content\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "staged feat"], cwd=self.git_path, check=True)
        feat_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        subprocess.run(
            ["git", "config", "git2svn.defaultRange", f"{base_hash}..{feat_hash}"], cwd=self.git_path, check=True
        )

        # Run stage without ref
        code = git2svn.main(
            [
                "--git-dir",
                str(self.git_path),
                "--svn-dir",
                str(self.svn_path),
                "stage",
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual((self.svn_path / "stage_default.txt").read_text(), "staged content\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_eol_preservation_on_crlf_target(self, mock_svn_cmd):
        """Verify git apply with an LF patch preserves CRLF line endings on CRLF target file."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # SVN checkout has CRLF file
        svn_file = self.svn_path / "crlf_file.txt"
        svn_file.write_bytes(b"line 1\r\nline 2\r\nline 3\r\n")

        # Git mirror has LF file
        git_file = self.git_path / "crlf_file.txt"
        git_file.write_bytes(b"line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)

        # Modify file in Git with LF line endings
        git_file.write_bytes(b"line 1\nline 2 modified\nline 3\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "modify in git (LF)"], cwd=self.git_path, check=True)
        c_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        self.sync_mgr.stage(c_hash)

        # Content in SVN should be updated and remain cleanly CRLF without mixed endings
        content = svn_file.read_bytes()
        self.assertEqual(content, b"line 1\r\nline 2 modified\r\nline 3\r\n")
        self.assertNotIn(b"\r\r\n", content)
        # Ensure no orphan \n exists
        self.assertEqual(content.count(b"\n"), content.count(b"\r\n"))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_copy_mode_preserves_target_eol(self, mock_svn_cmd):
        """Verify --copy mode preserves target CRLF line endings when overwriting."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        svn_file = self.svn_path / "copy_crlf.txt"
        svn_file.write_bytes(b"line 1\r\nline 2\r\n")

        git_file = self.git_path / "copy_crlf.txt"
        git_file.write_bytes(b"line 1\nline 2\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)

        git_file.write_bytes(b"line 1\nline 2 updated\nline 3\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "update"], cwd=self.git_path, check=True)
        c_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        self.sync_mgr.stage(c_hash, use_copy=True)

        content = svn_file.read_bytes()
        self.assertEqual(content, b"line 1\r\nline 2 updated\r\nline 3\r\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_stage_snapshot(self, mock_svn_cmd):
        """
        Verify stage --snapshot accurately aligns the SVN workspace to Git state:
        - Removes files present in SVN but missing in Git
        - Adds files present in Git but missing in SVN
        - Modifies existing files to match Git content
        """
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # 1. SVN workspace currently has:
        # - keep_and_modify.txt
        # - to_delete.txt
        (self.svn_path / "keep_and_modify.txt").write_bytes(b"svn old content\r\n")
        (self.svn_path / "to_delete.txt").write_bytes(b"delete me\r\n")

        # 2. Git repo at target_ref has:
        # - keep_and_modify.txt (modified)
        # - brand_new.txt (added)
        # (and does NOT have to_delete.txt)
        (self.git_path / "keep_and_modify.txt").write_bytes(b"git new content\n")
        (self.git_path / "brand_new.txt").write_bytes(b"new file content\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "target snapshot commit"], cwd=self.git_path, check=True)
        target_ref = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Run stage --snapshot
        self.sync_mgr.stage(target_ref, snapshot=True)

        # Assertions on disk:
        # - to_delete.txt removed
        self.assertFalse((self.svn_path / "to_delete.txt").exists())
        # - keep_and_modify.txt updated (and EOL preserved as CRLF because original was CRLF)
        self.assertEqual((self.svn_path / "keep_and_modify.txt").read_bytes(), b"git new content\r\n")
        # - brand_new.txt created
        self.assertEqual((self.svn_path / "brand_new.txt").read_bytes(), b"new file content\n")

        # SVN structural commands invoked:
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["rm", "to_delete.txt"], called_args)
        self.assertIn(["add", "brand_new.txt", "--parents"], called_args)

    def test_replay_skip(self):
        """Verify replay_skip clears current commit and continues with remaining."""
        state_data = {
            "current_commit": "abc",
            "current_commit_msg": "skip me",
            "remaining_commits": [],
            "git_dir": str(self.git_path),
        }
        git2svn.save_replay_state(self.svn_path, state_data)
        rej = self.svn_path / "bad.txt.rej"
        rej.write_text("conflict rej")

        with patch.object(git2svn.SvnWorkspace, "run_cmd") as mock_svn_cmd:
            mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            self.sync_mgr.replay_skip()

        self.assertIsNone(git2svn.load_replay_state(self.svn_path))
        self.assertFalse(rej.exists())

    def test_replay_continue_no_state_error(self):
        """Verify replay_continue raises RuntimeError if no replay state exists."""
        with self.assertRaises(RuntimeError):
            self.sync_mgr.replay_continue()

    def test_replay_abort_no_state_error(self):
        """Verify replay_abort raises RuntimeError if no replay state exists."""
        with self.assertRaises(RuntimeError):
            self.sync_mgr.replay_abort()

    def test_replay_skip_no_state_error(self):
        """Verify replay_skip raises RuntimeError if no replay state exists."""
        with self.assertRaises(RuntimeError):
            self.sync_mgr.replay_skip()

    def test_cli_verbose_exception(self):
        """Verify verbose mode prints traceback on unhandled exception."""
        with (
            patch("git2svn.cli.Synchronizer.stage", side_effect=RuntimeError("boom")),
            patch("traceback.print_exc") as mock_tb,
            patch("sys.stderr"),
        ):
            res = git2svn.main(
                [
                    "--git-dir",
                    str(self.git_path),
                    "--svn-dir",
                    str(self.svn_path),
                    "--verbose",
                    "stage",
                    "HEAD",
                ]
            )
            self.assertEqual(res, 1)
            mock_tb.assert_called_once()

    def test_replay_raises_if_svn_workspace_dirty(self):
        """Verify replay raises RuntimeError if SVN workspace has uncommitted changes."""
        with patch.object(self.svn_ws, "is_clean", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                self.sync_mgr.replay("HEAD~1..HEAD")
            self.assertIn("SVN workspace has uncommitted changes", str(cm.exception))

    def test_replay_single_commit_raises_if_dirty(self):
        """Verify replay of single commit raises RuntimeError if SVN workspace has uncommitted changes."""
        with patch.object(self.svn_ws, "is_clean", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                self.sync_mgr.replay("HEAD")
            self.assertIn("SVN workspace has uncommitted changes", str(cm.exception))

    def test_snapshot_symlink(self):
        """Verify stage --snapshot handles symlinks (mode 120000)."""
        import os

        # Git tree with target file and a symlink pointing to it
        target = self.git_path / "target.txt"
        target.write_text("target file content\n")
        link = self.git_path / "link.txt"
        try:
            os.symlink("target.txt", link)
        except OSError:
            self.skipTest("Symlinks not supported on this platform/privilege level")

        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "add symlink"], cwd=self.git_path, check=True)
        target_ref = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        with patch.object(self.svn_ws, "run_cmd") as mock_svn_cmd:
            mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            self.sync_mgr.stage(target_ref, snapshot=True)

        svn_link = self.svn_path / "link.txt"
        self.assertTrue(svn_link.is_symlink())
        self.assertEqual(os.readlink(svn_link), "target.txt")


if __name__ == "__main__":
    unittest.main()
