#!/usr/bin/env python3
"""Preview or explicitly delete a reviewed local-only package. No recursive delete."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import uuid

from backup_persona import inventory
from migrate_persona import (ROOT, check_private_location, package_file, plan_digest,
                            plain_path, read_json, sha, write_new)
from package_policy import ExecutionTarget, authorize_operation, policy_from_manifest

NOT_COVERED = (
    "Original inputs, caches or generated files outside this package directory",
    "Other local copies, backups, remote copies or third-party applications",
    "Conversation memory in running processes; close those sessions separately",
    "Filesystem snapshots, recycle bins and physical storage remanence",
)


def plan_delete(package: Path, *, confirm_local_storage: bool, workspace: Path = ROOT,
                _lock_token: bytes | None = None) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm the selected package is on non-synced local storage")
    package = check_private_location(package, workspace)
    if not package.is_dir():
        raise ValueError("A complete local package directory is required")
    own_lock = package.parent / ".deletion.lock"
    if _lock_token is not None and (not plain_path(own_lock).is_file() or own_lock.read_bytes() != _lock_token):
        raise ValueError("Deletion lock ownership changed")
    blocked = [package / "manifest.json", package / ".migration-incomplete.json",
               package / ".backup-incomplete.json", package / ".deletion-incomplete.json",
               package.parent / ".migration.lock", package.parent / ".backup.lock"]
    if _lock_token is None:
        blocked.append(own_lock)
    if any(path.exists() or path.is_symlink() for path in blocked):
        raise ValueError("Incomplete, locked or legacy-aliased package requires local recovery first")
    header = package_file(package, "manifest.v2.json")
    if header.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Manifest exceeds metadata size limit")
    original = sha(header)
    try:
        manifest = read_json(header)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid deletion manifest metadata") from None
    policy = policy_from_manifest(manifest)
    if (manifest["visibility"] != "private" or policy["execution_policy"]["mode"] != "local_only"
            or policy["distribution_policy"]["mode"] != "local_only"):
        raise ValueError("Deletion planning only supports private local-only packages")
    authorize_operation([policy], "read", ExecutionTarget("local", False))
    if package.name != manifest["person_id"]:
        raise ValueError("Selected directory must match the stable package identity")
    for relative in manifest["files"].values():
        package_file(package, relative)
    files, directories = inventory(package)
    if sha(header) != original or any(path.exists() or path.is_symlink() for path in blocked):
        raise ValueError("Package changed during deletion planning")
    return {"plan_version": "1.0", "dry_run": True, "operation": "delete_local_package",
            "package": str(package), "manifest_sha256": original, "files": files,
            "directories": directories, "total_bytes": sum(item["bytes"] for item in files),
            "not_covered": list(NOT_COVERED), "deletes_package_directory": True,
            "notice": "Review on your local terminal. No files have been deleted."}


def deletion_path(package: Path, relative: str) -> Path:
    """Resolve each exact inventory member immediately before a destructive call."""
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
            or relative != PurePosixPath(relative).as_posix()
            or any(part in {"..", ".git"} or ":" in part for part in PurePosixPath(relative).parts)):
        raise ValueError("Unsafe deletion inventory path")
    path = plain_path(package / relative)
    if path == package or not path.is_relative_to(package):
        raise ValueError("Deletion inventory escapes the selected package")
    return path


def delete_verified_file(package: Path, record: dict) -> None:
    path = deletion_path(package, record["path"])
    if not path.is_file() or path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]:
        raise ValueError("File changed before deletion; incomplete state retained")
    # No globbing, recursive removal or expansion of a stored command string.
    path.unlink()


def apply_delete(package: Path, *, confirm_local_storage: bool, confirm_delete: bool,
                 expected_digest: str, workspace: Path = ROOT) -> dict:
    if confirm_delete is not True:
        raise ValueError("Explicit irreversible deletion confirmation is required")
    plan = plan_delete(package, confirm_local_storage=confirm_local_storage, workspace=workspace)
    if not isinstance(expected_digest, str) or plan_digest(plan) != expected_digest:
        raise ValueError("Deletion plan changed; inspect a new dry-run before applying")
    package = check_private_location(Path(plan["package"]), workspace)
    policy = policy_from_manifest(read_json(package_file(package, "manifest.v2.json")))
    authorize_operation([policy], "delete", ExecutionTarget("local", False))
    lock = package.parent / ".deletion.lock"
    token = json.dumps({"pid": os.getpid(), "token": uuid.uuid4().hex}).encode()
    write_new(lock, token)
    try:
        fresh = plan_delete(package, confirm_local_storage=True, workspace=workspace, _lock_token=token)
        if plan_digest(fresh) != expected_digest:
            raise ValueError("Package changed before deletion; no files removed")
        marker = package / ".deletion-incomplete.json"
        marker_data = json.dumps({"plan_sha256": expected_digest, "plan": plan}, ensure_ascii=False).encode("utf-8")
        write_new(marker, marker_data)
        # Keep manifest until last, although every runtime rejects the marker.
        records = sorted(plan["files"], key=lambda item: (item["path"] == "manifest.v2.json", item["path"]))
        for record in records:
            check_private_location(package, workspace)
            if plain_path(lock).read_bytes() != token or plain_path(marker).read_bytes() != marker_data:
                raise ValueError("Deletion state changed; stop and inspect locally")
            delete_verified_file(package, record)
        for relative in sorted(plan["directories"], key=lambda value: (-len(PurePosixPath(value).parts), value)):
            deletion_path(package, relative).rmdir()  # Fails on new/unlisted files.
        check_private_location(package, workspace)
        if set(package.iterdir()) != {marker} or plain_path(marker).read_bytes() != marker_data:
            raise ValueError("Unexpected remaining files; deletion state retained")
        marker.unlink()
        package.rmdir()
        return {"ok": True, "status": "local_package_deleted", "plan_sha256": expected_digest,
                "deleted_files": len(records), "not_covered": list(NOT_COVERED), "secure_erasure": False}
    finally:
        if plain_path(lock).is_file() and lock.read_bytes() == token:
            lock.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--confirm-local-storage", action="store_true")
    parser.add_argument("--show-plan", action="store_true", help="Show private path inventory on a local terminal only")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-plan-sha256")
    parser.add_argument("--confirm-delete", action="store_true", help="Confirm irreversible deletion of exactly this package")
    args = parser.parse_args(argv)
    if args.show_plan and args.apply:
        parser.error("Use --show-plan separately before --apply")
    if args.show_plan and not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
        print(json.dumps({"ok": False, "error": "Private deletion inventory requires a local terminal"}), file=sys.stderr)
        return 1
    try:
        if args.apply:
            result = apply_delete(args.package, confirm_local_storage=args.confirm_local_storage,
                                  confirm_delete=args.confirm_delete, expected_digest=args.expect_plan_sha256)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        plan = plan_delete(args.package, confirm_local_storage=args.confirm_local_storage)
        result = {"ok": True, "dry_run": True, "plan_sha256": plan_digest(plan),
                  "file_count": len(plan["files"]), "directory_count": len(plan["directories"]),
                  "total_bytes": plan["total_bytes"], "not_covered": plan["not_covered"]}
        if args.show_plan:
            result["plan"] = plan
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"ok": False, "error": "Cannot inspect the local deletion scope"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
