#!/usr/bin/env python3
"""Offline TXT/Markdown preparation with explicit review gates and snapshots.

This prepares source-linked local-only chunks for human evidence work. It does
not synthesize a persona, allocate a model, or publish a package.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import uuid

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
PIPELINE_VERSION = "local-text-v1"
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
MAX_BYTES = 5 * 1024 * 1024
MAX_FILES = 500
SENSITIVE = {"email": re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
             "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
             "credential": re.compile(r"(?i)(?:api[_ -]?key|password|secret|密码)\s*[:=：]\s*\S+")}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_text(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError("Refusing symlink output")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def run_dir(job_id: str, root: Path = ROOT) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("Invalid job ID")
    root = root.resolve()
    directory = root / "人物蒸馏/runs" / job_id
    if not directory.resolve().is_relative_to(root) or directory.is_symlink():
        raise ValueError("Task directory must stay inside the workspace")
    return directory


@contextmanager
def locked(directory: Path):
    path = directory / ".lock"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(canonical({"pid": os.getpid(), "created_at": now()}))
    except FileExistsError:
        raise ValueError("Task is already locked; after a crash inspect .lock before removing it") from None
    try:
        yield
    finally:
        path.unlink()


def validate_config(job: dict, root: Path) -> None:
    schema = read_json(root / "人物蒸馏/schemas/distillation-job.schema.json")
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(job))
    if errors:
        raise ValueError("Invalid job configuration at " + "/".join(map(str, errors[0].absolute_path)))
    if job["input"]["mode"] != "upload":
        raise ValueError("This runner supports local upload mode only")
    if job["input"]["upload"].get("participant_map_file"):
        raise ValueError("Participant-map import is not implemented; supply manually de-identified text with a reviewed local job")
    policy = job["privacy"]
    if (policy["local_only"] is not True or policy["network_enabled"] is not False
            or policy["remote_processing_allowed"] is not False or policy["publish_visibility"] != "private"):
        raise ValueError("Local runner requires local_only/private with network and remote processing disabled")


def initialize(config: Path, root: Path = ROOT) -> dict:
    config = config.resolve()
    job = read_json(config)
    validate_config(job, root)
    directory = run_dir(job["job"]["id"], root)
    if directory.exists():
        raise ValueError("Job already exists; frozen configuration cannot be overwritten")
    incoming = Path(job["input"]["upload"]["incoming_directory"])
    incoming = incoming if incoming.is_absolute() else config.parent / incoming
    if incoming.is_symlink() or not incoming.is_dir():
        raise ValueError("Incoming directory must exist and cannot be a symlink")
    paths = {"incoming": str(incoming.resolve())}
    for name, value in (("consent", job.get("consent", {}).get("record_file")),
                        ("participant_map", job["input"]["upload"].get("participant_map_file"))):
        if value:
            path = Path(value)
            paths[name] = str((path if path.is_absolute() else config.parent / path).resolve())
    directory.mkdir(parents=True)
    write_json(directory / "job.json", job)
    write_json(directory / "paths.private.json", paths)
    state = {"schema_version": "1.0", "pipeline_version": PIPELINE_VERSION, "job_id": job["job"]["id"],
             "config_sha256": digest(canonical(job).encode()), "paths_sha256": digest(canonical(paths).encode()),
             "status": "identity_review", "approvals": {},
             "revision": 0, "source_ids": {}, "updated_at": now(), "network_requests": 0}
    write_json(directory / "state.json", state)
    return state


def load_job(directory: Path, root: Path) -> tuple[dict, dict, dict]:
    job, paths, state = (read_json(directory / name) for name in ("job.json", "paths.private.json", "state.json"))
    validate_config(job, root)
    if (state.get("config_sha256") != digest(canonical(job).encode())
            or state.get("paths_sha256") != digest(canonical(paths).encode())
            or state.get("pipeline_version") != PIPELINE_VERSION or job["job"]["id"] != directory.name):
        raise ValueError("Frozen job configuration or pipeline version changed; create a new job")
    return job, paths, state


def inventory(paths: dict) -> tuple[str, list[dict]]:
    incoming = Path(paths["incoming"])
    if incoming.is_symlink() or not incoming.is_dir():
        raise ValueError("Incoming directory disappeared or became a symlink")
    entries = []
    files = sorted(p for p in incoming.rglob("*") if p.is_file() or p.is_symlink())
    if len(files) > MAX_FILES:
        raise ValueError("Input exceeds 500 files; split into smaller jobs")
    for path in files:
        relative = path.relative_to(incoming).as_posix()
        file_id = digest(relative.encode())[:16]
        unsafe = path.is_symlink() or not path.resolve().is_relative_to(incoming.resolve())
        if unsafe:
            entries.append({"file_id": file_id, "path": relative, "status": "unsafe_path"})
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            entries.append({"file_id": file_id, "path": relative, "status": "too_large", "size": size})
            continue
        payload = path.read_bytes()
        entries.append({"file_id": file_id, "path": relative, "size": len(payload), "sha256": digest(payload), "status": "pending"})
    records = {"files": entries}
    for key in ("consent", "participant_map"):
        if key in paths:
            path = Path(paths[key])
            if path.is_symlink() or not path.is_file():
                records[key] = "missing_or_symlink"
            else:
                records[key] = digest(path.read_bytes())
    return digest(canonical(records).encode()), entries


def approve(job_id: str, gate: str, reviewer: str, note: str, root: Path = ROOT) -> dict:
    if gate not in {"identity", "privacy", "sensitivity"} or not reviewer.strip() or not note.strip():
        raise ValueError("Choose identity/privacy/sensitivity and record reviewer plus review note")
    directory = run_dir(job_id, root)
    with locked(directory):
        job, paths, state = load_job(directory, root)
        fingerprint, _ = inventory(paths)
        state["approvals"][gate] = {"reviewer": reviewer, "note": note, "fingerprint": fingerprint, "at": now()}
        state.update(updated_at=now(), status="created")
        write_json(directory / "state.json", state)
    return state


def consent_valid(job: dict, paths: dict) -> bool:
    if job["subject"]["kind"] not in {"living_public", "living_private", "self", "deceased_private"}:
        return True
    consent = job.get("consent", {})
    path = Path(paths.get("consent", ""))
    expiration = consent.get("expires_on")
    return (consent.get("status") == "granted" and path.is_file() and not path.is_symlink()
            and path.stat().st_size > 0 and (not expiration or date.fromisoformat(expiration) >= date.today()))


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    if "\x00" in text:
        raise ValueError("binary_content")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def segment(text: str, maximum: int = 800) -> list[tuple[str, str]]:
    result = []
    lines = text.splitlines(keepends=True)
    current, start = "", 1
    for number, line in enumerate(lines, 1):
        if current and len(current) + len(line) > maximum:
            result.append((current, f"lines {start}-{number-1}"))
            current = ""
        if len(line) > maximum:
            for offset in range(0, len(line), maximum):
                result.append((line[offset:offset+maximum], f"line {number}; chars {offset}-{min(offset+maximum, len(line))}"))
            start = number + 1
        else:
            if not current:
                start = number
            current += line
    if current:
        result.append((current, f"lines {start}-{len(lines)}"))
    return result


def build(job_id: str, root: Path = ROOT, update: bool = False) -> dict:
    directory = run_dir(job_id, root)
    with locked(directory):
        job, paths, state = load_job(directory, root)
        fingerprint, entries = inventory(paths)
        if not state["approvals"].get("identity"):
            return pause(directory, state, "identity_review")
        if not consent_valid(job, paths):
            return pause(directory, state, "consent_review")
        if state["approvals"].get("privacy", {}).get("fingerprint") != fingerprint:
            return pause(directory, state, "privacy_review")
        if state.get("input_fingerprint") == fingerprint:
            return state
        if state.get("input_fingerprint") and not update:
            return pause(directory, state, "input_changed_use_update")
        documents, quarantined, duplicates, findings = [], [], [], []
        content_seen = set()
        for entry in entries:
            file_id = entry["file_id"]
            path = Path(paths["incoming"]) / entry["path"]
            if entry["status"] != "pending" or path.suffix.casefold() not in {".md", ".txt"}:
                quarantined.append({"file_id": file_id, "reason": entry["status"] if entry["status"] != "pending" else "unsupported_format"})
                continue
            try:
                payload = path.read_bytes()
                if digest(payload) != entry["sha256"]:
                    raise ValueError("input_changed_during_build")
                text = normalize(payload.decode("utf-8-sig"))
                if not text:
                    raise ValueError("empty_text")
                content_hash = digest(text.encode())
                if content_hash in content_seen:
                    duplicates.append(file_id)
                    continue
                content_seen.add(content_hash)
                rules = [rule for rule, pattern in SENSITIVE.items() if pattern.search(text)]
                if rules:
                    findings.append({"file_id": file_id, "rules": rules})
                documents.append({"file_id": file_id, "source_path": entry["path"], "content_sha256": content_hash, "text": text})
            except (UnicodeError, OSError, ValueError) as error:
                reason = str(error) if isinstance(error, ValueError) and str(error) in {"empty_text", "binary_content", "input_changed_during_build"} else "unreadable_or_invalid_utf8"
                quarantined.append({"file_id": file_id, "reason": reason})
        if findings and state["approvals"].get("sensitivity", {}).get("fingerprint") != fingerprint:
            state["findings"] = findings
            return pause(directory, state, "sensitivity_review")
        # Never put credentials into prepared chunks, even after a generic review.
        blocked_ids = {finding["file_id"] for finding in findings if "credential" in finding["rules"]}
        documents = [item for item in documents if item["file_id"] not in blocked_ids]
        quarantined.extend({"file_id": file_id, "reason": "credential_requires_clean_input"} for file_id in sorted(blocked_ids))
        if not documents:
            state["quarantined"] = quarantined
            return pause(directory, state, "needs_inputs")
        source_ids = dict(state["source_ids"])
        year = int(state.get("source_year", date.today().year))
        chunks, sources = [], []
        for document in documents:
            key = document["content_sha256"]
            if key not in source_ids:
                ordinal = len(source_ids) + 1
                if ordinal > 999:
                    raise ValueError("Source ID capacity reached; create an incremental job")
                source_ids[key] = f"P-{year:04d}-{ordinal:03d}"
            source_id = source_ids[key]
            text = document["text"]
            # Contact findings require review first, then get deterministic masks.
            for rule in ("email", "phone"):
                text = SENSITIVE[rule].sub(f"[{rule.upper()}_REDACTED]", text)
            sources.append({"source_id": source_id, "ingest_mode": "upload", "title": document["file_id"],
                            "url_or_local_reference": "input-file:" + document["file_id"],
                            "content_checksum": key, "privacy_level": "private", "rights_or_permission": "local review only"})
            for number, (part, location) in enumerate(segment(text), 1):
                if number > 9999:
                    raise ValueError("Chunk ID capacity exceeded")
                chunks.append({"schema_version": "1.0", "chunk_id": f"{source_id}#C-{number:04d}",
                               "source_id": source_id, "content_kind": "uploaded_text", "content_mode": "excerpt",
                               "text": part, "speaker": None, "date": None,
                               "language": "zh" if re.search(r"[\u3400-\u9fff]", part) else "en",
                               "location": location + " (normalized input)", "direct_quote": False, "topics": [],
                               "privacy_level": "private", "rights_mode": "local_only_text", "delivery_channel": "local_only",
                               "checksum": digest(part.encode())})
        # Check for concurrent source edits before committing the immutable view.
        if inventory(paths)[0] != fingerprint:
            return pause(directory, state, "input_changed_during_build")
        revision = state["revision"] + 1
        snapshot = directory / "snapshots" / f"v{revision:04d}-{uuid.uuid4().hex[:8]}"
        snapshot.mkdir(parents=True)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(sources[0]))
        writer.writeheader()
        writer.writerows(sources)
        atomic_text(snapshot / "01-sources.csv", stream.getvalue())
        atomic_text(snapshot / "chunks.jsonl", "".join(canonical(item) + "\n" for item in chunks))
        write_json(snapshot / "inventory.private.json", entries)
        report = {"pipeline_version": PIPELINE_VERSION, "job_id": job_id, "revision": revision,
                  "unique_text_sources": len(sources), "independent_sources_verified": False,
                  "chunks": len(chunks), "duplicates": duplicates, "quarantined": quarantined, "findings": findings,
                  "network_requests": 0, "evidence_cards": 0, "persona_generated": False,
                  "privacy_screening_complete": False,
                  "minimum_independent_sources_requested": job["quality"]["minimum_independent_sources"],
                  "minimum_evidence_cards_requested": job["quality"]["minimum_evidence_cards"],
                  "next_step": "Human review of sources, attribution, rights, evidence and context memory"}
        active = sorted(item["source_id"] for item in sources)
        report["added_source_ids"] = sorted(set(active) - set(state.get("active_source_ids", [])))
        report["withdrawn_source_ids"] = sorted(set(state.get("active_source_ids", [])) - set(active))
        write_json(snapshot / "report.json", report)
        atomic_text(snapshot / "review.md", "# 本地来源与片段审核\n\n状态：等待证据与人物合成的人工工作。\n\n"
                    + f"来源：{len(sources)}；片段：{len(chunks)}；隔离文件：{len(quarantined)}。\n\n"
                    + "这些片段为本地私有材料，不可直接复制为公开人物包。无自动事实提炼、完整隐私保证或发布。\n")
        state.update(status="review_required", revision=revision, input_fingerprint=fingerprint,
                     source_ids=source_ids, source_year=year, active_source_ids=active, snapshot=snapshot.relative_to(directory).as_posix(),
                     findings=findings, quarantined=quarantined, updated_at=now())
        write_json(directory / "state.json", state)
        return state


def pause(directory: Path, state: dict, status: str) -> dict:
    state.update(status=status, updated_at=now())
    write_json(directory / "state.json", state)
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--config", type=Path, required=True)
    for command in ("build", "status", "approve"):
        child = commands.add_parser(command)
        child.add_argument("--job", required=True)
        if command == "build":
            child.add_argument("--update", action="store_true")
        if command == "approve":
            child.add_argument("--gate", choices=("identity", "privacy", "sensitivity"), required=True)
            child.add_argument("--reviewed-by", required=True)
            child.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            state = initialize(args.config, args.root)
        elif args.command == "build":
            state = build(args.job, args.root, args.update)
        elif args.command == "approve":
            state = approve(args.job, args.gate, args.reviewed_by, args.note, args.root)
        else:
            _, _, state = load_job(run_dir(args.job, args.root), args.root)
        print(json.dumps({"ok": True, **state}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
