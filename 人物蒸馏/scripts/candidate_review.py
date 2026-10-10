"""Read a verified external candidate run before review; never calls a model."""
from __future__ import annotations

import json
import copy
from pathlib import Path
import re

import local_distill as local
from local_candidates import checked_snapshot, PROMPT
from candidate_records import fingerprint, verify_candidates, normalize_candidates, text


def prepare_review(bundle: dict, chunks: list[dict], policy: dict, decision: dict) -> dict:
    """Validate explicit human decisions; this API never invents or performs a review."""
    raw = verify_candidates(bundle, chunks, policy)
    required = {"candidates_sha256", "reviewer", "note", "decisions"}
    if (not isinstance(decision, dict) or set(decision) != required
            or decision["candidates_sha256"] != fingerprint(bundle)
            or not isinstance(decision["decisions"], list)):
        raise ValueError("Review must bind the exact candidate bundle")
    reviewer = text(decision["reviewer"], limit=120)
    note = text(decision["note"], limit=2000)
    originals = {}
    for name, kind, id_key in (("evidence", "evidence", "evidence_id"),
                               ("context_memory", "memories", "memory_id")):
        for record, item in zip(bundle[name], raw[kind]):
            originals[record[id_key]] = (kind, id_key, record, item)
    if len(decision["decisions"]) != len(originals):
        raise ValueError("Every candidate requires an explicit decision")
    accepted, rejected, edits, seen = [], [], [], set()
    for entry in decision["decisions"]:
        if not isinstance(entry, dict) or set(entry) not in (
                {"record_id", "action", "reason"}, {"record_id", "action", "reason", "replacement"}):
            raise ValueError("Invalid review decision fields")
        rid, action = entry["record_id"], entry["action"]
        if not isinstance(rid, str) or rid not in originals or rid in seen:
            raise ValueError("Unknown or duplicate review target")
        seen.add(rid)
        reason = text(entry["reason"], limit=2000)
        if action not in ("accept", "reject", "edit") or ("replacement" in entry) != (action == "edit"):
            raise ValueError("Invalid review action or replacement")
        kind, id_key, original, item = originals[rid]
        if action == "reject":
            rejected.append({"record_id": rid, "reason": reason})
            continue
        replacement = entry["replacement"] if action == "edit" else item
        if (not isinstance(replacement, dict) or not isinstance(replacement.get("chunk_ids"), list)
                or any(cid not in item["chunk_ids"] for cid in replacement["chunk_ids"])):
            raise ValueError("Edited records may not add unreviewed source chunks")
        payload = {"evidence": [], "memories": [], "gaps": []}
        payload[kind] = [replacement]
        normalized = normalize_candidates(payload, chunks, policy)
        field = "evidence" if kind == "evidence" else "context_memory"
        record = normalized[field][0]
        record[id_key], record["status"] = rid, "reviewed"
        lineage = normalized["lineage"][0]
        lineage["record_id"] = rid
        accepted.append({"record": record, "lineage": lineage, "action": action, "reason": reason})
        if action == "edit":
            edits.append(rid)
    accepted.sort(key=lambda entry: entry["lineage"]["record_id"])
    rejected.sort(key=lambda entry: entry["record_id"])
    return {"schema_version": "1.0", "status": "review_recorded", "delivery_channel": "local_only",
            "candidates_sha256": fingerprint(bundle), "reviewer": reviewer, "note": note,
            "policy": copy.deepcopy(bundle["policy"]), "accepted": accepted, "rejected": rejected,
            "edited_record_ids": sorted(edits), "gaps": copy.deepcopy(bundle["gaps"]),
            "independent_review_verified": False, "publication_approved": False, "persona_generated": False}


