from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from .colors import TerminalColor
from .eol import detect_file_eol, normalize_file_eol
from .git import FileChange, GitRepo, parse_ref_arguments
from .patcher import Patcher
from .state import (
    ReplayState,
    clean_conflict_artifacts,
    clear_replay_state,
    find_conflict_artifacts,
    load_replay_session,
    save_replay_state,
)
from .svn import SvnError, SvnWorkspace

logger = logging.getLogger("git2svn")


def resolve_sync_range(git_repo: GitRepo, svn_workspace: SvnWorkspace) -> Optional[str]:
    """
    Dynamically resolve default sync range as '<mirror_remote>/<svn_branch>..HEAD'.
    Returns None if mirror remote is missing or remote tracking ref does not exist.
    """
    configured_remote = git_repo.get_config("git2svn.mirrorRemote")
    mirror_remote = configured_remote
    if not mirror_remote:
        remotes = git_repo.get_remotes()
        mirror_remote = "svn-mirror" if "svn-mirror" in remotes else ("origin" if "origin" in remotes else None)

    if mirror_remote:
        svn_branch = svn_workspace.get_current_branch_name()
        remote_ref = f"{mirror_remote}/{svn_branch}"
        if git_repo.ref_exists(remote_ref):
            return f"{remote_ref}..HEAD"

    return None


