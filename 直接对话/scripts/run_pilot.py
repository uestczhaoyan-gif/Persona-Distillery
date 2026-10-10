"""Bounded local development pilot. Results stay under ignored .work/pilots."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import time

from ollama_provider import ProviderError
from pilot_runtime import canonical, current_inputs, provider_for, read_snapshot, reject_link, snapshot_path
from resolve_persona import ROOT

BUDGET = {"first_requests": 32, "max_requests": 64, "max_attempts_per_case": 2,
          "stop_after_same_failures": 3, "max_wall_seconds": 7200}


class PilotStop(ValueError):
    pass


class ResponseFailure(ValueError):
    pass


def append_event(path: Path, data: dict) -> None:
    reject_link(path)
    with path.open("ab") as stream:
        stream.write(canonical(data) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def execute(config_sha256: str, *, root: Path = ROOT, preview: bool = False,
            confirm_run: bool = False) -> dict:
    if not confirm_run:
        raise ValueError("Explicit local execution and result storage confirmation required")
    config = read_snapshot(config_sha256, root, model=True)
    if (config.get("schema_version") != "1.0" or config.get("kind") != "pilot_model_config"
            or config.get("status") != "CONFIG_FROZEN_CAPACITY_PENDING"
            or config.get("budget") != BUDGET or config.get("tools") != []
            or config.get("capacity_verified") is not False):
        raise ValueError("Unsupported pilot configuration")
    plan = current_inputs(config["input_sha256"], root, preview)
    if len(plan["inputs"]) != 16 or len(plan["questions"]) != 2:
        raise ValueError("Pilot must contain exactly 32 cases")
    provider = provider_for(plan, config["requested_model"], config["base_url"], config["timeout_seconds"])
    if provider.options() != config["model_identity"]["options"]:
        raise ValueError("Frozen sampling options differ")
    directory = snapshot_path(config_sha256, root, model=True).parent / ("run-" + config_sha256)
    reject_link(directory)
    # One execution per configuration. A crash/uncertain request is not silently replayed.
    directory.mkdir()
    marker = directory / ".running"
    marker.write_bytes(b"incomplete; inspect journal before any manual recovery\n")
    journal = directory / "events.jsonl"
    started = time.monotonic()
    reserved = inference_attempts = successes = failures = consecutive = 0
    previous_error = None
    status = "RUNNING"
    append_event(journal, {"event": "start", "config_sha256": config_sha256,
                           "input_sha256": config["input_sha256"], "budget": BUDGET})

    def check_time():
        remaining = BUDGET["max_wall_seconds"] - (time.monotonic() - started)
        if remaining < 1:
            raise PilotStop("wall_budget")
        provider.timeout = min(config["timeout_seconds"], remaining)

    def check_identity():
        check_time()
        if provider.inspect_identity() != config["model_identity"]:
            raise PilotStop("model_changed")
        check_time()

    try:
        for item in plan["inputs"]:
            for number, question in enumerate(plan["questions"]):
                case = f"{item['person_id']}:{item['group']}:{number + 1}"
                for attempt in range(1, BUDGET["max_attempts_per_case"] + 1):
                    check_time()
                    if reserved >= BUDGET["max_requests"]:
                        raise PilotStop("request_budget")
                    # Revalidate eligibility and exact frozen prompt before each attempt.
                    current_inputs(config["input_sha256"], root, preview)
                    if read_snapshot(config_sha256, root, model=True) != config:
                        raise PilotStop("configuration_changed")
                    reserved += 1
                    append_event(journal, {"event": "reserved", "case": case, "attempt": attempt,
                                           "reserved_attempts": reserved})
                    error_kind = None
                    try:
                        check_identity()
                        messages = copy.deepcopy(item["messages"]) + [{"role": "user", "content": question}]
                        check_time()
                        inference_attempts += 1
                        append_event(journal, {"event": "inference_started", "case": case,
                                               "attempt": attempt, "inference_attempts": inference_attempts})
                        response = provider.complete(messages, [])
                        metrics = copy.deepcopy(provider.last_metrics)
                        check_identity()
                        current_inputs(config["input_sha256"], root, preview)
                        check_time()
                        if (not isinstance(response, dict) or response.get("role") != "assistant"
                                or not isinstance(response.get("content"), str)
                                or not response["content"].strip() or response.get("tool_calls")):
                            raise ResponseFailure("invalid_response")
                        if metrics.get("done_reason") == "length":
                            raise ResponseFailure("output_limit")
                        successes += 1
                        consecutive, previous_error = 0, None
                        append_event(journal, {"event": "answer", "case": case, "attempt": attempt,
                                               "content": response["content"], "metrics": metrics})
                        break
                    except ProviderError:
                        error_kind = "transport_or_provider"
                    except ResponseFailure as error:
                        error_kind = str(error)
                    failures += 1
                    consecutive = consecutive + 1 if error_kind == previous_error else 1
                    previous_error = error_kind
                    append_event(journal, {"event": "failure", "case": case, "attempt": attempt,
                                           "category": error_kind, "consecutive_same": consecutive})
                    if consecutive >= BUDGET["stop_after_same_failures"]:
                        raise PilotStop("repeated_" + error_kind)
        status = "GENERATED_REVIEW_PENDING" if successes == 32 else "INCOMPLETE_REVIEW_PENDING"
    except PilotStop as error:
        status = "STOPPED_" + str(error).upper()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # Avoid serializing private paths, provider bodies or arbitrary exception text.
        status = "STOPPED_INPUT_OR_STORAGE_ERROR"
    report = {"config_sha256": config_sha256, "status": status, "reserved_attempts": reserved,
              "inference_attempts": inference_attempts, "successful_cases": successes,
              "failed_attempts": failures, "capacity_verified": False,
              "human_review_completed": False, "elapsed_seconds": round(time.monotonic() - started, 3),
              "notice": "Development pilot only; token counts do not prove absence of truncation"}
    append_event(journal, {"event": "finish", **report})
    with (directory / "report.json").open("xb") as stream:
        stream.write(canonical(report))
        stream.flush()
        os.fsync(stream.fileno())
    marker.unlink()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_sha256")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--confirm-run-and-local-results", action="store_true")
    args = parser.parse_args()
    try:
        report = execute(args.config_sha256, preview=args.preview,
                         confirm_run=args.confirm_run_and_local_results)
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report["status"] == "GENERATED_REVIEW_PENDING" else 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('{"ok":false,"error":"Pilot failed; inspect local configuration or unfinished run"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
