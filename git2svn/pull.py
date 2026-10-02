from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from .colors import TerminalColor
from .git import GitRepo
from .svn import SvnError, SvnWorkspace

logger = logging.getLogger("git2svn")


def run_pull(
    git_repo: GitRepo,
    svn_workspace: SvnWorkspace,
    rebase: bool = True,
    dry_run: bool = False,
    color_mode: str = "auto",
) -> int:
    """
    Ingest remote SVN revisions into the local Git repository without requiring an external svn2git mirror.
    1. Updates the managed SVN working copy (svn update).
    2. Checks for newly arrived SVN revisions since git2svn.lastSvnRev (or base branch commit).
    3. Commits the updated SVN tree onto the standalone base branch (e.g. 'svn-base') in an isolated index.
    4. Records the new lastSvnRev in git config.
    5. Optionally rebases the currently active branch onto the base branch.
    """
    color = TerminalColor(color_mode)

    if not git_repo.is_valid_repo():
        print(f"Error: '{git_repo.repo_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    base_branch = git_repo.get_config("git2svn.baseBranch") or "svn-base"
    if not git_repo.ref_exists(f"refs/heads/{base_branch}"):
        print(
            f"Error: Standalone base branch '{base_branch}' does not exist in Git.\n"
            f"Run 'git2svn setup <SVN_URL>' to initialize standalone mode.",
            file=sys.stderr,
        )
        return 1

    # 1. Update SVN working copy
    old_rev = svn_workspace.get_revision()
    logger.info("Executing svn update in %s...", svn_workspace.workspace_dir)
    if not dry_run:
        try:
            svn_workspace.update()
        except SvnError as e:
            print(f"Error updating SVN working copy:\n  {e.args[0]}", file=sys.stderr)
            return 1
    new_rev = svn_workspace.get_revision() if not dry_run else old_rev

    last_saved_rev_str = git_repo.get_config("git2svn.lastSvnRev")
    last_saved_rev = int(last_saved_rev_str) if last_saved_rev_str and last_saved_rev_str.isdigit() else old_rev

    if old_rev is not None and new_rev is not None and new_rev == last_saved_rev and old_rev == new_rev:
        print(f"Already up to date. SVN working copy is at revision r{new_rev}.")
        return 0

    rev_range_str = f"r{new_rev}"
    log_summary_lines: List[str] = []
    if last_saved_rev is not None and new_rev is not None and new_rev > last_saved_rev:
        rev_range_str = f"r{last_saved_rev + 1}:r{new_rev}"
        entries = svn_workspace.get_log_entries(revision_range=f"{last_saved_rev + 1}:{new_rev}")
        for entry in entries:
            r = entry.get("revision", "")
            author = entry.get("author", "unknown")
            msg = entry.get("message", "").splitlines()[0] if entry.get("message") else "No message"
            log_summary_lines.append(f"* r{r} by {author}: {msg}")

    print(color.bold_cyan(f"Synchronizing SVN changes ({rev_range_str}) into Git branch '{base_branch}'..."))

    if dry_run:
        print(f"[DRY-RUN] Would update SVN working copy to HEAD (r{new_rev or 'unknown'})")
        print(f"[DRY-RUN] Would commit updated SVN tree to '{base_branch}'")
        if rebase:
            print(f"[DRY-RUN] Would rebase current branch onto '{base_branch}'")
        return 0

    # 2. Stage updated SVN tree to base_branch using an isolated index (leaving user working directory intact)
    base_head = git_repo.get_commit_hash(base_branch)
    if entries and len(entries) == 1:
        first_line = (
            entries[0].get("message", "").strip().splitlines()[0] if entries[0].get("message") else "sync remote change"
        )
        r_num = entries[0].get("revision") or new_rev
        commit_msg_header = f"svn(r{r_num}): {first_line}"
    else:
        commit_msg_header = f"svn: sync {rev_range_str} from remote"
    commit_body = "\n".join(log_summary_lines)
    full_commit_msg = f"{commit_msg_header}\n\n{commit_body}\n" if commit_body else f"{commit_msg_header}\n"

    try:
        new_commit_hash = commit_tree_from_directory(
            git_repo=git_repo,
            source_dir=svn_workspace.workspace_dir,
            parent_commit=base_head,
            commit_message=full_commit_msg,
        )
    except Exception as e:
        print(f"Error creating Git commit from SVN tree: {e}", file=sys.stderr)
        return 1

    if new_commit_hash == base_head:
        print(f"SVN working copy tree is identical to '{base_branch}'. No new commit needed.")
    else:
        git_repo.update_ref(f"refs/heads/{base_branch}", new_commit_hash, msg=f"git2svn pull: {rev_range_str}")
        print(f"Successfully updated '{base_branch}' to {new_commit_hash[:8]} ({rev_range_str}).")

    if new_rev is not None:
        git_repo.set_config("git2svn.lastSvnRev", str(new_rev))

    # 3. Optional rebase onto base_branch
    current_branch = git_repo.get_current_branch()
    if rebase and current_branch not in (base_branch, "HEAD (detached)", "unknown"):
        print(f"\nRebasing current branch '{current_branch}' onto '{base_branch}'...")
        res = git_repo.run_cmd(["rebase", base_branch], check=False)
        if res.returncode != 0:
            print(color.bold_yellow("\n[Rebase Conflict]"), file=sys.stderr)
            print(
                "Automatic rebase stopped due to conflicts.\n"
                "Resolve the conflicts using standard Git commands, then run:\n"
                "    git rebase --continue\n"
                "(or 'git rebase --abort' to cancel the rebase).\n",
                file=sys.stderr,
            )
            return res.returncode
        print(color.bold_green(f"Branch '{current_branch}' successfully rebased onto '{base_branch}'.\n"))

    return 0


def commit_tree_from_directory(
    git_repo: GitRepo,
    source_dir: Path,
    parent_commit: Optional[str],
    commit_message: str,
) -> str:
    """
    Creates a Git tree and commit representing the files in source_dir (excluding .svn),
    using an isolated temporary Git index. Does NOT touch the active working directory or active index.
    Returns the created 40-character commit hash (or parent_commit if tree is unchanged).
    """
    with tempfile.NamedTemporaryFile(prefix="git2svn_index_") as tf:
        temp_index = Path(tf.name)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(temp_index)
    env["GIT_WORK_TREE"] = str(source_dir)
    env["GIT_DIR"] = str(git_repo.get_git_dir())

    # Ensure author and committer identity are set
    if "GIT_AUTHOR_NAME" not in env:
        name_res = git_repo.run_cmd(["config", "user.name"], check=False)
        user_name = name_res.stdout.strip() if name_res.returncode == 0 and name_res.stdout.strip() else "git2svn"
        env["GIT_AUTHOR_NAME"] = user_name
        env["GIT_COMMITTER_NAME"] = user_name
    if "GIT_AUTHOR_EMAIL" not in env:
        email_res = git_repo.run_cmd(["config", "user.email"], check=False)
        user_email = (
            email_res.stdout.strip() if email_res.returncode == 0 and email_res.stdout.strip() else "git2svn@local"
        )
        env["GIT_AUTHOR_EMAIL"] = user_email
        env["GIT_COMMITTER_EMAIL"] = user_email

    try:
        # If parent_commit exists, read its tree into our temporary index
        if parent_commit:
            subprocess.run(
                ["git", "read-tree", parent_commit],
                cwd=source_dir,
                env=env,
                check=True,
                capture_output=True,
            )

        # Stage all files from source_dir into the isolated index
        # We must ignore .svn
        subprocess.run(
            ["git", "add", "-A", "--", ".", ":!.svn", ":!.svn/**"],
            cwd=source_dir,
            env=env,
            check=True,
            capture_output=True,
        )

        # Sync executable bits for files in source_dir (especially relevant on Windows/POSIX)
        for root, dirs, files in os.walk(source_dir):
            if ".svn" in dirs:
                dirs.remove(".svn")
            for f in files:
                full_f = Path(root) / f
                try:
                    rel_f = full_f.relative_to(source_dir).as_posix()
                    # Check if file has svn:executable property or execute permissions
                    is_exec = os.access(full_f, os.X_OK) and not sys.platform == "win32"
                    if is_exec:
                        subprocess.run(
                            ["git", "update-index", "--chmod=+x", "--", rel_f],
                            cwd=source_dir,
                            env=env,
                            check=False,
                            capture_output=True,
                        )
                except Exception:
                    pass

        # Write index to tree object
        tree_res = subprocess.run(
            ["git", "write-tree"],
            cwd=source_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        tree_hash = tree_res.stdout.strip()

        # If parent_commit has the identical tree, return parent_commit directly
        if parent_commit:
            parent_tree = subprocess.run(
                ["git", "rev-parse", f"{parent_commit}^{{tree}}"],
                cwd=git_repo.repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if parent_tree == tree_hash:
                return parent_commit

        # Create commit object pointing to parent
        cmd = ["git", "commit-tree", tree_hash, "-m", commit_message]
        if parent_commit:
            cmd.extend(["-p", parent_commit])

        commit_res = subprocess.run(
            cmd,
            cwd=git_repo.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return commit_res.stdout.strip()
    finally:
        if temp_index.exists():
            temp_index.unlink(missing_ok=True)
