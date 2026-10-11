"""Local spec verification and human decisions. No model calls or public export."""
from __future__ import annotations

import copy
from pathlib import Path

import local_distill as local
from local_merge import exact_hash, bounded_json, _read_merge_locked
from local_spec import spec_messages
from spec_candidate import verify_spec, normalize_spec
from candidate_records import fingerprint, text


def _read_spec_locked(library, job_id, spec_id):
    directory = local.job_directory(library, job_id)
    output = local.plain_path(directory / "specs" / exact_hash(spec_id))
    expected = {"binding.json", "request-started.json", "spec-candidate.json"}
    if {p.name for p in output.iterdir()} != expected | {"checksums.json"}:
        raise ValueError("Spec snapshot incomplete or unexpected files")
    index, _ = bounded_json(output / "checksums.json")
    if set(index) != expected:
        raise ValueError("Spec inventory changed")
    values = {}
    for name in expected:
        values[name], checksum = bounded_json(output / name)
        if checksum != index[name]:
            raise ValueError("Spec file changed")
    binding = values["binding.json"]
    if (fingerprint(binding) != spec_id or binding.get("schema_version") != "1.0"
            or binding.get("kind") != "local_spec_candidate" or binding.get("attempt_limit") != 1
            or values["request-started.json"] != {"inference_attempts": 1}):
        raise ValueError("Invalid spec binding")
    merged = _read_merge_locked(library, job_id, exact_hash(binding.get("merge_sha256")), confirm_local_storage=True)
    catalog = merged["catalog"]
    if binding.get("catalog_sha256") != fingerprint(catalog) or binding.get("prompt_sha256") != fingerprint(spec_messages(catalog)):
        raise ValueError("Spec input or prompt contract changed")
    candidate = values["spec-candidate.json"]
    verify_spec(candidate, catalog)
    return {"candidate": candidate, "catalog": catalog, "spec_content_sha256": fingerprint(candidate)}


def read_spec(library: Path, job_id: str, spec_id: str, *, confirm_local_storage=False):
    local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    with local.job_lock(local.job_directory(library, job_id)):
        return _read_spec_locked(library, job_id, spec_id)


def prepare_decision(current, decision):
    if not isinstance(decision, dict):
        raise ValueError("Explicit spec decision required")
    expected = {"spec_content_sha256", "action", "reviewer", "note"}
    action = decision.get("action")
    if action == "edit":
        expected.add("replacement")
    if (set(decision) != expected or action not in ("accept", "reject", "edit")
            or decision["spec_content_sha256"] != fingerprint(current["candidate"])):
        raise ValueError("Spec decision must bind the exact candidate")
    reviewer, note = text(decision["reviewer"], limit=120), text(decision["note"], limit=2000)
    verify_spec(current["candidate"], current["catalog"])
    candidate = None
    if action == "accept":
        candidate = copy.deepcopy(current["candidate"])
    elif action == "edit":
        candidate = normalize_spec(decision["replacement"], current["catalog"])
    return {"schema_version": "1.0", "status": "review_recorded", "action": action,
            "spec_content_sha256": decision["spec_content_sha256"], "reviewer": reviewer, "note": note,
            "candidate": candidate, "independent_review_verified": False,
            "publication_approved": False, "persona_generated": False}


def _read_stored(directory, review_id):
    output = local.plain_path(directory / "spec-reviews" / exact_hash(review_id))
    if {p.name for p in output.iterdir()} != {"review.json", "checksums.json"}:
        raise ValueError("Spec review incomplete or unexpected files")
    value, checksum = bounded_json(output / "review.json")
    index, _ = bounded_json(output / "checksums.json")
    if (index != {"review.json": checksum} or fingerprint(value) != review_id
            or set(value) != {"schema_version", "spec_sha256", "decision", "result"} or value["schema_version"] != "1.0"):
        raise ValueError("Spec review binding changed")
    return value


def save_spec_review(library: Path, job_id: str, spec_id: str, decision: dict, *, confirm_local_storage=False):
    local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        current = _read_spec_locked(library, job_id, spec_id)
        result = prepare_decision(current, decision)
        value = {"schema_version": "1.0", "spec_sha256": spec_id, "decision": copy.deepcopy(decision), "result": result}
        review_id = fingerprint(value)
        output = local.plain_path(directory / "spec-reviews" / review_id)
        if output.exists():
            if _read_stored(directory, review_id) != value:
                raise ValueError("Existing spec review differs")
        else:
            output.mkdir(parents=True, exist_ok=False)
            local.write_new(output / ".incomplete", b"local spec review in progress\n")
            raw = (local.canonical(value) + "\n").encode("utf-8")
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Spec review exceeds limit")
            local.write_new(output / "review.json", raw)
            local.write_new(output / "checksums.json", (local.canonical({"review.json": local.digest(raw)}) + "\n").encode())
            if _read_spec_locked(library, job_id, spec_id) != current:
                raise ValueError("Spec changed during review save")
            local.plain_path(output / ".incomplete").unlink()
        return {"ok": True, "status": "review_recorded", "review_sha256": review_id,
                "action": result["action"], "publication_approved": False, "persona_generated": False}


def _read_spec_review_locked(library: Path, job_id: str, review_id: str):
    directory = local.job_directory(library, job_id)
    value = _read_stored(directory, review_id)
    current = _read_spec_locked(library, job_id, value["spec_sha256"])
    if prepare_decision(current, value["decision"]) != value["result"]:
        raise ValueError("Spec review no longer validates")
    return value


def read_spec_review(library: Path, job_id: str, review_id: str, *, confirm_local_storage=False):
    local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        return _read_spec_review_locked(library, job_id, review_id)