class Synchronizer:
    """Coordinates Git extraction, patch/copy operations, and SVN staging."""

    def __init__(
        self,
        git_repo: GitRepo,
        svn_workspace: SvnWorkspace,
        patcher: Patcher,
        dry_run: bool = False,
        color_mode: str = "auto",
    ):
        self.git = git_repo
        self.svn = svn_workspace
        self.patcher = patcher
        self.dry_run = dry_run
        self.color = TerminalColor(color_mode)

    def show_identity_banner(self, target_ref_spec: str) -> None:
        """Display identity banner showing active Git and SVN target branches/URLs."""
        branch = self.git.get_current_branch()
        head = self.git.get_head_commit()
        svn_info = self.svn.get_info()

        svn_target = svn_info.get("Relative URL") or svn_info.get("URL") or str(self.svn.workspace_dir.name)
        svn_rev = svn_info.get("Revision")
        svn_suffix = f" (r{svn_rev})" if svn_rev else ""

        target_badge = self.color.target_badge("[TARGET]")
        banner = (
            f"{target_badge} Git source : {self.git.repo_dir.name} [{branch} @ {head}] -> ref: {target_ref_spec}\n"
            f"{target_badge} SVN target : {self.svn.workspace_dir.name} [{svn_target}{svn_suffix}]"
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
            # If a range was supplied (e.g. origin/main..main or ref1 and ref2), snapshot targets the end ref
            is_single, start_or_commit, end_ref = parse_ref_arguments(ref1, ref2)
            target_ref = end_ref if not is_single and end_ref else start_or_commit
            self.stage_snapshot(target_ref)
            return

        if not self.dry_run and self.svn.is_clean():
            self.svn.update()

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
        from .snapshot import SnapshotSynchronizer

        self.show_identity_banner(f"{target_ref} (snapshot)")
        SnapshotSynchronizer(self.git, self.svn, dry_run=self.dry_run).align_workspace(target_ref)

    def diff(self, stat: bool = False) -> str:
        """
        Inspect uncommitted changes in the SVN workspace using svn diff.
        Returns the diff output text (or stat summary if stat=True).
        """
        return self.svn.diff(stat=stat)

    def clean(self) -> None:
        """
        Revert all uncommitted changes, remove untracked conflict files (.rej/.orig),
        remove unversioned files/dirs, and clear SVN locks via svn cleanup.
        """
        logger.info("Cleaning SVN workspace at %s...", self.svn.workspace_dir)
        if self.dry_run:
            print(
                f"[DRY-RUN] Revert uncommitted changes, cleanup locks, and remove conflict artifacts in {self.svn.workspace_dir}"
            )
            return

        # 1. Clear locks
        try:
            self.svn.cleanup()
        except Exception as e:
            logger.warning("svn cleanup reported: %s", e)

        # 2. Revert versioned changes
        self.svn.revert_all()

        # 3. Clean untracked conflict artifacts (.rej / .orig)
        clean_conflict_artifacts(self.svn.workspace_dir)

        # 4. Remove leftover unversioned files/dirs
        unversioned = self.svn.get_unversioned_items()
        for item in unversioned:
            full_p = self.svn.workspace_dir / item
            if full_p.is_dir() and not full_p.is_symlink():
                shutil.rmtree(full_p, ignore_errors=True)
            elif full_p.exists() or full_p.is_symlink():
                full_p.unlink(missing_ok=True)

        # 5. Clear any interrupted replay state file
        clear_replay_state(self.svn.workspace_dir)
        logger.info("SVN workspace cleaned successfully.")

    def purge_workspace(self) -> None:
        """
        Completely delete the local managed SVN working copy directory so it can be re-cloned cleanly.
        """
        logger.info("Purging SVN workspace directory at %s...", self.svn.workspace_dir)
        if self.dry_run:
            print(f"[DRY-RUN] Delete directory {self.svn.workspace_dir}")
            return

        if self.svn.workspace_dir.exists():
            shutil.rmtree(self.svn.workspace_dir, ignore_errors=True)
            logger.info("SVN workspace purged.")

    def switch(self, target_branch: str) -> str:
        """
        Switch the SVN working copy to target_branch (e.g. 'trunk' or 'release-1.0').
        """
        target_url = self.svn.switch(target_branch)
        branch_name = self.svn.get_current_branch_name()
        print(f"Switched SVN working copy to: {branch_name} ({target_url})")
        return target_url

    def _check_branch_alignment(self, force: bool = False, assume_yes: bool = False) -> None:
        """
        Check if the current Git branch matches the active SVN working copy branch.
        Warns or prompts for confirmation if they differ.
        """
        git_branch = self.git.get_current_branch()
        svn_branch = self.svn.get_current_branch_name()

        if not git_branch or git_branch in ("unknown", "HEAD (detached)"):
            return
        if not svn_branch or svn_branch == "unknown":
            return

        # Equivalence normalization: trunk, main, master are treated as equivalent standard roots
        def normalize_name(name: str) -> str:
            lower = name.lower()
            if lower in ("trunk", "main", "master"):
                return "trunk"
            if lower.startswith("branches/"):
                return lower.split("/", 1)[1]
            return lower

        git_norm = normalize_name(git_branch)
        svn_norm = normalize_name(svn_branch)

        if git_norm != svn_norm and not force and not assume_yes:
            warn_badge = self.color.warn_badge("[WARN]  ")
            print(
                f"\n{warn_badge} Branch mismatch detected:\n"
                f"  Git branch : {self.color.bold(git_branch)}\n"
                f"  SVN target : {self.color.bold(svn_branch)}\n\n"
                f"You are about to replay commits from Git '{git_branch}' into SVN '{svn_branch}'.",
                file=sys.stderr,
            )

            # Interactive confirmation prompt
            if sys.stdin.isatty():
                try:
                    resp = input("Do you want to proceed? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted by user.", file=sys.stderr)
                    raise RuntimeError("Replay aborted: branch mismatch.") from None
                if resp not in ("y", "yes"):
                    print("Aborted by user.", file=sys.stderr)
                    raise RuntimeError("Replay aborted: branch mismatch.")
            else:
                logger.warning(
                    "Branch mismatch (Git '%s' vs SVN '%s') detected in non-interactive mode. Proceeding.",
                    git_branch,
                    svn_branch,
                )

    def replay(
        self,
        ref1: str,
        ref2: Optional[str] = None,
        force: bool = False,
        assume_yes: bool = False,
        interactive: bool = False,
    ) -> None:
        """
        Replay a single commit or range of commits onto SVN, committing each with its Git message.
        If force=False, commits already present in recent SVN logs are automatically skipped.
        If interactive=True, steps through each commit with confirmation and diff preview prompts.
        """
        if interactive and not sys.stdin.isatty():
            raise RuntimeError("Interactive replay requires an interactive terminal (stdin is not a tty).")

        is_single, start_or_commit, end_ref = parse_ref_arguments(ref1, ref2)
        target_spec = f"{start_or_commit}..{end_ref}" if not is_single else start_or_commit
        self.show_identity_banner(target_spec)

        if not self.dry_run and not self.svn.is_clean():
            raise RuntimeError(
                "SVN workspace has uncommitted changes. Please commit, stash, or revert them before starting a replay."
            )

        self._check_branch_alignment(force=force, assume_yes=assume_yes)

        if not self.dry_run:
            self.svn.update()

        if is_single:
            commit_hash = start_or_commit
            logger.info("Replaying single commit %s", commit_hash)
            self._execute_replay_queue(
                [commit_hash], total_commits=1, start_index=1, force=force, interactive=interactive
            )
        else:
            start_ref = start_or_commit
            assert end_ref is not None

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
            self._execute_replay_queue(
                commits, total_commits=len(commits), start_index=1, force=force, interactive=interactive
            )

    def replay_continue(self, interactive: bool = False) -> None:
        """Resume an interrupted replay after user resolves conflicts."""
        if interactive and not sys.stdin.isatty():
            raise RuntimeError("Interactive replay requires an interactive terminal (stdin is not a tty).")

        session = load_replay_session(self.svn.workspace_dir)
        if not session:
            raise RuntimeError("No replay in progress. Nothing to continue.")

        current_commit = session.current_commit
        current_msg = session.current_commit_msg
        remaining = session.remaining_commits
        total = session.total_commits
        completed = session.completed_commits

        # 1. Check for leftover .rej / .orig files
        rej_files = find_conflict_artifacts(self.svn.workspace_dir)
        if rej_files:
            rel_rejs = [str(r.relative_to(self.svn.workspace_dir)) for r in rej_files]
            raise RuntimeError(
                f"Found rejected patch artifacts ({', '.join(rel_rejs)}). "
                "Please resolve conflicts and delete .rej / .orig files before running --continue."
            )

        # If paused by user (state == "PAUSED"), current_commit has NOT been staged/applied yet
        if session.state == "PAUSED":
            queue = [current_commit] + remaining
            self._execute_replay_queue(
                queue, total_commits=total, start_index=completed + 1, force=True, interactive=interactive
            )
            return

        # 2. Commit the resolved changes for the interrupted commit
        pad_width = len(str(total))
        first_line = current_msg.splitlines()[0] if current_msg else ""
        progress_prefix = f"[{completed + 1:>{pad_width}}/{total}] Resolving {current_commit[:8]}: {first_line}"

        if not self.svn.is_clean():
            logger.info("Committing resolved commit %s to SVN...", current_commit)
            sys.stdout.write(f"{progress_prefix}... ")
            sys.stdout.flush()
            self.svn.commit(current_msg)
            sys.stdout.write("OK\n")
            sys.stdout.flush()
        else:
            logger.info(
                "No changes in SVN workspace to commit for %s (commit resolved as empty or skipped).", current_commit
            )
            sys.stdout.write(f"{progress_prefix}... SKIPPED (no changes)\n")
            sys.stdout.flush()

        completed += 1
        logger.info("Commit %s (%d/%d) resolved and committed.", current_commit, completed, total)

        # 3. Resume remaining queue
        if remaining:
            self._execute_replay_queue(
                remaining, total_commits=total, start_index=completed + 1, force=True, interactive=interactive
            )
        else:
            clear_replay_state(self.svn.workspace_dir)
            logger.info("Replay completed successfully! All %d commits applied.", total)
            self.svn.update()

    def replay_abort(self) -> None:
        """Abort in-progress replay and revert uncommitted changes."""
        session = load_replay_session(self.svn.workspace_dir)
        if not session:
            raise RuntimeError("No replay in progress. Nothing to abort.")

        current = session.current_commit
        logger.info("Aborting replay at commit %s...", current)
        if not self.dry_run:
            self.svn.revert_all()
            clean_conflict_artifacts(self.svn.workspace_dir)
            clear_replay_state(self.svn.workspace_dir)
        logger.info("Replay aborted. SVN workspace reverted to last clean commit.")

    def replay_skip(self, interactive: bool = False) -> None:
        """Skip current interrupted commit and proceed with remaining queue."""
        if interactive and not sys.stdin.isatty():
            raise RuntimeError("Interactive replay requires an interactive terminal (stdin is not a tty).")

        session = load_replay_session(self.svn.workspace_dir)
        if not session:
            raise RuntimeError("No replay in progress. Nothing to skip.")

        current = session.current_commit
        remaining = session.remaining_commits
        total = session.total_commits
        completed = session.completed_commits

        logger.info("Skipping commit %s...", current)
        if not self.dry_run:
            self.svn.revert_all()
            clean_conflict_artifacts(self.svn.workspace_dir)

        if remaining:
            self._execute_replay_queue(
                remaining, total_commits=total, start_index=completed + 2, force=True, interactive=interactive
            )
        else:
            clear_replay_state(self.svn.workspace_dir)
            logger.info("Replay finished (last commit was skipped).")

    def _show_commit_diff(self, commit_hash: str) -> None:
        """Display diff and commit details."""
        diff_text = self.git.get_commit_show(commit_hash, color=self.color.enabled)
        if diff_text:
            print("\n" + diff_text.rstrip() + "\n")
        else:
            print(f"(No diff available for {commit_hash})")

    def _execute_replay_queue(
        self,
        commits: List[str],
        total_commits: int,
        start_index: int,
        force: bool = False,
        interactive: bool = False,
    ) -> None:
        """Execute a list of commits sequentially, catching conflicts and persisting state."""
        if interactive and not sys.stdin.isatty():
            raise RuntimeError("Interactive replay requires an interactive terminal (stdin is not a tty).")

        queue_start = time.perf_counter()
        pad_width = len(str(total_commits))

        # Check recent SVN commit log messages to detect already committed commits.
        # Find how many commits from the start of our replay queue match consecutive SVN commits.
        skip_count = 0
        if not force and not self.dry_run and commits:
            recent_svn_msgs = [
                m.replace("\r\n", "\n").strip()
                for m in self.svn.get_recent_log_messages(limit=max(25, len(commits) * 2))
                if m.strip()
            ]
            git_msgs = [self.git.get_commit_message(h).replace("\r\n", "\n").strip() for h in commits]

            # In SVN log, messages are newest-first: [newest_svn, ..., oldest_svn].
            # In Git replay queue, commits are chronological: [c0, c1, c2, ...].
            # If c0..cK were replayed to SVN, the most recent SVN commit (recent_svn_msgs[0])
            # is cK, recent_svn_msgs[1] is cK-1, ..., up to recent_svn_msgs[k] is c0.
            # We search for the longest prefix of git_msgs [0..k] that matches
            # reversed recent_svn_msgs ending at the latest SVN commit (recent_svn_msgs[0]).
            for k in range(len(git_msgs), 0, -1):
                candidate_prefix = git_msgs[:k]
                if len(recent_svn_msgs) >= k and recent_svn_msgs[:k] == candidate_prefix[::-1]:
                    skip_count = k
                    break

        for idx, commit_hash in enumerate(commits, start=start_index):
            commit_offset = idx - start_index
            commit_msg = self.git.get_commit_message(commit_hash)
            first_line = commit_msg.splitlines()[0] if commit_msg else ""
            progress_prefix = f"[{idx:>{pad_width}}/{total_commits}] Applying {commit_hash[:8]}: {first_line}"
            logger.info(progress_prefix)

            # Auto-skip if within the consecutive prefix of already committed SVN entries
            if commit_offset < skip_count:
                skip_tag = self.color.skip_badge("[SKIP]")
                skip_notice = (
                    f"{skip_tag} [{idx:>{pad_width}}/{total_commits}] {commit_hash[:8]} '{first_line}' "
                    "already committed to SVN. Skipping.\n"
                )
                if sys.stdout.isatty():
                    sys.stdout.write(f"\r\033[K{skip_notice}")
                else:
                    sys.stdout.write(skip_notice)
                sys.stdout.flush()
                logger.info(
                    "Commit %s '%s' matches consecutive SVN log prefix. Auto-skipped (use --force to override).",
                    commit_hash[:8],
                    first_line,
                )
                continue

            if interactive:
                should_skip = False
                while True:
                    prompt_str = (
                        f"Apply commit {self.color.bold_cyan(commit_hash[:8])} "
                        f'({idx}/{total_commits}): "{first_line}"?\n'
                        f"[{self.color.bold('y')}]es / "
                        f"[{self.color.bold('s')}]kip / "
                        f"[{self.color.bold('a')}]ll / "
                        f"[{self.color.bold('d')}]iff / "
                        f"[{self.color.bold('q')}]uit / "
                        f"[{self.color.bold('?')}] [y]: "
                    )
                    try:
                        choice = input(prompt_str).strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "q"
                        print()

                    if choice in ("", "y", "yes"):
                        break
                    elif choice in ("s", "n", "skip", "no"):
                        should_skip = True
                        break
                    elif choice in ("a", "all"):
                        interactive = False
                        break
                    elif choice in ("d", "diff"):
                        self._show_commit_diff(commit_hash)
                        continue
                    elif choice in ("q", "quit"):
                        remaining = commits[idx - start_index + 1 :]
                        state_data = ReplayState(
                            git_dir=str(self.git.repo_dir),
                            svn_dir=str(self.svn.workspace_dir),
                            current_commit=commit_hash,
                            current_commit_msg=commit_msg,
                            remaining_commits=remaining,
                            total_commits=total_commits,
                            completed_commits=idx - 1,
                            state="PAUSED",
                        )
                        if not self.dry_run:
                            save_replay_state(self.svn.workspace_dir, state_data)
                        paused_badge = self.color.paused_badge("[PAUSED]")
                        print(
                            f"\n{paused_badge} Replay paused at commit {commit_hash[:8]} ({idx}/{total_commits}).\n"
                            f"To resume:\n"
                            f"  git2svn replay --continue\n"
                            f"  (or 'git2svn replay --continue -i' for interactive mode)\n"
                            f"To abort:\n"
                            f"  git2svn replay --abort\n"
                        )
                        return
                    elif choice in ("?", "h", "help"):
                        print(
                            "\nAvailable commands:\n"
                            "  y, yes  - Apply this commit to SVN and proceed\n"
                            "  s, skip - Skip this commit and proceed to the next commit\n"
                            "  a, all  - Apply this and all remaining commits without prompting\n"
                            "  d, diff - Show colorized diffstat and patch for this commit\n"
                            "  q, quit - Quit and pause replay (state is preserved to resume later)\n"
                            "  ?, help - Show this help message\n"
                        )
                        continue
                    else:
                        print(f"Unknown choice '{choice}'. Please enter y, s, a, d, q, or ?.")

                if should_skip:
                    skip_tag = self.color.skip_badge("[SKIP]")
                    skip_msg = (
                        f"{skip_tag} [{idx:>{pad_width}}/{total_commits}] Skipping {commit_hash[:8]}: {first_line}\n"
                    )
                    if sys.stdout.isatty():
                        sys.stdout.write(f"\r\033[K{skip_msg}")
                    else:
                        sys.stdout.write(skip_msg)
                    sys.stdout.flush()
                    continue

            # Interactive console progress output
            if sys.stdout.isatty():
                sys.stdout.write(f"\r\033[K{progress_prefix}... ")
                sys.stdout.flush()
            else:
                sys.stdout.write(f"{progress_prefix}... ")
                sys.stdout.flush()

            commit_start = time.perf_counter()
            try:
                self._patch_and_stage_commit(commit_hash)
                self.svn.commit(commit_msg)
                elapsed = time.perf_counter() - commit_start
                sys.stdout.write(f"OK ({elapsed:.2f}s)\n")
                sys.stdout.flush()
            except SvnError:
                elapsed = time.perf_counter() - commit_start
                sys.stdout.write(f"FAILED (SVN error, {elapsed:.2f}s)\n")
                sys.stdout.flush()
                # SVN operational failure (e.g. working copy locked, out-of-date, collision)
                # Re-raise directly to display actionable SVN resolution hints
                raise
            except Exception:
                elapsed = time.perf_counter() - commit_start
                sys.stdout.write(f"CONFLICT ({elapsed:.2f}s)\n")
                sys.stdout.flush()
                remaining = commits[idx - start_index + 1 :]
                state_data = ReplayState(
                    git_dir=str(self.git.repo_dir),
                    svn_dir=str(self.svn.workspace_dir),
                    current_commit=commit_hash,
                    current_commit_msg=commit_msg,
                    remaining_commits=remaining,
                    total_commits=total_commits,
                    completed_commits=idx - 1,
                    state="CONFLICT_PAUSED",
                )
                if not self.dry_run:
                    save_replay_state(self.svn.workspace_dir, state_data)

                rej_files = find_conflict_artifacts(self.svn.workspace_dir)
                rej_info = ""
                if rej_files:
                    rej_rel = [str(r.relative_to(self.svn.workspace_dir)) for r in rej_files]
                    rej_info = "\nConflicts detected in:\n" + "\n".join(f"  - {f}" for f in rej_rel)

                paused_badge = self.color.paused_badge("[PAUSED]")
                print(
                    f'\n{paused_badge} Conflict while applying commit {commit_hash[:8]} ({idx}/{total_commits}): "{first_line}"'
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
        total_elapsed = time.perf_counter() - queue_start
        logger.info(
            "Replay completed successfully! All %d commits applied in %.2fs.",
            total_commits,
            total_elapsed,
        )
        self.svn.update()

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
        self._sync_executable_properties(commit_hash, changes)

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
        self._sync_executable_properties(end_ref, changes)

    def _sync_executable_properties(self, target_ref: str, changes: List[FileChange]) -> None:
        """Synchronize svn:executable properties for all added/modified/renamed files."""
        for change in changes:
            if not change.is_deleted:
                mode = self.git.get_file_mode(target_ref, change.path)
                if mode == "100755":
                    self.svn.sync_file_executable_property(change.path, is_executable=True)
                elif mode == "100644":
                    self.svn.sync_file_executable_property(change.path, is_executable=False)

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

        self._sync_executable_properties(target_ref, changes)

    def _ensure_parent_dirs_for_changes(self, changes: List[FileChange]) -> None:
        """Create parent directories in SVN workspace for new/renamed files."""
        for change in changes:
            if change.is_added or change.is_renamed or change.is_copied:
                target_file = self.svn.workspace_dir / change.path
                if self.dry_run:
                    print(f"[DRY-RUN] Ensure directory exists: {target_file.parent}")
                else:
                    target_file.parent.mkdir(parents=True, exist_ok=True)

    def status(self) -> int:
        """
        Inspect and display status of Git repo, SVN working copy, in-progress replay, and pending commits.
        Returns:
            0 if clean and ready / in sync
            1 if errors, working copy locked, merge conflicts, or linear violations exist
        """
        from .status import StatusReporter

        return StatusReporter(self.git, self.svn, self.color).report()
