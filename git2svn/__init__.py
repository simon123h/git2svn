"""
git2svn: A consolidated CLI utility to synchronize changes from a local Git repository
to a local Subversion (SVN) working copy.
"""

from __future__ import annotations

from .cli import build_parser, main, parse_cli_args
from .colors import ColoredLogFormatter, TerminalColor
from .completion import get_completion_script, install_completion, run_completion
from .core import Synchronizer
from .doctor import CheckResult, Doctor, FixResult, run_doctor
from .eol import detect_file_eol, normalize_file_eol
from .git import FileChange, GitRepo, parse_name_status, parse_name_status_z, parse_ref_arguments
from .patcher import Patcher
from .setup import run_setup
from .snapshot import SnapshotSynchronizer
from .state import (
    ReplayState,
    clean_conflict_artifacts,
    clear_replay_state,
    find_conflict_artifacts,
    load_replay_session,
    load_replay_state,
    save_replay_state,
)
from .status import StatusReporter
from .svn import SvnError, SvnLockError, SvnOutOfDateError, SvnWorkspace

try:
    from ._version import __version__  # type: ignore[import-not-found]
except ImportError:
    __version__ = "0.0.0.dev0"


def get_version() -> str:
    """Return package version from installed distribution metadata or fallback to __version__."""
    try:
        from importlib.metadata import version

        return str(version("git2svn"))
    except Exception:
        return str(__version__)


__all__ = [
    "Synchronizer",
    "GitRepo",
    "SvnWorkspace",
    "SvnError",
    "SvnLockError",
    "SvnOutOfDateError",
    "Patcher",
    "TerminalColor",
    "ColoredLogFormatter",
    "StatusReporter",
    "Doctor",
    "run_doctor",
    "CheckResult",
    "FixResult",
    "run_completion",
    "get_completion_script",
    "install_completion",
    "run_setup",
    "FileChange",
    "parse_ref_arguments",
    "parse_name_status",
    "parse_name_status_z",
    "detect_file_eol",
    "normalize_file_eol",
    "SnapshotSynchronizer",
    "save_replay_state",
    "load_replay_state",
    "load_replay_session",
    "ReplayState",
    "clear_replay_state",
    "find_conflict_artifacts",
    "clean_conflict_artifacts",
    "build_parser",
    "parse_cli_args",
    "main",
    "get_version",
    "__version__",
]
