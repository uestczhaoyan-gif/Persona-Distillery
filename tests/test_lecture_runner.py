"""Lecture outline generation with original synthetic model replies."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_lecture_prepare as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "定题演讲/scripts"))
import lecture_runner as runner


class LectureRunnerTests(unittest.TestCase):
    def setUp(self):
        fixtures.LecturePreparationTests.setUp(self)
        self.brief = runner.prep.prepare(self.config, root=self.root, preview=True)
        self.sid = self.brief["session_id"]
        self.path = self.root / "定题演讲/sessions" / self.sid
        self.raw = {"title": "Synthetic lecture", "thesis": "Original synthetic thesis", "scope": "edge",
                    "scope_reason": "Synthetic material limit", "research_questions": ["An original unresolved question?"],
                    "sections": [{"heading": "Synthetic opening", "purpose": "Explain a synthetic example", "minutes": 10,
                                  "evidence_ids": [], "fact_ids": []}]}
        constructor = patch.object(runner, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        self.model = constructor.return_value
        self.model.inspect_identity.return_value = {"model": "synthetic", "digest": "a"*64}
        self.model.last_metrics = {"done_reason": "stop"}
        self.response()

    def response(self):
        self.model.complete.return_value = {"content": json.dumps(self.raw)}

    def generate(self):
        return runner.generate_outline(self.sid, "synthetic", root=self.root, confirm_local_service=True)

    def test_outline_snapshot_and_repeat_refusal(self):
        result = self.generate()
        self.assertEqual(result["status"], "outlined")
        self.assertFalse(result["semantic_quality_verified"])
        pointer = runner.prep.read_json(self.path / "outline-current.json")
        for name, digest in pointer["checksums"].items():
            self.assertEqual(runner.storage.sha(self.path / pointer["snapshot"] / name), digest)
        value = runner.prep.read_json(self.path / pointer["snapshot"] / "outline.json")
        self.assertFalse(value["duration_verified"])
        loaded = json.loads(self.model.complete.call_args.args[0][1]["content"])
        self.assertEqual(loaded["fact_verification"], "supplied_not_independently_verified")
        self.assertNotIn("facts", loaded["persona"])
        with self.assertRaises(ValueError):
            self.generate()
        self.model.complete.assert_called_once()

    def test_unknown_citations_and_wrong_duration_never_commit(self):
        for key, value in (("evidence_ids", ["E-9999"]), ("fact_ids", ["F-9999"]), ("minutes", 11)):
            original = self.raw["sections"][0][key]
            self.raw["sections"][0][key] = value
            self.response()
            with self.assertRaises(ValueError):
                self.generate()
            self.assertFalse((self.path / "outline-current.json").exists())
            self.raw["sections"][0][key] = original

    def test_outside_topic_creates_redirection_not_outline_success(self):
        self.raw.update(scope="outside", sections=[])
        self.response()
        self.assertEqual(self.generate()["status"], "needs_topic_change")

    def test_input_changed_during_generation_rejects_late_result(self):
        response = self.model.complete.return_value
        def changed(*args):
            brief = runner.prep.read_json(self.path / "input.json")
            brief["topic"] = "Another original synthetic topic"
            (self.path / "input.json").write_text(json.dumps(brief), encoding="utf-8")
            return response
        self.model.complete.side_effect = changed
        with self.assertRaises(ValueError):
            self.generate()
        self.assertFalse((self.path / "outline-current.json").exists())

    def test_material_drift_and_missing_confirmation_never_call_model(self):
        with self.assertRaises(ValueError):
            runner.generate_outline(self.sid, "synthetic", root=self.root)
        manifest = runner.prep.read_json(self.root / "personas/kongzi/manifest.json")
        file = self.root / "personas/kongzi" / manifest["files"]["core_anchors"]
        file.write_text("Original altered fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.generate()
        self.model.complete.assert_not_called()

    def test_failure_releases_lock_and_keeps_brief_for_retry(self):
        original = (self.path / "input.json").read_bytes()
        self.model.complete.side_effect = OSError("Synthetic unavailable service")
        with self.assertRaises(ValueError):
            self.generate()
        self.assertFalse((self.path / ".lock").exists())
        self.assertEqual((self.path / "input.json").read_bytes(), original)
        self.model.complete.side_effect = None
        self.assertEqual(self.generate()["status"], "outlined")


if __name__ == "__main__":
    unittest.main()
