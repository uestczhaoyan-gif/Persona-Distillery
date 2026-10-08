"""External v2 job initialization uses synthetic data only."""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_local_package as fixtures
import local_distill as local


class LocalDistillationTests(unittest.TestCase):
    save = fixtures.LocalPackageTests.save

    def setUp(self):
        fixtures.LocalPackageTests.setUp(self)
        self.library = self.base / "jobs"
        self.incoming = self.base / "incoming"
        self.incoming.mkdir()
        (self.incoming / "source.txt").write_text("SYNTHETIC PRIVATE SOURCE", encoding="utf-8")
        self.config = self.base / "job.json"
        self.job = json.loads((fixtures.ROOT / "人物蒸馏/examples/v2/local-fictional-upload.json").read_text(encoding="utf-8"))
        self.job["output"]["personas_directory"] = "./personas-output"
        self.write_config()

    def write_config(self):
        self.config.write_text(json.dumps(self.job), encoding="utf-8")

    def start(self):
        return local.initialize(self.config, self.library, confirm_local_storage=True)

    def load(self):
        return local.load_job(self.library, self.job["job"]["id"], confirm_local_storage=True)

    def test_initialize_and_status_do_not_read_input_bodies_or_use_network(self):
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.is_relative_to(self.incoming):
                raise AssertionError("Input body read during initialization")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded), patch("socket.socket.connect", side_effect=AssertionError("network")):
            state = self.start()
            job, paths, loaded = self.load()
        self.assertEqual(state, loaded)
        self.assertEqual(state["status"], "identity_review")
        self.assertEqual(job, self.job)
        self.assertEqual(paths["incoming"], str(self.incoming.resolve()))
        self.assertFalse((self.base / "personas-output").exists())
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.start()

    def test_all_subject_kinds_initialize_with_local_recollection_declaration(self):
        for number, kind in enumerate(("historical_public", "living_public", "living_private", "deceased_private", "self", "fictional")):
            self.job["job"]["id"] = f"sample-job-{number}"
            self.job["subject"]["kind"] = self.job["policy"]["subject_kind"] = kind
            self.job["policy"]["material_basis"] = ["user_recollection"]
            self.job["material_declaration"]["recollection_acknowledged"] = True
            self.write_config()
            self.assertEqual(self.start()["status"], "identity_review")

    def test_invalid_policy_precedes_input_path_handling(self):
        self.job["policy"]["execution_policy"]["allowed_services"] = ["remote"]
        self.write_config()
        with patch.object(local, "resolve_paths", side_effect=AssertionError("input paths processed")):
            with self.assertRaises(ValueError):
                self.start()
        self.assertFalse(self.library.exists())

    def test_git_sync_and_overlapping_locations_are_rejected(self):
        self.library.mkdir()
        (self.library / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "Git"):
            self.start()
        (self.library / ".git").rmdir()
        for incoming in (str(self.library), str(self.base / "OneDrive/incoming")):
            self.job["input"]["upload"]["incoming_directory"] = incoming
            self.write_config()
            with self.assertRaises(ValueError):
                self.start()
        self.assertEqual(list(self.library.iterdir()), [])

    def test_private_communication_record_must_exist_outside_sources(self):
        declaration = self.job["material_declaration"]
        declaration.update(private_communications=True, authorization_record="./permission.md")
        self.write_config()
        with self.assertRaises(ValueError):
            self.start()
        (self.base / "permission.md").write_text("SYNTHETIC AUTHORIZATION RECORD", encoding="utf-8")
        self.assertEqual(self.start()["status"], "identity_review")
        self.assertIn("authorization", self.load()[1])

    def test_changed_frozen_configuration_and_incomplete_state_are_rejected(self):
        self.start()
        directory = self.library / self.job["job"]["id"]
        marker = directory / ".initializing"
        marker.write_text("incomplete", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.load()
        marker.unlink()
        config = directory / "job.v2.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["subject"]["display_name"] = "CHANGED"
        config.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Frozen"):
            self.load()

    def test_interrupted_initialization_stays_incomplete_and_does_not_overwrite(self):
        original = local.write_new
        def interrupted(path, data):
            if path.name == "state.json":
                raise OSError("synthetic disk failure")
            return original(path, data)
        with patch.object(local, "write_new", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.start()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.load()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.start()
        self.assertFalse((self.library / ".job-init.lock").exists())

    def test_cli_output_does_not_reveal_names_paths_or_body(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = local.main(["--library", str(self.library), "--confirm-local-storage", "init", "--config", str(self.config)])
        self.assertEqual(code, 0)
        for private in (str(self.base), self.job["subject"]["display_name"], self.job["job"]["id"], "SYNTHETIC PRIVATE SOURCE"):
            self.assertNotIn(private, out.getvalue())
        self.assertFalse(json.loads(out.getvalue())["persona_generated"])

    def approve(self, *gates):
        record = self.base / "review.json"
        record.write_text(json.dumps({"reviewer": "fixture-reviewer", "note": "Reviewed synthetic fixture only",
                                      "decision": "approved"}), encoding="utf-8")
        return local.approve(self.library, self.job["job"]["id"], list(gates), record, confirm_local_storage=True)

    def build(self, update=False):
        return local.build(self.library, self.job["job"]["id"], confirm_local_storage=True, update=update)

    def ready(self):
        self.start()
        self.approve("identity", "material")
        return self.build()

    def snapshot(self, state):
        return self.library / self.job["job"]["id"] / state["snapshot"]

    def test_review_gates_and_policy_bearing_idempotent_snapshot(self):
        self.start()
        with patch.object(local, "input_inventory", side_effect=AssertionError("read before identity")):
            self.assertEqual(self.build()["status"], "identity_review")
        self.approve("identity")
        self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        with patch("socket.socket.connect", side_effect=AssertionError("network")):
            state = self.build()
        self.assertEqual(state["status"], "review_required")
        self.assertEqual(self.build(), state)
        snapshot = self.snapshot(state)
        self.assertEqual(json.loads((snapshot / "policy.json").read_text(encoding="utf-8")), self.job["policy"])
        report = json.loads((snapshot / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["persona_generated"])
        self.assertFalse(report["privacy_screening_complete"])
        checksums = json.loads((snapshot / "checksums.json").read_text(encoding="utf-8"))
        self.assertTrue(all(local.sha(snapshot / name) == value for name, value in checksums.items()))
        from jsonschema import Draft202012Validator
        schema = json.loads((fixtures.ROOT / "人物蒸馏/schemas/normalized-chunk.schema.json").read_text(encoding="utf-8"))
        for line in (snapshot / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
            chunk = json.loads(line)
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(chunk)), [])
            self.assertFalse(chunk["direct_quote"])

    def test_input_changes_require_review_then_explicit_increment(self):
        first = self.ready()
        before = (self.snapshot(first) / "chunks.jsonl").read_bytes()
        (self.incoming / "second.md").write_text("Another original fixture statement", encoding="utf-8")
        self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        self.assertEqual(self.build()["status"], "input_changed_use_update")
        second = self.build(update=True)
        self.assertEqual(second["revision"], 2)
        self.assertEqual((self.snapshot(first) / "chunks.jsonl").read_bytes(), before)
        self.assertTrue(set(first["active_source_ids"]).issubset(second["active_source_ids"]))

    def test_contacts_require_review_and_credentials_never_enter_chunks(self):
        (self.incoming / "contact.txt").write_text("Synthetic contact test@example.com", encoding="utf-8")
        (self.incoming / "credential.txt").write_text("api_key: SYNTHETIC-TEST-ONLY", encoding="utf-8")
        self.start()
        self.approve("identity", "material")
        self.assertEqual(self.build()["status"], "sensitivity_review")
        self.approve("sensitivity")
        state = self.build()
        text = (self.snapshot(state) / "chunks.jsonl").read_text(encoding="utf-8")
        self.assertIn("[EMAIL_REDACTED]", text)
        self.assertNotIn("test@example.com", text)
        self.assertNotIn("SYNTHETIC-TEST-ONLY", text)

    def test_duplicate_corrupt_and_unsupported_inputs_are_reported(self):
        (self.incoming / "copy.txt").write_bytes((self.incoming / "source.txt").read_bytes())
        (self.incoming / "broken.txt").write_bytes(b"\xff")
        (self.incoming / "unsupported.pdf").write_bytes(b"not a pdf, original fixture")
        state = self.ready()
        report = json.loads((self.snapshot(state) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["duplicates"]), 1)
        self.assertEqual(len(report["quarantined"]), 2)

    def test_authorization_change_invalidates_material_review(self):
        self.job["material_declaration"].update(private_communications=True, authorization_record="./permission.md")
        record = self.base / "permission.md"
        record.write_text("FIRST SYNTHETIC PERMISSION", encoding="utf-8")
        self.write_config()
        self.ready()
        record.write_text("CHANGED SYNTHETIC PERMISSION", encoding="utf-8")
        self.assertEqual(self.build()["status"], "material_review")

    def test_failed_snapshot_is_not_activated_and_retry_can_complete(self):
        self.start()
        self.approve("identity", "material")
        original = local.write_new
        def fail(path, data):
            if path.name == "checksums.json":
                raise OSError("simulated disk failure")
            return original(path, data)
        with patch.object(local, "write_new", side_effect=fail):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(self.load()[2]["revision"], 0)
        self.assertNotIn("snapshot", self.load()[2])
        self.assertEqual(self.build()["revision"], 1)

    def test_missing_storage_confirmation_or_job_lock_prevents_work(self):
        self.start()
        directory = self.library / self.job["job"]["id"]
        with self.assertRaises(ValueError):
            local.build(self.library, self.job["job"]["id"], confirm_local_storage=False)
        self.assertFalse((directory / ".lock").exists())
        (directory / ".lock").write_text("another owner", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual((directory / ".lock").read_text(encoding="utf-8"), "another owner")

    def test_changed_snapshot_is_not_silently_reported_as_complete(self):
        state = self.ready()
        snapshot = self.snapshot(state)
        review = json.loads((snapshot / "review.json").read_text(encoding="utf-8"))
        self.assertIn("material", review["approvals"])
        (snapshot / "chunks.jsonl").write_text("EDITED", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot content changed"):
            self.build()

    def test_malformed_state_is_rejected_without_traceback_content(self):
        self.start()
        path = self.library / self.job["job"]["id"] / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        state["approvals"] = "SYNTHETIC PRIVATE VALUE"
        path.write_text(json.dumps(state), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = local.main(["--library", str(self.library), "--confirm-local-storage", "build", "--job", self.job["job"]["id"]])
        self.assertEqual(result, 1)
        self.assertNotIn("SYNTHETIC PRIVATE VALUE", err.getvalue())


if __name__ == "__main__":
    unittest.main()
