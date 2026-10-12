#!/usr/bin/env python3
"""Three-decision story state: explicit costs, actor knowledge and immutable branches."""
from __future__ import annotations

import copy
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "圆桌会议/scripts"))
import roundtable as store

NOTICE = "这是人物视角的虚构思想实验，不是历史记录或真实经历。"


def text(value, maximum=5000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid story text")
    return value.strip()


def resource_map(value, *, signed=False):
    if (not isinstance(value, dict) or len(value) > 20
            or any(not isinstance(k, str) or not k.strip() or len(k) > 80 or type(v) is not int
                   or not (-100000 if signed else 0) <= v <= 100000 for k, v in value.items())):
        raise ValueError("Invalid resources")
    return value


def directory(identifier, root=ROOT):
    root = Path(root).resolve()
    if not isinstance(identifier, str) or not re.fullmatch(r"ST-[0-9]{8}-[0-9a-f]{12}", identifier):
        raise ValueError("Invalid story identifier")
    path = root / "故事推演/sessions" / identifier
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Story path escapes workspace")
    return path


def branch_path(path, branch):
    if not isinstance(branch, str) or not re.fullmatch(r"main|b-[0-9a-f]{12}", branch):
        raise ValueError("Invalid branch")
    result = path / "branches" / branch
    if result.is_symlink() or not result.resolve().is_relative_to(path.resolve()):
        raise ValueError("Invalid branch path")
    return result


def commit(path, state):
    content = store.dump(state)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    folder = path / "snapshots"
    folder.mkdir(exist_ok=True)
    file = folder / (digest + ".json")
    if not file.exists():
        store.atomic(file, content)
    elif store.sha(file) != digest:
        raise ValueError("Immutable snapshot changed")
    target = branch_path(path, state["branch_id"])
    target.mkdir(parents=True, exist_ok=True)
    store.atomic(target / "current.json", store.dump({"snapshot": digest, "version": state["version"]}))
    return {"session_id": state["session_id"], "branch_id": state["branch_id"], "version": state["version"],
            "snapshot": digest, "status": state["status"]}


def read_snapshot(path, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid snapshot")
    file = store.checked_file(path, f"snapshots/{digest}.json")
    if store.sha(file) != digest:
        raise ValueError("Story snapshot changed")
    value = store.read_json(file)
    if value.get("session_id") != path.name:
        raise ValueError("Snapshot belongs to another story")
    return value


def load(identifier, branch="main", root=ROOT):
    path = directory(identifier, root)
    pointer = store.read_json(store.checked_file(branch_path(path, branch), "current.json"))
    value = read_snapshot(path, pointer["snapshot"])
    if value["branch_id"] != branch or value["version"] != pointer["version"]:
        raise ValueError("Branch pointer mismatch")
    return value


def check_pins(state, root):
    store.check_pins({"pins": {state["person_id"]: state["pins"]}}, Path(root))


def create(config, *, root=ROOT, preview=False):
    root = Path(root).resolve()
    if not isinstance(config, dict) or set(config) != {"person_id", "title", "premise", "rules", "goal", "facts", "resources"}:
        raise ValueError("Invalid story fields")
    person = config["person_id"]
    if not isinstance(person, str) or store.registry(root).get(person) != person:
        raise ValueError("Unknown persona")
    package = root / "personas" / person
    manifest_file = store.checked_file(package, "manifest.json")
    manifest = store.read_json(manifest_file)
    store.require_legacy_manifest(manifest)
    if manifest.get("subject_kind") != "historical_public" or manifest.get("person_id") != person:
        raise ValueError("Only historical public story previews supported")
    ready = (manifest.get("visibility") == "public" and manifest.get("readiness") in {"simulation-ready", "publish-ready"}
             and manifest.get("capabilities", {}).get("story_simulation") is True)
    if not ready and not preview:
        raise ValueError("Explicit historical draft preview required")
    for field in ("title", "premise", "rules", "goal"):
        text(config[field])
    resource_map(config["resources"])
    facts = config["facts"]
    if not isinstance(facts, list) or not 1 <= len(facts) <= 100:
        raise ValueError("Provide initial world facts")
    ids = set()
    for fact in facts:
        if (not isinstance(fact, dict) or set(fact) != {"id", "text", "known"}
                or not isinstance(fact["id"], str) or not re.fullmatch(r"W-[0-9]{4,}", fact["id"])
                or fact["id"] in ids or type(fact["known"]) is not bool):
            raise ValueError("Invalid world fact")
        text(fact["text"])
        ids.add(fact["id"])
    pins = {manifest_file.relative_to(root).as_posix(): store.sha(manifest_file)}
    for role in ("persona_spec", "core_anchors", "dialogue_examples", "structured_evidence", "sources"):
        file = store.checked_file(package, manifest["files"][role])
        pins[file.relative_to(root).as_posix()] = store.sha(file)
    identifier = f"ST-{date.today():%Y%m%d}-{uuid.uuid4().hex[:12]}"
    state = {**copy.deepcopy(config), "schema_version": "1.0", "session_id": identifier, "branch_id": "main",
             "version": 0, "status": "ready", "decisions": 0, "history": [], "pending": None, "parent_snapshot": None,
             "pins": pins, "preview": not ready, "display_name": manifest["display_name"], "simulation_notice": NOTICE}
    path = directory(identifier, root)
    path.mkdir(parents=True, exist_ok=False)
    return commit(path, state)


def actor_view(state):
    """Never pass the full world or causal engine history to the character."""
    return {key: copy.deepcopy(state[key]) for key in ("title", "premise", "rules", "goal", "resources", "decisions")} | {
        "known_facts": [copy.deepcopy(f) for f in state["facts"] if f["known"]],
        "experience": [{"choice": h["choice"]["label"], "cost": h["choice"]["cost"], "observation": h["observation"]}
                       for h in state["history"]]}


def mutate(identifier, branch, version, operation, payload, *, root=ROOT):
    path = directory(identifier, root)
    with store.locked(path):
        state = load(identifier, branch, root)
        if state["version"] != version:
            raise ValueError("Stale story request")
        check_pins(state, root)
        status = state["status"]
        if operation == "pause" and status in {"ready", "choosing", "awaiting_effect"}:
            state.update(status="paused", resume_status=status)
        elif operation == "resume" and status == "paused":
            state["status"] = state.pop("resume_status")
        elif operation == "propose" and status == "ready":
            if not isinstance(payload, dict) or set(payload) != {"situation", "options"}:
                raise ValueError("Invalid decision proposal")
            text(payload["situation"])
            options = payload["options"]
            if not isinstance(options, list) or not 2 <= len(options) <= 4:
                raise ValueError("Provide two to four choices")
            for option in options:
                if not isinstance(option, dict) or set(option) != {"label", "reason", "cost", "tradeoff"}:
                    raise ValueError("Invalid choice")
                for key in ("label", "reason", "tradeoff"):
                    text(option[key])
                resource_map(option["cost"])
                if not option["cost"] or not any(option["cost"].values()) or any(k not in state["resources"] or v > state["resources"][k] for k,v in option["cost"].items()):
                    raise ValueError("Choice requires an affordable explicit resource cost")
            state.update(pending=copy.deepcopy(payload), status="choosing")
        elif operation == "choose" and status == "choosing":
            if type(payload) is not int or not 0 <= payload < len(state["pending"]["options"]):
                raise ValueError("Invalid choice index")
            choice = copy.deepcopy(state["pending"]["options"][payload])
            for resource, cost in choice["cost"].items():
                state["resources"][resource] -= cost
            state["pending"]["selected"] = choice
            state["pending"]["selected_index"] = payload
            state["status"] = "awaiting_effect"
        elif operation == "effect" and status == "awaiting_effect":
            if not isinstance(payload, dict) or set(payload) != {"observation", "cause", "resource_delta", "reveal", "new_facts"}:
                raise ValueError("Invalid consequence")
            text(payload["observation"]); text(payload["cause"])
            resource_map(payload["resource_delta"], signed=True)
            for resource, delta in payload["resource_delta"].items():
                if resource not in state["resources"] or not 0 <= state["resources"][resource] + delta <= 100000:
                    raise ValueError("Invalid consequence resource change")
                state["resources"][resource] += delta
            ids = {f["id"] for f in state["facts"]}
            reveal = payload["reveal"]
            if not isinstance(reveal, list) or any(not isinstance(i, str) or i not in ids for i in reveal):
                raise ValueError("Cannot reveal unknown world facts")
            for fact in state["facts"]:
                if fact["id"] in reveal:
                    fact["known"] = True
            new = payload["new_facts"]
            if not isinstance(new, list) or len(new) > 10:
                raise ValueError("Too many new world facts")
            for fact in new:
                if not isinstance(fact, dict) or set(fact) != {"text", "known"} or type(fact["known"]) is not bool:
                    raise ValueError("Invalid new world fact")
                text(fact["text"])
                number = max(int(i[2:]) for i in ids) + 1
                identifier_new = f"W-{number:04d}"
                ids.add(identifier_new)
                state["facts"].append({"id": identifier_new, **fact})
            state["history"].append({"step": state["decisions"]+1, "choice": state["pending"]["selected"],
                                     **copy.deepcopy(payload)})
            state["decisions"] += 1
            state.update(pending=None, status="ended" if state["decisions"] == 3 else "ready")
        else:
            raise ValueError("Operation unavailable in this story state")
        state["version"] += 1
        return commit(path, state)


def fork(identifier, digest, *, root=ROOT):
    path = directory(identifier, root)
    with store.locked(path):
        state = read_snapshot(path, digest)
        if state["status"] not in {"ready", "choosing"}:
            raise ValueError("Branch from a pre-decision snapshot")
        check_pins(state, root)
        state.update(branch_id="b-"+uuid.uuid4().hex[:12], version=0, parent_snapshot=digest)
        return commit(path, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    new = sub.add_parser("create")
    new.add_argument("--config", type=Path, required=True)
    new.add_argument("--preview", action="store_true")
    for action in ("status", "actor", "propose", "choose", "effect", "pause", "resume", "fork"):
        command = sub.add_parser(action)
        command.add_argument("--session", required=True)
        command.add_argument("--branch", default="main")
        if action == "fork":
            command.add_argument("--snapshot", required=True)
        elif action not in {"status", "actor"}:
            command.add_argument("--version", type=int, required=True)
            if action in {"propose", "effect"}:
                command.add_argument("--payload", type=Path, required=True)
            elif action == "choose":
                command.add_argument("--index", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "create":
            result = create(store.read_json(args.config), preview=args.preview)
        elif args.action == "fork":
            result = fork(args.session, args.snapshot)
        elif args.action in {"status", "actor"}:
            value = load(args.session, args.branch)
            result = actor_view(value) if args.action == "actor" else {k: value[k] for k in ("session_id", "branch_id", "version", "status", "decisions")}
        else:
            payload = store.read_json(args.payload) if args.action in {"propose", "effect"} else getattr(args, "index", None)
            result = mutate(args.session, args.branch, args.version, args.action, payload)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print("Story operation failed; check local state and inputs", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
