# DECISIONS · 语音音色改造（树洞 v7）

> 本文件是本轮「音色改造」的决策记录与实测证据，同时是第三轮「效率整改」要求的时间账与授权清单。
> 所有数字都来自本机实测（`docs/voice-candidates/samples/manifest.json`、`docs/voice-candidates/live/e2e.json`），不是估计值；估计值会标注「估」。

---

## 0. 时间账（本轮）

| 项目 | 数值 |
|---|---|
| 本轮耗时（断电恢复 → 本轮收尾） | ≈ 2 h 20 min（估：模型下载 6 min / 批量合成 25 min / 回听+CER 20 min / 代码+测试 40 min / 端到端与文档 25 min） |
| 合成调用次数 | 本轮 34 次（样音 30 + 端到端 4）；含前几轮调参复测累计 ≈ 60 次 |
| 单次合成成本 | 在线 edge 22 字 1.30 s、29 字 1.23 s、缓存命中 0.037 s；离线 Melo int8 RTF 4.3–5.2×（比在线慢一个数量级） |
| 耗时主项 | ① 离线模型一次性下载（melo 233 MB / kokoro 169 MB / vosk 65 MB / piper 60 MB）；② 批量合成+回听；③ 离线 RTF 实测 |

效率整改执行情况：样音一次批量产出（10 组 × 3 句 = 30 个文件，一轮跑完，不再一个音色一轮）；判据先定后测（见 §2）；决策项自己拍定并记录在此；代码先提交（`a5f5d70`）再补文档。

---

## 1. 现场核查（断电后）

| 检查项 | 结果 |
|---|---|
| `git status` | 8 个已改文件（Dockerfile / TtsController.java / docker-compose.yml / test_tts_post.py / tts_server.py / index.html / tts.js / tts_provider.test.js）+ 未跟踪 `docs/voice-candidates/`；无冲突标记、无半截文件 |
| 当前分支 | `feat/voice-candidates`（独立 worktree `treehole-voice`，主仓 `treehole-ci` 保持 `feat/r7-resilience` 不动） |
| `git log -1` | `8defaf2 test(e2e): 失败态取 total 前先等本轮收尾（busy=false）` |
| `index.lock` | worktree 与主仓均无 |
| `dist/` | 不存在（本仓发布物是 `android/app/src/main/assets/www`，由 `scripts/build_assets.sh` 生成，`assets/www` 不入库） |
| 源 ↔ 产物一致 | 已重跑 `scripts/build_assets.sh`：`ASSETS_MANIFEST files=74 bytes=1392959`，`ASSET_VERIFY PASS`（逐文件 sha256 对比通过） |
| 断电时刻 | Windows 事件：`2026-11-07 08:01:45` 非正常关机（Kernel-Power 41 + EventLog 6008），随后 08:02、08:04 两次重启。这就是上轮中断的时点；工作区未受损，仅未提交改动留在 worktree |

> 主仓上游已走到 `1ccbe9f`（月度回放等 4 个提交）。它们只动了 `web/app.js`、`web/js/api.js`、`web/index.html`（删 `resilience.js` 那一行）等，与本次改动无同行冲突，合并时按常规 merge 即可。

---

## 2. 现状归因：到底哪一层在响，差在哪

**结论：出厂 APK 实际发声的是 Android 系统 TTS，不是 WebView 的 `speechSynthesis`，更不是服务端。**

链路与证据：

1. `web/js/config.js` 默认 `tts: "server"`、`ttsServer: ""`、`voice: "zh-CN-XiaoyiNeural"`；
2. `web/js/tts.js` 的 `createServer()` 只有在 `ttsServer` 非空或注入了 `window.XIAOMAN_TTS_SERVER` 时才 available → 空值时 server 层直接跳过；
3. 于是落到 native 层 `TtsController.java`：`setLanguage(Locale.SIMPLIFIED_CHINESE)`、不指定引擎、不指定 Voice，`tts.setPitch(1.15)` + `setSpeechRate(1.02)`；
4. native 也不可用时才落到 `speechSynthesis`（大多数国产 ROM 的 WebView 本来就转调系统 TTS，音色还是同一个）。

**"差"的三层拆解（按贡献排序）：**

| 层 | 具体 | 量化证据 |
|---|---|---|
| 引擎本身 | 系统默认中文女声（各 ROM 多为拼接式/老式参数合成），没有情感与韵律，天然播报腔 | A1/A2 代理样音中位 F0 208/222 Hz、字速 3.03 字/s、单句 6.4–9.0 s，句尾拖 |
| 参数没调 | `pitch = 1.15` 把基频再抬高 15%，机器腔被抬成"电子音"；`rate 1.02` 又略快，句尾还拖 | pitch 1.15 使中位 F0 从 208 → 222 Hz，p90 从 278 → 303 Hz（更尖） |
| 音频通路 | **无损**。native 走 `TextToSpeech.synthesizeToFile`/PCM 直出，没有重采样或低码率压缩 | 采样率由设备引擎决定（多为 16k/22.05k 单声道 PCM），本次未引入任何转码 |

