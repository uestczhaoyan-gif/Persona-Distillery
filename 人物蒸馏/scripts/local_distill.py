#!/usr/bin/env python3
"""External local-only v2 distillation jobs. Initialization is not persona generation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import csv
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import sys
import uuid

from distill import canonical, digest, now, normalize, segment, SENSITIVE, write_json
from migrate_persona import (ROOT, check_private_location, overlap, plain_path,
                            read_json, sha, validate, write_new)
from package_policy import ExecutionTarget, authorize_operation

PIPELINE_VERSION = "local-text-v2"
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024


def validate_job(job: dict) -> None:
    validate(job, "distillation-job-v2.schema.json")
    upload = job["input"]["upload"]
    if upload.get("participant_map_file") or upload.get("allow_ocr") or upload.get("allow_transcription"):
        raise ValueError("Participant maps, OCR and transcription are not implemented by this local text runner")
    authorize_operation([job["policy"]], "read", ExecutionTarget("local", False))


def job_directory(library: Path, job_id: str, workspace: Path = ROOT) -> Path:
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid local job ID")
    library = check_private_location(library, workspace)
    return check_private_location(library / job_id, workspace)


def resolve_paths(job: dict, config: Path, library: Path, workspace: Path = ROOT) -> dict:
    def resolve(value):
        path = Path(value)
        return check_private_location(path if path.is_absolute() else config.parent / path, workspace)
    incoming = resolve(job["input"]["upload"]["incoming_directory"])
    output = resolve(job["output"]["personas_directory"])
    if not incoming.is_dir() or (output.exists() and not output.is_dir()):
        raise ValueError("Local incoming directory must already exist")
    if any(overlap(a, b) for a, b in ((incoming, library), (incoming, output), (library, output))):
        raise ValueError("Inputs, job storage and persona output must be separate directories")
    if any(config.is_relative_to(path) for path in (incoming, library, output)):
        raise ValueError("Keep the source configuration separate from input and output directories")
    paths = {"incoming": str(incoming), "personas": str(output)}
    record = job["material_declaration"].get("authorization_record")
    if record:
        path = resolve(record)
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("Authorization record must be a nonempty local file within the size limit")
        if path.is_relative_to(incoming) or path.is_relative_to(library) or path.is_relative_to(output):
            raise ValueError("Keep authorization records separate from source text and generated files")
        paths["authorization"] = str(path)
    return paths


def checked_paths(paths: dict, library: Path, workspace: Path) -> None:
    if not isinstance(paths, dict) or not {"incoming", "personas"}.issubset(paths) or set(paths) - {"incoming", "personas", "authorization"}:
        raise ValueError("Invalid frozen local paths")
    for value in paths.values():
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("Frozen local paths must be absolute")
        check_private_location(Path(value), workspace)
    incoming, output = Path(paths["incoming"]), Path(paths["personas"])
    if (not incoming.is_dir() or (output.exists() and not output.is_dir())
            or any(overlap(a, b) for a, b in ((incoming, library), (incoming, output), (library, output)))):
        raise ValueError("Frozen local input or output boundary changed")
    if "authorization" in paths:
        record = Path(paths["authorization"])
        if not record.is_file() or any(record.is_relative_to(p) for p in (incoming, output, library)):
            raise ValueError("Frozen authorization record boundary changed")


def initialize(config: Path, library: Path, *, confirm_local_storage: bool, workspace: Path = ROOT) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm configuration, inputs and outputs are on non-synced local storage")
    library = check_private_location(library, workspace)
    config = check_private_location(config, workspace)
    if not config.is_file() or config.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Local job configuration is missing or too large")
    config_digest = sha(config)
    job = read_json(config)
    validate_job(job)
    paths = resolve_paths(job, config, library, workspace)
    directory = job_directory(library, job["job"]["id"], workspace)
    if directory.exists():
        raise ValueError("Job already exists; frozen configuration cannot be overwritten")
    library.mkdir(parents=True, exist_ok=True)
    check_private_location(library, workspace)
    lock = library / ".job-init.lock"
    token = canonical({"pid": os.getpid(), "token": uuid.uuid4().hex}).encode()
    write_new(lock, token)
    try:
        if sha(config) != config_digest:
            raise ValueError("Configuration changed during initialization")
        checked_paths(paths, library, workspace)
        directory.mkdir()
        marker = directory / ".initializing"
        write_new(marker, token)
        write_new(directory / "job.v2.json", (canonical(job) + "\n").encode("utf-8"))
        write_new(directory / "paths.private.json", (canonical(paths) + "\n").encode("utf-8"))
        state = {"schema_version": "2.0", "pipeline_version": PIPELINE_VERSION,
                 "job_id": job["job"]["id"], "config_sha256": digest(canonical(job).encode()),
                 "paths_sha256": digest(canonical(paths).encode()), "status": "identity_review",
                 "approvals": {}, "revision": 0, "source_ids": {}, "updated_at": now(), "network_requests": 0}
        write_new(directory / "state.json", (canonical(state) + "\n").encode("utf-8"))
        if sha(config) != config_digest:
            raise ValueError("Configuration changed; incomplete initialization retained")
        check_private_location(directory, workspace)
        if plain_path(marker).read_bytes() != token:
            raise ValueError("Initialization state changed")
        marker.unlink()
        return state
    finally:
        if plain_path(lock).is_file() and lock.read_bytes() == token:
            lock.unlink()


def load_job(library: Path, job_id: str, *, confirm_local_storage: bool, workspace: Path = ROOT) -> tuple[dict, dict, dict]:
    if confirm_local_storage is not True:
        raise ValueError("Confirm the local job storage before loading")
    library = check_private_location(library, workspace)
    directory = job_directory(library, job_id, workspace)
    if any(path.exists() or path.is_symlink() for path in (directory / ".initializing", library / ".job-init.lock")):
        raise ValueError("Local job initialization is incomplete or locked")
    job = read_json(plain_path(directory / "job.v2.json"))
    validate_job(job)
    paths = read_json(plain_path(directory / "paths.private.json"))
    state = read_json(plain_path(directory / "state.json"))
    if (type(state.get("revision")) is not int or not 0 <= state["revision"] <= 9999
            or not isinstance(state.get("approvals"), dict) or not isinstance(state.get("source_ids"), dict)
            or state.get("status") not in ("created", "identity_review", "material_review", "sensitivity_review",
                                           "input_changed_use_update", "input_changed_during_build", "needs_inputs", "review_required")
            or type(state.get("source_year", 2000)) is not int or not 1000 <= state.get("source_year", 2000) <= 9999):
        raise ValueError("Invalid local job state")
    for gate, record in state["approvals"].items():
        if (gate not in {"identity", "material", "sensitivity"} or not isinstance(record, dict)
                or not isinstance(record.get("fingerprint"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["fingerprint"])
                or record.get("decision") != "approved"):
            raise ValueError("Invalid local review state")
    if any(not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(value, str)
           or not re.fullmatch(r"P-\d{4}-\d{3}", value) for key, value in state["source_ids"].items()):
        raise ValueError("Invalid source identity state")
    if (state.get("schema_version") != "2.0" or state.get("pipeline_version") != PIPELINE_VERSION
            or state.get("config_sha256") != digest(canonical(job).encode())
            or state.get("paths_sha256") != digest(canonical(paths).encode())
            or state.get("job_id") != job_id or job["job"]["id"] != job_id):
        raise ValueError("Frozen local job or pipeline version changed; create a new job")
    checked_paths(paths, library, workspace)
    return job, paths, state


def verify_snapshot(directory: Path, state: dict) -> None:
    relative = state.get("snapshot")
    if not isinstance(relative, str) or not re.fullmatch(r"snapshots/v\d{4}-[0-9a-f]{8}", relative):
        raise ValueError("Invalid snapshot path")
    snapshot = plain_path(directory / relative)
    index = plain_path(snapshot / "checksums.json")
    if sha(index) != state.get("snapshot_sha256"):
        raise ValueError("Snapshot checksum index changed")
    checksums = read_json(index)
    expected = {"01-sources.csv", "chunks.jsonl", "inventory.private.json", "report.json", "policy.json", "review.json"}
    if set(checksums) != expected or {p.name for p in snapshot.iterdir()} != expected | {"checksums.json"}:
        raise ValueError("Snapshot inventory changed")
    for name, checksum in checksums.items():
        if sha(plain_path(snapshot / name)) != checksum:
            raise ValueError("Immutable snapshot content changed")


@contextmanager
def job_lock(directory: Path):
    path = plain_path(directory / ".lock")
    token = canonical({"pid": os.getpid(), "token": uuid.uuid4().hex}).encode()
    write_new(path, token)
    try:
        yield
    finally:
        if plain_path(path).is_file() and path.read_bytes() == token:
            path.unlink()


def input_inventory(paths: dict) -> tuple[str, list[dict]]:
    incoming = plain_path(Path(paths["incoming"]))
    entries, total = [], 0
    def failed_walk(error):
        raise ValueError("Cannot enumerate all local input files") from None
    for current, directories, filenames in os.walk(incoming, followlinks=False, onerror=failed_walk):
        for name in directories:
            plain_path(Path(current) / name)
            if name.casefold() == ".git":
                raise ValueError("Nested Git input is not supported")
        for name in sorted(filenames):
            if len(entries) >= 500:
                raise ValueError("Input exceeds 500 files")
            path = Path(current) / name
            relative = path.relative_to(incoming).as_posix()
            record = {"file_id": digest(relative.encode())[:16], "path": relative}
            try:
                plain_path(path)
            except ValueError:
                record["status"] = "unsafe_path"
                entries.append(record)
                continue
            info = path.stat()
            if not path.is_file():
                raise ValueError("Only regular local input files are supported")
            record.update(size=info.st_size, modified_ns=info.st_mtime_ns)
            if info.st_size > MAX_INPUT_BYTES:
                record["status"] = "too_large"
            else:
                total += info.st_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Input exceeds the 20 MiB total batch limit")
                record.update(sha256=sha(path), status="pending")
                if (path.stat().st_size, path.stat().st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    raise ValueError("Inputs changed during fingerprinting")
            entries.append(record)
    entries.sort(key=lambda value: value["path"])
    basis = {"files": entries}
    if "authorization" in paths:
        record = plain_path(Path(paths["authorization"]))
        if not record.is_file() or not 0 < record.stat().st_size <= 2 * 1024 * 1024:
            raise ValueError("Authorization record is empty or unavailable")
        basis["authorization_sha256"] = sha(record)
    return digest(canonical(basis).encode()), entries


def approve(library: Path, job_id: str, gates: list[str], review_record: Path, *,
            confirm_local_storage: bool, workspace: Path = ROOT) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm local storage before reviewing inputs")
    if not gates or set(gates) - {"identity", "material", "sensitivity"}:
        raise ValueError("Choose identity, material or sensitivity review")
    directory = job_directory(library, job_id, workspace)
    with job_lock(directory):
        job, paths, state = load_job(library, job_id, confirm_local_storage=confirm_local_storage, workspace=workspace)
        record_path = check_private_location(review_record, workspace)
        if any(record_path.is_relative_to(Path(value)) for value in (str(library.resolve()), paths["incoming"], paths["personas"])):
            raise ValueError("Keep review records separate from input and generated directories")
        record = read_json(record_path)
        if (set(record) != {"reviewer", "note", "decision"} or record["decision"] != "approved"
                or any(not isinstance(record[key], str) or not record[key].strip() for key in ("reviewer", "note"))):
            raise ValueError("A local approved review record with reviewer and note is required")
        fingerprint = input_inventory(paths)[0] if set(gates) - {"identity"} else state["config_sha256"]
        for gate in gates:
            state["approvals"][gate] = {**record, "at": now(), "fingerprint": state["config_sha256"] if gate == "identity" else fingerprint}
        state.update(status="created", updated_at=now())
        write_json(plain_path(directory / "state.json"), state)
        return state


def build(library: Path, job_id: str, *, confirm_local_storage: bool,
          update: bool = False, workspace: Path = ROOT) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm local storage before preparing inputs")
    directory = job_directory(library, job_id, workspace)
    with job_lock(directory):
        job, paths, state = load_job(library, job_id, confirm_local_storage=confirm_local_storage, workspace=workspace)
        def pause(status):
            state.update(status=status, updated_at=now())
            write_json(plain_path(directory / "state.json"), state)
            return state
        if state["approvals"].get("identity", {}).get("fingerprint") != state["config_sha256"]:
            return pause("identity_review")
        fingerprint, entries = input_inventory(paths)
        if state["approvals"].get("material", {}).get("fingerprint") != fingerprint:
            return pause("material_review")
        if state.get("input_fingerprint") == fingerprint:
            verify_snapshot(directory, state)
            return state
        if state.get("input_fingerprint") and not update:
            return pause("input_changed_use_update")
        documents, quarantined, findings, duplicates = [], [], [], []
        seen = set()
        for entry in entries:
            path = plain_path(Path(paths["incoming"]) / entry["path"]) if entry["status"] != "unsafe_path" else None
            if entry["status"] != "pending" or path.suffix.casefold() not in {".txt", ".md"}:
                quarantined.append({"file_id": entry["file_id"], "reason": entry["status"] if entry["status"] != "pending" else "unsupported_format"})
                continue
            try:
                with path.open("rb") as handle:
                    payload = handle.read(MAX_INPUT_BYTES + 1)
                if len(payload) > MAX_INPUT_BYTES or digest(payload) != entry["sha256"]:
                    return pause("input_changed_during_build")
                text = normalize(payload.decode("utf-8-sig"))
                if not text:
                    raise ValueError("empty_text")
            except (UnicodeError, ValueError):
                quarantined.append({"file_id": entry["file_id"], "reason": "invalid_or_empty_text"})
                continue
            checksum = digest(text.encode())
            if checksum in seen:
                duplicates.append(entry["file_id"])
                continue
            seen.add(checksum)
            rules = [name for name, pattern in SENSITIVE.items() if pattern.search(text)]
            if rules:
                findings.append({"file_id": entry["file_id"], "rules": rules})
            if "credential" in rules:
                quarantined.append({"file_id": entry["file_id"], "reason": "credential_requires_clean_input"})
                continue
            for name in ("email", "phone"):
                text = SENSITIVE[name].sub(f"[{name.upper()}_REDACTED]", text)
            documents.append({"file_id": entry["file_id"], "checksum": checksum, "text": text})
        if findings and state["approvals"].get("sensitivity", {}).get("fingerprint") != fingerprint:
            state["findings"] = findings
            return pause("sensitivity_review")
        if not documents:
            state["quarantined"] = quarantined
            return pause("needs_inputs")
        source_ids, sources, chunks = dict(state["source_ids"]), [], []
        year = state.get("source_year", date.today().year)
        for document in documents:
            checksum = document["checksum"]
            if checksum not in source_ids:
                if len(source_ids) >= 999:
                    raise ValueError("Source ID capacity reached; start a new job")
                source_ids[checksum] = f"P-{year:04d}-{len(source_ids)+1:03d}"
            source_id = source_ids[checksum]
            sources.append({"source_id": source_id, "ingest_mode": "upload", "title": document["file_id"],
                            "url_or_local_reference": "input-file:" + document["file_id"], "content_checksum": checksum,
                            "privacy_level": "private", "rights_or_permission": "local declared and reviewed material"})
            for number, (part, location) in enumerate(segment(document["text"]), 1):
                chunks.append({"schema_version": "1.0", "chunk_id": f"{source_id}#C-{number:04d}", "source_id": source_id,
                               "content_kind": "uploaded_text", "content_mode": "excerpt", "text": part,
                               "speaker": None, "date": None, "language": "zh" if re.search(r"[\u3400-\u9fff]", part) else "en",
                               "location": location + " (normalized/redacted local input)", "direct_quote": False,
                               "topics": [], "privacy_level": "private", "rights_mode": "local_only_text",
                               "delivery_channel": "local_only", "checksum": digest(part.encode())})
        if input_inventory(paths)[0] != fingerprint:
            return pause("input_changed_during_build")
        if state["revision"] >= 9999:
            raise ValueError("Snapshot revision capacity reached; start a new job")
        revision = state["revision"] + 1
        snapshot = plain_path(directory / "snapshots" / f"v{revision:04d}-{uuid.uuid4().hex[:8]}")
        snapshot.mkdir(parents=True)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(sources[0]))
        writer.writeheader()
        writer.writerows(sources)
        write_new(snapshot / "01-sources.csv", stream.getvalue().encode("utf-8"))
        write_new(snapshot / "chunks.jsonl", ("\n".join(canonical(item) for item in chunks) + "\n").encode("utf-8"))
        active = sorted(item["source_id"] for item in sources)
        report = {"pipeline_version": PIPELINE_VERSION, "revision": revision, "chunks": len(chunks),
                  "unique_text_sources": len(sources), "independent_sources_verified": False,
                  "persona_generated": False, "evidence_cards": 0, "network_requests": 0,
                  "privacy_screening_complete": False, "findings": findings, "quarantined": quarantined,
                  "duplicates": duplicates, "material_basis": job["policy"]["material_basis"],
                  "added_source_ids": sorted(set(active) - set(state.get("active_source_ids", []))),
                  "withdrawn_source_ids": sorted(set(state.get("active_source_ids", [])) - set(active))}
        review = {"material_declaration": job["material_declaration"], "approvals": state["approvals"],
                  "input_fingerprint": fingerprint, "config_sha256": state["config_sha256"]}
        effective_policy = copy.deepcopy(job["policy"])
        effective_policy["sensitive_data"] = effective_policy["sensitive_data"] or bool(findings)
        for name, value in (("inventory.private.json", entries), ("report.json", report),
                            ("policy.json", effective_policy), ("review.json", review)):
            write_new(snapshot / name, (canonical(value) + "\n").encode("utf-8"))
        checksums = {path.name: sha(path) for path in snapshot.iterdir()}
        write_new(snapshot / "checksums.json", (canonical(checksums) + "\n").encode("utf-8"))
        state.update(status="review_required", revision=revision, snapshot=snapshot.relative_to(directory).as_posix(),
                     snapshot_sha256=sha(snapshot / "checksums.json"), input_fingerprint=fingerprint,
                     source_ids=source_ids, source_year=year, active_source_ids=active, updated_at=now())
        write_json(plain_path(directory / "state.json"), state)
        return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--confirm-local-storage", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("--config", type=Path, required=True)
    commands.add_parser("status").add_argument("--job", required=True)
    prepare = commands.add_parser("build")
    prepare.add_argument("--job", required=True)
    prepare.add_argument("--update", action="store_true")
    review = commands.add_parser("approve")
    review.add_argument("--job", required=True)
    review.add_argument("--gate", nargs="+", choices=("identity", "material", "sensitivity"), required=True)
    review.add_argument("--record", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            state = initialize(args.config, args.library, confirm_local_storage=args.confirm_local_storage)
        elif args.command == "approve":
            state = approve(args.library, args.job, args.gate, args.record, confirm_local_storage=args.confirm_local_storage)
        elif args.command == "build":
            state = build(args.library, args.job, confirm_local_storage=args.confirm_local_storage, update=args.update)
        else:
            _, _, state = load_job(args.library, args.job, confirm_local_storage=args.confirm_local_storage)
            if state.get("snapshot"):
                verify_snapshot(job_directory(args.library, args.job), state)
        print(json.dumps({"ok": True, "status": state["status"], "revision": state["revision"],
                          "network_requests": 0, "persona_generated": False}))
        return 0
    except (ValueError, OSError):
        # Do not echo malformed private metadata or filesystem paths.
        print(json.dumps({"ok": False, "error": "Local job validation or storage check failed; inspect the local configuration and documentation"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
