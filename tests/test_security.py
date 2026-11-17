import getpass
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402


class CsvSafe(unittest.TestCase):
    def test_prefixes_formula_chars(self):
        for ch in ("=", "+", "-", "@", "\t", "\r"):
            self.assertEqual(crate.csv_safe(ch + "x"), "'" + ch + "x")

    def test_plain_text_unchanged(self):
        self.assertEqual(crate.csv_safe("Radiohead"), "Radiohead")


class HostOk(unittest.TestCase):
    def test_accepts(self):
        for h in ("127.0.0.1:8765", "localhost:8765", "192.168.1.5:8765"):
            self.assertTrue(crate.host_ok(h, "192.168.1.5", 8765), h)

    def test_rejects(self):
        for h in ("evil.com:8765", "127.0.0.1:9999", None, ""):
            self.assertFalse(crate.host_ok(h, "192.168.1.5", 8765), h)


class NoRedirect(unittest.TestCase):
    def test_redirect_refused(self):
        self.assertIsNone(crate._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.com"))


class Resolve(unittest.TestCase):
    def test_valid(self):
        for ref in ("r123", "123", "https://www.discogs.com/release/123-x"):
            self.assertEqual(crate.resolve(None, ref), 123, ref)

    def test_invalid(self):
        for ref in ("r12/../3", "abc"):
            with self.assertRaises(ValueError):
                crate.resolve(None, ref)


class IdValidation(unittest.TestCase):
    def setUp(self):
        self.dc = crate.Discogs("tok")
        self.dc._user = "u"
        self.calls = []
        self.dc.call = lambda method, path, params=None: self.calls.append((method, path)) or {"instance_id": 456}

    def test_remove_rejects(self):
        for args in (("123", "1/../..", "456"), ("x", "1", "456"), ("123", "1", "-1")):
            with self.assertRaises(ValueError):
                self.dc.remove(*args)
        self.assertEqual(self.calls, [])

    def test_add_rejects(self):
        for args in (("123", "1/../.."), ("x", "1"), ("-5", "1")):
            with self.assertRaises(ValueError):
                self.dc.add(*args)
        self.assertEqual(self.calls, [])

    def test_rejects_non_canonical_digits(self):
        for bad in ("1_0", "+5", "１２", "1 0", "0x10"):
            with self.assertRaises(ValueError):
                self.dc.add(bad, "1")
            with self.assertRaises(ValueError):
                self.dc.add("123", bad)
            for args in ((bad, "1", "4"), ("1", bad, "4"), ("1", "1", bad)):
                with self.assertRaises(ValueError):
                    self.dc.remove(*args)
        self.assertEqual(self.calls, [])

    def test_remove_accepts(self):
        self.dc.remove("123", "1", "456")
        self.assertEqual(self.calls, [("DELETE", "/users/u/collection/folders/1/releases/123/instances/456")])

    def test_remove_folder_zero(self):
        self.dc.remove("123", "0", "456")
        self.assertEqual(len(self.calls), 1)

    def test_add_accepts(self):
        self.assertEqual(self.dc.add("123", "1"), 456)
        self.assertEqual(self.calls, [("POST", "/users/u/collection/folders/1/releases/123")])


class SetupPerms(unittest.TestCase):
    def test_token_file_mode(self):
        with tempfile.TemporaryDirectory() as d:
            tf = Path(d) / "cfg" / "token"
            with mock.patch.object(crate, "TOKEN_FILE", tf), \
                    mock.patch.object(getpass, "getpass", return_value="faketoken"), \
                    mock.patch.object(crate.Discogs, "username", "someone"):
                crate.cmd_setup(None)
            self.assertEqual(stat.S_IMODE(os.stat(tf).st_mode), 0o600)
            self.assertEqual(tf.read_text(), "faketoken")


ROOT = Path(__file__).resolve().parent.parent


class NoShell(unittest.TestCase):
    def test_crate_py_spawns_nothing(self):
        src = (ROOT / "crate.py").read_text(encoding="utf-8")
        for pat in ("subprocess", "os.system", "os.popen", "eval(", "exec("):
            self.assertNotIn(pat, src, pat)

    def test_hooks_have_no_shell(self):
        hooks = sorted((ROOT / ".claude" / "hooks").glob("*.py"))
        self.assertTrue(hooks)
        for p in hooks:
            src = p.read_text(encoding="utf-8")
            for pat in ("shell=True", "os.system", "os.popen"):
                self.assertNotIn(pat, src, f"{p.name}: {pat}")


class CiPermissions(unittest.TestCase):
    def setUp(self):
        import re
        self.re = re
        self.text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_top_level_contents_read(self):
        self.assertTrue(self.re.search(r"(?m)^permissions:\s*\n[ \t]+contents:[ \t]*read[ \t]*$", self.text))

    def test_no_pull_request_target(self):
        self.assertNotIn("pull_request_target", self.text)


class AgentDenyList(unittest.TestCase):
    def test_deny_entries(self):
        import json
        cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        deny = cfg.get("permissions", {}).get("deny", [])
        for entry in ("Read(~/.config/crate-to-discogs/**)", "Read(./.env)"):
            self.assertIn(entry, deny)
        self.assertIn("hooks", cfg)


ALLOWED_HOSTS = {"api.discogs.com", "itunes.apple.com"}
# Reviewed URL literals in crate.py that are never request targets (UA string, setup hint,
# CSP header, LAN-server hint, release link in CSV). Anything else must be an allowed host.
NON_REQUEST_URLS = {
    "https://github.com/mkny13/crate-cataloger",
    "https://www.discogs.com/settings/developers",
    "https://*.discogs.com",
    "http://",
    "https://www.discogs.com/release/",
}
# Every outbound call site in crate.py: (enclosing function, callee). Adding one must be
# reviewed here under the same allow-list discipline.
EXPECTED_CALL_SITES = [
    ("Discogs.call", "Request", "url"),
    ("Discogs.call", "_opener.open", "req"),
    ("spell_fix", "urlopen", "url"),
]


def _outbound_problems(src):
    """Static check of crate.py source; returns a list of problems (empty when compliant)."""
    import ast
    import re
    from urllib.parse import urlparse
    tree = ast.parse(src)
    problems = []

    # (1) every http(s) literal is an allowed host or a reviewed non-request literal
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for u in re.findall(r"https?://[^\s\"'{}();]*", node.value):
                host = urlparse(u).hostname
                if host not in ALLOWED_HOSTS and u not in NON_REQUEST_URLS:
                    problems.append(f"non-allowlisted URL literal: {u}")

    # (2) enumerate outbound call sites with their enclosing function
    def callee(func):
        if isinstance(func, ast.Name) and func.id in ("urlopen", "Request"):
            return func.id
        if isinstance(func, ast.Attribute):
            if func.attr in ("urlopen", "Request"):
                return func.attr
            if func.attr == "open" and isinstance(func.value, ast.Name) and "opener" in func.value.id:
                return f"{func.value.id}.open"
        return None

    sites, url_assigns = [], {}

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, scope + [child.name])
                continue
            name = ".".join(scope)
            if isinstance(child, ast.Call) and (c := callee(child.func)):
                first = child.args[0] if child.args else None
                arg = first.id if isinstance(first, ast.Name) else ast.dump(first) if first else None
                sites.append((name, c, arg))
            if isinstance(child, (ast.Assign, ast.AugAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                if any(isinstance(t, ast.Name) and t.id == "url" for t in targets):
                    url_assigns.setdefault(name, []).append(child)
            visit(child, scope)

    visit(tree, [])
    if sorted(sites) != sorted(EXPECTED_CALL_SITES):
        problems.append(f"outbound call sites changed: {sorted(sites)} != {sorted(EXPECTED_CALL_SITES)}")

    # (3) in each function issuing a request, `url` is built only from API or an allowed literal
    def base_ok(expr):
        while isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            expr = expr.left
        if isinstance(expr, ast.Name):
            return expr.id == "API"
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return urlparse(expr.value).hostname in ALLOWED_HOSTS
        return False

    for fn in {s[0] for s in sites if s[2] == "url"}:
        assigns = url_assigns.get(fn, [])
        if not any(isinstance(a, ast.Assign) for a in assigns):
            problems.append(f"{fn}: no `url = <allowlisted base> + ...` assignment")
        for a in assigns:
            if isinstance(a, ast.Assign) and not base_ok(a.value):
                problems.append(f"{fn}: url assigned from non-allowlisted base (line {a.lineno})")
            if isinstance(a, ast.AugAssign) and isinstance(a.value, ast.Constant) \
                    and "://" in str(a.value.value):
                problems.append(f"{fn}: url += absolute URL (line {a.lineno})")
    return problems


class OutboundHosts(unittest.TestCase):
    def setUp(self):
        self.src = (ROOT / "crate.py").read_text(encoding="utf-8")

    def test_request_bases_are_allowlisted(self):
        from urllib.parse import urlparse
        self.assertEqual(urlparse(crate.API).hostname, "api.discogs.com")
        self.assertEqual(_outbound_problems(self.src), [])

    def _assert_caught(self, src):
        self.assertTrue(_outbound_problems(src), "mutation was not caught")

    def test_rejects_inline_disallowed_urlopen(self):
        self._assert_caught(self.src + '\n\ndef leak():\n    urllib.request.urlopen("https://other.example/x")\n')

    def test_rejects_inline_disallowed_request(self):
        self._assert_caught(self.src.replace("urllib.request.Request(\n                url,",
                                             'urllib.request.Request(\n                "https://other.example/",', 1))

    def test_rejects_extra_call_site_with_url_var(self):
        self._assert_caught(self.src + "\n\ndef extra(url):\n    urllib.request.urlopen(url)\n")

    def test_rejects_url_from_other_variable(self):
        self._assert_caught(self.src.replace("url = API + path", "url = OTHER + path", 1))

    def test_rejects_removed_call_site(self):
        self._assert_caught(self.src.replace("urllib.request.urlopen(url, timeout=8)", "open(url)", 1))


if __name__ == "__main__":
    unittest.main()
