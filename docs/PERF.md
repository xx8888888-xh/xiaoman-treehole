# 流畅度与包体专项（feat/performance / treehole-w13）

基线 `61384a0`，全部数据在 headless Chromium（Playwright + CDP）实测：静态站 8911、mock 8912、
TTS provider=none 8913，全局静音（`--mute-audio`，tts=off），不复用 8901–8903。
复现：

```bash
python tools/perf/measure.py --out artifacts/perf/after.json            # 首屏/200轮/滚动/耗电
python tools/perf/measure.py --only micro --out artifacts/perf/micro.json
python tools/perf/viewports.py --out artifacts/perf/viewports
python tools/compress_assets.py --dry-run --json artifacts/perf/assets.json
```

## 结论（改前 → 改后）

| 指标 | 改前 | 改后 | 变化 |
|---|---|---|---|
| FCP | 64 ms | 68 ms | +4 ms（噪声） |
| LCP | 1608 ms | 1552 ms | −56 ms |
| 可交互（bind ok） | 138 ms | 132 ms | −6 ms |
| DOMContentLoaded | 152 ms | 147 ms | −5 ms |
| 加载期长任务 | 2 个 / 最长 82 ms / 合计 154 ms | 2 个 / 最长 74 ms / 合计 133 ms | 最长 −10%，合计 −14% |
| 加载期主线程 TaskDuration | 439.5 ms | 396.3 ms | −9.8% |
| 加载期 ScriptDuration | 106.6 ms | 93.2 ms | −12.6% |
| 加载期 JS 堆峰值 | 5.77 MB | 5.99 MB | 噪声（±0.2） |
| `ChatPager.push` ×300（真实 buildRow） | 175.9 ms（586 µs/条） | 46.3 ms（154 µs/条） | **−73.7%** |
| 300 条历史翻页帧率 | 60.0 fps，掉帧 0，长任务 0 | 60.0 fps，掉帧 0，长任务 0 | 持平（已无长任务） |
| 200 轮后 JS 堆增长（强制 GC 后） | +1.75 MB | +1.69 MB | −3%，marks 收敛 |
| 200 轮后 DOM 节点 | 223，窗口恒 14 行 | 223，窗口恒 14 行 | 无泄漏 |
| 静止 60 s rAF 排帧 | 7202 | 7202 | 未改善（见遗留 P0） |
| 静止 60 s 定时器触发 | timeout 3 / interval 5 / WS 0 | 同左 | 持平 |
| 连聊 60 s（339 轮）rAF | 7210 | 7210 | 渲染非瓶颈；timeout 2214 次 |

包体：发布物口径 **6.96 MiB（verify_assets：7,294,227 B / 54 文件）**；其中 live2d 模型 5.82 MiB（83.7%），
js 1.11 MiB，css 31.5 KiB。包内可无损压缩图片 **0 个 / 0 KiB**（`web/assets` 只有 live2d，
按约束冻结）；`web/docs/shots` 两张截图可无损省 25.1 KiB，但它们不进包，故未写盘。
可省但未动的最大项：`Hiyori.2048` 纹理档 **4.12 MiB 未被任何视口使用**（运行时取 1024 档），
删/构建期排除后可到 **2.84 MiB（−59%）**，属 live2d 资源，需主流程协调。

## 改动

1. `web/js/chat_pager.js`
   - **增量滑窗**：窗口已满且只前进 1 行时，只删首行 + 建末行（原来整页重建 14 行）。
     流式每落一条消息的 DOM 构建量 14 → 1。
   - 整页重建用 `DocumentFragment` 一次插入；`bottom` 锚点不再多余读取 `scrollHeight`（少一次强制布局）。
   - **修复**：贴底后内容增高（语音按钮/长文本）会让 `scrollTop` 落后 48–96 px，此时新消息只进窗口
     数据却不渲染——里程碑播报（聊满 100 次）因此丢失。改为 `pinned` 标记 + 一行容差跟随；
     用户手动上滚仍会解除跟随，不打断看历史。E2E ⑦e-3 由此恢复。
2. `web/js/memory.js`：召回打分把查询分词（正则 + 2-gram 滑窗）移出 `map`，200 条记忆不再重复构造 200 次。

未改（文件域限制）：`web/js/stage.js`、`web/js/risk.js`、`web/js/app.js`、`server/**`、`android/**`、`web/index.html`。

## 明细

### 首屏（390×844，mock 模式）
长任务 2 个：约 1.0 s/74 ms、1.5 s/59 ms，均来自 live2d 纹理解码与 Pixi 初始化（stage.js，冻结）。
`LayoutCount 11 / RecalcStyleCount 20`，DOM 631 节点，canvas 正常。

