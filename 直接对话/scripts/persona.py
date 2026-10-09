#!/usr/bin/env python3
"""Local persona inspection, context compilation and lexical memory audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from persona_memory import compile_context, doctor_persona, search_memory
from resolve_persona import ROOT, list_personas, resolve_persona


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List package status without loading memories")
    for command in ("doctor", "context", "search", "chat"):
        child = commands.add_parser(command)
        child.add_argument("name", help="Exact alias or stable ID")
        child.add_argument("--preview", action="store_true", help="Use existing authorized historical-public preview")
        if command in {"context", "chat"}:
            child.add_argument("--max-chars", type=int, default=100000)
            child.add_argument("--prompt-profile", choices=("current", "m2-v1"), default="current",
                               help="Opt-in unvalidated dialogue candidate; default stays current")
        if command == "chat":
            child.add_argument("--provider", choices=("ollama",), required=True)
            child.add_argument("--model", required=True, help="Installed local model")
            child.add_argument("--base-url", default="http://127.0.0.1:11434")
            child.add_argument("--timeout", type=float, default=60)
            child.add_argument("--num-ctx", type=int, default=32768)
            child.add_argument("--max-output-tokens", type=int, default=1600)
            child.add_argument("--prompt", help="Single turn; otherwise interactive")
            child.add_argument("--resume", help="Resume an explicitly saved summary")
        if command == "search":
            child.add_argument("query")
            child.add_argument("--top-k", type=int, default=6)
            child.add_argument("--kind", choices=("evidence", "context_memory"))
    args = parser.parse_args(argv)
    try:
        if args.command == "chat":
            from chat_runtime import ChatSession, interactive
            from ollama_provider import OllamaProvider
            # Admission precedes even the metadata request.
            resolve_persona(args.name, args.root, args.preview)
            provider = OllamaProvider(args.model, args.base_url, args.timeout, args.num_ctx, args.max_output_tokens)
            provider.prepare()
            session = ChatSession(args.name, provider, args.root, args.preview, args.max_chars,
                                  prompt_profile=args.prompt_profile)
            if args.prompt_profile != "current":
                print("精简候选 m2-v1 · 尚未通过真实质量评测", file=sys.stderr)
            if args.resume:
                session.resume_summary(args.resume)
            print(f"人物视角模拟 · {session.compiled['display_name']} · {provider.model} · 数据发送至 {provider.base_url} · 默认不保存对话", file=sys.stderr)
            if args.prompt is not None:
                print(session.send(args.prompt))
            else:
                interactive(session)
            return 0
        if args.command == "list":
            result = {"ok": True, "personas": list_personas(args.root)}
        elif args.command == "doctor":
            result = doctor_persona(args.name, args.root, args.preview)
        elif args.command == "context":
            result = compile_context(args.name, args.root, args.preview, args.max_chars,
                                     prompt_profile=args.prompt_profile)
        else:
            result = search_memory(args.name, args.query, args.root, args.preview, args.top_k, args.kind)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
