#!/usr/bin/env python3
"""Resolve an explicit persona selection into a read-only Agent loading plan."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
PERSON_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
READY = {"chat-ready", "simulation-ready", "publish-ready"}
ROLES = (
    "persona_spec", "core_anchors", "dialogue_examples", "profile",
    "evidence", "context_memory", "character_card",
)
OPTIONAL_ROLES = {"core_anchors", "dialogue_examples", "context_memory"}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def checked_file(base: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing file path")
    parts = PurePosixPath(relative).parts
    if (PureWindowsPath(relative).drive or "\\" in relative
            or PurePosixPath(relative).is_absolute() or ".." in parts
            or any(part.startswith(".") or part in {"raw", "research.local"} for part in parts)):
        raise ValueError(f"Unsupported loading path: {relative}")
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError(f"Missing file or path outside package: {relative}")
    # Resolve symlinks before deciding whether a file belongs to the public package.
    resolved_parts = path.relative_to(base.resolve()).parts
    if any(part.startswith(".") or part in {"raw", "research.local"} for part in resolved_parts):
        raise ValueError(f"Protected loading path: {relative}")
    return path


def registry(root: Path) -> dict[str, str]:
    persons_dir = root / "personas"
    ids = {
        path.name for path in persons_dir.iterdir()
        if path.is_dir() and PERSON_ID.fullmatch(path.name)
        and (path / "manifest.json").is_file()
    }
    data = read_json(root / "直接对话" / "persona-aliases.json")
    if data.get("schema_version") != "1.0" or not isinstance(data.get("aliases"), dict):
        raise ValueError("Invalid alias registry")
    result = {person_id: person_id for person_id in ids}
    for alias, person_id in data["aliases"].items():
        if not isinstance(person_id, str) or person_id not in ids:
            raise ValueError(f"Alias references a missing persona: {alias}")
        if not alias.strip() or alias != alias.strip():
            raise ValueError("Empty or padded alias")
        key = alias.casefold()
        if key in result and result[key] != person_id:
            raise ValueError(f"Ambiguous alias: {alias}")
        result[key] = person_id
    return result


def local_preview_ids(root: Path) -> list[str]:
    path = root / ".persona" / "preview.local.json"
    if not path.exists():
        return []
    data = read_json(path)
    ids = data.get("person_ids")
    if (data.get("schema_version") != "1.0" or data.get("scope") != "local_read_only"
            or not isinstance(ids, list)
            or any(not isinstance(item, str) or not PERSON_ID.fullmatch(item) for item in ids)):
        raise ValueError("Invalid local preview configuration")
    return ids


def resolve_persona(name: str, root: Path = ROOT, preview: bool = False) -> dict:
    root = root.resolve()
    person_id = registry(root).get(name.strip().casefold())
    if person_id is None:
        raise ValueError(f"Unknown persona: {name}")
    package = root / "personas" / person_id
    if package.is_symlink() or not package.resolve().is_relative_to(root / "personas"):
        raise ValueError("Persona package must stay inside personas/")
    manifest_path = checked_file(package, "manifest.json")
    manifest = read_json(manifest_path)
    if manifest.get("person_id") != person_id:
        raise ValueError("Manifest identity does not match directory")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("direct_chat") is not True:
        raise ValueError("Direct chat is disabled")
    readiness, visibility = manifest.get("readiness"), manifest.get("visibility")
    if readiness not in READY | {"draft"} or visibility not in {"private", "team", "public"}:
        raise ValueError("Invalid readiness or visibility")
    public = readiness in READY and visibility == "public"
    if not public:
        if manifest.get("subject_kind") != "historical_public":
            raise ValueError("Shortcut preview only supports historical public personas")
        if not preview and person_id not in local_preview_ids(root):
            raise ValueError("Local draft/private preview requires existing user authorization")

    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("Missing manifest file map")
    module = root / "直接对话"
    paths = [manifest_path, checked_file(module, "prompts/agent.md")]
    for role in ROLES:
        if role in OPTIONAL_ROLES and role not in files:
            continue
        paths.append(checked_file(package, files.get(role)))
    adapter = f"personas/{person_id}.md"
    if (module / adapter).exists() or (module / adapter).is_symlink():
        paths.append(checked_file(module, adapter))
    return {
        "ok": True,
        "person_id": person_id,
        "display_name": manifest.get("display_name", person_id),
        "package_version": manifest.get("package_version"),
        "readiness": readiness,
        "visibility": visibility,
        "mode": "public" if public else "local_preview",
        "read_only": True,
        "load_files": [path.relative_to(root).as_posix() for path in paths],
    }


def list_personas(root: Path = ROOT) -> list[dict]:
    entries = []
    for person_id in sorted(set(registry(root).values())):
        manifest = read_json(checked_file(root / "personas" / person_id, "manifest.json"))
        entries.append({key: manifest.get(key) for key in (
            "person_id", "display_name", "package_version", "readiness", "visibility",
        )})
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", help="Exact alias or stable person ID")
    parser.add_argument("--list", action="store_true", help="List personas without loading memory")
    parser.add_argument("--preview", action="store_true", help="Use existing session preview authorization")
    args = parser.parse_args()
    if args.list and (args.name or args.preview):
        parser.error("--list cannot be combined with a persona or --preview")
    if not args.list and not args.name:
        parser.error("Provide a persona name or --list")
    try:
        result = {"ok": True, "personas": list_personas()} if args.list else resolve_persona(args.name, preview=args.preview)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
