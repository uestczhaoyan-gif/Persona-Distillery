"""Lecture HTTP workflow uses real backend stages and original model fixtures."""
import json
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import test_local_app as fixtures
import lecture_api


class LectureAppTests(unittest.TestCase):
    setUp = fixtures.LocalAppTests.setUp
    close = fixtures.LocalAppTests.close
    request = fixtures.LocalAppTests.request

    def create(self):
        value = self.request("/api/lectures", {"person_id": "kongzi", "topic": "An original synthetic lecture topic",
                                             "audience": "Synthetic learners", "goal": "A synthetic explanation", "minutes": 10,
                                             "as_of": "2026-10-13", "facts": [], "preview": True})
        self.sid = value["session_id"]

    def action(self, name, **extra):
        return self.request(f"/api/lectures/{self.sid}/{name}", {"model": "synthetic", "confirm_local_service": True, **extra})

    def wait_job(self):
        for _ in range(100):
            value = self.request(f"/api/lectures/{self.sid}")
            if value["job"]["status"] != "running":
                return value
            time.sleep(.03)
        self.fail("Synthetic lecture job failed to finish")

    def test_http_outline_draft_revision_and_artifacts(self):
        self.assertIn("保存演讲简报", self.request("/lecture"))
        self.assertEqual(len(self.request("/api/lecture-library")["personas"]), 2)
        self.create()
        with patch.object(lecture_api.outlines, "OllamaProvider") as provider:
            model = provider.return_value
            model.inspect_identity.return_value = {"model": "synthetic", "digest": "a"*64}
            model.last_metrics = {"done_reason": "stop"}
            outline = {"title": "Original synthetic title", "thesis": "An original synthetic thesis", "scope": "edge",
                       "scope_reason": "Synthetic limited coverage", "research_questions": [],
                       "sections": [{"heading": "Synthetic explanation", "purpose": "Explain the synthetic idea", "minutes": 10,
                                     "evidence_ids": [], "fact_ids": []}]}
            model.complete.return_value = {"content": json.dumps(outline)}
            self.action("outline")
            self.assertEqual(self.wait_job()["status"], "outlined")
            raw = {"paragraphs": [{"section_index": 0, "text": "Original first synthetic speech.", "evidence_ids": [], "fact_ids": []}]}
            model.complete.return_value = {"content": json.dumps(raw)}
            self.action("draft")
            self.assertEqual(self.wait_job()["status"], "drafted")
            raw["paragraphs"][0]["text"] = "Original revised synthetic speech."
            model.complete.return_value = {"content": json.dumps(raw)}
            self.action("revise", text="Clarify the example")
            result = self.wait_job()
            self.assertEqual(result["job"]["status"], "done")
            self.assertIn("revised", result["draft"]["paragraphs"][0]["text"])
            self.assertEqual(model.complete.call_count, 3)
        self.assertIn("revised", self.request(f"/api/lecture-artifacts/{self.sid}/speech.md")["content"])
        self.assertIn("段落 1", self.request(f"/api/lecture-artifacts/{self.sid}/provenance.md")["content"])
        self.assertNotIn("pins", self.request(f"/api/lectures/{self.sid}")["brief"])
        self.assertEqual(self.request("/api/lectures")["sessions"][0]["status"], "drafted")
        self.assertEqual(lecture_api.Lectures(self.root).view(self.sid)["draft"], result["draft"])

    def test_failed_outline_preserves_prepared_brief_and_hides_raw_error(self):
        self.create()
        with patch.object(lecture_api.outlines, "generate_outline", side_effect=ValueError("SYNTHETIC_RAW_INTERNAL_OUTPUT")):
            self.action("outline")
            value = self.wait_job()
        self.assertEqual(value["status"], "prepared")
        self.assertEqual(value["job"]["status"], "failed")
        self.assertNotIn("SYNTHETIC_RAW", json.dumps(value))
        self.assertIn("An original synthetic lecture topic", self.request(f"/api/lecture-artifacts/{self.sid}/00-brief.md")["content"])

    def test_stage_and_artifact_boundaries(self):
        self.create()
        for action in ("draft", "revise"):
            with self.assertRaises(HTTPError):
                self.action(action, text="Synthetic revision")
        for name in ("input.json", "manifest.json", "speech.md"):
            with self.assertRaises(HTTPError):
                self.request(f"/api/lecture-artifacts/{self.sid}/{name}")
        with self.assertRaises(HTTPError):
            self.request(f"/api/lectures/{self.sid}/outline", {"model": "synthetic"})


if __name__ == "__main__":
    unittest.main()
