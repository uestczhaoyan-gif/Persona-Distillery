"""Synthetic migration fixtures check no writes, no hidden copying and storage gates."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("persona_migration", ROOT / "人物蒸馏/scripts/migrate_persona.py")
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class MigrationPlanTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root.is_relative_to(ROOT):
            # These fixtures intentionally test non-repository storage. Other
            # tests may use .work/tmp; a private library must never accept it.
            temp_root = Path(os.environ["LOCALAPPDATA"]) / "Temp" if os.name == "nt" else Path("/tmp")
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".git").mkdir()
        self.source = self.workspace / "personas/sample"
        self.source.mkdir(parents=True)
        self.library = self.base / "private-library"
        self.manifest = json.loads((ROOT / "personas/_template/manifest.json").read_text(encoding="utf-8"))
        self.manifest.update(person_id="sample", display_name="PRIVATE FIXTURE IDENTITY")
        for relative in self.manifest["files"].values():
            (self.source / relative).write_text("ORIGINAL FIXTURE BODY", encoding="utf-8")
        (self.source / "raw").mkdir()
        (self.source / "raw/original.txt").write_text("UNSELECTED RAW FIXTURE", encoding="utf-8")
        self.policy = {"policy_version": "1.0", "subject_kind": "historical_public", "sensitive_data": False,
                       "material_basis": ["public_source"], "review_status": "candidate",
                       "execution_policy": {"mode": "local_only", "allowed_services": []},
                       "distribution_policy": {"mode": "local_only"}}
        self.save()

    def save(self):
        (self.source / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def plan(self):
        return migration.plan_migration(self.source, self.library, self.policy, self.workspace)

    def apply(self, digest, *, resume=False, confirmed=True):
        return migration.apply_migration(self.source, self.library, self.policy, digest,
                                          confirmed, self.workspace, resume=resume)

    def test_apply_requires_explicit_confirmation_and_matching_plan(self):
        digest = migration.plan_digest(self.plan())
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.apply(digest, confirmed=False)
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.apply("0" * 64)
        self.assertFalse(self.library.exists())

    def test_apply_copies_verified_files_and_preserves_source(self):
        plan = self.plan()
        before = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        result = self.apply(migration.plan_digest(plan))
        target = self.library / "sample"
        self.assertTrue(result["source_preserved"])
        self.assertFalse(result["runtime_available"])
        self.assertFalse((target / "manifest.json").exists())
        self.assertFalse((target / ".migration-incomplete.json").exists())
        self.assertFalse((self.library / ".migration.lock").exists())
        self.assertEqual(json.loads((target / "manifest.v2.json").read_text(encoding="utf-8")), plan["proposed_manifest"])
        for record in plan["copy_files"]:
            self.assertEqual(migration.sha(target / record["path"]), record["sha256"])
        self.assertNotIn("ORIGINAL FIXTURE BODY", (target / "05-dialogue-log.md").read_text(encoding="utf-8"))
        after = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.apply(migration.plan_digest(plan))

    def test_source_edit_after_preflight_is_rejected_without_writes(self):
        digest = migration.plan_digest(self.plan())
        (self.source / self.manifest["files"]["profile"]).write_text("Changed fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.apply(digest)
        self.assertFalse(self.library.exists())

    def test_interrupted_run_resumes_only_matching_verified_files(self):
        digest = migration.plan_digest(self.plan())
        original = migration.copy_verified
        calls = []
        def interrupt(source, target, record):
            calls.append(record["path"])
            if len(calls) == 2:
                raise OSError("Simulated interruption")
            original(source, target, record)
        with patch.object(migration, "copy_verified", side_effect=interrupt):
            with self.assertRaises(OSError):
                self.apply(digest)
        target = self.library / "sample"
        self.assertTrue((target / ".migration-incomplete.json").exists())
        self.assertFalse((target / "manifest.v2.json").exists())
        self.assertFalse((self.library / ".migration.lock").exists())
        self.assertTrue(self.apply(digest, resume=True)["ok"])

    def test_resume_does_not_overwrite_edited_or_unexpected_files(self):
        digest = migration.plan_digest(self.plan())
        with patch.object(migration, "copy_verified", side_effect=OSError("Interrupted")):
            with self.assertRaises(OSError):
                self.apply(digest)
        target = self.library / "sample"
        unexpected = target / "user-notes.txt"
        unexpected.write_text("User fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.apply(digest, resume=True)
        self.assertEqual(unexpected.read_text(encoding="utf-8"), "User fixture")
        unexpected.unlink()
        edited = target / self.manifest["files"]["profile"]
        edited.write_text("User edit", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.apply(digest, resume=True)
        self.assertEqual(edited.read_text(encoding="utf-8"), "User edit")

    def test_existing_lock_blocks_without_touching_target(self):
        digest = migration.plan_digest(self.plan())
        self.library.mkdir()
        lock = self.library / ".migration.lock"
        lock.write_text("other process", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "locked"):
            self.apply(digest)
        self.assertFalse((self.library / "sample").exists())
        self.assertEqual(lock.read_text(encoding="utf-8"), "other process")

    def test_partial_generated_metadata_remains_unloadable(self):
        digest = migration.plan_digest(self.plan())
        original = migration.write_new
        def interrupt(path, data):
            if path.name == "manifest.v2.json":
                raise OSError("Manifest write interrupted")
            original(path, data)
        with patch.object(migration, "write_new", side_effect=interrupt):
            with self.assertRaises(OSError):
                self.apply(digest)
        self.assertTrue((self.library / "sample/.migration-incomplete.json").exists())
        self.assertTrue(self.apply(digest, resume=True)["ok"])

    def test_source_edit_during_copy_keeps_destination_incomplete(self):
        digest = migration.plan_digest(self.plan())
        original = migration.copy_verified
        def change(source, target, record):
            original(source, target, record)
            source.write_text("Changed during copy", encoding="utf-8")
        with patch.object(migration, "copy_verified", side_effect=change):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                self.apply(digest)
        self.assertTrue((self.library / "sample/.migration-incomplete.json").exists())
        self.assertFalse((self.library / "sample/manifest.v2.json").exists())

    def test_plan_is_read_only_and_omits_logs_raw_and_example(self):
        before = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        result = self.plan()
        after = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(self.library.exists())
        copied = {f["path"] for f in result["copy_files"]}
        self.assertNotIn("05-dialogue-log.md", copied)
        self.assertNotIn("09-usage-example.md", copied)
        self.assertFalse(any(p.startswith("raw/") for p in copied))
        self.assertEqual(result["proposed_manifest"]["schema_version"], "2.0")
        self.assertEqual(result["proposed_manifest"]["visibility"], "private")
        self.assertNotIn("ORIGINAL FIXTURE BODY", json.dumps(result))
        self.assertEqual(result, self.plan())

    def test_git_workspace_cloud_and_existing_target_are_rejected(self):
        other = self.base / "other-repo"
        other.mkdir()
        (other / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
        for library in (self.workspace / "ignored", self.base, other / "library", self.base / "OneDrive - Example" / "library"):
            with self.subTest(library=library):
                self.library = library
                with self.assertRaises(ValueError):
                    self.plan()
        self.library = self.base / "private-library"
        (self.library / "sample").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.plan()

    def test_custom_sync_environment_is_checked(self):
        with patch.dict(os.environ, {"OneDrive": str(self.library)}):
            with self.assertRaisesRegex(ValueError, "synced"):
                self.plan()

    def test_symlink_ancestor_and_traversal_cannot_bypass_checks(self):
        with self.assertRaisesRegex(ValueError, "traversal"):
            migration.plain_path(self.base / "something/../elsewhere")
        alias = self.base / "alias"
        try:
            alias.symlink_to(self.workspace, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.library = alias / "private-library"
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.plan()

    def test_policy_checked_before_source_access(self):
        self.policy["execution_policy"] = {"mode": "declared_services", "allowed_services": ["cloud"]}
        self.policy["distribution_policy"]["mode"] = "review_required"
        with patch.object(migration, "package_file", side_effect=AssertionError("source opened")):
            with self.assertRaisesRegex(ValueError, "local_only"):
                self.plan()

    def test_windows_reparse_ancestor_is_rejected_without_following_it(self):
        original = Path.lstat
        def metadata(path):
            if path == self.library:
                return SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, "lstat", metadata):
            with self.assertRaisesRegex(ValueError, "reparse"):
                migration.plain_path(self.library / "sample")

    def test_restricted_source_cannot_remain_in_git(self):
        self.manifest["subject_kind"] = "living_private"
        self.policy["subject_kind"] = "living_private"
        self.save()
        with self.assertRaisesRegex(ValueError, "workspace"):
            self.plan()

    def test_all_kinds_work_from_external_source(self):
        self.source = self.base / "external-source"
        self.source.mkdir()
        for relative in self.manifest["files"].values():
            (self.source / relative).write_text("Fixture", encoding="utf-8")
        for kind in ("historical_public", "living_public", "living_private", "deceased_private", "self", "fictional"):
            with self.subTest(kind=kind):
                self.manifest["subject_kind"] = kind
                self.policy["subject_kind"] = kind
                self.save()
                self.assertEqual(self.plan()["proposed_manifest"]["subject_kind"], kind)

    def test_identity_change_and_protected_files_are_rejected(self):
        self.policy["subject_kind"] = "fictional"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.plan()
        self.policy["subject_kind"] = "historical_public"
        for relative in ("../outside.md", "raw/original.txt", "research.local/summary.md", "./00-profile.md", "manifest.v2.json"):
            with self.subTest(relative=relative):
                self.manifest["files"]["profile"] = relative
                self.save()
                with self.assertRaises(ValueError):
                    self.plan()

    def test_index_dependencies_are_explicit_and_checked(self):
        directory = self.source / "processed/index"
        directory.mkdir(parents=True)
        self.manifest["files"]["search_index_manifest"] = "processed/index/index-manifest.json"
        data = {"full_text_index": {"path": "memory.sqlite3"}, "vector_index": {"path": "vectors.npy", "records": "records.jsonl"}}
        index = directory / "index-manifest.json"
        index.write_text(json.dumps(data), encoding="utf-8")
        for name in ("memory.sqlite3", "vectors.npy", "records.jsonl"):
            (directory / name).write_text("Fixture", encoding="utf-8")
        self.save()
        copied = {f["path"] for f in self.plan()["copy_files"]}
        self.assertIn("processed/index/vectors.npy", copied)
        data["vector_index"]["path"] = "../../raw/original.txt"
        index.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "protected"):
            self.plan()

    def test_cli_summary_does_not_print_identity_paths_or_content(self):
        policy_file = self.base / "policy.json"
        policy_file.write_text(json.dumps(self.policy), encoding="utf-8")
        argv = ["migrate_persona.py", str(self.source), "--library", str(self.library), "--policy", str(policy_file)]
        original = migration.plan_migration
        with patch("sys.argv", argv), patch("sys.stdout", new_callable=io.StringIO) as out:
            with patch.object(migration, "plan_migration", side_effect=lambda s,l,p: original(s,l,p,self.workspace)):
                self.assertEqual(migration.main(), 0)
            output = out.getvalue()
            self.assertNotIn("PRIVATE FIXTURE IDENTITY", output)
            self.assertNotIn(str(self.source), output)
            self.assertNotIn("ORIGINAL FIXTURE BODY", output)
            self.assertTrue(json.loads(output)["dry_run"])

    def test_real_cli_dry_run_then_apply_round_trip(self):
        policy_file = self.base / "policy.json"
        policy_file.write_text(json.dumps(self.policy), encoding="utf-8")
        command = [sys.executable, str(ROOT / "人物蒸馏/scripts/migrate_persona.py"),
                   str(self.source), "--library", str(self.library), "--policy", str(policy_file)]
        env = {**os.environ, "PYTHONUTF8": "1"}
        result = subprocess.run(command, capture_output=True, encoding="utf-8", env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        digest = json.loads(result.stdout)["plan_sha256"]
        result = subprocess.run(command + ["--apply", "--confirm-local-storage", "--expect-plan-sha256", digest],
                                capture_output=True, encoding="utf-8", env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "local_package_created")
        self.assertNotIn("PRIVATE FIXTURE IDENTITY", result.stdout)
