# 风格化头像生成规范：`<人物名称或代号>`

头像的任务是让用户迅速识别人物，不是制造一张足以被误认为真人的新肖像。全项目统一采用 `现代编辑插画式半身人物肖像`；它可以轻度卡通化，但不采用夸张 Q 版、特定动漫流派或照片写实风。界面配文写"风格化人物图"。完整规范见 [`../../../docs/00-视觉规范.md`](../../../docs/00-视觉规范.md)。

| 项目 | 选择 |
| --- | --- |
| 风格 | `现代编辑插画式半身人物肖像（全项目统一）` |
| 姿势和表情 | `<例如：侧身思考，轻微微笑>` |
| 时代识别物 | `<服饰或极少量道具；避免刻板化>` |
| 色彩 | `<2--4 个主色>` |
| 用途 | `人物选择器、人物卡、圆桌座位` |
| 母版 | `avatar.png`，`1024 × 1024`，sRGB |
| 界面版 | 可从母版导出 `avatar.webp`；保留 PNG 母版 |

## 生成提示词（填写后再生成）

```text
Use case: stylized-concept
Asset type: character-card and roundtable avatar
Primary request: a respectful, clearly stylized editorial illustration portrait representing <人物名称>, not a photorealistic likeness.
Subject: <外貌和时代特征；只保留有史料基础的特征>
Style/medium: modern editorial character illustration, subtly stylized, clean silhouette, consistent with a unified historical-and-contemporary character-card collection
Composition/framing: centered head-and-shoulders portrait, readable at small size, generous clean margin
Lighting/mood: thoughtful, warm, approachable
Color palette: <颜色>
Constraints: no text, no watermark, no brand marks, no photorealism, no fabricated historical event or propaganda symbolism
Avoid: caricatured ethnicity, mocking exaggeration, political endorsement
```

生成后应人工检查：小尺寸可辨识、没有文字错误、不含冒犯性符号、不会被误认作真实照片。保留最终提示词和生成日期。
