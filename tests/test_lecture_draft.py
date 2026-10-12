"""Local speech draft/revision lifecycle, using only original model fixtures."""
import json
import unittest
import test_lecture_runner as fixtures
import lecture_draft as draft


class LectureDraftTests(unittest.TestCase):
    def setUp(self):
        fixtures.LectureRunnerTests.setUp(self)
        fixtures.runner.generate_outline(self.sid, "synthetic", root=self.root, confirm_local_service=True)
        self.raw = {"paragraphs": [{"section_index": 0, "text": "An original synthetic spoken paragraph.", "evidence_ids": [], "fact_ids": []}]}
        self.response()

    response = fixtures.LectureRunnerTests.response

    def generate(self, revision=None):
        return draft.generate_draft(self.sid, "synthetic", root=self.root, confirm_local_service=True, revision=revision)

    def test_draft_and_revision_keep_old_artifacts(self):
        self.assertEqual(self.generate()["status"], "drafted")
        old = (self.path / "draft-current.json").read_bytes()
        pointer = json.loads(old)
        previous_text = (self.path / pointer["snapshot"] / "speech.md").read_bytes()
        self.raw["paragraphs"][0]["text"] = "A revised original synthetic paragraph."
        self.response()
        result = self.generate("Make the synthetic example clearer")
        self.assertTrue(result["revised"])
        self.assertEqual((self.path / pointer["snapshot"] / "speech.md").read_bytes(), previous_text)
        current, _ = draft.snapshot(self.path, "draft-current.json", "drafts", "draft.json")
        self.assertIn("revised", current["paragraphs"][0]["text"])
        self.assertIsNotNone(current["previous_pointer_sha256"])
        self.assertFalse(current["duration_verified"])
        loaded = json.loads(self.model.complete.call_args.args[0][1]["content"])
        self.assertEqual(loaded["previous_draft"]["paragraphs"][0]["text"], "An original synthetic spoken paragraph.")

    def test_bad_revision_keeps_committed_pointer_and_allows_retry(self):
        self.generate()
        old = (self.path / "draft-current.json").read_bytes()
        self.raw["paragraphs"][0]["fact_ids"] = ["F-9999"]
        self.response()
        with self.assertRaises(ValueError):
            self.generate("Try a clearer example")
        self.assertEqual((self.path / "draft-current.json").read_bytes(), old)
        self.assertFalse((self.path / ".lock").exists())
        self.raw["paragraphs"][0]["fact_ids"] = []
        self.response()
        self.assertTrue(self.generate("Try a clearer example")["revised"])

    def test_explicit_revision_and_valid_section_required(self):
        with self.assertRaises(ValueError):
            self.generate("No prior draft")
        self.raw["paragraphs"][0]["section_index"] = 4
        self.response()
        with self.assertRaises(ValueError):
            self.generate()
        self.raw["paragraphs"][0]["section_index"] = 0
        self.response()
        self.generate()
        count = self.model.complete.call_count
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.model.complete.call_count, count)

    def test_source_snapshot_corruption_refused_before_model_call(self):
        pointer = json.loads((self.path / "outline-current.json").read_text(encoding="utf-8"))
        (self.path / pointer["snapshot"] / "outline.json").write_text("{}", encoding="utf-8")
        count = self.model.complete.call_count
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.model.complete.call_count, count)


if __name__ == "__main__":
    unittest.main()
