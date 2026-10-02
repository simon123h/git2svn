import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git2svn.colors import TerminalColor
from git2svn.doctor import CheckResult, Doctor, run_doctor
from git2svn.git import GitRepo
from git2svn.state import save_replay_state


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

        self.git_dir = self.path / "git_repo"
        self.git_dir.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"], cwd=self.git_dir, check=True, capture_output=True
        )

        dummy_file = self.git_dir / "init.txt"
        dummy_file.write_text("initial commit\n", encoding="utf-8")
        subprocess.run(["git", "add", "init.txt"], cwd=self.git_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.git_dir, check=True, capture_output=True)

        self.git_repo = GitRepo(self.git_dir)
        self.color = TerminalColor("never")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_check_git_cli_success(self):
        doctor = Doctor(self.git_repo, color=self.color)
        res = doctor.check_git_cli()
        self.assertEqual(res.status, "OK")
        self.assertIn("git version", res.message)

    def test_check_git_cli_missing(self):
        doctor = Doctor(self.git_repo, color=self.color)
        with patch("shutil.which", return_value=None):
            res = doctor.check_git_cli()
            self.assertEqual(res.status, "FAIL")
            self.assertIn("not found", res.message)

    def test_check_git_repo_valid(self):
        doctor = Doctor(self.git_repo, color=self.color)
        res = doctor.check_git_repo()
        self.assertEqual(res.status, "OK")
        self.assertIn("main", res.message)

    def test_check_git_repo_invalid(self):
        invalid_repo = GitRepo(self.path / "nonexistent")
        doctor = Doctor(invalid_repo, color=self.color)
        res = doctor.check_git_repo()
        self.assertEqual(res.status, "FAIL")
        self.assertIn("not a valid Git repository", res.message)

    def test_check_git_working_tree_clean_and_dirty(self):
        doctor = Doctor(self.git_repo, color=self.color)
        res_clean = doctor.check_git_working_tree()
        self.assertEqual(res_clean.status, "OK")

        # Create untracked file to make dirty
        (self.git_dir / "dirty.txt").write_text("dirty content\n")
        res_dirty = doctor.check_git_working_tree()
        self.assertEqual(res_dirty.status, "WARN")
        self.assertIn("Dirty", res_dirty.message)

    def test_check_svn_cli_success(self):
        doctor = Doctor(self.git_repo, color=self.color)
        res = doctor.check_svn_cli()
        # In this test environment, svn is installed
        if shutil.which("svn"):
            self.assertEqual(res.status, "OK")
            self.assertIn("svn", res.message.lower())
        else:
            self.assertEqual(res.status, "FAIL")

    def test_check_svn_cli_missing(self):
        doctor = Doctor(self.git_repo, color=self.color)
        with patch("git2svn.doctor.find_svn_binary", return_value="fake_svn_bin"):
            with patch("subprocess.run", side_effect=FileNotFoundError("not found")):
                res = doctor.check_svn_cli()
                self.assertEqual(res.status, "FAIL")
                self.assertIn("not found", res.message)

    def test_check_svn_workspace_no_config(self):
        doctor = Doctor(self.git_repo, color=self.color)
        with patch.dict(os.environ, {}, clear=True):
            results = doctor.check_svn_workspace()
            self.assertTrue(any(r.status == "FAIL" for r in results))
            self.assertIn("No SVN working copy", results[0].message)

    def test_check_svn_workspace_missing_dir(self):
        missing_dir = self.path / "missing_svn"
        doctor = Doctor(self.git_repo, svn_dir=missing_dir, color=self.color)
        results = doctor.check_svn_workspace()
        presence = next(r for r in results if r.name == "Working Copy Presence")
        self.assertEqual(presence.status, "FAIL")

    def test_check_svn_workspace_managed_dir_pending_checkout(self):
        managed_dir = self.path / "pending_managed"
        doctor = Doctor(
            self.git_repo,
            svn_dir=managed_dir,
            svn_url="http://svn.example.com/repo/trunk",
            color=self.color,
        )
        results = doctor.check_svn_workspace()
        presence = next(r for r in results if r.name == "Working Copy Presence")
        self.assertEqual(presence.status, "WARN")
        self.assertIn("does not exist yet", presence.message)

    def test_check_svn_workspace_not_svn(self):
        not_svn = self.path / "regular_dir"
        not_svn.mkdir()
        doctor = Doctor(self.git_repo, svn_dir=not_svn, color=self.color)
        results = doctor.check_svn_workspace()
        validity = next(r for r in results if r.name == "Working Copy Validity")
        self.assertEqual(validity.status, "FAIL")
        self.assertIn("not an SVN working copy", validity.message)

    def test_check_svn_workspace_valid_mocked(self):
        svn_mock_dir = self.path / "mock_svn"
        svn_mock_dir.mkdir()
        (svn_mock_dir / ".svn").mkdir()

        doctor = Doctor(self.git_repo, svn_dir=svn_mock_dir, color=self.color)
        with patch("git2svn.doctor.SvnWorkspace.is_valid_workspace", return_value=True):
            with patch(
                "git2svn.doctor.SvnWorkspace.get_info",
                return_value={
                    "URL": "file:///tmp/repo/trunk",
                    "Revision": "42",
                    "Repository Root": "file:///tmp/repo",
                },
            ):
                with patch("git2svn.doctor.SvnWorkspace.is_clean", return_value=True):
                    with patch("git2svn.doctor.SvnWorkspace.get_current_branch_name", return_value="trunk"):
                        results = doctor.check_svn_workspace()
                        self.assertTrue(all(r.status == "OK" for r in results))

    def test_check_svn_workspace_branch_mismatch(self):
        svn_mock_dir = self.path / "mock_svn_mismatch"
        svn_mock_dir.mkdir()
        (svn_mock_dir / ".svn").mkdir()

        doctor = Doctor(self.git_repo, svn_dir=svn_mock_dir, color=self.color)
        with patch("git2svn.doctor.SvnWorkspace.is_valid_workspace", return_value=True):
            with patch("git2svn.doctor.SvnWorkspace.get_info", return_value={"URL": "^/branches/feature-1"}):
                with patch("git2svn.doctor.SvnWorkspace.is_clean", return_value=True):
                    with patch("git2svn.doctor.SvnWorkspace.get_current_branch_name", return_value="feature-1"):
                        # Git is on 'main' while SVN is on 'feature-1'
                        results = doctor.check_svn_workspace()
                        branch_check = next(r for r in results if r.name == "Branch Alignment")
                        self.assertEqual(branch_check.status, "WARN")
                        self.assertIn("differs from SVN branch", branch_check.message)

    def test_check_mirror_remote(self):
        doctor = Doctor(self.git_repo, color=self.color)

        # 1. Unset
        res_unset = doctor.check_mirror_remote()
        self.assertEqual(res_unset.status, "WARN")
        self.assertIn("not set", res_unset.message)

        # 2. Configured but remote not in 'git remote'
        self.git_repo.set_config("git2svn.mirrorRemote", "origin")
        res_missing = doctor.check_mirror_remote()
        self.assertEqual(res_missing.status, "WARN")
        self.assertIn("not found in 'git remote'", res_missing.message)

        # 3. Remote in 'git remote' with tracking branch
        subprocess.run(
            ["git", "remote", "add", "origin", "https://example.com/repo.git"],
            cwd=self.git_dir,
            check=True,
            capture_output=True,
        )
        # Create a mock remote ref refs/remotes/origin/main
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
            cwd=self.git_dir,
            check=True,
            capture_output=True,
        )
        res_ok = doctor.check_mirror_remote()
        self.assertEqual(res_ok.status, "OK")
        self.assertIn("tracking branch: origin/main", res_ok.message)

    def test_check_git_config_safety(self):
        doctor = Doctor(self.git_repo, color=self.color)

        # Initial state: pull.ff unset, aliases unset
        results = doctor.check_git_config_safety()
        pull_check = next(r for r in results if r.name == "Fast-Forward Policy")
        alias_check = next(r for r in results if r.name == "Git Aliases")
        self.assertEqual(pull_check.status, "WARN")
        self.assertEqual(alias_check.status, "WARN")

        # Set configs
        self.git_repo.set_config("pull.ff", "only")
        self.git_repo.set_config("alias.svn-push", "!git2svn replay")
        self.git_repo.set_config("alias.svn-pull", "!git fetch")
        self.git_repo.set_config("alias.svn-status", "!git2svn status")

        results2 = doctor.check_git_config_safety()
        pull_check2 = next(r for r in results2 if r.name == "Fast-Forward Policy")
        alias_check2 = next(r for r in results2 if r.name == "Git Aliases")
        self.assertEqual(pull_check2.status, "OK")
        self.assertEqual(alias_check2.status, "OK")

    def test_check_pre_push_hook(self):
        doctor = Doctor(self.git_repo, color=self.color)
        self.git_repo.set_config("git2svn.mirrorRemote", "origin")

        # 1. Missing
        res_missing = doctor.check_pre_push_hook()
        self.assertEqual(res_missing.status, "WARN")
        self.assertIn("Not installed", res_missing.message)

        # 2. Exists but without guard
        hook_path = self.git_dir / ".git" / "hooks" / "pre-push"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook_path.chmod(0o755)

        res_no_guard = doctor.check_pre_push_hook()
        self.assertEqual(res_no_guard.status, "WARN")
        self.assertIn("guard block is missing", res_no_guard.message)

        # 3. Installed with guard
        guard_content = (
            "#!/bin/sh\n# --- START GIT2SVN PRE-PUSH GUARD ---\nREMOTE_NAME=$1\n# --- END GIT2SVN PRE-PUSH GUARD ---\n"
        )
        hook_path.write_text(guard_content, encoding="utf-8")
        hook_path.chmod(0o755)

        res_ok = doctor.check_pre_push_hook()
        self.assertEqual(res_ok.status, "OK")
        self.assertIn("Installed and active", res_ok.message)

    def test_check_replay_session(self):
        doctor = Doctor(self.git_repo, color=self.color)
        res_none = doctor.check_replay_session()
        self.assertEqual(res_none.status, "OK")

        # Save an active replay state
        save_replay_state(
            self.git_dir,
            {
                "git_dir": str(self.git_dir),
                "svn_dir": str(self.git_dir),
                "remaining_commits": ["abcdef1234567890"],
                "completed_commits": 1,
                "current_commit": "abcdef1234567890",
                "current_commit_msg": "Commit message",
                "total_commits": 2,
            },
        )

        res_paused = doctor.check_replay_session()
        self.assertEqual(res_paused.status, "WARN")
        self.assertIn("Replay paused", res_paused.message)

    def test_report_success_and_failure_exit_codes(self):
        # 1. Failure exit code
        invalid_repo = GitRepo(self.path / "nonexistent")
        fail_doctor = Doctor(invalid_repo, color=self.color)
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            code = fail_doctor.report()
        self.assertEqual(code, 1)
        self.assertIn("Doctor found", stdout.getvalue())
        self.assertIn("failure(s)", stdout.getvalue())

        # 2. Success exit code (when mocked to all OK)
        ok_doctor = Doctor(self.git_repo, color=self.color)
        with patch.object(
            ok_doctor,
            "run_diagnostics",
            return_value=[
                CheckResult("Git", "Git CLI", "OK", "git 2.43.0"),
                CheckResult("SVN", "SVN CLI", "OK", "svn 1.14"),
            ],
        ):
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = ok_doctor.report()
            self.assertEqual(code, 0)
            self.assertIn("All checks passed!", stdout.getvalue())

    def test_run_doctor_function(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            code = run_doctor(self.git_repo, color=self.color)
        # In a fresh git repo without svn configured, it returns 1 (no SVN workspace configured)
        self.assertEqual(code, 1)
        self.assertIn("git2svn Doctor", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
