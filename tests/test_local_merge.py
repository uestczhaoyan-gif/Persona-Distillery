"""External incremental merge integration, using only synthetic original sources."""
import json
import shutil
import unittest
from unittest.mock import patch

import test_local_candidates as fixtures
import candidate_review as review
import local_merge as merge


class LocalMergeTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation"):
        locals()[_name] = getattr(fixtures.LocalCandidateTests, _name)

    def make_review(self):
        result = self.run_generation()
        current = review.read_run(self.library, self.job["job"]["id"], result["run_sha256"], confirm_local_storage=True)
        decision = {"candidates_sha256": current["candidates_sha256"], "reviewer": "Synthetic reviewer", "note": "Fixture only",
                    "decisions": [{"record_id": entry["record_id"], "action": "accept", "reason": "Synthetic decision"}
                                  for entry in current["bundle"]["lineage"]]}
        return review.save_review(self.library, self.job["job"]["id"], result["run_sha256"], decision,
                                  confirm_local_storage=True)["review_sha256"]

    def prepare(self):
        self.ready()
        provider = self.provider()
        def response(messages, tools):
            chunks = json.loads(messages[1]["content"])["chunks"]
            return {"content": json.dumps({"evidence": [{"card_type": "synthesis", "claim": chunk["text"],
                        "evidence_summary": "Synthetic source", "interpretation": "Tentative", "limitations": "Fixture only",
                        "chunk_ids": [chunk["chunk_id"]], "topics": [], "confidence": "low"} for chunk in chunks],
                        "memories": [], "gaps": []})}
        provider.complete.side_effect = response
        return self.make_review()

    def merge(self, ids, previous=None):
        return merge.save_merge(self.library, self.job["job"]["id"], ids, previous_merge=previous, confirm_local_storage=True)

    def catalog(self, result):
        return merge.read_snapshot(self.library / self.job["job"]["id"], result["merge_sha256"])["catalog"]

    def test_incremental_material_retains_old_record_id(self):
        first_review = self.prepare()
        first = self.merge([first_review])
        self.assertEqual(self.merge([first_review]), first)
        (self.incoming / "second.txt").write_text("Additional original synthetic source", encoding="utf-8")
        self.approve("material")
        self.build(update=True)
        second_review = self.make_review()
        result = self.merge([first_review, second_review], first["merge_sha256"])
        catalog = self.catalog(result)
        self.assertEqual(catalog["counts"]["active"], 2)
        self.assertEqual(catalog["entries"][0]["stable_id"], "E-0001")
        self.assertEqual(catalog["entries"][0]["active_record"]["claim"], "SYNTHETIC PRIVATE SOURCE")
        self.assertEqual(len(catalog["entries"][0]["variants"]), 2)

    def test_removed_source_stops_active_use_but_preserves_history(self):
        first_review = self.prepare()
        first = self.merge([first_review])
        (self.incoming / "source.txt").unlink()
        (self.incoming / "new.txt").write_text("Replacement synthetic source", encoding="utf-8")
        self.approve("material")
        self.build(update=True)
        result = self.merge([first_review], first["merge_sha256"])
        self.assertEqual(result["counts"]["withdrawn"], 1)
        self.assertIsNone(self.catalog(result)["entries"][0]["active_record"])
        with self.assertRaises(ValueError):
            review.read_review(self.library, self.job["job"]["id"], first_review, confirm_local_storage=True)

    def test_history_corruption_blocks_merge(self):
        review_id = self.prepare()
        directory = self.library / self.job["job"]["id"]
        path = directory / "reviews" / review_id / "review.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.merge([review_id])
        self.assertFalse((directory / "merges").exists())

    def test_storage_failure_is_preserved_not_reused(self):
        review_id = self.prepare()
        original = merge.local.write_new
        def fail(path, data):
            if path.name == "merge.json":
                raise OSError("Synthetic disk error")
            return original(path, data)
        with patch.object(merge.local, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.merge([review_id])
        output = next((self.library / self.job["job"]["id"] / "merges").iterdir())
        self.assertTrue((output / ".incomplete").exists())
        with self.assertRaises(ValueError):
            self.merge([review_id])

    def test_confirmation_before_private_read(self):
        with self.assertRaises(ValueError):
            merge.save_merge(self.library, self.job["job"]["id"], [])

    def test_copied_other_job_history_is_rejected(self):
        review_id = self.prepare()
        old = self.library / self.job["job"]["id"]
        self.job["job"]["id"] = "second-synthetic-job"
        self.write_config()
        self.ready()
        directory = self.library / self.job["job"]["id"]
        for name in ("reviews", "candidates"):
            shutil.copytree(old / name, directory / name)
        for snapshot in (old / "snapshots").iterdir():
            shutil.copytree(snapshot, directory / "snapshots" / snapshot.name)
        with self.assertRaisesRegex(ValueError, "another job"):
            self.merge([review_id])

    def test_source_changed_during_save_leaves_incomplete_snapshot(self):
        review_id = self.prepare()
        original = merge.local.write_new
        def changed(path, data):
            original(path, data)
            if path.name == "merge.json":
                (self.incoming / "source.txt").write_text("Changed synthetic source", encoding="utf-8")
        with patch.object(merge.local, "write_new", side_effect=changed):
            with self.assertRaises(ValueError):
                self.merge([review_id])
        output = next((self.library / self.job["job"]["id"] / "merges").iterdir())
        self.assertTrue((output / ".incomplete").exists())


if __name__ == "__main__":
    unittest.main()
