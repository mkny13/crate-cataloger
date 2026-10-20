import io
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402


class LanIpResourceTests(unittest.TestCase):
    @mock.patch("socket.socket")
    def test_lan_ip_oserror_returns_fallback_and_closes_socket(self, mock_socket_cls):
        mock_sock = mock.MagicMock()
        mock_sock.connect.side_effect = OSError("network unreachable")
        mock_sock.__enter__.return_value = mock_sock
        mock_sock.__exit__.return_value = False
        mock_socket_cls.return_value = mock_sock

        ip = crate.lan_ip()

        self.assertEqual(ip, "127.0.0.1")
        self.assertTrue(mock_sock.close.called or mock_sock.__exit__.called)

    @mock.patch("socket.socket")
    def test_lan_ip_success_closes_socket(self, mock_socket_cls):
        mock_sock = mock.MagicMock()
        mock_sock.getsockname.return_value = ("192.168.1.50", 12345)
        mock_sock.__enter__.return_value = mock_sock
        mock_sock.__exit__.return_value = False
        mock_socket_cls.return_value = mock_sock

        ip = crate.lan_ip()

        self.assertEqual(ip, "192.168.1.50")
        self.assertTrue(mock_sock.close.called or mock_sock.__exit__.called)


class CmdServeResourceTests(unittest.TestCase):
    @mock.patch("crate.lan_ip", return_value="127.0.0.1")
    @mock.patch("crate.client")
    @mock.patch("crate.ThreadingHTTPServer")
    def test_cmd_serve_keyboard_interrupt_closes_server(self, mock_server_cls, mock_client, _mock_lan_ip):
        mock_dc = mock.MagicMock()
        mock_dc.username = "testuser"
        mock_dc.collection_index.return_value = (set(), set())
        mock_client.return_value = mock_dc

        mock_server = mock.MagicMock()
        mock_server.__enter__.return_value = mock_server
        mock_server.__exit__.return_value = False
        mock_server.serve_forever.side_effect = KeyboardInterrupt
        mock_server_cls.return_value = mock_server

        args = mock.Mock(host="127.0.0.1", port=8765, folder=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(io.StringIO()):
                    crate.cmd_serve(args)
            finally:
                os.chdir(orig_cwd)

        self.assertTrue(mock_server.server_close.called or mock_server.__exit__.called)


class DiscogsCallResourceTests(unittest.TestCase):
    @mock.patch("crate._opener.open")
    def test_call_404_closes_error(self, mock_open):
        mock_fp = mock.MagicMock()
        err_404 = urllib.error.HTTPError(
            "https://api.discogs.com/test", 404, "Not Found", {}, mock_fp
        )
        mock_open.side_effect = err_404

        dc = crate.Discogs("dummy-token")
        res = dc.call("GET", "/test")

        self.assertIsNone(res)
        self.assertTrue(mock_fp.close.called)

    @mock.patch("time.sleep")
    @mock.patch("crate._opener.open")
    def test_call_429_then_success_closes_error(self, mock_open, mock_sleep):
        mock_fp_429 = mock.MagicMock()
        err_429 = urllib.error.HTTPError(
            "https://api.discogs.com/test", 429, "Too Many Requests", {}, mock_fp_429
        )

        mock_resp = mock.MagicMock()
        mock_resp.headers = {}
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        mock_open.side_effect = [err_429, mock_resp]

        dc = crate.Discogs("dummy-token")
        res = dc.call("GET", "/test")

        self.assertEqual(res, {"ok": True})
        self.assertTrue(mock_fp_429.close.called)
        mock_sleep.assert_called_with(15)

    @mock.patch("crate._opener.open")
    def test_call_500_closes_error_on_raise(self, mock_open):
        mock_fp = mock.MagicMock()
        mock_fp.read.return_value = b"Internal Server Error"
        err_500 = urllib.error.HTTPError(
            "https://api.discogs.com/test", 500, "Server Error", {}, mock_fp
        )
        mock_open.side_effect = err_500

        dc = crate.Discogs("dummy-token")
        with self.assertRaises(RuntimeError):
            dc.call("GET", "/test")

        self.assertTrue(mock_fp.close.called)


class CmdDupesResourceTests(unittest.TestCase):
    @mock.patch("crate.client")
    def test_cmd_dupes_utf8_encoding(self, mock_client):
        mock_dc = mock.MagicMock()
        mock_dc.username = "testuser"
        item1 = {
            "id": 101,
            "instance_id": 1,
            "folder_id": 1,
            "basic_information": {
                "id": 101,
                "master_id": 55,
                "artists": [{"name": "Bj\u00f6rk"}],
                "title": "Homogenic",
                "year": 1997,
                "labels": [{"name": "One Little Indian"}],
            },
        }
        item2 = {
            "id": 102,
            "instance_id": 2,
            "folder_id": 1,
            "basic_information": {
                "id": 102,
                "master_id": 55,
                "artists": [{"name": "Bj\u00f6rk"}],
                "title": "Homogenic",
                "year": 1997,
                "labels": [{"name": "One Little Indian"}],
            },
        }
        mock_dc.collection_items.return_value = [item1, item2]
        mock_client.return_value = mock_dc

        args = mock.Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with redirect_stdout(io.StringIO()):
                    crate.cmd_dupes(args)
                csv_files = list(Path(".").glob("dupes-*.csv"))
                self.assertEqual(len(csv_files), 1)
                content = csv_files[0].read_text(encoding="utf-8")
                self.assertIn("Bj\u00f6rk", content)
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
