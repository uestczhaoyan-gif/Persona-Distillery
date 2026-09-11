# 风格化头像生成规范：理查德·费曼

| 项目 | 选择 |
| --- | --- |
| 风格 | 现代编辑插画式半身人物肖像 |
| 姿势和表情 | 轻微 3/4 侧身，专注而带一点好奇的神情 |
| 可核验识别特征 | 中年男性、后梳深棕发、较高前额、鲜明眉眼；20 世纪中期衬衫与深色外套 |
| 唯一道具 | 手持一支粉笔；不画公式文字 |
| 色彩 | 暖白、炭黑、暗红、低饱和青绿 |
| 用途 | 人物卡、对话头像、未来圆桌座位 |
| 母版 | `avatar.png`，1024 × 1024，sRGB |

## 最终生成提示词

```text
Use case: stylized-concept
Asset type: reusable character-card avatar for a conversation and roundtable application
Primary request: a respectful, clearly stylized editorial illustration portrait representing physicist Richard Feynman, not a photorealistic likeness.
Subject: middle-aged Richard Feynman with swept-back dark brown hair, a high forehead, alert expressive eyes, wearing a simple mid-20th-century white shirt and charcoal jacket, holding one small piece of chalk as the only prop
Style/medium: modern editorial character illustration, subtle stylization, clean readable silhouette, cohesive with a collection representing people across eras and professions
Composition/framing: square 1:1, centered head-and-shoulders portrait, gentle three-quarter view, generous margin, readable at 64 pixels
Scene/backdrop: flat warm off-white background with one restrained muted teal geometric field, no classroom scene
Lighting/mood: thoughtful, curious, energetic but not theatrical
Color palette: warm off-white, charcoal, muted brick red, low-saturation teal
Constraints: no text, no equations, no watermark, no logo, no photorealism, no Nobel medal, no atomic bomb imagery, no bongos, one subtle prop at most
Avoid: caricatured ethnicity, mocking exaggeration, specific anime franchise style, genius-aura effects, cluttered background
```

生成日期与审核结论在 `../06-build-report.md` 中记录；平台、模型界面标签、生成参数和发布授权见 `avatar-metadata.md`。