### 200 轮连续对话（offline 规则回复，模拟真实发送路径）
- 全程 0 个 >50 ms 长任务；`ScriptDuration` 327 ms / 49.2 s（0.66%），`TaskDuration` 5.70 s（含等待）。
- 堆：3.87 → 5.56 MB（强制 GC），marks 5.26/5.35/5.40/5.48/5.58（每 40 轮 +0.09/0.05/0.08/0.10 MB，
  与 600 条记录的数据体量相符，非泄漏）；DOM 223 恒定，渲染窗口恒 14 行。

### 300 条历史
窗口不可滚时（14 行 ≈ 视口高）用 `ChatPager.older()` 走同一条翻页热路径，24 次翻页覆盖 142 条：
325 帧 / 5.4 s = 60.0 fps，帧间隔最大 16.8 ms、P95 16.8 ms，掉帧 0，长任务 0。

### 耗电（60 s 档）
| 场景 | rAF 排帧 | setTimeout 触发 | setInterval 触发 | WebSocket | CSS 动画 |
|---|---|---|---|---|---|
| 静止（minimal 预设，canvas 在） | 7202（≈120/s） | 3 | 5（心跳 60 s + presence 15 s） | 0 | 0 |
| 连聊 339 轮 | 7210 | 2214（逐字/分段定时器） | 5 | 0 | 0 |

rAF 调用栈固定为 `pixi.min.js _tick`，页面内自建 60 fps 循环实测 60.3/s → **同一帧被排了两次 rAF**，
即 Pixi 的应用 ticker 与共享 ticker 至少有一个在 minimal（motion=off）下未被真正停掉。
这是静止耗电的最大项（120 次/秒唤醒），根因在 `stage.js`（另一会话文件），本轮未改，见遗留 P0。

### 适配（五档视口，均 PASS）
| 视口 | 横向溢出 | 可点区域 <44 px | 文字裁切 | 越界元素 | 截图 |
|---|---|---|---|---|---|
| 320×568 | 否 | 0 | 0 | 0 | `docs/perf-shots/320x568.png` |
| 390×844 | 否 | 0 | 0 | 0 | `docs/perf-shots/390x844.png` |
| 430×932 | 否 | 0 | 0 | 0 | `docs/perf-shots/430x932.png` |
| 768×1024 | 否 | 0 | 0 | 0 | `docs/perf-shots/768x1024.png` |
| 1200×900 | 否 | 0 | 0 | 0 | `docs/perf-shots/1200x900.png` |

检查脚本：`tools/perf/viewports.py`（每档真实发 3 轮对话后再量）。

## 门槛（改后全绿）

| 项 | 结果 |
|---|---|
| `npm test`（node --test） | **139 / 139 pass** |
| `pytest server/tests` | **93 passed, 19 subtests** |
| `biome check js tests` | **0 error / 0 warning** |
| `verify_assets.py` | **PASS**（构建 51 文件，无泄漏） |
| `test_appearance.py` | **22 / 22 PASS** |
| `test_e2e.py` | **60 / 60 通过** |

E2E 中 TTS 保持 provider=none（4 条 503 属预期降级，符合静音约束）。

## 遗留

- **P0 静止 120 rAF/s**：需在 `stage.js` 的 `setMotionEnabled(false)`/unmount 路径里同时停掉
  `PIXI.Ticker.shared`（或 `app.destroy()`），并把「固定 600 ms 后停表」改成「首帧后立即停」。
  验收：`python tools/perf/measure.py --only power` 静止档 rAF 应从 7202 降到个位数。
- **包体 −4.12 MiB**：`Hiyori.2048` 全档未使用，需在 `scripts/build_assets.sh`（不在本会话文件域）
  排除，或与 live2d 会话确认后删除；可再压 WebP/量化 1024 档（需 live2d 会话点头）。
- **加载期 74 ms 长任务**：纹理解码，属 live2d 资源冻结范围；可用低分辨率纹理或延迟到空闲时段解码。
- **真机复测未做**：本轮未构建 APK/MuMu，滚动帧率与耗电数据来自 headless；建议主流程在 MuMu 上
  用 DevTools remote 复测「静止 60 s rAF」与 300 条历史滚动。
- `app.js` 的 `buildRow` 模板与 `risk.js` 正则危机判定不在本会话文件域，未做拆分；微基准显示
  窗口推进已降到 154 µs/条，后续收益主要在首条渲染与正则。
