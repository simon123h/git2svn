from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .colors import TerminalColor
from .git import GitRepo

logger = logging.getLogger("git2svn")


def derive_repo_name(url: str) -> str:
    """Extract repository directory name from Git URL (e.g. 'https://.../foo.git' -> 'foo')."""
    clean = url.strip().rstrip("/")
    last_seg = clean.split("/")[-1].split(":")[-1]
    if last_seg.endswith(".git"):
        last_seg = last_seg[:-4]
    return last_seg or "git-repo"


def run_clone(
    mirror_url: str,
    svn_url: str,
    target_dir: Optional[Path | str] = None,
    origin_url: Optional[str] = None,
    mirror_remote: str = "svn-mirror",
    dry_run: bool = False,
    color_mode: str = "auto",
) -> int:
    """
    Automate turnkey cloning of an SVN-to-Git mirror repository and wire up git2svn.
    1. Clones mirror_url with --origin <mirror_remote> into target_dir.
    2. Runs git2svn setup <svn_url> to initialize the managed SVN working copy and aliases.
    3. Adds origin_url as 'origin' if specified.
    """
    color = TerminalColor(color_mode)

    clean_mirror_url = mirror_url.strip()
    if not clean_mirror_url:
        print(color.bold_red("Error: A mirror Git URL must be provided: 'git2svn clone <mirror-url>'"), file=sys.stderr)
        return 1

    clean_svn_url = svn_url.strip()
    if not clean_svn_url:
        print(
            color.bold_red(
                "Error: An SVN URL must be provided via --svn-url: 'git2svn clone <mirror-url> --svn-url <svn-url>'"
            ),
            file=sys.stderr,
        )
        return 1

    dest_name = str(target_dir).strip() if target_dir else derive_repo_name(clean_mirror_url)
    dest_path = Path(dest_name).resolve()

    if dest_path.exists():
        if any(dest_path.iterdir()):
            print(
                color.bold_red(f"Error: Destination path '{dest_path}' already exists and is not empty."),
                file=sys.stderr,
            )
            return 1

    print(color.bold_cyan(f"Cloning mirror repository '{clean_mirror_url}' into '{dest_path.name}'..."))
    clone_cmd = ["git", "clone", clean_mirror_url, str(dest_path), "--origin", mirror_remote]

    if dry_run:
        print(f"[DRY-RUN] Would run: {' '.join(clone_cmd)}")
        print(f"[DRY-RUN] (in {dest_path}) git2svn setup {clean_svn_url}")
        if origin_url:
            print(f"[DRY-RUN] (in {dest_path}) git remote add origin {origin_url}")
        return 0

    clone_res = subprocess.run(clone_cmd, check=False)
    if clone_res.returncode != 0:
        print(color.bold_red(f"Error: 'git clone' failed with return code {clone_res.returncode}"), file=sys.stderr)
        return clone_res.returncode

    git_repo = GitRepo(dest_path)
    if not git_repo.is_valid_repo():
        print(color.bold_red(f"Error: Cloned directory '{dest_path}' is not a valid Git repository."), file=sys.stderr)
        return 1

    # Configure git2svn setup
    from .setup import run_setup

    print(color.bold_cyan(f"\nConfiguring git2svn setup with SVN target '{clean_svn_url}'..."))
    setup_rc = run_setup(git_repo, svn_target=clean_svn_url)
    if setup_rc != 0:
        print(color.bold_red(f"Error: git2svn setup failed with return code {setup_rc}"), file=sys.stderr)
        return setup_rc

    # Ensure git2svn.mirrorRemote points to mirror_remote
    git_repo.set_config("git2svn.mirrorRemote", mirror_remote)

    # Optionally configure secondary development remote 'origin'
    if origin_url:
        clean_origin_url = origin_url.strip()
        print(color.bold_cyan(f"Adding secondary team Git remote 'origin' -> {clean_origin_url}"))
        remotes = git_repo.get_remotes()
        if "origin" in remotes:
            git_repo.run_cmd(["remote", "set-url", "origin", clean_origin_url], check=False)
        else:
            git_repo.run_cmd(["remote", "add", "origin", clean_origin_url], check=False)

    print(color.bold_green(f"\nSuccessfully cloned and configured '{dest_path.name}'!"))
    print(f"  Mirror remote tracking : {mirror_remote} ({clean_mirror_url})")
    if origin_url:
        print(f"  Team Git remote        : origin ({origin_url})")
    print(f"  SVN target URL         : {clean_svn_url}")
    print(f"  Managed SVN workspace  : {dest_path / '.git' / 'git2svn' / 'svn_wc'}")
    print("\nNext steps:")
    print(f"  cd {dest_path.name}")
    print("  git svn-status")
    print("  git svn-pull\n")
    return 0
