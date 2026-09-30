from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys

logger = logging.getLogger("git2svn")


class Patcher:
    """Wrapper to apply diffs using git apply."""

    def __init__(self, target_dir: Path, git_bin: str = "git", dry_run: bool = False):
        self.target_dir = target_dir.resolve()
        self.git_bin = git_bin
        self.dry_run = dry_run

    def apply_diff(self, diff_content: str) -> None:
        """Apply unified diff text to target_dir using git apply."""
        if not diff_content.strip():
            logger.info("Diff is empty. Nothing to patch.")
            return

        cmd = [self.git_bin, "apply", "--ignore-whitespace", "--unsafe-paths", "--reject"]
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
            logger.error("git apply failed with code %d:\nSTDOUT:\n%s\nSTDERR:\n%s",
                         proc.returncode, proc.stdout, proc.stderr)
            print(f"Error: git apply failed (exit code {proc.returncode}).", file=sys.stderr)
            if proc.stdout:
                print(proc.stdout, file=sys.stderr)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            print("Tip: Check for .rej reject files in the SVN workspace or use 'git2svn stage --copy' to copy files directly.", file=sys.stderr)
            raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
        else:
            if proc.stdout or proc.stderr:
                msg = (proc.stdout + "\n" + proc.stderr).strip()
                logger.info("Patch applied successfully:\n%s", msg)