所以"居然用默认的"不是通路问题，是**引擎 + 参数**双重问题；换引擎收益最大，改参数是零成本兜底。

---

## 3. 可验证判据（先定，后测）

判据在合成前定死，避免"听着差不多"：

| 指标 | 合格区间 | 依据 |
|---|---|---|
| 中位 F0 | 240–320 Hz | 年轻女声常见区间；>340 偏尖/儿童音，<200 偏中性或男声 |
| 语速 | 3.4–4.2 字/s | 日常对话语速；<3.0 拖，>4.8 是播报 |
| 单句时长 | 20–35 字句 5.0–7.5 s | 与语速互证，防止"字少但拖" |
| 静音率 | 0.25–0.45 | 40 ms 帧 RMS<0.01 的占比；过低=黏连，过高=拖沓 |
| CER | ≤ 0.10 | vosk `small-cn` 离线回听，3 句平均；>0.10 说明已听不清 |
| RTF（离线） | ≤ 1.0 | 必须能实时，否则交互不可用 |
| 首包延迟 | 在线 ≤ 2 s / 本地 ≤ 300 ms | 1 句以内不能明显卡顿 |

测量方法：ffprobe 探采样率/码率/时长；16 kHz 单声道解码后按 40 ms 帧/10 ms 跳做自相关基频（只统计浊音帧，peak>0.45）；静音率按 40 ms 帧 RMS；CER 用 vosk 回听对齐原文。
基频为自相关估计，存在 ±1 个倍频的可能（极少数帧会把 300 Hz 判成 150 Hz），故表中给 p10–p90 区间而不是单点。

---

## 4. 候选实测（30 个样音，同一段 3 句）

同一样本：`1_greet`（22 字）/ `2_comfort`（29 字）/ `3_rest`（32 字），口语化、贴"小满"人设。
试听目录：`docs/voice-candidates/samples/`；对照带：`_compare_A2_B4_C_D_E1.mp3`（95.2 s，A2→B4→C→D→E1 连播，组间 0.9 s 静音，整片解码 0 坏帧）。

| 方案 | 均时长 s | 字速 字/s | 中位 F0 Hz | F0 p10–p90 | 静音率 | 均 CER | 均 KB | 判据 |
|---|---|---|---|---|---|---|---|---|
| A1 系统引擎代理·原调 | 7.7 | 3.03 | 208 | 142–278 | 0.440 | 0.044 | 91 | ✗ F0 偏低、语速偏慢、句尾拖 |
| A2 系统引擎代理·pitch+15%（**当前 APK 参数**） | 7.7 | 3.03 | 222 | 156–303 | 0.440 | 0.044 | 91 | ✗ 同上，且更尖 |
| B1 edge-tts · Xiaoyi（默认） | 6.0 | 3.89 | 299 | 223–363 | 0.414 | 0.044 | 35 | ✗ F0 上沿逼近 340，p90 已 363 |
| B2 edge-tts · Xiaoxiao | 6.0 | 3.89 | 261 | 191–326 | 0.354 | 0.058 | 35 | ✓ 全项过，但偏"温柔客服" |
| B3 edge-tts · 辽宁-Xiaobei | 6.7 | 3.45 | 245 | 192–301 | 0.390 | 0.058 | 39 | ✓ 全项过，带东北口音 |
| **B4 edge-tts · Xiaoyi rate -5% pitch +2Hz（落地）** | **6.3** | **3.69** | **303** | **224–369** | **0.408** | **0.057** | **37** | ✓ 全项过 |
| C sherpa-onnx · MeloTTS zh_en（离线） | 4.9 | 4.74 | 238 | 209–276 | 0.315 | 0.083 | 58 | ✗ 语速过快、F0 擦线 |
| D piper · zh_CN-huayan-medium（离线） | 4.9 | 4.81 | 205 | 172–288 | 0.135 | 0.149 | 59 | ✗ CER 0.149（"待一会儿就好"听成"但也坏人啊"） |
| E1 Kokoro int8 · sid45（离线） | 6.5 | 3.61 | 254 | 171–311 | 0.169 | 0.124 | 77 | ✗ 偶发整句念错（"已经很晚啦"→"的精神外卖"） |
| E2 Kokoro int8 · sid47（离线） | 5.4 | 4.33 | 251 | 183–296 | 0.156 | 0.170 | 64 | ✗ CER 0.170 |

分句 CER（按 1/2/3 句）：A 0.056/0.040/0.037；B4 0.056/0.040/0.074；C 0.056/0.120/0.074；D 0.056/**0.280**/0.111；E1 0.222/0.040/0.111；E2 0.167/0.120/0.222。

---

## 5. 三条路 + 决策表

