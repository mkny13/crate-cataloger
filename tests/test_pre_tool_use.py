import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py"


class TestPreToolUseHook(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.test_dir = Path(self.temp_dir.name)

        self.bin_dir = self.test_dir / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        self.nudge_dir = self.test_dir / "nudge"

        # Create stub mahler executable
        self.stub_mahler = self.bin_dir / "mahler"
        self.stub_mahler.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if 'status' in sys.argv:\n"
            "    output = os.environ.get('STUB_MAHLER_STATUS_JSON', '{\"leases\": []}')\n"
            "    sys.stdout.write(output)\n"
            "    sys.exit(0)\n"
            "sys.exit(0)\n"
        )
        self.stub_mahler.chmod(0o755)

    def run_hook(
        self,
        env_overrides: dict = None,
        stdin_data: str = "{}",
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        # Default to interactive mode by unsetting MAHLER_RUN_ID
        env.pop("MAHLER_RUN_ID", None)
        # Prepend stub mahler to PATH
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"

        if env_overrides:
            for k, v in env_overrides.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = str(v)

        return subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=stdin_data,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_unclaimed_interactive_run_prints_nudge_and_sets_permissions(self):
        """1. Unclaimed interactive run prints nudge, sets 0700 dir and 0600 file."""
        session_id = "test-session-alpha"
        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
        })
        self.assertEqual(res.returncode, 0)
        self.assertIn(
            "If this work relates to a backlog item, run `mahler claim crate-cataloger#N`.",
            res.stdout,
        )

        # Assert directory permissions are 0700
        self.assertTrue(self.nudge_dir.is_dir())
        self.assertEqual(stat.S_IMODE(self.nudge_dir.stat().st_mode), 0o700)

        # Assert marker file permissions are 0600
        marker_file = self.nudge_dir / session_id
        self.assertTrue(marker_file.is_file())
        self.assertEqual(stat.S_IMODE(marker_file.stat().st_mode), 0o600)

    def test_subsequent_interactive_run_suppresses_nudge(self):
        """2. Subsequent interactive run with same session ID suppresses nudge."""
        session_id = "test-session-repeat"
        env = {
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
        }
        res1 = self.run_hook(env)
        self.assertEqual(res1.returncode, 0)
        self.assertIn("mahler claim crate-cataloger#N", res1.stdout)

        # Second run should produce no nudge output
        res2 = self.run_hook(env)
        self.assertEqual(res2.returncode, 0)
        self.assertEqual(res2.stdout, "")

    def test_claimed_interactive_run_suppresses_nudge_and_creates_no_marker(self):
        """3. Claimed interactive run suppresses nudge and does not create marker file."""
        session_id = "claimed_session_123"
        my_holder = f"interactive:{session_id[:8]}"
        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
            "STUB_MAHLER_STATUS_JSON": json.dumps({"leases": [{"holder": my_holder}]}),
        })
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        marker_file = self.nudge_dir / session_id
        self.assertFalse(marker_file.exists())

    def test_claimed_interactive_run_with_slash_prefix_holder(self):
        """Claimed interactive run with e.g. subagent/<holder> also suppresses nudge."""
        session_id = "subsession_456"
        my_holder = f"interactive:{session_id[:8]}"
        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
            "STUB_MAHLER_STATUS_JSON": json.dumps({"leases": [{"holder": f"parent/{my_holder}"}]}),
        })
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        marker_file = self.nudge_dir / session_id
        self.assertFalse(marker_file.exists())

    def test_session_id_sanitization(self):
        """4. Session ID sanitization strips traversal/special characters and stays in nudge dir."""
        session_id = "../../bad/session:id!"
        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
        })
        self.assertEqual(res.returncode, 0)

        # Check marker filename is sanitized and contained within MAHLER_NUDGE_DIR
        marker_files = list(self.nudge_dir.iterdir())
        self.assertEqual(len(marker_files), 1)
        marker_file = marker_files[0]
        self.assertEqual(marker_file.name, "badsessionid")
        self.assertIsNotNone(re.fullmatch(r"[A-Za-z0-9_-]+", marker_file.name))
        self.assertEqual(marker_file.resolve().parent, self.nudge_dir.resolve())
        # Ensure no outside file or directory traversal occurred
        self.assertFalse((self.test_dir / "bad").exists())

    def test_symlink_safety_does_not_modify_target(self):
        """5. Symlink safety: pre-created symlink at marker location is not overwritten or modified."""
        outside_file = self.test_dir / "target.txt"
        outside_file.write_text("protected content")
        self.nudge_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        session_id = "symlink_session"
        marker_symlink = self.nudge_dir / session_id
        marker_symlink.symlink_to(outside_file)

        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
        })
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertEqual(outside_file.read_text(), "protected content")
        self.assertTrue(marker_symlink.is_symlink())

    def test_dangling_symlink_treated_as_existing(self):
        """Dangling symlink at marker location is treated as existing via lexists."""
        dangling_target = self.test_dir / "does_not_exist.txt"
        self.nudge_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        session_id = "dangling_session"
        marker_symlink = self.nudge_dir / session_id
        marker_symlink.symlink_to(dangling_target)

        res = self.run_hook({
            "MAHLER_NUDGE_DIR": str(self.nudge_dir),
            "CLAUDE_CODE_SESSION_ID": session_id,
        })
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertFalse(dangling_target.exists())
        self.assertTrue(marker_symlink.is_symlink())

    def test_yield_behavior_in_autonomous_mode(self):
        """6. Yield behavior in autonomous mode exits with status 2 and prints yield instructions."""
        runs_dir = self.test_dir / "runs"
        run_id = "run-999"
        yield_file = runs_dir / run_id / "yield"
        yield_file.parent.mkdir(parents=True, exist_ok=True)
        yield_file.write_text("stop requested")

        res = self.run_hook({
            "MAHLER_RUN_ID": run_id,
            "MAHLER_RUNS_DIR": str(runs_dir),
        })
        self.assertEqual(res.returncode, 2)
        self.assertIn(
            "yield delivered: commit your work, push the branch, and end with STATUS: YIELDED",
            res.stdout,
        )

    def test_yield_dangling_symlink_triggers_yield(self):
        """8. A dangling symlink at the yield file triggers yield (lexists vs exists semantics)."""
        runs_dir = self.test_dir / "runs"
        run_id = "run-dangling"
        yield_file = runs_dir / run_id / "yield"
        yield_file.parent.mkdir(parents=True, exist_ok=True)
        # os.path.exists() would return False for this; os.path.lexists() returns True.
        yield_file.symlink_to(runs_dir / run_id / "missing-target")

        res = self.run_hook({
            "MAHLER_RUN_ID": run_id,
            "MAHLER_RUNS_DIR": str(runs_dir),
        })
        self.assertEqual(res.returncode, 2)
        self.assertIn("yield delivered", res.stdout)

    def test_default_nudge_dir_fallback_to_home(self):
        """Fallback to ~/.mahler/nudge when MAHLER_NUDGE_DIR is unset."""
        fake_home = self.test_dir / "home"
        fake_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        session_id = "home_fallback_session"

        res = self.run_hook({
            "MAHLER_NUDGE_DIR": None,
            "HOME": str(fake_home),
            "CLAUDE_CODE_SESSION_ID": session_id,
        })
        self.assertEqual(res.returncode, 0)
        self.assertIn("mahler claim crate-cataloger#N", res.stdout)
        fallback_marker = fake_home / ".mahler" / "nudge" / session_id
        self.assertTrue(fallback_marker.is_file())
        self.assertEqual(stat.S_IMODE(fallback_marker.stat().st_mode), 0o600)

    def test_yield_check_uses_lexists(self):
        """7. Static check: the autonomous yield-file existence check uses os.path.lexists."""
        source = HOOK_SCRIPT.read_text(encoding="utf-8")
        # The yield check must use lexists (symlink-safe), matching the nudge path.
        self.assertIn("os.path.lexists(yield_file)", source)
        # The old exists()-based yield check must not be present.
        self.assertNotIn("os.path.exists(yield_file)", source)
        # Consistency: both yield and nudge state-file checks use lexists.
        self.assertGreaterEqual(source.count("os.path.lexists("), 2)


if __name__ == "__main__":
    unittest.main()
