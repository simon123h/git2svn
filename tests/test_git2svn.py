#!/usr/bin/env python3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TestCliArgs(unittest.TestCase):
    def test_stage_single_commit(self):
        args = git2svn.parse_cli_args(["stage", "abc1234", "--svn-dir", "/path/to/svn"])
        self.assertEqual(args.command, "stage")
        self.assertEqual(args.ref1, "abc1234")
        self.assertIsNone(args.ref2)
        self.assertFalse(args.copy)
        self.assertEqual(args.svn_dir, Path("/path/to/svn"))

    def test_stage_range_two_args(self):
        args = git2svn.parse_cli_args(["stage", "main", "feature", "-s", "/path/to/svn", "--copy"])
        self.assertEqual(args.command, "stage")
        self.assertEqual(args.ref1, "main")
        self.assertEqual(args.ref2, "feature")
        self.assertTrue(args.copy)

    def test_stage_range_dot_notation(self):
        args = git2svn.parse_cli_args(["stage", "main..feature", "-s", "/path/to/svn"])
        self.assertEqual(args.command, "stage")
        self.assertEqual(args.ref1, "main..feature")
        self.assertIsNone(args.ref2)

    def test_replay_args(self):
        args = git2svn.parse_cli_args(["replay", "main", "feature", "-s", "/path/to/svn"])
        self.assertEqual(args.command, "replay")
        self.assertEqual(args.ref1, "main")
        self.assertEqual(args.ref2, "feature")
        self.assertIsNone(args.replay_action)

        args_single = git2svn.parse_cli_args(["replay", "abc1234", "-s", "/path/to/svn"])
        self.assertEqual(args_single.command, "replay")
        self.assertEqual(args_single.ref1, "abc1234")

        args_cont = git2svn.parse_cli_args(["replay", "--continue", "-s", "/path/to/svn"])
        self.assertEqual(args_cont.command, "replay")
        self.assertEqual(args_cont.replay_action, "continue")

        args_abort = git2svn.parse_cli_args(["replay", "--abort", "-s", "/path/to/svn"])
        self.assertEqual(args_abort.command, "replay")
        self.assertEqual(args_abort.replay_action, "abort")

        args_skip = git2svn.parse_cli_args(["replay", "--skip", "-s", "/path/to/svn"])
        self.assertEqual(args_skip.command, "replay")
        self.assertEqual(args_skip.replay_action, "skip")


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


class TestPatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name).resolve()
        self.patcher = git2svn.Patcher(self.target_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_diff_creates_and_modifies_files(self):
        target_file = self.target_dir / "sample.txt"
        target_file.write_text("Hello\nWorld\n")

        diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,3 @@\n Hello\n+Awesome\n World\n"
        self.patcher.apply_diff(diff)
        self.assertEqual(target_file.read_text(), "Hello\nAwesome\nWorld\n")

    def test_apply_diff_empty(self):
        with patch("subprocess.run") as mock_run:
            self.patcher.apply_diff("")
            mock_run.assert_not_called()


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
    def test_replay_with_no_update_flag(self, mock_svn_cmd):
        """Verify git2svn replay --no-update disables svn update."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "no_update_test.txt"
        f.write_text("no update test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "no update test"], cwd=self.git_path, check=True)
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
                "--no-update",
                commit_hash,
            ]
        )
        self.assertEqual(code, 0)
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertNotIn(["update"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_replay_with_git_config_auto_update_disabled(self, mock_svn_cmd):
        """Verify git config git2svn.autoUpdate false disables svn update on replay."""
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        subprocess.run(["git", "config", "git2svn.autoUpdate", "false"], cwd=self.git_path, check=True)

        f = self.git_path / "config_disabled_update.txt"
        f.write_text("config disabled\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "config disabled"], cwd=self.git_path, check=True)
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
        self.assertNotIn(["update"], called_args)

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


class TestEolUtilities(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_file_eol(self):
        f_crlf = self.path / "crlf.txt"
        f_crlf.write_bytes(b"a\r\nb\r\nc\r\n")
        self.assertEqual(git2svn.detect_file_eol(f_crlf), b"\r\n")

        f_lf = self.path / "lf.txt"
        f_lf.write_bytes(b"a\nb\nc\n")
        self.assertEqual(git2svn.detect_file_eol(f_lf), b"\n")

        f_empty = self.path / "empty.txt"
        f_empty.write_bytes(b"")
        self.assertIsNone(git2svn.detect_file_eol(f_empty))

        f_bin = self.path / "bin.dat"
        f_bin.write_bytes(b"foo\0bar\r\n")
        self.assertIsNone(git2svn.detect_file_eol(f_bin))

    def test_normalize_file_eol(self):
        f = self.path / "mixed.txt"
        # Mixed: CRLF and LF in same file
        f.write_bytes(b"line 1\r\nline 2\nline 3\r\n")
        git2svn.normalize_file_eol(f, target_eol=b"\r\n")
        self.assertEqual(f.read_bytes(), b"line 1\r\nline 2\r\nline 3\r\n")

        git2svn.normalize_file_eol(f, target_eol=b"\n")
        self.assertEqual(f.read_bytes(), b"line 1\nline 2\nline 3\n")

    def test_version_cli(self):
        """Verify -V and --version flags output program version and exit with code 0."""
        import io
        from unittest.mock import patch

        for flag in ["-V", "--version"]:
            with patch("sys.stdout", new=io.StringIO()) as fake_out, self.assertRaises(SystemExit) as cm:
                git2svn.main([flag])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn(f"git2svn {git2svn.get_version()}", fake_out.getvalue())

    def test_bare_invocation_prints_help(self):
        """Verify calling git2svn with no arguments prints full help to stderr and exits with code 1."""
        import io
        from unittest.mock import patch

        with patch("sys.stderr", new=io.StringIO()) as fake_err, self.assertRaises(SystemExit) as cm:
            git2svn.main([])
        self.assertEqual(cm.exception.code, 1)
        output = fake_err.getvalue()
        self.assertIn("Core Actions:", output)
        self.assertIn("stage", output)
        self.assertIn("replay", output)

    def test_setup_command(self):
        """Verify git2svn setup command configures git settings and aliases properly."""
        svn_dir = self.path / "fake_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "fake_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "trunk"], cwd=git_dir, check=True, capture_output=True)
        # Create an initial commit
        (git_dir / "README.md").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        # Create a fake svn-mirror remote branch
        subprocess.run(
            ["git", "update-ref", "refs/remotes/svn-mirror/trunk", "HEAD"],
            cwd=git_dir,
            check=True,
        )

        res = git2svn.main(["--git-dir", str(git_dir), "setup", str(svn_dir)])
        self.assertEqual(res, 0)

        git_repo = git2svn.GitRepo(git_dir)
        self.assertEqual(git_repo.get_config("git2svn.svnDir"), str(svn_dir).replace("\\", "/"))
        self.assertEqual(git_repo.get_config("git2svn.defaultRange"), "svn-mirror/trunk..trunk")
        self.assertIsNone(git_repo.get_config("git2svn.autoUpdate"))
        self.assertEqual(git_repo.get_config("pull.ff"), "only")
        self.assertIn("git2svn replay", git_repo.get_config("alias.svn-push"))
        self.assertIn("git fetch svn-mirror", git_repo.get_config("alias.svn-pull"))

    def test_setup_interactive_input(self):
        """Verify git2svn setup prompts for svn path via input() when tty."""
        svn_dir = self.path / "prompt_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "prompt_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)

        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=str(svn_dir)):
            res = git2svn.main(["--git-dir", str(git_dir), "setup"])
            self.assertEqual(res, 0)
            git_repo = git2svn.GitRepo(git_dir)
            self.assertEqual(git_repo.get_config("git2svn.svnDir"), str(svn_dir).replace("\\", "/"))

    def test_setup_missing_svn_dir_error(self):
        """Verify setup fails with exit code 1 when no svn_dir is provided and non-interactive."""
        git_dir = self.path / "err_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        with patch("sys.stdin.isatty", return_value=False), patch("sys.stderr"):
            res = git2svn.main(["--git-dir", str(git_dir), "setup"])
            self.assertEqual(res, 1)

    def test_setup_invalid_svn_dir(self):
        """Verify setup fails with exit code 1 when path is not an SVN working copy."""
        not_svn = self.path / "not_svn"
        not_svn.mkdir()
        git_dir = self.path / "err_git2"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        with patch("sys.stderr"):
            res = git2svn.main(["--git-dir", str(git_dir), "setup", str(not_svn)])
            self.assertEqual(res, 1)

    def test_cli_missing_ref_errors(self):
        """Verify stage and replay exit with code 1 if ref is missing and no defaultRange configured."""
        svn_dir = self.path / "cli_err_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "cli_err_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        with patch("sys.stderr"):
            self.assertEqual(git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage"]), 1)
            self.assertEqual(git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "replay"]), 1)

    def test_cli_missing_svn_dir(self):
        """Verify error when SVN dir is completely omitted and unconfigured for stage."""
        git_dir = self.path / "cli_no_svn_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        with patch.dict("os.environ", {}, clear=True), patch("sys.stderr"):
            self.assertEqual(git2svn.main(["--git-dir", str(git_dir), "stage", "HEAD"]), 1)

    def test_cli_invalid_git_dir(self):
        """Verify error when git dir is not a git repo."""
        not_git = self.path / "not_git"
        not_git.mkdir()
        with patch("sys.stderr"):
            self.assertEqual(git2svn.main(["--git-dir", str(not_git), "stage", "HEAD"]), 1)

    def test_find_svn_binary_windows_discovery(self):
        """Verify find_svn_binary falls back to candidate paths when on win32."""
        with (
            patch("shutil.which", return_value=None),
            patch("sys.platform", "win32"),
            patch.object(Path, "is_file") as mock_is_file,
        ):
            # When candidate exists
            mock_is_file.return_value = True
            bin_path = git2svn.svn.find_svn_binary()
            self.assertIn("svn.exe", bin_path)

            # When no candidate exists
            mock_is_file.return_value = False
            self.assertEqual(git2svn.svn.find_svn_binary(), "svn")


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


if __name__ == "__main__":
    unittest.main()
