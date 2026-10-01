from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from .git import GitRepo


def run_setup(git_repo: GitRepo, svn_dir_path: Optional[Path | str]) -> int:
    """Automate repository configuration, branch detection, and productivity aliases."""
    if not git_repo.is_valid_repo():
        print(f"Error: '{git_repo.repo_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    # 1. Resolve SVN working copy path
    resolved_svn: Optional[Path] = None
    if svn_dir_path:
        resolved_svn = Path(svn_dir_path).resolve()
    else:
        # Check existing config or environment variable
        cfg_svn = git_repo.get_config("git2svn.svnDir")
        if cfg_svn:
            resolved_svn = Path(cfg_svn).resolve()
        elif "SVN_DIR" in os.environ:
            resolved_svn = Path(os.environ["SVN_DIR"]).resolve()

    if not resolved_svn:
        # Interactive prompt if stdin is a tty, otherwise display error
        if sys.stdin.isatty():
            try:
                entered = input("Path to SVN working copy: ").strip()
                if entered:
                    resolved_svn = Path(entered).resolve()
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                return 1

    if not resolved_svn:
        print(
            "Error: SVN working copy path must be provided: 'git2svn setup <path/to/svn>'.",
            file=sys.stderr,
        )
        return 1

    if not (resolved_svn / ".svn").exists():
        print(
            f"Error: '{resolved_svn}' does not appear to be an SVN working copy (no .svn found).",
            file=sys.stderr,
        )
        return 1

    # 2. Auto-detect local trunk branch
    local_branches = git_repo.get_local_branches()
    detected_trunk: Optional[str] = None
    if "trunk" in local_branches:
        detected_trunk = "trunk"
    elif "main" in local_branches:
        detected_trunk = "main"
    elif "master" in local_branches:
        detected_trunk = "master"
    else:
        # Use current branch if available
        current = git_repo.get_current_branch()
        if current and current in local_branches:
            detected_trunk = current
        elif local_branches:
            detected_trunk = local_branches[0]

    # 3. Auto-detect remote tracking mirror branch
    remote_branches = git_repo.get_remote_branches()
    detected_mirror: Optional[str] = None
    preferred_remotes = ["svn-mirror/trunk", "origin/trunk", "svn/trunk", "mirror/trunk"]
    for pref in preferred_remotes:
        if pref in remote_branches:
            detected_mirror = pref
            break

    if not detected_mirror and detected_trunk:
        # Check for remote branch matching <remote>/<detected_trunk>
        candidates = [b for b in remote_branches if b.endswith(f"/{detected_trunk}")]
        if candidates:
            # Prefer svn-mirror if present
            svn_cand = [c for c in candidates if "svn" in c.lower() or "mirror" in c.lower()]
            detected_mirror = svn_cand[0] if svn_cand else candidates[0]

    # 4. Apply Git configuration
    # SVN directory path (use forward slashes for cross-platform consistency in git config)
    svn_dir_str = str(resolved_svn).replace("\\", "/")
    git_repo.set_config("git2svn.svnDir", svn_dir_str)
    git_repo.set_config("pull.ff", "only")

    default_range: Optional[str] = None
    if detected_mirror and detected_trunk:
        default_range = f"{detected_mirror}..{detected_trunk}"
        git_repo.set_config("git2svn.defaultRange", default_range)

    # 5. Configure productivity aliases
    mirror_remote = detected_mirror.split("/")[0] if detected_mirror else "svn-mirror"
    mirror_branch = detected_mirror or f"{mirror_remote}/trunk"
    trunk_name = detected_trunk or "trunk"

    push_script = (
        f"!f() {{ git2svn replay && git fetch {mirror_remote} && git checkout {trunk_name} && "
        f"if git diff --quiet {trunk_name} {mirror_branch}; then "
        f"git reset --hard {mirror_branch}; "
        f"else echo '[git svn-push] Warning: {trunk_name} differs from {mirror_branch}. Not resetting.' >&2; fi; }}; f"
    )
    pull_script = (
        f"!f() {{ git fetch {mirror_remote} && git checkout {trunk_name} && "
        f"if ! git merge --ff-only {mirror_branch} 2>/dev/null; then "
        f"echo '[git svn-pull] Fast-forward not possible (local commits on {trunk_name}). Rebasing onto {mirror_branch}...'; "
        f"git rebase {mirror_branch}; fi; }}; f"
    )
    status_script = "!git2svn status"

    git_repo.set_config("alias.svn-push", push_script)
    git_repo.set_config("alias.svn-pull", pull_script)
    git_repo.set_config("alias.svn-status", status_script)

    print("Successfully configured git2svn:")
    print(f"  git2svn.svnDir      = {svn_dir_str}")
    if default_range:
        print(f"  git2svn.defaultRange= {default_range}")
    else:
        print("  git2svn.defaultRange= (not set; could not detect mirror branch)")
    print("  pull.ff             = only")
    print(f"  alias.svn-push      = {push_script}")
    print(f"  alias.svn-pull      = {pull_script}")
    print(f"  alias.svn-status    = {status_script}")
    return 0
