from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .colors import TerminalColor
from .git import GitRepo

logger = logging.getLogger("git2svn")


def is_git_svn_available() -> bool:
    """Check if 'git svn' subcommand is available in PATH."""
    git_bin = shutil.which("git")
    if not git_bin:
        return False
    try:
        res = subprocess.run(
            [git_bin, "svn", "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_git_svn_version() -> Optional[str]:
    """Return the git-svn version string or None if unavailable."""
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    try:
        res = subprocess.run(
            [git_bin, "svn", "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def run_init_mirror(
    svn_url: str,
    target_dir: Optional[Path | str] = None,
    git_dir: Optional[Path | str] = None,
    trunk: Optional[str] = "trunk",
    branches: Optional[str] = "branches",
    tags: Optional[str] = "tags",
    stdlayout: bool = False,
    prefix: str = "svn-mirror/",
    from_revision: Optional[str] = None,
    no_fetch: bool = False,
    dry_run: bool = False,
    color_mode: str = "auto",
) -> int:
    """
    Bootstrap a local git-svn mirror remote and configure git2svn in one command.
    1. Verifies git-svn is installed.
    2. Initializes Git repository if not already existing.
    3. Runs 'git svn init' with standard or custom layout.
    4. Fetches remote SVN revisions ('git svn fetch').
    5. Configures git2svn with managed working copy, mirrorRemote, and git-svn pull/push aliases.
    """
    color = TerminalColor(color_mode)

    if not is_git_svn_available():
        print(color.bold_red("Error: 'git svn' is not installed or not in PATH.\n"), file=sys.stderr)
        print(
            "The 'init-mirror' command relies on the official Git-SVN bridge to bootstrap\n"
            "a local mirror tracking branch without requiring external mirror servers.\n\n"
            "To install git-svn on your system:\n"
            "  - Ubuntu / Debian:    sudo apt-get install git-svn\n"
            "  - Fedora / RHEL / Alma: sudo dnf install git-svn\n"
            "  - macOS (Homebrew):   brew install git-svn\n"
            "  - Arch Linux:         sudo pacman -S git-svn\n"
            "  - Windows (Git Bash): Included with standard Git for Windows installation\n",
            file=sys.stderr,
        )
        return 1

    clean_url = svn_url.strip()
    if not clean_url:
        print("Error: An SVN repository URL must be provided: 'git2svn init-mirror <svn-url>'", file=sys.stderr)
        return 1

    # Resolve target Git directory
    dest_path: Path
    if target_dir:
        dest_path = Path(target_dir).resolve()
    elif git_dir:
        dest_path = Path(git_dir).resolve()
    else:
        dest_path = Path.cwd().resolve()

    if not dest_path.exists():
        if dry_run:
            print(f"[DRY-RUN] Would create directory: {dest_path}")
        else:
            dest_path.mkdir(parents=True, exist_ok=True)

    git_repo = GitRepo(dest_path)
    if not git_repo.is_valid_repo():
        print(f"Initializing empty Git repository at: {dest_path}")
        if dry_run:
            print(f"[DRY-RUN] Would run: git init -b main at {dest_path}")
        else:
            init_res = subprocess.run(["git", "init", "-b", "main"], cwd=dest_path, capture_output=True, check=False)
            if init_res.returncode != 0:
                subprocess.run(["git", "init"], cwd=dest_path, capture_output=True, check=True)

    # 1. Construct git svn init arguments
    init_cmd = ["svn", "init"]
    if stdlayout:
        init_cmd.append("-s")
    else:
        if trunk:
            init_cmd.extend(["-T", trunk])
        if branches:
            init_cmd.extend(["-b", branches])
        if tags:
            init_cmd.extend(["-t", tags])

    clean_prefix = prefix if prefix.endswith("/") else f"{prefix}/"
    init_cmd.append(f"--prefix={clean_prefix}")
    init_cmd.append(clean_url)

    print(color.bold_cyan(f"Initializing local SVN mirror for: {clean_url}"))
    if dry_run:
        print(f"[DRY-RUN] (in {dest_path}) git {' '.join(init_cmd)}")
    else:
        res = git_repo.run_cmd(init_cmd, check=False)
        if res.returncode != 0:
            print(f"Error initializing git-svn:\n{res.stderr.strip()}", file=sys.stderr)
            return res.returncode

    # 2. Fetch revisions from SVN
    remote_prefix_name = clean_prefix.rstrip("/")
    detected_trunk_name = trunk or "trunk"

    if no_fetch:
        print("[INFO] Skipping 'git svn fetch' (--no-fetch requested). Run 'git svn fetch' manually.")
    else:
        fetch_cmd = ["svn", "fetch"]
        if from_revision:
            fetch_cmd.extend(["-r", str(from_revision)])
        rev_info = f" (revision {from_revision})" if from_revision else ""
        print(color.bold_cyan(f"Fetching SVN revisions into '{remote_prefix_name}/{detected_trunk_name}'{rev_info}..."))
        if dry_run:
            print(f"[DRY-RUN] (in {dest_path}) git {' '.join(fetch_cmd)}")
        else:
            # Run fetch interactively/streaming stdout so user sees revision progress
            logger.info("Executing git %s in %s...", " ".join(fetch_cmd), dest_path)
            fetch_res = subprocess.run(["git"] + fetch_cmd, cwd=dest_path, check=False)
            if fetch_res.returncode != 0:
                print(color.bold_yellow("\nWarning: 'git svn fetch' exited with non-zero status."), file=sys.stderr)

    # 3. Create or checkout local branch from mirror tracking branch
    mirror_tracking_ref = f"{remote_prefix_name}/{detected_trunk_name}"
    full_mirror_ref = f"refs/remotes/{mirror_tracking_ref}"
    if not dry_run and git_repo.ref_exists(full_mirror_ref):
        if not git_repo.ref_exists("HEAD"):
            # Repository has no commits yet, checkout local trunk branch pointing to mirror
            print(f"Creating local branch '{detected_trunk_name}' from '{mirror_tracking_ref}'...")
            git_repo.run_cmd(["checkout", "-b", detected_trunk_name, mirror_tracking_ref], check=False)
        else:
            current_branch = git_repo.get_current_branch()
            if current_branch == "main" and not git_repo.ref_exists(f"refs/heads/{detected_trunk_name}"):
                git_repo.run_cmd(["branch", detected_trunk_name, mirror_tracking_ref], check=False)

    # 4. Configure git2svn via run_setup
    from .setup import run_setup

    setup_url = clean_url
    if trunk and not clean_url.endswith(f"/{trunk}") and not stdlayout:
        setup_url = f"{clean_url.rstrip('/')}/{trunk.strip('/')}"
    elif stdlayout and not clean_url.endswith("/trunk"):
        setup_url = f"{clean_url.rstrip('/')}/trunk"

    if dry_run:
        print(f"[DRY-RUN] Would configure git2svn setup with SVN target: {setup_url}")
        return 0

    print("\nConfiguring git2svn environment...")
    rc = run_setup(git_repo, svn_target=setup_url)
    if rc != 0:
        return rc

    # 5. Customize productivity aliases for git-svn backend
    trunk_branch = detected_trunk_name if git_repo.ref_exists(f"refs/heads/{detected_trunk_name}") else "main"
    pull_script = (
        f"!f() {{ git svn fetch && git checkout {trunk_branch} && "
        f"if ! git merge --ff-only {remote_prefix_name}/{detected_trunk_name} 2>/dev/null; then "
        f"echo '[git svn-pull] Fast-forward not possible (local commits on {trunk_branch}). Rebasing onto {remote_prefix_name}/{detected_trunk_name}...'; "
        f"git rebase {remote_prefix_name}/{detected_trunk_name}; fi; }}; f"
    )
    push_script = (
        f"!f() {{ git2svn replay && git svn fetch && git checkout {trunk_branch} && "
        f"if git diff --quiet {trunk_branch} {remote_prefix_name}/{detected_trunk_name}; then "
        f"git reset --hard {remote_prefix_name}/{detected_trunk_name}; else "
        f"echo '[git svn-push] Warning: {trunk_branch} differs from {remote_prefix_name}/{detected_trunk_name}. Not resetting.' >&2; fi; }}; f"
    )

    git_repo.set_config("git2svn.mirrorRemote", remote_prefix_name)
    git_repo.set_config("git2svn.mirrorBackend", "git-svn")
    git_repo.set_config("alias.svn-pull", pull_script)
    git_repo.set_config("alias.svn-push", push_script)

    print(color.bold_green("\nSuccessfully initialized local git-svn mirror and configured git2svn!"))
    print(f"  Mirror remote tracking ref : {mirror_tracking_ref}")
    print(f"  Local staging branch       : {trunk_branch}")
    print("  Pull alias (git svn-pull)  : git svn fetch && merge/rebase")
    print("  Push alias (git svn-push)  : git2svn replay && git svn fetch && align")
    print("\nNext steps:")
    print(f"  1. Start feature branches from '{trunk_branch}':")
    print("     git checkout -b feature/my-work")
    print("  2. Replay completed work to Subversion:")
    print("     git svn-push   (or: git2svn replay)")
    print("  3. Fetch latest upstream Subversion commits:")
    print("     git svn-pull\n")
    return 0
