#!/usr/bin/env python3
"""Prepare a historical-public lecture brief and a separate supplied fact brief."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
from resolve_persona import checked_file, read_json, registry, require_legacy_manifest

NOTICE = "AI 人物视角模拟演讲，不是历史原话或本人真实表态。"


def text(value, label, maximum=3000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid " + label)
    return value.strip()


def facts(value):
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("At most 50 supplied facts")
    result, seen = [], set()
    for row in value:
        keys = {"fact_id", "statement", "source_title", "source_url", "checked_on", "limitations"}
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError("Fact fields must identify statement, source, date and limitations")
        item = {key: text(row[key], key) for key in keys}
        if not re.fullmatch(r"F-[0-9]{4,}", item["fact_id"]) or item["fact_id"] in seen:
            raise ValueError("Fact IDs must be unique")
        source = urlsplit(item["source_url"])
        if source.scheme not in {"https", "http"} or not source.hostname or source.username or source.password:
            raise ValueError("Supply a public source URL without credentials")
        date.fromisoformat(item["checked_on"])
        seen.add(item["fact_id"])
        result.append(item)
    return result


def prepare(config, *, root=ROOT, preview=False):
    root = Path(root).resolve()
    allowed = {"person_id", "topic", "audience", "goal", "minutes", "as_of", "facts"}
    if not isinstance(config, dict) or set(config) != allowed:
        raise ValueError("Expected person, topic, audience, goal, minutes, as_of and facts")
    person = text(config["person_id"], "person", 64)
    if registry(root).get(person) != person:
        raise ValueError("Unknown historical public persona")
    package = root / "personas" / person
    manifest_file = checked_file(package, "manifest.json")
    manifest = read_json(manifest_file)
    require_legacy_manifest(manifest)
    if manifest.get("subject_kind") != "historical_public" or manifest.get("person_id") != person:
        raise ValueError("Only historical public personas supported")
    ready = (manifest.get("visibility") == "public" and manifest.get("readiness") in {"chat-ready", "simulation-ready", "publish-ready"}
             and manifest.get("capabilities", {}).get("themed_lecture") is True)
    if not ready and not preview:
        raise ValueError("Explicit historical draft preview required")
    minutes = config["minutes"]
    if type(minutes) is not int or not 1 <= minutes <= 60:
        raise ValueError("Minutes must be between 1 and 60")
    as_of = text(config["as_of"], "as_of", 10)
    date.fromisoformat(as_of)
    supplied = facts(config["facts"])
    if any(row["checked_on"] > as_of for row in supplied):
        raise ValueError("Fact check date exceeds lecture research date")
    pins = {"manifest.json": hashlib.sha256(manifest_file.read_bytes()).hexdigest()}
    for role in ("persona_spec", "core_anchors", "dialogue_examples", "structured_evidence", "sources"):
        relative = manifest["files"][role]
        pins[relative] = hashlib.sha256(checked_file(package, relative).read_bytes()).hexdigest()
    brief = {"schema_version": "1.0", "session_id": f"SP-{date.today():%Y%m%d}-{uuid.uuid4().hex[:12]}",
             "status": "prepared", "person_id": person, "display_name": manifest["display_name"],
             "package_version": manifest["package_version"], "preview": not ready,
             **{key: text(config[key], key) for key in ("topic", "audience", "goal")},
             "minutes": minutes, "as_of": as_of, "simulation_notice": NOTICE,
             "facts": supplied, "fact_verification": "supplied_not_independently_verified",
             "network_policy": "off", "pins": pins, "semantic_quality_verified": False}
    parent = root / "定题演讲/sessions"
    path = parent / brief["session_id"]
    if not path.resolve().is_relative_to(root) or parent.is_symlink():
        raise ValueError("Lecture output must remain inside repository")
    path.mkdir(parents=True, exist_ok=False)
    # input.json is the last-written preparation marker; incomplete directories cannot be used as prepared sessions.
    brief_md = f"# {brief['topic']}\n\n{NOTICE}\n\n人物：{brief['display_name']}\n\n听众：{brief['audience']}\n\n目标：{brief['goal']}\n\n目标时长：{minutes} 分钟（未口读验证）\n\n研究时间点：{as_of}\n"
    research = "# 本场事实简报\n\n与人物记忆分开保存；以下为提供的来源陈述，程序尚未独立核验。\n\n"
    if not supplied:
        research += "没有提供当代事实。后续生成不得把缺失事实补写为已核实结论。\n"
    for row in supplied:
        research += f"## {row['fact_id']}\n\n{row['statement']}\n\n来源：{row['source_title']} — {row['source_url']}\n\n核查日期（提供者记录）：{row['checked_on']}\n\n限制：{row['limitations']}\n\n"
    (path / "00-brief.md").write_text(brief_md, encoding="utf-8")
    (path / "01-research.md").write_text(research, encoding="utf-8")
    (path / "input.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return brief


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args(argv)
    try:
        brief = prepare(read_json(args.config), preview=args.preview)
        print(json.dumps({"session_id": brief["session_id"], "status": brief["status"]}))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print("Lecture preparation failed; check public persona and brief fields", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
