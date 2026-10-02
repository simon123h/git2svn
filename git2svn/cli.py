from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .colors import ColoredLogFormatter, TerminalColor
from .core import Synchronizer, resolve_sync_range
from .git import GitRepo
from .patcher import Patcher
from .state import load_replay_state
from .svn import (
    SvnError,
    SvnLockError,
    SvnOutOfDateError,
    SvnWorkspace,
    checkout_working_copy,
    get_default_managed_svn_dir,
)

logger = logging.getLogger("git2svn")


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
            encoding="utf-8",
            errors="replace",
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
        help="Path to SVN working copy (optional, defaults to git config git2svn.svnDir, $SVN_DIR, or managed working copy)",
    )
    common_parser.add_argument(
        "--svn-url",
        type=str,
        default=argparse.SUPPRESS,
        help="SVN repository URL (optional, defaults to git config git2svn.svnUrl or $SVN_URL)",
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
    common_parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default=argparse.SUPPRESS,
        help="Control colored output (auto, always, never; default: git config git2svn.color or auto)",
    )

    parser = argparse.ArgumentParser(
        prog="git2svn",
        description=(
            "Consolidated utility to synchronize Git revisions to an SVN workspace.\n\n"
            "Recommended Workflow:\n"
            "  1. Bootstrap once:   git2svn setup <SVN_URL_or_PATH>\n"
            "                       (Automatically sets up managed workspace in .git/git2svn/svn_wc,\n"
            "                        detects mirror tracking branches, and creates git aliases)\n"
            "  2. Inspect state:    git2svn status\n"
            "  3. Sync changes:     git2svn stage [ref]   or   git2svn replay [ref]\n"
            "                       (No --svn-dir needed! Everything resolves automatically)\n\n"
            "Core Actions:\n"
            "  stage   NEVER commits. Applies Git changes to the SVN working copy (svn add/rm)\n"
            "          leaving files uncommitted for inspection, manual review, or squashing.\n"
            "  replay  ALWAYS commits. Sequentially ports individual Git commits into SVN history,\n"
            "          preserving original author messages, commit order, and providing stateful\n"
            "          conflict pause/continue lifecycle management."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common_parser],
    )
    from . import get_version

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {get_version()}",
        help="Show program's version number and exit",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands",
        required=True,
    )

    # stage
    parser_stage = subparsers.add_parser(
        "stage",
        parents=[common_parser],
        help="Stage changes in SVN workspace without committing (for review or squash)",
        description=(
            "Stage changes from a Git commit or range in the SVN workspace without committing.\n"
            "Leaves the SVN working copy dirty (uncommitted) so you can inspect diffs,\n"
            "review changes in TortoiseSVN/CLI, or commit manually."
        ),
    )
    parser_stage.add_argument(
        "ref1",
        nargs="?",
        default=None,
        help="Commit hash, branch, or start ref (e.g. 'abc1234' or 'main..feature' or 'main')",
    )
    parser_stage.add_argument("ref2", nargs="?", default=None, help="End ref if range given as two arguments")
    parser_stage.add_argument(
        "--copy",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Brute-force copy modified/added files using Git object DB (bypasses patch; ideal for binaries/conflicts)",
    )
    parser_stage.add_argument(
        "--snapshot",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Mirror the exact tree of ref1 onto SVN without knowing base ref (adds new, removes missing, updates modified)",
    )
    parser_stage.add_argument(
        "--diff",
        "-p",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Immediately show 'svn diff' preview of staged changes after staging",
    )

    # replay
    parser_replay = subparsers.add_parser(
        "replay",
        parents=[common_parser],
        help="Replay commit(s) sequentially onto SVN, committing each with its Git message",
        description=(
            "Replay one or more Git commits sequentially into SVN history.\n"
            "Each commit is applied and committed with its original Git message and metadata.\n"
            "If a patch conflict occurs, replay pauses and persists state for interactive\n"
            "resolution via --continue, --abort, or --skip."
        ),
    )
    parser_replay.add_argument("ref1", nargs="?", default=None, help="Commit hash or start ref")
    parser_replay.add_argument("ref2", nargs="?", default=None, help="End ref if range given as two arguments")
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
    parser_replay.add_argument(
        "--force",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Bypass duplicate commit check (apply commits even if their messages match recent SVN log entries)",
    )
    parser_replay.add_argument(
        "-y",
        "--yes",
        dest="assume_yes",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Automatically confirm prompt if Git branch and SVN working copy branch differ",
    )

    # switch
    parser_switch = subparsers.add_parser(
        "switch",
        parents=[common_parser],
        help="Switch SVN working copy to a different branch (e.g. 'trunk' or 'release-1.0')",
        description=(
            "Switch the Subversion working copy to another branch URL.\n"
            "'trunk', 'main', and 'master' switch to ^/trunk.\n"
            "Any other branch name switches to ^/branches/<name>."
        ),
    )
    parser_switch.add_argument(
        "branch",
        type=str,
        help="Target SVN branch (e.g. 'trunk', 'release-2.0', or 'branches/feature-x')",
    )

    # setup
    parser_setup = subparsers.add_parser(
        "setup",
        parents=[common_parser],
        help="Automate initial repository configuration, tracking branches, and git aliases",
        description=(
            "Configure repository settings for git2svn on a fresh clone.\n"
            "Automatically detects local trunk and remote SVN mirror tracking branches,\n"
            "sets git2svn.svnDir, git2svn.mirrorRemote, pull.ff only,\n"
            "and configures git alias.svn-push, alias.svn-pull, and alias.svn-status."
        ),
    )
    parser_setup.add_argument(
        "setup_target",
        nargs="?",
        type=str,
        default=None,
        metavar="SVN_TARGET",
        help="Path to SVN working copy or SVN repository URL (optional if already configured or set via --svn-dir/--svn-url)",
    )

    # diff
    parser_diff = subparsers.add_parser(
        "diff",
        parents=[common_parser],
        help="Inspect uncommitted changes in the SVN workspace (preview after staging)",
        description="Run svn diff on the SVN workspace to inspect uncommitted changes staged by git2svn.",
    )
    parser_diff.add_argument(
        "--stat",
        action="store_true",
        default=False,
        help="Display a diffstat summary of changed files instead of the full patch",
    )

    # status
    subparsers.add_parser(
        "status",
        parents=[common_parser],
        help="Inspect synchronization health, pending commits, and workspace state",
        description=(
            "Display current state of Git repository, SVN working copy, in-progress replay,\n"
            "and pending commits in the active synchronization range."
        ),
    )

    # clean
    parser_clean = subparsers.add_parser(
        "clean",
        parents=[common_parser],
        help="Revert uncommitted changes, clear SVN locks, and remove conflict artifacts",
        description=(
            "Clean the SVN workspace by running 'svn revert -R', 'svn cleanup', deleting untracked\n"
            "conflict artifacts (.rej / .orig), and removing unversioned files/directories.\n"
            "Optionally pass --purge to completely delete and re-initialize the managed working copy."
        ),
    )
    parser_clean.add_argument(
        "--purge",
        action="store_true",
        default=False,
        help="Completely delete the local managed SVN working copy directory (fresh re-checkout)",
    )

    # doctor
    parser_doctor = subparsers.add_parser(
        "doctor",
        parents=[common_parser],
        help="Run pre-flight diagnostics on Git, SVN, hooks, and repository configuration",
        description=(
            "Inspect system prerequisites, Git/SVN CLI binaries, repository configuration,\n"
            "pre-push hook guard, and SVN working copy connectivity/health.\n"
            "Optionally pass --fix to automatically repair configuration issues, aliases, and locks."
        ),
    )
    parser_doctor.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Automatically repair fixable configuration issues, missing hooks, aliases, and locks",
    )

    # completion
    parser_completion = subparsers.add_parser(
        "completion",
        help="Generate shell tab-completion scripts for bash, zsh, or fish",
        description=(
            "Generate shell tab-completion scripts for git2svn commands and options.\n"
            "Supported shells: bash, zsh, fish.\n\n"
            "Usage Examples:\n"
            '  eval "$(git2svn completion bash)"         # Activate immediately in current bash session\n'
            "  git2svn completion --install               # Automatically install to user completion directory\n"
            "  git2svn completion zsh > ~/.zsh/_git2svn   # Save script to custom zsh completion folder"
        ),
    )
    parser_completion.add_argument(
        "shell",
        nargs="?",
        choices=["bash", "zsh", "fish"],
        default=None,
        help="Target shell (bash, zsh, fish; default: auto-detect from $SHELL)",
    )
    parser_completion.add_argument(
        "--install",
        action="store_true",
        default=False,
        help="Install completion script to standard user completions directory",
    )

    return parser


