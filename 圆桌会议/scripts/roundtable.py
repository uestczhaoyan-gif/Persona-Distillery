#!/usr/bin/env python3
"""Offline roundtable state protocol; no model calls or automatic moderation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
from resolve_persona import checked_file, read_json, registry

NOTICE = "这是基于材料构建的人物视角模拟讨论，不是历史记录或真实发言。"


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError("Symlink output refused")
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def validate(value: dict, name: str, root: Path) -> None:
    schema = read_json(root / f"圆桌会议/schemas/{name}.schema.json")
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise ValueError(f"Invalid {name} at " + "/".join(map(str, errors[0].absolute_path)))


def directory(session_id: str, root: Path = ROOT) -> Path:
    if not re.fullmatch(r"RT-\d{8}-\d{3,}", session_id):
        raise ValueError("Invalid session ID")
    path = root.resolve() / "圆桌会议/sessions" / session_id
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Session directory must stay inside the workspace")
    return path


@contextmanager
def locked(path: Path):
    lock = path / ".lock"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(dump({"pid": os.getpid(), "at": now()}))
    except FileExistsError:
        raise ValueError("Session locked; inspect the lock after a crash") from None
    try:
        yield
    finally:
        lock.unlink()


def participant_pins(session: dict, root: Path, preview: bool) -> dict:
    pins = {}
    persons = registry(root)
    for participant in session["participants"]:
        person_id = participant["person_id"]
        if persons.get(person_id) != person_id:
            raise ValueError("Unknown participant ID")
        package = root / "personas" / person_id
        if package.is_symlink():
            raise ValueError("Symlink package refused")
        manifest_file = checked_file(package, "manifest.json")
        manifest = read_json(manifest_file)
        if manifest.get("person_id") != person_id or manifest.get("package_version") != participant["package_version"]:
            raise ValueError("Participant identity/version mismatch")
        if manifest.get("display_name") != participant["display_name"]:
            raise ValueError("Participant display name mismatch")
        public = (manifest.get("readiness") in {"simulation-ready", "publish-ready"}
                  and manifest.get("visibility") == "public" and manifest.get("capabilities", {}).get("roundtable") is True)
        if not public:
            if (not preview or not session["preview_override"] or participant.get("preview_override") is not True
                    or manifest.get("subject_kind") != "historical_public"):
                raise ValueError("Participant requires explicit historical-public private preview")
        adapter = checked_file(root / "圆桌会议", f"personas/{person_id}.md")
        text = adapter.read_text(encoding="utf-8")
        if participant["adapter_version"] not in text:
            raise ValueError("Adapter version mismatch")
        pinned = {f"personas/{person_id}/manifest.json": sha(manifest_file),
                  adapter.relative_to(root).as_posix(): sha(adapter)}
        for role in ("persona_spec", "core_anchors", "dialogue_examples", "profile", "sources", "evidence",
                     "context_memory", "structured_evidence", "structured_context_memory"):
            if role in manifest["files"]:
                file = checked_file(package, manifest["files"][role])
                pinned[file.relative_to(root).as_posix()] = sha(file)
        pins[person_id] = pinned
    return pins


def check_pins(runtime: dict, root: Path) -> None:
    for paths in runtime["pins"].values():
        for relative, expected in paths.items():
            path = checked_file(root, relative)
            if sha(path) != expected:
                raise ValueError("Pinned persona or adapter changed; create a new session")


def transcript(session: dict, turns: list[dict], events: list[dict]) -> str:
    names = {p["person_id"]: p["display_name"] for p in session["participants"]}
    text = f"# {session['title']}\n\n{NOTICE}\n\n"
    if session["preview_override"]:
        text += "资料不足的私有预览版本；能力开关未升级。\n\n"
    merged = [(turn["sequence"], "turn", turn) for turn in turns]
    merged += [(event["after_sequence"] + .5, "event", event) for event in events]
    for _, kind, item in sorted(merged, key=lambda row: row[0]):
        if kind == "event":
            text += f"## 用户插话 · {item['event_id']}\n\n{item['text']}\n\n"
        else:
            text += f"## {item['sequence']}. {names[item['speaker_id']]} · {item['turn_id']}\n\n{item['text']}\n\n"
    return text


def persist(path: Path, bundle: dict, root: Path) -> None:
    session = bundle["session"]
    validate(session, "session", root)
    snapshot = path / "snapshots" / f"v{session['state_version']:05d}-{uuid.uuid4().hex[:8]}"
    snapshot.mkdir(parents=True)
    artifacts = {"session.json": dump(session), "runtime.json": dump(bundle["runtime"]),
                 "turns.jsonl": "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in bundle["turns"]),
                 "events.jsonl": "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in bundle["events"]),
                 "transcript.md": transcript(session, bundle["turns"], bundle["events"]),
                 "provenance.md": "# 研究附录\n\n" + NOTICE + "\n\n" + "\n".join(
                     f"- {t['turn_id']}：{json.dumps(t['basis'], ensure_ascii=False)}" for t in bundle["turns"])}
    if bundle.get("article") is not None:
        artifacts["article.json"] = dump(bundle["article"])
        article = bundle["article"]
        essay = f"# {article['title']}\n\n{article['simulation_notice']}\n\n{article['background']}\n\n"
        for section in article["development"]:
            essay += f"## {section['heading']}\n\n{section['body']}\n\n"
        essay += "## 共识\n\n" + "\n".join("- " + value for value in article["consensus"]) + "\n\n"
        essay += "## 分歧\n\n" + "\n".join("- " + value["summary"] for value in article["disagreements"]) + "\n\n"
        essay += "## 未决问题\n\n" + "\n".join("- " + value for value in article["open_questions"]) + "\n"
        artifacts["essay.md"] = essay
    for name, content in artifacts.items():
        atomic(snapshot / name, content)
    pointer = {"snapshot": snapshot.relative_to(path).as_posix(), "state_version": session["state_version"],
               "checksums": {name: sha(snapshot / name) for name in artifacts}}
    # This single replacement commits the entire immutable snapshot.
    atomic(path / "current.json", dump(pointer))


def load(path: Path, root: Path) -> dict:
    pointer = read_json(checked_file(path, "current.json"))
    relative = pointer.get("snapshot", "")
    if not isinstance(relative, str) or not re.fullmatch(r"snapshots/v\d{5,}-[0-9a-f]{8}", relative):
        raise ValueError("Invalid committed snapshot path")
    for name, expected in pointer["checksums"].items():
        if sha(checked_file(path, f"{relative}/{name}")) != expected:
            raise ValueError("Committed snapshot checksum mismatch")
    snapshot = path / relative
    def rows(name):
        return [json.loads(line) for line in checked_file(snapshot, name).read_text(encoding="utf-8").splitlines() if line]
    session = read_json(checked_file(snapshot, "session.json"))
    validate(session, "session", root)
    if session["state_version"] != pointer["state_version"]:
        raise ValueError("Committed state version mismatch")
    return {"session": session, "runtime": read_json(checked_file(snapshot, "runtime.json")),
            "turns": rows("turns.jsonl"), "events": rows("events.jsonl"),
            "article": read_json(snapshot / "article.json") if (snapshot / "article.json").exists() else None}


def create(config: dict, root: Path = ROOT, preview: bool = False) -> dict:
    root = root.resolve()
    validate(config, "session", root)
    if config["status"] != "draft" or config["state_version"] != 0 or config["current"]["turn_index"] != 0 or config.get("active_generation"):
        raise ValueError("New session must be an empty draft at version zero")
    if config["network_policy"] != "off" or config.get("fact_brief_id"):
        raise ValueError("State prototype supports network off and no fact brief")
    if config["current"]["section_index"] != 0 or config["current"]["pending_user_event_id"]:
        raise ValueError("New session cannot contain old sections or user events")
    if set(config["limits"]) - {"max_sections", "max_turns", "max_chars_per_turn", "no_new_claim_limit"}:
        raise ValueError("Time/cost accounting is not implemented in this offline prototype")
    ids = [p["person_id"] for p in config["participants"]]
    if len(ids) != len(set(ids)) or config["current"]["next_speaker"] not in ids:
        raise ValueError("Duplicate participant or invalid next speaker")
    pins = participant_pins(config, root, preview)
    path = directory(config["session_id"], root)
    if path.exists():
        raise ValueError("Session already exists")
    path.mkdir(parents=True)
    bundle = {"session": copy.deepcopy(config), "runtime": {"pins": pins, "no_new_claims": 0}, "turns": [], "events": [], "article": None}
    persist(path, bundle, root)
    return bundle["session"]


def advance(session: dict) -> None:
    session["state_version"] += 1
    session["updated_at"] = now()


def transition(session_id: str, action: str, root: Path = ROOT, text: str = "") -> dict:
    path = directory(session_id, root)
    with locked(path):
        bundle = load(path, root)
        session = bundle["session"]
        status = session["status"]
        allowed = {"start": {"draft"}, "pause": {"running"}, "resume": {"paused", "awaiting_user"},
                   "interject": {"running", "paused"}, "finish": {"running", "paused", "awaiting_user"}}
        if action not in allowed or status not in allowed[action]:
            raise ValueError(f"Invalid {action} transition from {status}")
        if action in {"start", "resume"}:
            check_pins(bundle["runtime"], root)
            session["status"] = "running"
            if action == "start":
                session["started_at"] = now()
            if text:
                session["current"]["question"] = text
        elif action == "interject":
            if not text.strip() or len(text) > 10000:
                raise ValueError("Interjection must contain 1–10000 characters")
            event_id = f"EV-{len(bundle['events'])+1:04d}"
            bundle["events"].append({"event_id": event_id, "event_type": "user_interjection", "text": text,
                                     "after_sequence": len(bundle["turns"]), "created_at": now()})
            session["current"]["pending_user_event_id"] = event_id
            session["status"] = "awaiting_user"
        else:
            session["status"] = "paused" if action == "pause" else "finishing"
        session["active_generation"] = None
        advance(session)
        persist(path, bundle, root)
        return session


def request_turn(session_id: str, speaker: str | None = None, root: Path = ROOT) -> dict:
    path = directory(session_id, root)
    with locked(path):
        bundle = load(path, root)
        session = bundle["session"]
        check_pins(bundle["runtime"], root)
        if session["status"] != "running" or session.get("active_generation"):
            raise ValueError("Session must be running without an active request")
        if len(bundle["turns"]) >= session["limits"]["max_turns"]:
            raise ValueError("Turn limit reached")
        speaker = speaker or session["current"]["next_speaker"]
        if speaker not in {p["person_id"] for p in session["participants"]}:
            raise ValueError("Unknown speaker")
        advance(session)
        ticket = {"session_id": session_id, "request_id": uuid.uuid4().hex,
                  "turn_id": f"{session_id}-T-{len(bundle['turns'])+1:04d}", "state_version": session["state_version"],
                  "speaker_id": speaker, "sequence": len(bundle["turns"]) + 1,
                  "section_index": session["current"]["section_index"]}
        session["active_generation"] = {key: ticket[key] for key in ("request_id", "turn_id", "state_version")}
        session["active_generation"].update(status="running", started_at=now())
        bundle["runtime"]["ticket"] = ticket
        persist(path, bundle, root)
        return ticket


def commit_turn(ticket: dict, turn: dict, root: Path = ROOT) -> dict:
    path = directory(ticket.get("session_id", ""), root)
    with locked(path):
        bundle = load(path, root)
        session = bundle["session"]
        if (session["status"] != "running" or session.get("active_generation") is None
                or ticket != bundle["runtime"].get("ticket") or ticket.get("state_version") != session["state_version"]):
            return {"accepted": False, "reason": "stale_or_cancelled_request"}
        check_pins(bundle["runtime"], root)
        validate(turn, "turn", root)
        for key in ("session_id", "turn_id", "speaker_id", "sequence", "section_index"):
            if turn[key] != ticket[key]:
                raise ValueError("Turn identity does not match generation ticket")
        if len(turn["text"]) > session["limits"]["max_chars_per_turn"] or not turn["moderation"]["accepted"]:
            raise ValueError("Turn exceeds length limit or lacks moderation acceptance")
        known_turns = {t["turn_id"] for t in bundle["turns"]}
        known_claims = {c["claim_id"] for t in bundle["turns"] for c in t["claims"]}
        known_events = {e["event_id"] for e in bundle["events"]}
        for target in turn["responds_to"]:
            if target not in known_turns | known_claims | known_events | {"opening"}:
                raise ValueError("Response target does not exist")
        pending = session["current"]["pending_user_event_id"]
        if pending and pending not in turn["responds_to"]:
            raise ValueError("Turn must address the pending user interjection")
        new_claims = [c["claim_id"] for c in turn["claims"]]
        if len(new_claims) != len(set(new_claims)) or set(new_claims) & known_claims:
            raise ValueError("Duplicate claim ID")
        if any(c.get("responds_to_claim_id") and c["responds_to_claim_id"] not in known_claims for c in turn["claims"]):
            raise ValueError("Claim response target does not exist")
        package = root / "personas" / turn["speaker_id"]
        manifest = read_json(checked_file(package, "manifest.json"))
        evidence_path = checked_file(package, manifest["files"]["structured_evidence"])
        evidence = {e["evidence_id"]: e for e in (json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines() if line)}
        with checked_file(package, manifest["files"]["sources"]).open(encoding="utf-8-sig", newline="") as handle:
            sources = {row["source_id"] for row in csv.DictReader(handle)}
        basis = turn["basis"]
        if (set(basis["evidence_ids"]) - evidence.keys() or set(basis["source_ids"]) - sources or basis["fact_ids"]
                or ("direct" in basis["modes"] and not basis["evidence_ids"])):
            raise ValueError("Unknown or unsupported evidence/source/fact basis")
        if any(evidence[e].get("status") == "withdrawn" or evidence[e].get("delivery_channel") == "local_only" for e in basis["evidence_ids"]):
            raise ValueError("Withdrawn or local-only evidence cannot support this turn")
        linked_sources = {ref["source_id"] for eid in basis["evidence_ids"] for ref in evidence[eid]["source_refs"]}
        if basis["evidence_ids"] and set(basis["source_ids"]) - linked_sources:
            raise ValueError("Source IDs do not support the cited evidence")
        bundle["turns"].append(copy.deepcopy(turn))
        bundle["runtime"]["no_new_claims"] = (0 if any(c["novelty"] in {"new", "reframed"} for c in turn["claims"])
                                               else bundle["runtime"]["no_new_claims"] + 1)
        ids = [p["person_id"] for p in session["participants"]]
        session["current"].update(turn_index=len(bundle["turns"]), next_speaker=ids[(ids.index(turn["speaker_id"]) + 1) % len(ids)], pending_user_event_id=None)
        session["active_generation"] = None
        signal = turn["moderation"]["stop_signal"]
        if signal == "section_ready":
            session["current"]["section_index"] += 1
            session["status"] = "paused"
        if (len(bundle["turns"]) >= session["limits"]["max_turns"]
                or session["current"]["section_index"] >= session["limits"]["max_sections"]
                or bundle["runtime"]["no_new_claims"] >= session["limits"]["no_new_claim_limit"]
                or signal == "session_ready"):
            session["status"] = "finishing"
        elif turn.get("needs_fact_check") or signal in {"risk", "needs_user", "needs_fact_check"}:
            session["status"] = "paused"
        advance(session)
        persist(path, bundle, root)
        return {"accepted": True, "status": session["status"], "state_version": session["state_version"]}


def complete(session_id: str, article: dict, root: Path = ROOT) -> dict:
    path = directory(session_id, root)
    with locked(path):
        bundle = load(path, root)
        session = bundle["session"]
        if session["status"] != "finishing":
            raise ValueError("Article completion requires finishing state")
        validate(article, "article", root)
        ids = {turn["turn_id"] for turn in bundle["turns"]}
        if article["session_id"] != session_id or set(article["source_turn_ids"]) - ids or not article["source_turn_ids"]:
            raise ValueError("Article must cite existing turns from this session")
        participants = {p["person_id"] for p in session["participants"]}
        for section in article["development"] + article["disagreements"]:
            if set(section["turn_ids"]) - set(article["source_turn_ids"]):
                raise ValueError("Article paragraph references must be included in its existing source turns")
            if "participants" in section and set(section["participants"]) - participants:
                raise ValueError("Article disagreement cites unknown participants")
        if article["simulation_notice"] != NOTICE:
            raise ValueError("Article must retain the simulation notice")
        bundle["article"] = copy.deepcopy(article)
        session["status"] = "completed"
        advance(session)
        persist(path, bundle, root)
        return session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("create")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--preview", action="store_true")
    for action in ("start", "pause", "resume", "interject", "finish", "status", "request", "complete"):
        child = commands.add_parser(action)
        child.add_argument("--session", required=True)
        if action in {"resume", "interject"}:
            child.add_argument("--text", default="", required=action == "interject")
        if action == "request":
            child.add_argument("--speaker")
        if action == "complete":
            child.add_argument("--article", type=Path, required=True)
    commit = commands.add_parser("commit")
    commit.add_argument("--ticket", type=Path, required=True)
    commit.add_argument("--turn", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create(read_json(args.config), args.root, args.preview)
        elif args.command == "request":
            result = request_turn(args.session, args.speaker, args.root)
        elif args.command == "commit":
            result = commit_turn(read_json(args.ticket), read_json(args.turn), args.root)
        elif args.command == "complete":
            result = complete(args.session, read_json(args.article), args.root)
        elif args.command == "status":
            result = load(directory(args.session, args.root), args.root)["session"]
        else:
            result = transition(args.session, args.command, args.root, getattr(args, "text", ""))
        output = result if args.command == "request" else {"ok": True, **result}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
