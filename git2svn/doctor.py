from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .colors import TerminalColor
from .git import GitRepo
from .setup import install_pre_push_hook
from .state import load_replay_session
from .svn import (
    SvnWorkspace,
    checkout_working_copy,
    find_svn_binary,
    get_default_managed_svn_dir,
)


@dataclass
class CheckResult:
    """Represents the outcome of a single diagnostic check."""

    category: str
    name: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    details: List[str] = field(default_factory=list)
    hint: Optional[str] = None


@dataclass
class FixResult:
    """Represents an automatic remediation action performed by doctor --fix."""

    name: str
    message: str
    success: bool = True
    error: Optional[str] = None


class Doctor:
    """Performs comprehensive pre-flight diagnostics on the environment and repository configuration."""

    def __init__(
        self,
        git_repo: GitRepo,
        svn_dir: Optional[Path | str] = None,
        svn_url: Optional[str] = None,
        color: Optional[TerminalColor] = None,
    ):
        self.git = git_repo
        self.raw_svn_dir = Path(svn_dir) if svn_dir else None
        self.raw_svn_url = str(svn_url).strip() if svn_url else None
        self.color = color or TerminalColor("auto")

    def check_git_cli(self) -> CheckResult:
        """Verify Git CLI installation and version."""
        git_bin = shutil.which("git")
        if not git_bin:
            return CheckResult(
                category="Git Environment",
                name="Git CLI",
                status="FAIL",
                message="Executable 'git' not found in PATH",
                hint="Install Git from https://git-scm.com or your system package manager.",
            )

        try:
            res = subprocess.run(
                [git_bin, "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            version_str = res.stdout.strip() if res.returncode == 0 else "unknown"
            return CheckResult(
                category="Git Environment",
                name="Git CLI",
                status="OK",
                message=f"{version_str} ({git_bin})",
            )
        except Exception as e:
            return CheckResult(
                category="Git Environment",
                name="Git CLI",
                status="FAIL",
                message=f"Failed to execute 'git': {e}",
            )

    def check_git_repo(self) -> CheckResult:
        """Verify that current or specified directory is a valid Git repository."""
        if not self.git.is_valid_repo():
            return CheckResult(
                category="Git Environment",
                name="Git Repository",
                status="FAIL",
                message=f"'{self.git.repo_dir}' is not a valid Git repository",
                hint="Run 'git init' or run git2svn from inside a Git repository clone.",
            )

        branch = self.git.get_current_branch()
        head_commit = self.git.get_head_commit()
        head_subject = self.git.get_head_subject()
        desc = f"branch '{branch}' at {head_commit[:8]}"
        if head_subject:
            desc += f' ("{head_subject}")'

        return CheckResult(
            category="Git Environment",
            name="Git Repository",
            status="OK",
            message=f"{self.git.repo_dir} ({desc})",
        )

    def check_git_working_tree(self) -> CheckResult:
        """Verify Git working tree cleanliness."""
        if not self.git.is_valid_repo():
            return CheckResult(
                category="Git Environment",
                name="Git Working Tree",
                status="WARN",
                message="Skipped (not a Git repository)",
            )

        if self.git.is_clean():
            return CheckResult(
                category="Git Environment",
                name="Git Working Tree",
                status="OK",
                message="Clean (no uncommitted changes or untracked files)",
            )
        return CheckResult(
            category="Git Environment",
            name="Git Working Tree",
            status="WARN",
            message="Dirty (uncommitted changes or untracked files present)",
            hint="Commit, stash, or discard local changes ('git status') before syncing.",
        )

    def check_svn_cli(self) -> CheckResult:
        """Verify Subversion CLI installation and version."""
        svn_bin = find_svn_binary()
        bin_path = shutil.which(svn_bin) or svn_bin

        try:
            res = subprocess.run(
                [svn_bin, "--version", "--quiet"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode == 0:
                ver = res.stdout.strip()
                return CheckResult(
                    category="Subversion Environment",
                    name="Subversion CLI",
                    status="OK",
                    message=f"svn {ver} ({bin_path})",
                )
            # Try without --quiet
            res = subprocess.run(
                [svn_bin, "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode == 0:
                first_line = res.stdout.splitlines()[0] if res.stdout.splitlines() else "installed"
                return CheckResult(
                    category="Subversion Environment",
                    name="Subversion CLI",
                    status="OK",
                    message=f"{first_line} ({bin_path})",
                )
            return CheckResult(
                category="Subversion Environment",
                name="Subversion CLI",
                status="FAIL",
                message=f"Executable '{svn_bin}' returned error code {res.returncode}",
                hint="Verify Subversion installation and PATH configuration.",
            )
        except FileNotFoundError:
            hint_msg = (
                "Install Subversion and ensure 'svn' is on your PATH. "
                "On Windows, install TortoiseSVN (enable 'command line tools') or SlikSVN."
            )
            return CheckResult(
                category="Subversion Environment",
                name="Subversion CLI",
                status="FAIL",
                message=f"Executable '{svn_bin}' not found in PATH",
                hint=hint_msg,
            )
        except Exception as e:
            return CheckResult(
                category="Subversion Environment",
                name="Subversion CLI",
                status="FAIL",
                message=f"Failed to execute '{svn_bin}': {e}",
            )

    def _resolve_svn_paths(self) -> tuple[Optional[Path], Optional[str]]:
        """Resolve effective SVN workspace directory and repository URL."""
        svn_dir = self.raw_svn_dir
        if not svn_dir and "SVN_DIR" in os.environ:
            svn_dir = Path(os.environ["SVN_DIR"])
        if not svn_dir and self.git.is_valid_repo():
            cfg_dir = self.git.get_config("git2svn.svnDir")
            if cfg_dir:
                svn_dir = Path(cfg_dir)

        svn_url = self.raw_svn_url
        if not svn_url and "SVN_URL" in os.environ:
            svn_url = os.environ["SVN_URL"].strip()
        if not svn_url and self.git.is_valid_repo():
            cfg_url = self.git.get_config("git2svn.svnUrl")
            if cfg_url:
                svn_url = cfg_url

        if not svn_dir and svn_url and self.git.is_valid_repo():
            svn_dir = get_default_managed_svn_dir(self.git.repo_dir)

        return svn_dir, svn_url

    def check_svn_workspace(self) -> List[CheckResult]:
        """Verify SVN workspace configuration, working copy validity, remote connectivity, and cleanliness."""
        results: List[CheckResult] = []
        svn_dir, svn_url = self._resolve_svn_paths()

        if not svn_dir and not svn_url:
            results.append(
                CheckResult(
                    category="SVN Working Copy",
                    name="Configuration",
                    status="FAIL",
                    message="No SVN working copy or repository URL configured",
                    hint="Run 'git2svn setup <SVN_URL_or_PATH>' to configure your workspace.",
                )
            )
            return results

        display_path = str(svn_dir) if svn_dir else "None"
        results.append(
            CheckResult(
                category="SVN Working Copy",
                name="Configuration",
                status="OK",
                message=f"Path: {display_path}" + (f", URL: {svn_url}" if svn_url else ""),
            )
        )

        if svn_dir and not svn_dir.exists():
            if svn_url:
                results.append(
                    CheckResult(
                        category="SVN Working Copy",
                        name="Working Copy Presence",
                        status="WARN",
                        message=f"Managed working copy does not exist yet at '{svn_dir}'",
                        hint="Run 'git2svn setup' or any sync command to automatically check it out.",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        category="SVN Working Copy",
                        name="Working Copy Presence",
                        status="FAIL",
                        message=f"Directory '{svn_dir}' does not exist",
                        hint="Create or check out the SVN working copy, or run 'git2svn setup <SVN_URL>'.",
                    )
                )
            return results

        if not svn_dir:
            return results

        ws = SvnWorkspace(svn_dir)
        if not ws.is_valid_workspace():
            results.append(
                CheckResult(
                    category="SVN Working Copy",
                    name="Working Copy Validity",
                    status="FAIL",
                    message=f"'{svn_dir}' exists but is not an SVN working copy (no .svn metadata)",
                    hint="Ensure the path points to an SVN checkout, or run 'git2svn setup <SVN_URL>'.",
                )
            )
            return results

        # Valid working copy: inspect info
        try:
            info = ws.get_info()
            url = info.get("URL") or info.get("Relative URL") or "unknown"
            rev = info.get("Revision")
            root = info.get("Repository Root")
            info_details = [f"URL: {url}"]
            if rev:
                info_details.append(f"Base Revision: r{rev}")
            if root:
                info_details.append(f"Repository Root: {root}")

            results.append(
                CheckResult(
                    category="SVN Working Copy",
                    name="Working Copy Metadata",
                    status="OK",
                    message=f"Valid working copy ({url} at r{rev or '?'})",
                    details=info_details,
                )
            )
        except Exception as e:
            results.append(
                CheckResult(
                    category="SVN Working Copy",
                    name="Working Copy Metadata",
                    status="FAIL",
                    message=f"Failed to read SVN info: {e}",
                    hint="Run 'svn cleanup' or re-checkout the working copy.",
                )
            )

        # Check working copy cleanliness and locks
        try:
            if ws.is_clean():
                results.append(
                    CheckResult(
                        category="SVN Working Copy",
                        name="Working Copy State",
                        status="OK",
                        message="Clean and unlocked (ready for sync)",
                    )
                )
            else:
                uncommitted = ws.get_status_summary()
                results.append(
                    CheckResult(
                        category="SVN Working Copy",
                        name="Working Copy State",
                        status="WARN",
                        message=f"Dirty ({len(uncommitted)} uncommitted or untracked changes)",
                        hint="Run 'git2svn clean' to revert uncommitted changes and remove artifacts.",
                    )
                )
        except Exception as e:
            results.append(
                CheckResult(
                    category="SVN Working Copy",
                    name="Working Copy State",
                    status="WARN",
                    message=f"Could not determine cleanliness: {e}",
                    hint="Run 'git2svn clean' to release working copy locks.",
                )
            )

        # Branch alignment check
        if self.git.is_valid_repo():
            try:
                svn_branch = ws.get_current_branch_name()
                git_branch = self.git.get_current_branch()
                trunk_synonyms = {"trunk", "main", "master"}
                aligned = (svn_branch == git_branch) or (svn_branch in trunk_synonyms and git_branch in trunk_synonyms)
                if aligned:
                    results.append(
                        CheckResult(
                            category="SVN Working Copy",
                            name="Branch Alignment",
                            status="OK",
                            message=f"Git branch '{git_branch}' aligns with SVN '{svn_branch}'",
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            category="SVN Working Copy",
                            name="Branch Alignment",
                            status="WARN",
                            message=f"Git branch '{git_branch}' differs from SVN branch '{svn_branch}'",
                            hint=f"Run 'git2svn switch {git_branch}' to switch the SVN working copy to matching branch.",
                        )
                    )
            except Exception:
                pass

        return results

    def check_mirror_remote(self) -> CheckResult:
        """Verify mirror remote configuration and tracking branch existence."""
        if not self.git.is_valid_repo():
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Mirror Remote",
                status="WARN",
                message="Skipped (not a Git repository)",
            )

        mode = self.git.get_config("git2svn.mode")
        base_branch = self.git.get_config("git2svn.baseBranch") or "svn-base"
        if mode == "standalone" or (not self.git.get_remotes() and self.git.ref_exists(f"refs/heads/{base_branch}")):
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Synchronization Mode",
                status="OK",
                message=f"Standalone Mode (base branch: '{base_branch}')",
            )

        mirror = self.git.get_config("git2svn.mirrorRemote")
        if not mirror:
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Mirror Remote",
                status="WARN",
                message="Config 'git2svn.mirrorRemote' is not set",
                hint="Run 'git2svn setup' to configure automatic tracking branch detection.",
            )

        remotes = self.git.get_remotes()
        if mirror not in remotes:
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Mirror Remote",
                status="WARN",
                message=f"Configured mirror remote '{mirror}' is not found in 'git remote'",
                hint=f"Add the remote via 'git remote add {mirror} <url>' or run 'git2svn setup'.",
            )

        current_branch = self.git.get_current_branch()
        tracking_candidates = [f"{mirror}/{current_branch}", f"{mirror}/trunk", f"{mirror}/main", f"{mirror}/master"]
        remote_branches = self.git.get_remote_branches()
        found_branch = next((b for b in tracking_candidates if b in remote_branches), None)

        if found_branch:
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Mirror Remote",
                status="OK",
                message=f"Configured ({mirror}), tracking branch: {found_branch}",
            )
        return CheckResult(
            category="Repository Configuration & Safety",
            name="Mirror Remote",
            status="WARN",
            message=f"Remote '{mirror}' exists, but no tracking branch found for '{current_branch}'",
            hint=f"Fetch latest commits from the mirror: 'git fetch {mirror}'.",
        )

    def check_git_config_safety(self) -> List[CheckResult]:
        """Verify pull.ff policy and productivity aliases."""
        results: List[CheckResult] = []
        if not self.git.is_valid_repo():
            return results

        pull_ff = self.git.get_config("pull.ff")
        if pull_ff == "only":
            results.append(
                CheckResult(
                    category="Repository Configuration & Safety",
                    name="Fast-Forward Policy",
                    status="OK",
                    message="pull.ff = only (prevents accidental merge commits)",
                )
            )
        else:
            results.append(
                CheckResult(
                    category="Repository Configuration & Safety",
                    name="Fast-Forward Policy",
                    status="WARN",
                    message=f"pull.ff is '{pull_ff or 'unset'}' (expected 'only')",
                    hint="Run 'git config pull.ff only' to guarantee linear updates.",
                )
            )

        aliases = ["alias.svn-push", "alias.svn-pull", "alias.svn-status"]
        configured_aliases = [a.split(".", 1)[1] for a in aliases if self.git.get_config(a)]
        if len(configured_aliases) == len(aliases):
            results.append(
                CheckResult(
                    category="Repository Configuration & Safety",
                    name="Git Aliases",
                    status="OK",
                    message=f"Configured ({', '.join(configured_aliases)})",
                )
            )
        else:
            missing = [a.split(".", 1)[1] for a in aliases if not self.git.get_config(a)]
            results.append(
                CheckResult(
                    category="Repository Configuration & Safety",
                    name="Git Aliases",
                    status="WARN",
                    message=f"Missing recommended aliases: {', '.join(missing)}",
                    hint="Run 'git2svn setup' to automatically configure git aliases.",
                )
            )

        return results

    def check_pre_push_hook(self) -> CheckResult:
        """Verify pre-push hook guard installation and executable status."""
        if not self.git.is_valid_repo():
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Pre-push Hook Guard",
                status="WARN",
                message="Skipped (not a Git repository)",
            )

        mode = self.git.get_config("git2svn.mode")
        remotes = self.git.get_remotes()
        if mode == "standalone" or (not remotes and not self.git.get_config("git2svn.mirrorRemote")):
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Pre-push Hook Guard",
                status="OK",
                message="Not required (standalone mode has no mirror remote)",
            )

        hook_file = self.git.repo_dir / ".git" / "hooks" / "pre-push"
        if not hook_file.exists():
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Pre-push Hook Guard",
                status="WARN",
                message="Not installed (.git/hooks/pre-push missing)",
                hint="Run 'git2svn setup' to install the guard preventing direct pushes to the SVN mirror remote.",
            )

        content = hook_file.read_text(encoding="utf-8", errors="replace")
        has_guard = "# --- START GIT2SVN PRE-PUSH GUARD ---" in content
        if not has_guard:
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Pre-push Hook Guard",
                status="WARN",
                message="Hook file exists but git2svn guard block is missing",
                hint="Run 'git2svn setup' to add the git2svn guard block.",
            )

        # Check executable permissions on POSIX
        if sys.platform != "win32" and not os.access(hook_file, os.X_OK):
            return CheckResult(
                category="Repository Configuration & Safety",
                name="Pre-push Hook Guard",
                status="WARN",
                message="Hook file is present but lacks execute permission",
                hint=f"Make the hook executable: 'chmod +x {hook_file}'.",
            )

        mirror = self.git.get_config("git2svn.mirrorRemote") or "mirror"
        return CheckResult(
            category="Repository Configuration & Safety",
            name="Pre-push Hook Guard",
            status="OK",
            message=f"Installed and active (protects remote '{mirror}')",
        )

    def check_replay_session(self) -> CheckResult:
        """Verify that no interrupted replay session is currently blocking the workspace."""
        svn_dir, _ = self._resolve_svn_paths()
        target_dir = (
            svn_dir if svn_dir and svn_dir.is_dir() else (self.git.repo_dir if self.git.is_valid_repo() else None)
        )
        if not target_dir:
            return CheckResult(
                category="Replay Queue",
                name="Replay State",
                status="OK",
                message="No replay session active",
            )

        session = load_replay_session(target_dir)
        if session:
            current_commit = session.current_commit or "unknown"
            remaining = len(session.remaining_commits)
            return CheckResult(
                category="Replay Queue",
                name="Replay State",
                status="WARN",
                message=f"Replay paused at commit {current_commit[:8]} ({remaining} commits remaining)",
                hint="Resolve any patch conflicts and run 'git2svn replay --continue' (or '--abort' / '--skip').",
            )

        return CheckResult(
            category="Replay Queue",
            name="Replay State",
            status="OK",
            message="No interrupted or paused replay session",
        )

    def apply_fixes(self) -> List[FixResult]:
        """
        Automatically remediate fixable repository and workspace configurations:
        - Auto-configure git2svn.mirrorRemote if remotes exist in git
        - Set pull.ff = only
        - Configure recommended git aliases (git svn-push, git svn-pull, git svn-status)
        - Install / fix executable permissions on pre-push hook guard (.git/hooks/pre-push)
        - Checkout managed SVN working copy if svnUrl is configured but working copy does not exist
        - Run svn cleanup if working copy is locked
        """
        fixes: List[FixResult] = []

        if self.git.is_valid_repo():
            # 1. Mirror Remote auto-detection
            mirror = self.git.get_config("git2svn.mirrorRemote")
            remotes = self.git.get_remotes()
            if not mirror and remotes:
                if "svn-mirror" in remotes:
                    chosen_mirror = "svn-mirror"
                elif "origin" in remotes:
                    chosen_mirror = "origin"
                else:
                    chosen_mirror = remotes[0]
                self.git.set_config("git2svn.mirrorRemote", chosen_mirror)
                fixes.append(
                    FixResult("Mirror Remote", f"Auto-detected and configured git2svn.mirrorRemote = {chosen_mirror}")
                )

            # 2. Fast-Forward policy
            pull_ff = self.git.get_config("pull.ff")
            if pull_ff != "only":
                self.git.set_config("pull.ff", "only")
                fixes.append(FixResult("Fast-Forward Policy", "Configured pull.ff = only"))

            # 3. Recommended Git Aliases
            aliases = ["alias.svn-push", "alias.svn-pull", "alias.svn-status"]
            missing_aliases = [a for a in aliases if not self.git.get_config(a)]
            if missing_aliases:
                local_branches = self.git.get_local_branches()
                detected_trunk = (
                    "trunk"
                    if "trunk" in local_branches
                    else (
                        "main"
                        if "main" in local_branches
                        else ("master" if "master" in local_branches else (self.git.get_current_branch() or "trunk"))
                    )
                )
                effective_mirror = self.git.get_config("git2svn.mirrorRemote") or "origin"
                remote_branches = self.git.get_remote_branches()
                mirror_branch = f"{effective_mirror}/{detected_trunk}"
                for cand in [
                    f"{effective_mirror}/{detected_trunk}",
                    f"{effective_mirror}/trunk",
                    f"{effective_mirror}/main",
                    f"{effective_mirror}/master",
                ]:
                    if cand in remote_branches:
                        mirror_branch = cand
                        break

                push_script = (
                    f"!f() {{ git2svn replay && git fetch {effective_mirror} && git checkout {detected_trunk} && "
                    f"if git diff --quiet {detected_trunk} {mirror_branch}; then "
                    f"git reset --hard {mirror_branch}; "
                    f"else echo '[git svn-push] Warning: {detected_trunk} differs from {mirror_branch}. Not resetting.' >&2; fi; }}; f"
                )
                pull_script = (
                    f"!f() {{ git fetch {effective_mirror} && git checkout {detected_trunk} && "
                    f"if ! git merge --ff-only {mirror_branch} 2>/dev/null; then "
                    f"echo '[git svn-pull] Fast-forward not possible (local commits on {detected_trunk}). Rebasing onto {mirror_branch}...'; "
                    f"git rebase {mirror_branch}; fi; }}; f"
                )
                status_script = "!git2svn status"

                if "alias.svn-push" in missing_aliases:
                    self.git.set_config("alias.svn-push", push_script)
                if "alias.svn-pull" in missing_aliases:
                    self.git.set_config("alias.svn-pull", pull_script)
                if "alias.svn-status" in missing_aliases:
                    self.git.set_config("alias.svn-status", status_script)

                fixes.append(FixResult("Git Aliases", "Configured git svn-push, svn-pull, svn-status"))

            # 4. Pre-push Hook Guard
            hook_file = self.git.repo_dir / ".git" / "hooks" / "pre-push"
            hook_needs_fix = False
            if not hook_file.exists():
                hook_needs_fix = True
            else:
                content = hook_file.read_text(encoding="utf-8", errors="replace")
                if "# --- START GIT2SVN PRE-PUSH GUARD ---" not in content:
                    hook_needs_fix = True
                elif sys.platform != "win32" and not os.access(hook_file, os.X_OK):
                    hook_needs_fix = True

            if hook_needs_fix:
                effective_mirror = self.git.get_config("git2svn.mirrorRemote") or "origin"
                install_pre_push_hook(self.git, effective_mirror)
                fixes.append(
                    FixResult(
                        "Pre-push Hook Guard",
                        f"Installed and made executable (.git/hooks/pre-push, protects '{effective_mirror}')",
                    )
                )

        # 5. SVN Working Copy auto-checkout (if svnUrl configured but working copy missing)
        svn_dir, svn_url = self._resolve_svn_paths()
        if svn_dir and not svn_dir.exists() and svn_url:
            try:
                checkout_working_copy(svn_url, svn_dir)
                fixes.append(FixResult("Working Copy Presence", f"Checked out managed SVN working copy to {svn_dir}"))
            except Exception as e:
                fixes.append(
                    FixResult(
                        "Working Copy Presence",
                        f"Failed to checkout SVN repository: {e}",
                        success=False,
                        error=str(e),
                    )
                )

        # 6. SVN Working Copy lock cleanup
        if svn_dir and svn_dir.exists():
            ws = SvnWorkspace(svn_dir)
            if ws.is_valid_workspace():
                try:
                    res = ws.run_cmd(["status"], check=False)
                    if "locked" in res.stderr.lower() or "cleanup" in res.stderr.lower():
                        ws.cleanup()
                        fixes.append(FixResult("SVN Cleanup", f"Executed 'svn cleanup' in {svn_dir} to release locks"))
                except Exception:
                    pass

        return fixes

    def run_diagnostics(self) -> List[CheckResult]:
        """Execute all diagnostic checks and return structured results."""
        results: List[CheckResult] = []
        results.append(self.check_git_cli())
        results.append(self.check_git_repo())
        results.append(self.check_git_working_tree())
        results.append(self.check_svn_cli())
        results.extend(self.check_svn_workspace())
        results.append(self.check_mirror_remote())
        results.extend(self.check_git_config_safety())
        results.append(self.check_pre_push_hook())
        results.append(self.check_replay_session())
        return results

    def report(self, fix: bool = False) -> int:
        """Execute checks, print colored diagnostic output, and return exit code (0 = success, 1 = failure)."""
        c = self.color

        if fix:
            print(f"\n{c.bold('git2svn Doctor')} - Pre-flight Diagnostic & Auto-Remediation\n")
            fixes = self.apply_fixes()
            print(f"{c.bold_cyan('[Applying Automatic Fixes]')}")
            if fixes:
                for f in fixes:
                    if f.success:
                        print(f"  {c.fixed_badge()} {f.name}: {f.message}")
                    else:
                        print(f"  {c.fail_badge()} {f.name}: {f.message}")
            else:
                print(f"  {c.ok_badge()} No automatic fixes required.")
            print()
        else:
            print(f"\n{c.bold('git2svn Doctor')} - Pre-flight Diagnostic Check\n")

        results = self.run_diagnostics()

        categories: Dict[str, List[CheckResult]] = {}
        for r in results:
            categories.setdefault(r.category, []).append(r)

        for cat_name, cat_results in categories.items():
            print(f"{c.bold_cyan(f'[{cat_name}]')}")
            for r in cat_results:
                if r.status == "OK":
                    badge = c.ok_badge("[OK]    ")
                elif r.status == "WARN":
                    badge = c.warn_badge("[WARN]  ")
                elif r.status == "FAIL":
                    badge = c.fail_badge("[FAIL]  ")
                else:
                    badge = c.info_badge(f"[{r.status}]  ")

                print(f"  {badge} {r.name}: {r.message}")
                for d in r.details:
                    print(f"           {c.dim(d)}")
                if r.hint:
                    print(f"           {c.yellow('Hint:')} {r.hint}")
            print()

        num_fail = sum(1 for r in results if r.status == "FAIL")
        num_warn = sum(1 for r in results if r.status == "WARN")

        if num_fail > 0:
            print(
                f"{c.bold_red('Doctor found')} {num_fail} failure(s) and {num_warn} warning(s). "
                "Please resolve the critical issues above before syncing.\n"
            )
            return 1

        if num_warn > 0:
            print(
                f"{c.bold_yellow('Doctor found')} {num_warn} recommendation(s). "
                "git2svn is functional, but following the hints above is recommended.\n"
            )
            return 0

        print(f"{c.bold_green('All checks passed!')} git2svn is properly configured and ready to sync.\n")
        return 0


def run_doctor(
    git_repo: GitRepo,
    svn_dir: Optional[Path | str] = None,
    svn_url: Optional[str] = None,
    color: Optional[TerminalColor] = None,
    fix: bool = False,
) -> int:
    """Entry point for git2svn doctor command."""
    doctor = Doctor(git_repo, svn_dir=svn_dir, svn_url=svn_url, color=color)
    return doctor.report(fix=fix)
