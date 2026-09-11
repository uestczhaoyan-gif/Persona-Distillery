#!/usr/bin/env python3
"""Initialize a Git-ignored, rights-sensitive research overlay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SOURCE_HEADER = (
    "source_id,title,creator,access_basis,terms_reference,local_copy_allowed,"
    "remote_processing,publish_policy,quote_limit,privacy_level,retention,reviewed_by,reviewed_on\n"
)


def initialize(person_dir: Path) -> None:
    manifest = json.loads((person_dir / "manifest.json").read_text(encoding="utf-8"))
    local = person_dir / "research.local"
    (local / "raw").mkdir(parents=True, exist_ok=True)
    (local / "processed").mkdir(parents=True, exist_ok=True)
    (local / "index").mkdir(parents=True, exist_ok=True)
    overlay = {
        "$schema": "../../../人物蒸馏/schemas/local-research-overlay.schema.json",
        "schema_version": "1.0",
        "person_id": manifest["person_id"],
        "default_channel": "local_only",
        "remote_agent": {
            "enabled": False,
            "allowed_channels": ["public"],
            "raw_text_allowed": False,
            "provider_note": "Enable only after a per-source rights and privacy review.",
        },
        "paths": {
            "sources": "sources.local.csv",
            "raw": "raw/",
            "chunks": "processed/chunks.local.jsonl",
            "evidence": "processed/evidence.local.jsonl",
        },
        "operator_confirmation": {
            "rights_reviewed": False,
            "privacy_reviewed": False,
            "reviewed_on": None,
        },
    }
    files = {
        local / "overlay.json": json.dumps(overlay, ensure_ascii=False, indent=2) + "\n",
        local / "sources.local.csv": SOURCE_HEADER,
        local / "processed" / "chunks.local.jsonl": "",
        local / "processed" / "evidence.local.jsonl": "",
    }
    for path, content in files.items():
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    print(json.dumps({"person_id": manifest["person_id"], "overlay": str(local)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for person_dir in args.person_dirs:
        initialize(person_dir.resolve())


if __name__ == "__main__":
    main()
