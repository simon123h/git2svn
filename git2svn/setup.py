from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

from .git import GitRepo
from .svn import (
    SvnError,
    checkout_working_copy,
    get_default_managed_svn_dir,
    is_svn_url_or_repo,
)


def run_setup(git_repo: GitRepo, svn_target: Optional[Path | str]) -> int:
    """Automate repository configuration, branch detection, and productivity aliases."""
    if not git_repo.is_valid_repo():
        print(f"Error: '{git_repo.repo_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    # 1. Resolve SVN target (URL, repo path, or existing working copy)
    raw_target: Optional[str] = str(svn_target).strip() if svn_target else None
    if not raw_target:
        # Check existing config or environment variable
        cfg_url = git_repo.get_config("git2svn.svnUrl")
        cfg_svn = git_repo.get_config("git2svn.svnDir")
        if cfg_url:
            raw_target = cfg_url
        elif cfg_svn:
            raw_target = cfg_svn
        elif "SVN_URL" in os.environ:
            raw_target = os.environ["SVN_URL"].strip()
        elif "SVN_DIR" in os.environ:
            raw_target = os.environ["SVN_DIR"].strip()

    if not raw_target:
        # Interactive prompt if stdin is a tty, otherwise display error
        if sys.stdin.isatty():
            try:
                entered = input("Path to SVN working copy or SVN repository URL: ").strip()
                if entered:
                    raw_target = entered
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                return 1

    if not raw_target:
        print(
            "Error: SVN working copy path or SVN repository URL must be provided: 'git2svn setup <url-or-path>'.",
            file=sys.stderr,
        )
        return 1

    resolved_svn: Optional[Path] = None
    configured_url: Optional[str] = None

    if is_svn_url_or_repo(raw_target):
        configured_url = raw_target
        managed_dir = get_default_managed_svn_dir(git_repo.repo_dir)
        print(f"Detected SVN repository URL: {configured_url}")
        print(f"Setting up managed working copy at: {managed_dir}")
        try:
            checkout_working_copy(configured_url, managed_dir)
            resolved_svn = managed_dir
        except SvnError as e:
            print(f"Error checking out SVN repository:\n  {e.args[0]}", file=sys.stderr)
            return 1
    else:
        resolved_svn = Path(raw_target).resolve()
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
    # Preferred mirror candidates, checking svn-mirror first, then origin
    target_names = [detected_trunk] if detected_trunk else ["trunk", "main", "master"]
    preferred_remotes: List[str] = []
    for t_name in target_names:
        preferred_remotes.extend(
            [
                f"svn-mirror/{t_name}",
                f"origin/{t_name}",
                f"svn/{t_name}",
                f"mirror/{t_name}",
            ]
        )

    for pref in preferred_remotes:
        if pref in remote_branches:
            detected_mirror = pref
            break

    if not detected_mirror and detected_trunk:
        # Check for remote branch matching <remote>/<detected_trunk>
        candidates = [b for b in remote_branches if b.endswith(f"/{detected_trunk}")]
        if candidates:
            # Prefer svn/mirror, fallback to origin, then first candidate
            svn_cand = [c for c in candidates if any(k in c.lower() for k in ("svn", "mirror", "origin"))]
            detected_mirror = svn_cand[0] if svn_cand else candidates[0]

    # 4. Apply Git configuration
    # SVN directory path (use forward slashes for cross-platform consistency in git config)
    svn_dir_str = str(resolved_svn).replace("\\", "/")
    git_repo.set_config("git2svn.svnDir", svn_dir_str)
    if configured_url:
        git_repo.set_config("git2svn.svnUrl", configured_url)
    git_repo.set_config("pull.ff", "only")

    default_range: Optional[str] = None
    if detected_mirror and detected_trunk:
        default_range = f"{detected_mirror}..{detected_trunk}"
        git_repo.set_config("git2svn.defaultRange", default_range)

    # 5. Configure productivity aliases
    mirror_remote = (
        detected_mirror.split("/")[0]
        if detected_mirror
        else (
            "origin"
            if "origin/HEAD"
            in git_repo.run_cmd(
                ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], check=False
            ).stdout
            or any(b.startswith("origin/") for b in remote_branches)
            else "svn-mirror"
        )
    )
    mirror_branch = detected_mirror or f"{mirror_remote}/{detected_trunk or 'trunk'}"
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

    # 6. Install pre-push hook to guard against accidental direct git push to mirror branch
    hook_installed = install_pre_push_hook(git_repo, mirror_remote, trunk_name)

    print("Successfully configured git2svn:")
    if configured_url:
        print(f"  git2svn.svnUrl      = {configured_url}")
    print(f"  git2svn.svnDir      = {svn_dir_str}")
    if default_range:
        print(f"  git2svn.defaultRange= {default_range}")
    else:
        print("  git2svn.defaultRange= (not set; could not detect remote mirror branch)")
    print("  pull.ff             = only")
    print(f"  alias.svn-push      = {push_script}")
    print(f"  alias.svn-pull      = {pull_script}")
    print(f"  alias.svn-status    = {status_script}")
    if hook_installed:
        print(f"  pre-push hook       = installed (protects '{mirror_remote}/{trunk_name}' from direct push)")

    if not detected_mirror:
        print(
            "\nTip: No remote mirror branch (e.g. 'origin/trunk' or 'svn-mirror/trunk') was detected.\n"
            "     If you maintain an incremental SVN-to-Git mirror (e.g. svn2git), add it as a git remote:\n"
            "       git remote add origin <mirror-git-url>  # or: git remote add svn-mirror <mirror-git-url>\n"
            "       git fetch origin\n"
            "       git2svn setup\n"
            "     This enables automated 'git svn-pull' and 'git svn-push' fast-forward resets."
        )
    return 0


