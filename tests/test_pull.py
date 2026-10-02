from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git2svn.cli import main as cli_main
from git2svn.git import GitRepo
from git2svn.pull import commit_tree_from_directory, run_pull
from git2svn.svn import SvnWorkspace, find_svn_binary

SVN_BIN = find_svn_binary()
SVNADMIN_BIN = shutil.which("svnadmin")
GIT_BIN = shutil.which("git")
HAS_SVN = bool(GIT_BIN and shutil.which(SVN_BIN) and SVNADMIN_BIN)


class TestCommitTreeFromDirectory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()
        self.git_dir = self.path / "git_repo"
        self.git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=self.git_dir, check=True)
        self.git_repo = GitRepo(self.git_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_commit_tree_initial_import(self):
        source = self.path / "source"
        source.mkdir()
        (source / ".svn").mkdir()
        (source / ".svn" / "entries").write_text("svn internal")
        (source / "hello.txt").write_text("Hello World\n")
        (source / "nested").mkdir()
        (source / "nested" / "inner.txt").write_text("Inner content\n")

        commit_sha = commit_tree_from_directory(
            git_repo=self.git_repo,
            source_dir=source,
            parent_commit=None,
            commit_message="Initial import",
        )
        self.assertEqual(len(commit_sha), 40)

        # Verify tree content using git ls-tree
        res = self.git_repo.run_cmd(["ls-tree", "-r", "--name-only", commit_sha])
        files = res.stdout.strip().splitlines()
        self.assertIn("hello.txt", files)
        self.assertIn("nested/inner.txt", files)
        self.assertNotIn(".svn/entries", files)

    def test_commit_tree_unchanged_returns_parent(self):
        source = self.path / "source"
        source.mkdir()
        (source / "file.txt").write_text("version 1")

        first_sha = commit_tree_from_directory(
            git_repo=self.git_repo,
            source_dir=source,
            parent_commit=None,
            commit_message="First commit",
        )

        # Calling again with identical source should return the same commit SHA
        second_sha = commit_tree_from_directory(
            git_repo=self.git_repo,
            source_dir=source,
            parent_commit=first_sha,
            commit_message="Second commit with no changes",
        )
        self.assertEqual(first_sha, second_sha)


class TestPullUnit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()
        self.git_dir = self.path / "git_repo"
        self.git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.git_dir, check=True)
        subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=self.git_dir, check=True)
        (self.git_dir / "init.txt").write_text("init")
        subprocess.run(["git", "add", "."], cwd=self.git_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.git_dir, check=True)

        self.git_repo = GitRepo(self.git_dir)
        self.svn_dir = self.path / "svn_wc"
        self.svn_dir.mkdir()
        (self.svn_dir / ".svn").mkdir()
        self.svn_ws = SvnWorkspace(self.svn_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pull_rejects_non_standalone_mode(self):
        self.git_repo.set_config("git2svn.mode", "mirror")
        with patch("sys.stderr"):
            rc = run_pull(self.git_repo, self.svn_ws)
        self.assertEqual(rc, 1)

    def test_pull_already_up_to_date(self):
        self.git_repo.set_config("git2svn.mode", "standalone")
        self.git_repo.set_config("git2svn.lastSvnRev", "5")
        self.git_repo.create_branch("svn-base", "HEAD")

        with (
            patch.object(self.svn_ws, "update", return_value="At revision 5."),
            patch.object(self.svn_ws, "get_revision", return_value=5),
        ):
            rc = run_pull(self.git_repo, self.svn_ws)
            self.assertEqual(rc, 0)

    def test_pull_no_rebase(self):
        self.git_repo.set_config("git2svn.mode", "standalone")
        self.git_repo.set_config("git2svn.lastSvnRev", "1")
        self.git_repo.create_branch("svn-base", "HEAD")
        orig_head = self.git_repo.get_commit_hash("HEAD")

        # Create a new file in SVN workspace
        (self.svn_dir / "new_svn.txt").write_text("from svn")

        with (
            patch.object(self.svn_ws, "update", return_value="At revision 2."),
            patch.object(self.svn_ws, "get_revision", return_value=2),
            patch.object(
                self.svn_ws, "get_log_entries", return_value=[{"revision": 2, "author": "dev", "message": "r2: change"}]
            ),
        ):
            rc = run_pull(self.git_repo, self.svn_ws, rebase=False)
            self.assertEqual(rc, 0)

        # Base branch moved forward
        new_base = self.git_repo.get_commit_hash("svn-base")
        self.assertNotEqual(new_base, orig_head)
        # Active branch remained at orig_head because rebase=False
        current_head = self.git_repo.get_commit_hash("HEAD")
        self.assertEqual(current_head, orig_head)

    def test_setup_standalone_flag(self):
        # Setup with explicit --standalone on existing repo with commits
        rc = cli_main(["--git-dir", str(self.git_dir), "setup", "--standalone", str(self.svn_dir)])
        self.assertEqual(rc, 0)
        self.assertEqual(self.git_repo.get_config("git2svn.mode"), "standalone")
        self.assertEqual(self.git_repo.get_config("git2svn.baseBranch"), "svn-base")
        self.assertTrue(self.git_repo.ref_exists("refs/heads/svn-base"))


@unittest.skipUnless(HAS_SVN, "Standalone E2E requires git, svn, and svnadmin")
class TestStandaloneWorkflowE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        self.svn_repo_dir = self.root / "svn_remote"
        self.svn_remote_url = self.svn_repo_dir.as_uri()

        # 1. Create SVN repository
        subprocess.run([SVNADMIN_BIN, "create", str(self.svn_repo_dir)], check=True, capture_output=True)

        # 2. SVN seed working copy to commit initial files
        seed_wc = self.root / "seed_wc"
        seed_wc.mkdir()
        subprocess.run([SVN_BIN, "checkout", self.svn_remote_url, str(seed_wc)], check=True, capture_output=True)
        (seed_wc / "svn_initial.txt").write_text("SVN initial version\n")
        subprocess.run([SVN_BIN, "add", "svn_initial.txt"], cwd=seed_wc, check=True, capture_output=True)
        subprocess.run(
            [SVN_BIN, "commit", "-m", "r1: SVN initial repo commit"],
            cwd=seed_wc,
            check=True,
            capture_output=True,
        )
        self.seed_wc = seed_wc

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_standalone_lifecycle(self):
        git_work_dir = self.root / "git_project"

        # 1. Setup git2svn in brand-new folder with SVN URL
        rc = cli_main(["--git-dir", str(git_work_dir), "setup", self.svn_remote_url])
        self.assertEqual(rc, 0)

        git_repo = GitRepo(git_work_dir)
        self.assertTrue(git_repo.is_valid_repo())
        self.assertEqual(git_repo.get_config("git2svn.mode"), "standalone")
        self.assertEqual(git_repo.get_config("git2svn.baseBranch"), "svn-base")
        self.assertTrue(git_repo.ref_exists("refs/heads/svn-base"))
        self.assertTrue((git_work_dir / "svn_initial.txt").exists())

        # 2. Develop in Git: add a new file and commit
        (git_work_dir / "feature.py").write_text("print('hello standalone')\n")
        git_repo.run_cmd(["add", "feature.py"])
        git_repo.run_cmd(["commit", "-m", "feat: add feature.py"])

        # 3. Replay Git commits into SVN
        replay_rc = cli_main(["--git-dir", str(git_work_dir), "replay"])
        self.assertEqual(replay_rc, 0)

        # Verify svn-base was advanced to HEAD commit
        head_sha = git_repo.get_commit_hash("HEAD")
        base_sha = git_repo.get_commit_hash("svn-base")
        self.assertEqual(head_sha, base_sha)

        # Verify SVN repository has feature.py
        subprocess.run([SVN_BIN, "update"], cwd=self.seed_wc, check=True, capture_output=True)
        self.assertTrue((self.seed_wc / "feature.py").exists())

        # 4. Another user commits into SVN
        (self.seed_wc / "colleague.txt").write_text("Colleague remote SVN work\n")
        subprocess.run([SVN_BIN, "add", "colleague.txt"], cwd=self.seed_wc, check=True, capture_output=True)
        subprocess.run(
            [SVN_BIN, "commit", "-m", "r3: Colleague added colleague.txt"],
            cwd=self.seed_wc,
            check=True,
            capture_output=True,
        )

        # 5. Local Git user does local work before pulling
        (git_work_dir / "local_work.txt").write_text("My local unpushed work\n")
        git_repo.run_cmd(["add", "local_work.txt"])
        git_repo.run_cmd(["commit", "-m", "feat: local work"])

        # 6. Run git2svn pull
        pull_rc = cli_main(["--git-dir", str(git_work_dir), "pull"])
        self.assertEqual(pull_rc, 0)

        # Verify colleague.txt is present and local_work.txt is rebased on top
        self.assertTrue((git_work_dir / "colleague.txt").exists())
        self.assertTrue((git_work_dir / "local_work.txt").exists())

        # Check git log structure: HEAD is local work, parent is svn-base with colleague.txt
        log_res = git_repo.run_cmd(["log", "--oneline", "-n", "3"])
        log_lines = log_res.stdout.strip().splitlines()
        self.assertIn("feat: local work", log_lines[0])
        self.assertIn("r3: Colleague added colleague.txt", log_lines[1])

        # 7. Replay local work into SVN
        replay_rc2 = cli_main(["--git-dir", str(git_work_dir), "replay"])
        self.assertEqual(replay_rc2, 0)

        # Both working copy and SVN repo are fully in sync
        subprocess.run([SVN_BIN, "update"], cwd=self.seed_wc, check=True, capture_output=True)
        self.assertTrue((self.seed_wc / "local_work.txt").exists())
