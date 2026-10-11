"""Ground persona-spec candidates in active reviewed records, never in model memory."""
from __future__ import annotations

import copy
import json

from candidate_records import fingerprint, validate
from package_policy import authorize_operation, ExecutionTarget


def spec_material(catalog: dict) -> list[dict]:
    """Caller must obtain catalog through current-source read_merge, not read_snapshot."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get("policy"), dict):
        raise ValueError("Verified catalog required")
    authorize_operation([catalog["policy"]], "read", ExecutionTarget("local", False))
    if catalog.get("schema_version") != "1.0" or catalog.get("status") != "merge_review_pending":
        raise ValueError("Unknown review catalog")
    entries = catalog.get("entries")
    if not isinstance(entries, list) or len(entries) > 19998:
        raise ValueError("Invalid catalog entries")
    records, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("state") not in {"active", "withdrawn", "rejected", "review_required"}:
            raise ValueError("Invalid catalog state")
        if entry["state"] == "review_required":
            raise ValueError("Resolve known conflicts before persona synthesis")
        if entry["state"] != "active":
            if entry.get("active_record") is not None:
                raise ValueError("Inactive record cannot enter synthesis")
            continue
        record = entry.get("active_record")
        if not isinstance(record, dict) or record.get("status") != "reviewed":
            raise ValueError("Active synthesis input must be reviewed")
        kind = "evidence" if "evidence_id" in record else "memory"
        validate(record, "evidence-card.schema.json" if kind == "evidence" else "context-memory.schema.json")
        rid = record["evidence_id" if kind == "evidence" else "memory_id"]
        if rid != entry.get("stable_id") or rid in seen:
            raise ValueError("Duplicate or mismatched stable record")
        seen.add(rid)
        records.append({"record_id": rid, "kind": kind, "record": copy.deepcopy(record)})
    if not 1 <= len(records) <= 200:
        raise ValueError("Synthesis requires 1-200 active reviewed records; no silent truncation")
    return records


def normalize_spec(payload: dict, catalog: dict) -> dict:
    records = spec_material(catalog)
    if len(json.dumps(payload, ensure_ascii=False)) > 80000:
        raise ValueError("Spec candidate exceeds limit")
    validate(payload, "persona-spec-candidate.schema.json")
    lookup = {record["record_id"]: record["record"] for record in records}
    def check(value):
        if isinstance(value, dict):
            if "record_ids" in value and any(rid not in lookup for rid in value["record_ids"]):
                raise ValueError("Spec references inactive or unknown evidence")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, str) and not value.strip():
            raise ValueError("Spec contains empty text")
    check(payload)
    candidate = copy.deepcopy(payload)
    for example in candidate["examples"]:
        example["provenance"] = "original_simulation_not_quote"
    policy = copy.deepcopy(catalog["policy"])
    policy.update(review_status="candidate", execution_policy={"mode": "local_only", "allowed_services": []},
                  distribution_policy={"mode": "local_only"})
    policy.pop("publication_review", None)
    return {"schema_version": "1.0", "status": "candidate", "delivery_channel": "local_only",
            "policy": policy, "catalog_sha256": fingerprint(catalog),
            "record_sha256": {rid: fingerprint(record) for rid, record in lookup.items()},
            "candidate": candidate, "behavior_mode": "optional_tendencies_not_response_template",
            "semantic_review_completed": False, "avatar_ready": False, "persona_generated": False}


def verify_spec(candidate: dict, catalog: dict) -> dict:
    try:
        raw = copy.deepcopy(candidate["candidate"])
        for example in raw["examples"]:
            if example.pop("provenance") != "original_simulation_not_quote":
                raise ValueError("Spec example provenance changed")
        if normalize_spec(raw, catalog) != candidate:
            raise ValueError("Spec content or derived fields changed")
        return raw
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Invalid normalized spec candidate") from None
