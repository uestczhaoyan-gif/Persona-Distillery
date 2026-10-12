#!/usr/bin/env python3
"""Render a completed branch without inventing events outside its actual path."""
from __future__ import annotations

import argparse
import json
import sys

import story


def documents(state):
    if state["status"] != "ended" or state["decisions"] != 3 or len(state["history"]) != 3:
        raise ValueError("Rendering requires three completed decisions")
    header = f"# {state['title']}\n\n{story.NOTICE}\n\n"
    prose = header + state["premise"] + "\n\n"
    timeline = header + f"分支：{state['branch_id']}\n\n"
    provenance = header + f"人物：{state['display_name']}（{state['person_id']}）\n\n每段只来自本路径已提交的选择与可见后果。\n\n"
    for event in state["history"]:
        step, choice = event["step"], event["choice"]
        cost = "、".join(f"{name} {amount}" for name, amount in choice["cost"].items() if amount)
        prose += (f"在第 {step} 次抉择中，{state['display_name']}视角选择了：{choice['label']}。"
                  f"考虑是：{choice['reason']}。这项行动花费了{cost}，也意味着{choice['tradeoff']}。\n\n"
                  + event["observation"] + "\n\n")
        timeline += (f"## 决策 {step}\n\n行动：{choice['label']}\n\n付出：{cost}\n\n取舍：{choice['tradeoff']}\n\n"
                     f"观察：{event['observation']}\n\n内部因果说明：{event['cause']}\n\n"
                     f"额外资源变化：{json.dumps(event['resource_delta'], ensure_ascii=False)}\n\n"
                     f"本次获知：{', '.join(event['reveal']) or '无新增揭示'}\n\n")
        basis = choice.get("basis", {"modes": ["simulation"], "evidence_ids": [], "source_ids": []})
        provenance += f"- 正文阶段 {step} → 本分支决策 {step}；依据：{json.dumps(basis, ensure_ascii=False)}\n"
    prose += "这条路径在第三次选择的后果处结束；尚未解决的问题不因此被视为解决。\n\n---\n按实际路径整理的时间顺序草稿；人物表达与因果合理性尚待审核。\n"
    timeline += "## 路径结束时的资源\n\n" + json.dumps(state["resources"], ensure_ascii=False) + "\n"
    provenance += "\n结构检查不等于语义核实；正文没有补写未选择分支、隐藏世界事实或额外结局。\n"
    provenance += "\n## 人物材料绑定\n\n" + "\n".join(f"- {path}: {digest}" for path, digest in state["pins"].items()) + "\n"
    return {"story.md": prose, "timeline.md": timeline, "provenance.md": provenance}


def render(identifier, *, branch="main", root=story.ROOT):
    path = story.directory(identifier, root)
    with story.store.locked(path):
        location = story.branch_path(path, branch)
        pointer = story.store.read_json(story.store.checked_file(location, "current.json"))
        state = story.read_snapshot(path, pointer["snapshot"])
        if state["branch_id"] != branch or state["version"] != pointer["version"]:
            raise ValueError("Branch state mismatch")
        story.check_pins(state, root)
        files = documents(state)
        relative = "rendered/" + pointer["snapshot"]
        target = path / relative
        if target.is_symlink() or not target.resolve().is_relative_to(path.resolve()):
            raise ValueError("Invalid result path")
        target.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            file = target / name
            if file.exists():
                if file.read_text(encoding="utf-8") != body:
                    raise ValueError("Existing rendered result changed; preserve it for inspection")
            else:
                story.store.atomic(file, body)
        result = {"session_id": identifier, "branch_id": branch, "source_snapshot": pointer["snapshot"],
                  "directory": relative, "checksums": {name: story.store.sha(target / name) for name in files},
                  "semantic_quality_verified": False, "format": "chronological_draft"}
        story.store.atomic(location / "result-current.json", story.store.dump(result))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(render(args.session, branch=args.branch)))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print("Story rendering failed; committed branch retained", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
