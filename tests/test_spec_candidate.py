"""Persona specification candidates use only original fictional test evidence."""
import copy
import unittest

import test_review_merge as fixtures
from spec_candidate import normalize_spec, spec_material, verify_spec


class SpecCandidateTests(unittest.TestCase):
    source = fixtures.ReviewMergeTests.source
    normalize = fixtures.ReviewMergeTests.normalize
    merge = fixtures.ReviewMergeTests.merge

    def setUp(self):
        fixtures.ReviewMergeTests.setUp(self)
        self.catalog = self.merge([self.source()])
        claim = {"description": "The fictional teacher tests explanations.", "limitations": "One original fixture only",
                 "record_ids": ["E-0001"]}
        self.spec = {"profile": claim, "attention": [copy.deepcopy(claim)], "judgment": [], "dialogue_style": [],
                     "knowledge_limits": [], "language": {"mode": "undetermined", "adjustment": "Need more voice evidence",
                                                            "record_ids": ["E-0001"]},
                     "anchors": [{"name": "Small experiment", "trigger": "A confusing result", "move": "Try a small test",
                                  "misuse": "Do not force experiments into every chat", "record_ids": ["M-0001"]}],
                     "examples": [{"user": "My toy model failed", "assistant": "Which one thing could we test?",
                                   "record_ids": ["E-0001", "M-0001"]}], "gaps": ["No independent voice material"]}

    def test_grounded_candidate_is_not_a_complete_persona(self):
        before = copy.deepcopy(self.spec)
        result = normalize_spec(self.spec, self.catalog)
        self.assertFalse(result["persona_generated"])
        self.assertFalse(result["avatar_ready"])
        self.assertFalse(result["semantic_review_completed"])
        self.assertEqual(result["candidate"]["examples"][0]["provenance"], "original_simulation_not_quote")
        self.assertEqual(result["behavior_mode"], "optional_tendencies_not_response_template")
        self.assertEqual(self.spec, before)
        self.assertEqual(set(result["record_sha256"]), {"E-0001", "M-0001"})

    def test_unknown_reference_in_each_section_rejected(self):
        for key in ("profile", "attention", "language", "anchors", "examples"):
            spec = copy.deepcopy(self.spec)
            target = spec[key][0] if isinstance(spec[key], list) else spec[key]
            target["record_ids"] = ["E-9999"]
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_spec(spec, self.catalog)

    def test_policy_identity_and_review_fields_cannot_be_injected(self):
        for key in ("policy", "person_id", "review_status", "avatar"):
            spec = {**self.spec, key: "synthetic injection"}
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_spec(spec, self.catalog)

    def test_unresolved_and_fully_withdrawn_catalogs_rejected(self):
        history = [self.source(edit="A"), self.source(edit="B")]
        for catalog in (self.merge(history), self.merge([self.source()], chunks=[])):
            with self.assertRaises(ValueError):
                normalize_spec(self.spec, catalog)

    def test_inactive_record_not_exposed_as_model_material(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["entries"][1].update(state="withdrawn", active_record=None)
        self.assertEqual([r["record_id"] for r in spec_material(catalog)], ["E-0001"])
        with self.assertRaises(ValueError):
            normalize_spec(self.spec, catalog)

    def test_empty_sections_allowed_but_whitespace_rejected(self):
        spec = copy.deepcopy(self.spec)
        spec["attention"], spec["anchors"], spec["examples"] = [], [], []
        self.assertEqual(normalize_spec(spec, self.catalog)["candidate"]["anchors"], [])
        spec["profile"]["description"] = " "
        with self.assertRaises(ValueError):
            normalize_spec(spec, self.catalog)

    def test_normalized_spec_reconstructs_original_payload(self):
        candidate = normalize_spec(self.spec, self.catalog)
        self.assertEqual(verify_spec(candidate, self.catalog), self.spec)

    def test_changed_example_provenance_and_model_completion_flags_rejected(self):
        for change in (lambda c: c["candidate"]["examples"][0].update(provenance="real_quote"),
                       lambda c: c.update(persona_generated=True),
                       lambda c: c.update(record_sha256={})):
            candidate = normalize_spec(self.spec, self.catalog)
            change(candidate)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_spec(candidate, self.catalog)
        spec = copy.deepcopy(self.spec)
        spec["examples"][0]["provenance"] = "historical_quote"
        with self.assertRaises(ValueError):
            normalize_spec(spec, self.catalog)


if __name__ == "__main__":
    unittest.main()
