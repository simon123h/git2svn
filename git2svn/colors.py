from __future__ import annotations

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

    # Semantic badges
    def ok(self, text: str = "OK") -> str:
        return self.bold_green(f"✔ {text}")

    def clean(self, text: str = "Clean") -> str:
        return self.bold_green(f"✔ {text}")

    def warn(self, text: str = "Warning") -> str:
        return self.bold_yellow(f"⚠ {text}")

    def error(self, text: str = "Error") -> str:
        return self.bold_red(f"✖ {text}")
