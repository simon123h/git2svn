from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("git2svn")


def parse_ref_arguments(
    primary_ref: Optional[str], secondary_ref: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Parses CLI ref arguments into either a single commit or a (start_ref, end_ref) range.
    Returns: (is_single, start_or_commit, end_ref)
    Examples:
        'abc1234' -> (True, 'abc1234', None)
        'main..feature' -> (False, 'main', 'feature')
        'main', 'feature' -> (False, 'main', 'feature')
    """
    if not primary_ref:
        raise ValueError("A commit reference or range is required.")

    if secondary_ref:
        return False, primary_ref, secondary_ref

    if ".." in primary_ref:
        parts = primary_ref.split("..", 1)
        return False, parts[0], parts[1]

    return True, primary_ref, None


class FileChange:
    """Represents a file change between two Git revisions."""

    def __init__(self, action: str, path: str, old_path: Optional[str] = None):
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
    """Parse standard tabular output of `git diff --name-status`."""
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
    """Parse NUL-delimited output from `git diff -z --name-status`."""
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

    def run_cmd_bytes(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess[bytes]:
        cmd = [self.git_bin] + args
        logger.debug("Executing Git command (bytes) in %s: %s", self.repo_dir, " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.repo_dir,
            check=check,
            capture_output=True,
        )

    def get_file_content_bytes(self, ref: str, path: Path) -> bytes:
        """Extract exact binary content of a file from Git's object database at revision ref."""
        ref_path = f"{ref}:{path.as_posix()}"
        res = self.run_cmd_bytes(["show", ref_path])
        return res.stdout

    def get_file_mode(self, ref: str, path: Path) -> Optional[str]:
        """Return the file mode (e.g. '100644', '100755', '120000') from git ls-tree."""
        res = self.run_cmd(["ls-tree", ref, path.as_posix()], check=False)
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split()
            if parts:
                return parts[0]
        return None

    def is_valid_repo(self) -> bool:
        try:
            res = self.run_cmd(["rev-parse", "--is-inside-work-tree"], check=False)
            return res.returncode == 0 and res.stdout.strip() == "true"
        except Exception:
            return False

    def get_current_branch(self) -> str:
        """Return the current branch name or 'HEAD (detached)'."""
        res = self.run_cmd(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        if res.returncode == 0:
            branch = res.stdout.strip()
            return branch if branch != "HEAD" else "HEAD (detached)"
        return "unknown"

    def get_head_commit(self) -> str:
        """Return the short hash of HEAD."""
        res = self.run_cmd(["rev-parse", "--short", "HEAD"], check=False)
        if res.returncode == 0:
            return res.stdout.strip()
        return "unknown"

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
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]

    def get_merge_commits(self, start_ref: str, end_ref: str) -> List[str]:
        """Return list of merge commits in the range (start_ref..end_ref)."""
        res = self.run_cmd(["rev-list", "--merges", f"{start_ref}..{end_ref}"])
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]

    def get_tree_files(self, ref: str) -> List[Path]:
        """Return list of all relative file paths at revision ref (from git ls-tree -r -z)."""
        res = self.run_cmd_bytes(["ls-tree", "-r", "-z", "--name-only", ref])
        raw_paths = res.stdout.split(b"\0")
        files: List[Path] = []
        for raw in raw_paths:
            if not raw:
                continue
            path_str = raw.decode("utf-8", errors="replace")
            files.append(Path(path_str))
        return files
