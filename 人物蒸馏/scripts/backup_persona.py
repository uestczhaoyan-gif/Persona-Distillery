#!/usr/bin/env python3
"""Plan or explicitly copy an entire local-only v2 package. No public export."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
import uuid

from migrate_persona import (ROOT, check_private_location, overlap, package_file,
                            plain_path, plan_digest, read_json, sha, copy_verified, write_new)
from package_policy import ExecutionTarget, authorize_operation, policy_from_manifest

MAX_FILES = 10000
MAX_BYTES = 10 * 1024 * 1024 * 1024


def inventory(package: Path, *, skip_incomplete_marker: bool = False) -> tuple[list[dict], list[str]]:
    """Include private raw files and logs for LOCAL backup, without following links."""
    records = []
    folders = []
    total = 0
    def failed_walk(error):
        raise ValueError("Cannot enumerate the complete backup source") from None
    for current, directories, filenames in os.walk(package, followlinks=False, onerror=failed_walk):
        for name in directories + filenames:
            path = Path(current) / name
            plain_path(path)
            if name.casefold() == ".git":
                raise ValueError("Nested Git data is not permitted in a private backup")
            if name.endswith((" ", ".")) or ":" in name or "\\" in name:
                raise ValueError("Non-portable backup path")
        folders.extend((Path(current) / name).relative_to(package).as_posix() for name in directories)
        if len(folders) > MAX_FILES:
            raise ValueError("Backup exceeds directory count limit")
        for name in filenames:
            if skip_incomplete_marker and Path(current) == package and name == ".backup-incomplete.json":
                continue
            path = Path(current) / name
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Backup supports regular files only")
            total += info.st_size
            if len(records) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError("Backup exceeds file count or byte limit")
            digest = sha(path)
            after = path.stat()
            if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Backup source changed during planning")
            records.append({"path": path.relative_to(package).as_posix(),
                            "bytes": info.st_size, "sha256": digest})
    names = [record["path"].casefold() for record in records] + [name.casefold() for name in folders]
    if len(set(names)) != len(names):
        raise ValueError("Backup contains case-colliding paths")
    return sorted(records, key=lambda record: record["path"]), sorted(folders)


def plan_backup(package: Path, destination_library: Path, *, confirm_local_storage: bool,
                workspace: Path = ROOT, _incomplete_target: Path | None = None) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm both source and destination are non-synced local storage")
    package = check_private_location(package, workspace)
    library = check_private_location(destination_library, workspace)
    if not package.is_dir() or overlap(package, library):
        raise ValueError("Backup source must exist and be separate from its destination")
    blocked = (package / "manifest.json", package / ".migration-incomplete.json",
               package / ".backup-incomplete.json", package.parent / ".migration.lock",
               package.parent / ".backup.lock")
    if any(path.exists() or path.is_symlink() for path in blocked):
        raise ValueError("Incomplete, locked or legacy-aliased source cannot be backed up")
    header_path = package_file(package, "manifest.v2.json")
    if header_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Manifest exceeds metadata size limit")
    header_digest = sha(header_path)
    manifest = read_json(header_path)
    policy = policy_from_manifest(manifest)
    if (manifest["visibility"] != "private" or policy["execution_policy"]["mode"] != "local_only"
            or policy["distribution_policy"]["mode"] != "local_only"):
        raise ValueError("Backup planner only supports private local-only packages")
    authorize_operation([policy], "backup", ExecutionTarget("local", False))
    if package.name != manifest["person_id"]:
        raise ValueError("Backup source directory must match its stable identity")
    target = check_private_location(library / manifest["person_id"], workspace)
    if target.exists() and target != _incomplete_target:
        raise ValueError("Backup destination already exists; overwriting is not supported")
    # Required declared files must be present; the inventory then also includes
    # undeclared raw/local data so this is a complete private backup, not export.
    for relative in manifest["files"].values():
        package_file(package, relative)
    records, folders = inventory(package)
    if sha(header_path) != header_digest or any(path.exists() or path.is_symlink() for path in blocked):
        raise ValueError("Backup source changed during planning")
    return {"plan_version": "1.0", "dry_run": True, "operation": "local_backup",
            "source": str(package), "target": str(target), "files": records, "directories": folders,
            "total_bytes": sum(record["bytes"] for record in records),
            "execution_mode": "local_only", "distribution_mode": "local_only",
            "notice": "Full private backup includes raw data and logs; never upload or publish it."}


def apply_backup(package: Path, destination_library: Path, *, confirm_local_storage: bool,
                 expected_digest: str, workspace: Path = ROOT) -> dict:
    plan = plan_backup(package, destination_library, confirm_local_storage=confirm_local_storage,
                       workspace=workspace)
    if not isinstance(expected_digest, str) or plan_digest(plan) != expected_digest:
        raise ValueError("Backup plan changed; review a fresh dry-run before applying")
    library = check_private_location(destination_library, workspace)
    library.mkdir(parents=True, exist_ok=True)
    check_private_location(library, workspace)
    lock = library / ".backup.lock"
    token = json.dumps({"pid": os.getpid(), "token": uuid.uuid4().hex}).encode()
    # Exclusive create: another owner or a stale lock is never overwritten.
    write_new(lock, token)
    try:
        current = plan_backup(package, library, confirm_local_storage=True, workspace=workspace)
        if plan_digest(current) != expected_digest:
            raise ValueError("Backup source changed before copying")
        source, target = Path(plan["source"]), Path(plan["target"])
        target.mkdir()
        marker = target / ".backup-incomplete.json"
        marker_data = json.dumps({"plan_sha256": expected_digest, "state": "incomplete"}).encode()
        write_new(marker, marker_data)
        for relative in plan["directories"]:
            directory = plain_path(target / relative)
            directory.mkdir(parents=True, exist_ok=True)
        # Metadata may be copied early, but the incomplete marker blocks loads.
        for record in plan["files"]:
            copy_verified(plain_path(source / record["path"]), plain_path(target / record["path"]), record)
        fresh = plan_backup(source, library, confirm_local_storage=True, workspace=workspace,
                            _incomplete_target=target)
        if plan_digest(fresh) != expected_digest:
            raise ValueError("Backup source changed during copying")
        check_private_location(target, workspace)
        copied, folders = inventory(target, skip_incomplete_marker=True)
        if copied != plan["files"] or folders != plan["directories"] or marker.read_bytes() != marker_data:
            raise ValueError("Backup verification failed; incomplete copy retained")
        marker.unlink()
        return {"ok": True, "status": "local_backup_created", "file_count": len(copied),
                "total_bytes": plan["total_bytes"], "plan_sha256": expected_digest,
                "distribution_mode": "local_only"}
    finally:
        if plain_path(lock).is_file() and lock.read_bytes() == token:
            lock.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("destination_library", type=Path)
    parser.add_argument("--confirm-local-storage", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            result = apply_backup(args.package, args.destination_library,
                                  confirm_local_storage=args.confirm_local_storage,
                                  expected_digest=args.expect_plan_sha256)
            print(json.dumps(result))
            return 0
        plan = plan_backup(args.package, args.destination_library,
                           confirm_local_storage=args.confirm_local_storage)
        # Paths, person identity and inventory are deliberately not printed.
        print(json.dumps({"ok": True, "dry_run": True, "file_count": len(plan["files"]),
                          "total_bytes": plan["total_bytes"], "plan_sha256": plan_digest(plan),
                          "notice": plan["notice"]}))
        return 0
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({"ok": False, "error": "Cannot access local backup storage"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
