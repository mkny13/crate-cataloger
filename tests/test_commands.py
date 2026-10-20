import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402


def cand(ref, title, release_id=None, master_id=None, year=None):
    return {"ref": ref, "title": title, "release_id": release_id, "master_id": master_id, "year": year}


class QuickPick(unittest.TestCase):
    def setUp(self):
        self.dc = mock.Mock()
        self.dc.main_release.return_value = 999
        self.cands = [cand("r1", "A - One", release_id=1, year=1990), cand("r2", "B - Two", release_id=2)]
        self.coll = (set(), set())

    def pick(self, choice, cands=None, coll=None):
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = crate._quick_pick(self.dc, choice, self.cands if cands is None else cands,
                                    self.coll if coll is None else coll)
        return res, buf.getvalue()

    def test_enter_picks_first(self):
        res, _ = self.pick("")
        self.assertEqual(res, (1, None, "r1 · A - One (1990)"))

    def test_out_of_range_skips(self):
        res, out = self.pick("3")
        self.assertIsNone(res)
        self.assertEqual(out, "  no such option, skipped\n\n")

    def test_s_skips(self):
        res, out = self.pick("s")
        self.assertIsNone(res)
        self.assertEqual(out, "  skipped\n\n")

    def test_owned_declined(self):
        with mock.patch("builtins.input", return_value="n") as inp:
            res, out = self.pick("1", coll=({1}, set()))
        self.assertIsNone(res)
        self.assertEqual(out, "  skipped\n\n")
        inp.assert_called_once_with("  Already owned. Add a second copy? [y/N] ")

    def test_owned_accepted(self):
        with mock.patch("builtins.input", return_value="y"):
            res, _ = self.pick("1", coll=({1}, set()))
        self.assertEqual(res[0], 1)

    def test_master_candidate(self):
        cands = [cand("m50", "C - Three", master_id=50)]
        res, _ = self.pick("1", cands=cands)
        self.dc.main_release.assert_called_once_with(50)
        self.assertEqual(res, (999, 50, "m50 · C - Three"))

    def test_url(self):
        url = "https://www.discogs.com/release/123-x"
        res, _ = self.pick(url)
        self.assertEqual(res, (123, None, url))

    def test_bad_pick_raises(self):
        with self.assertRaises(ValueError):
            self.pick("zzz")


def item(rid, artist, title, master_id=0, formats=None, instance_id=None):
    bi = {"id": rid, "master_id": master_id, "artists": [{"name": artist}], "title": title,
          "year": 2000, "labels": [{"name": "Lbl"}]}
    if formats is not None:
        bi["formats"] = formats
    return {"id": rid, "instance_id": instance_id, "folder_id": 1, "basic_information": bi}


class DupeGroups(unittest.TestCase):
    def test_master_group_and_single(self):
        items = [item(1, "A", "X", master_id=7), item(2, "A", "X", master_id=7), item(3, "B", "Y")]
        groups = crate._dupe_groups(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual([it["id"] for it in groups[0]], [1, 2])

    def test_same_release_no_master(self):
        items = [item(5, "A", "X", instance_id=1), item(5, "A", "X", instance_id=2)]
        groups = crate._dupe_groups(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_sorted_by_artist_then_title(self):
        items = [item(1, "b", "Z", master_id=1), item(2, "b", "Z", master_id=1),
                 item(3, "A", "Y", master_id=2), item(4, "A", "Y", master_id=2)]
        groups = crate._dupe_groups(items)
        self.assertEqual([g[0]["id"] for g in groups], [3, 1])


class DupeRow(unittest.TestCase):
    def test_format_with_descriptions(self):
        it = item(10, "A", "X", formats=[{"name": "Vinyl", "descriptions": ["LP", "Album"]}])
        row = crate._dupe_row(it, [it, it])
        self.assertEqual(len(row), 11)
        self.assertEqual(row[9], "Vinyl LP, Album")
        self.assertEqual(row[2], 2)
        self.assertEqual(row[3], "yes")

    def test_no_formats(self):
        it = item(11, "A", "X")
        other = item(12, "A", "X")
        row = crate._dupe_row(it, [it, other])
        self.assertEqual(row[9], "")
        self.assertEqual(row[3], "no")

    def test_url(self):
        it = item(42, "A", "X")
        self.assertEqual(crate._dupe_row(it, [it])[10], "https://www.discogs.com/release/42")


if __name__ == "__main__":
    unittest.main()
