import tempfile
import unittest
from pathlib import Path

import git2svn


class TestEolUtilities(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_file_eol(self):
        f_crlf = self.path / "crlf.txt"
        f_crlf.write_bytes(b"a\r\nb\r\nc\r\n")
        self.assertEqual(git2svn.detect_file_eol(f_crlf), b"\r\n")

        f_lf = self.path / "lf.txt"
        f_lf.write_bytes(b"a\nb\nc\n")
        self.assertEqual(git2svn.detect_file_eol(f_lf), b"\n")

        f_empty = self.path / "empty.txt"
        f_empty.write_bytes(b"")
        self.assertIsNone(git2svn.detect_file_eol(f_empty))

        f_bin = self.path / "bin.dat"
        f_bin.write_bytes(b"foo\0bar\r\n")
        self.assertIsNone(git2svn.detect_file_eol(f_bin))

    def test_normalize_file_eol(self):
        f = self.path / "mixed.txt"
        # Mixed: CRLF and LF in same file
        f.write_bytes(b"line 1\r\nline 2\nline 3\r\n")
        git2svn.normalize_file_eol(f, target_eol=b"\r\n")
        self.assertEqual(f.read_bytes(), b"line 1\r\nline 2\r\nline 3\r\n")

        git2svn.normalize_file_eol(f, target_eol=b"\n")
        self.assertEqual(f.read_bytes(), b"line 1\nline 2\nline 3\n")


if __name__ == "__main__":
    unittest.main()
