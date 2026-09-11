# 本地研究增强层

本目录用于依法取得、但不能随开源人物包分发的材料。除本说明和 `overlay.example.json` 外，内容均被 Git 忽略。先阅读 [`../../../人物蒸馏/06-受保护材料处理规范.md`](../../../人物蒸馏/06-受保护材料处理规范.md)，再运行：

```powershell
python 人物蒸馏/scripts/init_local_research.py personas/<person-id>
```

默认只允许本地处理。不要把 `raw/` 交给 Codex、Claude Code 或远程 API；经逐源审核的原创摘要可标为 `agent_summary`，并通过 `export_agent_overlay.py` 生成过滤后的会话记忆。
