from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

logger = logging.getLogger("git2svn")


def get_replay_state_path(workspace_dir: Path) -> Path:
    """Get the path to the replay state file inside .svn metadata or workspace root."""
    svn_meta = workspace_dir / ".svn"
    if svn_meta.is_dir():
        return svn_meta / "git2svn-replay.json"
    return workspace_dir / ".git2svn-replay.json"


def save_replay_state(workspace_dir: Path, data: Dict[str, Any]) -> None:
    """Save replay state to disk."""
    path = get_replay_state_path(workspace_dir)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_replay_state(workspace_dir: Path) -> Optional[Dict[str, Any]]:
    """Load replay state from disk if it exists."""
    path = get_replay_state_path(workspace_dir)
    if path.is_file():
        try:
            val = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(val, dict):
                return cast(Dict[str, Any], val)
        except Exception as e:
            logger.error("Failed to parse replay state file: %s", e)
    return None


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
