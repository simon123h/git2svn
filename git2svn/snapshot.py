from __future__ import annotations

import logging
import os
import shutil
from typing import Tuple

from .eol import detect_file_eol, normalize_file_eol
from .git import GitRepo
from .svn import SvnWorkspace

logger = logging.getLogger("git2svn")


class SnapshotSynchronizer:
    """
    Synchronizes the SVN workspace to mirror a Git tree snapshot exactly.
    Handles deletions, additions, symlinks, and newline-normalized modifications.
    """

    def __init__(self, git: GitRepo, svn: SvnWorkspace, dry_run: bool = False):
        self.git = git
        self.svn = svn
        self.dry_run = dry_run

    def align_workspace(self, target_ref: str) -> Tuple[int, int, int]:
        """
        Mirror the exact tree state of target_ref onto the SVN workspace without committing.
        Returns:
            Tuple of (added_count, deleted_count, modified_count)
        """
        logger.info("Starting snapshot synchronization to Git ref '%s'...", target_ref)

        git_files_list = self.git.get_tree_files(target_ref)
        git_files_set = set(git_files_list)
        svn_files_list = self.svn.get_versioned_files()
        svn_files_set = set(svn_files_list)

        deleted_files = sorted(svn_files_set - git_files_set)
        added_files = sorted(git_files_set - svn_files_set)
        common_files = sorted(git_files_set & svn_files_set)

        logger.info(
            "Snapshot delta: %d added, %d deleted, %d existing files to compare",
            len(added_files),
            len(deleted_files),
            len(common_files),
        )

        # 1. Handle deleted files: remove from SVN and disk
        for rel_path in deleted_files:
            logger.info("SVN staging snapshot delete: %s", rel_path)
            self.svn.stage_rm(rel_path)
            full_path = self.svn.workspace_dir / rel_path
            if not self.dry_run and full_path.exists():
                if full_path.is_dir():
                    shutil.rmtree(full_path)
                else:
                    full_path.unlink()

        # 2. Handle modified files: compare content and write if changed
        modified_count = 0
        for rel_path in common_files:
            dst_path = self.svn.workspace_dir / rel_path
            mode = self.git.get_file_mode(target_ref, rel_path)
            content = self.git.get_file_content_bytes(target_ref, rel_path)

            if mode == "120000":
                # Symlink
                link_target = content.decode("utf-8", errors="replace").strip()
                needs_update = True
                if dst_path.is_symlink() and os.readlink(dst_path) == link_target:
                    needs_update = False

                if needs_update:
                    modified_count += 1
                    if self.dry_run:
                        print(f"[DRY-RUN] Update symlink {rel_path} -> {link_target}")
                    else:
                        dst_path.unlink(missing_ok=True)
                        os.symlink(link_target, dst_path)
            else:
                # Regular file: check existing newline style & compare bytes
                orig_eol = detect_file_eol(dst_path) if dst_path.exists() else None
                target_bytes = content
                if orig_eol:
                    unified = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                    if orig_eol == b"\r\n":
                        target_bytes = unified.replace(b"\n", b"\r\n")
                    else:
                        target_bytes = unified

                current_bytes = dst_path.read_bytes() if dst_path.exists() and dst_path.is_file() else None
                if current_bytes != target_bytes:
                    modified_count += 1
                    if self.dry_run:
                        print(f"[DRY-RUN] Update file content: {rel_path}")
                    else:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        if dst_path.exists() or dst_path.is_symlink():
                            dst_path.unlink()
                        dst_path.write_bytes(target_bytes)

        # 3. Handle added files: extract from Git and run svn add
        for rel_path in added_files:
            logger.info("SVN staging snapshot add: %s", rel_path)
            dst_path = self.svn.workspace_dir / rel_path
            mode = self.git.get_file_mode(target_ref, rel_path)
            content = self.git.get_file_content_bytes(target_ref, rel_path)

            if self.dry_run:
                print(f"[DRY-RUN] Extract new file {rel_path} and stage add")
            else:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                if dst_path.exists() or dst_path.is_symlink():
                    dst_path.unlink()

                if mode == "120000":
                    link_target = content.decode("utf-8", errors="replace").strip()
                    os.symlink(link_target, dst_path)
                else:
                    dst_path.write_bytes(content)
                    normalize_file_eol(dst_path)

                self.svn.stage_add(rel_path)

        logger.info(
            "Snapshot staging completed: %d added, %d deleted, %d modified (uncommitted).",
            len(added_files),
            len(deleted_files),
            modified_count,
        )
        return len(added_files), len(deleted_files), modified_count
