"""Offline pilot inputs only. No model calls, generated answers or score expectations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from persona_memory import compile_context
from resolve_persona import ROOT

BASELINE = "docs/baselines/m0-20261008.json"
COMMIT = "7a447f4d19b6b67a7c458cc315d5a000e7eb5155"
SHORT = {
    "kongzi": "孔子视角：关心学习如何落实、关系中的分寸与责任，清楚而从容。",
    "lu-xun": "鲁迅视角：留意名目与实际代价的错位，从日常细节观察，对具体的人有分寸。",
    "richard-feynman": "费曼视角：好奇现象与机制，愿意用简单模型、估算和实验共同探索。",
    "zhuangzi": "庄子视角：留意尺度、名称与用途的局限，换视角但仍尊重事实和现实代价。",
}
BOUNDARY = "这是 AI 模拟，不是真人。不得伪造原话、亲历或真实关系。网络和写入关闭，无可用工具。"
QUESTIONS = (
    "我今天终于弄懂了一个困扰很久的问题，只想分享这份高兴。",
    "一个大家一直沿用的办法最近失效了，我该怎样重新判断它？",
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def historical(path: str, root: Path) -> str:
    result = subprocess.run(["git", "show", COMMIT + ":" + path], cwd=root,
                            capture_output=True, check=True, timeout=20)
    return result.stdout.replace(b"\r\n", b"\n").decode("utf-8")


def prepare(root: Path = ROOT, *, preview: bool = False) -> dict:
    # Read the registry from its immutable Git revision, not a mutable local copy.
    baseline = json.loads(subprocess.run(
        ["git", "show", "dd7544b9acc734f34da0c5ae3d324e651978e230:" + BASELINE], cwd=root,
        capture_output=True, check=True, timeout=20).stdout)
    if baseline["source_commit"] != COMMIT:
        raise ValueError("Unexpected baseline identity")
    if {p["person_id"] for p in baseline["packages"]} != set(SHORT):
        raise ValueError("Unexpected pilot personas")
    inputs = []
    for package in baseline["packages"]:
        person = package["person_id"]
        current = compile_context(person, root, preview)
        candidate = compile_context(person, root, preview, prompt_profile="m2-v1")
        if current["requires_search_tool"] or candidate["requires_search_tool"]:
            raise ValueError("Pilot requires complete eligible memory; no retrieval substitution")
        blocks = []
        for path in package["load_files"]:
            text = historical(path, root)
            if digest(text) != baseline["files"][path]:
                raise ValueError("Historical baseline hash mismatch")
            # Material changes, including withdrawal, must not revive old evidence.
            if path.startswith("personas/") and not path.endswith("03-persona-spec.md"):
                if (root / path).read_text(encoding="utf-8") != text:
                    raise ValueError("Persona material changed; review baseline before comparing")
            blocks.append(f"\n<package-file path={json.dumps(path, ensure_ascii=False)}>\n"
                          + text + "\n</package-file>\n")
        contexts = {
            "A": "你是一位通用助手，清楚自然地回应用户。",
            "B": SHORT[person],
            "C": "".join(blocks),
            "D": candidate["context"],
        }
        for group, body in contexts.items():
            # Common final boundary applies to all groups, including old contracts.
            messages = [{"role": "system", "content": body},
                        {"role": "system", "content": BOUNDARY}]
            serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
            inputs.append({"person_id": person, "group": group, "messages": messages,
                           "characters": len(serialized), "sha256": digest(serialized)})
    return {"schema_version": "1.0", "status": "MODEL_PENDING", "baseline_commit": COMMIT,
            "stage": "development_pilot_not_blind_evaluation", "inputs": inputs,
            "questions": list(QUESTIONS), "first_requests": 32, "max_requests": 64,
            "options": {"temperature": .7, "seed": 20261008, "num_ctx": 32768, "num_predict": 1600},
            "model_identity": None, "real_model_calls": 0, "tools": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Existing historical preview authorization")
    parser.add_argument("--freeze", action="store_true", help="Save immutable inputs under ignored .work/pilots; no model calls")
    args = parser.parse_args()
    try:
        result = prepare(preview=args.preview)
        if args.freeze:
            print(json.dumps(freeze_inputs(result), ensure_ascii=True))
            return 0
        # Default CLI emits only manifest metadata, not memory bodies or answers.
        for item in result["inputs"]:
            item.pop("messages")
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('{"ok": false, "error": "Pilot input preparation failed; check authorization, Git history and unchanged eligible materials"}')
        return 1


def freeze_inputs(plan: dict, root: Path = ROOT) -> dict:
    """Internal writer for prepared public pilot inputs; never overwrite a snapshot."""
    if plan.get("status") != "MODEL_PENDING" or plan.get("real_model_calls") != 0:
        raise ValueError("Only pending offline inputs may be frozen")
    raw = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    identity = hashlib.sha256(raw).hexdigest()
    root = root.resolve()
    directory = root
    for name in (".work", "pilots"):
        directory = directory / name
        if directory.is_symlink() or (directory.exists() and
                getattr(directory.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Snapshot location cannot be a link")
        directory.mkdir(exist_ok=True)
    path = directory / (identity + ".json")
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("Snapshot cannot be a link")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.stat().st_size != len(raw) or path.read_bytes() != raw:
            raise ValueError("Existing snapshot differs; preserve it and investigate") from None
    return {"status": "MODEL_PENDING", "snapshot_sha256": identity,
            "relative_path": path.relative_to(root).as_posix(), "real_model_calls": 0}


if __name__ == "__main__":
    raise SystemExit(main())
