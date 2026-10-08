"""Deterministic egress and mixed-session policy matrix; no model or network."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "人物蒸馏/scripts"))
import package_policy as policy


def local(kind="living_private"):
    return {"policy_version":"1.0", "subject_kind":kind, "sensitive_data":False,
            "material_basis":["public_source"], "review_status":"candidate",
            "execution_policy":{"mode":"local_only", "allowed_services":[]},
            "distribution_policy":{"mode":"local_only"}}


def remote(services):
    result = local("historical_public")
    result["execution_policy"] = {"mode":"declared_services", "allowed_services":services}
    result["distribution_policy"] = {"mode":"review_required"}
    return result


class OperationPolicyTests(unittest.TestCase):
    def test_all_local_operations_accept_local_target(self):
        for operation in policy.OPERATIONS:
            with self.subTest(operation=operation):
                self.assertTrue(policy.authorize_operation([local()], operation,
                                policy.ExecutionTarget("local", False))["allowed"])

    def test_private_policy_rejects_every_service_operation(self):
        for operation in policy.OPERATIONS:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(policy.PolicyError, "Local-only"):
                    policy.authorize_operation([local()], operation,
                                               policy.ExecutionTarget("service", True, "example_llm"))

    def test_local_label_with_network_enabled_is_not_accepted(self):
        for target in (policy.ExecutionTarget("local", True),
                       policy.ExecutionTarget("local", False, "example_llm"),
                       policy.ExecutionTarget("local", "false"),
                       policy.ExecutionTarget("unknown", False), {"location":"local"}):
            with self.subTest(target=target), self.assertRaises(policy.PolicyError):
                policy.authorize_operation([local()], "infer", target)

    def test_service_must_be_declared_by_all_participants(self):
        participants = [remote(["first", "common"]), remote(["common", "second"])]
        result = policy.authorize_operation(participants, "infer", policy.ExecutionTarget("service", True, "common"))
        self.assertEqual(result["allowed_services"], ["common"])
        with self.assertRaisesRegex(policy.PolicyError, "every participant"):
            policy.authorize_operation(participants, "infer", policy.ExecutionTarget("service", True, "first"))

    def test_mixed_private_session_inherits_strictest_policy(self):
        participants = [remote(["example_llm"]), local()]
        before = copy.deepcopy(participants)
        result = policy.authorize_operation(participants, "infer", policy.ExecutionTarget("local", False))
        self.assertEqual(result["distribution_mode"], "local_only")
        self.assertEqual(result["allowed_services"], [])
        self.assertEqual(participants, before)
        with self.assertRaises(policy.PolicyError):
            policy.authorize_operation(participants, "infer", policy.ExecutionTarget("service", True, "example_llm"))

    def test_no_shared_service_is_not_permission_to_pick_one(self):
        participants = [remote(["first"]), remote(["second"])]
        self.assertEqual(policy.effective_policy(participants)["allowed_services"], [])
        with self.assertRaises(policy.PolicyError):
            policy.authorize_operation(participants, "ocr", policy.ExecutionTarget("service", True, "first"))

    def test_public_export_and_remote_backup_are_not_implemented(self):
        value = remote(["example_llm"])
        value.update(review_status="reviewed", distribution_policy={"mode":"public_approved"},
                     publication_review={"record_id":"review-01", "reviewer_id":"reviewer-01",
                                         "reviewed_on":"2026-10-09", "content_digest":"a"*64})
        with self.assertRaisesRegex(policy.PolicyError, "not implemented"):
            policy.authorize_operation([value], "export_public", policy.ExecutionTarget("local", False))
        with self.assertRaises(policy.PolicyError):
            policy.authorize_operation([value], "backup", policy.ExecutionTarget("service", True, "example_llm"))

    def test_empty_malformed_and_unknown_policy_fail_closed(self):
        for policies in ([], {}, [None], [dict(local(), policy_version="2.0")]):
            with self.subTest(policies=policies), self.assertRaises(policy.PolicyError):
                policy.authorize_operation(policies, "read", policy.ExecutionTarget("local", False))

    def test_full_manifest_validation_precedes_policy_extraction(self):
        value = json.loads((ROOT / "personas/_template/manifest.json").read_text(encoding="utf-8"))
        value.update(local("historical_public"), schema_version="2.0")
        self.assertEqual(policy.policy_from_manifest(value), local("historical_public"))
        value["unknown_policy_bypass"] = True
        with self.assertRaises(policy.PolicyError):
            policy.policy_from_manifest(value)
