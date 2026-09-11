# Persona-Distillery · Persona Distillation Lab

[中文](README.md) | **English**

Turn public source material into research-based persona perspectives with traceable evidence. Organize a person's knowledge, expression, values, and decision methods, then explore the perspectives of Confucius, Zhuangzi, Lu Xun, or Richard Feynman in a workspace-aware AI agent. Ask what supports an answer and where interpretation begins.

These are simulations grounded in research, not the people themselves or statements of their current views.

## What works today

This is a **v0.1.0 research preview** with four persona packages: Confucius, Zhuangzi, Lu Xun, and Richard Feynman.

- Sources, evidence, structured memories, contexts, and evaluation material.
- An agent direct-read workflow for loading material and holding single-persona conversations.
- An alias resolver that produces loading plans and checks persona routes and file paths.

Packages remain draft/private for authorized, read-only local maintainer previews. A standalone CLI, Web service, roundtable runtime, story branching, and voice output are not implemented. Designs and templates are not runnable products.

## Quick start

Open the repository root in an agent that can read workspace files and send:

```text
/persona kongzi I have read many books, but still struggle to make sound judgments.
```

Other entries include /孔子, /庄子, /鲁迅, and /费曼. These are project text conventions, not built-in client commands. If the client intercepts slash commands, use “人物 孔子”. If project instructions are not loaded automatically, ask the agent to read AGENTS.md first.

A first draft preview requires maintainer authorization; existing session or local authorization need not be requested again. Cloning does not inherit another person's authorization. See [usage instructions](直接对话/使用方法.md) and [command routing](直接对话/命令入口.md) for preview, switching, and exit behavior. These detailed guides are currently in Chinese. Conversations use the host agent's model; the loading-plan resolver does not call a model or start a conversation on its own.

## Repository structure

| Directory | Contents |
| --- | --- |
| personas/ | Persona sources, evidence, memories, evaluations, manifests, and indexes |
| 人物蒸馏/ | Source-processing protocols, schemas, and structured-memory tools |
| 直接对话/ | Persona routing, loading-plan resolver, and agent response contract |
| 圆桌会议/, 故事推演/, 定题演讲/ | Protocols, designs, and templates for future capabilities |
| docs/ | Architecture, governance, visual rules, and contribution guidance |

See the [structure and contribution guide](docs/03-项目结构与贡献指南.md) and [release notes](RELEASE.md).

## Evidence and boundaries

- Facts, quotations, and experiences must remain traceable. Distinguish evidence, interpretation, and simulation; state uncertainty when material is insufficient.
- Public packages exclude copyrighted full text, private uploads, unredacted chats, and API keys.
- Operation is read-only by default and does not save full private conversations. Web lookup is used when verification is requested or a factual gap materially affects a judgment.
- A public repository does not change the packages' preview status or grant unrestricted reuse of third-party material and images.

## Development checks

The routing resolver requires Python 3.10+ and uses the standard library. Run from the repository root:

```powershell
python -m unittest discover -s tests -v
python 直接对话/scripts/resolve_persona.py --list
```

Structured-memory building and validation also require NumPy; see individual persona build reports for commands. Passing software checks does not establish dialogue quality, which still needs held-out questions and human evaluation.

## License

Project code and original documentation use the [MIT License](LICENSE). Third-party persona material, quotations, and images may carry separate rights and must be used according to their sources and permissions.
