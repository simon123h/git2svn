from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from git2svn.cli import main as cli_main
from git2svn.svn import find_svn_binary

SVN_BIN = find_svn_binary()
SVNADMIN_BIN = shutil.which("svnadmin")
GIT_BIN = shutil.which("git")

# E2E requires git, svn, and svnadmin
HAS_E2E_DEPENDENCIES = bool(GIT_BIN and shutil.which(SVN_BIN) and SVNADMIN_BIN)


@unittest.skipUnless(HAS_E2E_DEPENDENCIES, "E2E tests require 'git', 'svn', and 'svnadmin' in PATH")
class TestGit2SvnE2E(unittest.TestCase):
    """
    End-to-end tests using real Git and real SVN repositories (file:// protocol).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.git_dir = self.root_path / "git_repo"
        self.svn_repo_dir = self.root_path / "svn_remote_repo"
        self.svn_wc_dir = self.root_path / "svn_wc"

        self.git_dir.mkdir()
        self.svn_wc_dir.mkdir()

        # 1. Initialize real local SVN repository
        subprocess.run(
            [SVNADMIN_BIN, "create", str(self.svn_repo_dir)],
            check=True,
            capture_output=True,
        )

        # 2. Checkout working copy from local file:// repo
        repo_url = self.svn_repo_dir.as_uri()
        subprocess.run(
            [SVN_BIN, "checkout", repo_url, str(self.svn_wc_dir)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        # 3. Initialize real Git repo
        subprocess.run([GIT_BIN, "init"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run([GIT_BIN, "config", "user.name", "E2E Test"], cwd=self.git_dir, check=True)
        subprocess.run([GIT_BIN, "config", "user.email", "e2e@example.com"], cwd=self.git_dir, check=True)
        # Avoid potential commit signing or crlf warnings interfering
        subprocess.run([GIT_BIN, "config", "commit.gpgsign", "false"], cwd=self.git_dir, check=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git_commit(self, msg: str) -> str:
        subprocess.run([GIT_BIN, "add", "."], cwd=self.git_dir, check=True)
        subprocess.run([GIT_BIN, "commit", "-m", msg], cwd=self.git_dir, check=True, capture_output=True)
        res = subprocess.run(
            [GIT_BIN, "rev-parse", "HEAD"],
            cwd=self.git_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return res.stdout.strip()

    def _svn_status(self) -> str:
        res = subprocess.run(
            [SVN_BIN, "status"],
            cwd=self.svn_wc_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return res.stdout

    def _svn_log_messages(self) -> list[str]:
        res = subprocess.run(
            [SVN_BIN, "log", "-r", "1:HEAD", "-q"],
            cwd=self.svn_wc_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # Detailed log with messages
        res_full = subprocess.run(
            [SVN_BIN, "log", "-r", "1:HEAD"],
            cwd=self.svn_wc_dir,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return [res.stdout, res_full.stdout]

    def test_e2e_stage_single_commit(self):
        """Test staging a commit leaves changes correctly in the SVN workspace without committing."""
        # Create a commit with a nested directory and file in Git
        test_file = self.git_dir / "src" / "main.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("print('hello e2e')\n", encoding="utf-8")
        commit_hash = self._git_commit("feat: initial python code")

        # Run git2svn stage
        exit_code = cli_main(["stage", commit_hash, "-g", str(self.git_dir), "-s", str(self.svn_wc_dir)])
        self.assertEqual(exit_code, 0)

        # Verify SVN working copy state
        svn_file = self.svn_wc_dir / "src" / "main.py"
        self.assertTrue(svn_file.is_file())
        self.assertEqual(svn_file.read_text(encoding="utf-8"), "print('hello e2e')\n")

        # Check real svn status reports added
        status_output = self._svn_status()
        self.assertIn("A", status_output)
        self.assertIn("src", status_output)

    def test_e2e_replay_multi_commits_with_utf8(self):
        """Test replaying a range of commits with multi-line UTF-8 commit messages and verify SVN log."""
        # Initial commit in SVN base
        base_file = self.git_dir / "README.md"
        base_file.write_text("# Project Header\n", encoding="utf-8")
        base_hash = self._git_commit("docs: base readme")

        # Copy to SVN and commit initial revision 1
        (self.svn_wc_dir / "README.md").write_text("# Project Header\n", encoding="utf-8")
        subprocess.run([SVN_BIN, "add", "README.md"], cwd=self.svn_wc_dir, check=True, capture_output=True)
        subprocess.run([SVN_BIN, "commit", "-m", "init svn base"], cwd=self.svn_wc_dir, check=True, capture_output=True)

        # Commit 1 in Git (adds umlauts and multi-line body)
        msg_1 = "feat: Hinzufügen von Ümlauten\n\nAusführliche Beschreibung mit Ä, Ö, Ü und ß."
        base_file.write_text("# Project Header\n\nDeutsche Umlaute: ÄÖÜäöüß\n", encoding="utf-8")
        self._git_commit(msg_1)

        # Commit 2 in Git (adds a module file and deletes a line)
        msg_2 = "feat: Add calculation module\n\nResolves #123"
        calc_file = self.git_dir / "calc.py"
        calc_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        head_hash = self._git_commit(msg_2)

        # Run replay range
        exit_code = cli_main(
            ["replay", f"{base_hash}..{head_hash}", "-g", str(self.git_dir), "-s", str(self.svn_wc_dir)]
        )
        self.assertEqual(exit_code, 0)

        # Verify SVN working copy is clean after successful replay
        status_output = self._svn_status()
        self.assertEqual(status_output.strip(), "")

        # Verify SVN log contains both Git commit messages and UTF-8 characters intact
        _, full_log = self._svn_log_messages()
        self.assertIn("Hinzufügen von Ümlauten", full_log)
        self.assertIn("Ä, Ö, Ü und ß", full_log)
        self.assertIn("Add calculation module", full_log)

        # Verify final files on disk
        svn_calc = self.svn_wc_dir / "calc.py"
        self.assertTrue(svn_calc.is_file())
        self.assertEqual(svn_calc.read_text(encoding="utf-8"), "def add(a, b):\n    return a + b\n")

    def test_e2e_snapshot_sync(self):
        """Test snapshot synchronization between divergent Git tree and SVN workspace."""
        # Create base file in SVN
        (self.svn_wc_dir / "obsolete.txt").write_text("old file\n", encoding="utf-8")
        (self.svn_wc_dir / "keep.txt").write_text("keep v1\n", encoding="utf-8")
        subprocess.run([SVN_BIN, "add", "obsolete.txt", "keep.txt"], cwd=self.svn_wc_dir, check=True)
        subprocess.run(
            [SVN_BIN, "commit", "-m", "initial svn setup"], cwd=self.svn_wc_dir, check=True, capture_output=True
        )

        # In Git, obsolete.txt does not exist, keep.txt is modified, brand_new.txt is added
        (self.git_dir / "keep.txt").write_text("keep v2\n", encoding="utf-8")
        (self.git_dir / "brand_new.txt").write_text("brand new\n", encoding="utf-8")
        git_ref = self._git_commit("feat: snapshot target")

        # Run snapshot stage
        exit_code = cli_main(["stage", git_ref, "--snapshot", "-g", str(self.git_dir), "-s", str(self.svn_wc_dir)])
        self.assertEqual(exit_code, 0)

        # Verify SVN workspace state
        self.assertFalse((self.svn_wc_dir / "obsolete.txt").exists())
        self.assertTrue((self.svn_wc_dir / "brand_new.txt").is_file())
        self.assertEqual((self.svn_wc_dir / "keep.txt").read_text(encoding="utf-8"), "keep v2\n")

        # Verify SVN status shows scheduled delete and add
        status = self._svn_status()
        self.assertIn("D       obsolete.txt", status)
        self.assertIn("A       brand_new.txt", status)
