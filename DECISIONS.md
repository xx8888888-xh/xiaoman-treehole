# DECISIONS · 2026-11-07 语音可断言线

本轮授权清单（用户明确：测试脚本、断言实现、文档由 AI 自行决定，写入本文件）。
只有「放宽断言」和「改产品降级行为」必须回去问；本轮两类都没有发生。

## 1. 失败日志结构化（voice_tip.js）

- 旧 schema：`{at, reason}`，reason 只有 `play_failed` / `start_failed`，长会话写满 20 条也只能知道「失败过 20 次」。
- 新 schema 追加可选 `detail`，白名单字段：
  `provider | error | textLen | bucket | textHash | elapsedMs | timedOut | httpStatus | attempts[]`
- **隐私红线（P1-5）不变**：不落对话原文、不落堆栈。文本只保留长度、长度分桶、FNV-1a 哈希（用于识别"同一句反复失败"）。
- `detail` 缺省时条目仍是 `{at, reason}`，旧读端 / 旧测试不受影响。
- 新增纯函数 `VoiceTip.stats(log)`：按 reason / error / 长度桶 / provider / 超时 / 重复文本哈希聚合，供排障与 e2e 产物使用。
- 长度分桶阈值：`≤20 short`，`≤60 mid`，`>60 long`（与语音回复实际长度分布对齐）。

## 2. 失败原因采样口径（tts.js）

只加诊断，不改任何播放/降级路径：

- 原因枚举：`off / muted / empty_text / no_provider / bridge_rejected / browser_unavailable /
  http_<status> / network_error / start_timeout / audio_error / play_rejected / browser_error / speak_error`
- `lastFailure.error` 保留**首个**败因（最接近根因的一次），`attempts[]` 保留最多 4 跳（含 provider / httpStatus / timedOut / elapsedMs）。
- `lastFailure` 只作探针与失败日志输入；`speak` / `speakSafe` 的返回值与 `onEnd/onSilent` 契约未变。

## 3. 离线断言实现（node 层，模拟器只做最终确认）

- `web/tests/voice_probe.js`：
  - `buildWav`：可控假 TTS，按文本时长生成 16-bit PCM（可造静音/截短）。
  - `decodeWav` / `analyze`：解出 RMS、峰值、静音占比、首尾静音、有声时长（等价于浏览器 decodeAudioData 的 PCM）。
  - `judge`：`nonzero` + `durationOk` + `truncated`。
- 阈值（与 `TTS._speechPlan` 同一文本时间线，语速 1.02）：
  - `durationOk = 0.45×expected ≤ duration ≤ 2.6×expected`
  - `truncated = duration < 0.45×expected` 或（有声段 < 55% 且尾部静音 > 30%）
  - `nonzero = rms ≥ 0.02 且 peak ≥ 0.05`
- 这些是新增断言，没有放宽任何既有断言。
- 无 TTS 环境（无 AndroidTTS / 无 speechSynthesis / 无服务端）必须判 `spoke=false`，并验证明细与失败日志照写；**不以 skip 计为通过**。

## 4. verify_tts.py：去掉「无 TTS = SKIP 通过」

- 旧行为：服务端 503 `tts_provider_unavailable` → 打印 `[SKIP]` 并 `return 0`。
- 新行为：503 时改跑离线假 TTS 的 PCM 断言（非静音 / 时长合理 / 未截断），并用 silent/short 两个负例验证判据真的会判红；任一不满足则 `exit 1`。
- 真实服务端音频路径在 ASR 之前也先做 PCM 断言（体积够 ≠ 有声音）。
- ASR 依赖缺失仍是 `[SKIP]`，但只在 PCM 断言已通过之后；退出码语义写明在脚本 docstring。

## 5. e2e 产物

`scripts/emulator_e2e_walk.py` 的 `07_voice_tip` 探针在原有 `logs/last` 之外，追加最近 5 条完整条目与 `stats`，让"失败在哪"进入 artifact，而不是只剩总数。
