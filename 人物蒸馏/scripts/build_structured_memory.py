#!/usr/bin/env python3
"""Build reviewable JSONL memory and a reproducible local search index.

This migrates manually reviewed Markdown evidence. It deliberately emits summary
chunks, not source quotations, when raw source text is unavailable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np


CARD_HEADING = re.compile(r"^### (E-\d{4})\s+(.+)$", re.MULTILINE)
CONTEXT_ID = re.compile(r"^M-\d{4}$")
SOURCE_ID = re.compile(r"P-\d{4}-\d{3}")
TABLE_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$")
BOLD_FIELD = re.compile(r"^\*\*([^*：]+)：\*\*\s*(.*)$")


def clean(value: str) -> str:
    return value.strip().strip("`").strip()


def split_topics(value: str) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in re.split(r"[；;、]", value) if x.strip()))


def parse_cards(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    matches = list(CARD_HEADING.finditer(text))
    cards: list[dict[str, Any]] = []
    for i, match in enumerate(matches):
        body = text[match.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        fields: dict[str, str] = {}
        narratives: dict[str, str] = {}
        for line in body.splitlines():
            table = TABLE_ROW.match(line)
            if table:
                key, value = clean(table.group(1)), clean(table.group(2))
                if key not in {"字段", "---"} and value != "---":
                    fields[key] = value
                continue
            bold = BOLD_FIELD.match(line)
            if bold:
                narratives[clean(bold.group(1))] = clean(bold.group(2))
        cards.append(
            {
                "evidence_id": match.group(1),
                "claim": clean(match.group(2)),
                "fields": fields,
                "narratives": narratives,
            }
        )
    return cards


def parse_context_memory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("|"):
            continue
        cells = [clean(cell) for cell in line.strip().strip("|").split("|")]
        if not cells or not CONTEXT_ID.fullmatch(cells[0]):
            continue
        if len(cells) != 11:
            raise ValueError(f"{path}:{line_number} must have 11 table columns, got {len(cells)}")
        memory_id, title, period, situation, observed_move, tension, dialogue_use, source_field, topic_field, confidence, status = cells
        source_ids = list(dict.fromkeys(SOURCE_ID.findall(source_field)))
        if not source_ids:
            raise ValueError(f"{path}:{line_number} {memory_id} has no source reference")
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"{path}:{line_number} {memory_id} has invalid confidence")
        if status not in {"candidate", "reviewed", "withdrawn"}:
            raise ValueError(f"{path}:{line_number} {memory_id} has invalid status")
        records.append({
            "schema_version": "1.0",
            "memory_id": memory_id,
            "title": title,
            "period": period,
            "situation": situation,
            "observed_move": observed_move,
            "tension": tension,
            "dialogue_use": dialogue_use,
            "source_ids": source_ids,
            "topics": split_topics(topic_field),
            "confidence": confidence,
            "status": status,
        })
    if len({item["memory_id"] for item in records}) != len(records):
        raise ValueError(f"Duplicate context memory IDs in {path}")
    return records


def map_card_type(value: str) -> str:
    if "变化" in value or "修正" in value:
        return "change_over_time"
    if "决策" in value:
        return "decision_case"
    if "风格" in value:
        return "style_observation"
    if "可靠转述" in value:
        return "reliable_report"
    if any(token in value for token in ("文学", "小说", "象征", "寓言", "散文诗")):
        return "literary_scene"
    if "事实" in value:
        return "fact"
    if any(token in value for token in ("直接观点", "直接论", "演讲", "杂文")):
        return "direct_view"
    return "synthesis"


def map_confidence(fields: dict[str, str]) -> str:
    explicit = fields.get("可信度", "")
    if "低" in explicit:
        return "low"
    if "中" in explicit:
        return "medium"
    status = fields.get("证据状态", "")
    if any(token in status for token in ("高可信", "强", "直接证据", "直接文本")) or "高" in explicit:
        return "high"
    return "medium"


def first_present(values: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        if values.get(key):
            return values[key]
    return ""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hashed_vector(text: str, dimensions: int) -> np.ndarray:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    counts: Counter[tuple[int, int]] = Counter()
    for size in (2, 3, 4):
        if len(normalized) < size:
            continue
        for i in range(len(normalized) - size + 1):
            gram = normalized[i : i + size]
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "little")
            counts[(number % dimensions, 1 if number & (1 << 63) else -1)] += 1
    for token in re.findall(r"\w+", normalized, flags=re.UNICODE):
        digest = hashlib.blake2b(("w:" + token).encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "little")
        counts[(number % dimensions, 1 if number & (1 << 63) else -1)] += 2
    vector = np.zeros(dimensions, dtype=np.float32)
    for (index, sign), count in counts.items():
        vector[index] += sign * (1.0 + math.log(count))
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector


def build(person_dir: Path, dimensions: int) -> dict[str, Any]:
    manifest = json.loads((person_dir / "manifest.json").read_text(encoding="utf-8"))
    with (person_dir / "01-sources.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    source_by_id = {row["source_id"]: row for row in sources}
    cards = parse_cards(person_dir / "02-evidence.md")
    context_records = parse_context_memory(person_dir / "08-context-memory.md")
    if not cards:
        raise ValueError(f"No evidence cards found in {person_dir}")
    for item in context_records:
        unknown = [source_id for source_id in item["source_ids"] if source_id not in source_by_id]
        if unknown:
            raise ValueError(f"{item['memory_id']} references unknown sources: {unknown}")

    processed = person_dir / "processed"
    index_dir = processed / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []

    for card in cards:
        fields = card["fields"]
        narratives = card["narratives"]
        source_field = fields.get("来源", "")
        source_ids = list(dict.fromkeys(SOURCE_ID.findall(source_field)))
        if not source_ids:
            raise ValueError(f"{card['evidence_id']} has no source reference")
        unknown = [source_id for source_id in source_ids if source_id not in source_by_id]
        if unknown:
            raise ValueError(f"{card['evidence_id']} references unknown sources: {unknown}")

        summary = first_present(narratives, ("证据概述", "原始摘录（必要时用自己的话概述，并保留精确位置）"))
        interpretation = first_present(narratives, ("可支持的解释", "可形成的理解", "对话可用方式"))
        limitations = first_present(narratives, ("限制与用法", "限制", "反例、变化或限制"))
        if not summary or not interpretation or not limitations:
            raise ValueError(f"{card['evidence_id']} is missing summary, interpretation, or limitations")

        topics = split_topics(fields.get("主题标签", fields.get("主题", "")))
        card_number = int(card["evidence_id"].split("-")[1])
        source_refs: list[dict[str, Any]] = []
        for ordinal, source_id in enumerate(source_ids, start=1):
            source = source_by_id[source_id]
            chunk_id = f"{source_id}#C-{card_number * 10 + ordinal:04d}"
            location = clean(source.get("location_marker", "")) or clean(source_field) or "evidence-card summary"
            privacy = clean(source.get("privacy_level", "public"))
            if privacy not in {"public", "private", "sensitive"}:
                privacy = "private"
            rights_mode = "summary_only" if privacy == "public" else "local_only_text"
            chunk = {
                "schema_version": "1.0",
                "chunk_id": chunk_id,
                "source_id": source_id,
                "content_kind": clean(source.get("source_type", "source-summary")) or "source-summary",
                "content_mode": "summary",
                "text": summary,
                "speaker": clean(source.get("creator_or_speaker", "")) or None,
                "date": clean(source.get("first_published", "")) or None,
                "language": clean(source.get("language", "")) or "und",
                "location": location,
                "direct_quote": False,
                "topics": topics,
                "privacy_level": privacy,
                "rights_mode": rights_mode,
                "checksum": sha256_text(f"{source_id}\n{card['evidence_id']}\n{summary}"),
            }
            chunks.append(chunk)
            source_refs.append({"source_id": source_id, "locations": [location], "chunk_ids": [chunk_id]})

        evidence_records.append(
            {
                "schema_version": "1.0",
                "evidence_id": card["evidence_id"],
                "card_type": map_card_type(fields.get("类型", "")),
                "claim": card["claim"],
                "evidence_summary": summary,
                "interpretation": interpretation,
                "limitations": limitations,
                "source_refs": source_refs,
                "topics": topics,
                "confidence": map_confidence(fields),
                "status": "reviewed",
            }
        )

    chunks_path = processed / "chunks.jsonl"
    evidence_path = processed / "evidence.jsonl"
    context_path = processed / "context-memory.jsonl"
    chunks_path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in chunks), encoding="utf-8")
    evidence_path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in evidence_records), encoding="utf-8")
    context_path.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in context_records), encoding="utf-8")

    records: list[dict[str, Any]] = []
    for item in evidence_records:
        records.append(
            {
                "kind": "evidence",
                "id": item["evidence_id"],
                "title": item["claim"],
                "text": "\n".join((item["evidence_summary"], item["interpretation"], item["limitations"])),
                "topics": item["topics"],
                "source_ids": [ref["source_id"] for ref in item["source_refs"]],
            }
        )
    for item in chunks:
        records.append(
            {
                "kind": "chunk",
                "id": item["chunk_id"],
                "title": source_by_id[item["source_id"]].get("title", item["source_id"]),
                "text": item["text"],
                "topics": item["topics"],
                "source_ids": [item["source_id"]],
            }
        )
    for item in context_records:
        records.append(
            {
                "kind": "context",
                "id": item["memory_id"],
                "title": item["title"],
                "text": "\n".join((item["period"], item["situation"], item["observed_move"], item["tension"], item["dialogue_use"])),
                "topics": item["topics"],
                "source_ids": item["source_ids"],
            }
        )

    records_path = index_dir / "records.jsonl"
    records_path.write_text("".join(json.dumps({"row": row, **item}, ensure_ascii=False, separators=(",", ":")) + "\n" for row, item in enumerate(records)), encoding="utf-8")
    vectors = np.vstack([hashed_vector("\n".join((item["title"], item["text"], " ".join(item["topics"]))), dimensions) for item in records])
    np.save(index_dir / "vectors.npy", vectors, allow_pickle=False)

    sqlite_path = index_dir / "memory.sqlite3"
    if sqlite_path.exists():
        sqlite_path.unlink()
    connection = sqlite3.connect(sqlite_path)
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources (
          source_id TEXT PRIMARY KEY, title TEXT, creator TEXT, source_type TEXT,
          first_published TEXT, url TEXT, grade TEXT, rights TEXT, privacy TEXT
        );
        CREATE TABLE chunks (
          chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL,
          location TEXT, topics_json TEXT, checksum TEXT, FOREIGN KEY(source_id) REFERENCES sources(source_id)
        );
        CREATE TABLE evidence (
          evidence_id TEXT PRIMARY KEY, card_type TEXT, claim TEXT NOT NULL,
          evidence_summary TEXT, interpretation TEXT, limitations TEXT,
          source_refs_json TEXT, topics_json TEXT, confidence TEXT, status TEXT
        );
        CREATE TABLE context_memory (
          memory_id TEXT PRIMARY KEY, title TEXT NOT NULL, period TEXT,
          situation TEXT, observed_move TEXT, tension TEXT, dialogue_use TEXT,
          source_ids_json TEXT, topics_json TEXT, confidence TEXT, status TEXT
        );
        """
    )
    tokenizer = "trigram"
    try:
        connection.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(kind, record_id, title, text, topics, source_ids, tokenize='trigram')")
    except sqlite3.OperationalError:
        tokenizer = "unicode61"
        connection.execute("CREATE VIRTUAL TABLE memory_fts USING fts5(kind, record_id, title, text, topics, source_ids, tokenize='unicode61')")
    connection.executemany(
        "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                row["source_id"], row.get("title"), row.get("creator_or_speaker"), row.get("source_type"),
                row.get("first_published"), row.get("url_or_local_reference"), row.get("grade"),
                row.get("rights_or_permission"), row.get("privacy_level"),
            )
            for row in sources
        ],
    )
    connection.executemany(
        "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
        [(item["chunk_id"], item["source_id"], item["text"], item["location"], json.dumps(item["topics"], ensure_ascii=False), item["checksum"]) for item in chunks],
    )
    connection.executemany(
        "INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                item["evidence_id"], item["card_type"], item["claim"], item["evidence_summary"],
                item["interpretation"], item["limitations"], json.dumps(item["source_refs"], ensure_ascii=False),
                json.dumps(item["topics"], ensure_ascii=False), item["confidence"], item["status"],
            )
            for item in evidence_records
        ],
    )
    connection.executemany(
        "INSERT INTO context_memory VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                item["memory_id"], item["title"], item["period"], item["situation"],
                item["observed_move"], item["tension"], item["dialogue_use"],
                json.dumps(item["source_ids"], ensure_ascii=False),
                json.dumps(item["topics"], ensure_ascii=False), item["confidence"], item["status"],
            )
            for item in context_records
        ],
    )
    connection.executemany(
        "INSERT INTO memory_fts VALUES (?,?,?,?,?,?)",
        [(item["kind"], item["id"], item["title"], item["text"], " ".join(item["topics"]), " ".join(item["source_ids"])) for item in records],
    )
    metadata = {
        "schema_version": "1.0",
        "person_id": manifest["person_id"],
        "package_version": manifest["package_version"],
        "built_on": str(date.today()),
        "chunk_mode": "summary migration from reviewed Markdown evidence",
        "fts_tokenizer": tokenizer,
        "vector_method": "hashed-char-ngram-v1",
        "vector_dimensions": str(dimensions),
    }
    connection.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
    connection.commit()
    connection.close()

    index_manifest = {
        "schema_version": "1.0",
        "person_id": manifest["person_id"],
        "package_version": manifest["package_version"],
        "built_on": str(date.today()),
        "canonical_inputs": {
            "sources": "../../01-sources.csv",
            "evidence_review": "../../02-evidence.md",
            "chunks": "../chunks.jsonl",
            "evidence": "../evidence.jsonl",
            "context_review": "../../08-context-memory.md",
            "context_memory": "../context-memory.jsonl",
        },
        "records": {"sources": len(sources), "chunks": len(chunks), "evidence": len(evidence_records), "context_memory": len(context_records), "search_rows": len(records)},
        "full_text_index": {"path": "memory.sqlite3", "engine": "SQLite FTS5", "tokenizer": tokenizer},
        "vector_index": {
            "path": "vectors.npy",
            "records": "records.jsonl",
            "method": "hashed-char-ngram-v1",
            "dimensions": dimensions,
            "dtype": "float32",
            "normalized": True,
            "semantic_embeddings": False,
            "note": "Deterministic offline lexical vectors; rebuild with an embedding provider for semantic retrieval.",
        },
        "checksums": {
            "sources_sha256": sha256_text((person_dir / "01-sources.csv").read_text(encoding="utf-8-sig")),
            "evidence_markdown_sha256": sha256_text((person_dir / "02-evidence.md").read_text(encoding="utf-8")),
            "chunks_jsonl_sha256": sha256_text(chunks_path.read_text(encoding="utf-8")),
            "evidence_jsonl_sha256": sha256_text(evidence_path.read_text(encoding="utf-8")),
            "context_markdown_sha256": sha256_text((person_dir / "08-context-memory.md").read_text(encoding="utf-8")) if (person_dir / "08-context-memory.md").exists() else None,
            "context_jsonl_sha256": sha256_text(context_path.read_text(encoding="utf-8")),
        },
    }
    (index_dir / "index-manifest.json").write_text(json.dumps(index_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_dirs", nargs="+", type=Path)
    parser.add_argument("--dimensions", type=int, default=4096)
    args = parser.parse_args()
    for person_dir in args.person_dirs:
        result = build(person_dir.resolve(), args.dimensions)
        print(json.dumps({"person_id": result["person_id"], **result["records"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
