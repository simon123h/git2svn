from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Union

logger = logging.getLogger("git2svn")


class Patcher:
    """Wrapper to apply diffs using git apply."""

    def __init__(self, target_dir: Path, git_bin: str = "git", dry_run: bool = False):
        self.target_dir = target_dir.resolve()
        self.git_bin = git_bin
        self.dry_run = dry_run

    def apply_diff(self, diff_content: Union[str, bytes]) -> None:
        """Apply unified diff text or bytes to target_dir using git apply."""
        if isinstance(diff_content, str):
            diff_bytes = diff_content.encode("utf-8")
        else:
            diff_bytes = diff_content

        if not diff_bytes.strip():
            logger.info("Diff is empty. Nothing to patch.")
            return

        cmd = [self.git_bin, "apply", "--ignore-whitespace", "--unsafe-paths", "--reject"]
        if self.dry_run:
            preview = diff_bytes.strip()[:200].decode("utf-8", errors="replace")
            print(f"[DRY-RUN] (in {self.target_dir}) {' '.join(cmd)} << EOF\n{preview}...\nEOF")
            return

        logger.debug("Applying patch to %s using %s", self.target_dir, " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=self.target_dir,
            input=diff_bytes,
            capture_output=True,
        )

        stdout_str = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        stderr_str = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

        if proc.returncode != 0:
            logger.error(
                "git apply failed with code %d:\nSTDOUT:\n%s\nSTDERR:\n%s", proc.returncode, stdout_str, stderr_str
            )
            print(f"Error: git apply failed (exit code {proc.returncode}).", file=sys.stderr)
            if stdout_str:
                print(stdout_str, file=sys.stderr)
            if stderr_str:
                print(stderr_str, file=sys.stderr)
            print(
                "Tip: Check for .rej reject files in the SVN workspace or use 'git2svn stage --copy' to copy files directly.",
                file=sys.stderr,
            )
            raise subprocess.CalledProcessError(proc.returncode, cmd, stdout_str, stderr_str)
        else:
            if stdout_str or stderr_str:
                msg = (stdout_str + "\n" + stderr_str).strip()
                logger.info("Patch applied successfully:\n%s", msg)
