import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from git2svn.completion import (
    detect_shell,
    generate_bash_completion,
    generate_fish_completion,
    generate_zsh_completion,
    get_completion_script,
    get_default_install_path,
    install_completion,
    run_completion,
)


class TestCompletion(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.fake_home = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_shell(self):
        with patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(detect_shell(), "bash")

        with patch.dict(os.environ, {"SHELL": "/usr/local/bin/zsh"}):
            self.assertEqual(detect_shell(), "zsh")

        with patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
            self.assertEqual(detect_shell(), "fish")

        with patch.dict(os.environ, {"SHELL": "/bin/csh"}):
            self.assertEqual(detect_shell(), "bash")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(detect_shell(), "bash")

    def test_generate_bash_completion(self):
        script = generate_bash_completion()
        self.assertIn("complete -F _git2svn_completions git2svn", script)
        self.assertIn("stage", script)
        self.assertIn("replay", script)
        self.assertIn("doctor", script)
        self.assertIn("completion", script)

    def test_generate_zsh_completion(self):
        script = generate_zsh_completion()
        self.assertTrue(script.startswith("#compdef git2svn"))
        self.assertIn("stage:Stage changes", script)
        self.assertIn("replay:Sequentially port", script)
        self.assertIn("doctor:Run pre-flight", script)
        self.assertIn("completion:Generate shell", script)

    def test_generate_fish_completion(self):
        script = generate_fish_completion()
        self.assertIn("complete -c git2svn", script)
        self.assertIn("-a stage", script)
        self.assertIn("-a replay", script)
        self.assertIn("-a doctor", script)
        self.assertIn("-a completion", script)

    def test_get_completion_script_supported_and_unsupported(self):
        self.assertEqual(get_completion_script("bash"), generate_bash_completion())
        self.assertEqual(get_completion_script("zsh"), generate_zsh_completion())
        self.assertEqual(get_completion_script("fish"), generate_fish_completion())

        with self.assertRaises(ValueError):
            get_completion_script("powershell")

    def test_get_default_install_path(self):
        with patch("pathlib.Path.home", return_value=self.fake_home):
            bash_path = get_default_install_path("bash")
            self.assertEqual(bash_path, self.fake_home / ".local/share/bash-completion/completions/git2svn")

            zsh_path = get_default_install_path("zsh")
            self.assertEqual(zsh_path, self.fake_home / ".zsh/completion/_git2svn")

            fish_path = get_default_install_path("fish")
            self.assertEqual(fish_path, self.fake_home / ".config/fish/completions/git2svn.fish")

    def test_install_completion(self):
        with patch("pathlib.Path.home", return_value=self.fake_home):
            installed_path = install_completion("bash")
            self.assertTrue(installed_path.is_file())
            content = installed_path.read_text(encoding="utf-8")
            self.assertIn("complete -F _git2svn_completions git2svn", content)

    def test_run_completion_stdout(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            code = run_completion(shell="bash", install=False)
        self.assertEqual(code, 0)
        self.assertIn("complete -F _git2svn_completions git2svn", stdout.getvalue())

    def test_run_completion_install(self):
        with patch("pathlib.Path.home", return_value=self.fake_home):
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = run_completion(shell="zsh", install=True)
            self.assertEqual(code, 0)
            self.assertIn("Successfully installed zsh completion", stdout.getvalue())
            installed = self.fake_home / ".zsh/completion/_git2svn"
            self.assertTrue(installed.is_file())

    def test_run_completion_invalid_shell(self):
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            code = run_completion(shell="invalid_shell")
        self.assertEqual(code, 1)
        self.assertIn("Unsupported shell", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
