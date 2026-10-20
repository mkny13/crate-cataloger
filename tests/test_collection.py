import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402


def release(release_id, master_id=None):
    basic = {"id": release_id}
    if master_id:
        basic["master_id"] = master_id
    return {"id": release_id, "basic_information": basic}


class CollectionReleases(unittest.TestCase):
    def discogs(self, responses):
        dc = crate.Discogs("token")
        dc._user = "u"
        calls = []

        def call(method, path, params):
            calls.append((method, path, params))
            return responses[params["page"] - 1]

        dc.call = call
        return dc, calls

    def test_items_and_index_consume_all_pages(self):
        responses = [
            {"releases": [release(1, 10)], "pagination": {"pages": 3}},
            {"releases": [release(2), release(3, 30)], "pagination": {"pages": 3}},
            {"releases": [release(4, 10)], "pagination": {"pages": 3}},
        ]
        dc, calls = self.discogs(responses)
        self.assertEqual([item["id"] for item in dc.collection_items()], [1, 2, 3, 4])
        self.assertEqual(len(calls), 3)

        dc, calls = self.discogs(responses)
        self.assertEqual(dc.collection_index(), ({1, 2, 3, 4}, {10, 30}))
        self.assertEqual(len(calls), 3)

    def test_empty_first_response_returns_empty_collections(self):
        dc, calls = self.discogs([None])
        self.assertEqual(dc.collection_items(), [])
        self.assertEqual(len(calls), 1)

        dc, calls = self.discogs([None])
        self.assertEqual(dc.collection_index(), (set(), set()))
        self.assertEqual(len(calls), 1)


class Dedupe(unittest.TestCase):
    def test_keeps_first_master_and_distinct_release_references(self):
        cands = [
            {"ref": "r1", "master_id": 7},
            {"ref": "r2", "master_id": 7},
            {"ref": "r3", "master_id": None},
            {"ref": "r4", "master_id": None},
        ]
        self.assertEqual([c["ref"] for c in crate._dedupe(cands)], ["r1", "r3", "r4"])

    def test_find_prefers_fixed_candidates_and_limits_results(self):
        initial = [
            {"ref": "m1", "master_id": 1, "score": 0.2},
            {"ref": "r3", "master_id": None, "score": 0.1},
            {"ref": "r4", "master_id": None, "score": 0.1},
            {"ref": "r5", "master_id": None, "score": 0.1},
            {"ref": "r6", "master_id": None, "score": 0.1},
        ]
        fixed = [
            {"ref": "m2", "master_id": 2, "score": 0.9},
            {"ref": "r1", "master_id": 1, "score": 0.8},
        ]
        with mock.patch.object(crate, "_search", side_effect=[initial, fixed]), \
             mock.patch.object(crate, "spell_fix", return_value="corrected"):
            cands = crate.find(mock.Mock(), "misspelled")

        self.assertEqual([c["ref"] for c in cands], ["m2", "r1", "r3", "r4"])
        self.assertEqual(cands[0]["fixed"], "corrected")
        self.assertLessEqual(len(cands), crate.N_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
