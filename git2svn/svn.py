from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

from .git import FileChange

logger = logging.getLogger("git2svn")


def find_svn_binary() -> str:
    """Find svn executable, checking TortoiseSVN / SlikSVN default paths if on Windows."""
    found = shutil.which("svn")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            Path("C:/Program Files/TortoiseSVN/bin/svn.exe"),
            Path("C:/Program Files (x86)/TortoiseSVN/bin/svn.exe"),
            Path("C:/Program Files/SlikSvn/bin/svn.exe"),
            Path("C:/Program Files (x86)/SlikSvn/bin/svn.exe"),
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
    return "svn"


def is_svn_url_or_repo(target: str | Path) -> bool:
    """
    Determine if target is an SVN URL (http://, svn://, file://, etc.) or a raw SVN repository directory.
    """
    target_str = str(target).strip()
    url_schemes = ("http://", "https://", "svn://", "svn+ssh://", "file://")
    if any(target_str.startswith(scheme) for scheme in url_schemes):
        return True

    p = Path(target_str)
    # Check if local path points to an SVN repository store (not a working copy)
    if p.is_dir() and (p / "format").is_file() and (p / "db").is_dir():
        return True

    return False


def get_default_managed_svn_dir(git_repo_dir: Path) -> Path:
    """Return canonical path for the repository-local managed SVN working copy."""
    return git_repo_dir / ".git" / "git2svn" / "svn_wc"


