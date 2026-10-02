import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn
from git2svn.snapshot import SnapshotSynchronizer


class TestSnapshotSynchronizer(unittest.TestCase):
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
        self.synchronizer = SnapshotSynchronizer(self.git_repo, self.svn_ws, dry_run=False)

    def tearDown(self):
        self.git_dir.cleanup()
        self.svn_dir.cleanup()

    def test_snapshot_deleted_directory(self):
        """Verify snapshot cleans up deleted directories with shutil.rmtree."""
        # Create an SVN sub-directory with a file
        sub_dir = self.svn_path / "old_dir"
        sub_dir.mkdir()
        (sub_dir / "old_file.txt").write_text("old")

        # Initial Git commit has a file outside old_dir
        (self.git_path / "keep.txt").write_text("keep")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.git_path, check=True)

        with patch.object(self.svn_ws, "stage_rm"):
            # Mock SVN versioned files including old_dir
            with patch.object(self.svn_ws, "get_versioned_files", return_value=[Path("old_dir"), Path("keep.txt")]):
                self.synchronizer.align_workspace("HEAD")
        self.assertFalse(sub_dir.exists())

    def test_snapshot_symlink_create_and_update(self):
        """Verify snapshot creates, updates, and ignores unchanged symlinks."""
        # Git commit with symlink
        (self.git_path / "target.txt").write_text("hello target\n")
        symlink_path = self.git_path / "link.txt"
        os.symlink("target.txt", symlink_path)
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "add symlink"], cwd=self.git_path, check=True)

        with (
            patch.object(self.svn_ws, "stage_add"),
            patch.object(self.svn_ws, "get_versioned_files", return_value=[]),
        ):
            self.synchronizer.align_workspace("HEAD")

        svn_link = self.svn_path / "link.txt"
        self.assertTrue(svn_link.is_symlink())
        self.assertEqual(os.readlink(svn_link), "target.txt")

        # Now update symlink in Git to point to target2.txt
        symlink_path.unlink()
        os.symlink("target2.txt", symlink_path)
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "update symlink"], cwd=self.git_path, check=True)

        with (
            patch.object(self.svn_ws, "stage_add"),
            patch.object(self.svn_ws, "get_versioned_files", return_value=[Path("target.txt"), Path("link.txt")]),
        ):
            self.synchronizer.align_workspace("HEAD")

        self.assertEqual(os.readlink(svn_link), "target2.txt")

        # Re-running when unchanged should not modify symlink
        with (
            patch.object(self.svn_ws, "stage_add"),
            patch.object(self.svn_ws, "get_versioned_files", return_value=[Path("target.txt"), Path("link.txt")]),
            patch("os.symlink") as mock_os_symlink,
        ):
            self.synchronizer.align_workspace("HEAD")
            mock_os_symlink.assert_not_called()

    def test_snapshot_dry_run_mode(self):
        """Verify dry-run mode prints planned actions without altering disk or staging."""
        (self.git_path / "new_file.txt").write_text("new content")
        (self.git_path / "new_link.txt")
        os.symlink("new_file.txt", self.git_path / "new_link.txt")
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "add file and link"], cwd=self.git_path, check=True)

        # Existing modified file and link in SVN
        (self.svn_path / "new_file.txt").write_text("old content")
        os.symlink("old_target.txt", self.svn_path / "new_link.txt")

        dry_synchronizer = SnapshotSynchronizer(self.git_repo, self.svn_ws, dry_run=True)
        fake_stdout = io.StringIO()
        with (
            patch("sys.stdout", fake_stdout),
            patch.object(self.svn_ws, "get_versioned_files", return_value=[Path("new_file.txt"), Path("new_link.txt")]),
            patch.object(self.svn_ws, "stage_add") as mock_stage_add,
            patch.object(self.svn_ws, "stage_rm") as mock_stage_rm,
        ):
            dry_synchronizer.align_workspace("HEAD")
            mock_stage_add.assert_not_called()
            mock_stage_rm.assert_not_called()

        out = fake_stdout.getvalue()
        self.assertIn("[DRY-RUN] Update file content: new_file.txt", out)
        self.assertIn("[DRY-RUN] Update symlink new_link.txt -> new_file.txt", out)

    def test_snapshot_executable_property_sync(self):
        """Verify executable property is synced for added and modified files in snapshot mode."""
        script_file = self.git_path / "run.sh"
        script_file.write_text("#!/bin/sh\necho hi\n")
        script_file.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.git_path, check=True)
        subprocess.run(["git", "commit", "-m", "add executable script"], cwd=self.git_path, check=True)

        with (
            patch.object(self.svn_ws, "stage_add"),
            patch.object(self.svn_ws, "get_versioned_files", return_value=[Path("run.sh")]),
            patch.object(self.svn_ws, "sync_file_executable_property") as mock_sync_prop,
        ):
            self.synchronizer.align_workspace("HEAD")
            mock_sync_prop.assert_called_with(Path("run.sh"), is_executable=True)


if __name__ == "__main__":
    unittest.main()