| 方案 | 授权 | 可离线 | 包体增量 | 音色可调程度 | 首字延迟 | 失败率/重试代价 | AVD 可验证 | 集成改动点 | 风险 |
|---|---|---|---|---|---|---|---|---|---|
| **edge-tts（选定主路径）** | `edge-tts` 库 MIT；音频受微软在线服务条款约束（个人/小规模免费，商用需评估） | 否（须联网） | **0**（不进 APK，装在服务端镜像里） | 高：30+ 中文音色 × rate/pitch 任意调，可换 Xiaoyi/Xiaoxiao/Xiaobei/Xiaoni | 22 字 1.30 s、29 字 1.23 s；命中缓存 0.037 s | 单次 12 s 超时 + 重试 1 次（代价 ≤ 12.8 s/句），仍失败回 503，前端静默降级 native | 可：与设备能力无关，只要能访问 TTS 服务（本机已跑通 HTTP 200 `audio/mpeg`） | 服务端新增 provider；客户端只需配 `ttsServer` | 依赖第三方在线服务；离线/被墙时降级；商用条款需评估 |
| 系统 TTS 换 voice（兜底） | 随 ROM/Google TTS，无授权问题 | 是 | 0 | 低～中：仅设备自带音色，名字/质量因 ROM 而异；代码里已按名挑本地中文女声 | ~即时 | 极低；`isNetworkConnectionRequired` 的 voice 一律跳过，避免断网哑掉 | 部分：AVD `google_apis` 镜像默认无中文 TTS，需另装 Google TTS 才能验 | 已改 `TtsController.applyCuteVoice()`，零配置生效 | 各机型音色差异大，效果不可控 |
| sherpa-onnx + MeloTTS zh_en（离线） | sherpa-onnx Apache-2.0；MeloTTS 代码 MIT，模型随包 | 是 | int8 约 60–70 MB（`model.int8.onnx` 51 MB + 词典/lexicon） | 中：多说话人 sid 可选，但语速韵律不及神经 TTS 自然 | 本地 RTF 4.3–5.2×（CPU）→ 30 字要 4–5 s，不达首包要求 | 无网络失败，但慢；低端机更慢 | 可，但 APK 体积与 CPU 占用需真机确认 | 客户端需 JNI/onnxruntime + 模型资产 | **RTF > 1 直接出局**（本机实测） |
| Kokoro 多语 int8（离线） | Apache-2.0 | 是 | 约 115 MB（108.9 MB onnx + voices + 词典） | 中：多 sid，但中文发音不稳 | RTF 可接受，但整句错读 | 无网络失败，错读不可重试（同一输入同一错误） | 可，同上前提 | 同 sherpa | CER 0.124–0.170，错读出现在最不该错的安慰句 |
| piper zh_CN-huayan-medium（离线） | piper MIT；**模型数据集许可 Unknown**（MODEL_CARD 原话） | 是 | ≈ 63 MB（60.3 MB onnx + config） | 低：单说话人、音色固定 | RTF < 1（快），但断句生硬 | 无网络失败 | 可 | 同 sherpa | 许可不明 + CER 0.149，商用不可接受 |
| GPT-SoVITS / Bert-VITS2（自训练路线） | GPT-SoVITS MIT；Bert-VITS2 需按仓库协议核对 | 是 | 权重 1–2 GB 起 + 训练数据与训练时长 | 极高（可克隆/定制人设） | 推理慢，移动端需量化 | 训练与调参成本高 | 否，本机无法训练（用户已禁） | 需独立推理服务 | 本轮明确不做（不下大模型、不训练） |

---

## 6. 选型：走 edge-tts 主路径 + 系统 TTS 兜底，不做 100 MB+ 离线模型

**理由（按证据，不按偏好）：**

1. **判据只放行 B4**。B4 是唯一"四项全过且听感年轻偏软"的组合：F0 303（224–369）、字速 3.69、单句 5.2/6.3/7.4 s、静音率 0.408、CER 0.057。
2. **包体零增量**。三条离线路最低也要 60 MB（piper），最高 115 MB（Kokoro），而 edge 是 0——APK 体积取舍不需要砍功能，也就不需要回来问。
3. **离线路全被判据挡住**：Melo int8 RTF 4.3–5.2× 不实时；piper 许可 Unknown 且 CER 0.149；Kokoro CER 0.124–0.170 且有整句错读。它们不是"差一点"，是在关键句上听错。
4. **授权干净**：edge 不需要 API key（不产生任何凭据），因此不存在"把凭据写进产物"的问题。
5. **降级路径短**：服务端不可达时，前端自动落回 native 的系统 TTS，且 native 侧已改成"优先本地中文女声 + pitch 1.06"——不会因为 TTS 失败就不出文字（文字渲染先于语音，`speakSafe` 永不 reject）。

**音色取向落地（"可爱"定义成参数，而不是形容词）：**
- 语速略慢：edge `rate -5%`（≈ 字速 3.69，比默认 3.89 慢 5%）；
- 音高略高：edge `pitch +2Hz`，且把 Android 侧 `setPitch` 从 1.15 降到 1.06（旧的 1.15 才是"电子尖"的来源）；
- 句尾不拖：不加任何尾部停顿，静音率从默认 0.414 降到 0.408 不是目的，真正起作用的是 -5% 语速让末句 7.4 s（原 A 组 9.0 s）。

---

## 7. 落地改动（commit `a5f5d70`）

