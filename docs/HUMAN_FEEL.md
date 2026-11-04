# W11 · 情感陪伴「真人感」深度优化

> 分支 `feat/human-feel`，隔离 worktree `treehole-w11`，基线 `61384a0`。
> 本次只动服务端提示词 / 记忆能力 / 测试 / 文档；**未触碰** `web/**`、`android/**`、`scripts/test_e2e.py`、`scripts/test_appearance.py`。
> 全程静音：未启动 TTS、未占用 8901-8903；延迟实测使用动态端口。

## 0. 结论

| 项 | 结果 |
|---|---|
| 真人感做了什么 | 提示词新增【真人感硬约束】+【长期记忆槽的用法】两段（只加不删原五段）；服务端实现长期记忆槽（提取→槽→检索→注入）；64 条多轮评测集 + 可回归 harness |
| 评测通过率 | **54/64 = 84.4%**（mock 引擎实跑，10 类场景） |
| 危机 100% | **保持**：`test_crisis.py` 全过；`test_parity_with_client_thresholds` 通过；风险词表/阈值零改动 |
| 首字延迟 | mock SSE p50 **5ms**、p95 **22ms**；真实 provider 3 次采样首字 **613 / 1069 / 空响应**（≤1.5s，未压缩措辞） |
| 记忆槽设计 | 64 槽/用户、钉子户不淘汰、情绪权重×2+LRU+使用次数的混合淘汰；用户否定可删；详见 `docs/MEMORY_CONTRACT.md` |
| 遗留项 | 5 类失败全部在 **mock 模板引擎层**（沉默追问、老板名提取、记忆感知冷却、引导 hook 抢占、失眠不入大事），不在提示词/记忆链路；建议下一分支修引擎 |

回归门槛：`pytest server/tests` **122 passed + 19 subtests**；`node --test` **139 passed**；`biome check` **0 error**（53 条既有 warning，全部在未改动的 web 文件）。

## 1. 提示词：只加不删

原五段（安全三条 / 身份 / 长度跑题分寸 / 底线 / JSON 协议）逐字未动：

- `server/tests/test_prompt_parity.py` 继续通过：服务端 6 个常量与 `web/js/api.js` 逐字一致；
- `server/tests/test_human_feel_prompt.py::test_five_sections_untouched` 断言安全三条、长度 30/3/80、跑题、12356 底线、JSON 协议原文仍在；
- 段落顺序：安全 → 身份 → 真人感细则 → **真人感硬约束** → 记忆 → **长期记忆槽** → 底线 → JSON。

新增两段（`server/prompt_core.py`）：

```text
【真人感硬约束（只加表达，不覆盖上面的安全与硬约束）】
- 禁用套话：“首先/其次/再次/最后/总之/综上/总的来说/希望对你有帮助/如有需要/建议你/你可以试试/作为一个朋友”。
- 默认不给建议：ta 没问“怎么办/该怎么做”就不出主意、不列步骤；问了也只给一条具体的，不铺开。
- 允许偏移：可以不接 ta 的最后一句话，接前面某个细节；可以答非所问；不必把话题拉回情绪。
- 允许很短：一到两句就够；可以只回“嗯/在/不知道/我哪懂”，可以只发一个词。
- 允许说不知道：不懂就直说“这个我真不知道”，不装懂、不硬接、不绕成鸡汤。
- 允许沉默式回应：ta 只发“……/嗯/哦/累”时，不追问、不逼话，回一句短的陪着就行；不要求每轮都提问。
- 允许不好笑：玩笑没接住就说没接住，不硬捧场，不每条都夸。
- 记忆连续性：记得 ta 上次说的事，相关时自然提一句进展（“你上次说的那个考试”）；不确定就模糊提，不罗列、不背清单。
- 反模板：不排比、不三点式、不先总结再建议；同一轮里不把情绪词堆满。

【长期记忆槽的用法】
- 【你记得的关于 ta 的事】里每条是一个槽：类别 + 内容 + 最近提起。只取最相关的 1-2 条。
- ta 说“忘了/别记/删掉这个”时当场答应（“行，删了”），之后不再提；ta 纠正旧信息时以新说法为准。
- 不主动汇报“我记住了”，不把记忆当成绩单；只在对话里自然露出来。
```

每条都对应评测里可判定的规则（见 §3），不是“请更自然一点”这种空话。

## 2. 分层拼接（控首字延迟）

`prompt_core` 拆成：

- `static_core(profile="full")` / `CORE_PROMPT`：常量拼装，不随会话变化，可被上游前缀缓存；
- `system_prompt(memory_section=..., now=..., strict_json=False)`：动态段**只追加在末尾**（记忆槽 / 时间 / JSON 强约束），不会重排前文；
- `profile="lean"`：去掉两段长细则（1422 字），保留安全/长度/底线/JSON；仅在上游 prefill 明显变慢时降级用，默认 full。

## 3. 记忆链

完整设计、字段、容量、淘汰、删除、注入格式与 Web 接入契约见 **[MEMORY_CONTRACT.md](MEMORY_CONTRACT.md)**。摘要：

```
用户文本 → ingest：先删（忘了/别记/删掉这个）→ 规则提取/模型 memory_updates → 槽
        → recall：字面二元组 + 类别别名 + 情绪权重 + 最近度 + 使用次数 → 1-2 行
        → prompt_core.system_prompt(memory_section=...) → 下一轮 system
```

- 槽字段：`id/key/value/kind/emotion/confidence/source/created/updated/last_used/uses`
- 容量 64；钉子户（昵称/生日/大事/宠物 等）16 条内不淘汰；其余按 `emotion*2 + recency + uses*0.5` 淘汰
- 用户否定即删，返回被删项；`render()` 注入格式带“（今天/3 天前）”，提示词要求最多自然带 1 条
- 验证：`server/tests/test_memory_slots.py` 17 例；`test_memory_slot_chain_covers_followup_cases` 证明“若接入，追问类 4/4 应记得”

