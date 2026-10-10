"""External local review history storage. Historical material is never runtime context."""
from __future__ import annotations

import json
from pathlib import Path
import re

import local_distill as local
from candidate_records import fingerprint, verify_candidates
from candidate_review import _read_review_locked, prepare_review
from local_candidates import checked_snapshot, PROMPT
from package_policy import authorize_operation, ExecutionTarget
from review_merge import merge_history


def exact_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Exact content hash required")
    return value


def bounded_json(path, limit=2 * 1024 * 1024):
    with local.plain_path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Local JSON exceeds limit")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected local JSON object")
    return value, local.digest(raw)


def chunks_from(snapshot):
    with local.plain_path(snapshot / "chunks.jsonl").open("rb") as handle:
        raw = handle.read(160001)
    if len(raw) > 160000 or local.digest(raw) != local.read_json(snapshot / "checksums.json")["chunks.jsonl"]:
        raise ValueError("Historical chunk snapshot changed or too large")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def historical_source(directory, review_id, job, state):
    """Caller owns job lock and checked policy. Output is for audit/merge ONLY."""
    authorize_operation([job["policy"]], "read", ExecutionTarget("local", False))
    envelope = _read_review_locked(directory, review_id)
    run_id = exact_hash(envelope["run_sha256"])
    output = local.plain_path(directory / "candidates" / run_id)
    expected = {"binding.json", "request-started.json", "candidates.json"}
    if {p.name for p in output.iterdir()} != expected | {"checksums.json"}:
        raise ValueError("Historical run incomplete or unexpected files")
    index, _ = bounded_json(output / "checksums.json")
    if set(index) != expected:
        raise ValueError("Historical run inventory changed")
    values = {}
    for name in expected:
        values[name], checksum = bounded_json(output / name)
        if checksum != index[name]:
            raise ValueError("Historical run content changed")
    binding = values["binding.json"]
    if (fingerprint(binding) != run_id or binding.get("schema_version") != "1.0"
            or binding.get("kind") != "local_candidate_extraction" or binding.get("attempt_limit") != 1
            or values["request-started.json"] != {"inference_attempts": 1}):
        raise ValueError("Invalid historical run binding")
    matches = []
    root = local.plain_path(directory / "snapshots")
    paths = list(root.iterdir())
    if len(paths) > 9999:
        raise ValueError("Historical snapshot capacity exceeded")
    for path in paths:
        if not re.fullmatch(r"v\d{4}-[0-9a-f]{8}", path.name):
            raise ValueError("Unexpected historical snapshot path")
        path = local.plain_path(path)
        if local.sha(local.plain_path(path / "checksums.json")) == binding.get("snapshot_sha256"):
            matches.append(path)
    if len(matches) != 1:
        raise ValueError("Historical snapshot missing or ambiguous")
    snapshot = matches[0]
    local.verify_snapshot(directory, {"snapshot": snapshot.relative_to(directory).as_posix(),
                                      "snapshot_sha256": binding["snapshot_sha256"]})
    review = local.read_json(snapshot / "review.json")
    if (review.get("config_sha256") != state["config_sha256"]
            or review.get("input_fingerprint") != binding.get("input_fingerprint")
            or review.get("material_declaration") != job["material_declaration"]):
        raise ValueError("Historical review belongs to another job or material basis")
    approvals = review.get("approvals", {})
    for gate, expected_hash in (("identity", state["config_sha256"]), ("material", binding["input_fingerprint"])):
        if (approvals.get(gate, {}).get("decision") != "approved"
                or approvals[gate].get("fingerprint") != expected_hash):
            raise ValueError("Historical source lacked required review")
    policy = local.read_json(snapshot / "policy.json")
    authorize_operation([job["policy"], policy], "read", ExecutionTarget("local", False))
    chunks = chunks_from(snapshot)
    content = local.canonical({"material_basis": policy["material_basis"], "chunks": chunks})
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]
    if fingerprint(messages) != binding.get("prompt_sha256"):
        raise ValueError("Historical prompt needs explicit migration")
    bundle = values["candidates.json"]
    verify_candidates(bundle, chunks, policy)
    if prepare_review(bundle, chunks, policy, envelope["decision"]) != envelope["result"]:
        raise ValueError("Historical human decision changed")
    return {"bundle": bundle, "chunks": chunks, "policy": policy, "decision": envelope["decision"]}


