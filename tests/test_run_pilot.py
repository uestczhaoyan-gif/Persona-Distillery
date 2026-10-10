import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
import run_pilot as runner
from ollama_provider import ProviderError
from pilot_runtime import canonical


class FakeProvider:
    def __init__(self, identity):
        self.identity = identity
        self.calls = 0
        self.inspections = 0
        self.last_metrics = {}
        self.action = None

    def options(self):
        return self.identity["options"]

    def inspect_identity(self):
        self.inspections += 1
        return copy.deepcopy(self.identity)

    def complete(self, messages, tools):
        self.calls += 1
        assert tools == []
        assert messages[-1]["role"] == "user"
        self.last_metrics = {"done_reason": "stop", "eval_count": 5}
        if self.action:
            return self.action(self.calls)
        return {"role": "assistant", "content": "synthetic answer", "thinking": "never save this"}


class RunPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        directory = self.root / ".work/pilots"
        directory.mkdir(parents=True)
        options = {"temperature": .7, "seed": 20261008, "num_ctx": 32768, "num_predict": 1600}
        self.identity = {"model": "synthetic", "digest": "a" * 64,
                         "metadata_sha256": "b" * 64, "supports_tools": False, "options": options}
        self.plan = {"inputs": [{"person_id": "synthetic-" + str(i), "group": "ABCD"[i % 4],
                                 "messages": [{"role": "system", "content": "original fixture"}]}
                                for i in range(16)], "questions": ["question one", "question two"], "options": options}
        self.config = {"schema_version": "1.0", "kind": "pilot_model_config",
                       "status": "CONFIG_FROZEN_CAPACITY_PENDING", "budget": runner.BUDGET,
                       "tools": [], "capacity_verified": False, "input_sha256": "c" * 64,
                       "requested_model": "synthetic", "base_url": "http://127.0.0.1:11434",
                       "timeout_seconds": 60, "model_identity": self.identity}
        raw = canonical(self.config)
        self.sha = hashlib.sha256(raw).hexdigest()
        (directory / ("model-" + self.sha + ".json")).write_bytes(raw)
        self.provider = FakeProvider(self.identity)
        self.current = patch.object(runner, "current_inputs", return_value=self.plan).start()
        patch.object(runner, "provider_for", return_value=self.provider).start()
        self.addCleanup(patch.stopall)

    def execute(self):
        return runner.execute(self.sha, root=self.root, preview=True, confirm_run=True)

    def run_path(self):
        return self.root / ".work/pilots" / ("run-" + self.sha)

    def test_complete_records_32_cases_without_claiming_review_or_capacity(self):
        result = self.execute()
        self.assertEqual(result["status"], "GENERATED_REVIEW_PENDING")
        self.assertEqual(result["inference_attempts"], 32)
        self.assertEqual(result["successful_cases"], 32)
        self.assertFalse(result["human_review_completed"])
        self.assertFalse(result["capacity_verified"])
        text = (self.run_path() / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("never save this", text)
        events = [json.loads(line) for line in text.splitlines()]
        self.assertEqual(events[1]["event"], "reserved")
        self.assertEqual(events[2]["event"], "inference_started")
        self.assertFalse((self.run_path() / ".running").exists())
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(self.provider.calls, 32)

    def test_one_retry_per_case_and_64_request_bound(self):
        def action(number):
            if number % 2:
                raise ProviderError("synthetic transient error")
            return {"role": "assistant", "content": "synthetic answer"}
        self.provider.action = action
        result = self.execute()
        self.assertEqual(result["successful_cases"], 32)
        self.assertEqual(result["inference_attempts"], 64)
        self.assertEqual(result["failed_attempts"], 32)

    def test_three_same_failures_stop_and_do_not_save_exception_body(self):
        def action(_):
            raise ProviderError("sensitive error body must not be stored")
        self.provider.action = action
        result = self.execute()
        self.assertEqual(result["inference_attempts"], 3)
        self.assertEqual(result["status"], "STOPPED_REPEATED_TRANSPORT_OR_PROVIDER")
        self.assertNotIn("sensitive error", (self.run_path() / "events.jsonl").read_text())

    def test_model_drift_discards_response(self):
        self.provider.inspect_identity = lambda: self.identity if not self.provider.calls else {**self.identity, "digest": "d" * 64}
        result = self.execute()
        self.assertEqual(result["status"], "STOPPED_MODEL_CHANGED")
        self.assertEqual(result["successful_cases"], 0)
        self.assertEqual(result["inference_attempts"], 1)

    def test_length_limit_is_failure_and_does_not_count_as_answer(self):
        def action(_):
            self.provider.last_metrics = {"done_reason": "length"}
            return {"role": "assistant", "content": "cut off"}
        self.provider.action = action
        result = self.execute()
        self.assertEqual(result["status"], "STOPPED_REPEATED_OUTPUT_LIMIT")
        self.assertEqual(result["successful_cases"], 0)

    def test_expired_budget_sends_no_request(self):
        with patch.object(runner.time, "monotonic", side_effect=[0, 7201, 7201]):
            result = self.execute()
        self.assertEqual(result["status"], "STOPPED_WALL_BUDGET")
        self.assertEqual(self.provider.calls, 0)

    def test_interrupt_keeps_uncertain_run_and_never_replays_it(self):
        def action(_):
            raise KeyboardInterrupt()
        self.provider.action = action
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertTrue((self.run_path() / ".running").exists())
        with self.assertRaises(FileExistsError):
            self.execute()
        self.assertEqual(self.provider.calls, 1)

    def test_confirmation_required_before_reading_inputs(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            runner.execute(self.sha, root=self.root)
        self.current.assert_not_called()

    def test_input_drift_after_request_stops_without_accepting_answer(self):
        self.current.side_effect = [self.plan, self.plan, ValueError("changed fixture")]
        result = self.execute()
        self.assertEqual(result["status"], "STOPPED_INPUT_OR_STORAGE_ERROR")
        self.assertEqual(result["successful_cases"], 0)
        self.assertEqual(result["inference_attempts"], 1)

    def test_tools_are_rejected_not_executed(self):
        self.provider.action = lambda _: {"role": "assistant", "content": "tool attempt",
                                          "tool_calls": [{"function": {"name": "write_file"}}]}
        result = self.execute()
        self.assertEqual(result["status"], "STOPPED_REPEATED_INVALID_RESPONSE")
        self.assertEqual(result["successful_cases"], 0)

    def test_failed_metadata_consumes_reservations_but_not_inference_attempts(self):
        def inspect():
            raise ProviderError("offline fixture")
        self.provider.inspect_identity = inspect
        result = self.execute()
        self.assertEqual(result["reserved_attempts"], 3)
        self.assertEqual(result["inference_attempts"], 0)
        self.assertEqual(result["status"], "STOPPED_REPEATED_TRANSPORT_OR_PROVIDER")
