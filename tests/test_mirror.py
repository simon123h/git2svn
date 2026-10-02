import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import git2svn
from git2svn.mirror import get_git_svn_version, is_git_svn_available, run_init_mirror


class TestMirror(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("git2svn.mirror.subprocess.run")
    def test_is_git_svn_available_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="git-svn version 2.43.0 (svn 1.14.3)\n")
        self.assertTrue(is_git_svn_available())
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[1:], ["svn", "--version"])

    @patch("git2svn.mirror.subprocess.run")
    def test_is_git_svn_available_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="git: 'svn' is not a git command\n")
        self.assertFalse(is_git_svn_available())

    @patch("git2svn.mirror.subprocess.run", side_effect=FileNotFoundError)
    def test_is_git_svn_available_filenotfound(self, mock_run):
        self.assertFalse(is_git_svn_available())

    @patch("git2svn.mirror.subprocess.run")
    def test_get_git_svn_version(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="git-svn version 2.43.0 (svn 1.14.3)\n")
        self.assertEqual(get_git_svn_version(), "git-svn version 2.43.0 (svn 1.14.3)")

    @patch("git2svn.mirror.is_git_svn_available", return_value=False)
    def test_run_init_mirror_git_svn_missing(self, mock_avail):
        ret = run_init_mirror(
            svn_url="svn://example.com/repo",
            target_dir=self.path / "mirror-repo",
        )
        self.assertEqual(ret, 1)

    @patch("git2svn.mirror.is_git_svn_available", return_value=True)
    def test_run_init_mirror_dry_run(self, mock_avail):
        target = self.path / "mirror-repo"
        # Dry run should not create target or run commands
        ret = run_init_mirror(
            svn_url="https://svn.example.com/repo",
            target_dir=target,
            stdlayout=True,
            prefix="svn-mirror/",
            dry_run=True,
        )
        self.assertEqual(ret, 0)
        self.assertFalse(target.exists())

    @patch("git2svn.setup.run_setup", return_value=0)
    @patch("git2svn.git.GitRepo.run_cmd")
    @patch("git2svn.mirror.subprocess.run")
    @patch("git2svn.mirror.is_git_svn_available", return_value=True)
    def test_run_init_mirror_success_flow(self, mock_avail, mock_subproc, mock_run_cmd, mock_setup):
        target = self.path / "mirror-repo"
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_run_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ret = run_init_mirror(
            svn_url="https://svn.example.com/repo",
            target_dir=target,
            stdlayout=True,
            prefix="svn-mirror/",
            from_revision="100:HEAD",
            no_fetch=False,
        )
        self.assertEqual(ret, 0)
        self.assertTrue(target.exists())

        # Verify git svn init was executed via git_repo.run_cmd
        mock_run_cmd.assert_any_call(
            ["svn", "init", "-s", "--prefix=svn-mirror/", "https://svn.example.com/repo"], check=False
        )

        # Verify git svn fetch was executed via subprocess.run
        mock_subproc.assert_any_call(["git", "svn", "fetch", "-r", "100:HEAD"], cwd=target, check=False)

        # check run_setup called
        mock_setup.assert_called_once()
        self.assertEqual(mock_setup.call_args[1]["svn_target"], "https://svn.example.com/repo/trunk")
        self.assertEqual(mock_setup.call_args[0][0].repo_dir, target)

    @patch("git2svn.setup.run_setup", return_value=0)
    @patch("git2svn.git.GitRepo.run_cmd")
    @patch("git2svn.mirror.subprocess.run")
    @patch("git2svn.mirror.is_git_svn_available", return_value=True)
    def test_run_init_mirror_custom_paths_and_no_fetch(self, mock_avail, mock_subproc, mock_run_cmd, mock_setup):
        target = self.path / "mirror-repo"
        mock_subproc.return_value = MagicMock(returncode=0, stdout="")
        mock_run_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ret = run_init_mirror(
            svn_url="https://svn.example.com/repo",
            target_dir=target,
            trunk="my-trunk",
            branches="my-branches",
            tags="my-tags",
            prefix="custom/",
            no_fetch=True,
        )
        self.assertEqual(ret, 0)

        # Verify git svn init arguments
        mock_run_cmd.assert_any_call(
            [
                "svn",
                "init",
                "-T",
                "my-trunk",
                "-b",
                "my-branches",
                "-t",
                "my-tags",
                "--prefix=custom/",
                "https://svn.example.com/repo",
            ],
            check=False,
        )
        mock_setup.assert_called_once()
        self.assertEqual(mock_setup.call_args[1]["svn_target"], "https://svn.example.com/repo/my-trunk")

    @patch("git2svn.mirror.run_init_mirror", return_value=0)
    def test_cli_dispatch_init_mirror(self, mock_init_mirror):
        ret = git2svn.main(
            ["init-mirror", "https://svn.example.com/repo", "my-mirror", "--stdlayout", "--revision", "50:HEAD"]
        )
        self.assertEqual(ret, 0)
        mock_init_mirror.assert_called_once()
        kwargs = mock_init_mirror.call_args[1]
        self.assertEqual(kwargs["svn_url"], "https://svn.example.com/repo")
        self.assertEqual(kwargs["target_dir"], Path("my-mirror"))
        self.assertTrue(kwargs["stdlayout"])
        self.assertEqual(kwargs["from_revision"], "50:HEAD")