def read_snapshot(directory, merge_id):
    output = local.plain_path(directory / "merges" / exact_hash(merge_id))
    if {p.name for p in output.iterdir()} != {"merge.json", "checksums.json"}:
        raise ValueError("Merge snapshot incomplete or unexpected files")
    value, checksum = bounded_json(output / "merge.json", 16 * 1024 * 1024)
    index, _ = bounded_json(output / "checksums.json")
    if index != {"merge.json": checksum} or fingerprint(value) != merge_id:
        raise ValueError("Merge snapshot changed")
    expected = {"schema_version", "config_sha256", "review_ids", "previous_merge", "snapshot_sha256", "catalog"}
    if value.get("schema_version") == "1.1":
        expected.add("input_fingerprint")
    elif value.get("schema_version") != "1.0":
        raise ValueError("Unknown merge snapshot version")
    if set(value) != expected:
        raise ValueError("Invalid merge envelope")
    return value


def current_material(library, job_id):
    job, paths, state = local.load_job(library, job_id, confirm_local_storage=True)
    if state["status"] != "needs_inputs":
        state, snapshot, policy = checked_snapshot(library, job_id)
        return state, {"snapshot_sha256": state["snapshot_sha256"], "input_fingerprint": state["input_fingerprint"]}, policy, chunks_from(snapshot)
    # Empty means confirmed absence of all source files, not failed parsing or quarantined files.
    fingerprint_now, entries = local.input_inventory(paths)
    if entries:
        raise ValueError("Unusable inputs are not a complete source withdrawal")
    for gate, expected in (("identity", state["config_sha256"]), ("material", fingerprint_now)):
        if state["approvals"].get(gate, {}).get("fingerprint") != expected:
            raise ValueError("Empty source state requires current identity and material review")
    return state, {"snapshot_sha256": None, "input_fingerprint": fingerprint_now}, job["policy"], []


def save_merge(library: Path, job_id: str, review_ids: list[str], *, previous_merge: str | None = None,
               confirm_local_storage: bool = False) -> dict:
    job, _, state = local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    if (not isinstance(review_ids, list) or not 1 <= len(review_ids) <= 100
            or any(not isinstance(x, str) for x in review_ids) or len(set(review_ids)) != len(review_ids)):
        raise ValueError("Expected unique ordered review hashes")
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        material = current_material(library, job_id)
        state, binding, policy, chunks = material
        history = [historical_source(directory, exact_hash(rid), job, state) for rid in review_ids]
        previous = None
        if previous_merge is not None:
            previous = read_snapshot(directory, previous_merge)
            if (previous["config_sha256"] != state["config_sha256"]
                    or review_ids[:len(previous["review_ids"])] != previous["review_ids"]):
                raise ValueError("Prior merge belongs to another job or history changed")
        catalog = merge_history(history, chunks, policy,
                                previous_history=previous["catalog"]["history_sha256"] if previous else None)
        envelope = {"schema_version": "1.1", "config_sha256": state["config_sha256"], "review_ids": review_ids,
                    "previous_merge": previous_merge, **binding, "catalog": catalog}
        merge_id = fingerprint(envelope)
        output = local.plain_path(directory / "merges" / merge_id)
        if output.exists():
            if read_snapshot(directory, merge_id) != envelope:
                raise ValueError("Existing merge differs")
        else:
            output.mkdir(parents=True, exist_ok=False)
            local.write_new(output / ".incomplete", b"local merge in progress\n")
            raw = (local.canonical(envelope) + "\n").encode("utf-8")
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Merge exceeds size limit")
            local.write_new(output / "merge.json", raw)
            local.write_new(output / "checksums.json", (local.canonical({"merge.json": local.digest(raw)}) + "\n").encode())
            if current_material(library, job_id) != material:
                raise ValueError("Current material changed during merge")
            if [historical_source(directory, rid, job, state) for rid in review_ids] != history:
                raise ValueError("History changed during merge")
            local.plain_path(output / ".incomplete").unlink()
        return {"ok": True, "status": "merge_review_pending", "merge_sha256": merge_id,
                "counts": catalog["counts"], "publication_approved": False, "persona_generated": False}


def read_merge(library: Path, job_id: str, merge_id: str, *, confirm_local_storage: bool = False) -> dict:
    """Local consumers must use this current-source check instead of read_snapshot."""
    job, _, _ = local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        material = current_material(library, job_id)
        state, binding, policy, chunks = material
        envelope = read_snapshot(directory, merge_id)
        if (envelope["config_sha256"] != state["config_sha256"]
                or envelope["snapshot_sha256"] != binding["snapshot_sha256"]
                or (envelope["schema_version"] == "1.1" and envelope["input_fingerprint"] != binding["input_fingerprint"])):
            raise ValueError("Merge does not reflect current material")
        ids = envelope["review_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError("Invalid merge review list")
        history = [historical_source(directory, exact_hash(rid), job, state) for rid in ids]
        if merge_history(history, chunks, policy) != envelope["catalog"]:
            raise ValueError("Merge catalog does not rebuild from reviewed sources")
        if current_material(library, job_id) != material:
            raise ValueError("Source changed during merge read")
        return envelope
