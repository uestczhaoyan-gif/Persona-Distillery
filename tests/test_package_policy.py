"""Version and policy restrictions are checked before local overlay access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "overlay_export", ROOT / "人物蒸馏/scripts/export_agent_overlay.py")
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


class OverlayCompatibilityTests(unittest.TestCase):
    def test_restricted_or_unknown_manifest_does_not_read_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            for manifest in ({"schema_version": "2.0"}, {},
                             {"schema_version": "1.0", "execution_policy": "local_only"},
                             {"schema_version": "1.0", "distribution_policy": "local_only"}):
                with self.subTest(manifest=manifest):
                    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                    # There is deliberately no overlay file. Compatibility must
                    # fail before attempting to open any potentially private data.
                    with self.assertRaisesRegex(ValueError, "schema_version|policy fields"):
                        overlay.export(package)
                    self.assertFalse((package / ".runtime.local").exists())

    def test_legacy_export_retains_rights_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            (package / "manifest.json").write_text('{"schema_version":"1.0"}', encoding="utf-8")
            local = package / "research.local"
            local.mkdir()
            config = {"person_id": "fixture", "remote_agent": {
                "enabled": True, "raw_text_allowed": False, "allowed_channels": ["agent_summary"]},
                "operator_confirmation": {"rights_reviewed": True, "privacy_reviewed": True},
                "paths": {"chunks": "chunks.jsonl", "evidence": "evidence.jsonl"}}
            (local / "overlay.json").write_text(json.dumps(config), encoding="utf-8")
            records = [{"delivery_channel": "agent_summary", "text": "Original fixture summary"},
                       {"delivery_channel": "local_only", "text": "Must stay local"}]
            (local / "chunks.jsonl").write_text(
                "\n".join(json.dumps(r) for r in records), encoding="utf-8")
            with patch("builtins.print"):
                overlay.export(package)
            result = (package / ".runtime.local/agent-memory.jsonl").read_text(encoding="utf-8")
            self.assertIn("Original fixture summary", result)
            self.assertNotIn("Must stay local", result)
