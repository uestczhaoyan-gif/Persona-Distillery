import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "人物蒸馏/scripts"))
from candidate_records import normalize_candidates


class CandidateRecordsTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "人物蒸馏/examples/v2/local-fictional-upload.json").read_text(encoding="utf-8"))["policy"]
        body = "Original fictional teacher tried a small experiment and revised an explanation."
        self.chunk = {"schema_version": "1.0", "chunk_id": "P-2026-001#C-0001", "source_id": "P-2026-001",
                      "content_kind": "uploaded_text", "content_mode": "excerpt", "text": body,
                      "speaker": None, "date": None, "language": "en", "location": "lines 1-2",
                      "direct_quote": False, "topics": [], "privacy_level": "private",
                      "rights_mode": "local_only_text", "delivery_channel": "local_only",
                      "checksum": hashlib.sha256(body.encode()).hexdigest()}
        self.evidence = {"card_type": "decision_case", "claim": "The fictional teacher revised a model.",
                         "evidence_summary": "An original fixture describes an experiment.",
                         "interpretation": "May value feedback.", "limitations": "One invented scene only.",
                         "chunk_ids": [self.chunk["chunk_id"]], "topics": ["learning"], "confidence": "low"}
        self.memory = {"title": "Original experiment", "period": "unspecified", "situation": "A lesson failed.",
                       "observed_move": "Tried a smaller test.", "tension": "Explanation versus observation.",
                       "dialogue_use": "Ask what would change a model.", "chunk_ids": [self.chunk["chunk_id"]],
                       "topics": [], "confidence": "low"}
        self.payload = {"evidence": [self.evidence], "memories": [self.memory], "gaps": ["No corroborating scene."]}

    def normalize(self):
        return normalize_candidates(self.payload, [self.chunk], self.policy)

    def test_exact_lineage_and_schema_records_remain_candidates(self):
        original = copy.deepcopy((self.payload, self.chunk, self.policy))
        result = self.normalize()
        self.assertEqual(result, self.normalize())
        self.assertEqual(original, (self.payload, self.chunk, self.policy))
        self.assertEqual(result["evidence"][0]["source_refs"][0]["locations"], ["lines 1-2"])
        self.assertEqual(result["context_memory"][0]["source_ids"], ["P-2026-001"])
        self.assertEqual(result["lineage"][1]["chunk_refs"][0]["checksum"], self.chunk["checksum"])
        self.assertFalse(result["semantic_review_completed"])
        self.assertFalse(result["persona_generated"])
        self.assertEqual(result["delivery_channel"], "local_only")

    def test_model_cannot_inject_review_policy_location_or_identity(self):
        for key in ("status", "evidence_id", "source_refs", "policy", "delivery_channel"):
            with self.subTest(key=key):
                self.evidence[key] = "invented"
                with self.assertRaises(ValueError):
                    self.normalize()
                self.evidence.pop(key)

    def test_unknown_chunk_duplicate_ref_and_content_tamper_are_rejected(self):
        for refs in (["P-2026-999#C-0001"], [], [self.chunk["chunk_id"]] * 2):
            self.evidence["chunk_ids"] = refs
            with self.assertRaises(ValueError):
                self.normalize()
        self.evidence["chunk_ids"] = [self.chunk["chunk_id"]]
        self.chunk["text"] = "changed"
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.normalize()

    def test_metadata_only_cannot_support_candidate(self):
        self.chunk["content_mode"] = "metadata_only"
        with self.assertRaisesRegex(ValueError, "eligible"):
            self.normalize()

    def test_recollection_is_not_promoted_to_direct_fact(self):
        self.policy["subject_kind"] = "living_private"
        self.policy["material_basis"] = ["user_recollection"]
        self.evidence["card_type"] = "fact"
        with self.assertRaisesRegex(ValueError, "recollection"):
            self.normalize()
        self.evidence["card_type"] = "reliable_report"
        self.assertEqual(self.normalize()["policy"]["material_basis"], ["user_recollection"])

    def test_sensitive_input_propagates_and_output_never_becomes_public(self):
        self.chunk["privacy_level"] = "sensitive"
        result = self.normalize()
        self.assertTrue(result["policy"]["sensitive_data"])
        self.assertEqual(result["policy"]["distribution_policy"]["mode"], "local_only")

    def test_duplicate_records_and_excessive_payloads_are_rejected(self):
        self.payload["evidence"].append(copy.deepcopy(self.evidence))
        with self.assertRaisesRegex(ValueError, "Duplicate candidate"):
            self.normalize()
        self.payload["evidence"].pop()
        self.evidence["claim"] = "x" * 2001
        with self.assertRaisesRegex(ValueError, "limit"):
            self.normalize()

    def test_empty_candidates_can_report_missing_material_without_invention(self):
        self.payload = {"evidence": [], "memories": [], "gaps": ["Insufficient original material."]}
        result = self.normalize()
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["context_memory"], [])
        self.assertTrue(result["gaps"])

    def test_policy_is_validated_before_candidate_content(self):
        self.policy["policy_version"] = "99.0"
        with self.assertRaises(ValueError):
            normalize_candidates(None, None, self.policy)
