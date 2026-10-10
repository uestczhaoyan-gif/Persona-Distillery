"""Rebuild a stable local review catalog from an append-only, verified review history."""
from __future__ import annotations

import copy
import json

from candidate_records import fingerprint, normalize_candidates
from candidate_review import prepare_review
from package_policy import authorize_operation, ExecutionTarget


def merge_history(history: list[dict], current_chunks: list[dict], policy: dict, *,
                  previous_history: list[str] | None = None) -> dict:
    """Pure local API. Storage adapters must bind all history items to the same job."""
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    if (not isinstance(history, list) or not 1 <= len(history) <= 100
            or len(json.dumps(history, ensure_ascii=False)) > 16 * 1024 * 1024
            or not isinstance(current_chunks, list)):
        raise ValueError("Review history exceeds limits")
    current_policy = policy
    if current_chunks:
        current_policy = normalize_candidates({"evidence": [], "memories": [], "gaps": []}, current_chunks, policy)["policy"]
    current = {chunk["chunk_id"]: chunk["checksum"] for chunk in current_chunks}
    history_ids, reviews = [], []
    for source in history:
        if not isinstance(source, dict) or set(source) != {"bundle", "chunks", "policy", "decision"}:
            raise ValueError("Invalid review history item")
        authorize_operation([policy, source["policy"]], "read", ExecutionTarget("local", False))
        if source["policy"]["subject_kind"] != policy["subject_kind"]:
            raise ValueError("Cannot merge different subject kinds")
        review = prepare_review(source["bundle"], source["chunks"], source["policy"], source["decision"])
        history_ids.append(fingerprint(source))
        reviews.append(review)
    if len(set(history_ids)) != len(history_ids):
        raise ValueError("Duplicate history item")
    if previous_history is not None and (not isinstance(previous_history, list)
            or history_ids[:len(previous_history)] != previous_history):
        raise ValueError("History must preserve its existing prefix")
    entries, counts = {}, {"E": 0, "M": 0}
    for source, review, history_id in zip(history, reviews, history_ids):
        accepted = {item["lineage"]["record_id"]: item for item in review["accepted"]}
        rejected = {item["record_id"]: item for item in review["rejected"]}
        # Original identity is independent of batch numbering and later human edits.
        for original in source["bundle"]["lineage"]:
            batch_id = original["record_id"]
            origin_id = fingerprint({"content_sha256": original["content_sha256"], "chunk_refs": original["chunk_refs"]})
            if origin_id not in entries:
                prefix = batch_id[0]
                counts[prefix] += 1
                if counts[prefix] > 9999:
                    raise ValueError("Stable record ID capacity exceeded")
                entries[origin_id] = {"stable_id": f"{prefix}-{counts[prefix]:04d}", "origin_sha256": origin_id,
                                      "origin_lineage": copy.deepcopy(original), "variants": []}
            entry = entries[origin_id]
            if batch_id in rejected:
                variant = {"action": "reject", "record": None, "lineage": None,
                           "reason": rejected[batch_id]["reason"], "history_sha256": history_id}
            else:
                variant = copy.deepcopy(accepted[batch_id])
                id_key = "evidence_id" if batch_id.startswith("E") else "memory_id"
                variant["record"][id_key] = entry["stable_id"]
                variant["lineage"]["record_id"] = entry["stable_id"]
                variant["history_sha256"] = history_id
            entry["variants"].append(variant)
    for entry in entries.values():
        variants = entry["variants"]
        # Distinct decisions are never silently replaced, including rejection after acceptance.
        outcomes = {fingerprint({"record": v["record"], "lineage": v["lineage"]}) for v in variants}
        refs_valid = all(current.get(ref["chunk_id"]) == ref["checksum"]
                         for v in variants if v["lineage"] for ref in v["lineage"]["chunk_refs"])
        if not refs_valid:
            state = "withdrawn"
        elif len(outcomes) > 1:
            state = "review_required"
        elif variants[0]["action"] == "reject":
            state = "rejected"
        else:
            state = "active"
        entry["state"] = state
        entry["conflict"] = len(outcomes) > 1
        # Preserve every revision, but only unambiguous, source-valid records are usable.
        entry["active_record"] = copy.deepcopy(variants[0]["record"]) if state == "active" else None
    output_policy = copy.deepcopy(policy)
    output_policy.update(review_status="candidate", execution_policy={"mode": "local_only", "allowed_services": []},
                         distribution_policy={"mode": "local_only"},
                         sensitive_data=current_policy["sensitive_data"] or any(r["policy"]["sensitive_data"] for r in reviews))
    output_policy.pop("publication_review", None)
    return {"schema_version": "1.0", "status": "merge_review_pending", "policy": output_policy,
            "history_sha256": history_ids, "current_chunks_sha256": fingerprint(current_chunks),
            "entries": list(entries.values()), "counts": {state: sum(e["state"] == state for e in entries.values())
                for state in ("active", "withdrawn", "rejected", "review_required")},
            "publication_approved": False, "persona_generated": False}
