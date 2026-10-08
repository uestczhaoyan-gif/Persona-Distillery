"""Deletion planning must be complete, read-only and contained to a v2 package."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import test_local_package as fixtures
import delete_persona as deletion


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


if __name__ == "__main__":
    unittest.main()
