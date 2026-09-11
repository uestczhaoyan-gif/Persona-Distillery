# 风格化头像生成规范：孔子

头像的任务是让用户迅速识别人物，不是制造一张足以被误认为真人的新肖像。全项目统一采用 `现代编辑插画式半身人物肖像`；它可以轻度卡通化，但不采用夸张 Q 版、特定动漫流派或照片写实风。界面配文写"风格化人物图"。完整规范见 [`../../../docs/00-视觉规范.md`](../../../docs/00-视觉规范.md)。

| 项目 | 选择 |
| --- | --- |
| 风格 | `现代编辑插画式半身人物肖像（全项目统一）` |
| 姿势和表情 | `轻微 3/4 侧身，像在认真听学生说话；温和而有标准，不作威严帝王姿态` |
| 时代识别物 | `参考春秋时期朴素深衣轮廓；最多一卷简册，不用帝王冠冕、牌位或祭祀场景` |
| 色彩 | `墨黑、灰白、低饱和青绿、少量赭红` |
| 用途 | `人物选择器、人物卡、圆桌座位` |
| 生图平台设置 | `ChatGPT 生图网站；界面模型标签 gpt2；竖图 1024；标准质量；PNG；背景自动；审核宽松` |
| 母版 | 下载原图保存为 `avatar-original.png`；审核后裁切 `avatar.png`，`1024 × 1024`，sRGB |
| 界面版 | 可从母版导出 `avatar.webp`；保留 PNG 母版 |

## 生成提示词（填写后再生成）

```text
Use case: stylized-concept
Asset type: character-card and roundtable avatar
Primary request: a respectful, clearly stylized editorial illustration portrait representing Confucius as an attentive teacher, not a photorealistic or historically certain likeness.
Subject: an older East Asian scholar with a calm attentive gaze, subtle three-quarter pose as if listening to a student, simple Spring-and-Autumn-period-inspired robe silhouette; historical appearance is uncertain, so avoid presenting speculative facial details as fact
Style/medium: modern editorial character illustration, subtly stylized, clean silhouette, consistent with a unified historical-and-contemporary character-card collection
Composition/framing: centered head-and-shoulders portrait, readable at small size, generous clean margin
Lighting/mood: thoughtful, warm, approachable
Color palette: charcoal black, soft gray-white, muted celadon green, a restrained cinnabar accent
Constraints: no text, no watermark, no brand marks, no photorealism, no fabricated historical event, no temple altar, no political or religious propaganda symbolism
Avoid: caricatured ethnicity, exaggerated beard, imperial crown, deity halo, kneeling disciples, moralizing poster composition, political endorsement
```

生成后应人工检查：小尺寸可辨识、没有文字错误、不含冒犯性符号、不会被误认作真实照片。另建 `avatar-metadata.md` 记录平台、界面模型标签、日期、原图尺寸和项目所有者的开源授权确认。
