"""Explicit single-attempt retries without deleting or modifying failed runs."""
import json
import re
import local_distill as local
from candidate_records import fingerprint


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
