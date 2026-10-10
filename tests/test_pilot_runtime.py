"""Synthetic model metadata only; never execute a real model."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
import pilot_runtime as runtime


class PilotRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {"status": "MODEL_PENDING", "real_model_calls": 0,
                     "options": {"temperature": .7, "seed": 20261008,
                                 "num_ctx": 32768, "num_predict": 1600}}
        raw = runtime.canonical(self.plan)
        self.identity = hashlib.sha256(raw).hexdigest()
        path = self.root / ".work/pilots" / (self.identity + ".json")
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        self.metadata = {"model": "synthetic:latest", "digest": "a" * 64,
                         "metadata_sha256": "b" * 64, "supports_tools": False,
                         "options": copy.deepcopy(self.plan["options"])}
        self.prepare = patch.object(runtime, "prepare", return_value=self.plan).start()
        self.addCleanup(patch.stopall)

    def freeze(self, **kwargs):
        return runtime.freeze_model(self.identity, "synthetic", root=self.root,
                                    preview=True, confirm_local=True, **kwargs)

    def test_freeze_binds_inputs_model_and_budget_without_generation(self):
        with patch.object(runtime.OllamaProvider, "inspect_identity", return_value=self.metadata), \
                patch.object(runtime.OllamaProvider, "complete", side_effect=AssertionError("no inference")):
            result = self.freeze()
            self.assertEqual(result, self.freeze())
        saved = runtime.read_snapshot(result["config_sha256"], self.root, model=True)
        self.assertEqual(saved["model_identity"], self.metadata)
        self.assertEqual(saved["budget"]["max_requests"], 64)
        self.assertEqual(saved["input_sha256"], self.identity)
        self.assertFalse(saved["capacity_verified"])
        self.assertEqual(saved["real_model_calls"], 0)

    def test_model_drift_does_not_create_config(self):
        changed = {**self.metadata, "digest": "c" * 64}
        with patch.object(runtime.OllamaProvider, "inspect_identity", side_effect=[self.metadata, changed]):
            with self.assertRaisesRegex(ValueError, "identity changed"):
                self.freeze()
        self.assertFalse(list((self.root / ".work/pilots").glob("model-*")))

    def test_input_drift_after_metadata_does_not_create_config(self):
        self.prepare.side_effect = [self.plan, {**self.plan, "changed": True}]
        with patch.object(runtime.OllamaProvider, "inspect_identity", return_value=self.metadata):
            with self.assertRaisesRegex(ValueError, "Inputs changed"):
                self.freeze()
        self.assertFalse(list((self.root / ".work/pilots").glob("model-*")))

    def test_missing_confirmation_and_remote_endpoint_make_no_requests(self):
        with patch.object(runtime.OllamaProvider, "inspect_identity") as request:
            with self.assertRaisesRegex(ValueError, "Confirm"):
                runtime.freeze_model(self.identity, "synthetic", root=self.root)
            with self.assertRaisesRegex(ValueError, "loopback"):
                self.freeze(base_url="https://example.com")
            request.assert_not_called()

    def test_corrupt_input_rejected_before_metadata(self):
        runtime.snapshot_path(self.identity, self.root).write_text("broken", encoding="utf-8")
        with patch.object(runtime.OllamaProvider, "inspect_identity") as request:
            with self.assertRaisesRegex(ValueError, "hash/size"):
                self.freeze()
            request.assert_not_called()

    def test_invalid_hash_cannot_select_another_path(self):
        for value in ("../other", "A" * 64, "a" * 63, ""):
            with self.assertRaisesRegex(ValueError, "Invalid snapshot"):
                runtime.read_snapshot(value, self.root)

    def test_reparse_directory_rejected(self):
        original = Path.lstat
        def lstat(path):
            if path.name == "pilots":
                class Reparse:
                    st_file_attributes = 0x400
                    st_mode = 0o40755
                return Reparse()
            return original(path)
        with patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(ValueError, "links"):
                runtime.read_snapshot(self.identity, self.root)
