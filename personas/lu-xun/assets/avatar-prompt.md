# 风格化头像生成规范：鲁迅

| 项目 | 选择 |
| --- | --- |
| 风格 | 现代编辑插画式半身人物肖像 |
| 姿势和表情 | 轻微 3/4 侧身，安静直视，清醒而克制 |
| 可核验识别特征 | 中年东亚男性、短而直立的深色头发、较宽前额、浓直眉、短须；20 世纪 30 年代深色中式长衫 |
| 唯一道具 | 手持一支普通钢笔；不出现文字 |
| 色彩 | 暖白、墨黑、暗红、低饱和青绿 |
| 用途 | 人物卡、对话头像、未来圆桌座位 |
| 母版 | `avatar.png`，1024 × 1024，sRGB PNG |

## 最终生成提示词

```text
Use case: stylized-concept
Asset type: reusable character-card avatar for a conversation and roundtable application
Primary request: a respectful, clearly stylized editorial illustration portrait representing Chinese writer Lu Xun, not a photorealistic reconstruction.
Subject: middle-aged Lu Xun with short upright black hair, a broad forehead, strong straight eyebrows, alert restrained eyes, and his recognizable neat short moustache, wearing a plain dark 1930s Chinese changshan, holding one ordinary fountain pen as the only prop
Style/medium: modern editorial character illustration, subtle stylization, clean readable silhouette, cohesive with a collection representing people across eras and professions
Composition/framing: square 1:1, centered head-and-shoulders portrait, gentle three-quarter view, generous margin, readable at 64 pixels
Scene/backdrop: flat warm off-white background with one restrained muted teal geometric field, no study-room scene
Lighting/mood: lucid, observant, humane, quietly incisive, never menacing or theatrical
Color palette: warm off-white, ink black, muted brick red, low-saturation teal
Constraints: no text, no calligraphy, no watermark, no logo, no photorealism, no cigarette, no blood, no political symbols, one subtle prop at most
Avoid: caricatured ethnicity, angry scowl, mocking exaggeration, specific anime franchise style, revolutionary-poster effects, cluttered background
```

## 统一生成参数

- 生成网站：ChatGPT 生图网站
- 界面模型标签：`gpt2`
- 画幅：竖图，`1024`
- 质量：标准
- 格式：PNG
- 背景：自动
- 审核：宽松
- 开源：项目所有者允许随人物包开源；平台条款与第三方权利仍需发布前复核

人物头像虽使用统一竖图生成设置，最终人物包母版仍应人工裁切为 1024 × 1024 的正方形 PNG，并检查 64 像素缩略图辨识度。