def install_pre_push_hook(git_repo: GitRepo, mirror_remote: str, trunk_branch: str) -> bool:
    """
    Install a pre-push hook into .git/hooks/pre-push that prevents accidental direct 'git push'
    to the SVN mirror tracking branch (e.g. origin/trunk or svn-mirror/trunk), while allowing
    pushes of other branches (e.g. feature branches or pull requests).
    """
    hooks_dir = git_repo.repo_dir / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_file = hooks_dir / "pre-push"

    hook_block = f"""# --- START GIT2SVN PRE-PUSH GUARD ---
# Block direct pushes to SVN mirror branch '{mirror_remote}/{trunk_branch}'
REMOTE_NAME="$1"
REMOTE_URL="$2"

if [ "$REMOTE_NAME" = "{mirror_remote}" ]; then
    while read -r local_ref local_sha remote_ref remote_sha; do
        if [ "$remote_ref" = "refs/heads/{trunk_branch}" ]; then
            echo "" >&2
            echo "[git2svn pre-push guard] ERROR: Direct push to '{mirror_remote}/{trunk_branch}' is blocked!" >&2
            echo "[git2svn pre-push guard] This branch is mirrored from SVN. Pushing directly causes svn2git to diverge." >&2
            echo "[git2svn pre-push guard] To publish your changes to SVN, run:" >&2
            echo "    git svn-push   (or: git2svn replay)" >&2
            echo "" >&2
            exit 1
        fi
    done
fi
# --- END GIT2SVN PRE-PUSH GUARD ---
"""

    if hook_file.exists():
        content = hook_file.read_text(encoding="utf-8", errors="replace")
        if "# --- START GIT2SVN PRE-PUSH GUARD ---" in content:
            # Update existing guard block
            import re

            pattern = r"# --- START GIT2SVN PRE-PUSH GUARD ---.*?# --- END GIT2SVN PRE-PUSH GUARD ---\n?"
            new_content = re.sub(pattern, hook_block, content, flags=re.DOTALL)
            hook_file.write_text(new_content, encoding="utf-8")
        else:
            # Append guard block to existing hook
            new_content = content.rstrip() + "\n\n" + hook_block
            hook_file.write_text(new_content, encoding="utf-8")
    else:
        new_content = "#!/bin/sh\n\n" + hook_block
        hook_file.write_text(new_content, encoding="utf-8")

    # Ensure executable permissions
    try:
        current_mode = hook_file.stat().st_mode
        hook_file.chmod(current_mode | 0o755)
    except Exception:
        pass

    return True
