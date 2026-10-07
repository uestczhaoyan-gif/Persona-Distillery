# Persona-Distillery · Persona Distillation Lab

[中文](README.md) | **English**

Turn public source material into research-based persona perspectives with traceable evidence. Organize a person's knowledge, expression, values, and decision methods, then explore the perspectives of Confucius, Zhuangzi, Lu Xun, or Richard Feynman in a workspace-aware AI agent. Ask what supports an answer and where interpretation begins.

These are simulations grounded in research, not the people themselves or statements of their current views.

## What works today

This is a **v0.2.0 local engineering preview** with four persona packages: Confucius, Zhuangzi, Lu Xun, and Richard Feynman.

- Sources, evidence, structured memories, contexts, and evaluation material.
- An agent direct-read workflow for loading material and holding single-persona conversations.
- An alias resolver that produces loading plans and checks persona routes and file paths.
- A local CLI for package checks, lexical memory search, context compilation, and explicitly selected local Ollama models.
- TXT/Markdown upload preparation with review gates, deduplication, chunking, immutable snapshots, and incremental changes. Evidence synthesis remains a human step.
- An offline roundtable state CLI with request tickets, pause/resume, atomic turn commits, and reviewed article import.

Packages remain draft/private for authorized local maintainer previews. Web service, model-driven roundtable orchestration, story branching, and voice output remain future work. Persona publication and dialogue quality require separate review.

## Quick start

Use Python 3.10+ and run from the repository root. These checks do not call a model:

```powershell
python -m pip install -r requirements.txt
python 直接对话/scripts/persona.py list
python 直接对话/scripts/persona.py doctor kongzi --preview
python 直接对话/scripts/persona.py search kongzi E-0001 --preview --top-k 3
```

Use `--preview` only for an already authorized local historical-public persona preview. With Ollama already running and a local model installed, replace the example model name:

```powershell
python 直接对话/scripts/persona.py chat kongzi --preview --provider ollama --model 'your-local-model'
```

The provider accepts loopback HTTP only and checks model metadata to reject remote models. It does not download models or perform web search. `/research on`, `/sources`, and `/counterevidence` expose the research layer. `/save your-summary` asks for confirmation and saves only that summary; resume with `--resume chat-<saved-id>` for the same persona and package version. Full conversations are not saved by default. See [local tool instructions](直接对话/docs/本地工具.md), [upload preparation](人物蒸馏/08-本地文本准备工具.md), and [roundtable state tools](圆桌会议/11-本地状态工具.md), currently in Chinese.

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
| 人物蒸馏/ | Source protocols, schemas, structured-memory and local upload tools |
| 直接对话/ | Persona routing, agent contract, memory search, and local Ollama CLI |
| 圆桌会议/ | Discussion protocol, offline state tools, and article export |
| 故事推演/, 定题演讲/ | Protocols, designs, and templates for future capabilities |
| docs/ | Architecture, governance, visual rules, and contribution guidance |

See the [contribution guide](docs/03-项目结构与贡献指南.md), [release notes](RELEASE.md), [finite improvement plan](docs/04-完善计划.md), [execution status](PROJECT_STATUS.md), and [acceptance report](docs/06-验收报告.md).

## Evidence and boundaries

- Facts, quotations, and experiences must remain traceable. Distinguish evidence, interpretation, and simulation; state uncertainty when material is insufficient.
- Public packages exclude copyrighted full text, private uploads, unredacted chats, and API keys.
- Operation is read-only by default and does not save full private conversations. Web lookup is used when verification is requested or a factual gap materially affects a judgment.
- A public repository does not change the packages' preview status or grant unrestricted reuse of third-party material and images.

## Development checks

Development dependencies include jsonschema and NumPy. Run from the repository root:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/validate_repository.py
```

GitHub Actions validates Linux/Windows with Python 3.10/3.12. Tests use offline and loopback HTTP stubs. Passing software checks does not establish real-model dialogue quality, which still needs held-out questions and human evaluation. Character budgets are not tokenizer context guarantees.

## License

Project code and original documentation use the [MIT License](LICENSE). Third-party persona material, quotations, and images may carry separate rights and must be used according to their sources and permissions.
