#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live2D 结构化手术脚本 · Hiyori 模型
=====================================
原始状态：无 Expressions，动作组仅 Idle/TapBody（Hiyori_m01~m10）
手术内容：
  S1 生成 6 个表情文件（exp3.json）: happy/sad/gentle/surprised/shy/neutral
  S2 重写 Hiyori.model3.json：
     - 注册 Expressions 数组（情绪标签 → 表情文件）
     - 动作组重映射：Greeting/Nod/Shake/HappyJump 复用原 motion 文件
     - 保留原 Idle/TapBody 不动（向后兼容）
  S3 输出手术报告 docs/live2d_surgery.md（含 before/after 对照）
参数依据：Hiyori.cdi3.json（70 个参数，标准 Cubism 命名）
"""
import json, os, shutil

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_DIR = os.path.join(ROOT, "web", "assets", "models", "hiyori")
DOC = os.path.join(ROOT, "docs", "live2d_surgery.md")

# ---------- S1: 表情定义 ----------
# 参数语义（Cubism 标准）：MouthForm 1笑/-1瘪；EyeSmile 1眯眼笑；BrowY 上扬/下垂；
# BrowForm 0正常/2困扰；Cheek 脸红；EyeOpen <1半闭；AngleZ 头部倾斜
EXPRESSIONS = {
    "happy":     [("ParamEyeLSmile",1.0),("ParamEyeRSmile",1.0),("ParamMouthForm",1.0),
                  ("ParamBrowLY",0.6),("ParamBrowRY",0.6),("ParamCheek",0.7)],
    "sad":       [("ParamEyeLOpen",0.65),("ParamEyeROpen",0.65),("ParamMouthForm",-0.9),
                  ("ParamBrowLY",-0.9),("ParamBrowRY",-0.9),("ParamBrowLForm",2.0),("ParamBrowRForm",2.0),
                  ("ParamAngleZ",-4)],
    "gentle":    [("ParamEyeLSmile",0.55),("ParamEyeRSmile",0.55),("ParamMouthForm",0.45),
                  ("ParamBrowLY",0.25),("ParamBrowRY",0.25),("ParamAngleZ",3)],
    "surprised": [("ParamEyeLOpen",1.35),("ParamEyeROpen",1.35),("ParamMouthOpenY",0.85),
                  ("ParamBrowLY",1.0),("ParamBrowRY",1.0),("ParamAngleY",6)],
    "shy":       [("ParamEyeLSmile",0.8),("ParamEyeRSmile",0.8),("ParamMouthForm",0.3),
                  ("ParamCheek",1.0),("ParamAngleZ",-6),("ParamBodyAngleZ",-2)],
    "neutral":   [("ParamEyeLOpen",1.0),("ParamEyeROpen",1.0),("ParamMouthForm",0.0),
                  ("ParamBrowLY",0.0),("ParamBrowRY",0.0),("ParamCheek",0.0)],
}

def exp3(params):
    return json.dumps({
        "Type": "Live2D Expression",
        "Parameters": [{"Id": p, "Value": v, "Blend": "Overwrite"} for p, v in params]
    }, ensure_ascii=False, indent=1)

os.makedirs(os.path.join(MODEL_DIR, "expressions"), exist_ok=True)
for name, params in EXPRESSIONS.items():
    with open(os.path.join(MODEL_DIR, "expressions", f"emo_{name}.exp3.json"), "w") as f:
        f.write(exp3(params))
print(f"S1 完成：{len(EXPRESSIONS)} 个表情文件")

# ---------- S2: 重写 model3.json ----------
m3path = os.path.join(MODEL_DIR, "Hiyori.model3.json")
shutil.copy(m3path, m3path + ".orig")          # 手术前留底
m3 = json.load(open(m3path + ".orig"))
fr = m3["FileReferences"]

fr["Expressions"] = [{"Name": n, "File": f"expressions/emo_{n}.exp3.json"} for n in EXPRESSIONS]

idle_files = [m["File"] for m in fr["Motions"]["Idle"]]
tap_files  = [m["File"] for m in fr["Motions"]["TapBody"]]
fr["Motions"]["Greeting"] = [{"File": tap_files[0], "FadeInTime": 0.3, "FadeOutTime": 0.5}] if tap_files else []
fr["Motions"]["Nod"]      = [{"File": idle_files[1 % len(idle_files)], "FadeInTime": 0.3, "FadeOutTime": 0.4}]
fr["Motions"]["Shake"]    = [{"File": idle_files[2 % len(idle_files)], "FadeInTime": 0.3, "FadeOutTime": 0.4}]
fr["Motions"]["HappyJump"]= [{"File": tap_files[1 % len(tap_files)] if len(tap_files) > 1 else tap_files[0],
                              "FadeInTime": 0.2, "FadeOutTime": 0.5}]

with open(m3path, "w") as f:
    json.dump(m3, f, ensure_ascii=False, indent=1)
print("S2 完成：model3.json 重写（原件存为 .orig）")

# ---------- S3: 手术报告 ----------
motions_map = "\n".join(
    f"| {g} | {'、'.join(m['File'] for m in v)} |" for g, v in fr["Motions"].items())
with open(DOC, "w") as f:
    f.write(f"""# Live2D 结构化手术报告 · Hiyori

## 手术前（before）
- Expressions: **无**
- Motions 组: Idle({len(idle_files)}个)、TapBody({len(tap_files)}个)
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
{motions_map}

### 情绪→表演 联动协议（API 结构化输出）
```json
{{"reply": "文本", "emotion": "happy|sad|gentle|surprised|shy|neutral",
  "motion": "Greeting|Nod|Shake|HappyJump|null"}}
```
前端 js/stage.js 消费该协议：expression(name) + motion(group) + 口型（TTS播放时 ParamMouthOpenY 振荡）。

### 回滚方式
`cp web/assets/models/hiyori/Hiyori.model3.json.orig web/assets/models/hiyori/Hiyori.model3.json`
""")
print("S3 完成：手术报告 → docs/live2d_surgery.md")
