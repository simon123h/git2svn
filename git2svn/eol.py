from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("git2svn")


def detect_file_eol(file_path: Path) -> Optional[bytes]:
    """
    Detect the predominant newline style of a file (b'\\r\\n' vs b'\\n').
    Returns None if the file does not exist, is empty, has no newlines, or is binary.
    """
    if not file_path.is_file() or file_path.is_symlink():
        return None
    try:
        data = file_path.read_bytes()
    except OSError:
        return None

    if b"\0" in data[:4096]:
        return None  # Likely binary file

    crlf_count = data.count(b"\r\n")
    lf_count = data.count(b"\n") - crlf_count

    if crlf_count > lf_count:
        return b"\r\n"
    if lf_count > 0:
        return b"\n"
    return None


def normalize_file_eol(file_path: Path, target_eol: Optional[bytes] = None) -> None:
    """
    Normalize all line endings in file_path to target_eol (b'\\r\\n' or b'\\n').
    If target_eol is None, detects predominant newline in file_path (defaults to b'\\n').
    Skips binary files and symlinks.
    """
    if not file_path.is_file() or file_path.is_symlink():
        return
    try:
        data = file_path.read_bytes()
    except OSError:
        return

    if b"\0" in data[:4096]:
        return  # Binary file

    if target_eol is None:
        crlf_count = data.count(b"\r\n")
        lf_count = data.count(b"\n") - crlf_count
        target_eol = b"\r\n" if crlf_count > lf_count else b"\n"

    unified = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if target_eol == b"\r\n":
        normalized = unified.replace(b"\n", b"\r\n")
    else:
        normalized = unified

    if normalized != data:
        try:
            file_path.write_bytes(normalized)
            logger.debug("Normalized EOL in %s to %s", file_path, repr(target_eol.decode("ascii")))
        except OSError as e:
            logger.warning("Could not normalize line endings for %s: %s", file_path, e)