| 文件 | 改动 |
|---|---|
| `server/tts_server.py` | 新增 `edge` provider：`_edge_synthesize()` 12 s 超时 + 1 次重试；`EDGE_RATES = {-12%, -5%, +5%}`；`_edge_pitch()` 默认 `+2Hz`；失败抛 `TtsProviderUnavailable` → 503；白名单删除不存在的 `zh-CN-XiaoyuanNeural`（实测 `NoAudioReceived`），换入 `zh-CN-shaanxi-XiaoniNeural`；`audio_content_type()` 让 edge 返回 `audio/mpeg` |
| `server/tests/test_tts_post.py` | 新增 `EdgeProviderTest`（假模块验证拼包/rate/pitch 透传、缺包抛错且异常不含原文、空音频抛错）+ 语速表/白名单回归 |
| `web/js/tts.js` | 语速表 0.84/0.96/1.12；`VOICES` 与后端白名单对齐；首包超时 7 s → 10 s；`u.pitch 1.15 → 1.06` |
| `web/index.html` | 音色选项：小艺·元气少女 / 小晓·温柔轻语 / 小妮·软糯（删除"小圆"） |
| `web/tests/tts_provider.test.js` | 语速与 pitch 断言、白名单回退用例 |
| `android/.../TtsController.java` | `applyCuteVoice()`：初始化后遍历 `getVoices()`，跳过 `isNetworkConnectionRequired()`，按名字打分挑中文女声；`pendingRate 1.02 → 0.96`、`setPitch 1.15 → 1.06` |
| `Dockerfile` / `docker-compose.yml` | 可选 `--build-arg TTS_EDGE=1` 把 edge-tts 打进镜像（默认关，保持镜像精简） |

**端到端实测**（`docs/voice-candidates/live/e2e.json`，`XIAOMAN_TTS_PROVIDER=edge`、端口 8913、真实 HTTP）：

| 请求 | 文本长度 | 耗时 | 状态 | Content-Type | 字节 |
|---|---|---|---|---|---|
| 1 | 22 字 | 1.304 s | 200 | `audio/mpeg` | 31 392 |
| 2 | 29 字 | 1.225 s | 200 | `audio/mpeg` | 37 584 |
| 3 | 32 字 | 0.037 s（缓存命中，`~/.cache/xiaoman_tts`） | 200 | `audio/mpeg` | 44 352 |
| 重复请求 | — | 0.020 s（缓存） | 200 | `audio/mpeg` | 31 392 |
| 非法 voice（XiaoyuanNeural） | 22 字 | — | **400**（不是 500） | — | — |

**测试**：服务端 `pytest` 214 passed / 2 skipped（22 subtests）；web `node --test "tests/*.test.js"` 295 passed / 0 failed；资产 `ASSET_VERIFY PASS`（74 文件、1 392 959 字节）；`TtsController.java` + `AudioFocusPolicy` + `NoisyGuard` + `AndroidTtsBridge` 用 `javac 21` 对 `android-34/android.jar` 编译通过（WSL）。编译期真抓到一个错：`TextToSpeech.getVoices()` 返回 `Set<Voice>` 而非 `List<Voice>`，已改。

---

## 8. 失败与降级路径（没有 TTS 也必须出文字）

```
文字消息 → 立即渲染（不等语音）
        └─ TTS.speakSafe()
            ├─ server(edge)  200/audio/mpeg → 播放；Envelope 走 blob: 解码口型（音色改造不动 lipSync）
            ├─ server 5xx/超时(10s) → 静默切下一层，不弹错、不阻断发送
            ├─ native 系统 TTS：已挑本地中文女声、pitch 1.06 / rate 0.96
            └─ speechSynthesis（WebView 转调系统引擎）
                 └─ 全失败 → onEnd(false)，气泡不标"已播"，只写一条 voice_tip 排障记录
```

- 断网：server 不可达 → native 兜底；本轮把"需要联网的 voice"直接排除，避免断网时 `setVoice` 到网络音色导致整条哑掉。
- 服务端 503：重试 1 次（0.4 s 退避）后放弃，代价 ≤ 12.8 s/句（12 s 超时 + 重试），不会把消息发送卡住。
- 设备无任何中文 TTS：`onEnd(false)` + 文字照常，只是没有声音；不会阻塞会话。

---

## 9. 授权清单（我自己拍定的项，按用户第三轮授权）

| 项 | 决定 | 是否触发"回来问" |
|---|---|---|
| 引擎选型 | edge-tts（服务端）+ 系统 TTS（设备兜底） | 否（授权内） |
| 音色参数 | Xiaoyi、rate -5%、pitch +2Hz；Android pitch 1.06 / rate 0.96；前端语速 0.84/0.96/1.12 | 否 |
| 包体取舍 | 接受 0 增量（不引入任何离线模型），不砍任何现有功能 | 否 |
| 是否打进 APK | edge 走服务端；APK 只改代码不动资产 | 否 |
| 凭据 | 全链路不需要 key/secret；仓库与产物中无凭据 | 否（无需回来问） |
| 口型 lipSync | 本轮不动 | 否 |

