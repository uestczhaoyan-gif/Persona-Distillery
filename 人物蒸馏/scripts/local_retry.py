"""Explicit single-attempt retries without deleting or modifying failed runs."""
import json
import re
import local_distill as local
from candidate_records import fingerprint


def list_attempts(library, job_id, *, confirm_local_storage=False):
    local.load_job(library, job_id, confirm_local_storage=confirm_local_storage)
    directory = local.job_directory(library, job_id)
    results = []
    with local.job_lock(directory):
        for folder, kind in (("candidates", "local_candidate_extraction"), ("specs", "local_spec_candidate")):
            root = local.plain_path(directory / folder)
            if not root.exists():
                continue
            for path in root.iterdir():
                path = local.plain_path(path)
                if not path.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", path.name):
                    raise ValueError("Unexpected attempt directory")
                if len(results) >= 1000:
                    raise ValueError("Attempt listing limit exceeded")
                item = {"kind": folder, "attempt_sha256": path.name,
                        "state": "incomplete" if local.plain_path(path / ".incomplete").exists() else "recorded_unverified",
                        "request_started_record": local.plain_path(path / "request-started.json").exists(),
                        "binding_valid": False, "current_source_verified": False}
                try:
                    with local.plain_path(path / "binding.json").open("rb") as handle:
                        raw = handle.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("Binding limit")
                    value = json.loads(raw.decode("utf-8"))
                    if (not isinstance(value, dict) or fingerprint(value) != path.name
                            or value.get("kind") != kind or value.get("schema_version") != "1.0"):
                        raise ValueError("Invalid binding")
                    parent = value.get("retry_of")
                    if parent is not None and (not isinstance(parent, str) or not re.fullmatch(r"[0-9a-f]{64}", parent)):
                        raise ValueError("Invalid lineage")
                    item.update(binding_valid=True, retry_of=parent)
                except (ValueError, OSError):
                    pass
                results.append(item)
    return {"ok": True, "attempts": sorted(results, key=lambda v: (v["kind"], v["attempt_sha256"]))}


def bind_retry(root, binding, retry_of, marker):
    if retry_of is None:
        return binding
    if not isinstance(retry_of, str) or not re.fullmatch(r"[0-9a-f]{64}", retry_of):
        raise ValueError("Exact failed run hash required")
    previous = local.plain_path(root / retry_of)
    with local.plain_path(previous / ".incomplete").open("rb") as handle:
        if handle.read(128) != marker:
            raise ValueError("Retry requires a known incomplete run")
    with local.plain_path(previous / "binding.json").open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise ValueError("Failed binding exceeds limit")
    old = json.loads(raw.decode("utf-8"))
    if not isinstance(old, dict) or fingerprint(old) != retry_of:
        raise ValueError("Failed binding changed")
    old = dict(old)
    ancestor = old.pop("retry_of", None)
    if ancestor is not None and (not isinstance(ancestor, str) or not re.fullmatch(r"[0-9a-f]{64}", ancestor)):
        raise ValueError("Invalid retry lineage")
    if old != binding:
        raise ValueError("Retry source, model or prompt changed; use a new normal run")
    return {**binding, "retry_of": retry_of}
