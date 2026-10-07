"""Fictional upload fixtures; immutable snapshots, privacy gates and incremental IDs."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("local_distill", ROOT / "人物蒸馏/scripts/distill.py")
distill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distill)


class LocalDistillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "人物蒸馏/schemas", self.root / "人物蒸馏/schemas")
        self.incoming = self.root / "incoming"
        self.incoming.mkdir()
        self.config = self.root / "job.json"
        self.job = json.loads((ROOT / "人物蒸馏/examples/private-upload.json").read_text(encoding="utf-8"))
        self.job["job"]["id"] = "fictional-test-001"
        self.job["subject"].update(person_id="fictional-teacher", display_name="虚构教学夹具", kind="fictional")
        self.job["input"]["upload"] = {"incoming_directory": "incoming"}
        self.job.pop("consent", None)
        self.write_config()
        (self.incoming / "first.md").write_text("项目原创夹具。\n做判断前先写一个可以验证的问题。", encoding="utf-8")

    def write_config(self):
        self.config.write_text(json.dumps(self.job, ensure_ascii=False), encoding="utf-8")

    def start(self):
        return distill.initialize(self.config, self.root)

    def approval(self, gate):
        return distill.approve(self.job["job"]["id"], gate, "fixture-reviewer", "Synthetic fixture reviewed for test", self.root)

    def build(self, update=False):
        return distill.build(self.job["job"]["id"], self.root, update)

    def ready(self):
        self.start()
        self.approval("identity")
        self.approval("privacy")
        return self.build()

    def report(self, state):
        return distill.read_json(distill.run_dir(self.job["job"]["id"], self.root) / state["snapshot"] / "report.json")

    def test_gates_and_offline_idempotent_snapshot(self):
        self.start()
        self.assertEqual(self.build()["status"], "identity_review")
        self.approval("identity")
        self.assertEqual(self.build()["status"], "privacy_review")
        self.approval("privacy")
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            state = self.build()
        self.assertEqual(state["status"], "review_required")
        self.assertEqual(state, self.build())
        report = self.report(state)
        self.assertEqual(report["evidence_cards"], 0)
        self.assertFalse(report["persona_generated"])
        self.assertFalse(report["independent_sources_verified"])
        chunks = distill.run_dir(self.job["job"]["id"], self.root) / state["snapshot"] / "chunks.jsonl"
        for line in chunks.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            self.assertEqual(item["delivery_channel"], "local_only")
            self.assertFalse(item["direct_quote"])
            schema = distill.read_json(self.root / "人物蒸馏/schemas/normalized-chunk.schema.json")
            self.assertEqual(list(distill.Draft202012Validator(schema).iter_errors(item)), [])

    def test_content_duplicates_and_corrupt_files_are_isolated(self):
        (self.incoming / "copy.txt").write_bytes("项目原创夹具。\r\n做判断前先写一个可以验证的问题。".encode("utf-8"))
        (self.incoming / "corrupt.txt").write_bytes(b"\xff\x00")
        (self.incoming / "unsupported.pdf").write_bytes(b"fake pdf")
        report = self.report(self.ready())
        self.assertEqual(report["unique_text_sources"], 1)
        self.assertEqual(len(report["duplicates"]), 1)
        self.assertEqual(len(report["quarantined"]), 2)

    def test_input_changes_require_new_privacy_review_and_update(self):
        first = self.ready()
        (self.incoming / "second.txt").write_text("另一份独立原创测试文本。", encoding="utf-8")
        self.assertEqual(self.build()["status"], "privacy_review")
        self.approval("privacy")
        self.assertEqual(self.build()["status"], "input_changed_use_update")
        second = self.build(update=True)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(set(first["source_ids"].values()) & set(second["source_ids"].values()), set(first["source_ids"].values()))
        directory = distill.run_dir(self.job["job"]["id"], self.root)
        self.assertTrue((directory / first["snapshot"]).is_dir())
        self.assertEqual(len(self.report(second)["added_source_ids"]), 1)

    def test_changed_content_gets_new_id_and_old_snapshot_is_preserved(self):
        first = self.ready()
        old_report = self.report(first)
        (self.incoming / "first.md").write_text("完全改变的新文本。", encoding="utf-8")
        self.approval("privacy")
        second = self.build(update=True)
        self.assertEqual(len(self.report(second)["withdrawn_source_ids"]), 1)
        self.assertEqual(self.report(first), old_report)

    def test_sensitive_contacts_pause_then_are_masked(self):
        (self.incoming / "contact.txt").write_text("虚构邮箱 test@example.invalid，手机号 13800000000。", encoding="utf-8")
        self.start()
        self.approval("identity")
        self.approval("privacy")
        self.assertEqual(self.build()["status"], "sensitivity_review")
        self.approval("sensitivity")
        state = self.build()
        output = (distill.run_dir(self.job["job"]["id"], self.root) / state["snapshot"] / "chunks.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("13800000000", output)
        self.assertNotIn("test@example.invalid", output)
        self.assertIn("REDACTED", output)

    def test_credentials_are_quarantined_even_after_review(self):
        (self.incoming / "secret.txt").write_text("api_key: fictional-test-secret", encoding="utf-8")
        self.start()
        self.approval("identity")
        self.approval("privacy")
        self.assertEqual(self.build()["status"], "sensitivity_review")
        self.approval("sensitivity")
        report = self.report(self.build())
        self.assertIn("credential_requires_clean_input", [r["reason"] for r in report["quarantined"]])

    def test_living_subject_requires_real_consent_record(self):
        self.job["subject"]["kind"] = "living_private"
        self.job["consent"] = {"status": "granted", "record_file": "consent.private.md"}
        self.write_config()
        self.start()
        self.approval("identity")
        self.assertEqual(self.build()["status"], "consent_review")

    def test_frozen_config_and_paths_cannot_be_modified(self):
        self.start()
        directory = distill.run_dir(self.job["job"]["id"], self.root)
        paths = distill.read_json(directory / "paths.private.json")
        paths["incoming"] = str(self.root)
        distill.write_json(directory / "paths.private.json", paths)
        with self.assertRaisesRegex(ValueError, "Frozen"):
            self.build()

    def test_bad_modes_policies_and_ids_are_rejected(self):
        self.job["privacy"]["network_enabled"] = True
        self.write_config()
        with self.assertRaisesRegex(ValueError, "Local runner"):
            self.start()
        with self.assertRaisesRegex(ValueError, "job ID"):
            distill.run_dir("../escape", self.root)

    def test_chunk_boundaries_preserve_normalized_text(self):
        text = "首段。\n" + "长" * 2100 + "\n最后一段。"
        parts = distill.segment(text)
        self.assertEqual("".join(part for part, _ in parts), text)
        self.assertTrue(all(len(part) <= 800 for part, _ in parts))

    def test_lock_prevents_reentry(self):
        self.start()
        directory = distill.run_dir(self.job["job"]["id"], self.root)
        with distill.locked(directory):
            with self.assertRaisesRegex(ValueError, "locked"):
                self.build()
