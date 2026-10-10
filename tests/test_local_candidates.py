"""Local extraction tests: original synthetic sources and a stub, never private user data."""
import copy
import io
import json
import unittest
from unittest.mock import patch

import test_local_distill as fixtures
import local_candidates as candidate


class LocalCandidateTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot"):
        locals()[_name] = getattr(fixtures.LocalDistillationTests, _name)

    def run_generation(self, **kwargs):
        return candidate.generate(self.library, self.job["job"]["id"], "synthetic-model",
                                  confirm_local_storage=True, confirm_local_service=True, **kwargs)

    def provider(self):
        self.stub = patch.object(candidate, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        provider = self.stub.return_value
        provider.inspect_identity.return_value = {"model": "synthetic-model", "digest": "a" * 64,
                                                  "options": {"temperature": 0}}
        provider.base_url = "http://127.0.0.1:11434"
        provider.last_metrics = {"done_reason": "stop"}
        provider.complete.return_value = {"content": json.dumps({"evidence": [], "memories": [],
                                                                  "gaps": ["Insufficient synthetic material"]}),
                                          "thinking": "MUST NOT BE SAVED"}
        return provider

    def test_candidate_snapshot_and_no_silent_repeat(self):
        state = self.ready()
        before = self.load()[2]
        provider = self.provider()
        result = self.run_generation()
        output = self.library / self.job["job"]["id"] / "candidates" / result["run_sha256"]
        self.assertFalse((output / ".incomplete").exists())
        data = json.loads((output / "candidates.json").read_text(encoding="utf-8"))
        self.assertFalse(data["semantic_review_completed"])
        self.assertFalse(result["persona_generated"])
        self.assertEqual(self.load()[2], before)
        index = json.loads((output / "checksums.json").read_text(encoding="utf-8"))
        self.assertTrue(all(fixtures.local.sha(output / name) == checksum for name, checksum in index.items()))
        self.assertNotIn("MUST NOT BE SAVED", "".join(p.read_text(encoding="utf-8") for p in output.iterdir()))
        with self.assertRaises(FileExistsError):
            self.run_generation()
        self.assertEqual(provider.complete.call_count, 1)
        self.assertEqual(provider.complete.call_args.args[1], [])

    def test_confirmation_and_policy_precede_provider(self):
        with patch.object(candidate, "OllamaProvider") as constructor:
            with self.assertRaises(ValueError):
                candidate.generate(self.library, "synthetic-job", "model")
            constructor.assert_not_called()
        self.start()
        directory = self.library / self.job["job"]["id"]
        path = directory / "job.v2.json"
        changed = copy.deepcopy(self.job)
        changed["policy"]["execution_policy"]["allowed_services"] = ["remote"]
        path.write_text(json.dumps(changed), encoding="utf-8")
        with patch.object(candidate, "OllamaProvider") as constructor:
            with self.assertRaises(ValueError):
                self.run_generation()
            constructor.assert_not_called()

    def test_model_preflight_before_private_material(self):
        self.ready()
        provider = self.provider()
        provider.inspect_identity.side_effect = ValueError("unavailable synthetic model")
        with patch.object(candidate.local, "input_inventory", side_effect=AssertionError("body read")):
            with self.assertRaises(ValueError):
                self.run_generation()
        provider.complete.assert_not_called()

    def test_changed_input_or_snapshot_never_sent(self):
        state = self.ready()
        provider = self.provider()
        path = self.incoming / "source.txt"
        original = path.read_bytes()
        path.write_text("changed original fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_generation()
        provider.complete.assert_not_called()
        # A distinct job avoids restoring timestamps to counterfeit an input fingerprint.
        self.approve("material")
        state = self.build(update=True)
        (self.snapshot(state) / "chunks.jsonl").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_generation()
        provider.complete.assert_not_called()

    def test_changed_model_after_request_preserves_incomplete(self):
        self.ready()
        provider = self.provider()
        identity = provider.inspect_identity.return_value
        provider.inspect_identity.side_effect = [identity, identity, {"digest": "b" * 64}]
        with self.assertRaises(ValueError):
            self.run_generation()
        root = self.library / self.job["job"]["id"] / "candidates"
        output = next(root.iterdir())
        self.assertTrue((output / ".incomplete").exists())
        self.assertFalse((output / "candidates.json").exists())
        self.assertFalse((self.library / self.job["job"]["id"] / ".lock").exists())

    def test_bad_response_never_published(self):
        self.ready()
        provider = self.provider()
        provider.complete.return_value = {"content": '{"evidence": [], "memories": [], "gaps": [], "reviewed": true}'}
        with self.assertRaises(ValueError):
            self.run_generation()
        output = next((self.library / self.job["job"]["id"] / "candidates").iterdir())
        self.assertTrue((output / ".incomplete").exists())
        self.assertFalse((output / "checksums.json").exists())

    def test_tool_or_length_response_rejected(self):
        self.ready()
        provider = self.provider()
        provider.last_metrics = {"done_reason": "length"}
        with self.assertRaises(ValueError):
            self.run_generation()
        provider.complete.assert_called_once()

    def test_tools_rejected_without_execution(self):
        self.ready()
        provider = self.provider()
        provider.complete.return_value["tool_calls"] = [{"function": {"name": "synthetic"}}]
        with self.assertRaises(ValueError):
            self.run_generation()
        output = next((self.library / self.job["job"]["id"] / "candidates").iterdir())
        self.assertFalse((output / "candidates.json").exists())

    def test_oversized_batch_is_not_silently_truncated(self):
        (self.incoming / "source.txt").write_text("Original synthetic long paragraph. " * 1700, encoding="utf-8")
        self.ready()
        provider = self.provider()
        with self.assertRaises(ValueError):
            self.run_generation()
        provider.complete.assert_not_called()

    def test_input_changed_during_inference_is_not_published(self):
        self.ready()
        provider = self.provider()
        response = provider.complete.return_value
        def changed(*args):
            (self.incoming / "source.txt").write_text("Changed synthetic input", encoding="utf-8")
            return response
        provider.complete.side_effect = changed
        with self.assertRaises(ValueError):
            self.run_generation()
        output = next((self.library / self.job["job"]["id"] / "candidates").iterdir())
        self.assertTrue((output / ".incomplete").exists())
        self.assertFalse((output / "checksums.json").exists())

    def test_cli_cannot_capture_private_run(self):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err), patch.object(candidate, "generate") as generate:
            code = candidate.main(["--library", str(self.library), "--job", "fixture", "--model", "fixture"])
        self.assertEqual(code, 1)
        generate.assert_not_called()
        self.assertNotIn(str(self.library), err.getvalue())


if __name__ == "__main__":
    unittest.main()
