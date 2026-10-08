#!/usr/bin/env python3
"""Preview the exact local package deletion scope. This phase never deletes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from backup_persona import inventory
from migrate_persona import ROOT, check_private_location, package_file, plan_digest, read_json, sha
from package_policy import ExecutionTarget, authorize_operation, policy_from_manifest

NOT_COVERED = (
    "Original inputs, caches or generated files outside this package directory",
    "Other local copies, backups, remote copies or third-party applications",
    "Conversation memory in running processes; close those sessions separately",
    "Filesystem snapshots, recycle bins and physical storage remanence",
)


def plan_delete(package: Path, *, confirm_local_storage: bool, workspace: Path = ROOT) -> dict:
    if confirm_local_storage is not True:
        raise ValueError("Confirm the selected package is on non-synced local storage")
    package = check_private_location(package, workspace)
    if not package.is_dir():
        raise ValueError("A complete local package directory is required")
    blocked = (package / "manifest.json", package / ".migration-incomplete.json",
               package / ".backup-incomplete.json", package / ".deletion-incomplete.json",
               package.parent / ".migration.lock", package.parent / ".backup.lock",
               package.parent / ".deletion.lock")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--confirm-local-storage", action="store_true")
    parser.add_argument("--show-plan", action="store_true", help="Show private path inventory on a local terminal only")
    args = parser.parse_args(argv)
    if args.show_plan and not (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()):
        print(json.dumps({"ok": False, "error": "Private deletion inventory requires a local terminal"}), file=sys.stderr)
        return 1
    try:
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
