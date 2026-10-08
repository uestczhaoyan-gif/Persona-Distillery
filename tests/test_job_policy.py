"""V2 job policy remains consistent with packages and fails closed in v1."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "人物蒸馏/scripts"))
import distill
from package_policy import require_legacy_job


class JobPolicyTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / "人物蒸馏/schemas/distillation-job-v2.schema.json").read_text(encoding="utf-8"))
        self.job = json.loads((ROOT / "人物蒸馏/examples/v2/local-fictional-upload.json").read_text(encoding="utf-8"))
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def valid(self, value):
        return not list(self.validator.iter_errors(value))

    def test_policy_contract_is_embedded_without_drift(self):
        standalone = json.loads((ROOT / "人物蒸馏/schemas/persona-policy.schema.json").read_text(encoding="utf-8"))
        for key in ("$schema", "$id", "title"):
            standalone.pop(key, None)
        self.assertEqual(self.schema["properties"]["policy"], standalone)
        Draft202012Validator.check_schema(self.schema)

    def test_all_subject_types_allow_local_declarations_without_blanket_consent(self):
        for kind in self.schema["properties"]["policy"]["properties"]["subject_kind"]["enum"]:
            job = copy.deepcopy(self.job)
            job["subject"]["kind"] = job["policy"]["subject_kind"] = kind
            job["policy"]["material_basis"] = ["user_recollection"]
            job["material_declaration"]["recollection_acknowledged"] = True
            self.assertTrue(self.valid(job), kind)

    def test_subject_policy_mismatch_and_remote_settings_are_rejected(self):
        value = copy.deepcopy(self.job)
        value["subject"]["kind"] = "living_private"
        self.assertFalse(self.valid(value))
        value = copy.deepcopy(self.job)
        value["policy"]["execution_policy"] = {"mode": "declared_services", "allowed_services": ["cloud"]}
        value["policy"]["distribution_policy"]["mode"] = "review_required"
        self.assertFalse(self.valid(value))
        self.job["input"]["mode"] = "web"
        self.assertFalse(self.valid(self.job))

    def test_private_communications_require_targeted_authorization_record(self):
        self.job["material_declaration"]["private_communications"] = True
        self.assertFalse(self.valid(self.job))
        self.job["material_declaration"]["authorization_record"] = "./permission.local.md"
        self.assertTrue(self.valid(self.job))
        self.job["material_declaration"]["rights_to_process"] = False
        self.assertFalse(self.valid(self.job))

    def test_recollection_and_date_validation(self):
        self.job["policy"]["material_basis"] = ["user_recollection"]
        self.assertFalse(self.valid(self.job))
        self.job["material_declaration"]["recollection_acknowledged"] = True
        self.assertTrue(self.valid(self.job))
        self.job["material_declaration"]["declared_on"] = "2026-02-30"
        self.assertFalse(self.valid(self.job))

    def test_new_policy_and_unknown_versions_cannot_downgrade(self):
        for version in ("2.0", "9.0", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                require_legacy_job(dict(self.job, schema_version=version))
        with self.assertRaisesRegex(ValueError, "downgraded"):
            require_legacy_job(dict(self.job, schema_version="1.0"))

    def test_legacy_resume_rejects_v2_before_private_paths_or_state(self):
        calls = []
        def metadata_only(path):
            calls.append(path.name)
            if path.name != "job.json":
                raise AssertionError("Private state read before version admission")
            return self.job
        with patch.object(distill, "read_json", side_effect=metadata_only):
            with self.assertRaisesRegex(ValueError, "schema_version"):
                distill.load_job(Path("synthetic-job"), ROOT)
        self.assertEqual(calls, ["job.json"])


if __name__ == "__main__":
    unittest.main()
