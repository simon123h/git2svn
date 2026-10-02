from __future__ import annotations

import logging
from typing import List

from .colors import TerminalColor
from .core import resolve_sync_range
from .git import GitRepo, parse_ref_arguments
from .state import find_conflict_artifacts, load_replay_session
from .svn import SvnError, SvnWorkspace

logger = logging.getLogger("git2svn")


class StatusReporter:
    """Inspects and displays status of Git repo, SVN working copy, replay state, and sync queue."""

    def __init__(
        self,
        git_repo: GitRepo,
        svn_workspace: SvnWorkspace,
        color: TerminalColor,
    ):
        self.git = git_repo
        self.svn = svn_workspace
        self.color = color

    def report(self) -> int:
        """
        Inspect and display status of Git repo, SVN working copy, in-progress replay, and pending commits.
        Returns:
            0 if clean and ready / in sync
            1 if errors, working copy locked, merge conflicts, or linear violations exist
        """
        has_error = False
        c = self.color

        # 1. Check in-progress replay state
        session = load_replay_session(self.svn.workspace_dir)
        if session:
            has_error = True
            current_commit = session.current_commit or "unknown"
            current_msg = session.current_commit_msg
            first_msg = current_msg.splitlines()[0] if current_msg else ""
            remaining = session.remaining_commits
            completed = session.completed_commits
            total = session.total_commits

            print(c.bold_red("[Replay In Progress]"))
            print(
                f'  State     : {c.bold_red("PAUSED")} (conflict at commit {c.yellow(current_commit[:8])} "{first_msg}")'
            )
            print(f"  Progress  : {completed} of {total} commits applied ({len(remaining)} remaining)")

            rej_files = find_conflict_artifacts(self.svn.workspace_dir)
            if rej_files:
                rel_rejs = [str(r.relative_to(self.svn.workspace_dir)) for r in rej_files]
                print(f"  Conflicts : {c.bold_red(', '.join(rel_rejs))}")
            action_text = (
                f"Resolve conflicts and run {c.bold_cyan('git2svn replay --continue')} (or '--abort' / '--skip')"
            )
            print(f"  Action    : {action_text}\n")

        # 2. Git Workspace Status
        git_clean = self.git.is_clean()
        branch = self.git.get_current_branch()
        head_commit = self.git.get_head_commit()
        head_subject = self.git.get_head_subject()
        head_desc = f'{c.yellow(head_commit)} "{head_subject}"' if head_subject else c.yellow(head_commit)

        print(c.bold_cyan("[Git Workspace]"))
        print(f"  Repository: {self.git.repo_dir}")
        print(f"  Branch    : {c.bold(branch)} (at {head_desc})")
        if git_clean:
            print(f"  Tree      : {c.bold_green('Clean')}")
        else:
            print(f"  Tree      : {c.bold_yellow('Dirty')} (uncommitted changes present)")

        # 3. SVN Working Copy Status
        print(f"\n{c.bold_cyan('[SVN Working Copy]')}")
        print(f"  Path      : {self.svn.workspace_dir}")
        svn_info = self.svn.get_info()
        svn_url = svn_info.get("URL") or svn_info.get("Relative URL") or "unknown"
        svn_rev = svn_info.get("Revision")
        rev_str = f" ({c.dim(f'r{svn_rev}')})" if svn_rev else ""
        print(f"  Target    : {svn_url}{rev_str}")

        try:
            svn_clean = self.svn.is_clean()
            if svn_clean:
                print(f"  Tree      : {c.bold_green('Clean')} (no uncommitted changes, unlocked)")
            else:
                uncommitted = self.svn.get_status_summary()
                print(f"  Tree      : {c.bold_yellow(f'Dirty ({len(uncommitted)} uncommitted changes)')}")
                for item in uncommitted[:5]:
                    print(f"              {item}")
                if len(uncommitted) > 5:
                    print(f"              {c.dim(f'... and {len(uncommitted) - 5} more')}")
        except SvnError as e:
            has_error = True
            print(f"  Tree      : {c.bold_red(f'Error: {e.args[0].splitlines()[0]}')}")

        # 4. Synchronization Queue
        sync_range = resolve_sync_range(self.git, self.svn)
        print(f"\n{c.bold_cyan('[Synchronization]')}")
        if not sync_range:
            configured_remote = self.git.get_config("git2svn.mirrorRemote")
            remotes = self.git.get_remotes()
            detected_remote = (
                configured_remote
                if configured_remote
                else ("svn-mirror" if "svn-mirror" in remotes else ("origin" if "origin" in remotes else None))
            )
            if not detected_remote:
                print(
                    f"  Range     : {c.dim('Not configured')} (set via 'git config git2svn.mirrorRemote <remote>' or run 'git2svn setup')"
                )
            else:
                svn_branch = self.svn.get_current_branch_name()
                current_git = self.git.get_current_branch()
                cand = [f"{detected_remote}/{svn_branch}"]
                if svn_branch == "trunk":
                    cand.extend([f"{detected_remote}/main", f"{detected_remote}/master"])
                if current_git and current_git != "HEAD" and f"{detected_remote}/{current_git}" not in cand:
                    cand.append(f"{detected_remote}/{current_git}")
                cand_str = ", ".join(f"'{ref}'" for ref in cand)
                print(f"  Range     : {c.dim('Unresolved')} (tracking branch not found)")
                print(
                    f"  Hint      : Remote '{detected_remote}' configured, but no tracking branch ({cand_str}) found in Git."
                )
                print(f"              Run 'git fetch {detected_remote}' to download remote tracking branches.")
        else:
            print(f"  Range     : {c.bold(sync_range)}")
            try:
                is_single, start_ref, end_ref = parse_ref_arguments(sync_range)
                if is_single:
                    commits = [start_ref] if self.git.ref_exists(start_ref) else []
                    merges: List[str] = []
                else:
                    assert end_ref is not None
                    if not self.git.ref_exists(start_ref):
                        warn_msg = f"Range start ref '{start_ref}' not found in Git."
                        print(f"  Warning   : {c.bold_yellow(warn_msg)}")
                        commits = []
                        merges = []
                    elif not self.git.ref_exists(end_ref):
                        warn_msg = f"Range end ref '{end_ref}' not found in Git."
                        print(f"  Warning   : {c.bold_yellow(warn_msg)}")
                        commits = []
                        merges = []
                    else:
                        merges = self.git.get_merge_commits(start_ref, end_ref)
                        commits = self.git.get_commit_range(start_ref, end_ref)

                if merges:
                    has_error = True
                    inv_msg = f"INVALID ({len(merges)} merge commits found in range - linear rebase required)"
                    print(f"  Linearity : {c.bold_red(inv_msg)}")
                else:
                    print(f"  Linearity : {c.bold_green('OK')} (strictly linear)")

                if not commits:
                    print(f"  Pending   : {c.bold_green('In sync')} (0 commits to replay)")
                else:
                    pending_title = f"{len(commits)} commit(s) ready to replay:"
                    print(f"  Pending   : {c.bold_yellow(pending_title)}")
                    for idx, c_hash in enumerate(commits[:10], start=1):
                        msg = self.git.get_commit_message(c_hash)
                        subject = msg.splitlines()[0] if msg else ""
                        print(f"              {c.dim(f'{idx}.')} [{c.yellow(c_hash[:8])}] {subject}")
                    if len(commits) > 10:
                        print(f"              {c.dim(f'... and {len(commits) - 10} more')}")
            except Exception as e:
                err_msg = f"Could not parse range '{sync_range}': {e}"
                print(f"  Error     : {c.bold_red(err_msg)}")
                has_error = True

        if has_error:
            return 1
        return 0
