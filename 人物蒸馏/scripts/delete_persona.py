#!/usr/bin/env python3
"""Preview or explicitly delete a reviewed local-only package. No recursive delete."""
from __future__ import annotations

import argparse
import json
import os
import re
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
    return {"plan_version": "1.1", "dry_run": True, "operation": "delete_local_package", "policy": policy,
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


def resume_state(package: Path, expected_digest: str, *, confirm_local_storage: bool,
                 workspace: Path = ROOT, _lock_token: bytes | None = None) -> tuple:
    if confirm_local_storage is not True:
        raise ValueError("Confirm local storage before inspecting interrupted deletion")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise ValueError("Provide the original approved deletion plan hash")
    package = check_private_location(package, workspace)
    lock = package.parent / ".deletion.lock"
    blocked = [package / "manifest.json", package / ".migration-incomplete.json",
               package / ".backup-incomplete.json", package.parent / ".migration.lock",
               package.parent / ".backup.lock"]
    if _lock_token is None:
        blocked.append(lock)
    elif not plain_path(lock).is_file() or lock.read_bytes() != _lock_token:
        raise ValueError("Deletion lock ownership changed")
    if any(path.exists() or path.is_symlink() for path in blocked):
        raise ValueError("Conflicting local package state prevents deletion recovery")
    marker = plain_path(package / ".deletion-incomplete.json")
    with marker.open("rb") as handle:
        data = handle.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Deletion recovery metadata exceeds size limit")
    try:
        state = json.loads(data)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid deletion recovery metadata") from None
    if not isinstance(state, dict) or not isinstance(state.get("plan"), dict):
        raise ValueError("Invalid deletion recovery metadata")
    plan = state["plan"]
    if state.get("plan_sha256") != expected_digest or plan_digest(plan) != expected_digest:
        raise ValueError("Recovery plan does not match the original approved hash")
    if (plan.get("plan_version") != "1.1" or plan.get("operation") != "delete_local_package"
            or plan.get("package") != str(package)):
        raise ValueError("Unsupported recovery plan or different package location")
    effective = authorize_operation([plan.get("policy")], "delete", ExecutionTarget("local", False))
    if effective["execution_mode"] != "local_only" or effective["distribution_mode"] != "local_only":
        raise ValueError("Recovery requires the original local-only policy")
    records, directories = plan.get("files"), plan.get("directories")
    if not isinstance(records, list) or not isinstance(directories, list) or len(records) > 10000 or len(directories) > 10000:
        raise ValueError("Invalid recovery inventory")
    approved = {}
    for record in records:
        if (not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}
                or type(record["bytes"]) is not int or record["bytes"] < 0
                or not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
            raise ValueError("Invalid recovery file record")
        path = deletion_path(package, record["path"])
        if path == marker or record["path"] in approved:
            raise ValueError("Duplicate or reserved recovery file")
        approved[record["path"]] = record
    for relative in directories:
        deletion_path(package, relative)
    if len(set(directories)) != len(directories):
        raise ValueError("Duplicate recovery directory")
    remaining, folders = inventory(package, skip_deletion_marker=True)
    if any(approved.get(record["path"]) != record for record in remaining) or not set(folders).issubset(directories):
        raise ValueError("Changed or unlisted content must be reviewed locally; no files removed")
    if plain_path(marker).read_bytes() != data:
        raise ValueError("Recovery metadata changed during inspection")
    return plan, remaining, folders, data


def apply_delete(package: Path, *, confirm_local_storage: bool, confirm_delete: bool,
                 expected_digest: str, workspace: Path = ROOT, resume: bool = False) -> dict:
    if confirm_delete is not True:
        raise ValueError("Explicit irreversible deletion confirmation is required")
    if resume:
        plan, _, _, _ = resume_state(package, expected_digest, confirm_local_storage=confirm_local_storage, workspace=workspace)
    else:
        plan = plan_delete(package, confirm_local_storage=confirm_local_storage, workspace=workspace)
    if not isinstance(expected_digest, str) or plan_digest(plan) != expected_digest:
        raise ValueError("Deletion plan changed; inspect a new dry-run before applying")
    package = check_private_location(Path(plan["package"]), workspace)
    authorize_operation([plan["policy"]], "delete", ExecutionTarget("local", False))
    lock = package.parent / ".deletion.lock"
    token = json.dumps({"pid": os.getpid(), "token": uuid.uuid4().hex}).encode()
    write_new(lock, token)
    try:
        if resume:
            fresh, remaining, folders, marker_data = resume_state(package, expected_digest, confirm_local_storage=True,
                                                                  workspace=workspace, _lock_token=token)
        else:
            fresh = plan_delete(package, confirm_local_storage=True, workspace=workspace, _lock_token=token)
            remaining, folders = plan["files"], plan["directories"]
            marker_data = json.dumps({"plan_sha256": expected_digest, "plan": plan}, ensure_ascii=False).encode("utf-8")
        if plan_digest(fresh) != expected_digest:
            raise ValueError("Package changed before deletion; no files removed")
        marker = package / ".deletion-incomplete.json"
        if len(marker_data) > 16 * 1024 * 1024:
            raise ValueError("Deletion plan is too large for recoverable metadata; no files removed")
        if not resume:
            write_new(marker, marker_data)
        # Keep manifest until last, although every runtime rejects the marker.
        records = sorted(remaining, key=lambda item: (item["path"] == "manifest.v2.json", item["path"]))
        for record in records:
            check_private_location(package, workspace)
            if plain_path(lock).read_bytes() != token or plain_path(marker).read_bytes() != marker_data:
                raise ValueError("Deletion state changed; stop and inspect locally")
            delete_verified_file(package, record)
        for relative in sorted(folders, key=lambda value: (-len(PurePosixPath(value).parts), value)):
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
    parser.add_argument("--resume", action="store_true", help="Inspect or continue an interrupted deletion using its original hash")
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
                                  confirm_delete=args.confirm_delete, expected_digest=args.expect_plan_sha256,
                                  resume=args.resume)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.resume:
            plan, remaining, folders, _ = resume_state(args.package, args.expect_plan_sha256,
                                                      confirm_local_storage=args.confirm_local_storage)
        else:
            plan = plan_delete(args.package, confirm_local_storage=args.confirm_local_storage)
            remaining, folders = plan["files"], plan["directories"]
        result = {"ok": True, "dry_run": True, "plan_sha256": plan_digest(plan),
                  "file_count": len(remaining), "directory_count": len(folders),
                  "total_bytes": sum(item["bytes"] for item in remaining), "not_covered": plan["not_covered"]}
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
