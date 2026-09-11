"""Loading-plan regression tests; no model calls or user conversations."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "persona_entry", ROOT / "直接对话" / "scripts" / "resolve_persona.py",
)
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class PersonaEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "personas" / "sample"
        self.package.mkdir(parents=True)
        module = self.root / "直接对话"
        (module / "prompts").mkdir(parents=True)
        (module / "prompts" / "agent.md").write_text("Contract", encoding="utf-8")
        self.write_json(module / "persona-aliases.json", {
            "schema_version": "1.0", "aliases": {"Example": "sample"},
        })
        self.manifest = {
            "person_id": "sample", "display_name": "Example", "package_version": "0.1.0",
            "subject_kind": "historical_public", "readiness": "draft", "visibility": "private",
            "capabilities": {"direct_chat": True},
            "files": {role: f"{role}.md" for role in entry.ROLES},
        }
        for relative in self.manifest["files"].values():
            (self.package / relative).write_text("Fixture", encoding="utf-8")
        self.save_manifest()

    @staticmethod
    def write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def save_manifest(self):
        self.write_json(self.package / "manifest.json", self.manifest)

    def test_all_real_aliases_and_adapters(self):
        for alias, person_id in entry.registry(ROOT).items():
            with self.subTest(alias=alias):
                result = entry.resolve_persona(alias, ROOT, preview=True)
                self.assertEqual(result["person_id"], person_id)
                self.assertTrue(all((ROOT / name).is_file() for name in result["load_files"]))
                self.assertFalse(any("research.local" in name for name in result["load_files"]))
                adapter = f"直接对话/personas/{person_id}.md"
                if (ROOT / adapter).exists():
                    self.assertEqual(result["load_files"][-1], adapter)

    def test_private_ready_still_requires_preview(self):
        for readiness in ("draft", "chat-ready"):
            self.manifest["readiness"] = readiness
            self.save_manifest()
            with self.assertRaisesRegex(ValueError, "authorization"):
                entry.resolve_persona("sample", self.root)

    def test_explicit_preview_and_case_insensitive_alias(self):
        result = entry.resolve_persona("EXAMPLE", self.root, preview=True)
        self.assertEqual(result["mode"], "local_preview")

    def test_saved_preview_does_not_require_a_flag(self):
        self.write_json(self.root / ".persona" / "preview.local.json", {
            "schema_version": "1.0", "scope": "local_read_only", "person_ids": ["sample"],
        })
        self.assertEqual(entry.resolve_persona("sample", self.root)["mode"], "local_preview")

    def test_unlisted_preview_is_denied(self):
        self.write_json(self.root / ".persona" / "preview.local.json", {
            "schema_version": "1.0", "scope": "local_read_only", "person_ids": ["someone-else"],
        })
        with self.assertRaisesRegex(ValueError, "authorization"):
            entry.resolve_persona("sample", self.root)

    def test_public_ready_needs_no_preview(self):
        self.manifest.update(readiness="chat-ready", visibility="public")
        self.save_manifest()
        self.assertEqual(entry.resolve_persona("sample", self.root)["mode"], "public")

    def test_disabled_capability_cannot_be_bypassed(self):
        self.manifest["capabilities"]["direct_chat"] = False
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "disabled"):
            entry.resolve_persona("sample", self.root, preview=True)

    def test_private_living_person_cannot_use_shortcut_preview(self):
        self.manifest["subject_kind"] = "living_private"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "historical public"):
            entry.resolve_persona("sample", self.root, preview=True)

    def test_manifest_paths_are_used_and_missing_files_fail(self):
        (self.package / "custom.md").write_text("Custom", encoding="utf-8")
        self.manifest["files"]["persona_spec"] = "custom.md"
        self.save_manifest()
        result = entry.resolve_persona("sample", self.root, preview=True)
        self.assertIn("personas/sample/custom.md", result["load_files"])
        (self.package / "custom.md").unlink()
        with self.assertRaisesRegex(ValueError, "Missing file"):
            entry.resolve_persona("sample", self.root, preview=True)

    def test_optional_undeclared_files_are_not_loaded(self):
        del self.manifest["files"]["core_anchors"]
        self.save_manifest()
        plan = entry.resolve_persona("sample", self.root, preview=True)
        self.assertNotIn("personas/sample/core_anchors.md", plan["load_files"])

    def test_protected_and_external_paths_are_rejected(self):
        for path in ("../outside.md", "raw/source.md", "research.local/summary.md",
                     ".runtime.local/summary.md", "C:/outside.md", "/outside.md"):
            with self.subTest(path=path):
                self.manifest["files"]["evidence"] = path
                self.save_manifest()
                with self.assertRaisesRegex(ValueError, "Unsupported loading path"):
                    entry.resolve_persona("sample", self.root, preview=True)

    def test_unknown_name_and_identity_mismatch_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown persona"):
            entry.resolve_persona("../sample", self.root, preview=True)
        self.manifest["person_id"] = "wrong-person"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "identity"):
            entry.resolve_persona("sample", self.root, preview=True)


if __name__ == "__main__":
    unittest.main()
