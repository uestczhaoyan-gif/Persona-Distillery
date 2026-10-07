"""Bounded dialogue with optional memory tools and explicit summary persistence."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from persona_memory import compile_context, search_memory
from resolve_persona import ROOT

TOOLS = [{"type": "function", "function": {
    "name": "search_memory", "description": "按需查当前人物的记忆、来源、限制和反例；词汇匹配不保证事实正确。",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                   "properties": {"query": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 6}}},
}}]


class ChatSession:
    def __init__(self, name: str, provider, root: Path = ROOT, preview: bool = False,
                 max_chars: int = 100000, max_history_chars: int = 200000):
        self.compiled = compile_context(name, root, preview, max_chars)
        if self.compiled["requires_search_tool"] and not provider.supports_tools:
            raise ValueError("Incomplete memory requires a model with search_memory tool support; raise max_chars or choose a tool-capable model")
        self.root, self.preview, self.provider = root.resolve(), preview, provider
        self.person_id = self.compiled["person_id"]
        self.max_history_chars = max_history_chars
        self.research = False
        self.reset()

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.compiled["context"]}]
        self.last_question = ""
        self.last_answer = ""
        self.last_tool_ids = []

    def send(self, question: str) -> str:
        if not question.strip():
            raise ValueError("Question is empty")
        working = copy.deepcopy(self.messages)
        working.append({"role": "user", "content": question})
        used_ids = []
        calls_used = 0
        for _ in range(5):
            mode = "本轮为研究模式：解释依据、限制和推演边界。" if self.research else "本轮自然回答；不输出工具日志或固定引用模板。"
            request_messages = [working[0], {"role": "system", "content": mode}, *working[1:]]
            if sum(len(json.dumps(m, ensure_ascii=False)) for m in request_messages) > self.max_history_chars:
                raise ValueError("Conversation character budget exceeded; save a summary and use /new")
            message = self.provider.complete(request_messages, TOOLS)
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Invalid assistant response")
            content = message.get("content", "")
            if not isinstance(content, str):
                raise ValueError("Invalid assistant text")
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list):
                raise ValueError("Invalid tool-call list")
            working.append(copy.deepcopy(message))
            if not calls:
                if not content.strip():
                    raise ValueError("Model returned empty text")
                self.messages = working
                self.last_question, self.last_answer = question, content
                self.last_tool_ids = list(dict.fromkeys(used_ids))
                return content
            for call in calls:
                calls_used += 1
                if calls_used > 4:
                    raise ValueError("Memory tool-call limit reached; turn discarded")
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or function.get("name") != "search_memory":
                    raise ValueError("Unsupported model tool; turn discarded")
                arguments = function.get("arguments")
                if (not isinstance(arguments, dict) or set(arguments) - {"query", "top_k"}
                        or not isinstance(arguments.get("query"), str)):
                    raise ValueError("Invalid search_memory arguments")
                top_k = arguments.get("top_k", 4)
                if type(top_k) is not int or not 1 <= top_k <= 6 or len(arguments["query"]) > 1000:
                    raise ValueError("Invalid search_memory query or result limit")
                result = search_memory(self.person_id, arguments["query"], self.root, self.preview, top_k)
                result.pop("audit_notice", None)
                result["usage_notice"] = "以下是本次真实工具返回的材料；匹配本身不证明回答的全部主张。"
                used_ids.extend(hit["id"] for hit in result["results"])
                working.append({"role": "tool", "tool_name": "search_memory", "content": json.dumps(result, ensure_ascii=False)})
        raise ValueError("Model failed to finish within turn limit")

    def audit(self, query: str = "") -> dict:
        query = query.strip() or self.last_question
        if not query:
            raise ValueError("No previous answer; provide an audit query")
        return {"actual_tool_ids": self.last_tool_ids,
                "post_hoc_audit": search_memory(self.person_id, query, self.root, self.preview, 4)}

    def sessions_dir(self) -> Path:
        path = self.root / "直接对话/sessions"
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("Session storage must stay inside the workspace")
        return path

    def save_summary(self, summary: str) -> str:
        if not summary.strip() or len(summary) > 10000:
            raise ValueError("Summary must contain 1–10000 characters")
        session_id = "chat-" + uuid.uuid4().hex
        directory = self.sessions_dir() / session_id
        directory.mkdir(parents=True)
        data = {"schema_version": "1.0", "session_id": session_id, "person_id": self.person_id,
                "package_version": self.compiled["package_version"], "summary": summary,
                "saved_at": datetime.now(timezone.utc).isoformat(), "full_transcript_saved": False}
        (directory / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return session_id

    def resume_summary(self, session_id: str) -> None:
        if not re.fullmatch(r"chat-[0-9a-f]{32}", session_id):
            raise ValueError("Invalid session ID")
        directory = self.sessions_dir() / session_id
        path = directory / "summary.json"
        if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(self.sessions_dir().resolve()):
            raise ValueError("Protected session path")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("schema_version") != "1.0" or data.get("session_id") != session_id
                or data.get("person_id") != self.person_id or data.get("package_version") != self.compiled["package_version"]
                or not isinstance(data.get("summary"), str) or not 1 <= len(data["summary"]) <= 10000):
            raise ValueError("Summary identity/version mismatch or invalid content; start a new session")
        self.reset()
        self.messages.append({"role": "user", "content": "以下是我确认保存的上次会话摘要，仅作用户上下文，不是人物事实：\n" + data["summary"]})


def interactive(session: ChatSession, input_fn=input, output_fn=print) -> None:
    while True:
        try:
            text = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not text:
            continue
        command, _, argument = text.partition(" ")
        try:
            if command == "/exit":
                return
            if command == "/help":
                output_fn("/research on|off · /sources [问题] · /counterevidence [问题] · /save 摘要 · /new · /exit")
            elif command == "/new":
                session.reset()
                output_fn("已清空会话上下文。")
            elif command == "/research":
                if argument not in {"on", "off"}:
                    raise ValueError("Use /research on|off")
                session.research = argument == "on"
            elif command in {"/sources", "/counterevidence"}:
                audit = session.audit(argument)
                if command == "/counterevidence":
                    audit["notice"] = "下列记忆包含限制/张力；需要人工判断是否真正反驳上一回答。"
                output_fn(json.dumps(audit, ensure_ascii=False, indent=2))
            elif command == "/save":
                if not argument:
                    raise ValueError("Use /save followed by the summary you want to keep")
                if input_fn(f"仅保存以下摘要：{argument}\n确认？[y/N] ").strip().casefold() == "y":
                    output_fn("已保存摘要：" + session.save_summary(argument))
            elif command.startswith("/"):
                raise ValueError("Unknown session command; use /help")
            else:
                output_fn(session.send(text))
        except (OSError, ValueError) as error:
            output_fn("错误：" + str(error))