---

## 10. 风险与遗留

1. **依赖在线服务**。edge-tts 是免费无 key 的在线接口，微软侧限流/变更会导致 503 → 已按"重试 + native 兜底"处理，但音色会在降级瞬间变回系统音。生产若要求稳定可爱音色，需自建 sherpa/piper 服务或换有 SLA 的商用 TTS。
2. **出厂 APK 的 `ttsServer` 仍为空**。这意味着**不配服务地址的安装包依然走系统 TTS**（只是参数从 1.15 柔化到 1.06）。要让 edge 音色真正生效，必须在 `web/js/config.js` 或构建期注入 `XIAOMAN_TTS_SERVER` 指向部署好的 TTS 服务——这是部署配置，不是代码缺陷，未擅自写死内网地址。
3. **AVD 未能本地验证**。本机无 JDK/adb/AVD；`TtsController.applyCuteVoice()` 只做编译级改动 + 逻辑自检，真机/AVD 上的音色挑选结果需一次实际安装验证（或在 CI 里跑）。
4. **F0 是自相关估计**，±1 倍频误差可能；B4 的 369 Hz p90 里含少量高音帧，主观上未出现"尖"的听感。
5. **样音的 A 组是"系统引擎代理"**（本机无 Android TTS 引擎，用 Windows SAPI 中文女声近似系统默认音色的参数与韵律），用于量化"默认有多差"，不代表某一具体 ROM 的音色。

---

## 附：产物位置

- 30 个样音：`docs/voice-candidates/samples/*.mp3` + `manifest.json`（逐句指标）
- 对照带：`docs/voice-candidates/samples/_compare_A2_B4_C_D_E1.mp3`
- 端到端：`docs/voice-candidates/live/e2e.json`、`live_1..3_*.mp3`、`server.log`

---

## 11. 静音禁令（2026-11-07 20:2x，优先级高于一切验证动作）

**触发原因**：用户在 20:1x 两次听到非预期声音。已确认其中一次是
2026-11-07 20:19:47 用 `ffplay` 播放 `samples/G_audible_check_1.mp3`（5.26s，退出码 0）。

**禁令（后续任何会话必须遵守）**：

1. **本机不许发出任何声音**。禁止 `ffplay` / `mpv` / VLC / Windows Media Player /
   `Start-Process` 打开音频；禁止 SAPI / `System.Speech` / PowerShell
   `SpeechSynthesizer` 朗读；禁止在带音频输出的模拟器里播放语音。
2. **起模拟器一律带 `-no-audio`**，且启动前确认 AVD `config.ini` 里
   `hw.audioInput=no`、`hw.audioOutput=no`。
   本仓库现场：`D:\android-avd\host-boot.ps1` 已含 `-no-audio`；
   `D:\android-avd\treehole_api34.avd\config.ini` 已是 `hw.audioInput=no` / `hw.audioOutput=no`。
3. **可听性验证改为纯离线**：文件级指标（时长 / 整体 RMS / 峰值 / 非静音占比）+
   离线 ASR 回听（vosk small-cn）+ CER。不再做人耳抽查，也不再询问用户"要不要听"。
   门限：时长 2–6s、RMS ≥ 0.02（≈ -34 dBFS）、峰值 ≥ 0.05（≈ -26 dBFS），
   脚本见实验室 `voicelab/gen_audible.py`，报告 `samples/audible_check.json`。

---

## 12. 服务绑定地址约定（2026-11-07 20:30）

**问题**：取证发现 `tts_server.py` 曾被以 `XIAOMAN_BIND_HOST=0.0.0.0` 起在 `:8903`，
同一局域网的其它机器可直接连上这个没有鉴权的合成接口。

**约定**：

- 本机所有启动入口默认 `XIAOMAN_BIND_HOST=127.0.0.1`（`server/xm_config.py` 默认值），
  **不监听 `0.0.0.0`**；`scripts/start_tts.sh` 不再自动绑全网卡。
- 只有容器内部才由 `docker-compose.yml` 注入 `0.0.0.0`，再由 `ports` 映射回宿主机
  `127.0.0.1:8903`；发布地址默认 `${TTS_PUBLISH:-127.0.0.1}`。
- 真机接入不再靠局域网直连，改走 USB 反向端口：`adb reverse tcp:8903 tcp:8903`，
  App 里填 `http://127.0.0.1:8903`。模拟器仍用 `http://10.0.2.2:8903`
  （QEMU 用户态网络把 `10.0.2.2` 转到宿主机回环，绑 `127.0.0.1` 也能连）。
- 确需暴露局域网时必须显式设置 `XIAOMAN_BIND_HOST=0.0.0.0`，属主动选择。

**实测（20:30，本机）**：

| 目标 | 结果 |
|---|---|
| `curl http://127.0.0.1:8903/health` | `200`，exit 0 |
| `curl http://192.168.0.100:8903/health`（本机以太网 IP） | `000`，exit 7（connection refused） |
| `curl http://172.19.80.1:8903/health`（WSL 虚拟网卡） | `000`，exit 7 |
| `Get-NetTCPConnection -LocalPort 8903 -State Listen` | 仅 `127.0.0.1:8903` |

