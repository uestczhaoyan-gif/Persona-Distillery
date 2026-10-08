"""Read-only backup planning with synthetic external local packages."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_local_package as fixtures
import backup_persona as backup


class LocalBackupTests(unittest.TestCase):
    save = fixtures.LocalPackageTests.save

    def setUp(self):
        fixtures.LocalPackageTests.setUp(self)
        self.destination = self.base / "backup-library"
        (self.package / "raw/empty").mkdir(parents=True)
        (self.package / "raw/input.txt").write_text("PRIVATE SYNTHETIC RAW", encoding="utf-8")

    def plan(self):
        return backup.plan_backup(self.package, self.destination, confirm_local_storage=True)

    def test_plan_preserves_policy_and_covers_raw_logs_and_empty_directories(self):
        before = {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob("*") if p.is_file()}
        plan = self.plan()
        self.assertEqual(plan["distribution_mode"], "local_only")
        self.assertEqual({record["path"] for record in plan["files"]}, {p.as_posix() for p in before})
        self.assertIn("raw/empty", plan["directories"])
        self.assertTrue(all(len(record["sha256"]) == 64 for record in plan["files"]))
        self.assertEqual(before, {p.relative_to(self.package): p.read_bytes() for p in self.package.rglob("*") if p.is_file()})
        self.assertFalse(self.destination.exists())

    def test_no_confirmation_or_invalid_policy_reads_no_inventory(self):
        with patch.object(backup, "inventory", side_effect=AssertionError("body read")):
            with self.assertRaisesRegex(ValueError, "Confirm"):
                backup.plan_backup(self.package, self.destination, confirm_local_storage=False)
            self.manifest["schema_version"] = "9.0"
            self.save()
            with self.assertRaises(ValueError):
                self.plan()

    def test_git_cloud_overlap_and_existing_destination_rejected(self):
        for location in (self.package / "backup", self.base / "OneDrive/backups"):
            with self.subTest(location=location.name), self.assertRaises(ValueError):
                backup.plan_backup(self.package, location, confirm_local_storage=True)
        self.destination.mkdir()
        (self.destination / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "Git"):
            self.plan()
        (self.destination / ".git").rmdir()
        (self.destination / "sample").mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.plan()

    def test_incomplete_package_or_nested_git_is_rejected(self):
        marker = self.package / ".backup-incomplete.json"
        marker.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.plan()
        marker.unlink()
        (self.package / "raw/.git").mkdir()
        with self.assertRaisesRegex(ValueError, "Nested Git"):
            self.plan()

    def test_source_edits_change_digest_and_inaccessible_walk_fails_closed(self):
        original = backup.plan_digest(self.plan())
        (self.package / "raw/input.txt").write_text("CHANGED", encoding="utf-8")
        self.assertNotEqual(original, backup.plan_digest(self.plan()))
        def failed_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("SENSITIVE PATH"))
            return iter(())
        with patch.object(backup.os, "walk", side_effect=failed_walk):
            with self.assertRaisesRegex(ValueError, "complete backup source") as caught:
                self.plan()
        self.assertNotIn("SENSITIVE PATH", str(caught.exception))

    def test_cli_outputs_counts_and_digest_without_identity_paths_or_content(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = backup.main([str(self.package), str(self.destination), "--confirm-local-storage"])
        self.assertEqual(code, 0)
        data = json.loads(out.getvalue())
        self.assertTrue(data["dry_run"])
        for private in (str(self.package), "sample", "PRIVATE SYNTHETIC RAW", "raw/input.txt"):
            self.assertNotIn(private, out.getvalue())
        self.assertFalse(self.destination.exists())

    def apply(self, digest):
        return backup.apply_backup(self.package, self.destination, confirm_local_storage=True,
                                   expected_digest=digest)

    def test_apply_roundtrip_preserves_every_byte_and_source(self):
        plan = self.plan()
        result = self.apply(backup.plan_digest(plan))
        self.assertEqual(result["status"], "local_backup_created")
        target = self.destination / "sample"
        self.assertEqual(backup.inventory(target), backup.inventory(self.package))
        self.assertFalse((target / ".backup-incomplete.json").exists())
        self.assertFalse((self.destination / ".backup.lock").exists())
        loaded = fixtures.loader.load_local_context(target, confirm_local_storage=True, preview=True)
        self.assertIn("SYNTHETIC", loaded.context)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.apply(backup.plan_digest(plan))

    def test_mismatched_plan_and_existing_lock_do_not_copy(self):
        with self.assertRaisesRegex(ValueError, "plan changed"):
            self.apply("0" * 64)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        lock = self.destination / ".backup.lock"
        lock.write_text("another owner", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.apply(backup.plan_digest(self.plan()))
        self.assertEqual(lock.read_text(encoding="utf-8"), "another owner")
        self.assertFalse((self.destination / "sample").exists())

    def test_interrupted_copy_stays_unloadable_and_does_not_overwrite(self):
        digest = backup.plan_digest(self.plan())
        with patch.object(backup, "copy_verified", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                self.apply(digest)
        target = self.destination / "sample"
        self.assertTrue((target / ".backup-incomplete.json").exists())
        self.assertFalse((self.destination / ".backup.lock").exists())
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            fixtures.loader.load_local_context(target, confirm_local_storage=True, preview=True)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.apply(digest)

    def test_source_edit_during_copy_prevents_completion(self):
        digest = backup.plan_digest(self.plan())
        original = backup.copy_verified
        def changed(source, target, record):
            original(source, target, record)
            if record["path"] == "raw/input.txt":
                source.write_text("MODIFIED AFTER COPY", encoding="utf-8")
        with patch.object(backup, "copy_verified", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "source changed"):
                self.apply(digest)
        self.assertTrue((self.destination / "sample/.backup-incomplete.json").exists())

    def test_temporary_marker_does_not_consume_the_source_file_limit(self):
        plan = self.plan()
        with patch.object(backup, "MAX_FILES", len(plan["files"])):
            result = self.apply(backup.plan_digest(plan))
        self.assertEqual(result["file_count"], len(plan["files"]))


if __name__ == "__main__":
    unittest.main()
