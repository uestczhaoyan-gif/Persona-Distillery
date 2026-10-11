"""Render accepted local specification views. Does not publish or create a persona package."""
from __future__ import annotations

import html
import re

from candidate_records import fingerprint, text
from spec_candidate import verify_spec, spec_material


def escape(value: str) -> str:
    # Render model/human text as literal prose, never active images, links or HTML.
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#!|>+\-.])", r"\\\1", value).replace("\r\n", "\n").replace("\r", "\n")


def references(item):
    return "依据：" + "、".join(item["record_ids"]) + "。"


def claim(item):
    return escape(item["description"]) + "\n\n" + references(item) + "局限：" + escape(item["limitations"]) + "\n"


def render_views(verified_review: dict, catalog: dict, display_name: str) -> dict[str, str]:
    """Caller must use read_spec_review and read_merge before calling this local-only helper."""
    name = escape(text(display_name, limit=120))
    result = verified_review.get("result", {})
    if (result.get("status") != "review_recorded" or result.get("action") not in {"accept", "edit"}
            or result.get("publication_approved") is not False or result.get("persona_generated") is not False):
        raise ValueError("An accepted local spec review is required")
    candidate = result.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError("No accepted spec candidate")
    raw = verify_spec(candidate, catalog)
    records = spec_material(catalog)
    documents = {}
    documents["00-profile.md"] = f"# {name} · 人物档案草稿\n\nAI 人物视角模拟；本地受限草稿，尚未完成独立质量验收。\n\n" + claim(raw["profile"])
    documents["00-character-card.md"] = f"# {name} · 人物卡草稿\n\n" + claim(raw["profile"])
    lines = [f"# {name} · 对话规范草稿", "", "这是 AI 人物视角模拟，不是本人。以下是可选择的倾向，不是固定回答步骤。",
             "按用户此刻的问题调整长短、节奏和语气；日常对话直接回应，依据编号仅供审核或追问出处时使用。",
             "不伪造经历、原话、私人关系或身后真实意见；新情境的推理不等于人物曾经表达的立场。", "",
             "## 人物核心", "", claim(raw["profile"])]
    for key, title in (("attention", "关注点"), ("judgment", "判断倾向"), ("dialogue_style", "表达节奏"),
                       ("knowledge_limits", "知识与解释边界")):
        lines.extend([f"## {title}", ""])
        lines.extend(claim(item) for item in raw[key])
        if not raw[key]:
            lines.append("材料尚不足，不补写固定性格规则。\n")
    lines.extend(["## 语言模式", "", raw["language"]["mode"], "", escape(raw["language"]["adjustment"]),
                  "", references(raw["language"]), "", "## 待补材料", ""])
    lines.extend("- " + escape(gap) for gap in raw["gaps"])
    documents["03-persona-spec.md"] = "\n".join(lines).rstrip() + "\n"
    lines = [f"# {name} · 核心锚点草稿", "", "锚点用于选择注意力和行动，不要求逐条执行。", ""]
    for number, anchor in enumerate(raw["anchors"], 1):
        lines.extend([f"## A-{number:02d} {escape(anchor['name'])}", "", "触发情境：" + escape(anchor["trigger"]), "",
                      "可尝试的动作：" + escape(anchor["move"]), "", "误用边界：" + escape(anchor["misuse"]), "",
                      references(anchor), ""])
    if not raw["anchors"]:
        lines.append("材料不足，尚未提炼可靠锚点。")
    documents["03-core-anchors.md"] = "\n".join(lines).rstrip() + "\n"
    lines = [f"# {name} · 原创模拟示例", "", "以下不是人物原话、真实会话或独立评测结果；只供理解节奏，不是标准答案。", ""]
    for number, example in enumerate(raw["examples"], 1):
        lines.extend([f"## 示例 {number}（原创模拟）", "", "用户：" + escape(example["user"]), "",
                      "模拟回答：" + escape(example["assistant"]), "", references(example), ""])
    if not raw["examples"]:
        lines.append("尚无经此轮审核采纳的原创示例。")
    documents["03-dialogue-examples.md"] = "\n".join(lines).rstrip() + "\n"
    evidence, memories = ["# 已审核证据（本地草稿）", ""], ["# 已审核情境记忆（本地草稿）", ""]
    for item in records:
        record = item["record"]
        if item["kind"] == "evidence":
            evidence.extend([f"## {item['record_id']}", ""])
            for key, label in (("card_type", "类型"), ("claim", "主张"), ("evidence_summary", "依据摘要"),
                               ("interpretation", "解释"), ("limitations", "局限"), ("confidence", "置信度")):
                evidence.extend([label + "：" + escape(record[key]), ""])
            for source in record["source_refs"]:
                evidence.extend(["来源：" + source["source_id"] + "；定位：" + escape("；".join(source["locations"])) +
                                 "；片段：" + "、".join(source["chunk_ids"]), ""])
        else:
            memories.extend([f"## {item['record_id']} {escape(record['title'])}", ""])
            for key, label in (("period", "时期"), ("situation", "情境"), ("observed_move", "可观察动作"),
                               ("tension", "张力"), ("dialogue_use", "迁移用法"), ("confidence", "置信度")):
                memories.extend([label + "：" + escape(record[key]), ""])
            memories.extend(["来源：" + "、".join(record["source_ids"]), ""])
    documents["02-evidence.md"] = "\n".join(evidence).rstrip() + "\n"
    documents["08-context-memory.md"] = "\n".join(memories).rstrip() + "\n"
    documents["04-evaluation.md"] = "# 验收待办\n\n真实模型试聊、独立题、人物辨识度和独立人工评审均未完成。不能将原创示例算作盲评结果。\n"
    documents["05-dialogue-log.md"] = "# 会话记录\n\n未保存会话。\n"
    documents["09-usage-example.md"] = "# 使用样例待补\n\n尚无真实试聊。原创模拟示例见 03-dialogue-examples.md，不能作为真实运行结果。\n"
    documents["06-build-report.md"] = ("# 本地构建审阅视图\n\n状态：草稿；非完整人物包；禁止公开传播。\n\n"
        + "规范候选哈希：" + fingerprint(candidate) + "\n\n目录哈希：" + fingerprint(catalog)
        + f"\n\n有效已审记录：{len(records)}；头像：未完成；真实模型质量与独立人工评审：未验收。\n"
        + "\n已记录明确人工决定，但程序不验证审核者身份或独立性。此视图不开放任何运行能力。\n")
    return documents