---

## 13. 开箱即用（出厂不再 `ttsServer=""` 空着）

**四层，按优先级**：

1. **注入**：`window.XIAOMAN_TTS_SERVER`（CI / 内嵌页面用）。
2. **显式设置**：设置页「语音服务地址」（`cfg.ttsServer`）。
3. **同机推导**（本轮新增，解决"用户没填第二项"）：对话后端 `apiBase` 是**内网地址**时，
   语音地址自动推导为 `http://<同 IP>:8903`。只对
   `10.*` / `192.168.*` / `172.16–31.*` / `*.local` 生效；公网 API 域名、
   `127.0.0.1`/`localhost`（在手机上指向手机自己）**一律不猜**，避免造出一个永远连不上、
   每句白等 10s 的 provider。
4. **兜底**：以上都不可达 → 柔化后的系统 TTS（pitch 1.06 / rate 0.96），文字照常。

**设置页提示与判定同源**：`app.js` 的 TTS 状态改用 `TTS.resolveServer(win, cfg)`，
不再只看 `cfg.ttsServer`——否则"只填了内网后端"的用户会被提示成"安卓系统语音"，
与实际发声相反（本轮实机发现并修复）。

**为什么做不到真正的零配置**：不背 60MB+ 离线模型的前提下，语音必然要一个在线服务；
能做的是"用户只填一次他已经必须填的对话后端，语音自动跟上"，以及一条命令起服务。

---

## 14. 真机（AVD）确认记录

环境：`treehole_api34`（API 34 x86_64），`-no-audio` + `hw.audioInput=no` / `hw.audioOutput=no`。
宿主机 `provider=edge`，服务只绑 `127.0.0.1:8903`；模拟器用 `http://10.0.2.2:8903`
（QEMU 把 `10.0.2.2` 转到宿主机回环）。

| 项 | 结果 |
|---|---|
| 安装 | `adb install -r` 成功（1.1M，`v060-debug`），`ASSET_CHECK PASS` 74 文件 / 1,395,310 字节 |
| 设置页提示 | 后端填 `http://10.0.2.2:8902`、语音地址留空 → 显示「实际发声：服务端语音」 |
| 服务端日志 | `OPTIONS /tts 204`、`TTS generating: provider=edge voice=zh-CN-XiaoyiNeural`、`POST /tts 200` |
| 产出音频 | `.cache/tts-avd/*.mp3`（23,616 / 20,016 字节，真实 edge 音频），已存证 `docs/voice-candidates/live/avd_edge_playback.mp3` |
| 降级 | 杀掉服务后：logcat `server tts 不可用，降级本机语音`，界面提示「语音暂时没法出声，文字照常」，回复文字正常 |
| 听感局限 | 模拟器无音频输出，**不以"编译通过"代替"听着可以"**；音色听感以服务端真实 mp3 + ASR CER 为准 |

**未决问题（本轮实机新发现）**：12:13 那次发消息，服务端收到了请求并 `200` 返回，
但 WebView 侧 `fetch` 报 `Failed to fetch`，界面落到兜底提示；同一 URL 在 19:52 是成功的。
日志里同时有 mixed-content 警告（页面跑在 `https://appassets.androidplatform.net`，请求的是
`http://10.0.2.2:8903`）。已核对服务端 `Access-Control-Allow-Origin: *` 存在、preflight `204` 正常，
因此怀疑是混合内容 / 私有网络访问（PNA）在**响应侧**拦截。下一步：真机复现 + 抓 WebView 详细错误，
必要时把 TTS 请求改走原生桥（`AndroidNet`）或本地 https 代理，彻底绕开混合内容。

---

## 15. 兜底音色（F 组）与对照带

兜底不是"只写参数"：用 ffmpeg 从原调基线做等价信号处理，
`pitch 1.06` + `rate 0.96`（`asetrate×1.06, aresample, atempo=0.96/1.06`），
因为 SAPI 的 SSML prosody 实测被 Huihui 忽略（时长与原始完全一致）。

| 文件 | 时长 | F0 中位 [p10–p90] | 字速 cps | 静音率 | CER |
|---|---|---|---|---|---|
| `A_sys_huihui_pitch100_1_greet`（基线） | 6.387s | 223.8 [155.3–280.7] | 2.82 | 0.503 | 0.056 |
| `F_sys_fallback_096_106_1_greet` | 6.627s | 231.9 [163.3–296.3] | 2.72 | 0.485 | 0.056 |
| `F_..._2_comfort` | 8.017s | 205.1 [145.2–285.7] | 3.12 | 0.400 | 0.080 |
| `F_..._3_rest` | 9.298s | 219.2 [142.9–296.3] | 2.90 | 0.397 | 0.037 |

结论：F0 约 +4%（相对基线），字速约 0.96×，CER 与基线同级——"语速略慢、音高略高"落到了可听层面，
不是纸面参数。对照带已更新为 `samples/_compare_A2_B4_C_D_E1_F.mp3`（6 组、120s、整片解码 0 错）。

