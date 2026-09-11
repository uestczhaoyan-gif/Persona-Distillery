# Persona Distillation Lab

Evidence-grounded persona research and dialogue system with structured memories, traceable sources, and natural character-perspective conversations.

This is the English companion to [README.md](README.md). The project is a `v0.1.0` research preview: it includes four structured persona packages and Agent direct-read workflows. The packages remain `draft/private`; CLI, Web, roundtable, story simulation, and voice runtimes are future work.

## Quick start

Open the repository root in Codex or another workspace-aware Agent and send:

```text
/孔子 我读了很多书，却还是不会判断事情。
```

Other entries: `/庄子`, `/鲁迅`, `/费曼`, `/persona kongzi`, or `人物 孔子`. If the host intercepts unknown slash commands, use `人物 孔子`. These are project conventions, not built-in client commands.

See [direct dialogue usage](直接对话/使用方法.md), [persona commands](直接对话/命令入口.md), and [project structure](docs/03-项目结构与贡献指南.md).

## What is included

- `personas/`: sources, evidence, context memory, evaluations, manifests, and indexes.
- `人物蒸馏/`: source-processing protocols, schemas, and structured-memory tools.
- `直接对话/`: persona routing, Agent contract, safe loading-plan resolver, and future CLI design.
- `圆桌会议/`, `故事推演/`, `定题演讲/`: protocols and templates for later capabilities.
- `docs/`: governance, architecture, visual rules, and contribution guidance.

## Boundaries

Historical facts, quotations, and experiences remain traceable. Public packages do not include copyrighted full text, private uploads, unredacted chats, or API keys. Default operation is read-only and offline; current facts are checked only when explicitly needed. This system presents a research-based perspective and does not impersonate a person or represent their current views.

## Verification

```powershell
python -m unittest discover -s tests -v
python 直接对话/scripts/resolve_persona.py --list
```

See [RELEASE.md](RELEASE.md) for the release scope and [LICENSE](LICENSE) for the project license. MIT covers project code and original documentation; persona materials, quotations, and images may have separate rights.
