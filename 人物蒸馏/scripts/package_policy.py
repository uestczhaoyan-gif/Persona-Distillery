"""Version boundaries and operation authorization for persona consumers.

Legacy entry checks remain standard-library only. V2 checks import jsonschema
on demand. Execution targets must come from trusted application adapters, never
from a model response or persona metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

FUTURE_POLICY_FIELDS = frozenset({
    "execution_policy", "distribution_policy", "material_basis", "review_status",
    "policy_version", "sensitive_data", "publication_review",
})
POLICY_FIELDS = FUTURE_POLICY_FIELDS | {"subject_kind"}
OPERATIONS = frozenset({"read", "infer", "search", "ocr", "transcribe", "generate_avatar", "backup", "delete"})


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionTarget:
    location: str
    network_enabled: bool
    service_id: str | None = None


def _validate(value: dict, filename: str) -> None:
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / filename).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise PolicyError(f"Invalid persona policy: {errors[0].validator} constraint")


def policy_from_manifest(manifest: dict) -> dict:
    _validate(manifest, "persona-package-v2.schema.json")
    return {key: manifest[key] for key in POLICY_FIELDS if key in manifest}


def effective_policy(policies: list[dict]) -> dict:
    if not isinstance(policies, (list, tuple)) or not policies:
        raise PolicyError("At least one validated policy is required")
    for policy in policies:
        _validate(policy, "persona-policy.schema.json")
    local = any(p["execution_policy"]["mode"] == "local_only" for p in policies)
    services = set(policies[0]["execution_policy"]["allowed_services"])
    for policy in policies[1:]:
        services.intersection_update(policy["execution_policy"]["allowed_services"])
    modes = {p["distribution_policy"]["mode"] for p in policies}
    distribution = "local_only" if "local_only" in modes else "review_required" if "review_required" in modes else "public_approved"
    return {"execution_mode": "local_only" if local else "declared_services",
            "allowed_services": [] if local else sorted(services),
            "distribution_mode": distribution,
            "sensitive_data": any(p["sensitive_data"] for p in policies)}


def authorize_operation(policies: list[dict], operation: str, target: ExecutionTarget) -> dict:
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise PolicyError("Operation is not implemented or authorized; public export is not enabled")
    if not isinstance(target, ExecutionTarget) or type(target.network_enabled) is not bool:
        raise PolicyError("Execution target must be supplied by a trusted adapter")
    effective = effective_policy(policies)
    if target.location == "local":
        if target.network_enabled or target.service_id is not None:
            raise PolicyError("Local execution must disable networking and remote services")
    elif target.location == "service":
        if (not target.network_enabled or not isinstance(target.service_id, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", target.service_id)):
            raise PolicyError("Invalid declared service target")
        if operation in {"backup", "delete"} or effective["execution_mode"] == "local_only":
            raise PolicyError("Local-only operation cannot send content to a service")
        if target.service_id not in effective["allowed_services"]:
            raise PolicyError("Service is not allowed by every participant policy")
    else:
        raise PolicyError("Unknown execution location")
    return {"allowed": True, "operation": operation, **effective}


def require_legacy_manifest(manifest: dict) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ValueError("Unsupported persona schema_version; this reader only supports 1.0")
    if FUTURE_POLICY_FIELDS.intersection(manifest):
        raise ValueError("Unsupported policy fields in legacy manifest; explicit migration is required")


def require_legacy_job(job: dict) -> None:
    if not isinstance(job, dict) or job.get("schema_version") != "1.0":
        raise ValueError("Unsupported job schema_version; use the dedicated v2 local job entry")
    if ({"policy", "material_declaration"} | FUTURE_POLICY_FIELDS).intersection(job):
        raise ValueError("New job policy cannot be downgraded into a legacy configuration")
