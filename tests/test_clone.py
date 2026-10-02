import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import git2svn
from git2svn.clone import derive_repo_name, run_clone


class TestClone(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_derive_repo_name(self):
        self.assertEqual(derive_repo_name("https://github.com/my-org/my-project.git"), "my-project")
        self.assertEqual(derive_repo_name("git@github.com:my-org/my-project.git/"), "my-project")
        self.assertEqual(derive_repo_name("https://example.com/repo"), "repo")

    def test_run_clone_missing_mirror_url(self):
        ret = run_clone(mirror_url="", svn_url="https://svn.example.com/trunk")
        self.assertEqual(ret, 1)

    def test_run_clone_missing_svn_url(self):
        ret = run_clone(mirror_url="https://example.com/repo.git", svn_url="")
        self.assertEqual(ret, 1)

    def test_run_clone_target_dir_exists_non_empty(self):
        target = self.path / "existing-repo"
        target.mkdir()
        (target / "dummy.txt").write_text("content")
        ret = run_clone(
            mirror_url="https://example.com/repo.git",
            svn_url="https://svn.example.com/trunk",
            target_dir=target,
        )
        self.assertEqual(ret, 1)

    def test_run_clone_dry_run(self):
        target = self.path / "dry-run-repo"
        ret = run_clone(
            mirror_url="https://example.com/repo.git",
            svn_url="https://svn.example.com/trunk",
            target_dir=target,
            origin_url="git@github.com:team/repo.git",
            mirror_remote="svn-mirror",
            dry_run=True,
        )
        self.assertEqual(ret, 0)
        self.assertFalse(target.exists())

    @patch("git2svn.setup.run_setup", return_value=0)
    @patch("git2svn.git.GitRepo.is_valid_repo", return_value=True)
    @patch("git2svn.git.GitRepo.get_remotes", return_value=["svn-mirror"])
    @patch("git2svn.git.GitRepo.run_cmd")
    @patch("git2svn.git.GitRepo.set_config")
    @patch("git2svn.clone.subprocess.run")
    def test_run_clone_success_flow(
        self,
        mock_subproc,
        mock_set_config,
        mock_run_cmd,
        mock_get_remotes,
        mock_is_valid,
        mock_setup,
    ):
        target = self.path / "my-cloned-repo"
        mock_subproc.return_value = MagicMock(returncode=0)
        mock_run_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ret = run_clone(
            mirror_url="https://example.com/mirror.git",
            svn_url="https://svn.example.com/repo/trunk",
            target_dir=target,
            origin_url="git@github.com:team/my-cloned-repo.git",
            mirror_remote="svn-mirror",
        )
        self.assertEqual(ret, 0)

        # 1. Verify git clone was called with --origin svn-mirror
        mock_subproc.assert_called_once_with(
            ["git", "clone", "https://example.com/mirror.git", str(target), "--origin", "svn-mirror"],
            check=False,
        )

        # 2. Verify git2svn setup was invoked
        mock_setup.assert_called_once()
        self.assertEqual(mock_setup.call_args[1]["svn_target"], "https://svn.example.com/repo/trunk")
        self.assertEqual(mock_setup.call_args[0][0].repo_dir, target)

        # 3. Verify git2svn.mirrorRemote was configured
        mock_set_config.assert_called_with("git2svn.mirrorRemote", "svn-mirror")

        # 4. Verify secondary remote origin was added
        mock_run_cmd.assert_called_with(
            ["remote", "add", "origin", "git@github.com:team/my-cloned-repo.git"],
            check=False,
        )

    @patch("git2svn.clone.run_clone", return_value=0)
    def test_cli_dispatch_clone(self, mock_clone):
        ret = git2svn.main(
            [
                "clone",
                "https://example.com/mirror.git",
                "my-dir",
                "--svn-url",
                "https://svn.example.com/trunk",
                "--origin-url",
                "git@github.com:team/repo.git",
            ]
        )
        self.assertEqual(ret, 0)
        mock_clone.assert_called_once()
        kwargs = mock_clone.call_args[1]
        self.assertEqual(kwargs["mirror_url"], "https://example.com/mirror.git")
        self.assertEqual(kwargs["target_dir"], Path("my-dir"))
        self.assertEqual(kwargs["svn_url"], "https://svn.example.com/trunk")
        self.assertEqual(kwargs["origin_url"], "git@github.com:team/repo.git")
        self.assertEqual(kwargs["mirror_remote"], "svn-mirror")
