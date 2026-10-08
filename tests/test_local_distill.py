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


if __name__ == "__main__":
    unittest.main()
