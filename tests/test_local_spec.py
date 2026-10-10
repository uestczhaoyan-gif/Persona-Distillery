"""Local spec integration uses a fictional source and a model stub exclusively."""
import io
import json
import unittest
from unittest.mock import patch

import test_local_merge as fixtures
import local_spec as spec


class LocalSpecTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation", "make_review", "prepare", "merge"):
        locals()[_name] = getattr(fixtures.LocalMergeTests, _name)

    def setup_spec(self):
        review_id = self.prepare()
        self.merge_id = self.merge([review_id])["merge_sha256"]
        constructor = patch.object(spec, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        provider = constructor.return_value
        provider.base_url = "http://127.0.0.1:11434"
        provider.inspect_identity.return_value = {"model": "fixture", "digest": "b" * 64, "options": {"temperature": 0.2}}
        provider.last_metrics = {"done_reason": "stop"}
        self.payload = {"profile": {"description": "An original synthetic character", "limitations": "Fixture only",
                                   "record_ids": ["E-0001"]}, "attention": [], "judgment": [], "dialogue_style": [],
                        "knowledge_limits": [], "language": {"mode": "undetermined", "adjustment": "Need voice evidence",
                                                               "record_ids": ["E-0001"]},
                        "anchors": [], "examples": [], "gaps": ["Not a real person or real assessment"]}
        provider.complete.return_value = {"content": json.dumps(self.payload), "thinking": "PRIVATE STUB THOUGHT"}
        return provider

    def generate(self):
        return spec.generate_spec(self.library, self.job["job"]["id"], self.merge_id, "fixture",
                                  confirm_local_storage=True, confirm_local_service=True)

    def test_synthesis_uses_active_records_and_saves_candidate_only(self):
        provider = self.setup_spec()
        result = self.generate()
        output = self.library / self.job["job"]["id"] / "specs" / result["spec_sha256"]
        self.assertFalse((output / ".incomplete").exists())
        candidate = json.loads((output / "spec-candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate["candidate"]["profile"]["record_ids"], ["E-0001"])
        self.assertFalse(result["persona_generated"])
        self.assertFalse(result["avatar_ready"])
        self.assertNotIn("PRIVATE STUB THOUGHT", "".join(p.read_text(encoding="utf-8") for p in output.iterdir()))
        messages, tools = provider.complete.call_args.args
        self.assertEqual(tools, [])
        self.assertEqual(set(json.loads(messages[1]["content"])), {"records"})
        with self.assertRaises(FileExistsError):
            self.generate()
        provider.complete.assert_called_once()

    def test_preflight_happens_before_merge_material_read(self):
        provider = self.setup_spec()
        provider.inspect_identity.side_effect = ValueError("Synthetic model unavailable")
        with patch.object(spec, "_read_merge_locked", side_effect=AssertionError("material read")):
            with self.assertRaises(ValueError):
                self.generate()
        provider.complete.assert_not_called()

    def test_source_change_during_generation_preserves_incomplete(self):
        provider = self.setup_spec()
        response = provider.complete.return_value
        def changed(*args):
            (self.incoming / "source.txt").write_text("Modified fixture", encoding="utf-8")
            return response
        provider.complete.side_effect = changed
        with self.assertRaises(ValueError):
            self.generate()
        output = next((self.library / self.job["job"]["id"] / "specs").iterdir())
        self.assertTrue((output / ".incomplete").exists())
        self.assertFalse((output / "spec-candidate.json").exists())

    def test_unknown_record_never_completes(self):
        provider = self.setup_spec()
        self.payload["profile"]["record_ids"] = ["E-9999"]
        provider.complete.return_value = {"content": json.dumps(self.payload)}
        with self.assertRaises(ValueError):
            self.generate()
        output = next((self.library / self.job["job"]["id"] / "specs").iterdir())
        self.assertTrue((output / ".incomplete").exists())

    def test_truncated_response_never_completes(self):
        provider = self.setup_spec()
        provider.last_metrics = {"done_reason": "length"}
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.generate()
        provider.complete.assert_called_once()

    def test_tool_response_never_executes_or_completes(self):
        provider = self.setup_spec()
        provider.complete.return_value["tool_calls"] = [{"function": {"name": "synthetic_tool"}}]
        with self.assertRaisesRegex(ValueError, "Invalid"):
            self.generate()
        provider.complete.assert_called_once()

    def test_model_drift_never_completes(self):
        provider = self.setup_spec()
        identity = provider.inspect_identity.return_value
        provider.inspect_identity.side_effect = [identity, identity, {"digest": "c" * 64}]
        with self.assertRaises(ValueError):
            self.generate()

    def test_confirmations_and_terminal_are_required(self):
        with patch.object(spec, "OllamaProvider") as constructor:
            with self.assertRaises(ValueError):
                spec.generate_spec(self.library, "fixture", "a" * 64, "fixture")
            constructor.assert_not_called()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()), patch.object(spec, "generate_spec") as generate:
            self.assertEqual(spec.main(["--library", str(self.library), "--job", "fixture", "--merge", "a" * 64,
                                        "--model", "fixture"]), 1)
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
