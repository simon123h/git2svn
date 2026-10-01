from __future__ import annotations

import logging
from typing import List

from .colors import TerminalColor
from .git import GitRepo, parse_ref_arguments
from .state import find_conflict_artifacts, load_replay_state
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
        state = load_replay_state(self.svn.workspace_dir)
        if state:
            has_error = True
            current_commit = state.get("current_commit", "unknown")
            current_msg = state.get("current_commit_msg", "")
            first_msg = current_msg.splitlines()[0] if current_msg else ""
            remaining = state.get("remaining_commits", [])
            completed = state.get("completed_commits", 0)
            total = state.get("total_commits", len(remaining) + 1)

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

        # 4. Synchronization Queue & Default Range
        default_range = self.git.get_config("git2svn.defaultRange")
        print(f"\n{c.bold_cyan('[Synchronization]')}")
        if not default_range:
            print(
                f"  Range     : {c.dim('Not configured')} (run 'git2svn setup' or 'git config git2svn.defaultRange <range>')"
            )
        else:
            print(f"  Range     : {c.bold(default_range)}")
            try:
                is_single, start_ref, end_ref = parse_ref_arguments(default_range)
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
                err_msg = f"Could not parse range '{default_range}': {e}"
                print(f"  Error     : {c.bold_red(err_msg)}")
                has_error = True

        if has_error:
            return 1
        return 0
