#!/usr/bin/env python3
"""
git2svn: A CLI utility to synchronize changes from a local Git repository
to a local Subversion (SVN) working copy.

Commands:
    cherry-pick <commit_hash>       Port a single Git commit to SVN using patch -p1.
    squash <start_ref> <end_ref>    Port a commit range to SVN using patch -p1.
    sync <base_ref> <target_ref>    Port changes by brute-force copying files using shutil.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("git2svn")


def find_conflict_artifacts(workspace_dir: Path) -> List[Path]:
    """Find all .rej and .orig files in workspace_dir (ignoring .svn)."""
    artifacts: List[Path] = []
    for root, dirs, files in os.walk(workspace_dir):
        if ".svn" in dirs:
            dirs.remove(".svn")
        for f in files:
            if f.endswith(".rej") or f.endswith(".orig"):
                artifacts.append(Path(root) / f)
    return artifacts


def clean_conflict_artifacts(workspace_dir: Path) -> List[Path]:
    """Remove all .rej and .orig files in workspace_dir."""
    artifacts = find_conflict_artifacts(workspace_dir)
    for a in artifacts:
        try:
            a.unlink(missing_ok=True)
            logger.debug("Removed conflict artifact: %s", a)
        except OSError:
            pass
    return artifacts


def get_replay_state_path(workspace_dir: Path) -> Path:
    """Get the path to the replay state file."""
    svn_meta = workspace_dir / ".svn"
    if svn_meta.is_dir():
        return svn_meta / "git2svn-replay.json"
    return workspace_dir / ".git2svn-replay.json"


def save_replay_state(workspace_dir: Path, data: dict) -> None:
    """Save replay state to disk."""
    path = get_replay_state_path(workspace_dir)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_replay_state(workspace_dir: Path) -> Optional[dict]:
    """Load replay state from disk if it exists."""
    path = get_replay_state_path(workspace_dir)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to parse replay state file: %s", e)
    return None


def clear_replay_state(workspace_dir: Path) -> None:
    """Remove the replay state file if it exists."""
    path = get_replay_state_path(workspace_dir)
    if path.is_file():
        path.unlink(missing_ok=True)


class FileChange:
    """Represents a file change between two Git revisions."""

    def __init__(self, action: str, path: str, old_path: Optional[str] = None):
        # action is typically 'A', 'D', 'M', 'R', or 'C'
        self.action = action.upper()
        self.path = Path(path)
        self.old_path = Path(old_path) if old_path else None

    @property
    def is_added(self) -> bool:
        return self.action.startswith("A")

    @property
    def is_deleted(self) -> bool:
        return self.action.startswith("D")

    @property
    def is_modified(self) -> bool:
        return self.action.startswith("M")

    @property
    def is_renamed(self) -> bool:
        return self.action.startswith("R")

    @property
    def is_copied(self) -> bool:
        return self.action.startswith("C")

    def __repr__(self) -> str:
        if self.old_path:
            return f"<FileChange {self.action}: {self.old_path} -> {self.path}>"
        return f"<FileChange {self.action}: {self.path}>"


def parse_name_status(status_output: str) -> List[FileChange]:
    """
    Parse the standard tabular output of `git diff --name-status`.
    Handles tab-separated lines such as:
        A       new_file.txt
        M       modified_file.txt
        D       deleted_file.txt
        R100    old_name.txt    new_name.txt
        C100    src_name.txt    dst_name.txt
    """
    changes: List[FileChange] = []
    for line in status_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if not parts:
            continue
        status_code = parts[0].strip()
        if not status_code:
            continue

        action_char = status_code[0].upper()
        if action_char in ("R", "C"):
            if len(parts) >= 3:
                old_p = parts[1].strip().strip('"')
                new_p = parts[2].strip().strip('"')
                changes.append(FileChange(action=action_char, path=new_p, old_path=old_p))
            else:
                logger.warning("Unrecognized rename/copy format: %s", line)
        else:
            if len(parts) >= 2:
                p = parts[1].strip().strip('"')
                changes.append(FileChange(action=action_char, path=p))
            else:
                logger.warning("Unrecognized status format: %s", line)
    return changes


def parse_name_status_z(null_output: str) -> List[FileChange]:
    """
    Parse NUL-delimited output from `git diff -z --name-status`.
    This handles special characters and paths with spaces robustly.
    """
    changes: List[FileChange] = []
    tokens = null_output.split("\0")
    idx = 0
    while idx < len(tokens):
        token = tokens[idx].strip()
        if not token:
            idx += 1
            continue

        action_char = token[0].upper()
        if action_char in ("R", "C"):
            if idx + 2 < len(tokens):
                old_p = tokens[idx + 1]
                new_p = tokens[idx + 2]
                changes.append(FileChange(action=action_char, path=new_p, old_path=old_p))
                idx += 3
            else:
                break
        else:
            if idx + 1 < len(tokens):
                p = tokens[idx + 1]
                changes.append(FileChange(action=action_char, path=p))
                idx += 2
            else:
                break
    return changes


class GitRepo:
    """Wrapper around git commands for the source repository."""

    def __init__(self, repo_dir: Path, git_bin: str = "git"):
        self.repo_dir = repo_dir.resolve()
        self.git_bin = git_bin

    def run_cmd(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.git_bin] + args
        logger.debug("Executing Git command in %s: %s", self.repo_dir, " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.repo_dir,
            check=check,
            capture_output=True,
            text=True,
        )

    def is_valid_repo(self) -> bool:
        try:
            res = self.run_cmd(["rev-parse", "--is-inside-work-tree"], check=False)
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def get_commit_parent(self, commit_hash: str) -> Optional[str]:
        """Return the parent commit hash, or None if root commit."""
        res = self.run_cmd(["rev-parse", "--verify", f"{commit_hash}^"], check=False)
        if res.returncode == 0:
            return res.stdout.strip()
        return None

    def get_diff(self, ref_spec: str) -> str:
        """Get unified diff for the given reference specification."""
        res = self.run_cmd(["diff", "--binary", ref_spec])
        return res.stdout

    def get_diff_root(self, commit_hash: str) -> str:
        """Get unified diff for a root commit."""
        # Diff against Git's empty tree hash
        empty_tree_hash = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        res = self.run_cmd(["diff", "--binary", empty_tree_hash, commit_hash])
        return res.stdout

    def get_name_status(self, ref_spec: str) -> List[FileChange]:
        """Get list of changed files with their status."""
        res = self.run_cmd(["diff", "-z", "--name-status", ref_spec])
        return parse_name_status_z(res.stdout)

    def get_name_status_root(self, commit_hash: str) -> List[FileChange]:
        """Get list of changed files for a root commit."""
        empty_tree_hash = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        res = self.run_cmd(["diff", "-z", "--name-status", empty_tree_hash, commit_hash])
        return parse_name_status_z(res.stdout)

    def get_commit_message(self, commit_hash: str) -> str:
        """Get the full commit message for the given commit hash."""
        res = self.run_cmd(["log", "-1", "--format=%B", commit_hash])
        return res.stdout.strip()

    def get_commit_range(self, start_ref: str, end_ref: str) -> List[str]:
        """Return list of commit hashes in chronological order (start_ref..end_ref)."""
        res = self.run_cmd(["rev-list", "--reverse", "--topo-order", f"{start_ref}..{end_ref}"])
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return lines

    def get_merge_commits(self, start_ref: str, end_ref: str) -> List[str]:
        """Return list of merge commits in the range (start_ref..end_ref)."""
        res = self.run_cmd(["rev-list", "--merges", f"{start_ref}..{end_ref}"])
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]


class SvnWorkspace:
    """Wrapper around SVN commands and filesystem staging operations."""

    def __init__(self, workspace_dir: Path, svn_bin: str = "svn", dry_run: bool = False):
        self.workspace_dir = workspace_dir.resolve()
        self.svn_bin = svn_bin
        self.dry_run = dry_run

    def run_cmd(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.svn_bin] + args
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) { ' '.join(cmd) }")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        logger.debug("Executing SVN command in %s: %s", self.workspace_dir, " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.workspace_dir,
            check=check,
            capture_output=True,
            text=True,
        )

    def is_valid_workspace(self) -> bool:
        """Check if workspace directory exists and has .svn or svn info."""
        if not self.workspace_dir.is_dir():
            return False
        if (self.workspace_dir / ".svn").exists():
            return True
        # Fallback check via svn info
        try:
            res = self.run_cmd(["info"], check=False)
            return res.returncode == 0
        except Exception:
            return False

    def is_clean(self) -> bool:
        """Check if SVN workspace has no uncommitted changes."""
        res = self.run_cmd(["status", "-q"], check=False)
        return not bool(res.stdout.strip())

    def revert_all(self) -> None:
        """Revert all uncommitted changes in the workspace."""
        logger.info("Reverting SVN workspace changes...")
        self.run_cmd(["revert", "-R", "."], check=False)

    def stage_add(self, rel_path: Path) -> None:
        """Run svn add <filepath> --parents. Creates parent directories if needed."""
        target_path = self.workspace_dir / rel_path
        if not self.dry_run:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            print(f"[DRY-RUN] Ensure parent directory exists: {target_path.parent}")

        posix_path = rel_path.as_posix()
        res = self.run_cmd(["add", posix_path, "--parents"], check=False)
        if res.returncode != 0:
            # Check if it was already versioned (common in repeated runs)
            if "is already under version control" not in res.stderr and "already exists" not in res.stderr:
                logger.error("Failed to 'svn add %s': %s", posix_path, res.stderr.strip())
                raise subprocess.CalledProcessError(res.returncode, [self.svn_bin, "add", posix_path], res.stdout, res.stderr)

    def stage_rm(self, rel_path: Path) -> None:
        """Run svn rm <filepath>."""
        posix_path = rel_path.as_posix()
        res = self.run_cmd(["rm", posix_path], check=False)
        if res.returncode != 0:
            if "is not under version control" in res.stderr:
                logger.warning("File %s not under SVN control to remove.", posix_path)
            else:
                logger.error("Failed to 'svn rm %s': %s", posix_path, res.stderr.strip())
                raise subprocess.CalledProcessError(res.returncode, [self.svn_bin, "rm", posix_path], res.stdout, res.stderr)

    def commit(self, message: str) -> None:
        """Run svn commit -m <message>."""
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) {self.svn_bin} commit -m {message!r}")
            return

        logger.info("Executing svn commit in %s...", self.workspace_dir)
        res = self.run_cmd(["commit", "-m", message], check=False)
        if res.returncode != 0:
            logger.error("Failed to 'svn commit': %s", res.stderr.strip())
            raise subprocess.CalledProcessError(
                res.returncode, [self.svn_bin, "commit", "-m", message], res.stdout, res.stderr
            )
        if res.stdout:
            logger.info("SVN commit output:\n%s", res.stdout.strip())

    def apply_structural_changes(self, changes: List[FileChange]) -> None:
        """
        Execute corresponding SVN commands for file changes:
        - Added (A): Run svn add <filepath> --parents
        - Deleted (D): Run svn rm <filepath>
        - Modified (M): No SVN structural command needed
        - Renamed (R): svn rm <old_name> and svn add <new_name> --parents
        - Copied (C): svn add <new_name> --parents
        """
        for change in changes:
            if change.is_deleted:
                logger.info("SVN staging delete: %s", change.path)
                self.stage_rm(change.path)
            elif change.is_renamed:
                assert change.old_path is not None
                logger.info("SVN staging rename: %s -> %s", change.old_path, change.path)
                self.stage_rm(change.old_path)
                self.stage_add(change.path)
            elif change.is_copied:
                logger.info("SVN staging copied file: %s", change.path)
                self.stage_add(change.path)
            elif change.is_added:
                logger.info("SVN staging add: %s", change.path)
                self.stage_add(change.path)
            elif change.is_modified:
                logger.debug("Modified file requires no SVN structural command: %s", change.path)


def find_patch_binary() -> str:
    """Find the patch executable, checking Git for Windows default paths if on Windows."""
    found = shutil.which("patch")
    if found:
        return found
    if sys.platform == "win32":
        git_path = shutil.which("git")
        if git_path:
            git_dir = Path(git_path).resolve().parent
            candidates = [
                git_dir.parent / "usr" / "bin" / "patch.exe",
                git_dir / "patch.exe",
                Path("C:/Program Files/Git/usr/bin/patch.exe"),
                Path("C:/Program Files (x86)/Git/usr/bin/patch.exe"),
            ]
            for c in candidates:
                if c.is_file():
                    return str(c)
    return "patch"


class Patcher:
    """Wrapper to apply diffs using patch -p1."""

    def __init__(self, target_dir: Path, patch_bin: Optional[str] = None, dry_run: bool = False):
        self.target_dir = target_dir.resolve()
        self.patch_bin = patch_bin or find_patch_binary()
        self.dry_run = dry_run

    def apply_diff(self, diff_content: str) -> None:
        """Apply unified diff text to target_dir using patch -p1."""
        if not diff_content.strip():
            logger.info("Diff is empty. Nothing to patch.")
            return

        cmd = [self.patch_bin, "-p1", "--batch", "--binary"]
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.target_dir}) { ' '.join(cmd) } << EOF\n{diff_content.strip()[:200]}...\nEOF")
            return

        logger.debug("Applying patch to %s using %s", self.target_dir, " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=self.target_dir,
            input=diff_content,
            capture_output=True,
            text=True,
        )

        if proc.returncode != 0:
            logger.error("patch failed with code %d:\nSTDOUT:\n%s\nSTDERR:\n%s",
                         proc.returncode, proc.stdout, proc.stderr)
            print(f"Error: patch command failed (exit code {proc.returncode}).", file=sys.stderr)
            if proc.stdout:
                print(proc.stdout, file=sys.stderr)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            print("Tip: Check for .rej reject files in the SVN workspace or use 'git2svn sync' to copy files directly.", file=sys.stderr)
            raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
        else:
            if proc.stdout:
                logger.info("Patch applied successfully:\n%s", proc.stdout.strip())


class Synchronizer:
    """Coordinates Git extraction, patch/copy operations, and SVN staging."""

    def __init__(
        self,
        git_repo: GitRepo,
        svn_workspace: SvnWorkspace,
        patcher: Patcher,
        dry_run: bool = False,
    ):
        self.git = git_repo
        self.svn = svn_workspace
        self.patcher = patcher
        self.dry_run = dry_run

    def cherry_pick(self, commit_hash: str, commit_svn: bool = False) -> None:
        """
        Port a single Git commit to SVN workspace:
        1. Extract diff of specified commit (<commit>^..<commit>).
        2. Apply diff to SVN workspace using patch -p1.
        3. Parse git diff --name-status and apply SVN structural commands.
        4. Optionally commit to SVN using the exact Git commit message.
        """
        logger.info("Running cherry-pick for commit %s", commit_hash)
        parent = self.git.get_commit_parent(commit_hash)
        if parent:
            ref_spec = f"{parent}..{commit_hash}"
            diff_text = self.git.get_diff(ref_spec)
            changes = self.git.get_name_status(ref_spec)
        else:
            logger.info("Commit %s is a root commit (no parent).", commit_hash)
            diff_text = self.git.get_diff_root(commit_hash)
            changes = self.git.get_name_status_root(commit_hash)

        self._ensure_parent_dirs_for_changes(changes)

        logger.info("Applying patch diff...")
        self.patcher.apply_diff(diff_text)

        logger.info("Staging SVN structural changes...")
        self.svn.apply_structural_changes(changes)

        if commit_svn:
            commit_msg = self.git.get_commit_message(commit_hash)
            logger.info("Committing to SVN with Git commit message:\n%s", commit_msg)
            self.svn.commit(commit_msg)

        logger.info("Cherry-pick completed successfully.")

    def squash(self, start_ref: str, end_ref: str) -> None:
        """
        Port a continuous range of Git commits (<start_ref>..<end_ref>) to SVN workspace:
        1. Extract diff between the two references.
        2. Apply diff to SVN workspace using patch -p1.
        3. Parse git diff --name-status and apply SVN structural commands.
        """
        logger.info("Running squash from %s to %s", start_ref, end_ref)
        ref_spec = f"{start_ref}..{end_ref}"
        diff_text = self.git.get_diff(ref_spec)
        changes = self.git.get_name_status(ref_spec)

        self._ensure_parent_dirs_for_changes(changes)

        logger.info("Applying patch diff...")
        self.patcher.apply_diff(diff_text)

        logger.info("Staging SVN structural changes...")
        self.svn.apply_structural_changes(changes)
        logger.info("Squash completed successfully.")

    def sync(self, base_ref: str, target_ref: str) -> None:
        """
        Port changes by brute-force copying files using shutil:
        1. Identify changed files between base_ref and target_ref.
        2. Copy modified/added files from Git workspace to SVN workspace.
        3. Parse git diff --name-status and apply SVN structural commands.
        """
        logger.info("Running sync from %s to %s", base_ref, target_ref)
        ref_spec = f"{base_ref}..{target_ref}"
        changes = self.git.get_name_status(ref_spec)

        # 1. Handle deleted files in SVN first
        for change in changes:
            if change.is_deleted:
                logger.info("SVN staging delete: %s", change.path)
                self.svn.stage_rm(change.path)
                # Ensure deleted from disk as well
                target_file = self.svn.workspace_dir / change.path
                if not self.dry_run and target_file.exists():
                    if target_file.is_dir():
                        shutil.rmtree(target_file)
                    else:
                        target_file.unlink()
            elif change.is_renamed:
                assert change.old_path is not None
                logger.info("SVN staging rename (removal of old path): %s", change.old_path)
                self.svn.stage_rm(change.old_path)
                target_old = self.svn.workspace_dir / change.old_path
                if not self.dry_run and target_old.exists():
                    target_old.unlink()

        # 2. Copy added, modified, renamed, and copied files
        for change in changes:
            if change.is_added or change.is_modified or change.is_renamed or change.is_copied:
                src_path = self.git.repo_dir / change.path
                dst_path = self.svn.workspace_dir / change.path

                if not src_path.exists():
                    logger.warning("Source file not found in Git workspace: %s", src_path)
                    print(f"Warning: '{src_path}' does not exist on disk in Git workspace. Make sure '{target_ref}' is checked out in Git.", file=sys.stderr)
                    continue

                if self.dry_run:
                    print(f"[DRY-RUN] Copy {src_path} -> {dst_path}")
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    if src_path.is_symlink():
                        if dst_path.exists() or dst_path.is_symlink():
                            dst_path.unlink()
                        shutil.copy2(src_path, dst_path, follow_symlinks=False)
                    elif src_path.is_file():
                        shutil.copy2(src_path, dst_path)

        # 3. Stage added, renamed, and copied files in SVN
        for change in changes:
            if change.is_added or change.is_renamed or change.is_copied:
                logger.info("SVN staging addition: %s", change.path)
                self.svn.stage_add(change.path)

        logger.info("Sync completed successfully.")

    def replay(self, start_ref: str, end_ref: str) -> None:
        """
        Replay a series of Git commits (start_ref..end_ref) sequentially onto SVN.
        Each commit is patched, staged, and committed using its Git commit message.
        If a conflict occurs, state is saved so user can resume with --continue.
        """
        if not self.dry_run and not self.svn.is_clean():
            raise RuntimeError(
                "SVN workspace has uncommitted changes. Please commit, stash, or revert them before starting a replay."
            )

        merges = self.git.get_merge_commits(start_ref, end_ref)
        if merges:
            raise RuntimeError(
                f"Range {start_ref}..{end_ref} contains {len(merges)} merge commit(s). "
                "Replay requires a linear history (fast-forward only). Please rebase your branch first."
            )

        commits = self.git.get_commit_range(start_ref, end_ref)
        if not commits:
            logger.info("No commits found in range %s..%s.", start_ref, end_ref)
            return

        logger.info("Starting replay of %d commit(s) from %s to %s...", len(commits), start_ref, end_ref)
        self._execute_replay_queue(commits, total_commits=len(commits), start_index=1)

    def replay_continue(self) -> None:
        """Resume an interrupted replay after user resolves conflicts."""
        state = load_replay_state(self.svn.workspace_dir)
        if not state:
            raise RuntimeError("No replay in progress. Nothing to continue.")

        current_commit = state["current_commit"]
        current_msg = state["current_commit_msg"]
        remaining = state.get("remaining_commits", [])
        total = state.get("total_commits", len(remaining) + 1)
        completed = state.get("completed_commits", 0)

        # 1. Check for leftover .rej / .orig files
        rej_files = find_conflict_artifacts(self.svn.workspace_dir)
        if rej_files:
            rel_rejs = [str(r.relative_to(self.svn.workspace_dir)) for r in rej_files]
            raise RuntimeError(
                f"Found rejected patch artifacts ({', '.join(rel_rejs)}). "
                "Please resolve conflicts and delete .rej / .orig files before running --continue."
            )

        # 2. Commit the resolved changes for the interrupted commit
        if not self.svn.is_clean():
            logger.info("Committing resolved commit %s to SVN...", current_commit)
            self.svn.commit(current_msg)
        else:
            logger.info("No changes in SVN workspace to commit for %s (commit resolved as empty or skipped).", current_commit)

        completed += 1
        logger.info("Commit %s (%d/%d) resolved and committed.", current_commit, completed, total)

        # 3. Resume remaining queue
        if remaining:
            self._execute_replay_queue(remaining, total_commits=total, start_index=completed + 1)
        else:
            clear_replay_state(self.svn.workspace_dir)
            logger.info("Replay completed successfully! All %d commits applied.", total)

    def replay_abort(self) -> None:
        """Abort in-progress replay and revert uncommitted changes."""
        state = load_replay_state(self.svn.workspace_dir)
        if not state:
            raise RuntimeError("No replay in progress. Nothing to abort.")

        current = state["current_commit"]
        logger.info("Aborting replay at commit %s...", current)
        if not self.dry_run:
            self.svn.revert_all()
            clean_conflict_artifacts(self.svn.workspace_dir)
            clear_replay_state(self.svn.workspace_dir)
        logger.info("Replay aborted. SVN workspace reverted to last clean commit.")

    def replay_skip(self) -> None:
        """Skip current interrupted commit and proceed with remaining queue."""
        state = load_replay_state(self.svn.workspace_dir)
        if not state:
            raise RuntimeError("No replay in progress. Nothing to skip.")

        current = state["current_commit"]
        remaining = state.get("remaining_commits", [])
        total = state.get("total_commits", len(remaining) + 1)
        completed = state.get("completed_commits", 0)

        logger.info("Skipping commit %s...", current)
        if not self.dry_run:
            self.svn.revert_all()
            clean_conflict_artifacts(self.svn.workspace_dir)

        if remaining:
            self._execute_replay_queue(remaining, total_commits=total, start_index=completed + 2)
        else:
            clear_replay_state(self.svn.workspace_dir)
            logger.info("Replay finished (last commit was skipped).")

    def _execute_replay_queue(self, commits: List[str], total_commits: int, start_index: int) -> None:
        """Execute a list of commits sequentially, catching conflicts and persisting state."""
        for idx, commit_hash in enumerate(commits, start=start_index):
            commit_msg = self.git.get_commit_message(commit_hash)
            first_line = commit_msg.splitlines()[0] if commit_msg else ""
            logger.info("[%d/%d] Applying commit %s: %s", idx, total_commits, commit_hash[:8], first_line)

            try:
                self.cherry_pick(commit_hash, commit_svn=True)
            except Exception as e:
                # Patch conflict or staging error
                remaining = commits[idx - start_index + 1:]
                state_data = {
                    "state": "CONFLICT_PAUSED",
                    "git_dir": str(self.git.repo_dir),
                    "svn_dir": str(self.svn.workspace_dir),
                    "current_commit": commit_hash,
                    "current_commit_msg": commit_msg,
                    "remaining_commits": remaining,
                    "total_commits": total_commits,
                    "completed_commits": idx - 1,
                }
                if not self.dry_run:
                    save_replay_state(self.svn.workspace_dir, state_data)

                rej_files = find_conflict_artifacts(self.svn.workspace_dir)
                rej_info = ""
                if rej_files:
                    rej_rel = [str(r.relative_to(self.svn.workspace_dir)) for r in rej_files]
                    rej_info = f"\nConflicts detected in:\n" + "\n".join(f"  - {f}" for f in rej_rel)

                print(
                    f"\n[PAUSED] Conflict while applying commit {commit_hash[:8]} ({idx}/{total_commits}): \"{first_line}\""
                    f"{rej_info}\n\n"
                    f"To resolve:\n"
                    f"  1. Resolve conflicts in '{self.svn.workspace_dir}' and stage changes ('svn add' / 'svn rm').\n"
                    f"  2. Remove any leftover .rej / .orig files.\n"
                    f"  3. Run: git2svn replay --continue\n"
                    f"     (or 'git2svn replay --abort' to discard, or 'git2svn replay --skip' to skip)\n",
                    file=sys.stderr,
                )
                raise
        clear_replay_state(self.svn.workspace_dir)
        logger.info("Replay completed successfully! All %d commits applied.", total_commits)

    def _ensure_parent_dirs_for_changes(self, changes: List[FileChange]) -> None:
        """Create parent directories in SVN workspace for new/renamed files."""
        for change in changes:
            if change.is_added or change.is_renamed or change.is_copied:
                target_file = self.svn.workspace_dir / change.path
                if self.dry_run:
                    print(f"[DRY-RUN] Ensure directory exists: {target_file.parent}")
                else:
                    target_file.parent.mkdir(parents=True, exist_ok=True)


def find_default_git_dir() -> Path:
    """Find the Git repository root from current working directory."""
    cwd = Path.cwd()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return Path(res.stdout.strip())
    except Exception:
        pass
    return cwd


def build_parser() -> argparse.ArgumentParser:
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--git-dir",
        "-g",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to Git repository (default: detected git root or current directory)",
    )
    common_parser.add_argument(
        "--svn-dir",
        "-s",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to SVN working copy (default: $SVN_DIR environment variable)",
    )
    common_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Print actions without modifying files or executing SVN commands",
    )
    common_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Enable verbose output",
    )

    parser = argparse.ArgumentParser(
        prog="git2svn",
        description="Synchronize commits and changes from a local Git repository to a local SVN workspace.",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Valid subcommands",
        required=True,
    )

    # cherry-pick
    parser_cp = subparsers.add_parser(
        "cherry-pick",
        parents=[common_parser],
        help="Port a single Git commit to SVN using patch -p1",
    )
    parser_cp.add_argument("commit_hash", help="Git commit hash to port")
    parser_cp.add_argument(
        "--commit",
        "-c",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Commit staged changes to SVN using the exact Git commit message",
    )

    # squash
    parser_sq = subparsers.add_parser(
        "squash",
        parents=[common_parser],
        help="Port a commit range (e.g. feature branch) to SVN using patch -p1",
    )
    parser_sq.add_argument("start_ref", help="Starting Git reference / commit / branch")
    parser_sq.add_argument("end_ref", help="Ending Git reference / commit / branch")

    # sync
    parser_sync = subparsers.add_parser(
        "sync",
        parents=[common_parser],
        help="Port changes by brute-force copying files using shutil (ideal for binaries/conflicts)",
    )
    parser_sync.add_argument("base_ref", help="Base Git reference for diff comparison")
    parser_sync.add_argument("target_ref", help="Target Git reference with finalized state")

    # replay
    parser_replay = subparsers.add_parser(
        "replay",
        parents=[common_parser],
        help="Fast-forward replay a series of commits one-by-one to SVN with conflict pause/resume",
    )
    parser_replay.add_argument("start_ref", nargs="?", default=None, help="Starting Git reference / base commit")
    parser_replay.add_argument("end_ref", nargs="?", default=None, help="Ending Git reference / target commit")
    action_group = parser_replay.add_mutually_exclusive_group()
    action_group.add_argument(
        "--continue",
        dest="replay_action",
        action="store_const",
        const="continue",
        default=argparse.SUPPRESS,
        help="Continue an in-progress replay after resolving conflicts",
    )
    action_group.add_argument(
        "--abort",
        dest="replay_action",
        action="store_const",
        const="abort",
        default=argparse.SUPPRESS,
        help="Abort in-progress replay and revert uncommitted changes",
    )
    action_group.add_argument(
        "--skip",
        dest="replay_action",
        action="store_const",
        const="skip",
        default=argparse.SUPPRESS,
        help="Skip the current failed commit and continue with the next",
    )

    return parser


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    namespace = argparse.Namespace(
        git_dir=None,
        svn_dir=None,
        dry_run=False,
        verbose=False,
        commit=False,
        replay_action=None,
    )
    return parser.parse_args(argv, namespace=namespace)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    svn_dir = args.svn_dir or (Path(os.environ["SVN_DIR"]) if "SVN_DIR" in os.environ else None)

    # For replay resume/abort actions, auto-detect svn_dir from cwd if it is an SVN checkout
    if not svn_dir and args.command == "replay" and getattr(args, "replay_action", None):
        cwd = Path.cwd()
        if (cwd / ".svn").exists() or load_replay_state(cwd):
            svn_dir = cwd

    if not svn_dir:
        print("Error: SVN workspace directory must be specified with --svn-dir or the SVN_DIR environment variable.", file=sys.stderr)
        return 1

    git_dir = args.git_dir
    if not git_dir and args.command == "replay" and getattr(args, "replay_action", None):
        state = load_replay_state(svn_dir)
        if state and "git_dir" in state:
            git_dir = Path(state["git_dir"])

    git_dir = git_dir or find_default_git_dir()

    git_repo = GitRepo(git_dir)
    if not git_repo.is_valid_repo():
        print(f"Error: '{git_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    svn_workspace = SvnWorkspace(svn_dir, dry_run=args.dry_run)
    if not svn_workspace.is_valid_workspace() and not args.dry_run:
        print(f"Error: '{svn_dir}' does not appear to be an SVN working copy (no .svn found).", file=sys.stderr)
        return 1

    patcher = Patcher(svn_dir, dry_run=args.dry_run)
    sync_mgr = Synchronizer(git_repo, svn_workspace, patcher, dry_run=args.dry_run)

    try:
        if args.command == "cherry-pick":
            sync_mgr.cherry_pick(args.commit_hash, commit_svn=getattr(args, "commit", False))
        elif args.command == "squash":
            sync_mgr.squash(args.start_ref, args.end_ref)
        elif args.command == "sync":
            sync_mgr.sync(args.base_ref, args.target_ref)
        elif args.command == "replay":
            action = getattr(args, "replay_action", None)
            if action == "continue":
                sync_mgr.replay_continue()
            elif action == "abort":
                sync_mgr.replay_abort()
            elif action == "skip":
                sync_mgr.replay_skip()
            else:
                if not args.start_ref or not args.end_ref:
                    print("Error: replay requires both start_ref and end_ref unless using --continue, --abort, or --skip.", file=sys.stderr)
                    return 1
                sync_mgr.replay(args.start_ref, args.end_ref)
        else:
            parser.print_help()
            return 1
    except subprocess.CalledProcessError as e:
        logger.error("Process failed with returncode %s", e.returncode)
        return e.returncode
    except Exception as e:
        logger.error("Operation failed: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
