from __future__ import annotations

import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Dict, List, Optional

from .eol import detect_file_eol, normalize_file_eol
from .git import FileChange, GitRepo, parse_ref_arguments
from .patcher import Patcher
from .state import (
    clean_conflict_artifacts,
    clear_replay_state,
    find_conflict_artifacts,
    load_replay_state,
    save_replay_state,
)
from .svn import SvnWorkspace

logger = logging.getLogger("git2svn")


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

    def show_identity_banner(self, target_ref_spec: str) -> None:
        """Display identity banner showing active Git and SVN target branches/URLs."""
        branch = self.git.get_current_branch()
        head = self.git.get_head_commit()
        svn_info = self.svn.get_info()

        svn_target = svn_info.get("Relative URL") or svn_info.get("URL") or str(self.svn.workspace_dir.name)
        svn_rev = svn_info.get("Revision")
        svn_suffix = f" (r{svn_rev})" if svn_rev else ""

        banner = (
            f"[TARGET] Git source : {self.git.repo_dir.name} [{branch} @ {head}] -> ref: {target_ref_spec}\n"
            f"[TARGET] SVN target : {self.svn.workspace_dir.name} [{svn_target}{svn_suffix}]"
        )
        print(banner)

    def stage(
        self,
        ref1: str,
        ref2: Optional[str] = None,
        use_copy: bool = False,
        snapshot: bool = False,
    ) -> None:
        """
        Stage changes from a commit or range in SVN workspace without committing.
        If snapshot=True, aligns the SVN workspace to match ref1 exactly (bypassing history).
        If use_copy=True, brute-force copies files using Git object DB (bypassing patch).
        """
        if snapshot:
            self.stage_snapshot(ref1)
            return

        is_single, start_or_commit, end_ref = parse_ref_arguments(ref1, ref2)
        target_spec = f"{start_or_commit}..{end_ref}" if not is_single else start_or_commit
        self.show_identity_banner(target_spec)

        if is_single:
            commit_hash = start_or_commit
            logger.info("Staging single commit %s (copy_mode=%s)", commit_hash, use_copy)
            if use_copy:
                parent = self.git.get_commit_parent(commit_hash)
                base_ref = parent if parent else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
                self._copy_and_stage_range(base_ref, commit_hash)
            else:
                self._patch_and_stage_commit(commit_hash)
        else:
            start_ref = start_or_commit
            assert end_ref is not None
            logger.info("Staging range %s..%s (copy_mode=%s)", start_ref, end_ref, use_copy)
            if use_copy:
                self._copy_and_stage_range(start_ref, end_ref)
            else:
                self._patch_and_stage_range(start_ref, end_ref)
        logger.info("Changes staged successfully in SVN workspace (uncommitted).")

    def stage_snapshot(self, target_ref: str) -> None:
        """
        Mirror the exact tree state of target_ref onto the SVN workspace without committing.
        Detects added, deleted, and modified files by comparing the Git tree against SVN files.
        """
        self.show_identity_banner(f"{target_ref} (snapshot)")
        logger.info("Starting snapshot synchronization to Git ref '%s'...", target_ref)

        git_files_list = self.git.get_tree_files(target_ref)
        git_files_set = set(git_files_list)
        svn_files_list = self.svn.get_versioned_files()
        svn_files_set = set(svn_files_list)

        deleted_files = sorted(svn_files_set - git_files_set)
        added_files = sorted(git_files_set - svn_files_set)
        common_files = sorted(git_files_set & svn_files_set)

        logger.info("Snapshot delta: %d added, %d deleted, %d existing files to compare",
                    len(added_files), len(deleted_files), len(common_files))

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
                # Normalize new content in memory to match orig_eol before comparing
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

    def replay(self, ref1: str, ref2: Optional[str] = None) -> None:
        """
        Replay a single commit or range of commits onto SVN, committing each with its Git message.
        """
        is_single, start_or_commit, end_ref = parse_ref_arguments(ref1, ref2)
        target_spec = f"{start_or_commit}..{end_ref}" if not is_single else start_or_commit
        self.show_identity_banner(target_spec)

        if is_single:
            commit_hash = start_or_commit
            logger.info("Replaying single commit %s", commit_hash)
            self._execute_replay_queue([commit_hash], total_commits=1, start_index=1)
        else:
            start_ref = start_or_commit
            assert end_ref is not None

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
                self._patch_and_stage_commit(commit_hash)
                self.svn.commit(commit_msg)
            except Exception as e:
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

    def _patch_and_stage_commit(self, commit_hash: str) -> None:
        """Extract diff for a single commit, apply using git apply, and stage in SVN."""
        parent = self.git.get_commit_parent(commit_hash)
        if parent:
            ref_spec = f"{parent}..{commit_hash}"
            diff_text = self.git.get_diff(ref_spec)
            changes = self.git.get_name_status(ref_spec)
        else:
            diff_text = self.git.get_diff_root(commit_hash)
            changes = self.git.get_name_status_root(commit_hash)

        target_eols: Dict[Path, Optional[bytes]] = {}
        for change in changes:
            if not change.is_deleted:
                target_file = self.svn.workspace_dir / change.path
                target_eols[change.path] = detect_file_eol(target_file)

        self._ensure_parent_dirs_for_changes(changes)
        self.patcher.apply_diff(diff_text)

        if not self.dry_run:
            for change in changes:
                if not change.is_deleted:
                    target_file = self.svn.workspace_dir / change.path
                    orig_eol = target_eols.get(change.path)
                    normalize_file_eol(target_file, target_eol=orig_eol)

        self.svn.apply_structural_changes(changes)

    def _patch_and_stage_range(self, start_ref: str, end_ref: str) -> None:
        """Extract diff between two references, apply using git apply, and stage in SVN."""
        ref_spec = f"{start_ref}..{end_ref}"
        diff_text = self.git.get_diff(ref_spec)
        changes = self.git.get_name_status(ref_spec)

        target_eols: Dict[Path, Optional[bytes]] = {}
        for change in changes:
            if not change.is_deleted:
                target_file = self.svn.workspace_dir / change.path
                target_eols[change.path] = detect_file_eol(target_file)

        self._ensure_parent_dirs_for_changes(changes)
        self.patcher.apply_diff(diff_text)

        if not self.dry_run:
            for change in changes:
                if not change.is_deleted:
                    target_file = self.svn.workspace_dir / change.path
                    orig_eol = target_eols.get(change.path)
                    normalize_file_eol(target_file, target_eol=orig_eol)

        self.svn.apply_structural_changes(changes)

    def _copy_and_stage_range(self, base_ref: str, target_ref: str) -> None:
        """Brute-force copy changed files using Git object DB, and stage in SVN."""
        ref_spec = f"{base_ref}..{target_ref}"
        changes = self.git.get_name_status(ref_spec)

        target_eols: Dict[Path, Optional[bytes]] = {}
        for change in changes:
            if not change.is_deleted:
                target_file = self.svn.workspace_dir / change.path
                target_eols[change.path] = detect_file_eol(target_file)

        # 1. Handle deleted files in SVN first
        for change in changes:
            if change.is_deleted:
                self.svn.stage_rm(change.path)
                target_file = self.svn.workspace_dir / change.path
                if not self.dry_run and target_file.exists():
                    if target_file.is_dir():
                        shutil.rmtree(target_file)
                    else:
                        target_file.unlink()
            elif change.is_renamed:
                assert change.old_path is not None
                self.svn.stage_rm(change.old_path)
                target_old = self.svn.workspace_dir / change.old_path
                if not self.dry_run and target_old.exists():
                    target_old.unlink()

        # 2. Extract added, modified, renamed, and copied files directly from Git object database
        for change in changes:
            if change.is_added or change.is_modified or change.is_renamed or change.is_copied:
                dst_path = self.svn.workspace_dir / change.path

                if self.dry_run:
                    print(f"[DRY-RUN] Extract {target_ref}:{change.path.as_posix()} -> {dst_path}")
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    if dst_path.exists() or dst_path.is_symlink():
                        dst_path.unlink()

                    mode = self.git.get_file_mode(target_ref, change.path)
                    content = self.git.get_file_content_bytes(target_ref, change.path)

                    if mode == "120000":
                        link_target = content.decode("utf-8", errors="replace").strip()
                        os.symlink(link_target, dst_path)
                    else:
                        dst_path.write_bytes(content)
                        orig_eol = target_eols.get(change.path)
                        if orig_eol:
                            normalize_file_eol(dst_path, target_eol=orig_eol)

        # 3. Stage added, renamed, and copied files in SVN
        for change in changes:
            if change.is_added or change.is_renamed or change.is_copied:
                self.svn.stage_add(change.path)

    def _ensure_parent_dirs_for_changes(self, changes: List[FileChange]) -> None:
        """Create parent directories in SVN workspace for new/renamed files."""
        for change in changes:
            if change.is_added or change.is_renamed or change.is_copied:
                target_file = self.svn.workspace_dir / change.path
                if self.dry_run:
                    print(f"[DRY-RUN] Ensure directory exists: {target_file.parent}")
                else:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
