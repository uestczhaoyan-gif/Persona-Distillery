#!/usr/bin/env python3
"""Plan or explicitly apply a local-only v1 -> v2 migration. No model calls."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys
import uuid

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "人物蒸馏/schemas"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_policy import require_legacy_manifest

SYNC_NAMES = {"onedrive", "dropbox", "google drive", "googledrive", "icloud drive", "box sync"}
OMITTED_ROLES = {"dialogue_log", "usage_example"}


def read_json(path: Path) -> dict:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JSON metadata exceeds size limit")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON metadata must be an object")
    return value


def validate(value: dict, schema_name: str) -> None:
    schema = read_json(SCHEMAS / schema_name)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        # Do not echo a malformed value, which may contain private text.
        raise ValueError(f"Invalid {schema_name}: {errors[0].validator} constraint")


def plain_path(path: Path) -> Path:
    if ".." in path.parts or str(path).startswith(("//", "\\\\")):
        raise ValueError("Parent traversal and network storage are not supported")
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise ValueError("Network drives are not supported for local storage")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Symlink/junction/reparse storage is not supported")
    return path.resolve()


def overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def check_private_location(path: Path, workspace: Path = ROOT) -> Path:
    path = plain_path(path)
    if path == Path(path.anchor) or overlap(path, workspace.resolve()):
        raise ValueError("Private library must be separate from the project workspace")
    for part in (path, *path.parents):
        if (part / ".git").exists() or (part / ".git").is_symlink() or part.name.casefold() == ".git":
            raise ValueError("Private library cannot be inside a Git repository")
        name = part.name.casefold()
        if name in SYNC_NAMES or name.startswith("onedrive -"):
            raise ValueError("Known cloud-synced storage is not allowed")
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial", "DROPBOX_ROOT"):
        configured = os.environ.get(variable)
        if configured and path.is_relative_to(Path(configured).resolve()):
            raise ValueError("Configured cloud-synced storage is not allowed")
    return path


def package_file(package: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Missing package file path")
    parts = PurePosixPath(relative).parts
    if (relative != PurePosixPath(relative).as_posix()
            or PureWindowsPath(relative).drive or PurePosixPath(relative).is_absolute()
            or "\\" in relative or "//" in relative
            or any(p in {"..", "raw", "research.local", "sessions"} or p.startswith(".")
                   or ":" in p or p.endswith((" ", ".")) for p in parts)):
        raise ValueError("Unsafe or protected package file path")
    path = plain_path(package / relative)
    if not path.is_relative_to(package) or not path.is_file():
        raise ValueError("Declared package file is missing or outside package")
    return path


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def needs_private_source(policy: dict) -> bool:
    return (policy["subject_kind"] in {"living_public", "living_private", "deceased_private"}
            or policy["sensitive_data"]
            or bool(set(policy["material_basis"]) & {"authorized_private", "user_recollection"}))


def plan_migration(source: Path, library: Path, policy: dict, workspace: Path = ROOT,
                   *, allow_incomplete: bool = False) -> dict:
    validate(policy, "persona-policy.schema.json")
    if (policy["execution_policy"]["mode"] != "local_only"
            or policy["distribution_policy"]["mode"] != "local_only"):
        raise ValueError("Initial migration supports local_only execution and distribution")
    source = plain_path(source)
    library = check_private_location(library, workspace)
    if overlap(source, library):
        raise ValueError("Source and destination library must not overlap")
    if needs_private_source(policy):
        check_private_location(source, workspace)
    manifest_file = package_file(source, "manifest.json")
    manifest = read_json(manifest_file)
    require_legacy_manifest(manifest)
    validate(manifest, "persona-package.schema.json")
    if manifest["subject_kind"] != policy["subject_kind"]:
        raise ValueError("Policy subject kind must match the source identity")
    target = check_private_location(library / manifest["person_id"], workspace)
    if target.exists():
        marker = target / ".migration-incomplete.json"
        if not allow_incomplete or not plain_path(marker).is_file():
            raise ValueError("Destination already exists; migration never overwrites a package")
    proposed = copy.deepcopy(manifest)
    proposed.update(copy.deepcopy(policy))
    proposed.update(schema_version="2.0", visibility="private")
    proposed["$schema"] = "https://example.invalid/persona-distill/schemas/persona-package-v2.schema.json"
    proposed["files"].pop("usage_example", None)
    proposed["files"]["dialogue_log"] = "05-dialogue-log.md"
    validate(proposed, "persona-package-v2.schema.json")
    selected = {}
    for role, relative in manifest["files"].items():
        if role not in OMITTED_ROLES:
            selected[relative] = package_file(source, relative)
    # These are the only transitive files that the current index declares.
    index_relative = manifest["files"].get("search_index_manifest")
    if index_relative:
        index = read_json(selected[index_relative])
        for section, key in (("full_text_index", "path"), ("vector_index", "path"), ("vector_index", "records")):
            block = index.get(section, {})
            if not isinstance(block, dict):
                raise ValueError("Invalid index dependency block")
            reference = block.get(key)
            if reference is not None:
                # Resolve relative to the index, but validate against package boundaries.
                if not isinstance(reference, str) or not reference or PurePosixPath(reference).is_absolute() or PureWindowsPath(reference).drive:
                    raise ValueError("Unsafe index dependency")
                relative = (PurePosixPath(index_relative).parent / reference).as_posix()
                selected[relative] = package_file(source, relative)
    names = [name.casefold() for name in selected]
    if len(set(names)) != len(names) or any(n in {"manifest.json", "manifest.v2.json", "05-dialogue-log.md", "migration-report.json"} for n in names):
        raise ValueError("Copy paths collide with generated migration files")
    inventory = [{"path": name, "bytes": path.stat().st_size, "sha256": sha(path)}
                 for name, path in sorted(selected.items())]
    return {"plan_version": "1.0", "dry_run": True, "source": str(source), "target": str(target),
            "source_manifest_sha256": sha(manifest_file), "proposed_manifest": proposed,
            "copy_files": inventory, "generated_files": ["manifest.v2.json", "05-dialogue-log.md", "migration-report.json"],
            "omitted_roles": sorted(OMITTED_ROLES & manifest["files"].keys()),
            "storage_notice": "Known sync paths checked; confirm no other backup/sync service monitors this location."}


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(canonical(plan).encode("utf-8")).hexdigest()


def write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def copy_verified(source: Path, target: Path, record: dict) -> None:
    """Never overwrite; a failed copy stays incomplete and is not loadable."""
    target.parent.mkdir(parents=True, exist_ok=True)
    plain_path(target)
    digest = hashlib.sha256()
    count = 0
    with source.open("rb") as inp, target.open("xb") as out:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            count += len(block)
            if count > record["bytes"]:
                raise ValueError("Source changed while copying")
            out.write(block)
            digest.update(block)
        out.flush()
        os.fsync(out.fileno())
    if count != record["bytes"] or digest.hexdigest() != record["sha256"]:
        raise ValueError("Source changed while copying")


def apply_migration(source: Path, library: Path, policy: dict, expected_digest: str,
                    confirm_local_storage: bool, workspace: Path = ROOT, *, resume: bool = False) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Explicit local storage confirmation is required")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("Provide the plan_sha256 from a successful dry-run")
    plan = plan_migration(source, library, policy, workspace, allow_incomplete=resume)
    if plan_digest(plan) != expected_digest:
        raise ValueError("Migration plan changed; inspect a new dry-run before applying")
    if resume and not Path(plan["target"]).exists():
        raise ValueError("No incomplete migration exists to resume")
    library = check_private_location(library, workspace)
    library.mkdir(parents=True, exist_ok=True)
    check_private_location(library, workspace)
    lock = library / ".migration.lock"
    token = canonical({"token": uuid.uuid4().hex, "pid": os.getpid(),
                       "created_at": datetime.now(timezone.utc).isoformat()}).encode("utf-8")
    try:
        write_new(lock, token)
    except FileExistsError:
        raise ValueError("Migration is locked; inspect the active process before recovery") from None
    try:
        # Recheck after obtaining the lock; a concurrent run may have finished.
        plan = plan_migration(source, library, policy, workspace, allow_incomplete=resume)
        if plan_digest(plan) != expected_digest:
            raise ValueError("Migration plan changed before writing")
        source = Path(plan["source"])
        target = check_private_location(Path(plan["target"]), workspace)
        marker = target / ".migration-incomplete.json"
        marker_data = {"plan_sha256": expected_digest, "state": "incomplete"}
        if resume:
            if not target.exists() or read_json(plain_path(marker)) != marker_data:
                raise ValueError("Incomplete migration marker does not match this plan")
        else:
            target.mkdir(exist_ok=False)
            write_new(marker, canonical(marker_data).encode("utf-8"))
        allowed = {item["path"] for item in plan["copy_files"]} | set(plan["generated_files"]) | {marker.name}
        for path in target.rglob("*"):
            plain_path(path)
            if path.is_file() and path.relative_to(target).as_posix() not in allowed:
                raise ValueError("Incomplete destination contains unexpected files; left untouched")
        for record in plan["copy_files"]:
            destination = plain_path(target / record["path"])
            if destination.exists():
                if not destination.is_file() or sha(destination) != record["sha256"]:
                    raise ValueError("Incomplete destination file differs; left untouched")
            else:
                copy_verified(package_file(source, record["path"]), destination, record)
        # Re-hash the source to catch edits made during a long copy.
        current = plan_migration(source, library, policy, workspace, allow_incomplete=True)
        if plan_digest(current) != expected_digest:
            raise ValueError("Source changed during migration; destination remains incomplete")
        generated = {
            "05-dialogue-log.md": "# 本地对话日志\n\n迁移未复制旧会话。默认不保存完整对话。\n",
            "migration-report.json": canonical({"plan_sha256": expected_digest,
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "copy_files": plan["copy_files"], "omitted_roles": plan["omitted_roles"],
                "source_preserved": True}) + "\n",
            "manifest.v2.json": json.dumps(plan["proposed_manifest"], ensure_ascii=False, indent=2) + "\n",
        }
        for name, text in generated.items():
            destination = plain_path(target / name)
            data = text.encode("utf-8")
            if destination.exists():
                if destination.read_bytes() != data:
                    raise ValueError("Generated destination metadata differs; left untouched")
            else:
                write_new(destination, data)
        # All future v2 readers must reject this marker, even if manifest exists.
        if read_json(plain_path(marker)) != marker_data:
            raise ValueError("Migration marker changed; refusing to complete")
        marker.unlink()
        return {"ok": True, "dry_run": False, "plan_sha256": expected_digest,
                "copy_file_count": len(plan["copy_files"]), "source_preserved": True,
                "status": "local_package_created", "runtime_available": False}
    finally:
        if plain_path(lock).is_file() and lock.read_bytes() == token:
            lock.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--show-plan", action="store_true", help="Show local metadata, including identity and paths; never share private output")
    parser.add_argument("--apply", action="store_true", help="Explicitly create the local package after dry-run")
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument("--confirm-local-storage", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Resume only an unchanged, incomplete migration")
    args = parser.parse_args()
    try:
        policy = read_json(plain_path(args.policy))
        if args.apply:
            if args.show_plan:
                raise ValueError("--show-plan is only available for dry-run")
            result = apply_migration(args.source, args.library, policy, args.expect_plan_sha256,
                                     args.confirm_local_storage, resume=args.resume)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.resume or args.confirm_local_storage or args.expect_plan_sha256:
            raise ValueError("Apply options require --apply")
        result = plan_migration(args.source, args.library, policy)
        output = result if args.show_plan else {"ok": True, "dry_run": True,
            "plan_sha256": plan_digest(result),
            "copy_file_count": len(result["copy_files"]), "generated_files": result["generated_files"],
            "omitted_roles": result["omitted_roles"], "storage_notice": result["storage_notice"]}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as error:
        message = str(error) if isinstance(error, ValueError) else "Filesystem or metadata unavailable; inspect locally"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
