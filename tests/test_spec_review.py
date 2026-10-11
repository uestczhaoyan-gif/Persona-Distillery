"""Local specification reviews use synthetic candidates and synthetic human decisions."""
import copy
import json
import unittest
from unittest.mock import patch

import test_local_spec as fixtures
import spec_review as review


class SpecReviewTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation", "make_review", "prepare", "merge", "setup_spec", "generate"):
        locals()[_name] = getattr(fixtures.LocalSpecTests, _name)

    def setup_review(self):
        self.setup_spec()
        self.spec_id = self.generate()["spec_sha256"]
        self.current = review.read_spec(self.library, self.job["job"]["id"], self.spec_id, confirm_local_storage=True)
        self.decision = {"spec_content_sha256": self.current["spec_content_sha256"], "action": "accept",
                         "reviewer": "Synthetic reviewer", "note": "Fixture only, not real assessment"}

    def save_review(self):
        return review.save_spec_review(self.library, self.job["job"]["id"], self.spec_id,
                                       self.decision, confirm_local_storage=True)

    def test_accept_read_and_idempotence_do_not_publish(self):
        self.setup_review()
        saved = self.save_review()
        self.assertEqual(self.save_review(), saved)
        value = review.read_spec_review(self.library, self.job["job"]["id"], saved["review_sha256"], confirm_local_storage=True)
        self.assertEqual(value["result"]["candidate"], self.current["candidate"])
        self.assertFalse(value["result"]["publication_approved"])
        self.assertFalse(value["result"]["independent_review_verified"])

    def test_human_edit_preserves_original_candidate(self):
        self.setup_review()
        before = copy.deepcopy(self.current)
        replacement = copy.deepcopy(self.payload)
        replacement["profile"]["description"] = "Human-refined synthetic profile"
        self.decision.update(action="edit", replacement=replacement)
        saved = self.save_review()
        value = review.read_spec_review(self.library, self.job["job"]["id"], saved["review_sha256"], confirm_local_storage=True)
        self.assertEqual(value["result"]["candidate"]["candidate"]["profile"]["description"], replacement["profile"]["description"])
        self.assertEqual(review.read_spec(self.library, self.job["job"]["id"], self.spec_id, confirm_local_storage=True), before)

    def test_reject_has_no_usable_candidate(self):
        self.setup_review()
        self.decision["action"] = "reject"
        saved = self.save_review()
        value = review.read_spec_review(self.library, self.job["job"]["id"], saved["review_sha256"], confirm_local_storage=True)
        self.assertIsNone(value["result"]["candidate"])

    def test_source_change_invalidates_spec_and_review(self):
        self.setup_review()
        saved = self.save_review()
        (self.incoming / "source.txt").unlink()
        with self.assertRaises(ValueError):
            review.read_spec(self.library, self.job["job"]["id"], self.spec_id, confirm_local_storage=True)
        with self.assertRaises(ValueError):
            review.read_spec_review(self.library, self.job["job"]["id"], saved["review_sha256"], confirm_local_storage=True)

    def test_tampered_derived_fields_fail_even_after_rehash(self):
        self.setup_review()
        output = self.library / self.job["job"]["id"] / "specs" / self.spec_id
        path = output / "spec-candidate.json"
        candidate = json.loads(path.read_text(encoding="utf-8"))
        candidate["avatar_ready"] = True
        path.write_text(json.dumps(candidate), encoding="utf-8")
        index = json.loads((output / "checksums.json").read_text(encoding="utf-8"))
        index[path.name] = review.local.sha(path)
        (output / "checksums.json").write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(ValueError):
            review.read_spec(self.library, self.job["job"]["id"], self.spec_id, confirm_local_storage=True)

    def test_unknown_edited_record_and_stale_hash_rejected(self):
        self.setup_review()
        decision = copy.deepcopy(self.decision)
        decision["spec_content_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            review.prepare_decision(self.current, decision)
        replacement = copy.deepcopy(self.payload)
        replacement["profile"]["record_ids"] = ["E-9999"]
        decision = {**self.decision, "action": "edit", "replacement": replacement}
        with self.assertRaises(ValueError):
            review.prepare_decision(self.current, decision)

    def test_incomplete_save_is_retained_and_refuses_reuse(self):
        self.setup_review()
        original = review.local.write_new
        def fail(path, data):
            if path.name == "review.json":
                raise OSError("Synthetic disk failure")
            return original(path, data)
        with patch.object(review.local, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.save_review()
        output = next((self.library / self.job["job"]["id"] / "spec-reviews").iterdir())
        self.assertTrue((output / ".incomplete").exists())
        with self.assertRaises(ValueError):
            self.save_review()


if __name__ == "__main__":
    unittest.main()
