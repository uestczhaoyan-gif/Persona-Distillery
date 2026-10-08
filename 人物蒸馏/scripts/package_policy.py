"""Fail-closed compatibility boundary shared by persona consumers.

This is not the v2 execution-policy implementation. Legacy consumers must not
read content carrying constraints they do not understand.
"""
from __future__ import annotations

FUTURE_POLICY_FIELDS = frozenset({
    "execution_policy", "distribution_policy", "material_basis", "review_status",
    "policy_version", "sensitive_data", "publication_review",
})


def require_legacy_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ValueError("Unsupported persona schema_version; this reader only supports 1.0")
    if FUTURE_POLICY_FIELDS.intersection(manifest):
        raise ValueError("Unsupported policy fields in legacy manifest; explicit migration is required")
