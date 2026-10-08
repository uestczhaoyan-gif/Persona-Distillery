#!/usr/bin/env python3
"""External local-only v2 distillation jobs. Initialization is not persona generation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

from distill import canonical, digest, now
from migrate_persona import (ROOT, check_private_location, overlap, plain_path,
                            read_json, sha, validate, write_new)
from package_policy import ExecutionTarget, authorize_operation

PIPELINE_VERSION = "local-text-v2"
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")


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
    if (state.get("schema_version") != "2.0" or state.get("pipeline_version") != PIPELINE_VERSION
            or state.get("config_sha256") != digest(canonical(job).encode())
            or state.get("paths_sha256") != digest(canonical(paths).encode())
            or state.get("job_id") != job_id or job["job"]["id"] != job_id):
        raise ValueError("Frozen local job or pipeline version changed; create a new job")
    checked_paths(paths, library, workspace)
    return job, paths, state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--confirm-local-storage", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("--config", type=Path, required=True)
    commands.add_parser("status").add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            state = initialize(args.config, args.library, confirm_local_storage=args.confirm_local_storage)
        else:
            _, _, state = load_job(args.library, args.job, confirm_local_storage=args.confirm_local_storage)
        print(json.dumps({"ok": True, "status": state["status"], "revision": state["revision"],
                          "network_requests": 0, "persona_generated": False}))
        return 0
    except (ValueError, OSError):
        # Do not echo malformed private metadata or filesystem paths.
        print(json.dumps({"ok": False, "error": "Local job validation or storage check failed; inspect the local configuration and documentation"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
