"""Local turn generation with original synthetic replies; no actual model calls."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_roundtable as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "圆桌会议/scripts"))
import roundtable_runner as runner


class RoundtableRunnerTests(unittest.TestCase):
    for _name in ("setUp", "create", "start", "state"):
        locals()[_name] = getattr(fixtures.RoundtableStateTests, _name)

    def provider(self):
        constructor = patch.object(runner, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        self.model = constructor.return_value
        self.model.inspect_identity.return_value = {"model": "synthetic", "digest": "a" * 64}
        self.model.last_metrics = {"done_reason": "stop"}
        self.raw = {"text": "An original synthetic contribution.", "responds_to": ["opening"], "stance": "opening",
                    "claims": [{"text": "A synthetic claim", "novelty": "new"}], "questions": [], "needs_fact_check": False}
        self.model.complete.return_value = {"content": json.dumps(self.raw)}

    def generate(self):
        return runner.generate_turn(self.identifier, "synthetic", root=self.root, confirm_local_service=True)

    def test_one_request_commits_turn_with_only_speakers_persona_context(self):
        self.start()
        self.provider()
        result = self.generate()
        self.assertTrue(result["accepted"])
        self.assertFalse(result["semantic_quality_verified"])
        self.model.complete.assert_called_once()
        messages, tools = self.model.complete.call_args.args
        value = json.loads(messages[1]["content"])
        self.assertEqual(value["speaker_id"], "kongzi")
        self.assertEqual(set(value["persona"]), {"persona_spec", "core_anchors", "dialogue_examples", "roundtable_adapter"})
        self.assertEqual(tools, [])
        state = self.state()
        self.assertEqual(len(state["turns"]), 1)
        self.assertEqual(state["turns"][0]["basis"]["modes"], ["simulation"])

    def test_pause_while_request_in_flight_rejects_late_response(self):
        self.start()
        self.provider()
        response = self.model.complete.return_value
        def late(*args):
            fixtures.rt.transition(self.identifier, "pause", self.root)
            return response
        self.model.complete.side_effect = late
        self.assertFalse(self.generate()["accepted"])
        self.assertEqual(self.state()["turns"], [])
        self.assertEqual(self.state()["session"]["status"], "paused")

    def test_failure_pauses_and_retains_prior_turn_then_explicit_resume(self):
        self.start()
        self.provider()
        self.generate()
        self.model.complete.side_effect = OSError("Synthetic service failure")
        with self.assertRaisesRegex(ValueError, "generation failed"):
            self.generate()
        self.assertEqual(len(self.state()["turns"]), 1)
        self.assertEqual(self.state()["session"]["status"], "paused")
        self.assertIsNone(self.state()["session"]["active_generation"])
        self.assertEqual(self.model.complete.call_count, 2)

    def test_interjection_is_in_context_and_must_be_addressed(self):
        self.start()
        self.provider()
        fixtures.rt.transition(self.identifier, "interject", self.root, text="An original synthetic user question")
        event = self.state()["events"][-1]["event_id"]
        fixtures.rt.transition(self.identifier, "resume", self.root)
        self.raw.update(responds_to=[event], stance="clarify")
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        self.assertTrue(self.generate()["accepted"])
        context = json.loads(self.model.complete.call_args.args[0][1]["content"])
        self.assertEqual(context["pending_user_event_id"], event)
        self.assertEqual(context["user_events"][-1]["text"], "An original synthetic user question")

    def test_model_cannot_inject_approval_or_tools(self):
        self.start()
        self.provider()
        self.raw["moderation"] = {"accepted": True}
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state()["turns"], [])

    def test_confirmation_and_model_preflight_before_ticket(self):
        self.start()
        self.provider()
        with self.assertRaises(ValueError):
            runner.generate_turn(self.identifier, "synthetic", root=self.root)
        self.model.inspect_identity.side_effect = ValueError("Synthetic missing model")
        with self.assertRaises(ValueError):
            self.generate()
        self.assertIsNone(self.state()["session"]["active_generation"])
        self.model.complete.assert_not_called()

    def test_loaded_evidence_citation_resolves_sources(self):
        self.start()
        self.provider()
        def supported(messages, tools):
            evidence = json.loads(messages[1]["content"])["evidence"]
            self.assertTrue(evidence)
            self.raw["evidence_ids"] = [evidence[0]["evidence_id"]]
            return {"content": json.dumps(self.raw)}
        self.model.complete.side_effect = supported
        self.assertTrue(self.generate()["accepted"])
        basis = self.state()["turns"][0]["basis"]
        self.assertTrue(basis["source_ids"])
        self.assertEqual(basis["modes"], ["simulation", "inference"])

    def test_not_loaded_evidence_is_refused(self):
        self.start()
        self.provider()
        self.raw["evidence_ids"] = ["E-9999"]
        self.model.complete.return_value = {"content": json.dumps(self.raw)}
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state()["turns"], [])

    def test_withdrawn_and_local_only_cards_never_retrieved(self):
        package = self.root / "personas/kongzi"
        manifest = fixtures.rt.read_json(package / "manifest.json")
        path = package / manifest["files"]["structured_evidence"]
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for i, record in enumerate(records):
            if i % 2:
                record["delivery_channel"] = "local_only"
            else:
                record["status"] = "withdrawn"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
        self.assertEqual(runner.retrieve_evidence(package, manifest, "学习 判断"), [])

    def test_three_person_six_turns_with_interjection_and_explicit_targets(self):
        import shutil
        original = fixtures.rt.read_json(fixtures.ROOT / "圆桌会议/_template/session.example.json")
        self.config["participants"] = original["participants"]
        self.config["limits"]["max_turns"] = 6
        source = fixtures.ROOT / "personas/richard-feynman"
        target = self.root / "personas/richard-feynman"
        target.mkdir()
        manifest = fixtures.rt.read_json(source / "manifest.json")
        shutil.copyfile(source / "manifest.json", target / "manifest.json")
        for role in ("persona_spec", "core_anchors", "dialogue_examples", "profile", "sources", "evidence",
                     "context_memory", "structured_evidence", "structured_context_memory"):
            if role in manifest["files"]:
                path = target / manifest["files"][role]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / manifest["files"][role], path)
        self.start()
        self.provider()
        seen = []
        def answer(messages, tools):
            value = json.loads(messages[1]["content"])
            decision = value["moderator"]
            seen.append(decision)
            response = dict(self.raw, responds_to=decision["responds_to"], stance="clarify")
            return {"content": json.dumps(response)}
        self.model.complete.side_effect = answer
        for i in range(6):
            if i == 3:
                fixtures.rt.transition(self.identifier, "interject", self.root, text="Original hypothetical constraint")
                fixtures.rt.transition(self.identifier, "resume", self.root)
            self.assertTrue(self.generate()["accepted"])
        state = self.state()
        self.assertEqual(state["session"]["status"], "finishing")
        self.assertEqual([t["speaker_id"] for t in state["turns"][:3]], ["kongzi", "zhuangzi", "richard-feynman"])
        self.assertEqual(seen[3]["responds_to"], [state["events"][0]["event_id"]])
        self.assertTrue(seen[4]["responds_to"][0].startswith("C-"))
        self.assertEqual(self.model.complete.call_count, 6)

    def test_ignoring_moderator_target_is_refused(self):
        self.config["limits"]["max_turns"] = 4
        self.start()
        self.provider()
        self.generate()
        self.generate()
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(len(self.state()["turns"]), 2)
        self.assertEqual(self.state()["session"]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
