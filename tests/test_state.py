import tempfile
import unittest
from pathlib import Path

from git2svn.state import (
    ReplayState,
    clean_conflict_artifacts,
    clear_replay_state,
    find_conflict_artifacts,
    load_replay_session,
    load_replay_state,
    save_replay_state,
)


class TestReplayState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        (self.workspace / ".svn").mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_lifecycle_with_dataclass(self):
        """Verify saving and loading ReplayState as typed dataclass."""
        state = ReplayState(
            git_dir="/path/to/git",
            svn_dir="/path/to/svn",
            current_commit="abcdef123456",
            current_commit_msg="feat: hello world",
            remaining_commits=["111111", "222222"],
            total_commits=3,
            completed_commits=1,
            state="CONFLICT_PAUSED",
        )
        save_replay_state(self.workspace, state)

        # Load as dict
        raw_dict = load_replay_state(self.workspace)
        self.assertIsNotNone(raw_dict)
        assert raw_dict is not None
        self.assertEqual(raw_dict["current_commit"], "abcdef123456")
        self.assertEqual(raw_dict["total_commits"], 3)

        # Load as typed ReplayState session
        session = load_replay_session(self.workspace)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.current_commit, "abcdef123456")
        self.assertEqual(session.current_commit_msg, "feat: hello world")
        self.assertEqual(session.remaining_commits, ["111111", "222222"])
        self.assertEqual(session.total_commits, 3)
        self.assertEqual(session.completed_commits, 1)

        # Clear
        clear_replay_state(self.workspace)
        self.assertIsNone(load_replay_session(self.workspace))

    def test_conflict_artifacts_cleaner(self):
        """Verify finding and cleaning .rej and .orig files."""
        sub = self.workspace / "sub"
        sub.mkdir()
        rej1 = self.workspace / "file.rej"
        orig1 = self.workspace / "file.orig"
        rej2 = sub / "other.rej"
        normal = sub / "keep.txt"

        for f in [rej1, orig1, rej2, normal]:
            f.write_text("content")

        artifacts = find_conflict_artifacts(self.workspace)
        self.assertEqual(len(artifacts), 3)

        cleaned = clean_conflict_artifacts(self.workspace)
        self.assertEqual(len(cleaned), 3)
        self.assertFalse(rej1.exists())
        self.assertFalse(orig1.exists())
        self.assertFalse(rej2.exists())
        self.assertTrue(normal.exists())


if __name__ == "__main__":
    unittest.main()
