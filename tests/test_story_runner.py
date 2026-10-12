"""Local character/consequence role separation with original synthetic model replies."""
import copy
import json
import unittest
from unittest.mock import patch
import test_story_state as fixtures
import story_runner as runner


class StoryRunnerTests(unittest.TestCase):
    setUp = fixtures.StoryStateTests.setUp
    state = fixtures.StoryStateTests.state
    apply = fixtures.StoryStateTests.apply

    def provider(self):
        constructor = patch.object(runner, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        self.model = constructor.return_value
        self.model.inspect_identity.return_value = {"model": "synthetic", "digest": "a"*64}
        self.model.last_metrics = {"done_reason": "stop"}
        self.raw = copy.deepcopy(self.proposal)
        for option in self.raw["options"]:
            option["evidence_ids"] = []
        self.model.complete.return_value = {"content": json.dumps(self.raw)}

    def generate(self):
        return runner.generate(self.sid, "synthetic", root=self.root, confirm_local_service=True)

    def test_actor_and_engine_use_distinct_information(self):
        self.provider()
        self.generate()
        actor = self.model.complete.call_args.args[0][1]["content"]
        self.assertNotIn("SYNTHETIC_HIDDEN", actor)
        self.assertIn('"character"', actor)
        self.apply("choose", 0)
        self.model.complete.return_value = {"content": json.dumps(self.effect)}
        self.generate()
        engine = self.model.complete.call_args.args[0][1]["content"]
        self.assertIn("SYNTHETIC_HIDDEN", engine)
        self.assertNotIn('"character"', engine)
        self.assertEqual(self.state()["resources"]["days"], 5)
        self.assertEqual(self.state()["history"][0]["choice"]["basis"]["modes"], ["simulation"])
        self.assertEqual(len(self.state()["generations"]), 2)

    def test_three_complete_model_decisions_end_with_six_calls(self):
        self.provider()
        for _ in range(3):
            self.model.complete.return_value = {"content": json.dumps(self.raw)}
            self.generate()
            self.apply("choose", 0)
            self.model.complete.return_value = {"content": json.dumps(self.effect)}
            self.generate()
        self.assertEqual(self.state()["status"], "ended")
        self.assertEqual(self.model.complete.call_count, 6)

    def test_pause_and_resume_reject_late_generation(self):
        self.provider()
        response = self.model.complete.return_value
        def late(*args):
            self.apply("pause")
            self.apply("resume")
            return response
        self.model.complete.side_effect = late
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state()["status"], "ready")
        self.assertIsNone(self.state()["pending"])

    def test_unloaded_citation_does_not_commit_and_can_retry(self):
        self.provider()
        self.raw["options"][0]["evidence_ids"] = ["E-9999"]
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state()["version"], 0)
        self.raw["options"][0]["evidence_ids"] = []
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        self.assertEqual(self.generate()["status"], "choosing")

    def test_explicit_confirmation_and_noninteractive_state_guard(self):
        self.provider()
        with self.assertRaises(ValueError):
            runner.generate(self.sid, "synthetic", root=self.root)
        self.generate()
        count = self.model.complete.call_count
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.model.complete.call_count, count)

    def test_loaded_evidence_sources_are_program_derived(self):
        self.provider()
        package = self.root / "personas/kongzi"
        manifest = fixtures.story.store.read_json(package / "manifest.json")
        rows = [json.loads(line) for line in (package / manifest["files"]["structured_evidence"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        evidence = next(row for row in rows if row.get("status") == "reviewed" and row.get("delivery_channel") != "local_only")
        self.raw["options"][0]["evidence_ids"] = [evidence["evidence_id"]]
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        with patch.object(runner, "retrieve_evidence", return_value=[evidence]):
            self.generate()
        basis = self.state()["pending"]["options"][0]["basis"]
        self.assertEqual(basis["source_ids"], sorted({r["source_id"] for r in evidence["source_refs"]}))
        self.assertEqual(basis["modes"], ["simulation", "inference"])


if __name__ == "__main__":
    unittest.main()
