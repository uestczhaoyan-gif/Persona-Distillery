"""Explicit local pilot configuration. Freezing configuration never generates answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from ollama_provider import OllamaProvider
from pilot_inputs import prepare
from resolve_persona import ROOT

HASH = re.compile(r"[0-9a-f]{64}")
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def reject_link(path: Path) -> None:
    if path.is_symlink() or (path.exists() and
            getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("Pilot storage cannot use links")


def snapshot_path(identity: str, root: Path, *, model: bool = False) -> Path:
    if not isinstance(identity, str) or HASH.fullmatch(identity) is None:
        raise ValueError("Invalid snapshot identity")
    path = root.resolve()
    for name in (".work", "pilots", ("model-" if model else "") + identity + ".json"):
        path = path / name
        reject_link(path)
    return path


def read_snapshot(identity: str, root: Path, *, model: bool = False) -> dict:
    path = snapshot_path(identity, root, model=model)
    with path.open("rb") as stream:
        raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES or hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError("Pilot snapshot hash/size mismatch")
    value = json.loads(raw)
    if not isinstance(value, dict) or canonical(value) != raw:
        raise ValueError("Pilot snapshot must be a canonical object")
    return value


def current_inputs(identity: str, root: Path, preview: bool) -> dict:
    saved = read_snapshot(identity, root)
    current = prepare(root, preview=preview)
    if canonical(saved) != canonical(current):
        raise ValueError("Inputs changed; prepare and freeze a new comparison")
    return saved


def provider_for(plan: dict, model: str, base_url: str, timeout: float) -> OllamaProvider:
    options = plan["options"]
    return OllamaProvider(model, base_url, timeout, options["num_ctx"], options["num_predict"],
                          temperature=options["temperature"], seed=options["seed"])


def freeze_model(input_sha256: str, model: str, *, root: Path = ROOT,
                 preview: bool = False, confirm_local: bool = False,
                 base_url: str = "http://127.0.0.1:11434", timeout: float = 60) -> dict:
    if not confirm_local:
        raise ValueError("Confirm the installed model service is local and trusted")
    plan = current_inputs(input_sha256, root, preview)
    provider = provider_for(plan, model, base_url, timeout)
    first = provider.inspect_identity()
    second = provider.inspect_identity()
    if first != second or first["options"] != plan["options"]:
        raise ValueError("Model identity changed while freezing")
    # Recheck source eligibility and candidate drift after metadata requests.
    current_inputs(input_sha256, root, preview)
    config = {"schema_version": "1.0", "kind": "pilot_model_config",
              "status": "CONFIG_FROZEN_CAPACITY_PENDING", "input_sha256": input_sha256,
              "requested_model": provider.model, "base_url": provider.base_url,
              "timeout_seconds": provider.timeout, "model_identity": first,
              "budget": {"first_requests": 32, "max_requests": 64,
                         "max_attempts_per_case": 2, "stop_after_same_failures": 3,
                         "max_wall_seconds": 7200},
              "tools": [], "real_model_calls": 0, "capacity_verified": False}
    raw = canonical(config)
    identity = hashlib.sha256(raw).hexdigest()
    path = snapshot_path(identity, root, model=True)
    # Input snapshot already established the parent; do not create alternate locations.
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if read_snapshot(identity, root, model=True) != config:
            raise ValueError("Existing model snapshot differs") from None
    return {"status": config["status"], "config_sha256": identity,
            "input_sha256": input_sha256, "real_model_calls": 0,
            "capacity_verified": False, "relative_path": path.relative_to(root.resolve()).as_posix()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_sha256")
    parser.add_argument("--model", required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--confirm-local-service", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    try:
        result = freeze_model(args.input_sha256, args.model, preview=args.preview,
                              confirm_local=args.confirm_local_service, base_url=args.base_url,
                              timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('{"ok":false,"error":"Model freeze failed; check local service, input identity and authorization"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