## 4. 对话质量评测

- 集子：`server/tests/human_feel_cases.json`，**64 条** 2-4 轮对话，10 类：日常倾诉 8 / 情绪低落 8 / 愤怒 6 / 暧昧 6 / 玩笑 6 / 跑题 6 / 追问 6 / 拒绝建议 6 / 沉默 6 / 重复话题 6。
- 每条都有 `criteria`（人类可读判定标准）+ 可选 `forbid_any` / `expect_any` / `max_advice_turns` / `silence`。
- Harness：`server/tests/test_human_feel_eval.py`，用 `mock_api.make_reply()` 逐轮跑（固定 seed、每例独立 session），检查：
  长度≤80、≤3 条、套话、总结复述、说教、建议轮次、复读/重复开头、非危机场景危机话术、跑题代码；
  沉默轮额外要求 ≤40 字/≤2 条/不含问号/不含“说说/然后呢/接着…”。
- 跑法：`python -m pytest server/tests/test_human_feel_eval.py -q -s`
- 门禁：整体 ≥75%，防止后续改动把真人感回退。

### 结果（本次实跑）

| 类别 | 通过 | 失败主因 |
|---|---|---|
| 日常倾诉 | 6/8 | 模板没接住具体细节；两轮都在给建议 |
| 情绪低落 | 8/8 | — |
| 愤怒 | 6/6 | — |
| 暧昧 | 6/6 | — |
| 玩笑 | 6/6 | — |
| 跑题 | 6/6 | — |
| 追问 | 4/6 | 老板名提取成“老板”；失眠不落“大事”；记忆感知冷却烧在提取轮 |
| 拒绝建议 | 6/6 | — |
| 沉默 | 1/6 | mock 通用池只会“然后呢/说说细节/接着” |
| 重复话题 | 5/6 | 两轮都劝早睡 |

**84.4%** 是模板引擎现状；提示词与记忆链路的改动只作用于在线真模型 + 后续接入，不能拉动 mock 模板分数。失败项已逐条列为遗留项，不调低标准。

## 5. 危机不能退

改动后重跑：

```
python -m pytest server/tests/test_crisis.py server/tests/test_risk_regression.py -q
```

- 危机集 **100% 通过**（含“不想活/想死/跳楼/药量”等）；
- `test_parity_with_client_thresholds` 通过（服务端/客户端阈值逐项一致）；
- `server/risk_guard.py` 零改动（题面“仅注释/日志”也未用，直接没动）。

## 6. 首字延迟实测

命令：`python scripts/measure_ttft.py --mock-n 20 --real-n 3`（动态端口、无 TTS）。

| 通道 | 采样 | 首字 p50 | 首字 p95 | 备注 |
|---|---|---|---|---|
| 本地 mock SSE | 20/20 | 5ms | 22ms | 用户离线路径 |
| 真实 provider（服务端代理 SSE） | 2/3 | 613ms | 1069ms | 1 次上游空 `content`，客户端按既有逻辑降级 mock |

提示词长度：1422 → **2032 字**（+610 字），约多 400-500 token 前缀；实测首字仍 ≤1.5s，**不需要压缩措辞**。客户端既有硬线 1400ms + 降级 mock 仍兜底。

## 7. 回归命令

```bash
python -m pytest server/tests -q          # 122 passed, 19 subtests passed
cd web && node --test "tests/*.test.js"   # 139 passed
cd web && npx -y @biomejs/biome@2.0.0 check js/... tests/  # 0 error
```

## 8. 遗留项（均为 mock/引擎侧，不在本分支文件域）

1. **沉默轮**：`mock_api.TOPIC["generic"]` 没有“嗯/在/陪你”这类短回应，`……` 会走进“然后呢，说说细节”。建议引擎侧加沉默池，命中 `……/嗯/哦/随便` 时不追问。
2. **老板名提取**：`extract_memory("我们老板叫张三")` 返回 `{"老板": "老板"}`（值 = 键），导致“记住张三”失效；需修 `mock_api` 提取函数。
3. **记忆感知冷却**：同一轮刚提取记忆就触发记忆感知，并烧掉 3 轮冷却，导致下一轮反而接不住（“年糕”案例）。应在 `mem_updates` 非空时跳过记忆感知。
4. **引导 hook 抢占**：前 4 轮“怎么称呼你”、前 8 轮“有啥大事”会覆盖主动回提，记忆回提要到第 5 轮才出现；评测已用 4-5 轮对话覆盖，但真实新用户触发更晚。
5. **失眠不入大事**：`RECALL_MAP["insomnia"] = ["大事"]`，但“失眠”不被提取成大事，失眠场景的回提永远不触发；本分支的 `memory_slots` 已把“状态”单列（`失眠/睡不着/熬夜…`），接入后即可召回。

修复这 5 项需要对 `server/mock_api.py` 和客户端离线引擎同步改动——按题面文件域（禁改 `web/**`，只允许 `prompt_core/memory*/risk_guard 注释/tests/docs`）留给下一分支。

## 9. 合并提示

- 本分支不 push、不合并、不开 PR；只提供分支名与 commit 供主合并流程合入。
- 与其它分支的潜在冲突：`server/prompt_core.py`（新增常量与 `system_prompt` 签名）、`server/tests/`（新增文件，无冲突）、`docs/`（新增文件）。
- `system_prompt()` 默认参数保持旧行为（full 全量），旧调用点无需改；新增能力是纯增量。

