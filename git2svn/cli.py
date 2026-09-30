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
    update_group = parser_replay.add_mutually_exclusive_group()
    update_group.add_argument(
        "-u",
        "--update",
        dest="update",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run 'svn update' upon successful replay completion (enabled by default)",
    )
    update_group.add_argument(
        "--no-update",
        dest="update",
        action="store_false",
        default=argparse.SUPPRESS,
        help="Disable automatic 'svn update' upon successful replay completion",
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

    # setup
    parser_setup = subparsers.add_parser(
        "setup",
        parents=[common_parser],
        help="Automate initial repository configuration, tracking branches, and git aliases",
        description=(
            "Configure repository settings for git2svn on a fresh clone.\n"
            "Automatically detects local trunk and remote SVN mirror tracking branches,\n"
            "sets git2svn.svnDir, git2svn.defaultRange, git2svn.autoUpdate, pull.ff only,\n"
            "and configures git alias.svn-push and alias.svn-pull."
        ),
    )
    parser_setup.add_argument(
        "setup_svn_dir",
        nargs="?",
        type=Path,
        default=None,
        metavar="SVN_DIR",
        help="Path to SVN working copy (optional if already configured or set via --svn-dir)",
    )

    return parser


def handle_setup(git_repo: GitRepo, svn_dir_path: Optional[Path | str]) -> int:
    """Automate repository configuration, branch detection, and productivity aliases."""
    if not git_repo.is_valid_repo():
        print(f"Error: '{git_repo.root_dir}' is not a valid Git repository.", file=sys.stderr)
        return 1

    # 1. Resolve SVN working copy path
    resolved_svn: Optional[Path] = None
    if svn_dir_path:
        resolved_svn = Path(svn_dir_path).resolve()
    else:
        # Check existing config or environment variable
        cfg_svn = git_repo.get_config("git2svn.svnDir")
        if cfg_svn:
            resolved_svn = Path(cfg_svn).resolve()
        elif "SVN_DIR" in os.environ:
            resolved_svn = Path(os.environ["SVN_DIR"]).resolve()

    if not resolved_svn:
        # Interactive prompt if stdin is a tty, otherwise display error
        if sys.stdin.isatty():
            try:
                entered = input("Path to SVN working copy: ").strip()
                if entered:
                    resolved_svn = Path(entered).resolve()
            except (EOFError, KeyboardInterrupt):
                print("", file=sys.stderr)
                return 1

    if not resolved_svn:
        print(
            "Error: SVN working copy path must be provided: 'git2svn setup <path/to/svn>'.",
            file=sys.stderr,
        )
        return 1

    if not (resolved_svn / ".svn").exists():
        print(
            f"Error: '{resolved_svn}' does not appear to be an SVN working copy (no .svn found).",
            file=sys.stderr,
        )
        return 1

    # 2. Auto-detect local trunk branch
    local_branches = git_repo.get_local_branches()
    detected_trunk: Optional[str] = None
    if "trunk" in local_branches:
        detected_trunk = "trunk"
    elif "main" in local_branches:
        detected_trunk = "main"
    elif "master" in local_branches:
        detected_trunk = "master"
    else:
        # Use current branch if available
        current = git_repo.get_current_branch()
        if current and current in local_branches:
            detected_trunk = current
        elif local_branches:
            detected_trunk = local_branches[0]

    # 3. Auto-detect remote tracking mirror branch
    remote_branches = git_repo.get_remote_branches()
    detected_mirror: Optional[str] = None
    preferred_remotes = ["svn-mirror/trunk", "origin/trunk", "svn/trunk", "mirror/trunk"]
    for pref in preferred_remotes:
        if pref in remote_branches:
            detected_mirror = pref
            break

    if not detected_mirror and detected_trunk:
        # Check for remote branch matching <remote>/<detected_trunk>
        candidates = [b for b in remote_branches if b.endswith(f"/{detected_trunk}")]
        if candidates:
            # Prefer svn-mirror if present
            svn_cand = [c for c in candidates if "svn" in c.lower() or "mirror" in c.lower()]
            detected_mirror = svn_cand[0] if svn_cand else candidates[0]

    # 4. Apply Git configuration
    # SVN directory path (use forward slashes for cross-platform consistency in git config)
    svn_dir_str = str(resolved_svn).replace("\\", "/")
    git_repo.set_config("git2svn.svnDir", svn_dir_str)
    git_repo.set_config("pull.ff", "only")

    default_range: Optional[str] = None
    if detected_mirror and detected_trunk:
        default_range = f"{detected_mirror}..{detected_trunk}"
        git_repo.set_config("git2svn.defaultRange", default_range)

    # 5. Configure productivity aliases
    mirror_remote = detected_mirror.split("/")[0] if detected_mirror else "svn-mirror"
    trunk_name = detected_trunk or "trunk"

    push_script = (
        f"!f() {{ git2svn replay && run-svn2git-sync && git fetch {mirror_remote} && "
        f"git checkout {trunk_name} && git reset --hard {detected_mirror or f'{mirror_remote}/{trunk_name}'}; }}; f"
    )
    pull_script = (
        f"!f() {{ run-svn2git-sync && git fetch {mirror_remote} && "
        f"git checkout {trunk_name} && git reset --hard {detected_mirror or f'{mirror_remote}/{trunk_name}'}; }}; f"
    )

    git_repo.set_config("alias.svn-push", push_script)
    git_repo.set_config("alias.svn-pull", pull_script)

    print("Successfully configured git2svn:")
    print(f"  git2svn.svnDir      = {svn_dir_str}")
    if default_range:
        print(f"  git2svn.defaultRange= {default_range}")
    else:
        print("  git2svn.defaultRange= (not set; could not detect mirror branch)")
    print("  pull.ff             = only")
    print(f"  alias.svn-push      = {push_script}")
    print(f"  alias.svn-pull      = {pull_script}")
    return 0


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    effective_argv = sys.argv[1:] if argv is None else argv
    if not effective_argv:
        parser.print_help(sys.stderr)
        sys.exit(1)

    namespace = argparse.Namespace(
        git_dir=None,
        svn_dir=None,
        dry_run=False,
        verbose=False,
        copy=False,
        snapshot=False,
        replay_action=None,
        update=None,
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

    if args.command == "setup":
        svn_arg = getattr(args, "setup_svn_dir", None) or getattr(args, "svn_dir", None)
        return handle_setup(git_repo, svn_arg)

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

    # auto_update defaults to True unless CLI --no-update or git config git2svn.autoUpdate false
    auto_update = getattr(args, "update", None)
    if auto_update is None:
        config_update = git_repo.get_config_bool("git2svn.autoUpdate")
        if config_update is not None:
            auto_update = config_update
        else:
            auto_update = True

    svn_workspace = SvnWorkspace(svn_dir, dry_run=dry_run)
    if not svn_workspace.is_valid_workspace() and not dry_run:
        print(f"Error: '{svn_dir}' does not appear to be an SVN working copy (no .svn found).", file=sys.stderr)
        return 1

    patcher = Patcher(svn_dir, dry_run=dry_run)
    sync_mgr = Synchronizer(git_repo, svn_workspace, patcher, dry_run=dry_run, auto_update=auto_update)

    # Fallback to git2svn.defaultRange if ref1 is omitted
    ref1 = args.ref1
    ref2 = getattr(args, "ref2", None)
    if not ref1 and args.command in ("stage", "replay"):
        # Replay actions (--continue, --abort, --skip) do not need ref1
        if args.command == "replay" and getattr(args, "replay_action", None):
            pass
        else:
            default_range = git_repo.get_config("git2svn.defaultRange")
            if default_range:
                ref1 = default_range
                ref2 = None
                logger.info("Using configured default range from git2svn.defaultRange: '%s'", default_range)

    try:
        if args.command == "stage":
            if not ref1:
                print(
                    "Error: stage requires a commit or range (e.g. 'git2svn stage main..feature') "
                    "or 'git config git2svn.defaultRange <range>'.",
                    file=sys.stderr,
                )
                return 1
            sync_mgr.stage(
                ref1,
                ref2,
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
                if not ref1:
                    print(
                        "Error: replay requires a commit or range unless using --continue, --abort, or --skip. "
                        "You can also configure a default range with 'git config git2svn.defaultRange <range>'.",
                        file=sys.stderr,
                    )
                    return 1
                sync_mgr.replay(ref1, ref2)
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
