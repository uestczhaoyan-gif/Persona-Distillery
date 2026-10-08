"""Policy contract matrix uses synthetic declarations, not real private data."""
import copy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = json.loads((Path(__file__).resolve().parents[1] /
                    "人物蒸馏/schemas/persona-policy.schema.json").read_text(encoding="utf-8"))
KINDS = ("historical_public", "deceased_private", "living_public", "living_private", "self", "fictional")


def fixture(kind="historical_public"):
    return {"policy_version": "1.0", "subject_kind": kind, "sensitive_data": False,
            "material_basis": ["public_source"], "review_status": "candidate",
            "execution_policy": {"mode": "local_only", "allowed_services": []},
            "distribution_policy": {"mode": "local_only"}}


class PolicySchemaTests(unittest.TestCase):
    def setUp(self):
        self.validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

    def valid(self, value):
        return self.validator.is_valid(value)

    def test_all_subject_types_can_have_local_candidate(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertTrue(self.valid(fixture(kind)))

    def test_living_and_private_types_cannot_enable_remote_or_distribution(self):
        for kind in ("deceased_private", "living_public", "living_private"):
            for remote in (False, True):
                with self.subTest(kind=kind, remote=remote):
                    value = fixture(kind)
                    value["distribution_policy"]["mode"] = "review_required"
                    if remote:
                        value["execution_policy"] = {"mode": "declared_services", "allowed_services": ["example_llm"]}
                    self.assertFalse(self.valid(value))

    def test_sensitive_or_recollection_material_forces_local_even_for_self(self):
        for kind in KINDS:
            for basis in ("authorized_private", "user_recollection", None):
                with self.subTest(kind=kind, basis=basis):
                    value = fixture(kind)
                    if basis:
                        value["material_basis"].append(basis)
                    else:
                        value["sensitive_data"] = True
                    self.assertTrue(self.valid(value))
                    value["distribution_policy"]["mode"] = "review_required"
                    self.assertFalse(self.valid(value))

    def test_eligible_public_inputs_can_explicitly_select_services(self):
        for kind in ("historical_public", "self", "fictional"):
            value = fixture(kind)
            value["distribution_policy"]["mode"] = "review_required"
            value["execution_policy"] = {"mode": "declared_services", "allowed_services": ["example_llm"]}
            self.assertTrue(self.valid(value))
            value["execution_policy"]["allowed_services"] = []
            self.assertFalse(self.valid(value))

    def test_local_policy_cannot_hide_services(self):
        value = fixture()
        value["execution_policy"]["allowed_services"] = ["example_llm"]
        self.assertFalse(self.valid(value))
        value["execution_policy"]["mode"] = "declared_services"
        self.assertFalse(self.valid(value))

    def test_public_approval_requires_separate_version_bound_review(self):
        value = fixture()
        value["distribution_policy"]["mode"] = "public_approved"
        self.assertFalse(self.valid(value))
        value["review_status"] = "reviewed"
        self.assertFalse(self.valid(value))
        value["publication_review"] = {"record_id": "review-001", "reviewer_id": "reviewer-001",
                                       "reviewed_on": "2026-10-08", "content_digest": "a" * 64}
        self.assertTrue(self.valid(value))
        value["publication_review"]["reviewed_on"] = "2026-02-30"
        self.assertFalse(self.valid(value))
        value["publication_review"]["reviewed_on"] = "2026-10-08"
        value["distribution_policy"]["mode"] = "local_only"
        self.assertFalse(self.valid(value))

    def test_missing_malformed_unknown_and_duplicate_fields_fail(self):
        for key in SCHEMA["required"]:
            value = fixture()
            del value[key]
            self.assertFalse(self.valid(value), key)
        for key, bad in (("policy_version", "2.0"), ("sensitive_data", "false"),
                         ("material_basis", []), ("material_basis", ["public_source"] * 2),
                         ("subject_kind", "unknown"), ("review_status", "approved")):
            value = fixture()
            value[key] = bad
            self.assertFalse(self.valid(value), key)
        for section in (None, "execution_policy", "distribution_policy"):
            value = copy.deepcopy(fixture())
            (value if section is None else value[section])["override"] = True
            self.assertFalse(self.valid(value))
