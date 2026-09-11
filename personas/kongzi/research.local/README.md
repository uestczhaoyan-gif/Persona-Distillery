# 孔子本地研究增强层

本目录用于保存依法取得、但不能随开源人物包分发的现代校注、译本、论文、扫描件和逐章研究笔记。古籍原文本身进入公版，不代表现代整理、标点、翻译、数据库或扫描图像可以任意再分发。除本说明和 `overlay.example.json` 外，内容均被 Git 忽略。完整规范见 [`../../../人物蒸馏/06-受保护材料处理规范.md`](../../../人物蒸馏/06-受保护材料处理规范.md)。

```powershell
python 人物蒸馏/scripts/init_local_research.py personas/kongzi
```

默认只允许本地处理。不要把 `raw/` 交给 Codex、Claude Code 或远程 API；经逐源审核的原创摘要可标为 `agent_summary`，并通过 `export_agent_overlay.py` 生成过滤后的会话记忆。
