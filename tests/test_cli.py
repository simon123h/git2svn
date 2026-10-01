import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestCli(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stage_single_commit_args(self):
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

    def test_version_cli(self):
        """Verify -V and --version flags output program version and exit with code 0."""
        for flag in ["-V", "--version"]:
            with patch("sys.stdout", new=io.StringIO()) as fake_out, self.assertRaises(SystemExit) as cm:
                git2svn.main([flag])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn(f"git2svn {git2svn.get_version()}", fake_out.getvalue())

    def test_bare_invocation_prints_help(self):
        """Verify calling git2svn with no arguments prints full help to stderr and exits with code 1."""
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
        (git_dir / "README.md").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

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
        self.assertEqual(git_repo.get_config("alias.svn-status"), "!git2svn status")

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

    def test_setup_detects_origin_remote(self):
        """Verify git2svn setup detects origin/trunk and creates origin-based aliases."""
        svn_dir = self.path / "origin_fake_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "origin_fake_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "trunk"], cwd=git_dir, check=True, capture_output=True)
        (git_dir / "README.md").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/trunk", "HEAD"],
            cwd=git_dir,
            check=True,
        )

        res = git2svn.main(["--git-dir", str(git_dir), "setup", str(svn_dir)])
        self.assertEqual(res, 0)

        git_repo = git2svn.GitRepo(git_dir)
        self.assertEqual(git_repo.get_config("git2svn.defaultRange"), "origin/trunk..trunk")
        self.assertIn("git fetch origin", git_repo.get_config("alias.svn-pull"))

    def test_setup_prints_tip_when_no_mirror_detected(self):
        """Verify git2svn setup prints an informative tip when no remote mirror branch is found."""
        svn_dir = self.path / "tip_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "tip_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)

        with patch("sys.stdout", new=io.StringIO()) as fake_out:
            res = git2svn.main(["--git-dir", str(git_dir), "setup", str(svn_dir)])
            self.assertEqual(res, 0)
            output = fake_out.getvalue()
            self.assertIn("Tip: No remote mirror branch", output)
            self.assertIn("git remote add origin", output)

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

    def test_setup_with_svn_url_and_checkout(self):
        """Verify setup with an SVN URL checks out managed working copy and configures svnUrl/svnDir."""
        git_dir = self.path / "url_setup_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)
        (git_dir / "README.md").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        with patch("git2svn.setup.checkout_working_copy") as mock_checkout:
            res = git2svn.main(["--git-dir", str(git_dir), "setup", "https://svn.example.com/trunk"])
            self.assertEqual(res, 0)
            managed_dir = git2svn.svn.get_default_managed_svn_dir(git_dir)
            mock_checkout.assert_called_once_with("https://svn.example.com/trunk", managed_dir)

            git_repo = git2svn.GitRepo(git_dir)
            self.assertEqual(git_repo.get_config("git2svn.svnUrl"), "https://svn.example.com/trunk")
            self.assertEqual(git_repo.get_config("git2svn.svnDir"), str(managed_dir).replace("\\", "/"))

    def test_setup_with_svn_url_checkout_error(self):
        """Verify setup fails cleanly if checkout raises SvnError."""
        git_dir = self.path / "url_fail_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)

        with (
            patch("git2svn.setup.checkout_working_copy", side_effect=git2svn.SvnError("Checkout connection error")),
            patch("sys.stderr"),
        ):
            res = git2svn.main(["--git-dir", str(git_dir), "setup", "https://invalid.example.com/repo"])
            self.assertEqual(res, 1)

    def test_cli_auto_checkout_managed_working_copy(self):
        """Verify CLI commands automatically check out working copy if missing and svnUrl is set."""
        git_dir = self.path / "auto_co_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)
        (git_dir / "file.txt").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        git_repo = git2svn.GitRepo(git_dir)
        git_repo.set_config("git2svn.svnUrl", "https://svn.example.com/trunk")
        managed_dir = git2svn.svn.get_default_managed_svn_dir(git_dir)

        # Mock checkout so that it creates .svn in managed_dir
        def fake_checkout(url, dest):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".svn").mkdir()

        with (
            patch("git2svn.cli.checkout_working_copy", side_effect=fake_checkout) as mock_co,
            patch("git2svn.cli.Synchronizer") as mock_sync_cls,
        ):
            code = git2svn.main(["--git-dir", str(git_dir), "stage", "HEAD"])
            self.assertEqual(code, 0)
            mock_co.assert_called_once_with("https://svn.example.com/trunk", managed_dir)
            self.assertTrue(mock_sync_cls.called)

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
            mock_is_file.return_value = True
            bin_path = git2svn.svn.find_svn_binary()
            self.assertIn("svn.exe", bin_path)

            mock_is_file.return_value = False
            self.assertEqual(git2svn.svn.find_svn_binary(), "svn")

    def test_diff_subcommand_args(self):
        """Verify diff subcommand argument parsing."""
        args = git2svn.parse_cli_args(["diff", "-s", "/path/to/svn"])
        self.assertEqual(args.command, "diff")
        self.assertFalse(args.stat)

        args_stat = git2svn.parse_cli_args(["diff", "--stat", "-s", "/path/to/svn"])
        self.assertEqual(args_stat.command, "diff")
        self.assertTrue(args_stat.stat)

        args_stage_diff = git2svn.parse_cli_args(["stage", "HEAD", "-p", "-s", "/path/to/svn"])
        self.assertEqual(args_stage_diff.command, "stage")
        self.assertTrue(args_stage_diff.diff)

    def test_diff_command_execution(self):
        """Verify git2svn diff invokes synchronizer.diff and prints output."""
        git_dir = self.path / "diff_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "diff_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls, patch("sys.stdout", new=io.StringIO()) as fake_out:
            mock_sync = mock_sync_cls.return_value
            mock_sync.diff.return_value = "Index: file.txt\n=== diff content ===\n"

            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "diff"])
            self.assertEqual(code, 0)
            mock_sync.diff.assert_called_once_with(stat=False)
            self.assertIn("=== diff content ===", fake_out.getvalue())

    def test_diff_stat_execution(self):
        """Verify git2svn diff --stat passes stat=True."""
        git_dir = self.path / "stat_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "stat_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls, patch("sys.stdout", new=io.StringIO()) as fake_out:
            mock_sync = mock_sync_cls.return_value
            mock_sync.diff.return_value = "file.txt | 2 +-\n"

            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "diff", "--stat"])
            self.assertEqual(code, 0)
            mock_sync.diff.assert_called_once_with(stat=True)
            self.assertIn("file.txt | 2 +-", fake_out.getvalue())

    def test_stage_with_diff_flag_prints_preview(self):
        """Verify git2svn stage -p / --diff invokes diff and prints preview output."""
        git_dir = self.path / "stage_diff_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "stage_diff_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls, patch("sys.stdout", new=io.StringIO()) as fake_out:
            mock_sync = mock_sync_cls.return_value
            mock_sync.diff.return_value = "Index: staged.txt\n"

            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage", "HEAD", "--diff"])
            self.assertEqual(code, 0)
            mock_sync.stage.assert_called_once()
            mock_sync.diff.assert_called_once_with()
            self.assertIn("Index: staged.txt", fake_out.getvalue())

    def test_stage_missing_ref_error(self):
        """Verify error when stage is invoked without ref or configured defaultRange."""
        git_dir = self.path / "no_ref_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "no_ref_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("sys.stderr"):
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage"])
            self.assertEqual(code, 1)

    def test_replay_missing_ref_error(self):
        """Verify error when replay is invoked without ref or configured defaultRange."""
        git_dir = self.path / "no_ref_replay_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "no_ref_replay_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("sys.stderr"):
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "replay"])
            self.assertEqual(code, 1)

    def test_cli_catches_svn_error(self):
        """Verify CLI main cleanly formats and returns error code on SvnLockError and SvnError."""
        git_dir = self.path / "svn_err_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "svn_err_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with (
            patch("git2svn.cli.Synchronizer.stage", side_effect=git2svn.SvnLockError("Locked", returncode=2)),
            patch("sys.stderr"),
        ):
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage", "HEAD"])
            self.assertEqual(code, 2)

        with (
            patch("git2svn.cli.Synchronizer.stage", side_effect=git2svn.SvnError("General SVN fail", returncode=3)),
            patch("sys.stderr"),
        ):
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage", "HEAD"])
            self.assertEqual(code, 3)

    def test_stage_snapshot_defaults_to_head(self):
        """Verify stage --snapshot defaults ref1 to HEAD when omitted."""
        git_dir = self.path / "snap_head_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "snap_head_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls:
            mock_sync = mock_sync_cls.return_value
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "stage", "--snapshot"])
            self.assertEqual(code, 0)
            mock_sync.stage.assert_called_once_with("HEAD", None, use_copy=False, snapshot=True)

    def test_cli_clean_execution(self):
        """Verify git2svn clean invokes synchronizer.clean and exits with code 0."""
        git_dir = self.path / "clean_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "clean_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls:
            mock_sync = mock_sync_cls.return_value
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "clean"])
            self.assertEqual(code, 0)
            mock_sync.clean.assert_called_once()

    def test_cli_clean_purge_execution(self):
        """Verify git2svn clean --purge invokes synchronizer.purge_workspace and exits with code 0."""
        git_dir = self.path / "purge_git"
        git_dir.mkdir()
        subprocess.run(["git", "init"], cwd=git_dir, check=True, capture_output=True)

        svn_dir = self.path / "purge_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        with patch("git2svn.cli.Synchronizer") as mock_sync_cls:
            mock_sync = mock_sync_cls.return_value
            code = git2svn.main(["--git-dir", str(git_dir), "--svn-dir", str(svn_dir), "clean", "--purge"])
            self.assertEqual(code, 0)
            mock_sync.purge_workspace.assert_called_once()

    def test_cli_stage_managed_working_copy_prints_inspection_hint(self):
        """Verify git2svn stage outputs working copy path and inspection tip when using managed workspace."""
        git_dir = self.path / "managed_stage_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=git_dir, check=True, capture_output=True)
        (git_dir / "file.txt").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        git_repo = git2svn.GitRepo(git_dir)
        git_repo.set_config("git2svn.svnUrl", "https://svn.example.com/trunk")
        managed_dir = git2svn.svn.get_default_managed_svn_dir(git_dir)
        managed_dir.mkdir(parents=True, exist_ok=True)
        (managed_dir / ".svn").mkdir()

        with (
            patch("git2svn.cli.Synchronizer") as mock_sync_cls,
            patch("sys.stdout", new=io.StringIO()) as fake_out,
        ):
            code = git2svn.main(["--git-dir", str(git_dir), "stage", "HEAD"])
            self.assertEqual(code, 0)
            mock_sync_cls.return_value.stage.assert_called_once()
            output = fake_out.getvalue()
            self.assertIn(f"Working copy : {managed_dir}", output)
            self.assertIn("Tip: Run 'git2svn diff' (or 'git2svn status') to inspect uncommitted changes.", output)

    def test_setup_installs_pre_push_hook(self):
        """Verify git2svn setup installs pre-push hook guarding against push to mirror."""
        svn_dir = self.path / "hook_fake_svn"
        svn_dir.mkdir()
        (svn_dir / ".svn").mkdir()

        git_dir = self.path / "hook_fake_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "trunk"], cwd=git_dir, check=True, capture_output=True)
        (git_dir / "file.txt").write_text("hello")
        subprocess.run(["git", "config", "user.name", "Test"], cwd=git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=git_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=git_dir, check=True)

        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/trunk", "HEAD"],
            cwd=git_dir,
            check=True,
        )

        res = git2svn.main(["--git-dir", str(git_dir), "setup", str(svn_dir)])
        self.assertEqual(res, 0)

        hook_file = git_dir / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook_file.is_file())
        hook_content = hook_file.read_text()
        self.assertIn("# --- START GIT2SVN PRE-PUSH GUARD ---", hook_content)
        self.assertIn('REMOTE_NAME="$1"', hook_content)
        self.assertIn('if [ "$REMOTE_NAME" = "origin" ]; then', hook_content)
        self.assertIn('if [ "$remote_ref" = "refs/heads/trunk" ]; then', hook_content)

        # Ensure calling setup again idempotently updates rather than duplicates
        res2 = git2svn.main(["--git-dir", str(git_dir), "setup", str(svn_dir)])
        self.assertEqual(res2, 0)
        self.assertEqual(hook_file.read_text().count("# --- START GIT2SVN PRE-PUSH GUARD ---"), 1)

    def test_pre_push_hook_blocks_mirror_push(self):
        """Verify that the generated pre-push hook script blocks push to origin/trunk but permits feature branches."""
        git_dir = self.path / "hook_exec_git"
        git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "trunk"], cwd=git_dir, check=True, capture_output=True)

        git2svn.setup.install_pre_push_hook(git2svn.GitRepo(git_dir), "origin", "trunk")
        hook_file = git_dir / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook_file.is_file())

        # Determine shell runner (on Windows, POSIX shell scripts cannot be spawned directly without sh/bash)
        sh_bin = shutil.which("sh") or shutil.which("bash")
        if sys.platform.startswith("win") and not sh_bin:
            self.skipTest("No sh or bash found on Windows to execute pre-push hook directly")

        base_cmd = [sh_bin, str(hook_file)] if sh_bin else [str(hook_file)]

        # 1. Simulate pushing trunk to origin -> must exit 1 and output error
        push_input = "refs/heads/trunk aaaa refs/heads/trunk bbbb\n"
        proc_block = subprocess.run(
            [*base_cmd, "origin", "https://github.com/example/repo.git"],
            input=push_input,
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc_block.returncode, 1)
        self.assertIn("[git2svn pre-push guard] ERROR: Direct push to 'origin/trunk' is blocked!", proc_block.stderr)
        self.assertIn("git svn-push", proc_block.stderr)

        # 2. Simulate pushing a feature branch to origin -> must succeed (exit 0)
        feature_input = "refs/heads/feature/login aaaa refs/heads/feature/login bbbb\n"
        proc_allow_feature = subprocess.run(
            [*base_cmd, "origin", "https://github.com/example/repo.git"],
            input=feature_input,
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc_allow_feature.returncode, 0)

        # 3. Simulate pushing trunk to a personal fork remote (e.g. 'myfork') -> must succeed (exit 0)
        proc_allow_remote = subprocess.run(
            [*base_cmd, "myfork", "https://github.com/user/fork.git"],
            input=push_input,
            text=True,
            capture_output=True,
        )
        self.assertEqual(proc_allow_remote.returncode, 0)


if __name__ == "__main__":
    unittest.main()
