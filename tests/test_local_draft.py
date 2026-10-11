"""Source-bound local drafts are built only from original synthetic fixtures."""
import json
import unittest
from unittest.mock import patch

import test_spec_review as fixtures
import local_draft as draft
import local_package


class LocalDraftTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation", "make_review", "prepare", "merge", "setup_spec", "generate",
                  "setup_review", "save_review"):
        locals()[_name] = getattr(fixtures.SpecReviewTests, _name)

    def prepare_draft(self):
        self.setup_review()
        self.review_id = self.save_review()["review_sha256"]
        return self.create()

    def create(self):
        result = draft.build_draft(self.library, self.job["job"]["id"], self.review_id, confirm_local_storage=True)
        self.output = self.base / "personas-output" / result["build_sha256"] / self.job["subject"]["person_id"]
        return result

    def test_build_current_verified_draft_and_explicit_local_preview(self):
        result = self.prepare_draft()
        self.assertFalse(result["persona_generated"])
        self.assertEqual(self.create(), result)
        manifest = json.loads((self.output / "manifest.v2.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["readiness"], "draft")
        self.assertEqual(manifest["visibility"], "private")
        self.assertFalse(any(manifest["capabilities"].values()))
        self.assertNotIn("avatar_png", manifest["files"])
        self.assertTrue(draft.verify_draft(self.output))
        with self.assertRaises(ValueError):
            local_package.load_local_context(self.output, confirm_local_storage=True)
        context = local_package.load_local_context(self.output, confirm_local_storage=True, preview=True)
        self.assertIn("An original synthetic character", context.context)
        self.assertIn("build-binding.json", dict(context.file_hashes))
        self.assertFalse((self.output / ".build-incomplete").exists())

    def test_source_withdrawal_invalidates_runtime_draft(self):
        self.prepare_draft()
        (self.incoming / "source.txt").unlink()
        with self.assertRaises(ValueError):
            draft.verify_draft(self.output)
        with self.assertRaises(ValueError):
            local_package.load_local_context(self.output, confirm_local_storage=True, preview=True)

    def test_changed_document_and_extra_file_refused(self):
        self.prepare_draft()
        path = self.output / "03-persona-spec.md"
        original = path.read_bytes()
        path.write_text("Synthetic unauthorized replacement", encoding="utf-8")
        with self.assertRaises(ValueError):
            draft.verify_draft(self.output)
        path.write_bytes(original)
        (self.output / "unexpected.txt").write_text("Synthetic extra", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.create()

    def test_interrupted_build_preserved_and_runtime_rejected(self):
        self.setup_review()
        self.review_id = self.save_review()["review_sha256"]
        original = draft.local.write_new
        def fail(path, data):
            if path.name == "02-evidence.md":
                raise OSError("Synthetic storage failure")
            return original(path, data)
        with patch.object(draft.local, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.create()
        output = next((self.base / "personas-output").glob("*/*"))
        self.assertTrue((output / ".build-incomplete").exists())
        with self.assertRaises(ValueError):
            self.create()
        with self.assertRaises(ValueError):
            local_package.load_local_context(output, confirm_local_storage=True, preview=True)

    def test_rejected_spec_cannot_build_and_confirmation_required(self):
        self.setup_review()
        self.decision["action"] = "reject"
        self.review_id = self.save_review()["review_sha256"]
        with self.assertRaises(ValueError):
            self.create()
        with self.assertRaises(ValueError):
            draft.build_draft(self.library, self.job["job"]["id"], self.review_id)
        self.assertFalse((self.base / "personas-output").exists())

    def test_omitting_manifest_binding_cannot_load_generated_draft(self):
        self.prepare_draft()
        path = self.output / "manifest.v2.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"].pop("build_binding")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "omit its source binding"):
            local_package.load_local_context(self.output, confirm_local_storage=True, preview=True)

    def test_source_change_during_context_read_is_detected(self):
        self.prepare_draft()
        original = local_package._bounded_bytes
        def changed(path, limit):
            value = original(path, limit)
            if path.name == "03-persona-spec.md":
                (self.incoming / "source.txt").write_text("Changed synthetic source", encoding="utf-8")
            return value
        with patch.object(local_package, "_bounded_bytes", side_effect=changed):
            with self.assertRaises(ValueError):
                local_package.load_local_context(self.output, confirm_local_storage=True, preview=True)


if __name__ == "__main__":
    unittest.main()
