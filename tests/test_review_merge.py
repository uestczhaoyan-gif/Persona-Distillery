"""Stable merge tests use original fictional material and synthetic review decisions."""
import copy
import unittest

import test_candidate_records as fixtures
from candidate_records import normalize_candidates, fingerprint
from review_merge import merge_history


class ReviewMergeTests(unittest.TestCase):
    setUp = fixtures.CandidateRecordsTests.setUp
    normalize = fixtures.CandidateRecordsTests.normalize

    def source(self, *, edit=None, reject=False, payload=None):
        bundle = normalize_candidates(payload or self.payload, [self.chunk], self.policy)
        decisions = [{"record_id": item["record_id"], "action": "reject" if reject else "accept", "reason": "Synthetic review"}
                     for item in bundle["lineage"]]
        if edit is not None:
            replacement = copy.deepcopy(self.evidence)
            replacement["claim"] = edit
            decisions[0].update(action="edit", replacement=replacement)
        return {"bundle": bundle, "chunks": [copy.deepcopy(self.chunk)], "policy": copy.deepcopy(self.policy),
                "decision": {"candidates_sha256": fingerprint(bundle), "reviewer": "Synthetic fixture reviewer",
                             "note": "Not a real quality review", "decisions": decisions}}

    def merge(self, history, chunks=None, **kwargs):
        return merge_history(history, [self.chunk] if chunks is None else chunks, self.policy, **kwargs)

    def test_human_edit_survives_rebuild_with_stable_ids(self):
        source = self.source(edit="Edited fictional claim")
        before = copy.deepcopy(source)
        first = self.merge([source])
        second = self.merge([source], previous_history=first["history_sha256"])
        self.assertEqual(first, second)
        self.assertEqual(source, before)
        self.assertEqual(first["entries"][0]["active_record"]["claim"], "Edited fictional claim")
        self.assertEqual(first["entries"][0]["active_record"]["evidence_id"], "E-0001")

    def test_new_batch_numbering_does_not_replace_existing_ids(self):
        first_source = self.source()
        payload = copy.deepcopy(self.payload)
        new = copy.deepcopy(self.evidence)
        new["claim"] = "A different fictional observation"
        payload["evidence"].insert(0, new)
        second_source = self.source(payload=payload)
        first = self.merge([first_source])
        result = self.merge([first_source, second_source], previous_history=first["history_sha256"])
        entries = {entry["stable_id"]: entry for entry in result["entries"]}
        self.assertEqual(entries["E-0001"]["active_record"]["claim"], self.evidence["claim"])
        self.assertEqual(entries["E-0002"]["active_record"]["claim"], new["claim"])
        self.assertEqual(len(entries["E-0001"]["variants"]), 2)

    def test_conflicting_revision_preserves_both_and_disables_active_use(self):
        first = self.source(edit="Human revision A")
        second = self.source(edit="Human revision B")
        result = self.merge([first, second])
        entry = result["entries"][0]
        self.assertEqual(entry["state"], "review_required")
        self.assertIsNone(entry["active_record"])
        self.assertEqual([v["record"]["claim"] for v in entry["variants"]], ["Human revision A", "Human revision B"])

    def test_rejection_after_acceptance_is_conflict_not_silent_deletion(self):
        result = self.merge([self.source(), self.source(reject=True)])
        self.assertEqual(result["counts"]["review_required"], 2)
        self.assertTrue(all(e["active_record"] is None for e in result["entries"]))
        rejected = self.merge([self.source(reject=True)])
        self.assertEqual(rejected["counts"]["rejected"], 2)

    def test_source_withdrawal_disables_content_without_erasing_human_edits(self):
        source = self.source(edit="Keep original human work")
        result = self.merge([source], chunks=[])
        self.assertEqual(result["counts"]["withdrawn"], 2)
        self.assertEqual(result["entries"][0]["variants"][0]["record"]["claim"], "Keep original human work")
        self.assertTrue(all(e["active_record"] is None for e in result["entries"]))

    def test_reordering_or_dropping_history_prefix_is_rejected(self):
        a, b = self.source(), self.source(edit="Another revision")
        first = self.merge([a, b])
        for history in ([b, a], [a], [a, a]):
            with self.subTest(history=history), self.assertRaises(ValueError):
                self.merge(history, previous_history=first["history_sha256"])

    def test_corrupt_review_and_changed_chunk_checksum_rejected(self):
        source = self.source()
        source["decision"]["candidates_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.merge([source])
        chunk = copy.deepcopy(self.chunk)
        chunk["text"] = "Changed synthetic body"
        with self.assertRaises(ValueError):
            self.merge([self.source()], chunks=[chunk])

    def test_sensitive_current_material_propagates(self):
        chunk = copy.deepcopy(self.chunk)
        chunk["privacy_level"] = "sensitive"
        result = self.merge([self.source()], chunks=[chunk])
        self.assertTrue(result["policy"]["sensitive_data"])
        self.assertEqual(result["policy"]["distribution_policy"]["mode"], "local_only")
        self.assertFalse(result["publication_approved"])

    def choice(self, result, position=1):
        entry = result["entries"][0]
        return {"origin_sha256": entry["origin_sha256"], "variants_sha256": fingerprint(entry["variants"]),
                "chosen_history_sha256": entry["variants"][position]["history_sha256"],
                "reviewer": "Synthetic resolver", "reason": "Fixture choice, not actual review"}

    def test_explicit_choice_keeps_both_revisions_and_activates_selected(self):
        history = [self.source(edit="A"), self.source(edit="B")]
        unresolved = self.merge(history)
        choice = self.choice(unresolved)
        result = self.merge(history, resolutions=[choice])
        entry = result["entries"][0]
        self.assertEqual(entry["state"], "active")
        self.assertEqual(entry["active_record"]["claim"], "B")
        self.assertEqual(entry["variants"], unresolved["entries"][0]["variants"])
        self.assertTrue(entry["conflict"])
        self.assertEqual(entry["resolution"], choice)

    def test_choice_of_rejection_does_not_delete_old_content(self):
        history = [self.source(), self.source(reject=True)]
        result = self.merge(history, resolutions=[self.choice(self.merge(history))])
        self.assertEqual(result["entries"][0]["state"], "rejected")
        self.assertIsNotNone(result["entries"][0]["variants"][0]["record"])

    def test_new_version_invalidates_old_resolution(self):
        history = [self.source(edit="A"), self.source(edit="B")]
        choice = self.choice(self.merge(history))
        with self.assertRaisesRegex(ValueError, "Conflict changed"):
            self.merge(history + [self.source(edit="C")], resolutions=[choice])

    def test_choice_never_revives_withdrawn_material(self):
        history = [self.source(edit="A"), self.source(edit="B")]
        choice = self.choice(self.merge(history))
        result = self.merge(history, chunks=[], resolutions=[choice])
        self.assertEqual(result["entries"][0]["state"], "withdrawn")
        self.assertIsNone(result["entries"][0]["active_record"])

    def test_unknown_or_duplicate_choice_is_rejected(self):
        history = [self.source(edit="A"), self.source(edit="B")]
        choice = self.choice(self.merge(history))
        for choices in ([choice, choice], [{**choice, "chosen_history_sha256": "0" * 64}],
                        [{**choice, "origin_sha256": "0" * 64}], [{**choice, "reviewer": ""}]):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                self.merge(history, resolutions=choices)


if __name__ == "__main__":
    unittest.main()
