"""Local v2 loader tests use synthetic text outside the Git workspace."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
import local_package as loader


class LocalPackageTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root.is_relative_to(ROOT):
            temp_root = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else Path("/tmp")
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.package = self.base / "library/sample"
        self.package.mkdir(parents=True)
        self.manifest = json.loads((ROOT / "personas/_template/manifest.json").read_text(encoding="utf-8"))
        self.manifest.update(schema_version="2.0", person_id="sample", policy_version="1.0",
                             sensitive_data=False, material_basis=["public_source"], review_status="candidate",
                             execution_policy={"mode": "local_only", "allowed_services": []},
                             distribution_policy={"mode": "local_only"})
        for role, relative in self.manifest["files"].items():
            (self.package / relative).write_text(f"SYNTHETIC {role} BODY", encoding="utf-8")
        self.save()

    def save(self):
        (self.package / "manifest.v2.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def load(self, **kwargs):
        return loader.load_local_context(self.package, confirm_local_storage=True, **{"preview": True, **kwargs})

    def assert_no_body_read(self, action):
        original = loader._bounded_bytes
        read = []
        def observed(path, limit):
            read.append(path.name)
            return original(path, limit)
        with patch.object(loader, "_bounded_bytes", side_effect=observed):
            with self.assertRaises(ValueError):
                action()
        self.assertTrue(all(name == "manifest.v2.json" for name in read), read)

    def test_read_only_context_excludes_logs_and_has_content_hashes(self):
        before = {p.name: p.read_bytes() for p in self.package.iterdir()}
        result = self.load()
        self.assertIn("SYNTHETIC persona_spec BODY", result.context)
        for role in ("dialogue_log", "usage_example", "evaluation", "build_report"):
            self.assertNotIn(f"SYNTHETIC {role} BODY", result.context)
        self.assertEqual(len(result.file_hashes), len(loader.CONTEXT_ROLES))
        self.assertNotIn("SYNTHETIC", repr(result))
        self.assertNotIn("sample", repr(result))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.package.iterdir()})

    def test_all_six_subject_kinds_can_be_explicit_local_previews(self):
        for kind in ("historical_public", "deceased_private", "living_public", "living_private", "self", "fictional"):
            with self.subTest(kind=kind):
                self.manifest["subject_kind"] = kind
                self.save()
                self.assertEqual(self.load().person_id, "sample")

    def test_confirmation_and_preview_are_required_before_body(self):
        self.assert_no_body_read(lambda: loader.load_local_context(self.package, confirm_local_storage=False))
        self.assert_no_body_read(lambda: self.load(preview=False))

    def test_unknown_version_and_malformed_policy_fail_before_body(self):
        self.manifest["schema_version"] = "9.0"
        self.save()
        self.assert_no_body_read(self.load)
        self.manifest["schema_version"] = "2.0"
        self.manifest["execution_policy"]["allowed_services"] = ["remote"]
        self.save()
        self.assert_no_body_read(self.load)

    def test_remote_policy_cannot_use_local_reader(self):
        self.manifest["execution_policy"] = {"mode": "declared_services", "allowed_services": ["remote"]}
        self.manifest["distribution_policy"]["mode"] = "review_required"
        self.save()
        self.assert_no_body_read(self.load)

    def test_incomplete_locked_and_legacy_aliases_are_rejected(self):
        for path in (self.package / ".migration-incomplete.json", self.package.parent / ".migration.lock",
                     self.package / ".backup-incomplete.json", self.package.parent / ".backup.lock",
                     self.package / ".deletion-incomplete.json", self.package.parent / ".deletion.lock",
                     self.package / "manifest.json"):
            with self.subTest(path=path.name):
                path.write_text("{}", encoding="utf-8")
                self.assert_no_body_read(self.load)
                path.unlink()

    def test_git_storage_unsafe_and_missing_paths_fail_before_body(self):
        (self.base / ".git").mkdir()
        self.assert_no_body_read(self.load)
        (self.base / ".git").rmdir()
        for relative in ("../outside.md", "raw/private.md", "missing.md"):
            self.manifest["files"]["build_report"] = relative
            self.save()
            self.assert_no_body_read(self.load)

    def test_exact_character_budget_and_invalid_encoding(self):
        size = len(self.load().context)
        self.assertEqual(len(self.load(max_chars=size).context), size)
        with self.assertRaisesRegex(ValueError, "budget"):
            self.load(max_chars=size - 1)
        (self.package / self.manifest["files"]["profile"]).write_bytes(b"\xff")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.load()

    def test_changes_during_loading_discard_the_context(self):
        original = loader._bounded_bytes
        header_reads = []
        def changed(path, limit):
            data = original(path, limit)
            if path.name == "manifest.v2.json":
                header_reads.append(True)
                if len(header_reads) == 2:
                    (self.package / self.manifest["files"]["profile"]).write_text("EDITED", encoding="utf-8")
            return data
        with patch.object(loader, "_bounded_bytes", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Context changed"):
                self.load()

    def test_manifest_change_during_loading_is_rejected(self):
        original = loader._bounded_bytes
        def changed(path, limit):
            data = original(path, limit)
            if path.name == self.manifest["files"]["profile"]:
                self.manifest["display_name"] = "MODIFIED SYNTHETIC NAME"
                self.save()
            return data
        with patch.object(loader, "_bounded_bytes", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Manifest changed"):
                self.load()


if __name__ == "__main__":
    unittest.main()
