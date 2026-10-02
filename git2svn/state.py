from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

logger = logging.getLogger("git2svn")


@dataclass
class ReplayState:
    """Represents an active or paused multi-commit replay session."""

    git_dir: str
    svn_dir: str
    current_commit: str
    current_commit_msg: str
    remaining_commits: List[str]
    total_commits: int
    completed_commits: int
    state: str = "CONFLICT_PAUSED"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReplayState:
        remaining = data.get("remaining_commits", [])
        return cls(
            git_dir=data.get("git_dir", ""),
            svn_dir=data.get("svn_dir", ""),
            current_commit=data.get("current_commit", ""),
            current_commit_msg=data.get("current_commit_msg", ""),
            remaining_commits=list(remaining),
            total_commits=int(data.get("total_commits", len(remaining) + 1)),
            completed_commits=int(data.get("completed_commits", 0)),
            state=data.get("state", "CONFLICT_PAUSED"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_replay_state_path(workspace_dir: Path) -> Path:
    """Get the path to the replay state file inside .svn metadata or workspace root."""
    svn_meta = workspace_dir / ".svn"
    if svn_meta.is_dir():
        return svn_meta / "git2svn-replay.json"
    return workspace_dir / ".git2svn-replay.json"


def save_replay_state(workspace_dir: Path, data: Dict[str, Any] | ReplayState) -> None:
    """Save replay state to disk."""
    path = get_replay_state_path(workspace_dir)
    raw = data.to_dict() if isinstance(data, ReplayState) else data
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def load_replay_state(workspace_dir: Path) -> Optional[Dict[str, Any]]:
    """Load replay state from disk as dict if it exists."""
    path = get_replay_state_path(workspace_dir)
    if path.is_file():
        try:
            val = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(val, dict):
                return cast(Dict[str, Any], val)
        except Exception as e:
            logger.error("Failed to parse replay state file: %s", e)
    return None


def load_replay_session(workspace_dir: Path) -> Optional[ReplayState]:
    """Load replay state from disk as typed ReplayState if it exists."""
    raw = load_replay_state(workspace_dir)
    return ReplayState.from_dict(raw) if raw else None


def clear_replay_state(workspace_dir: Path) -> None:
    """Remove the replay state file if it exists."""
    path = get_replay_state_path(workspace_dir)
    if path.is_file():
        path.unlink(missing_ok=True)


def find_conflict_artifacts(workspace_dir: Path) -> List[Path]:
    """Find all .rej and .orig files in workspace_dir (ignoring .svn)."""
    artifacts: List[Path] = []
    for root, dirs, files in os.walk(workspace_dir):
        if ".svn" in dirs:
            dirs.remove(".svn")
        for f in files:
            if f.endswith(".rej") or f.endswith(".orig"):
                artifacts.append(Path(root) / f)
    return artifacts


def clean_conflict_artifacts(workspace_dir: Path) -> List[Path]:
    """Remove all .rej and .orig files in workspace_dir."""
    artifacts = find_conflict_artifacts(workspace_dir)
    for a in artifacts:
        try:
            a.unlink(missing_ok=True)
            logger.debug("Removed conflict artifact: %s", a)
        except OSError:
            pass
    return artifacts
