#!/usr/bin/env python3
"""Validate structured persona memory without requiring jsonschema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np


CHUNK_ID = re.compile(r"^P-[0-9]{4}-[0-9]{3}#C-[0-9]{4}$")
SOURCE_ID = re.compile(r"^P-[0-9]{4}-[0-9]{3}$")
EVIDENCE_ID = re.compile(r"^E-[0-9]{4}$")
MEMORY_ID = re.compile(r"^M-[0-9]{4}$")
MARKDOWN_EVIDENCE = re.compile(r"^### (E-[0-9]{4})\s", re.MULTILINE)
MARKDOWN_MEMORY = re.compile(r"^\|\s*(M-[0-9]{4})\s*\|", re.MULTILINE)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected object")
        records.append(value)
    return records


def require(record: dict[str, Any], keys: set[str], label: str, errors: list[str]) -> None:
    missing = sorted(keys - record.keys())
    if missing:
        errors.append(f"{label}: missing {','.join(missing)}")


def validate(person_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest = json.loads((person_dir / "manifest.json").read_text(encoding="utf-8"))
    person_id = manifest["person_id"]
    with (person_dir / "01-sources.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    source_ids = {row["source_id"] for row in sources}
    if len(source_ids) != len(sources) or any(not SOURCE_ID.fullmatch(value) for value in source_ids):
        errors.append("source IDs are duplicated or invalid")

    chunks_path = person_dir / "processed" / "chunks.jsonl"
    evidence_path = person_dir / "processed" / "evidence.jsonl"
    context_path = person_dir / "processed" / "context-memory.jsonl"
    index_dir = person_dir / "processed" / "index"
    chunks = load_jsonl(chunks_path)
    evidence = load_jsonl(evidence_path)
    context_memory = load_jsonl(context_path)
    records = load_jsonl(index_dir / "records.jsonl")
    index_manifest = json.loads((index_dir / "index-manifest.json").read_text(encoding="utf-8"))

    chunk_required = {
        "schema_version", "chunk_id", "source_id", "content_kind", "content_mode", "text", "speaker",
        "date", "language", "location", "direct_quote", "topics", "privacy_level", "rights_mode", "checksum",
    }
    evidence_required = {
        "schema_version", "evidence_id", "card_type", "claim", "evidence_summary", "interpretation",
        "limitations", "source_refs", "topics", "confidence", "status",
    }
    context_required = {
        "schema_version", "memory_id", "title", "period", "situation", "observed_move",
        "tension", "dialogue_use", "source_ids", "topics", "confidence", "status",
    }
    chunk_ids: set[str] = set()
    for item in chunks:
        label = item.get("chunk_id", "chunk")
        require(item, chunk_required, label, errors)
        chunk_id = item.get("chunk_id", "")
        if not CHUNK_ID.fullmatch(chunk_id) or chunk_id in chunk_ids:
            errors.append(f"invalid or duplicate chunk ID: {chunk_id}")
        chunk_ids.add(chunk_id)
        if item.get("source_id") not in source_ids:
            errors.append(f"{chunk_id}: unknown source {item.get('source_id')}")
        if item.get("content_mode") == "summary" and item.get("direct_quote") is not False:
            errors.append(f"{chunk_id}: summary cannot be a direct quote")
        if not item.get("text") or not item.get("checksum"):
            errors.append(f"{chunk_id}: empty text or checksum")

    evidence_ids: set[str] = set()
    for item in evidence:
        label = item.get("evidence_id", "evidence")
        require(item, evidence_required, label, errors)
        evidence_id = item.get("evidence_id", "")
        if not EVIDENCE_ID.fullmatch(evidence_id) or evidence_id in evidence_ids:
            errors.append(f"invalid or duplicate evidence ID: {evidence_id}")
        evidence_ids.add(evidence_id)
        for ref in item.get("source_refs", []):
            if ref.get("source_id") not in source_ids:
                errors.append(f"{evidence_id}: unknown source {ref.get('source_id')}")
            for chunk_id in ref.get("chunk_ids", []):
                if chunk_id not in chunk_ids:
                    errors.append(f"{evidence_id}: unknown chunk {chunk_id}")

    markdown_ids = set(MARKDOWN_EVIDENCE.findall((person_dir / "02-evidence.md").read_text(encoding="utf-8")))
    if markdown_ids != evidence_ids:
        errors.append(f"Markdown/JSONL evidence mismatch: missing={sorted(markdown_ids-evidence_ids)}, extra={sorted(evidence_ids-markdown_ids)}")

    memory_ids: set[str] = set()
    for item in context_memory:
        label = item.get("memory_id", "context")
        require(item, context_required, label, errors)
        memory_id = item.get("memory_id", "")
        if not MEMORY_ID.fullmatch(memory_id) or memory_id in memory_ids:
            errors.append(f"invalid or duplicate memory ID: {memory_id}")
        memory_ids.add(memory_id)
        for source_id in item.get("source_ids", []):
            if source_id not in source_ids:
                errors.append(f"{memory_id}: unknown source {source_id}")
    markdown_memory_ids = set(MARKDOWN_MEMORY.findall((person_dir / "08-context-memory.md").read_text(encoding="utf-8")))
    if markdown_memory_ids != memory_ids:
        errors.append(f"Markdown/JSONL context mismatch: missing={sorted(markdown_memory_ids-memory_ids)}, extra={sorted(memory_ids-markdown_memory_ids)}")

    if [item.get("row") for item in records] != list(range(len(records))):
        errors.append("vector record rows are not consecutive")
    vectors = np.load(index_dir / "vectors.npy", allow_pickle=False)
    expected_dimensions = index_manifest["vector_index"]["dimensions"]
    if vectors.shape != (len(records), expected_dimensions) or vectors.dtype != np.float32:
        errors.append(f"vector shape/dtype mismatch: {vectors.shape}/{vectors.dtype}")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any((norms < 0.999) | (norms > 1.001)):
        errors.append("vector rows are not L2-normalized")

    connection = sqlite3.connect(index_dir / "memory.sqlite3")
    sqlite_counts = {
        "sources": connection.execute("SELECT count(*) FROM sources").fetchone()[0],
        "chunks": connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
        "evidence": connection.execute("SELECT count(*) FROM evidence").fetchone()[0],
        "context_memory": connection.execute("SELECT count(*) FROM context_memory").fetchone()[0],
        "search_rows": connection.execute("SELECT count(*) FROM memory_fts").fetchone()[0],
    }
    connection.close()
    expected_counts = {"sources": len(sources), "chunks": len(chunks), "evidence": len(evidence), "context_memory": len(context_memory), "search_rows": len(records)}
    if sqlite_counts != expected_counts or index_manifest.get("records") != expected_counts:
        errors.append(f"index counts mismatch: sqlite={sqlite_counts}, expected={expected_counts}, manifest={index_manifest.get('records')}")
    if index_manifest.get("person_id") != person_id or index_manifest.get("package_version") != manifest.get("package_version"):
        errors.append("index manifest identity/version mismatch")

    checksum_expectations = {
        "sources_sha256": sha256_text((person_dir / "01-sources.csv").read_text(encoding="utf-8-sig")),
        "evidence_markdown_sha256": sha256_text((person_dir / "02-evidence.md").read_text(encoding="utf-8")),
        "chunks_jsonl_sha256": sha256_text(chunks_path.read_text(encoding="utf-8")),
        "evidence_jsonl_sha256": sha256_text(evidence_path.read_text(encoding="utf-8")),
        "context_markdown_sha256": sha256_text((person_dir / "08-context-memory.md").read_text(encoding="utf-8")),
        "context_jsonl_sha256": sha256_text(context_path.read_text(encoding="utf-8")),
    }
    if index_manifest.get("checksums") != checksum_expectations:
        errors.append("index input/output checksums do not match")

    return {
        "person_id": person_id,
        "package_version": manifest["package_version"],
        **expected_counts,
        "vector_shape": list(vectors.shape),
        "errors": errors,
        "valid": not errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for person_dir in args.person_dirs:
        result = validate(person_dir.resolve())
        print(json.dumps(result, ensure_ascii=False))
        failed = failed or not result["valid"]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
