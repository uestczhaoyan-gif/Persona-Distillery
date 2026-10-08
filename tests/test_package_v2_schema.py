"""The new package format retains v1 roles but cannot imply public permission."""
import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "人物蒸馏/schemas/persona-package-v2.schema.json").read_text(encoding="utf-8"))


class PackageV2SchemaTests(unittest.TestCase):
    def setUp(self):
        self.validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
        self.value = json.loads((ROOT / "personas/_template/manifest.json").read_text(encoding="utf-8"))
        self.value.update(schema_version="2.0", policy_version="1.0", sensitive_data=False,
                          material_basis=["self_report"], subject_kind="self", review_status="candidate",
                          execution_policy={"mode": "local_only", "allowed_services": []},
                          distribution_policy={"mode": "local_only"})

    def test_local_draft_preserves_all_existing_package_roles(self):
        self.assertTrue(self.validator.is_valid(self.value))
        legacy = json.loads((ROOT / "人物蒸馏/schemas/persona-package.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(SCHEMA["properties"]["files"], legacy["properties"]["files"])
        self.assertTrue(set(legacy["required"]).issubset(SCHEMA["required"]))

    def test_policy_fields_and_constraints_match_standalone_contract(self):
        policy = json.loads((ROOT / "人物蒸馏/schemas/persona-policy.schema.json").read_text(encoding="utf-8"))
        for name, rule in policy["properties"].items():
            self.assertEqual(SCHEMA["properties"][name], rule)
        self.assertEqual(SCHEMA["allOf"][:len(policy["allOf"])], policy["allOf"])

    def test_quality_does_not_grant_public_visibility(self):
        self.value["readiness"] = "publish-ready"
        self.value["files"].update(avatar_png="assets/avatar.png", avatar_webp="assets/avatar.webp")
        self.assertTrue(self.validator.is_valid(self.value))
        self.value["visibility"] = "public"
        self.assertFalse(self.validator.is_valid(self.value))

    def test_ready_package_requires_actual_avatar_paths_but_draft_can_wait(self):
        for readiness in ("chat-ready", "simulation-ready", "publish-ready"):
            value = copy.deepcopy(self.value)
            value["readiness"] = readiness
            self.assertFalse(self.validator.is_valid(value))
            value["files"].update(avatar_png="assets/avatar.png", avatar_webp="assets/avatar.webp")
            self.assertTrue(self.validator.is_valid(value))

    def test_new_manifest_cannot_pass_legacy_schema_or_team_visibility(self):
        legacy = json.loads((ROOT / "人物蒸馏/schemas/persona-package.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(Draft202012Validator(legacy).is_valid(self.value))
        self.value["visibility"] = "team"
        self.assertFalse(self.validator.is_valid(self.value))

    def test_public_candidate_cannot_bypass_review_and_quality(self):
        self.value.update(subject_kind="historical_public", material_basis=["public_source"],
                          review_status="reviewed", visibility="public",
                          distribution_policy={"mode":"public_approved"},
                          publication_review={"record_id":"review-01", "reviewer_id":"reviewer-01",
                                              "reviewed_on":"2026-10-08", "content_digest":"a"*64})
        self.assertFalse(self.validator.is_valid(self.value))
        self.value["readiness"] = "chat-ready"
        self.value["files"].update(avatar_png="assets/avatar.png", avatar_webp="assets/avatar.webp")
        self.assertTrue(self.validator.is_valid(self.value))
