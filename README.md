# 人物蒸馏实验室 / Persona Distillation Lab

基于可追溯证据的人物视角研究与对话系统。它整理公开材料中的知识、语言、价值取向和决策方法，帮助模型以某人物的研究型视角思考；不冒充人物本人，也不替人物表达现实立场。

An evidence-grounded research and dialogue system for character perspectives. It organizes knowledge, language, values, and decision methods from public sources so an AI can reason through a research-based persona perspective. It does not impersonate a real person or represent their current views.

## 当前版本 / Current release

`v0.1.0` 研究预览 / research preview. 已包含四个人物包：孔子、庄子、鲁迅、理查德·费曼。人物包目前仍是 `draft/private`，Agent 直读和结构化资料可用于维护者本地预览；独立 CLI、Web、圆桌运行时、故事分支和语音仍在开发中。

Includes four persona packages: Confucius, Zhuangzi, Lu Xun, and Richard Feynman. Packages are still `draft/private`; Agent direct-read mode and structured research data are available for local maintainer preview. CLI, Web, roundtable runtime, story branching, and voice output are not implemented yet.

## 快速开始 / Quick start

在 Codex 或其他能读取工作区文件的 Agent 中打开仓库根目录：

Open the repository root in Codex or another workspace-aware Agent:

```text
/孔子 我读了很多书，却还是不会判断事情。
```

其他入口 / Other entries: `/庄子`、`/鲁迅`、`/费曼`、`/persona kongzi`、`人物 孔子`。若客户端拦截斜杠命令，使用 `人物 孔子`。这些是项目文本入口，不是客户端内置命令。

Other entries: `/庄子`, `/鲁迅`, `/费曼`, `/persona kongzi`, or `人物 孔子`. If the host intercepts slash commands, use `人物 孔子`. These are project conventions, not built-in client commands.

入口规则、预览授权和切换方式见 [直接对话/使用方法](直接对话/使用方法.md) 与 [命令入口](直接对话/命令入口.md)。

See [direct dialogue usage](直接对话/使用方法.md) and [persona commands](直接对话/命令入口.md) for routing, preview authorization, and switching.

## 项目结构 / Project structure

- `personas/`：人物来源、证据、情境记忆、评测和索引 / sources, evidence, context memory, evaluations, and indexes.
- `人物蒸馏/`：来源处理协议、Schema 和构建工具 / source processing protocols, schemas, and build tools.
- `直接对话/`：人物快捷入口、Agent 契约和未来 CLI 设计 / persona routing, Agent contract, and future CLI design.
- `圆桌会议/`、`故事推演/`、`定题演讲/`：后续功能协议和模板 / protocols and templates for later features.
- `docs/`：治理、架构、视觉和贡献指南 / governance, architecture, visual rules, and contribution guide.

详见 [项目结构与贡献指南](docs/03-项目结构与贡献指南.md)。

See [project structure and contribution guide](docs/03-项目结构与贡献指南.md).

## 设计边界 / Boundaries

- 重要事实、原话和经历必须可回溯到来源；材料不足时承认不确定性。
- Historical facts, quotations, and experiences must remain traceable; uncertainty is stated when evidence is insufficient.
- 公开薄包不包含受版权保护的原文、私密上传、未脱敏聊天或 API 密钥。
- Public packages exclude copyrighted full text, private uploads, unredacted chats, and API keys.
- 默认只读、不开启网络；需要当前事实核验时才建立临时事实简报。
- Default operation is read-only with no web request; current facts are checked only when needed.

## 验证 / Verification

```powershell
python -m unittest discover -s tests -v
python 直接对话/scripts/resolve_persona.py --list
```

人物结构化记忆校验需要 NumPy 环境，命令见各人物构建报告。

Structured-memory validation requires an environment with NumPy; see each persona build report for the command.

## 许可证 / License

仓库当前尚未选择统一许可证。发布到 GitHub 前请由项目所有者确定代码、文档、人物资料和图片分别适用的许可证；在此之前请勿将仓库标记为可自由再利用。

This repository does not yet have a single chosen license. Before publishing on GitHub, the project owner should decide licenses for code, documentation, persona data, and images separately. Until then, do not present the repository as freely reusable.

English version: [README.en.md](README.en.md)
License: [MIT](LICENSE) for project code and original documentation; persona materials and images may have separate rights.

