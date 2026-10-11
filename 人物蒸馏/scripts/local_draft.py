"""Build immutable external v2 draft packages bound to their current local source job."""
from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path

import local_distill as local
from candidate_records import fingerprint
from local_merge import current_material, exact_hash, bounded_json
from spec_review import _read_spec_review_locked, _read_spec_locked
from render_spec import render_views
from spec_candidate import spec_material
from package_policy import policy_from_manifest, authorize_operation, ExecutionTarget

ROLES = {"profile": "00-profile.md", "character_card": "00-character-card.md", "sources": "01-sources.csv",
         "evidence": "02-evidence.md", "persona_spec": "03-persona-spec.md", "core_anchors": "03-core-anchors.md",
         "dialogue_examples": "03-dialogue-examples.md", "evaluation": "04-evaluation.md", "dialogue_log": "05-dialogue-log.md",
         "build_report": "06-build-report.md", "context_memory": "08-context-memory.md", "usage_example": "09-usage-example.md",
         "structured_chunks": "processed/chunks.jsonl", "structured_evidence": "processed/evidence.jsonl",
         "structured_context_memory": "processed/context-memory.jsonl", "build_binding": "build-binding.json"}


def _json_bytes(value):
    return (local.canonical(value) + "\n").encode("utf-8")


def _jsonl(values):
    return ("\n".join(local.canonical(v) for v in values) + ("\n" if values else "")).encode("utf-8")


def _material_locked(library, job_id, review_id):
    job, paths, state = local.load_job(library, job_id, confirm_local_storage=True)
    reviewed = _read_spec_review_locked(library, job_id, exact_hash(review_id))
    current = _read_spec_locked(library, job_id, reviewed["spec_sha256"])
    catalog = current["catalog"]
    documents = render_views(reviewed, catalog, job["subject"]["display_name"])
    records = spec_material(catalog)
    material_state, binding, _, chunks = current_material(library, job_id)
    if material_state != state or binding["snapshot_sha256"] is None:
        raise ValueError("Draft requires a current nonempty material snapshot")
    evidence = [v["record"] for v in records if v["kind"] == "evidence"]
    memories = [v["record"] for v in records if v["kind"] == "memory"]
    # Include only chunks used by the selected active revisions, not withdrawn alternatives.
    used = set()
    for entry in catalog["entries"]:
        if entry["state"] != "active":
            continue
        selected = entry["variants"][0]
        if "resolution" in entry:
            selected = next(v for v in entry["variants"] if v["history_sha256"] == entry["resolution"]["chosen_history_sha256"])
        used.update(ref["chunk_id"] for ref in selected["lineage"]["chunk_refs"])
    chunks = [c for c in chunks if c["chunk_id"] in used]
    if len(chunks) != len(used):
        raise ValueError("Active draft references missing source chunks")
    snapshot = local.plain_path(local.job_directory(library, job_id) / state["snapshot"])
    sources_path = local.plain_path(snapshot / "01-sources.csv")
    with sources_path.open("rb") as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or local.digest(raw) != local.read_json(snapshot / "checksums.json")["01-sources.csv"]:
        raise ValueError("Source ledger changed or too large")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    source_ids = {c["source_id"] for c in chunks}
    sources = [row for row in reader if row["source_id"] in source_ids]
    if {r["source_id"] for r in sources} != source_ids:
        raise ValueError("Source ledger lacks used sources")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(sources)
    files = {name: text.encode("utf-8") for name, text in documents.items()}
    files.update({"01-sources.csv": stream.getvalue().encode("utf-8"), "processed/chunks.jsonl": _jsonl(chunks),
                  "processed/evidence.jsonl": _jsonl(evidence), "processed/context-memory.jsonl": _jsonl(memories)})
    policy = copy.deepcopy(reviewed["result"]["candidate"]["policy"])
    manifest = {"schema_version": "2.0", "person_id": job["subject"]["person_id"], "display_name": job["subject"]["display_name"],
                "package_version": "0.0.0", "readiness": "draft", "visibility": "private", "source_modes": ["upload"],
                "capabilities": {"direct_chat": False, "roundtable": False, "story_simulation": False, "themed_lecture": False},
                "coverage": {"period": "未独立核验", "topics": sorted({t for v in records for t in v["record"]["topics"]}),
                             "languages": sorted({c["language"] for c in chunks})}, "files": dict(ROLES), **policy}
    local.validate(manifest, "persona-package-v2.schema.json")
    files["manifest.v2.json"] = _json_bytes(manifest)
    build_id = fingerprint({"config_sha256": state["config_sha256"], "spec_review_sha256": review_id,
                            "files": {name: local.digest(data) for name, data in files.items()}})
    package = local.check_private_location(Path(paths["personas"]) / build_id / manifest["person_id"])
    files["build-binding.json"] = _json_bytes({"schema_version": "1.0", "library": str(Path(library).resolve()),
        "job_id": job_id, "spec_review_sha256": review_id, "build_sha256": build_id})
    files["checksums.json"] = _json_bytes({name: local.digest(data) for name, data in files.items()})
    return package, files, build_id


