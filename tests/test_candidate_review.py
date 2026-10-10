"""Review loading uses only synthetic sources; file hashes are not semantic approval."""
import json
import unittest
from unittest.mock import patch

import test_local_candidates as fixtures
import test_candidate_records as records
import candidate_review as review
from candidate_records import verify_candidates


class CandidateVerificationTests(unittest.TestCase):
    setUp = records.CandidateRecordsTests.setUp
    normalize = records.CandidateRecordsTests.normalize

    def test_reconstruction_matches_original_payload(self):
        self.assertEqual(verify_candidates(self.normalize(), [self.chunk], self.policy), self.payload)

    def test_derived_fields_and_reviews_cannot_be_injected(self):
        mutations = (
            lambda b: b["evidence"][0].update(status="reviewed"),
            lambda b: b["evidence"][0]["source_refs"][0].update(locations=["invented"]),
            lambda b: b["lineage"][0].update(content_sha256="a" * 64),
            lambda b: b["lineage"].append(b["lineage"][0]),
            lambda b: b.update(semantic_review_completed=True),
            lambda b: b["context_memory"][0].update(source_ids=["P-2026-999"]),
        )
        for change in mutations:
            bundle = self.normalize()
            change(bundle)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_candidates(bundle, [self.chunk], self.policy)


class CandidateRunReadTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation"):
        locals()[_name] = getattr(fixtures.LocalCandidateTests, _name)

    def prepare_run(self):
        self.ready()
        self.provider()
        self.result = self.run_generation()
        self.output = self.library / self.job["job"]["id"] / "candidates" / self.result["run_sha256"]

    def read(self):
        return review.read_run(self.library, self.job["job"]["id"], self.result["run_sha256"], confirm_local_storage=True)

    def test_read_verifies_without_model_or_snapshot_writes(self):
        self.prepare_run()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        with patch("socket.socket.connect", side_effect=AssertionError("network")):
            value = self.read()
        self.assertFalse(value["semantic_review_completed"])
        self.assertEqual(value["bundle"]["status"], "candidate")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_incomplete_extra_and_corrupt_files_rejected(self):
        self.prepare_run()
        for name in (".incomplete", "unexpected.json"):
            marker = self.output / name
            marker.write_text("fixture", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.read()
            marker.unlink()
        (self.output / "candidates.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.read()

    def test_rehashed_structural_forgery_still_rejected(self):
        self.prepare_run()
        path = self.output / "candidates.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["semantic_review_completed"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        index_path = self.output / "checksums.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index[path.name] = review.local.sha(path)
        index_path.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.read()

    def test_material_withdrawal_blocks_old_candidates(self):
        self.prepare_run()
        (self.incoming / "source.txt").unlink()
        with self.assertRaises(ValueError):
            self.read()

    def test_confirmation_and_hash_precede_private_reads(self):
        with patch.object(review.local, "load_job", side_effect=AssertionError("read")):
            for run, confirmed in (("a" * 64, False), ("../escape", True)):
                with self.assertRaises(ValueError):
                    review.read_run(self.library, "fixture", run, confirm_local_storage=confirmed)


if __name__ == "__main__":
    unittest.main()
