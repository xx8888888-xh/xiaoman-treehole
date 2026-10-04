# Live2D 结构化手术报告 · Hiyori

## 手术前（before）
- Expressions: **无**
- Motions 组: Idle(9个)、TapBody(1个)
- 参数: 70 个（Hiyori.cdi3.json）

## 手术后（after）
### 新增表情（web/assets/models/hiyori/expressions/）
| 表情文件 | 情绪 | 关键参数 |
|---|---|---|
| emo_happy.exp3.json | 开心 | 眯眼笑+嘴角上扬+脸红 |
| emo_sad.exp3.json | 难过 | 眉下垂困扰型+嘴瘪+视线垂 |
| emo_gentle.exp3.json | 温柔安慰 | 半眯眼+浅笑+头微偏 |
| emo_surprised.exp3.json | 惊讶 | 睁大眼+张嘴+抬头 |
| emo_shy.exp3.json | 害羞 | 脸红满格+歪头 |
| emo_neutral.exp3.json | 归零复位 | 全参数复位 |

### 动作组重映射（复用原 motion 文件，未新增二进制）
| 组名 | 来源 |
|---|---|
| Idle | motions/Hiyori_m01.motion3.json、motions/Hiyori_m02.motion3.json、motions/Hiyori_m03.motion3.json、motions/Hiyori_m05.motion3.json、motions/Hiyori_m06.motion3.json、motions/Hiyori_m07.motion3.json、motions/Hiyori_m08.motion3.json、motions/Hiyori_m09.motion3.json、motions/Hiyori_m10.motion3.json |
| TapBody | motions/Hiyori_m04.motion3.json |
| Greeting | motions/Hiyori_m04.motion3.json |
| Nod | motions/Hiyori_m02.motion3.json |
| Shake | motions/Hiyori_m03.motion3.json |
| HappyJump | motions/Hiyori_m04.motion3.json |

### 情绪→表演 联动协议（API 结构化输出）
```json
{"reply": "文本", "emotion": "happy|sad|gentle|surprised|shy|neutral",
  "motion": "Greeting|Nod|Shake|HappyJump|null"}
```
前端 js/stage.js 消费该协议：expression(name) + motion(group) + 口型（TTS播放时 ParamMouthOpenY 振荡）。

### 回滚方式
`cp web/assets/models/hiyori/Hiyori.model3.json.orig web/assets/models/hiyori/Hiyori.model3.json`
