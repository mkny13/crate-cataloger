import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "block_generated_csvs.py"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
    )


class TestFileEditingHooks(unittest.TestCase):
    def test_blocks_edit_on_added_csv(self):
        res = run_hook({"tool_name": "Edit", "tool_input": {"file_path": "added-20240101.csv"}})
        self.assertEqual(res.returncode, 2)
        self.assertIn("Blocked: added-20240101.csv", res.stderr)

    def test_blocks_edit_on_dupes_csv(self):
        res = run_hook({"tool_name": "Edit", "tool_input": {"file_path": "dupes-20240101.csv"}})
        self.assertEqual(res.returncode, 2)
        self.assertIn("Blocked: dupes-20240101.csv", res.stderr)

    def test_blocks_write_on_generated_csv(self):
        res = run_hook({"tool_name": "Write", "tool_input": {"file_path": "data/added-123.csv"}})
        self.assertEqual(res.returncode, 2)
        self.assertIn("Blocked: added-123.csv", res.stderr)

    def test_blocks_multiedit_on_generated_csv(self):
        res = run_hook({"tool_name": "MultiEdit", "tool_input": {"file_path": "added-20240101.csv"}})
        self.assertEqual(res.returncode, 2)
        self.assertIn("Blocked: added-20240101.csv", res.stderr)

        res_edits = run_hook({
            "tool_name": "MultiEdit",
            "tool_input": {"edits": [{"file_path": "dupes-20240101.csv"}]}
        })
        self.assertEqual(res_edits.returncode, 2)
        self.assertIn("Blocked: dupes-20240101.csv", res_edits.stderr)

    def test_blocks_notebook_edit_on_generated_csv(self):
        res = run_hook({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "added-20240101.csv"}})
        self.assertEqual(res.returncode, 2)
        self.assertIn("Blocked: added-20240101.csv", res.stderr)

    def test_allows_edit_on_regular_files(self):
        for path in ("crate.py", "README.md", "foo.txt", "LIST.review.csv", "other.csv"):
            res = run_hook({"tool_name": "Edit", "tool_input": {"file_path": path}})
            self.assertEqual(res.returncode, 0, f"Expected {path} to be allowed")


class TestBashHooks(unittest.TestCase):
    def test_blocks_sed_in_place(self):
        commands = [
            "sed -i added-*.csv",
            "sed -i added-20240101.csv",
            "sed -i '' 's/foo/bar/' added-20240101.csv",
            "sed -i.bak 's/foo/bar/' dupes-20240101.csv",
            "sed --in-place 's/foo/bar/' added-20240101.csv",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 2, f"Expected {cmd} to be blocked")
            self.assertIn("Blocked:", res.stderr)

    def test_blocks_awk_commands(self):
        commands = [
            "awk ... added-*.csv",
            "awk '{print}' added-20240101.csv",
            "awk '{print}' added-20240101.csv > tmp && mv tmp added-20240101.csv",
            "awk -F, '{print $1}' dupes-20240101.csv",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 2, f"Expected {cmd} to be blocked")
            self.assertIn("Blocked:", res.stderr)

    def test_blocks_redirections(self):
        commands = [
            "echo 'x' > added-20240101.csv",
            "echo 'x' >> dupes-20240101.csv",
            "cat other.csv > added-*.csv",
            "python3 script.py > added-20240101.csv",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 2, f"Expected {cmd} to be blocked")
            self.assertIn("Blocked:", res.stderr)

    def test_blocks_tee(self):
        commands = [
            "echo 'x' | tee added-20240101.csv",
            "echo 'x' | tee -a dupes-20240101.csv",
            "cat other.csv | tee added-*.csv",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 2, f"Expected {cmd} to be blocked")
            self.assertIn("Blocked:", res.stderr)

    def test_blocks_cp_and_mv_destination(self):
        commands = [
            "cp /tmp/source.csv added-20240101.csv",
            "mv /tmp/source.csv dupes-20240101.csv",
            "cp -f other.csv added-*.csv",
            "mv -f other.csv 'added-20240101.csv'",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 2, f"Expected {cmd} to be blocked")
            self.assertIn("Blocked:", res.stderr)

    def test_allows_non_mutating_or_non_csv_commands(self):
        commands = [
            "ls",
            "cat other.txt",
            "sed -i 's/foo/bar/' other.txt",
            "awk '{print}' other.txt",
            "echo 'x' > other.txt",
            "cp other.txt backup.txt",
            "mv other.txt backup.txt",
            "rm added-20240101.csv",
            "rm added-*.csv",
            "python3 crate.py undo added-20240101.csv",
            "cat added-20240101.csv",
            "cp added-20240101.csv /tmp/backup.csv",
            "mv added-20240101.csv /tmp/",
            "git status",
        ]
        for cmd in commands:
            res = run_hook({"tool_name": "Bash", "tool_input": {"command": cmd}})
            self.assertEqual(res.returncode, 0, f"Expected {cmd} to be allowed, got code {res.returncode}: {res.stderr}")


class TestSettingsConfig(unittest.TestCase):
    def test_matcher_includes_bash(self):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        pre_tool_hooks = data.get("hooks", {}).get("PreToolUse", [])
        hook_entries = [
            entry for entry in pre_tool_hooks
            if any("block_generated_csvs.py" in h.get("command", "") for h in entry.get("hooks", []))
        ]
        self.assertEqual(len(hook_entries), 1)
        matcher = hook_entries[0].get("matcher", "")
        self.assertIn("Bash", matcher.split("|"))
        self.assertIn("Edit", matcher.split("|"))
        self.assertIn("Write", matcher.split("|"))
        self.assertIn("MultiEdit", matcher.split("|"))
        self.assertIn("NotebookEdit", matcher.split("|"))


if __name__ == "__main__":
    unittest.main()
