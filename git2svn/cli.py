from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .core import Synchronizer
from .git import GitRepo
from .patcher import Patcher
from .state import load_replay_state
from .svn import SvnWorkspace

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
        help="Path to SVN working copy (default: $SVN_DIR environment variable)",
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

    parser = argparse.ArgumentParser(
        prog="git2svn",
        description=(
            "Consolidated utility to synchronize Git revisions to an SVN workspace.\n\n"
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
        "ref1", help="Commit hash, branch, or start ref (e.g. 'abc1234' or 'main..feature' or 'main')"
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
    parser_replay.add_argument(
        "-u",
        "--update",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run 'svn update' upon successful replay completion to bump working copy to HEAD",
    )
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

    return parser


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    namespace = argparse.Namespace(
        git_dir=None,
        svn_dir=None,
        dry_run=False,
        verbose=False,
        copy=False,
        snapshot=False,
        replay_action=None,
        update=False,
    )
    return parser.parse_args(argv, namespace=namespace)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_cli_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    git_dir = args.git_dir
    if not git_dir and args.command == "replay" and getattr(args, "replay_action", None):
        cwd = Path.cwd()
        state = load_replay_state(cwd)
        if state and "git_dir" in state:
            git_dir = Path(state["git_dir"])

    git_dir = git_dir or find_default_git_dir()
    git_repo = GitRepo(git_dir)
    if not git_repo.is_valid_repo():
        print(f"Error: '{git_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    # 1. SVN workspace directory resolution: CLI arg -> $SVN_DIR -> git config -> replay cwd
    svn_dir = args.svn_dir or (Path(os.environ["SVN_DIR"]) if "SVN_DIR" in os.environ else None)
    if not svn_dir:
        config_svn = git_repo.get_config("git2svn.svnDir")
        if config_svn:
            svn_dir = Path(config_svn)

    # Auto-detect svn_dir from cwd for replay actions if cwd is an SVN checkout
    if not svn_dir and args.command == "replay" and getattr(args, "replay_action", None):
        cwd = Path.cwd()
        if (cwd / ".svn").exists() or load_replay_state(cwd):
            svn_dir = cwd

    if not svn_dir:
        print(
            "Error: SVN workspace directory must be specified via --svn-dir, SVN_DIR env, or 'git config git2svn.svnDir <path>'.",
            file=sys.stderr,
        )
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

    auto_update = getattr(args, "update", False)
    if not auto_update:
        config_update = git_repo.get_config_bool("git2svn.autoUpdate")
        if config_update is not None:
            auto_update = config_update

    svn_workspace = SvnWorkspace(svn_dir, dry_run=dry_run)
    if not svn_workspace.is_valid_workspace() and not dry_run:
        print(f"Error: '{svn_dir}' does not appear to be an SVN working copy (no .svn found).", file=sys.stderr)
        return 1

    patcher = Patcher(svn_dir, dry_run=dry_run)
    sync_mgr = Synchronizer(git_repo, svn_workspace, patcher, dry_run=dry_run, auto_update=auto_update)

    try:
        if args.command == "stage":
            sync_mgr.stage(
                args.ref1,
                args.ref2,
                use_copy=use_copy,
                snapshot=getattr(args, "snapshot", False),
            )
        elif args.command == "replay":
            action = getattr(args, "replay_action", None)
            if action == "continue":
                sync_mgr.replay_continue()
            elif action == "abort":
                sync_mgr.replay_abort()
            elif action == "skip":
                sync_mgr.replay_skip()
            else:
                if not args.ref1:
                    print(
                        "Error: replay requires a commit or range unless using --continue, --abort, or --skip.",
                        file=sys.stderr,
                    )
                    return 1
                sync_mgr.replay(args.ref1, args.ref2)
        else:
            return 1
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
