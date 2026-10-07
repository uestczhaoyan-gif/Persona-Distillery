"""Validate examples and reject malformed contracts without model calls."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repository_validation", ROOT / "tools/validate_repository.py")
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class RepositoryContractTests(unittest.TestCase):
    def test_moderator_example_and_unknown_fields(self):
        schema = json.loads((ROOT / "圆桌会议/schemas/moderator-decision.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "圆桌会议/_template/moderator-decision.example.json").read_text(encoding="utf-8"))
        self.assertEqual(validation.schema_errors(example, schema, "example"), [])
        example["accidental_field"] = True
        self.assertTrue(validation.schema_errors(example, schema, "example"))

    def test_upload_privacy_contract_rejects_remote_processing(self):
        schema = json.loads((ROOT / "人物蒸馏/schemas/distillation-job.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "人物蒸馏/examples/private-upload.json").read_text(encoding="utf-8"))
        self.assertEqual(validation.schema_errors(example, schema, "example"), [])
        example["privacy"]["remote_processing_allowed"] = True
        self.assertTrue(validation.schema_errors(example, schema, "example"))

    def test_date_format_is_checked(self):
        self.assertTrue(validation.schema_errors("not-a-date", {"type": "string", "format": "date"}, "date"))
