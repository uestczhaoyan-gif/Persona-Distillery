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
    for command in ("doctor", "context", "search"):
        child = commands.add_parser(command)
        child.add_argument("name", help="Exact alias or stable ID")
        child.add_argument("--preview", action="store_true", help="Use existing authorized historical-public preview")
        if command == "context":
            child.add_argument("--max-chars", type=int, default=100000)
        if command == "search":
            child.add_argument("query")
            child.add_argument("--top-k", type=int, default=6)
            child.add_argument("--kind", choices=("evidence", "context_memory"))
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = {"ok": True, "personas": list_personas(args.root)}
        elif args.command == "doctor":
            result = doctor_persona(args.name, args.root, args.preview)
        elif args.command == "context":
            result = compile_context(args.name, args.root, args.preview, args.max_chars)
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
