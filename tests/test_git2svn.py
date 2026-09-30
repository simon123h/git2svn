#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

import git2svn


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
    def test_cherry_pick_args(self):
        args = git2svn.parse_cli_args(["cherry-pick", "abc1234", "--svn-dir", "/path/to/svn"])
        self.assertEqual(args.command, "cherry-pick")
        self.assertEqual(args.commit_hash, "abc1234")
        self.assertEqual(args.svn_dir, Path("/path/to/svn"))
        self.assertFalse(args.commit)

    def test_cherry_pick_commit_flag(self):
        args = git2svn.parse_cli_args(["cherry-pick", "abc1234", "--svn-dir", "/path/to/svn", "--commit"])
        self.assertTrue(args.commit)

        args_short = git2svn.parse_cli_args(["cherry-pick", "abc1234", "-s", "/path/to/svn", "-c"])
        self.assertTrue(args_short.commit)

    def test_squash_args(self):
        args = git2svn.parse_cli_args(["--svn-dir", "/path/to/svn", "squash", "main", "feature"])
        self.assertEqual(args.command, "squash")
        self.assertEqual(args.start_ref, "main")
        self.assertEqual(args.end_ref, "feature")
        self.assertEqual(args.svn_dir, Path("/path/to/svn"))

    def test_sync_args(self):
        args = git2svn.parse_cli_args(["sync", "main", "feature", "-s", "/path/to/svn", "-n", "-v"])
        self.assertEqual(args.command, "sync")
        self.assertEqual(args.base_ref, "main")
        self.assertEqual(args.target_ref, "feature")
        self.assertEqual(args.svn_dir, Path("/path/to/svn"))
        self.assertTrue(args.dry_run)
        self.assertTrue(args.verbose)


class TestSvnWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self.temp_dir.name)
        (self.workspace_dir / ".svn").mkdir()
        self.svn = git2svn.SvnWorkspace(self.workspace_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_commit(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="Committed revision 42.\n", stderr="")
        self.svn.commit("feat: some feature\n\nDetailed body.")
        mock_run.assert_called_once_with(
            ["svn", "commit", "-m", "feat: some feature\n\nDetailed body."],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_stage_add(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        rel_path = Path("nested/folder/file.txt")

        self.svn.stage_add(rel_path)

        # Ensure parent directory was created on disk
        self.assertTrue((self.workspace_dir / "nested" / "folder").is_dir())
        mock_run.assert_called_once_with(
            ["svn", "add", "nested/folder/file.txt", "--parents"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_stage_rm(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        rel_path = Path("file_to_remove.txt")

        self.svn.stage_rm(rel_path)

        mock_run.assert_called_once_with(
            ["svn", "rm", "file_to_remove.txt"],
            cwd=self.workspace_dir,
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_apply_structural_changes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        changes = [
            git2svn.FileChange("A", "new_file.txt"),
            git2svn.FileChange("D", "old_file.txt"),
            git2svn.FileChange("M", "mod_file.txt"),
            git2svn.FileChange("R", "new_name.txt", "old_name.txt"),
        ]

        self.svn.apply_structural_changes(changes)

        # Expected calls:
        # 1. rm old_file.txt (D)
        # 2. rm old_name.txt (R)
        # 3. add new_name.txt --parents (R)
        # 4. add new_file.txt --parents (A)
        # Modified produces no svn calls
        called_cmds = [call_args[0][0] for call_args in mock_run.call_args_list]
        self.assertIn(["svn", "rm", "old_file.txt"], called_cmds)
        self.assertIn(["svn", "rm", "old_name.txt"], called_cmds)
        self.assertIn(["svn", "add", "new_name.txt", "--parents"], called_cmds)
        self.assertIn(["svn", "add", "new_file.txt", "--parents"], called_cmds)
        self.assertNotIn(["svn", "add", "mod_file.txt", "--parents"], called_cmds)


class TestPatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name)
        self.patcher = git2svn.Patcher(self.target_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_diff_creates_and_modifies_files(self):
        target_file = self.target_dir / "sample.txt"
        target_file.write_text("Hello\nWorld\n")

        diff = (
            "--- a/sample.txt\n"
            "+++ b/sample.txt\n"
            "@@ -1,2 +1,3 @@\n"
            " Hello\n"
            "+Awesome\n"
            " World\n"
        )
        self.patcher.apply_diff(diff)
        self.assertEqual(target_file.read_text(), "Hello\nAwesome\nWorld\n")

    def test_apply_diff_empty(self):
        # Empty diff should not invoke patch command or raise error
        with patch("subprocess.run") as mock_run:
            self.patcher.apply_diff("")
            mock_run.assert_not_called()


class TestSynchronizer(unittest.TestCase):
    def setUp(self):
        self.git_dir = tempfile.TemporaryDirectory()
        self.svn_dir = tempfile.TemporaryDirectory()
        self.git_path = Path(self.git_dir.name)
        self.svn_path = Path(self.svn_dir.name)
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
    def test_cherry_pick(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Commit 1 in Git
        f1 = self.git_path / "hello.txt"
        f1.write_text("v1\n")
        subprocess.run(["git", "add", "hello.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.git_path, check=True)

        # Initialize file in SVN workspace
        (self.svn_path / "hello.txt").write_text("v1\n")

        # Commit 2 in Git (cherry-pick target)
        f1.write_text("v2\n")
        f2 = self.git_path / "nested" / "new.txt"
        f2.parent.mkdir(parents=True, exist_ok=True)
        f2.write_text("new file\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "update"], cwd=self.git_path, check=True)

        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Execute cherry-pick
        self.sync_mgr.cherry_pick(commit_hash)

        # Check content in SVN workspace
        self.assertEqual((self.svn_path / "hello.txt").read_text(), "v2\n")
        self.assertEqual((self.svn_path / "nested" / "new.txt").read_text(), "new file\n")

        # Check SVN staging commands called
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["add", "nested/new.txt", "--parents"], called_args)
        # Verify commit is NEVER called by default
        self.assertFalse(any(cmd[0] == "commit" for cmd in called_args))

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_cherry_pick_with_commit(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="Committed revision 100.\n", stderr="")

        # Commit in Git with multi-line message
        commit_msg = "feat(core): add feature X\n\nDetailed explanation of feature X."
        f = self.git_path / "feature.txt"
        f.write_text("feature content\n")
        subprocess.run(["git", "add", "feature.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=self.git_path, check=True)

        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Execute cherry-pick with commit_svn=True
        self.sync_mgr.cherry_pick(commit_hash, commit_svn=True)

        # Check content in SVN workspace
        self.assertEqual((self.svn_path / "feature.txt").read_text(), "feature content\n")

        # Check SVN staging and commit were called
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["add", "feature.txt", "--parents"], called_args)
        self.assertIn(["commit", "-m", commit_msg], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_cherry_pick_root_commit(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Commit 1 (root commit)
        f1 = self.git_path / "root.txt"
        f1.write_text("root content\n")
        subprocess.run(["git", "add", "root.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "root"], cwd=self.git_path, check=True)

        commit_hash = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True
        ).stdout.strip()

        # Execute cherry-pick of root commit
        self.sync_mgr.cherry_pick(commit_hash)

        self.assertEqual((self.svn_path / "root.txt").read_text(), "root content\n")
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["add", "root.txt", "--parents"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_squash(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Commit 1: base
        f = self.git_path / "code.txt"
        f.write_text("base\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        # SVN has base
        (self.svn_path / "code.txt").write_text("base\n")

        # Commit 2: step 1
        f.write_text("step1\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "step1"], cwd=self.git_path, check=True)

        # Commit 3: step 2
        f.write_text("step2 finalized\n")
        subprocess.run(["git", "add", "code.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "step2"], cwd=self.git_path, check=True)
        head_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        # Squash base..head
        self.sync_mgr.squash(base_hash, head_hash)

        self.assertEqual((self.svn_path / "code.txt").read_text(), "step2 finalized\n")

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_sync_brute_force_copy(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f1 = self.git_path / "binary_sim.bin"
        f1.write_bytes(b"\x00\x01\x02")
        del_f = self.git_path / "to_delete.txt"
        del_f.write_text("delete me\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        # SVN workspace has base state
        (self.svn_path / "binary_sim.bin").write_bytes(b"\x00\x01\x02")
        (self.svn_path / "to_delete.txt").write_text("delete me\n")

        # Target commit: modify binary, delete file, add new file
        f1.write_bytes(b"\x00\xFF\xFE\xFD")
        del_f.unlink()
        f_add = self.git_path / "sub" / "added.txt"
        f_add.parent.mkdir(parents=True, exist_ok=True)
        f_add.write_text("added content\n")
        subprocess.run(["git", "add", "-A"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "target"], cwd=self.git_path, check=True)
        target_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        # Sync
        self.sync_mgr.sync(base_hash, target_hash)

        # Verify binary file copied cleanly
        self.assertEqual((self.svn_path / "binary_sim.bin").read_bytes(), b"\x00\xFF\xFE\xFD")
        # Verify added file copied
        self.assertEqual((self.svn_path / "sub" / "added.txt").read_text(), "added content\n")
        # Verify deleted file removed
        self.assertFalse((self.svn_path / "to_delete.txt").exists())

        # Verify SVN commands
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["rm", "to_delete.txt"], called_args)
        self.assertIn(["add", "sub/added.txt", "--parents"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_sync_rename(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        old_file = self.git_path / "legacy.txt"
        old_file.write_text("rename me\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)
        base_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        # SVN workspace starts with legacy.txt
        (self.svn_path / "legacy.txt").write_text("rename me\n")

        # Git commit with rename
        subprocess.run(["git", "mv", "legacy.txt", "modern.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "rename"], cwd=self.git_path, check=True)
        target_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        self.sync_mgr.sync(base_hash, target_hash)

        # Check file state in SVN
        self.assertFalse((self.svn_path / "legacy.txt").exists())
        self.assertEqual((self.svn_path / "modern.txt").read_text(), "rename me\n")

        # Check SVN staging calls
        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["rm", "legacy.txt"], called_args)
        self.assertIn(["add", "modern.txt", "--parents"], called_args)

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_cherry_pick_rename(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        # Base commit
        f = self.git_path / "original.txt"
        f.write_text("original content\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=self.git_path, check=True)

        (self.svn_path / "original.txt").write_text("original content\n")

        # Rename commit
        subprocess.run(["git", "mv", "original.txt", "renamed.txt"], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "rename commit"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        self.sync_mgr.cherry_pick(commit_hash)

        self.assertFalse((self.svn_path / "original.txt").exists())
        self.assertEqual((self.svn_path / "renamed.txt").read_text(), "original content\n")

        called_args = [c[0][0] for c in mock_svn_cmd.call_args_list]
        self.assertIn(["rm", "original.txt"], called_args)
        self.assertIn(["add", "renamed.txt", "--parents"], called_args)

    def test_dry_run(self):
        f = self.git_path / "draft.txt"
        f.write_text("draft\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "draft"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        dry_svn = git2svn.SvnWorkspace(self.svn_path, dry_run=True)
        dry_patcher = git2svn.Patcher(self.svn_path, dry_run=True)
        dry_sync = git2svn.Synchronizer(self.git_repo, dry_svn, dry_patcher, dry_run=True)

        dry_sync.cherry_pick(commit_hash)

        # File should NOT be created in SVN workspace because dry_run=True
        self.assertFalse((self.svn_path / "draft.txt").exists())

    @patch.object(git2svn.SvnWorkspace, "run_cmd")
    def test_main_cli(self, mock_svn_cmd):
        mock_svn_cmd.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        f = self.git_path / "cli_test.txt"
        f.write_text("cli test\n")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "cli test"], cwd=self.git_path, check=True)
        commit_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.git_path, capture_output=True, text=True).stdout.strip()

        code = git2svn.main([
            "--git-dir", str(self.git_path),
            "--svn-dir", str(self.svn_path),
            "cherry-pick", commit_hash,
        ])
        self.assertEqual(code, 0)
        self.assertEqual((self.svn_path / "cli_test.txt").read_text(), "cli test\n")


if __name__ == "__main__":
    unittest.main()