**本轮时间账**：≈1h50m；合成 3 次（G 组严格门）+ 1 次（F 组 ffmpeg 派生，不算合成）；
耗时主项 = AVD 反复掉线重连（adb daemon 每次调用重启 + 模拟器随父 shell 退出）与
12:13 失败链路的日志取证。

---

## 16. 本机脚本约定（2026-11-07 20:45）

**触发原因**：用裸 `Invoke-WebRequest` 探 `http://127.0.0.1:8903/health` 时，
PowerShell 在用户桌面弹出「可能会运行网页中的脚本代码…是否继续执行」确认框。

**约定**：

1. PowerShell 里访问 HTTP **一律用 `curl.exe`**；确实要用 cmdlet 时必须写
   `Invoke-WebRequest -UseBasicParsing`。Trae 的 shell 是 `-NoProfile`，
   想靠 profile 改默认值对这个会话无效，只能靠命令写法。
2. 不要在 `scripts/` 里新增裸 `Invoke-WebRequest` / `Invoke-RestMethod`；
   现有仓库脚本已全量搜过，无命中（`scripts/gate.ps1` 也未使用）。
3. 服务监听只允许 `127.0.0.1`，不允许 `0.0.0.0`（见第 12 节）。
4. 不要留"每隔几秒探活一次"的循环；它会反复触发上述确认框并导致进程被终止。
   需要等待时用一次性 `sleep` + 有限次数轮询，并在同一次调用内完成。

---

## 17. 12:13 `Failed to fetch` 归因与修复（2026-11-07 22:5x）

**结论先说**：不是明文 HTTP 被拦，不是 localStorage，不是混合内容策略，也不是 `adb reverse`。
**失败在响应阶段**：服务端已经把 `200` 和 `Content-Length` 写出去了，body 传到一半连接中断，
Chromium 判 `net::ERR_CONTENT_LENGTH_MISMATCH`，`fetch` 于是抛 `TypeError: Failed to fetch`。

**排除过程（逐项实测）**：

| 假设 | 实测 | 结论 |
|---|---|---|
| 明文 HTTP 被拦 | `network_security_config.xml` 已放行 `10.0.2.2`；同一 APK 打 `POST /tts` 得 `200`，Network 事件 0 失败 | 排除 |
| 混合内容（https 页取 http） | `WebShell` 已 `MIXED_CONTENT_ALWAYS_ALLOW`；logcat 只有 warning，请求照发 | 排除（warning 不拦） |
| `localStorage` 读不到配置 | 请求确实带着 `voice=zh-CN-XiaoyiNeural rate=normal` 到达服务端 | 排除 |
| `adb reverse` 那一刻断了 | 走的是 `10.0.2.2`（QEMU 转宿主回环），不走 reverse；请求已到服务端 | 排除 |
| 前端 10s 超时主动 abort | 主动 abort 的报错是 `AbortError: The user aborted a request.`，与历史日志不符 | 排除 |

**为什么日志里的 `200` 不能自证成功**：`BaseHTTPRequestHandler.send_response()` 会先记
`"POST /tts" 200`，再写 body。历史日志 `20:13:24 TTS generating text_len=9`、
`20:13:25 POST /tts 200`（9 字正对应截图里的"然后呢…"）只证明**请求到达并开始回**，
不证明 body 送达。

**可复现步骤**（`temp_20261107_03` 实验室）：
1. `flaky_proxy.py` 起在 `127.0.0.1:8918`，转发到真 edge 服务 `127.0.0.1:8903`；
   对**第一个** `/tts` 先取得完整响应（`32112` 字节）再只回 `100` 字节，
   `flush + shutdown(SHUT_WR)` 后关连接。
2. 静音 AVD（`-no-audio`，`hw.audioInput/Output=no`）里把 `ttsServer` 设为 `http://10.0.2.2:8918`，
   调 `TTS.speakSafe("嗨，我是小满。今天过得怎么样，想聊聊吗？")`。
3. 期望：请求 #1 `200` → `Network.loadingFailed.errorText=net::ERR_CONTENT_LENGTH_MISMATCH`；
   控制台 `server tts 网络层失败，重试一次: Failed to fetch`；300ms 后请求 #2 `200` 成功。

**修复**（`web/js/tts.js`）：服务端 fetch 单独一套预算，不再复用 10s 的开播超时——

```js
const AUDIO_START_TIMEOUT = 10000;   // 音频 10s 没开播就降级（保留）
const SERVER_FETCH_TIMEOUT = 20000;  // edge 最坏 12s×2 + 余量
const SERVER_FETCH_ATTEMPTS = 2;     // 只在网络层 TypeError 重试一次
const SERVER_RETRY_BACKOFF = 300;
```

每次尝试新建 `AbortController` 并在 `finally` 清 timer；`AbortError` **不**重试
（那是我们自己的预算到了），`TypeError`（截断/拒绝/明文被拦）才重试。

**修复前后对比（同一条 flaky 链路，静音 AVD）**：

