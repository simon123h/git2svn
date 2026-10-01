from __future__ import annotations

import logging
import os
import sys


class TerminalColor:
    """ANSI terminal color formatting with auto/always/never detection and NO_COLOR support."""

    # ANSI Styles & Colors
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    BOLD_RED = "\033[1;31m"
    BOLD_GREEN = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"
    BOLD_CYAN = "\033[1;36m"

    def __init__(self, mode: str = "auto"):
        self.mode = mode.lower()
        self.enabled = self._resolve_enabled()

    def _resolve_enabled(self) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False

        # Mode is "auto"
        # 1. Respect NO_COLOR standard (https://no-color.org/)
        if "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != "":
            return False

        # 2. Check for dumb terminals
        if os.environ.get("TERM") == "dumb":
            return False

        # 3. Check if stdout is an interactive TTY
        try:
            return sys.stdout.isatty()
        except Exception:
            return False

    def colorize(self, text: str, ansi_code: str) -> str:
        """Wrap text in ANSI color escape codes if coloring is enabled."""
        if not self.enabled:
            return text
        return f"{ansi_code}{text}{self.RESET}"

    # Style helpers
    def bold(self, text: str) -> str:
        return self.colorize(text, self.BOLD)

    def dim(self, text: str) -> str:
        return self.colorize(text, self.DIM)

    def red(self, text: str) -> str:
        return self.colorize(text, self.RED)

    def green(self, text: str) -> str:
        return self.colorize(text, self.GREEN)

    def yellow(self, text: str) -> str:
        return self.colorize(text, self.YELLOW)

    def cyan(self, text: str) -> str:
        return self.colorize(text, self.CYAN)

    def gray(self, text: str) -> str:
        return self.colorize(text, self.GRAY)

    def bold_red(self, text: str) -> str:
        return self.colorize(text, self.BOLD_RED)

    def bold_green(self, text: str) -> str:
        return self.colorize(text, self.BOLD_GREEN)

    def bold_yellow(self, text: str) -> str:
        return self.colorize(text, self.BOLD_YELLOW)

    def bold_cyan(self, text: str) -> str:
        return self.colorize(text, self.BOLD_CYAN)

    # Semantic badges & prefixes
    def ok(self, text: str = "OK") -> str:
        return self.bold_green(f"✔ {text}")

    def clean(self, text: str = "Clean") -> str:
        return self.bold_green(f"✔ {text}")

    def warn(self, text: str = "Warning") -> str:
        return self.bold_yellow(f"⚠ {text}")

    def error(self, text: str = "Error") -> str:
        return self.bold_red(f"✖ {text}")

    def info_badge(self, text: str = "[INFO]  ") -> str:
        return self.bold_cyan(text)

    def target_badge(self, text: str = "[TARGET]") -> str:
        return self.bold_cyan(text)

    def warn_badge(self, text: str = "[WARN]  ") -> str:
        return self.bold_yellow(text)

    def error_badge(self, text: str = "[ERROR] ") -> str:
        return self.bold_red(text)

    def paused_badge(self, text: str = "[PAUSED]") -> str:
        return self.bold_yellow(text)


class ColoredLogFormatter(logging.Formatter):
    """Custom logging formatter that colorizes and aligns log level prefixes using TerminalColor."""

    def __init__(self, color: TerminalColor):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        msg = record.getMessage()

        # Format prefix aligned to 8 chars: e.g. [INFO]  , [WARN]  , [ERROR]
        if levelname == "INFO":
            raw_tag = "[INFO]  "
            badge = self.color.info_badge(raw_tag) if self.color.enabled else raw_tag
        elif levelname in ("WARNING", "WARN"):
            raw_tag = "[WARN]  "
            badge = self.color.warn_badge(raw_tag) if self.color.enabled else raw_tag
        elif levelname in ("ERROR", "CRITICAL"):
            raw_tag = "[ERROR] "
            badge = self.color.error_badge(raw_tag) if self.color.enabled else raw_tag
        elif levelname == "DEBUG":
            raw_tag = "[DEBUG] "
            badge = self.color.dim(raw_tag) if self.color.enabled else raw_tag
        else:
            raw_tag = f"[{levelname}]".ljust(8)
            badge = raw_tag

        return f"{badge} {msg}"
