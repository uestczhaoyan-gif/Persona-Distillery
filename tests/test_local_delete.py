"""Deletion planning must be complete, read-only and contained to a v2 package."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_local_package as fixtures
import delete_persona as deletion
import backup_persona as backup


class LocalDeletionTests(unittest.TestCase):
    save = fixtures.LocalPackageTests.save

    def setUp(self):
        fixtures.LocalPackageTests.setUp(self)
        (self.package / "raw/empty").mkdir(parents=True)
        (self.package / "raw/private.txt").write_text("SYNTHETIC PRIVATE RAW", encoding="utf-8")
        self.outside = self.base / "outside-private.txt"
        self.outside.write_text("OUTSIDE FIXTURE", encoding="utf-8")

    def plan(self):
        return deletion.plan_delete(self.package, confirm_local_storage=True)

    def test_full_scope_includes_raw_and_logs_but_never_external_files(self):
        before = {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        plan = self.plan()
        names = {item["path"] for item in plan["files"]}
        self.assertIn("raw/private.txt", names)
        self.assertIn(self.manifest["files"]["dialogue_log"], names)
        self.assertIn("raw/empty", plan["directories"])
        self.assertNotIn(self.outside.name, names)
        self.assertEqual(len(plan["not_covered"]), 4)
        self.assertEqual(before, {p.relative_to(self.base): p.read_bytes() for p in self.base.rglob("*") if p.is_file()})

    def test_invalid_policy_or_confirmation_is_rejected_before_inventory(self):
        with patch.object(deletion, "inventory", side_effect=AssertionError("body read")):
            with self.assertRaises(ValueError):
                deletion.plan_delete(self.package, confirm_local_storage=False)
            self.manifest["schema_version"] = "9.0"
            self.save()
            with self.assertRaises(ValueError):
                self.plan()

    def test_incomplete_locked_or_git_storage_is_rejected(self):
        for marker in (self.package / ".deletion-incomplete.json", self.package.parent / ".deletion.lock",
                       self.package / ".backup-incomplete.json", self.base / ".git"):
            with self.subTest(marker=marker.name):
                marker.write_text("{}", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.plan()
                marker.unlink()

    def test_parent_directory_cannot_be_selected_as_a_package(self):
        with self.assertRaises(ValueError):
            deletion.plan_delete(self.package.parent, confirm_local_storage=True)
        self.manifest["person_id"] = "different-id"
        self.save()
        with self.assertRaisesRegex(ValueError, "identity"):
            self.plan()

    def test_plan_digest_changes_with_content_or_additional_files(self):
        first = deletion.plan_digest(self.plan())
        (self.package / "raw/private.txt").write_text("UPDATED", encoding="utf-8")
        second = deletion.plan_digest(self.plan())
        self.assertNotEqual(first, second)
        (self.package / "new.txt").write_text("NEW", encoding="utf-8")
        self.assertNotEqual(second, deletion.plan_digest(self.plan()))

    def test_default_cli_is_metadata_only_and_show_plan_requires_terminal(self):
        out, err = io.StringIO(), io.StringIO()
        args = [str(self.package), "--confirm-local-storage"]
        with contextlib.redirect_stdout(out):
            self.assertEqual(deletion.main(args), 0)
        for private in (str(self.package), "raw/private.txt", "SYNTHETIC PRIVATE RAW"):
            self.assertNotIn(private, out.getvalue())
        self.assertTrue(json.loads(out.getvalue())["dry_run"])
        with contextlib.redirect_stderr(err), patch.object(deletion, "plan_delete", side_effect=AssertionError("body read")):
            self.assertEqual(deletion.main([*args, "--show-plan"]), 1)

    def apply(self, digest, *, confirmed=True, resume=False):
        return deletion.apply_delete(self.package, confirm_local_storage=True,
                                     confirm_delete=confirmed, expected_digest=digest, resume=resume)

    def test_apply_deletes_only_reviewed_package_and_leaves_external_file(self):
        plan = self.plan()
        result = self.apply(deletion.plan_digest(plan))
        self.assertEqual(result["status"], "local_package_deleted")
        self.assertEqual(result["deleted_files"], len(plan["files"]))
        self.assertFalse(result["secure_erasure"])
        self.assertFalse(self.package.exists())
        self.assertTrue(self.package.parent.is_dir())
        self.assertEqual(self.outside.read_text(encoding="utf-8"), "OUTSIDE FIXTURE")
        self.assertFalse((self.package.parent / ".deletion.lock").exists())

    def test_confirmation_and_changed_plan_prevent_any_deletion(self):
        digest = deletion.plan_digest(self.plan())
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.apply(digest, confirmed=False)
        (self.package / "new.txt").write_text("NEW", encoding="utf-8")
        with patch.object(deletion, "delete_verified_file", side_effect=AssertionError("deleted")):
            with self.assertRaisesRegex(ValueError, "plan changed"):
                self.apply(digest)
        self.assertTrue((self.package / "new.txt").exists())
        self.assertFalse((self.package / ".deletion-incomplete.json").exists())

    def test_interrupted_deletion_is_unloadable_and_unbackupable(self):
        digest = deletion.plan_digest(self.plan())
        with patch.object(deletion, "delete_verified_file", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.apply(digest)
        marker = self.package / ".deletion-incomplete.json"
        self.assertTrue(marker.is_file())
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["plan_sha256"], digest)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            fixtures.loader.load_local_context(self.package, confirm_local_storage=True, preview=True)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            backup.plan_backup(self.package, self.base / "new-backup", confirm_local_storage=True)
        self.assertFalse((self.package.parent / ".deletion.lock").exists())

    def test_mid_delete_edit_is_preserved_and_marker_remains(self):
        digest = deletion.plan_digest(self.plan())
        original = deletion.delete_verified_file
        def edited(package, record):
            if record["path"] == "raw/private.txt":
                (package / record["path"]).write_text("NEW PRIVATE CONTENT", encoding="utf-8")
            original(package, record)
        with patch.object(deletion, "delete_verified_file", side_effect=edited):
            with self.assertRaisesRegex(ValueError, "changed before deletion"):
                self.apply(digest)
        self.assertEqual((self.package / "raw/private.txt").read_text(encoding="utf-8"), "NEW PRIVATE CONTENT")
        self.assertTrue((self.package / ".deletion-incomplete.json").is_file())

    def test_unlisted_new_file_is_never_removed(self):
        digest = deletion.plan_digest(self.plan())
        original = deletion.delete_verified_file
        def extra(package, record):
            original(package, record)
            (package / "unexpected.txt").write_text("PRESERVE", encoding="utf-8")
        with patch.object(deletion, "delete_verified_file", side_effect=extra):
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                self.apply(digest)
        self.assertEqual((self.package / "unexpected.txt").read_text(encoding="utf-8"), "PRESERVE")
        self.assertTrue((self.package / ".deletion-incomplete.json").is_file())

    def test_deletion_path_refuses_escape_and_root(self):
        for relative in ("../outside-private.txt", str(self.outside), ".", "raw/../../outside-private.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                deletion.deletion_path(self.package, relative)
        self.assertTrue(self.outside.exists())

    def interrupt_after_one_file(self):
        plan = self.plan()
        digest = deletion.plan_digest(plan)
        original = deletion.delete_verified_file
        count = []
        def interrupted(package, record):
            count.append(True)
            if len(count) == 2:
                raise OSError("Simulated interruption")
            original(package, record)
        with patch.object(deletion, "delete_verified_file", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.apply(digest)
        return plan, digest

    def test_resume_only_removes_unchanged_remaining_files(self):
        plan, digest = self.interrupt_after_one_file()
        original, remaining, _, _ = deletion.resume_state(self.package, digest, confirm_local_storage=True)
        self.assertEqual(original, plan)
        self.assertEqual(len(remaining), len(plan["files"]) - 1)
        result = self.apply(digest, resume=True)
        self.assertEqual(result["deleted_files"], len(remaining))
        self.assertFalse(self.package.exists())
        self.assertTrue(self.outside.exists())

    def test_resume_rejects_edited_or_unlisted_content_before_any_mutation(self):
        _, digest = self.interrupt_after_one_file()
        extra = self.package / "unexpected.txt"
        extra.write_text("PRESERVE", encoding="utf-8")
        with patch.object(deletion, "delete_verified_file", side_effect=AssertionError("deleted")):
            with self.assertRaisesRegex(ValueError, "Changed or unlisted"):
                self.apply(digest, resume=True)
        self.assertTrue(extra.exists())
        extra.unlink()
        (self.package / "raw/private.txt").write_text("CHANGED", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Changed or unlisted"):
            self.apply(digest, resume=True)

    def test_tampered_journal_or_wrong_hash_does_not_authorize_resume(self):
        _, digest = self.interrupt_after_one_file()
        with self.assertRaisesRegex(ValueError, "original approved hash"):
            self.apply("0" * 64, resume=True)
        marker = self.package / ".deletion-incomplete.json"
        state = json.loads(marker.read_text(encoding="utf-8"))
        state["plan"]["files"][0]["path"] = "../outside-private.txt"
        marker.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "original approved hash"):
            self.apply(digest, resume=True)
        self.assertTrue(self.outside.exists())

    def test_resume_works_after_manifest_deleted_and_directory_cleanup_interrupted(self):
        from pathlib import Path
        digest = deletion.plan_digest(self.plan())
        original = Path.rmdir
        def interrupted(path):
            if path == self.package / "raw/empty":
                raise OSError("Simulated directory cleanup failure")
            return original(path)
        with patch.object(Path, "rmdir", interrupted):
            with self.assertRaises(OSError):
                self.apply(digest)
        self.assertFalse((self.package / "manifest.v2.json").exists())
        result = self.apply(digest, resume=True)
        self.assertEqual(result["deleted_files"], 0)
        self.assertFalse(self.package.exists())

    def test_resume_preview_cli_reports_remaining_counts_without_deleting(self):
        plan, digest = self.interrupt_after_one_file()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = deletion.main([str(self.package), "--confirm-local-storage", "--resume",
                                  "--expect-plan-sha256", digest])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["file_count"], len(plan["files"]) - 1)
        self.assertTrue(self.package.exists())
        self.assertNotIn(str(self.package), out.getvalue())
        applied = io.StringIO()
        with contextlib.redirect_stdout(applied):
            code = deletion.main([str(self.package), "--confirm-local-storage", "--resume", "--apply",
                                  "--confirm-delete", "--expect-plan-sha256", digest])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(applied.getvalue())["status"], "local_package_deleted")
        self.assertFalse(self.package.exists())


if __name__ == "__main__":
    unittest.main()
