import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import git2svn


class TestPatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name).resolve()
        self.patcher = git2svn.Patcher(self.target_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_diff_creates_and_modifies_files(self):
        target_file = self.target_dir / "sample.txt"
        target_file.write_text("Hello\nWorld\n")

        diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,3 @@\n Hello\n+Awesome\n World\n"
        self.patcher.apply_diff(diff)
        self.assertEqual(target_file.read_text(), "Hello\nAwesome\nWorld\n")

    def test_apply_diff_empty(self):
        with patch("subprocess.run") as mock_run:
            self.patcher.apply_diff("")
            mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