def handle_setup(git_repo: GitRepo, svn_dir_path: Optional[Path | str]) -> int:
    """Automate repository configuration, branch detection, and productivity aliases."""
    from .setup import run_setup

    return run_setup(git_repo, svn_dir_path)


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    effective_argv = sys.argv[1:] if argv is None else argv
    if not effective_argv:
        parser.print_help(sys.stderr)
        sys.exit(1)

    namespace = argparse.Namespace(
        git_dir=None,
        svn_dir=None,
        svn_url=None,
        dry_run=False,
        verbose=False,
        copy=False,
        snapshot=False,
        diff=False,
        stat=False,
        purge=False,
        fix=False,
        install=False,
        replay_action=None,
    )
    return parser.parse_args(argv, namespace=namespace)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)

    git_dir = args.git_dir
    if not git_dir and args.command == "replay" and getattr(args, "replay_action", None):
        cwd = Path.cwd()
        state = load_replay_state(cwd)
        if state and "git_dir" in state:
            git_dir = Path(state["git_dir"])

    git_dir = git_dir or find_default_git_dir()
    git_repo = GitRepo(git_dir)

    # Color mode: CLI --color -> git config git2svn.color -> "auto"
    color_mode = getattr(args, "color", None)
    if not color_mode:
        config_color = git_repo.get_config("git2svn.color") if git_repo.is_valid_repo() else None
        color_mode = config_color if config_color in ("auto", "always", "never") else "auto"

    log_level = logging.DEBUG if args.verbose else logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(ColoredLogFormatter(TerminalColor(color_mode)))
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [handler]

    if args.command == "completion":
        from .completion import run_completion

        return run_completion(shell=getattr(args, "shell", None), install=getattr(args, "install", False))

    if args.command == "doctor":
        from .doctor import run_doctor

        return run_doctor(
            git_repo,
            svn_dir=getattr(args, "svn_dir", None),
            svn_url=getattr(args, "svn_url", None),
            color=TerminalColor(color_mode),
            fix=getattr(args, "fix", False),
        )

    if not git_repo.is_valid_repo():
        print(f"Error: '{git_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    if args.command == "setup":
        svn_arg = (
            getattr(args, "setup_target", None) or getattr(args, "svn_url", None) or getattr(args, "svn_dir", None)
        )
        return handle_setup(git_repo, svn_arg)

    # 1. SVN workspace directory resolution: CLI arg -> $SVN_DIR -> git config -> replay cwd
    svn_dir = args.svn_dir or (Path(os.environ["SVN_DIR"]) if "SVN_DIR" in os.environ else None)
    if not svn_dir:
        config_svn = git_repo.get_config("git2svn.svnDir")
        if config_svn:
            svn_dir = Path(config_svn)

    # 2. SVN URL resolution: CLI arg -> $SVN_URL -> git config
    svn_url = getattr(args, "svn_url", None) or (os.environ.get("SVN_URL") if "SVN_URL" in os.environ else None)
    if not svn_url:
        svn_url = git_repo.get_config("git2svn.svnUrl")

    # If svn_dir was not explicitly set but svn_url is known, default to managed working copy
    if not svn_dir and svn_url:
        svn_dir = get_default_managed_svn_dir(git_repo.repo_dir)

    # Auto-detect svn_dir from cwd for replay actions if cwd is an SVN checkout
    if not svn_dir and args.command == "replay" and getattr(args, "replay_action", None):
        cwd = Path.cwd()
        if (cwd / ".svn").exists() or load_replay_state(cwd):
            svn_dir = cwd

    if not svn_dir:
        print(
            "Error: SVN workspace directory or repository URL must be specified via --svn-dir/--svn-url, "
            "SVN_DIR/SVN_URL env, or 'git2svn setup <url-or-path>'.",
            file=sys.stderr,
        )
        return 1

    # Check if working copy needs to be checked out from svn_url (self-healing / managed working copy)
    if svn_url and not (svn_dir / ".svn").exists() and not args.dry_run:
        print(f"SVN working copy missing at {svn_dir}. Checking out from {svn_url}...")
        try:
            checkout_working_copy(svn_url, svn_dir)
        except SvnError as e:
            print(f"Error checking out SVN repository:\n  {e.args[0]}", file=sys.stderr)
            return 1

    # Config fallbacks for dry_run and copy
    dry_run = args.dry_run
    if not dry_run:
        config_dry_run = git_repo.get_config_bool("git2svn.dryRun")
        if config_dry_run is not None:
            dry_run = config_dry_run

    use_copy = getattr(args, "copy", False)
    if not use_copy:
        config_copy = git_repo.get_config_bool("git2svn.copy")
        if config_copy is not None:
            use_copy = config_copy

    svn_workspace = SvnWorkspace(svn_dir, dry_run=dry_run)
    if not svn_workspace.is_valid_workspace() and not dry_run:
        print(f"Error: '{svn_dir}' does not appear to be an SVN working copy (no .svn found).", file=sys.stderr)
        return 1

    patcher = Patcher(svn_dir, dry_run=dry_run)
    sync_mgr = Synchronizer(
        git_repo,
        svn_workspace,
        patcher,
        dry_run=dry_run,
        color_mode=color_mode,
    )

    # Fallback to dynamic mirror remote range if ref1 is omitted
    ref1 = getattr(args, "ref1", None)
    ref2 = getattr(args, "ref2", None)
    if not ref1 and args.command in ("stage", "replay"):
        # Replay actions (--continue, --abort, --skip) do not need ref1
        if args.command == "replay" and getattr(args, "replay_action", None):
            pass
        else:
            resolved_range = resolve_sync_range(git_repo, svn_workspace)
            if resolved_range:
                ref1 = resolved_range
                ref2 = None
                logger.info("Using dynamic default range: '%s'", resolved_range)

    try:
        if args.command == "stage":
            is_snapshot = getattr(args, "snapshot", False)
            if not ref1 and is_snapshot:
                ref1 = "HEAD"
                logger.info("No ref specified for snapshot stage; defaulting to HEAD.")

            if not ref1:
                print(
                    "Error: stage requires a commit or range (e.g. 'git2svn stage main..feature') "
                    "or a configured 'git2svn.mirrorRemote'.",
                    file=sys.stderr,
                )
                return 1
            sync_mgr.stage(
                ref1,
                ref2,
                use_copy=use_copy,
                snapshot=is_snapshot,
            )
            managed_dir = get_default_managed_svn_dir(git_repo.repo_dir)
            if svn_dir.resolve() == managed_dir.resolve():
                print(f"Working copy : {svn_dir}")
                print("Tip: Run 'git2svn diff' (or 'git2svn status') to inspect uncommitted changes.")

            if getattr(args, "diff", False):
                diff_output = sync_mgr.diff()
                if diff_output.strip():
                    print(diff_output, end="")
        elif args.command == "diff":
            show_stat = getattr(args, "stat", False)
            diff_output = sync_mgr.diff(stat=show_stat)
            if diff_output.strip():
                print(diff_output, end="")
        elif args.command == "replay":
            action = getattr(args, "replay_action", None)
            if action == "continue":
                sync_mgr.replay_continue()
            elif action == "abort":
                sync_mgr.replay_abort()
            elif action == "skip":
                sync_mgr.replay_skip()
            else:
                if not ref1:
                    print(
                        "Error: replay requires a commit or range unless using --continue, --abort, or --skip. "
                        "Specify a commit/range or configure 'git2svn.mirrorRemote'.",
                        file=sys.stderr,
                    )
                    return 1
                force = getattr(args, "force", False)
                assume_yes = getattr(args, "assume_yes", False)
                sync_mgr.replay(ref1, ref2, force=force, assume_yes=assume_yes)
        elif args.command == "switch":
            sync_mgr.switch(args.branch)
            return 0
        elif args.command == "status":
            return sync_mgr.status()
        elif args.command == "clean":
            if getattr(args, "purge", False):
                sync_mgr.purge_workspace()
            else:
                sync_mgr.clean()
            return 0
        else:
            return 1
    except (SvnLockError, SvnOutOfDateError) as e:
        print(f"\n[SVN Error] {e}", file=sys.stderr)
        return e.returncode
    except SvnError as e:
        print(f"\n[SVN Error] {e}", file=sys.stderr)
        return e.returncode
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
