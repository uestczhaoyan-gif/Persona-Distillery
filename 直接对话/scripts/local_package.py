"""Read-only v2 context for trusted local adapters; never a context-export CLI.

This module does no network I/O. Consumers must authorize their actual provider
before calling it and must not print, upload or persist the returned context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "人物蒸馏/scripts"))
from migrate_persona import check_private_location, package_file, plain_path
from package_policy import ExecutionTarget, authorize_operation, policy_from_manifest

CONTEXT_ROLES = ("profile", "character_card", "persona_spec", "core_anchors",
                 "dialogue_examples", "context_memory", "sources", "evidence")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CONTEXT_CHARS = 1000000


@dataclass(frozen=True)
class LocalContext:
    # Avoid accidental disclosure when a caller logs the object representation.
    person_id: str = field(repr=False)
    context: str = field(repr=False)
    policy_json: str = field(repr=False)
    manifest_sha256: str
    file_hashes: tuple[tuple[str, str], ...] = field(repr=False)


def _complete_package(package: Path, workspace: Path) -> Path:
    package = check_private_location(package, workspace)
    if not package.is_dir():
        raise ValueError("Local package directory is missing")
    for path in (package / ".migration-incomplete.json", package.parent / ".migration.lock",
                 package / "manifest.json"):
        if path.exists() or path.is_symlink():
            raise ValueError("Incomplete, locked or legacy-aliased package cannot be loaded")
    return package


def _bounded_bytes(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError("Local context exceeds size budget; no partial context was loaded")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Local context exceeds size budget; no partial context was loaded")
    return data


def load_local_context(package: Path, *, confirm_local_storage: bool,
                       preview: bool = False, max_chars: int = 100000,
                       workspace: Path = ROOT) -> LocalContext:
    """Validate metadata first, then load bounded text into local process memory.

    Draft or disabled chat capability requires explicit builder preview. All six
    subject kinds share this rule. No historical-person whitelist is used here.
    """
    if confirm_local_storage is not True:
        raise ValueError("Explicit confirmation of non-synced local storage is required")
    if type(preview) is not bool or type(max_chars) is not int or not 1 <= max_chars <= MAX_CONTEXT_CHARS:
        raise ValueError("Invalid preview flag or context budget")
    package = _complete_package(Path(package), workspace)
    header_path = package_file(package, "manifest.v2.json")
    header = _bounded_bytes(header_path, MAX_METADATA_BYTES)
    try:
        manifest = json.loads(header.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ValueError("Invalid local manifest encoding or JSON") from None
    policy = policy_from_manifest(manifest)
    if (policy["execution_policy"]["mode"] != "local_only"
            or policy["distribution_policy"]["mode"] != "local_only"
            or manifest["visibility"] != "private"):
        raise ValueError("This reader only supports private local-only packages")
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    if package.name != manifest["person_id"]:
        raise ValueError("Package directory does not match the stable identity")
    if not preview and (manifest["readiness"] == "draft" or not manifest["capabilities"]["direct_chat"]):
        raise ValueError("Explicit builder preview is required for this package")

    # Validate every declared path before opening any body, including files not
    # used as dialogue memory. Logs, examples of usage and raw inputs stay out.
    paths = {role: package_file(package, relative) for role, relative in manifest["files"].items()}
    selected = [(role, paths[role]) for role in CONTEXT_ROLES if role in paths]
    if any(path == header_path for path in paths.values()):
        raise ValueError("Manifest cannot be used as a content file")
    if len({str(path).casefold() for _, path in selected}) != len(selected):
        raise ValueError("Context roles must refer to distinct files")
    sections, hashes = [], []
    remaining = max_chars
    for role, path in selected:
        # UTF-8 uses at most four bytes per code point, plus an optional BOM.
        data = _bounded_bytes(plain_path(path), remaining * 4 + 3)
        try:
            body = data.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("Local context file is not valid UTF-8") from None
        section = f"## {role}\n{body}"
        remaining -= len(section) + (2 if sections else 0)
        if remaining < 0:
            raise ValueError("Local context exceeds character budget; no partial context was loaded")
        sections.append(section)
        hashes.append((manifest["files"][role], hashlib.sha256(data).hexdigest()))

    # Detect ordinary edits/migration state changes during loading. This is not
    # a defence against a hostile local process with permission to alter files.
    _complete_package(package, workspace)
    if _bounded_bytes(package_file(package, "manifest.v2.json"), MAX_METADATA_BYTES) != header:
        raise ValueError("Manifest changed during loading; retry after local review")
    for relative, digest in hashes:
        data = _bounded_bytes(package_file(package, relative), max_chars * 4 + 3)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Context changed during loading; retry after local review")
    return LocalContext(manifest["person_id"], "\n\n".join(sections),
                        json.dumps(policy, ensure_ascii=False, sort_keys=True),
                        hashlib.sha256(header).hexdigest(), tuple(hashes))
