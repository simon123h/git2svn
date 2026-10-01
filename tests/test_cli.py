import io
import subprocess
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
            mock_is_file.return_value = True
            bin_path = git2svn.svn.find_svn_binary()
            self.assertIn("svn.exe", bin_path)

            mock_is_file.return_value = False
            self.assertEqual(git2svn.svn.find_svn_binary(), "svn")


if __name__ == "__main__":
    unittest.main()
