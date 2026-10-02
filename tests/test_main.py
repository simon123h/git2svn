import runpy
import sys
import unittest
from unittest.mock import patch


class TestMain(unittest.TestCase):
    def test_main_module_execution(self):
        """Verify python -m git2svn delegates to cli.main and exits."""
        with (
            patch.object(sys, "argv", ["git2svn", "--version"]),
            patch("git2svn.cli.main", return_value=0) as mock_cli_main,
            self.assertRaises(SystemExit) as cm,
        ):
            runpy.run_module("git2svn.__main__", run_name="__main__")
        self.assertEqual(cm.exception.code, 0)
        mock_cli_main.assert_called_once()


if __name__ == "__main__":
    unittest.main()