def _verify_files(package, expected):
    if (package / ".build-incomplete").exists():
        raise ValueError("Draft build incomplete")
    actual = set()
    for path in package.rglob("*"):
        path = local.plain_path(path)
        if path.is_file():
            actual.add(path.relative_to(package).as_posix())
        elif path.relative_to(package).as_posix() != "processed":
            raise ValueError("Unexpected draft directory")
        if len(actual) > len(expected):
            raise ValueError("Unexpected draft files")
    if actual != set(expected):
        raise ValueError("Draft file inventory changed")
    for name, wanted in expected.items():
        with local.plain_path(package / name).open("rb") as handle:
            if handle.read(len(wanted) + 1) != wanted:
                raise ValueError("Draft file content changed")


def build_draft(library: Path, job_id: str, review_id: str, *, confirm_local_storage=False):
    local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        package, files, build_id = _material_locked(library, job_id, review_id)
        if package.exists():
            _verify_files(package, files)
        else:
            package.mkdir(parents=True, exist_ok=False)
            local.write_new(package / ".build-incomplete", b"source-bound local draft in progress\n")
            for name, data in files.items():
                path = local.plain_path(package / name)
                path.parent.mkdir(parents=True, exist_ok=True)
                local.write_new(path, data)
            if _material_locked(library, job_id, review_id) != (package, files, build_id):
                raise ValueError("Source changed during draft build")
            local.plain_path(package / ".build-incomplete").unlink()
            _verify_files(package, files)
        return {"ok": True, "status": "draft", "build_sha256": build_id, "avatar_ready": False,
                "quality_verified": False, "publication_approved": False, "persona_generated": False}


def verify_draft(package: Path):
    """Rebuild and verify current content before local runtime loads any draft documents."""
    package = local.check_private_location(package)
    manifest, _ = bounded_json(package / "manifest.v2.json")
    policy = policy_from_manifest(manifest)
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    if manifest["files"].get("build_binding") != "build-binding.json":
        raise ValueError("Invalid draft build binding path")
    binding, _ = bounded_json(package / "build-binding.json")
    if (set(binding) != {"schema_version", "library", "job_id", "spec_review_sha256", "build_sha256"}
            or binding["schema_version"] != "1.0"):
        raise ValueError("Unknown draft build binding")
    library = Path(binding["library"])
    local.load_job(library, binding["job_id"], confirm_local_storage=True)
    with local.job_lock(local.job_directory(library, binding["job_id"])):
        expected_package, files, build_id = _material_locked(library, binding["job_id"], binding["spec_review_sha256"])
        if package != expected_package or build_id != binding["build_sha256"]:
            raise ValueError("Draft identity or source binding changed")
        _verify_files(package, files)
    return local.sha(local.plain_path(package / "build-binding.json"))
