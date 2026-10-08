#!/usr/bin/env python3
"""Interactive private v2 preview, on the user's local terminal only."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from local_chat import LocalChatSession

BANNER = (
    "本地人物视角模拟，不是本人或本人授权的真实发言。\n"
    "资料与回答仅限本地使用，请勿上传、公开传播或冒充本人。\n"
    "本程序只连接回环 Ollama；请确认服务不转发请求、终端不录制或同步。\n"
    "会话不保存；/new 清空本轮记忆，/exit 退出。"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="External local directory containing manifest.v2.json")
    parser.add_argument("--model", required=True, help="Installed local Ollama model")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--preview", action="store_true", help="Explicit builder preview of a draft/disabled capability")
    parser.add_argument("--confirm-local-storage", action="store_true", help="Confirm this library is not backed up or synced remotely")
    parser.add_argument("--confirm-local-model", action="store_true", help="Confirm the local service is trusted and never forwards requests")
    parser.add_argument("--max-chars", type=int, default=100000)
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    # Keep shell-history prompts and redirected output out of this private path.
    # A terminal can still be recorded externally; this is not DRM or a sandbox.
    if not sys.stdin.isatty() or not sys.stdout.isatty() or not sys.stderr.isatty():
        print("请在本机交互终端运行；私人入口不接受管道输入或重定向输出。", file=sys.stderr)
        return 1
    print(BANNER, file=sys.stderr)
    try:
        session = LocalChatSession(args.package, model=args.model, base_url=args.base_url,
                                   confirm_local_storage=args.confirm_local_storage,
                                   confirm_local_model=args.confirm_local_model, preview=args.preview,
                                   max_chars=args.max_chars, num_ctx=args.num_ctx, timeout=args.timeout)
        while True:
            try:
                question = input("你 > ").strip()
            except EOFError:
                return 0
            if question == "/exit":
                return 0
            if question == "/new":
                session.reset()
                print("已清空本轮记忆。")
                continue
            if not question:
                continue
            if question.startswith("/"):
                print("可用命令：/new、/exit。本入口不保存或导出会话。")
                continue
            try:
                print(session.send(question))
            except ValueError as error:
                print(f"本轮未完成：{error}", file=sys.stderr)
            except OSError:
                print("本地文件或服务访问失败；本轮未保存，请检查本机环境。", file=sys.stderr)
    except KeyboardInterrupt:
        return 0
    except ValueError as error:
        print(f"无法启动本地会话：{error}", file=sys.stderr)
        return 1
    except OSError:
        print("无法访问本地包或模型服务，请检查本机配置。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