| | 请求 #1 | 请求 #2 | 结果 |
|---|---|---|---|
| 修复前 | 200 + body 断 | 无 | `Failed to fetch`，界面落到兜底提示 |
| 修复后 | 200 → `ERR_CONTENT_LENGTH_MISMATCH` | 200，`upstream_bytes=32112` | 音频拿到；控制台明确记录"重试一次" |

后端仍打一条 Mixed Content warning（Chromium 的提示，不拦），属预期噪声。

**回归**：`web/tests/tts_provider.test.js` 新增"200 后 body 中断重试一次不降级"
（断言 `attempts==2`、`{spoke:true,provider:'server'}`）与"503 不重试"（`attempts==1`）。
`node --test web/tests/*.test.js` → **298 pass / 0 fail**。

---

## 18. 首选音色 B4 落地 + E1/Kokoro 无网降级（2026-11-07 23:0x）

### 18.1 B4 为首选（唯一默认，不再靠用户"记得选"）

| 项 | 值 | 位置 |
|---|---|---|
| 音色 | `zh-CN-XiaoyiNeural`（晓伊） | `server/tts_server.py` `DEFAULT_VOICE`；`web/js/config.js` `DEFAULTS.voice` |
| 语速 | `-5%`（`TTS_RATES.normal=0.95` 给 sherpa/cmd，`EDGE_RATES.normal="-5%"` 给 edge） | 同上 |
| 音高 | `+2Hz`（`_edge_pitch()`，可用 `XIAOMAN_TTS_EDGE_PITCH` 覆盖） | `server/tts_server.py` |

**证据（真机请求体，不是纸面参数）**：静音 AVD 里 `TTS.speakSafe` 打出的请求，
flaky 代理原文打印 `voice=zh-CN-XiaoyiNeural rate=normal text_len=20`
（默认配置、未手填任何语音项）；服务端 `TTS generating: ... voice=zh-CN-XiaoyiNeural`。

**单测固化**：`server/tests/test_tts_post.py` 断言 `TTS_RATES["normal"]==0.95`、
默认 voice 为 Xiaoyi、`_edge_pitch()` 默认 `+2Hz`。

### 18.2 无网降级：edge 挂了自动落到 E1（sherpa-onnx + Kokoro int8）

配置：`XIAOMAN_TTS_FALLBACK=sherpa` + `XIAOMAN_TTS_SHERPA_KIND=kokoro`
（模型 `voicelab/models/kokoro2/`：`model.int8.onnx` 108.9MB、`voices.bin` 26.9MB）。

**实测 A：只用离线 provider**

| 项 | 数字 |
|---|---|
| HTTP | `200`，`Content-Type: audio/wav` |
| 字节 / 时长 | `249,196` / `5.191s`（24kHz mono s16） |
| 电平 | `mean −22.8dB`，`max −9.2dB` |
| 服务端日志 | `TTS generating: provider=sherpa fallback=- voice=zh-CN-XiaoyiNeural text_len=20` |

**实测 B：edge 主 + sherpa 降级（把 edge 超时压到 0.001s 强制失败）**

| 项 | 数字 |
|---|---|
| `/health` | `{"provider":"edge","fallback":"sherpa","available":true}` |
| 触发日志 | `WARNING TTS provider edge failed, trying fallback: edge-tts failed: TimeoutError` |
| HTTP | `200`，`Content-Type: audio/wav` |
| 字节 / 时长 | `249,280` / `5.192s` |
| 电平 | `mean −22.8dB`，`max −8.7dB` |

**如实报告的代价**：本地 Kokoro 是 CPU int8，RTF **2.7458**
（`14.26s` 合成出 `5.19s` 音频，加载 2.60s），整条 `/tts` 请求 A=22.47s、B=21.24s。
它是"无网时还能出声"的保命档，不是日常档；日常仍是 edge（同句请求 0.5s 级）。
`/health` 的 `fallback` 字段让运维一眼看出降级是否挂好，不靠猜。

### 18.3 设置页"实际发声"与真实 provider 同源（`db78e0f` 验证）

**条件**：只填对话后端 `apiBase=http://192.168.0.100:8902`，语音地址（`ttsServer`）**留空**。

**真机（静音 AVD）实测输出**：

```
ttsServer  = ""
apiBase    = "http://192.168.0.100:8902"
ttsBase    = "http://127.0.0.1:8903"   （默认回环，不当作可用）
resolved   = "http://192.168.0.100:8903"   ← 同机推导
provider   = "server"
hintText   = "实际发声：服务端语音"
hintEl     = "实际发声：服务端语音"     ← 打开设置页后 DOM 里的真实文本
voiceEl    = "音色会按下面的设置生效（服务端语音已连通）。"
```

`hintEl` 是真点了 `#settingsBtn`、由 `updateTtsHint()` 写进 DOM 后读出来的，
不是只调纯函数比对——设置页文案与实际链路读的是同一个 `TTS.resolveServer`。

**回归**：`server/tests` → **219 passed, 2 skipped, 22 subtests passed**（9.72s）；
`web/tests` → **298 pass / 0 fail**。




