# 风格化头像生成规范：庄子

头像只代表项目中的庄子人物视角，不声称还原历史庄周真实相貌。全项目统一采用 `现代编辑插画式半身人物肖像`；完整规范见 [`../../../docs/00-视觉规范.md`](../../../docs/00-视觉规范.md)。

| 项目 | 选择 |
| --- | --- |
| 风格 | `现代编辑插画式半身人物肖像（全项目统一）` |
| 姿势和表情 | `轻微 3/4 侧身，眼神安静而带一点若有所悟的笑意，不作神仙或玄学大师姿态` |
| 时代识别物 | `战国时期朴素宽衣轮廓；不使用蝴蝶翅膀、仙鹤、光环等直白神化符号` |
| 色彩 | `墨黑、灰绿、低饱和天青、少量柔和赭黄` |
| 用途 | `人物选择器、人物卡、未来圆桌座位` |
| 生图平台设置 | `ChatGPT 生图网站；界面模型标签 gpt2；竖图 1024；标准质量；PNG；背景自动；审核宽松` |
| 母版 | 下载原图保存为 `avatar-original.png`；审核后裁切 `avatar.png`，`1024 × 1024`，sRGB |
| 界面版 | 可从母版导出 `avatar.webp`；保留 PNG 母版 |

## 生成提示词

```text
Use case: stylized-concept
Asset type: character-card and roundtable avatar
Primary request: a respectful, clearly stylized editorial illustration portrait representing Zhuangzi as a playful and penetrating thinker, not a photorealistic or historically certain likeness.
Subject: an adult East Asian scholar with a quiet alert gaze and a restrained half-smile, gentle three-quarter pose, wearing a simple loose Warring-States-period-inspired robe silhouette; historical facial appearance is unknown, so keep facial details non-specific and dignified
Style/medium: modern editorial character illustration, subtly stylized, clean readable silhouette, cohesive with a unified collection of people across eras and professions
Composition/framing: centered head-and-shoulders or bust portrait, generous clean margin, readable at 64 pixels
Scene/backdrop: minimal softly textured abstract background with one open curved shape suggesting spacious movement, not a literal landscape
Lighting/mood: calm, curious, lightly humorous, free of mystical spectacle
Color palette: ink black, muted gray-green, low-saturation sky blue, a small soft ochre accent
Constraints: no text, no watermark, no logo, no photorealism, no fabricated historical event, no deity halo, no propaganda symbolism
Avoid: butterfly wings, swarms of butterflies, immortal robes, crane imagery, magic aura, exaggerated beard, imperial headwear, caricatured ethnicity, specific anime franchise style, cluttered background
```

生成后建立 `avatar-metadata.md`，记录平台、界面模型标签、日期、原图尺寸、人工审核和项目所有者的开源授权确认。
