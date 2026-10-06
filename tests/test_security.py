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


if __name__ == "__main__":
    unittest.main()
