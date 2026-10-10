"""Normalize untrusted model candidates; citations are structural, never proof of truth."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from package_policy import ExecutionTarget, authorize_operation

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
EVIDENCE_TEXT = ("claim", "evidence_summary", "interpretation", "limitations")
MEMORY_TEXT = ("title", "period", "situation", "observed_move", "tension", "dialogue_use")


def validate(value: dict, name: str) -> None:
    from jsonschema import Draft202012Validator
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    if not Draft202012Validator(schema).is_valid(value):
        raise ValueError("Candidate schema validation failed")


def fingerprint(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def text(value, *, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Candidate text missing or exceeds limit")
    return value.strip()


def normalize_candidates(payload: dict, chunks: list[dict], policy: dict) -> dict:
    """Internal local-only API. Caller must enforce policy BEFORE reading private inputs."""
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    if not isinstance(payload, dict) or set(payload) != {"evidence", "memories", "gaps"}:
        raise ValueError("Unexpected candidate fields")
    if (not isinstance(chunks, list) or not 1 <= len(chunks) <= 256
            or any(not isinstance(payload[k], list) for k in payload)
            or len(payload["evidence"]) > 40 or len(payload["memories"]) > 40
            or len(payload["gaps"]) > 20):
        raise ValueError("Candidate batch exceeds limits")
    if len(json.dumps(payload, ensure_ascii=False)) > 200000:
        raise ValueError("Candidate payload exceeds limit")
    lookup = {}
    for chunk in chunks:
        validate(chunk, "normalized-chunk.schema.json")
        cid = chunk["chunk_id"]
        if cid in lookup or not cid.startswith(chunk["source_id"] + "#"):
            raise ValueError("Duplicate or inconsistent chunk identity")
        if (chunk["content_mode"] == "metadata_only" or chunk["rights_mode"] == "metadata_only"
                or not chunk["text"].strip() or len(chunk["text"]) > 12000):
            raise ValueError("Chunk has no eligible bounded text")
        if hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest() != chunk["checksum"]:
            raise ValueError("Chunk content checksum mismatch")
        lookup[cid] = chunk
    evidence, memories, lineage, seen = [], [], [], set()
    for kind, fields, target in (("evidence", EVIDENCE_TEXT, evidence), ("memories", MEMORY_TEXT, memories)):
        required = set(fields) | {"chunk_ids", "topics", "confidence"}
        if kind == "evidence":
            required.add("card_type")
        for item in payload[kind]:
            if not isinstance(item, dict) or set(item) != required:
                raise ValueError("Model must not set identities, sources, policy or review status")
            refs = item["chunk_ids"]
            if (not isinstance(refs, list) or not 1 <= len(refs) <= 12
                    or any(not isinstance(cid, str) or cid not in lookup for cid in refs)
                    or len(set(refs)) != len(refs)):
                raise ValueError("Candidate references unknown or duplicate chunks")
            topics = item["topics"]
            if not isinstance(topics, list) or len(topics) > 12:
                raise ValueError("Invalid candidate topics")
            record = {key: text(item[key]) for key in fields}
            record.update(schema_version="1.0", topics=[text(topic, limit=80) for topic in topics],
                          confidence=item["confidence"], status="candidate")
            sources = {}
            for cid in sorted(refs):
                chunk = lookup[cid]
                source = sources.setdefault(chunk["source_id"], {"source_id": chunk["source_id"],
                                                               "locations": [], "chunk_ids": []})
                if chunk["location"] not in source["locations"]:
                    source["locations"].append(chunk["location"])
                source["chunk_ids"].append(cid)
            if kind == "evidence":
                if (set(policy["material_basis"]) == {"user_recollection"}
                        and item["card_type"] not in {"reliable_report", "synthesis"}):
                    raise ValueError("User recollection cannot be promoted to verified fact or direct view")
                record.update(card_type=item["card_type"], source_refs=list(sources.values()), delivery_channel="local_only")
                schema, prefix, id_key = "evidence-card.schema.json", "E", "evidence_id"
            else:
                record["source_ids"] = list(sources)
                schema, prefix, id_key = "context-memory.schema.json", "M", "memory_id"
            content_hash = fingerprint({"kind": kind, "record": record, "chunk_ids": sorted(refs)})
            if content_hash in seen:
                raise ValueError("Duplicate candidate record")
            seen.add(content_hash)
            record[id_key] = f"{prefix}-{len(target) + 1:04d}"
            validate(record, schema)
            target.append(record)
            lineage.append({"record_id": record[id_key], "content_sha256": content_hash,
                            "chunk_refs": [{"chunk_id": cid, "checksum": lookup[cid]["checksum"]}
                                           for cid in sorted(refs)]})
    local_policy = copy.deepcopy(policy)
    local_policy["sensitive_data"] = policy["sensitive_data"] or any(c["privacy_level"] == "sensitive" for c in chunks)
    local_policy.update(review_status="candidate", execution_policy={"mode": "local_only", "allowed_services": []},
                        distribution_policy={"mode": "local_only"})
    local_policy.pop("publication_review", None)
    validate(local_policy, "persona-policy.schema.json")
    return {"schema_version": "1.0", "status": "candidate", "delivery_channel": "local_only",
            "policy": local_policy, "input_policy_sha256": fingerprint(policy),
            "input_chunks_sha256": fingerprint(chunks), "evidence": evidence, "context_memory": memories,
            "lineage": lineage, "gaps": [text(gap, limit=1000) for gap in payload["gaps"]],
            "semantic_review_completed": False, "persona_generated": False}


def verify_candidates(bundle: dict, chunks: list[dict], policy: dict) -> dict:
    """Rebuild all derived fields; file hashes alone do not validate a candidate contract."""
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    try:
        if not isinstance(bundle, dict) or not isinstance(bundle["lineage"], list):
            raise ValueError("Invalid candidate bundle")
        lookup = {entry["record_id"]: entry for entry in bundle["lineage"]}
        if len(lookup) != len(bundle["lineage"]):
            raise ValueError("Duplicate lineage")
        payload = {"evidence": [], "memories": [], "gaps": bundle["gaps"]}
        for field, target, fields, id_key in (
                ("evidence", "evidence", EVIDENCE_TEXT + ("card_type",), "evidence_id"),
                ("context_memory", "memories", MEMORY_TEXT, "memory_id")):
            for record in bundle[field]:
                raw = {key: record[key] for key in fields + ("topics", "confidence")}
                raw["chunk_ids"] = [ref["chunk_id"] for ref in lookup[record[id_key]]["chunk_refs"]]
                payload[target].append(raw)
        rebuilt = normalize_candidates(payload, chunks, policy)
        if rebuilt != bundle:
            raise ValueError("Candidate content or lineage changed")
        return payload
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid candidate bundle") from None