def checkout_working_copy(
    target_url_or_repo: str | Path,
    destination_dir: Path,
    svn_bin: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """
    Check out an SVN working copy to destination_dir from an SVN URL or repo path.
    """
    bin_name = svn_bin or find_svn_binary()
    target_str = str(target_url_or_repo).strip()
    if not any(target_str.startswith(s) for s in ("http://", "https://", "svn://", "svn+ssh://", "file://")):
        # If it's a local filesystem repo path, convert to file:// URI
        target_str = Path(target_str).resolve().as_uri()

    destination_dir = destination_dir.resolve()
    if dry_run:
        print(f"[DRY-RUN] {bin_name} checkout {target_str} {destination_dir}")
        return

    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Checking out SVN working copy from %s to %s...", target_str, destination_dir)
    res = subprocess.run(
        [bin_name, "checkout", target_str, str(destination_dir)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        logger.error("Failed to checkout SVN repository: %s", res.stderr.strip())
        raise parse_svn_error(res.stderr, f"checkout '{target_str}'", destination_dir)


class SvnError(RuntimeError):
    """Base exception for Subversion command failures."""

    def __init__(self, message: str, stderr: str = "", returncode: int = 1):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


class SvnLockError(SvnError):
    """Raised when the SVN working copy or a file is locked (e.g. E155004)."""

    pass


class SvnOutOfDateError(SvnError):
    """Raised when an SVN commit or update fails due to out-of-date items or collision (e.g. E155015 / E160024)."""

    pass


def parse_svn_error(stderr: str, action_desc: str, svn_dir: Path) -> SvnError:
    """Analyze Subversion stderr and construct actionable SvnError with cleanup hints."""
    lower_err = stderr.lower()
    clean_stderr = stderr.strip()

    # 1. Working copy locked (E155004 / "is already locked" / "run 'svn cleanup'")
    if (
        "e155004" in lower_err
        or "working copy locked" in lower_err
        or "already locked" in lower_err
        or "run 'svn cleanup'" in lower_err
    ):
        hint = (
            f"Subversion working copy at '{svn_dir}' is locked.\n"
            f"Details from SVN:\n  {clean_stderr}\n\n"
            f"To resolve:\n"
            f'  1. Run: svn cleanup "{svn_dir}"\n'
            f"  2. If a background process (IDE, mirror sync, indexing) is running, wait for it to finish.\n"
            f"  3. Retry your git2svn command."
        )
        return SvnLockError(hint, stderr=clean_stderr)

    # 2. Out of date / collision (E155015 / E160024 / E155011 / "out of date" / "item is out of date")
    if (
        "e155015" in lower_err
        or "e160024" in lower_err
        or "e155011" in lower_err
        or "out of date" in lower_err
        or "conflict" in lower_err
    ):
        hint = (
            f"Subversion working copy at '{svn_dir}' is out of date or conflicted.\n"
            f"Details from SVN:\n  {clean_stderr}\n\n"
            f"To resolve:\n"
            f'  1. Run: svn update "{svn_dir}"\n'
            f"  2. Resolve any SVN conflicts if present ('svn status').\n"
            f"  3. Retry your git2svn command (e.g. 'git2svn replay --continue' or retry replay)."
        )
        return SvnOutOfDateError(hint, stderr=clean_stderr)

    # Generic SVN error with context
    msg = f"Failed to {action_desc} in '{svn_dir}':\n  {clean_stderr}"
    return SvnError(msg, stderr=clean_stderr)


class SvnWorkspace:
    """Wrapper around SVN commands and filesystem staging operations."""

    def __init__(self, workspace_dir: Path, svn_bin: Optional[str] = None, dry_run: bool = False):
        self.workspace_dir = workspace_dir.resolve()
        self.svn_bin = svn_bin or find_svn_binary()
        self.dry_run = dry_run

    def run_cmd(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.svn_bin] + args
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) {' '.join(cmd)}")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        logger.debug("Executing SVN command in %s: %s", self.workspace_dir, " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.workspace_dir,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def is_valid_workspace(self) -> bool:
        """Check if workspace directory exists and has .svn or svn info."""
        if not self.workspace_dir.is_dir():
            return False
        if (self.workspace_dir / ".svn").exists():
            return True
        try:
            res = self.run_cmd(["info"], check=False)
            return res.returncode == 0
        except Exception:
            return False

    def get_info(self) -> Dict[str, str]:
        """Return key-value mapping of 'svn info' output."""
        info: Dict[str, str] = {}
        res = self.run_cmd(["info"], check=False)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
        return info

    def is_clean(self) -> bool:
        """Check if SVN workspace has no uncommitted changes."""
        res = self.run_cmd(["status", "-q"], check=False)
        if res.returncode != 0:
            raise parse_svn_error(res.stderr, "check status", self.workspace_dir)
        return not bool(res.stdout.strip())

    def diff(self, target: Optional[str] = None, stat: bool = False) -> str:
        """
        Run 'svn diff' on the workspace or a specific target.
        If stat is True, runs 'svn diff --stat' (summarized file changes).
        """
        args = ["diff"]
        if stat:
            args.append("--stat")
        if target:
            args.append(target)
        res = self.run_cmd(args, check=False)
        if res.returncode != 0:
            raise parse_svn_error(res.stderr, "diff", self.workspace_dir)
        return res.stdout

    def get_status_summary(self) -> List[str]:
        """Return lines of uncommitted changes from 'svn status -q'."""
        res = self.run_cmd(["status", "-q"], check=False)
        if res.returncode != 0:
            raise parse_svn_error(res.stderr, "check status", self.workspace_dir)
        return [line.rstrip() for line in res.stdout.splitlines() if line.strip()]

    def get_versioned_files(self) -> List[Path]:
        """
        Return list of all versioned file paths in the SVN workspace (relative to workspace_dir).
        Queries 'svn status -v -q --depth infinity' (ignores unversioned files and directories).
        Falls back to filesystem scan (excluding .svn) if svn command fails or workspace is empty.
        """
        files: List[Path] = []
        res = self.run_cmd(["status", "-v", "-q", "--depth", "infinity"], check=False)
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                target_str = parts[-1]
                target_path = Path(target_str)
                if target_path.is_absolute():
                    try:
                        rel = target_path.relative_to(self.workspace_dir)
                    except ValueError:
                        continue
                else:
                    rel = target_path

                full_path = self.workspace_dir / rel
                if str(rel) != "." and (full_path.is_file() or full_path.is_symlink() or not full_path.exists()):
                    files.append(rel)
            return files

        # Filesystem fallback if not in a working copy or status was empty
        for root, dirs, f_list in os.walk(self.workspace_dir):
            if ".svn" in dirs:
                dirs.remove(".svn")
            for f in f_list:
                full = Path(root) / f
                try:
                    files.append(full.relative_to(self.workspace_dir))
                except ValueError:
                    pass
        return files

    def revert_all(self) -> None:
        """Revert all uncommitted changes in the workspace."""
        logger.info("Reverting SVN workspace changes...")
        self.run_cmd(["revert", "-R", "."], check=False)

    def cleanup(self) -> None:
        """Run svn cleanup to release locks and fix interrupted operations."""
        logger.info("Running svn cleanup in %s...", self.workspace_dir)
        res = self.run_cmd(["cleanup"], check=False)
        if res.returncode != 0:
            logger.error("Failed to run 'svn cleanup': %s", res.stderr.strip())
            raise parse_svn_error(res.stderr, "cleanup", self.workspace_dir)

    def get_unversioned_items(self) -> List[Path]:
        """Return list of unversioned files and directories ('?' status in svn status)."""
        res = self.run_cmd(["status"], check=False)
        items: List[Path] = []
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("?"):
                    parts = line.split(maxsplit=1)
                    if len(parts) >= 2:
                        items.append(Path(parts[1].strip()))
        return items

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
            if "is already under version control" not in res.stderr and "already exists" not in res.stderr:
                logger.error("Failed to 'svn add %s': %s", posix_path, res.stderr.strip())
                raise parse_svn_error(res.stderr, f"add '{posix_path}'", self.workspace_dir)

    def stage_rm(self, rel_path: Path) -> None:
        """Run svn rm <filepath>."""
        posix_path = rel_path.as_posix()
        res = self.run_cmd(["rm", posix_path], check=False)
        if res.returncode != 0:
            if "is not under version control" in res.stderr:
                logger.warning("File %s not under SVN control to remove.", posix_path)
            else:
                logger.error("Failed to 'svn rm %s': %s", posix_path, res.stderr.strip())
                raise parse_svn_error(res.stderr, f"remove '{posix_path}'", self.workspace_dir)

    def commit(self, message: str) -> None:
        """Run svn commit using a temporary file with -F to support arbitrary message lengths and encodings."""
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) {self.svn_bin} commit -F <msg_file>")
            return

        logger.info("Executing svn commit in %s...", self.workspace_dir)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(message)
            tf_path = Path(tf.name)

        try:
            res = self.run_cmd(["commit", "-F", str(tf_path)], check=False)
            if res.returncode != 0:
                logger.error("Failed to 'svn commit': %s", res.stderr.strip())
                raise parse_svn_error(res.stderr, "commit", self.workspace_dir)
            if res.stdout:
                logger.info("SVN commit output:\n%s", res.stdout.strip())
        finally:
            tf_path.unlink(missing_ok=True)

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

    def update(self) -> None:
        """Run svn update on the working copy to bump the working base revision to HEAD."""
        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) {self.svn_bin} update")
            return

        logger.info("Executing svn update in %s...", self.workspace_dir)
        res = self.run_cmd(["update"], check=False)
        if res.returncode != 0:
            logger.error("Failed to 'svn update': %s", res.stderr.strip())
            raise parse_svn_error(res.stderr, "update", self.workspace_dir)
        if res.stdout:
            logger.info("SVN update output:\n%s", res.stdout.strip())

    def get_recent_log_messages(self, limit: int = 25) -> List[str]:
        """
        Fetch the most recent commit log messages from the SVN repository using svn log --xml.
        Returns a list of log message strings, ordered from newest to oldest.
        """
        if self.dry_run:
            return []

        res = self.run_cmd(["log", "--xml", "-l", str(limit)], check=False)
        if res.returncode != 0 or not res.stdout.strip():
            return []

        messages: List[str] = []
        try:
            root = ET.fromstring(res.stdout)
            for entry in root.findall("logentry"):
                msg_elem = entry.find("msg")
                if msg_elem is not None and msg_elem.text:
                    messages.append(msg_elem.text)
                else:
                    messages.append("")
        except Exception as e:
            logger.debug("Failed to parse svn log --xml output: %s", e)
        return messages

    def get_current_branch_name(self) -> str:
        """
        Determine the short name of the branch currently checked out in the SVN working copy.
        Returns 'trunk' if at trunk root, or the sub-directory name under 'branches/' or 'tags/'.
        """
        info = self.get_info()
        rel_url = info.get("Relative URL") or ""
        url = info.get("URL") or ""

        target = rel_url.lstrip("^/") if rel_url else url
        if not target:
            return "trunk"

        # Check standard Subversion directory layouts
        parts = [p for p in target.split("/") if p]
        if "trunk" in parts:
            return "trunk"
        if "branches" in parts:
            idx = parts.index("branches")
            if idx + 1 < len(parts):
                return "/".join(parts[idx + 1 :])
        if "tags" in parts:
            idx = parts.index("tags")
            if idx + 1 < len(parts):
                return f"tags/{'/'.join(parts[idx + 1 :])}"

        # Fallback to the last path segment
        return parts[-1] if parts else "trunk"

    def resolve_branch_url(self, branch_name: str) -> str:
        """
        Convert a branch name or path into a full Subversion target URL or repository-relative URL (^/...).
        Conventions:
        - 'trunk' / 'main' / 'master' -> '^/trunk'
        - 'branches/<name>' or 'tags/<name>' -> '^/<branch_name>'
        - full URL (http://, svn://, ^/...) -> unchanged
        - '<name>' -> '^/branches/<name>'
        """
        name = branch_name.strip()
        if not name:
            raise ValueError("Branch name cannot be empty.")

        if name.startswith(("^/", "http://", "https://", "svn://", "svn+ssh://", "file://")):
            return name

        norm = name.lower()
        if norm in ("trunk", "main", "master"):
            return "^/trunk"

        if name.startswith(("branches/", "tags/")):
            return f"^/{name}"

        return f"^/branches/{name}"

    def switch(self, target_branch: str) -> str:
        """
        Switch the SVN working copy to target_branch.
        Returns the resolved switch target URL.
        """
        if not self.is_clean():
            raise RuntimeError(
                f"Subversion working copy at '{self.workspace_dir}' has uncommitted changes. "
                "Please commit, stash, or revert changes before switching branches ('git2svn clean')."
            )

        resolved_target = self.resolve_branch_url(target_branch)
        logger.info("Switching SVN working copy in %s to %s...", self.workspace_dir, resolved_target)

        if self.dry_run:
            print(f"[DRY-RUN] (in {self.workspace_dir}) {self.svn_bin} switch {resolved_target}")
            return resolved_target

        res = self.run_cmd(["switch", resolved_target], check=False)
        if res.returncode != 0:
            logger.error("Failed to 'svn switch': %s", res.stderr.strip())
            raise parse_svn_error(res.stderr, f"switch to '{resolved_target}'", self.workspace_dir)

        if res.stdout:
            logger.info("SVN switch output:\n%s", res.stdout.strip())

        return resolved_target