def _read_run_locked(library: Path, job_id: str, run_id: str, *, confirm_local_storage: bool = False) -> dict:
    """Internal local-only API. Never invoke on actual private materials from a cloud agent."""
    if not confirm_local_storage or not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Local confirmation and exact run hash required")
    local.load_job(library, job_id, confirm_local_storage=True)
    directory = local.job_directory(library, job_id)
    state, snapshot, policy = checked_snapshot(library, job_id)
    output = local.plain_path(directory / "candidates" / run_id)
    expected = {"binding.json", "request-started.json", "candidates.json"}
    if {p.name for p in output.iterdir()} != expected | {"checksums.json"}:
        raise ValueError("Candidate run is incomplete or has unexpected files")
    index = local.read_json(local.plain_path(output / "checksums.json"))
    if set(index) != expected:
        raise ValueError("Invalid candidate inventory")
    values = {}
    for name in sorted(expected):
        path = local.plain_path(output / name)
        with path.open("rb") as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or local.digest(raw) != index[name]:
            raise ValueError("Candidate file changed")
        values[name] = json.loads(raw.decode("utf-8"))
    binding = values["binding.json"]
    if (fingerprint(binding) != run_id or binding.get("schema_version") != "1.0"
            or binding.get("kind") != "local_candidate_extraction" or binding.get("attempt_limit") != 1
            or binding.get("snapshot_sha256") != state["snapshot_sha256"]
            or binding.get("input_fingerprint") != state["input_fingerprint"]
            or values["request-started.json"] != {"inference_attempts": 1}):
        raise ValueError("Run is not bound to the current source snapshot")
    with local.plain_path(snapshot / "chunks.jsonl").open("rb") as handle:
        raw = handle.read(160001)
    if (len(raw) > 160000 or local.digest(raw) != local.read_json(snapshot / "checksums.json")["chunks.jsonl"]):
        raise ValueError("Chunk snapshot changed")
    chunks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    content = local.canonical({"material_basis": policy["material_basis"], "chunks": chunks})
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]
    if fingerprint(messages) != binding["prompt_sha256"]:
        raise ValueError("Prompt contract changed; old run needs explicit migration")
    bundle = values["candidates.json"]
    verify_candidates(bundle, chunks, policy)
    checked_snapshot(library, job_id, state)
    # Returned text remains private; a future local reviewer may display it locally only.
    return {"run_sha256": run_id, "candidates_sha256": fingerprint(bundle),
            "bundle": bundle, "chunks": chunks, "policy": policy,
            "semantic_review_completed": False}


def _directory(library, job_id, confirmed):
    local.load_job(library, job_id, confirm_local_storage=confirmed)
    return local.job_directory(library, job_id)


def read_run(library: Path, job_id: str, run_id: str, *, confirm_local_storage: bool = False) -> dict:
    if not confirm_local_storage or not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Local confirmation and exact run hash required")
    with local.job_lock(_directory(library, job_id, confirm_local_storage)):
        return _read_run_locked(library, job_id, run_id, confirm_local_storage=True)


def _read_review_locked(directory, review_id):
    if not isinstance(review_id, str) or not re.fullmatch(r"[0-9a-f]{64}", review_id):
        raise ValueError("Exact review hash required")
    output = local.plain_path(directory / "reviews" / review_id)
    if {p.name for p in output.iterdir()} != {"review.json", "checksums.json"}:
        raise ValueError("Review snapshot incomplete or unexpected files")
    path = local.plain_path(output / "review.json")
    with path.open("rb") as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if (len(raw) > 2 * 1024 * 1024
            or local.read_json(local.plain_path(output / "checksums.json")) != {"review.json": local.digest(raw)}):
        raise ValueError("Review snapshot changed")
    value = json.loads(raw.decode("utf-8"))
    if (fingerprint(value) != review_id or not isinstance(value, dict)
            or set(value) != {"schema_version", "run_sha256", "decision", "result"}
            or value["schema_version"] != "1.0"):
        raise ValueError("Review binding changed")
    return value


def save_review(library: Path, job_id: str, run_id: str, decision: dict, *,
                confirm_local_storage: bool = False) -> dict:
    """Persist supplied human decisions locally, without overwriting candidates or prior reviews."""
    directory = _directory(library, job_id, confirm_local_storage)
    with local.job_lock(directory):
        current = _read_run_locked(library, job_id, run_id, confirm_local_storage=True)
        result = prepare_review(current["bundle"], current["chunks"], current["policy"], decision)
        envelope = {"schema_version": "1.0", "run_sha256": run_id,
                    "decision": copy.deepcopy(decision), "result": result}
        review_id = fingerprint(envelope)
        output = local.plain_path(directory / "reviews" / review_id)
        if output.exists():
            if _read_review_locked(directory, review_id) != envelope:
                raise ValueError("Existing review differs")
        else:
            output.mkdir(parents=True, exist_ok=False)
            local.write_new(output / ".incomplete", b"local review in progress\n")
            raw = (local.canonical(envelope) + "\n").encode("utf-8")
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Review exceeds size limit")
            local.write_new(output / "review.json", raw)
            index = {"review.json": local.digest(raw)}
            local.write_new(output / "checksums.json", (local.canonical(index) + "\n").encode("utf-8"))
            if _read_run_locked(library, job_id, run_id, confirm_local_storage=True) != current:
                raise ValueError("Source changed while saving review")
            local.plain_path(output / ".incomplete").unlink()
        return {"ok": True, "status": "review_recorded", "review_sha256": review_id,
                "accepted_count": len(result["accepted"]), "rejected_count": len(result["rejected"]),
                "publication_approved": False, "persona_generated": False}


def read_review(library: Path, job_id: str, review_id: str, *, confirm_local_storage: bool = False) -> dict:
    directory = _directory(library, job_id, confirm_local_storage)
    with local.job_lock(directory):
        value = _read_review_locked(directory, review_id)
        current = _read_run_locked(library, job_id, value["run_sha256"], confirm_local_storage=True)
        if prepare_review(current["bundle"], current["chunks"], current["policy"], value["decision"]) != value["result"]:
            raise ValueError("Stored review result no longer validates")
        return value
