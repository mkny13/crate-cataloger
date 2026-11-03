"""Doc- and spec-drift tests for crate-cataloger.

Covers the checks (a)-(e) of issues #41 and #44:
(a) no `crate.py:<nnn>` line references remain in SECURITY.md,
(b) the crate.py module-level names referenced in SECURITY.md exist as
    attributes of the crate module,
(c) argparse subcommands appear in both the crate.py docstring and README.md,
(d) every serve/add long option appears in README.md,
(e) docs/drift-audit.md accurately records 26 replaced line refs.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SECURITY_MD = (BASE / "SECURITY.md").read_text(encoding="utf-8")
README_MD = (BASE / "README.md").read_text(encoding="utf-8")
DOCSTRING = crate.__doc__ or ""


# Module-level names that SECURITY.md must reference as backticked crate.py
# symbols. Each is verified to exist on the crate module.
EXPECTED_CRATE_NAMES = {
    "cmd_setup", "TOKEN_FILE", "client", "Discogs", "API", "_NoRedirect",
    "spell_fix", "_id", "csv_safe", "AddLog", "cmd_match", "cmd_dupes",
    "host_ok", "MAX_BODY", "resolve", "cmd_undo",
}

# Backticked identifiers that look like crate.py names but are not module-
# level crate.py entities (HTTP headers, stdlib modules, DOM APIs, the serve
# subcommand). They are excluded from the module-name existence check.
NON_CRATE_NAMES = {
    "Authorization", "DISCOGS_TOKEN", "urlopen", "nosniff", "CSP",
    "innerHTML", "textContent", "serve", "_authed", "chmod", "token", "undo",
    "CsvSafe", "HostOk", "IdValidation", "NoRedirect", "Resolve",
    "SetupPerms", "ValueError", "folder", "instance_id", "release_id",
}


class TestDocsDrift(unittest.TestCase):
    # (a) SECURITY.md must contain no `crate.py:<digits>` references.
    def test_no_crate_py_line_refs(self):
        bad = re.findall(r"crate\.py:\d+", SECURITY_MD)
        self.assertEqual(bad, [], f"SECURITY.md still contains crate.py line refs: {bad}")

    # (b) every module-level crate.py name kept in SECURITY.md exists as an
    # attribute of the crate module, and any other backticked crate-looking
    # identifier is either a known non-crate name or a real crate.py attribute.
    def test_security_md_names_exist_on_crate(self):
        for name in EXPECTED_CRATE_NAMES:
            self.assertTrue(
                hasattr(crate, name),
                f"{name!r} is not an attribute of the crate module",
            )

        for name in re.findall(r"`([^`]+)`", SECURITY_MD):
            if "." in name:  # Discogs.add, H._send, etc.
                continue
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                continue  # '0o600', 'no-referrer', commands, etc.
            if name in NON_CRATE_NAMES:
                continue
            self.assertTrue(
                hasattr(crate, name),
                f"{name!r} referenced in SECURITY.md is not a crate.py attribute",
            )

    # (c) build the parser and assert every subcommand appears in both the
    # crate.py docstring and the README.
    def test_subcommands_in_docstring_and_readme(self):
        sp = crate.build_parser()._subparsers
        sp_action = next(a for a in sp._actions if hasattr(a, "choices") and a.choices is not None)
        subcommands = sorted(sp_action.choices)
        expected = ["add", "dupes", "match", "quick", "serve", "setup", "undo"]
        self.assertEqual(subcommands, expected)
        for sub in subcommands:
            self.assertIn(sub, DOCSTRING, f"{sub!r} missing from crate.py docstring")
            self.assertIn(sub, README_MD, f"{sub!r} missing from README.md")

    # (d) serve/add long options must appear in README.
    def test_long_options_in_readme(self):
        for opt in ("--folder", "--yes", "--port", "--host"):
            self.assertIn(
                opt, README_MD, f"{opt!r} missing from README.md"
            )

    # (e) docs/drift-audit.md accurately records 26 replaced line refs
    def test_drift_audit_row_1_ref_count(self):
        drift = (BASE / "docs" / "drift-audit.md").read_text(encoding="utf-8")
        self.assertIn("All 26 stale refs replaced", drift)
        self.assertNotIn("All 23 stale refs replaced", drift)


if __name__ == "__main__":
    unittest.main()
