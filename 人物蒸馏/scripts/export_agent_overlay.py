#!/usr/bin/env python3
"""Export only explicitly allowed local summaries for a remote Agent session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def export(person_dir: Path) -> None:
    local = person_dir / "research.local"
    overlay = json.loads((local / "overlay.json").read_text(encoding="utf-8"))
    remote = overlay["remote_agent"]
    if not remote["enabled"]:
        raise ValueError(f"Remote Agent overlay is disabled for {person_dir.name}")
    confirmation = overlay.get("operator_confirmation", {})
    if not confirmation.get("rights_reviewed") or not confirmation.get("privacy_reviewed"):
        raise ValueError(f"Rights and privacy review are not confirmed for {person_dir.name}")
    if remote.get("raw_text_allowed") is not False:
        raise ValueError("raw_text_allowed must remain false")
    allowed = set(remote.get("allowed_channels", [])) & {"public", "agent_summary"}
    output: list[dict] = []
    for relative in (overlay["paths"]["chunks"], overlay["paths"]["evidence"]):
        for record in read_jsonl(local / relative):
            if record.get("delivery_channel") in allowed:
                if record.get("content_mode") in {"full_text", "excerpt"} or record.get("direct_quote"):
                    raise ValueError(f"Remote export rejected text-like record {record.get('chunk_id') or record.get('evidence_id')}")
                output.append(record)
    runtime = person_dir / ".runtime.local"
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "agent-memory.jsonl"
    target.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in output), encoding="utf-8")
    print(json.dumps({"person_id": overlay["person_id"], "exported": len(output), "path": str(target)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("person_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for person_dir in args.person_dirs:
        export(person_dir.resolve())


if __name__ == "__main__":
    main()
