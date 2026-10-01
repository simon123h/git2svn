import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestTerminalColor(unittest.TestCase):
    def test_color_always(self):
        color = git2svn.TerminalColor(mode="always")
        self.assertTrue(color.enabled)
        self.assertIn("\033[", color.bold_cyan("Test"))
        self.assertIn("\033[", color.clean())
        self.assertIn("\033[", color.ok())
        self.assertIn("\033[", color.warn())
        self.assertIn("\033[", color.error())

    def test_color_never(self):
        color = git2svn.TerminalColor(mode="never")
        self.assertFalse(color.enabled)
        self.assertEqual(color.bold_cyan("Test"), "Test")
        self.assertEqual(color.clean(), "✔ Clean")

    def test_color_auto_no_color_env(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            color = git2svn.TerminalColor(mode="auto")
            self.assertFalse(color.enabled)
            self.assertEqual(color.bold("Hello"), "Hello")

    def test_color_auto_dumb_term(self):
        with patch.dict(os.environ, {"TERM": "dumb", "NO_COLOR": ""}):
            color = git2svn.TerminalColor(mode="auto")
            self.assertFalse(color.enabled)

    def test_color_cli_and_git_config(self):
        temp_dir = tempfile.TemporaryDirectory()
        try:
            repo_path = Path(temp_dir.name).resolve()
            (repo_path / ".svn").mkdir()
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, check=True)
            subprocess.run(["git", "config", "git2svn.color", "always"], cwd=repo_path, check=True)

            with patch("git2svn.cli.Synchronizer") as mock_sync_cls:
                mock_sync_cls.return_value.status.return_value = 0
                # When no --color is passed, config 'always' should be used
                git2svn.main(["--git-dir", str(repo_path), "--svn-dir", str(repo_path), "status"])
                _, kwargs = mock_sync_cls.call_args
                self.assertEqual(kwargs.get("color_mode"), "always")

                # When CLI --color never is passed, it overrides git config
                git2svn.main(["--git-dir", str(repo_path), "--svn-dir", str(repo_path), "--color", "never", "status"])
                _, kwargs = mock_sync_cls.call_args
                self.assertEqual(kwargs.get("color_mode"), "never")
        finally:
            temp_dir.cleanup()

    def test_badges(self):
        color_always = git2svn.TerminalColor(mode="always")
        self.assertIn("\033[", color_always.info_badge())
        self.assertIn("\033[", color_always.target_badge())
        self.assertIn("\033[", color_always.warn_badge())
        self.assertIn("\033[", color_always.error_badge())
        self.assertIn("\033[", color_always.paused_badge())

        color_never = git2svn.TerminalColor(mode="never")
        self.assertEqual(color_never.info_badge(), "[INFO]")
        self.assertEqual(color_never.target_badge(), "[TARGET]")
        self.assertEqual(color_never.warn_badge(), "[WARN]")
        self.assertEqual(color_never.error_badge(), "[ERROR]")
        self.assertEqual(color_never.paused_badge(), "[PAUSED]")

    def test_colored_log_formatter(self):
        import logging

        record_info = logging.LogRecord("test", logging.INFO, "test.py", 10, "info message", (), None)
        record_warn = logging.LogRecord("test", logging.WARNING, "test.py", 10, "warn message", (), None)
        record_err = logging.LogRecord("test", logging.ERROR, "test.py", 10, "err message", (), None)
        record_debug = logging.LogRecord("test", logging.DEBUG, "test.py", 10, "debug message", (), None)

        # Mode: always
        fmt_always = git2svn.ColoredLogFormatter(git2svn.TerminalColor(mode="always"))
        out_info = fmt_always.format(record_info)
        self.assertIn("\033[", out_info)
        self.assertIn("info message", out_info)

        out_warn = fmt_always.format(record_warn)
        self.assertIn("\033[", out_warn)
        self.assertIn("warn message", out_warn)

        out_err = fmt_always.format(record_err)
        self.assertIn("\033[", out_err)
        self.assertIn("err message", out_err)

        out_debug = fmt_always.format(record_debug)
        self.assertIn("\033[", out_debug)
        self.assertIn("debug message", out_debug)

        # Mode: never
        fmt_never = git2svn.ColoredLogFormatter(git2svn.TerminalColor(mode="never"))
        self.assertEqual(fmt_never.format(record_info), "[INFO] info message")
        self.assertEqual(fmt_never.format(record_warn), "[WARNING] warn message")
        self.assertEqual(fmt_never.format(record_err), "[ERROR] err message")
        self.assertEqual(fmt_never.format(record_debug), "[DEBUG] debug message")


if __name__ == "__main__":
    unittest.main()
