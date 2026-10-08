#!/usr/bin/env python3
"""Read-only repository validation. Requires requirements-dev.txt."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

ROOT = Path(__file__).resolve().parents[1]


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def schema_errors(value, schema: dict, label: str) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{label}:{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in sorted(validator.iter_errors(value), key=lambda e: str(e.absolute_path))]


def validate(root: Path = ROOT) -> dict:
    root = root.resolve()
    errors: list[str] = []
    packages: list[dict] = []
    schemas = {}
    for directory in ("人物蒸馏", "圆桌会议"):
        for path in sorted((root / directory / "schemas").glob("*.schema.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(data)
                schemas[path.name] = data
            except (OSError, ValueError, SchemaError) as error:
                errors.append(f"{path.relative_to(root)}: {error}")
    if not schemas:
        return {"valid": False, "packages": [], "errors": errors + ["No schemas found"]}
    try:
        entry = module("repository_entry", root / "直接对话/scripts/resolve_persona.py")
        memory = module("repository_memory", root / "人物蒸馏/scripts/validate_structured_memory.py")
        ids = sorted(set(entry.registry(root).values()))
    except (OSError, ValueError) as error:
        return {"valid": False, "packages": [], "errors": errors + [str(error)]}
    for person_id in ids:
        package = root / "personas" / person_id
        try:
            manifest = entry.read_json(entry.checked_file(package, "manifest.json"))
            errors.extend(schema_errors(manifest, schemas["persona-package.schema.json"], f"{person_id}/manifest"))
            for role, relative in manifest["files"].items():
                entry.checked_file(package, relative)
            # Test the historical-public loading boundary without granting any
            # persistent preview authorization or changing package readiness.
            entry.resolve_persona(person_id, root, preview=True)
            job = entry.read_json(entry.checked_file(package, "distillation-job.json"))
            errors.extend(schema_errors(job, schemas["distillation-job.schema.json"], f"{person_id}/job"))
            if job["subject"]["person_id"] != person_id:
                errors.append(f"{person_id}: distillation job identity mismatch")
            result = memory.validate(package)
            packages.append(result)
            errors.extend(f"{person_id}: {message}" for message in result["errors"])
            chunks = memory.load_jsonl(package / manifest["files"]["structured_chunks"])
            chunk_sources = {item["chunk_id"]: item["source_id"] for item in chunks}
            evidence = memory.load_jsonl(package / manifest["files"]["structured_evidence"])
            chunk_cards = {chunk_id: item["evidence_id"] for item in evidence
                           for ref in item.get("source_refs", []) for chunk_id in ref.get("chunk_ids", [])}
            for item in chunks:
                errors.extend(schema_errors(item, schemas["normalized-chunk.schema.json"], f"{person_id}/{item.get('chunk_id')}"))
                # The existing Markdown migrator fingerprints source + card +
                # summary, rather than text alone. Keep its data contract.
                fingerprint = f"{item['source_id']}\n{chunk_cards.get(item['chunk_id'], '')}\n{item['text']}"
                digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
                if digest != item["checksum"]:
                    errors.append(f"{person_id}/{item['chunk_id']}: chunk checksum mismatch")
            for role, schema_name in (("structured_evidence", "evidence-card"),
                                      ("structured_context_memory", "context-memory")):
                for item in memory.load_jsonl(package / manifest["files"][role]):
                    label = item.get("evidence_id", item.get("memory_id", "record"))
                    errors.extend(schema_errors(item, schemas[f"{schema_name}.schema.json"], f"{person_id}/{label}"))
                    for ref in item.get("source_refs", []):
                        for chunk_id in ref.get("chunk_ids", []):
                            if chunk_sources.get(chunk_id) != ref.get("source_id"):
                                errors.append(f"{person_id}/{label}: chunk/source mismatch {chunk_id}")
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            errors.append(f"{person_id}: {error}")
    # Validate actual distributable examples, not placeholder persona manifests.
    examples = [(p, "distillation-job.schema.json") for p in
                sorted((root / "人物蒸馏/examples").glob("*.json"))]
    examples.extend((p, "distillation-job-v2.schema.json") for p in
                    sorted((root / "人物蒸馏/examples/v2").glob("*.json")))
    for path in sorted((root / "圆桌会议/_template").glob("*.example.json")):
        examples.append((path, path.name.replace(".example.json", ".schema.json")))
    examples.extend((path, "session.schema.json") for path in sorted((root / "圆桌会议/examples").glob("*.json")))
    for path, schema_name in examples:
        try:
            errors.extend(schema_errors(json.loads(path.read_text(encoding="utf-8")),
                                        schemas[schema_name], str(path.relative_to(root))))
        except (OSError, ValueError, KeyError) as error:
            errors.append(f"{path.relative_to(root)}: {error}")
    return {"valid": not errors, "schemas": len(schemas), "packages": packages,
            "examples": len(examples), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = validate(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
