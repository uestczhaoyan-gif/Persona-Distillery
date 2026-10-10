"""Read a verified external candidate run before review; never calls a model."""
from __future__ import annotations

import json
from pathlib import Path
import re

import local_distill as local
from local_candidates import checked_snapshot, PROMPT
from candidate_records import fingerprint, verify_candidates


def read_run(library: Path, job_id: str, run_id: str, *, confirm_local_storage: bool = False) -> dict:
    """Internal local-only API. Never invoke on actual private materials from a cloud agent."""
    if not confirm_local_storage or not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{64}", run_id):
        raise ValueError("Local confirmation and exact run hash required")
    local.load_job(library, job_id, confirm_local_storage=True)
    directory = local.job_directory(library, job_id)
    with local.job_lock(directory):
        state, snapshot, policy = checked_snapshot(library, job_id)
        output = local.plain_path(directory / "candidates" / run_id)
        expected = {"binding.json", "request-started.json", "candidates.json"}
        if {p.name for p in output.iterdir()} != expected | {"checksums.json"}:
            raise ValueError("Candidate run is incomplete or has unexpected files")
        index = local.read_json(local.plain_path(output / "checksums.json"))
        if set(index) != expected:
            raise ValueError("Invalid candidate inventory")
        values = {}
        for name in sorted(expected):
            path = local.plain_path(output / name)
            with path.open("rb") as handle:
                raw = handle.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024 or local.digest(raw) != index[name]:
                raise ValueError("Candidate file changed")
            values[name] = json.loads(raw.decode("utf-8"))
        binding = values["binding.json"]
        if (fingerprint(binding) != run_id or binding.get("schema_version") != "1.0"
                or binding.get("kind") != "local_candidate_extraction" or binding.get("attempt_limit") != 1
                or binding.get("snapshot_sha256") != state["snapshot_sha256"]
                or binding.get("input_fingerprint") != state["input_fingerprint"]
                or values["request-started.json"] != {"inference_attempts": 1}):
            raise ValueError("Run is not bound to the current source snapshot")
        with local.plain_path(snapshot / "chunks.jsonl").open("rb") as handle:
            raw = handle.read(160001)
        if (len(raw) > 160000 or local.digest(raw) != local.read_json(snapshot / "checksums.json")["chunks.jsonl"]):
            raise ValueError("Chunk snapshot changed")
        chunks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        content = local.canonical({"material_basis": policy["material_basis"], "chunks": chunks})
        messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]
        if fingerprint(messages) != binding["prompt_sha256"]:
            raise ValueError("Prompt contract changed; old run needs explicit migration")
        bundle = values["candidates.json"]
        verify_candidates(bundle, chunks, policy)
        checked_snapshot(library, job_id, state)
        # Returned text remains private; a future local reviewer may display it locally only.
        return {"run_sha256": run_id, "candidates_sha256": fingerprint(bundle),
                "bundle": bundle, "chunks": chunks, "policy": policy,
                "semantic_review_completed": False}
