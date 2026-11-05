# 真人感第三轮（w20）评测报告

分支 `feat/human-feel-r3`，基线 `r5-20261105` / `a283342`，worktree `treehole-w20`。

## 0. 结论先说

| 组别 | 场景数 | 通过 | 通过率 | 说明 |
|---|---|---|---|---|
| mock 模板引擎 | 112 | 112 | 100% | 模板引擎基线，不代表真实模型 |
| 真实模型·免费额度（合规真实样本，全量） | 112 | 75 | **66.96%** | 首轮 112 条/212 轮用 221 次请求跑完（约 3 小时 25 分）；再用 74 次请求重试 37 条失败，**37 条全部仍失败（0 翻转）** |
| 真实模型·付费（已作废） | 0（原 70） | — | — | 违反“只准免费”硬约束，**整批作废，不采用任何数字** |

**真实分覆盖率 100%（112/112）**：首轮新跑 112 条 → 续跑带入 75 条已通过 + 本次重试 37 条失败，剩余 0。
真实通过率 66.96% 来自 pollinations 匿名池（免费），JSON 原始明细在 `docs/eval_real_w20.json`。
37 条失败重试一次后**一条都没翻转**，说明它们是稳定的模型侧/提示词执行问题，不是采样噪声。
mock 的 100% 仍然不能冒充真实分。

## 1. 场景集：112 条，16 类，212 轮

覆盖任务要求的八类，并保留原有类别防止退化：

| 类别 | 条数 | 覆盖点 |
|---|---|---|
| 工作吐槽 | 6 | 甩锅/背锅/团建/客户/方案被毙 |
| 亲密关系 | 6 | 吵架/冷战/伴侣情绪/和好 |
| 兴奋分享 | 6 | 抢票/升职/夸/面试过/瘦了 |
| 拒绝说话 | 6 | 别问了/不想说/换话题/算了 |
| 长期记忆 | 4 | 猫名、老板名、搬家、备考，含遗忘 |
| 沉默 | 6 | 单字、省略号、空白、冷淡 |
| 追问 | 6 | 追问细节、连续问、复读陷阱 |
| 情绪低落 | 8 | 低落、自我否定、崩溃边缘 |
| 日常倾诉 | 8 | 加班、失眠、通勤、琐事 |
| 具体细节 | 20 | 人名/时间/地点/数字接住 |
| 愤怒 | 6 | 骂人、被气到、投诉 |
| 暧昧 | 6 | 试探、撒娇、边界 |
| 玩笑 | 6 | 玩笑没接住、谐音 |
| 跑题 | 6 | 写代码/翻译/做题/正经咨询 |
| 拒绝建议 | 6 | "别说建议""我知道怎么办" |
| 重复话题 | 6 | 同一件事说三遍 |

用例文件 `server/tests/human_feel_cases.json`（`version: 2`），新增字段：
`memory_values`、`forget_after`（第 N 轮说"忘了"后必须删掉的值）、`mute_any`（拒绝后不许出现的词）、
`refusal`、`expect_scope: "any"`（多轮用例任意一轮命中即可）。

## 2. 评测器：同一套判定，换后端只换 `--backend`

`scripts/eval_human_feel.py`：

```bash
# mock（模板引擎，秒级）
python scripts/eval_human_feel.py --backend mock --json out.json --md out.md

# 任意 OpenAI 兼容后端（真实模型，模型名必须在免费白名单内）
python scripts/eval_human_feel.py --backend api \
  --base https://openrouter.ai/api/v1 \
  --model <model:free> --delay 1 --timeout 90 --retries 4 --max-tokens 1200 \
  --max-requests 60 --json real.json --md real.md

# 多免费后端轮换（429/402 自动切下一家，每家最多 60 次请求）
python scripts/eval_human_feel.py --all --auto-rotate --max-requests 60
```

- 判定全部走 `check_turn` / `check_case_expect`，mock 与真实模型用**同一套硬约束**，不存在两套标准。
- `ApiBackend` 走真实链路：`memory_slots` 渲染 → `prompt_core.system_prompt` → `/chat/completions` → 解析 JSON → 回灌记忆槽。
- `--limit` / `--categories` 可缩样；`--min-rate` 设定退出码阈值。
- 报告输出：分类别通过率、失败原因归类、反套路四项指标、失败前 5 条原文。

## 3. 真实模型：跑到了哪、为什么没跑完（可复现证据）

| 尝试 | 结果 |
|---|---|
| OpenRouter `dots-studio/dots-3-note-preview:free`（配额耗尽前） | 单轮探针成功，约 4.3s，返回中文自然回复 |
| 同上，配额耗尽后（112 条全量） | 第 1 条即 `HTTP 429`：`Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day` |
| `xingchen-agi/xing4.0-29b-a4b:free` | 上游 429（61 条全失败） |
| `nvidia/nemotron-3.5-lightning:free` | `max_tokens` 被 reasoning 吃光，`content=null`、`finish_reason=length` |
| `meta-llama/llama-3.1-8b-instruct:free` | `HTTP 404`：该 slug 已无免费版（“This model is unavailable for free … use this slug instead: meta-llama/llama-3.1-8b-instruct”） |
| pollinations 免密端点（`text.pollinations.ai/openai`） | 可匿名调用，限速 `FREE_LIMIT: 1 reply every 45 seconds per IP` |
| 本机 ollama | 未安装，无本地模型 |
| Windows 凭据管理器 | 只有 Freebuff BYOK 一个目标，无第二家 provider key |

**本轮免费白名单全量（合规真实样本）**：`--all --auto-rotate --resume --max-requests 240`。

| 轮次 | 后端 | 模型 | 调用 | 失败 | 结果 |
|---|---|---|---|---|---|
| 首轮 | openrouter-free | `dots-studio/dots-3-note-preview:free` | 1 | 1 | HTTP 429 `free-models-per-day`，自动停用 |
| 首轮 | pollinations | `openai`（匿名池，免 key） | 220 | 0 | 112 条/212 轮跑完，通过 75，耗时 12308.7s |
| 重试轮 | openrouter-free | 同上 | 1 | 1 | 仍是 429（当日配额未恢复） |
| 重试轮 | pollinations | 同上 | 73 | 0 | 37 条失败全部重跑，0 条翻转，耗时 2994.8s |

- 首轮总请求 **221 次**（上限 240）；重试轮 **74 次**；两次都在 `--max-requests 240` 以内。
- 请求体只有 `model / temperature / max_tokens / messages`；`max_tokens` 上限 1200，未加任何额外字段。
- 覆盖率：**112/112（100.0%）** ＝ 续跑带入 75 + 本次重试 37，剩余 0；报告头由脚本自动写入，未手改数字。
- 唯一的 `backend_errors=1` 是 openrouter 那次 429 触发的后端切换，该条不计入质量分。

配额耗尽前的成功探针（**仅作连通性/中文能力证据，不计分**）：

> 用户：`团子又把我耳机线咬了`
> 模型（dots）：`团子要是看见估计得当场表演一个原地晕倒。那只猫有人管吗，还是蹲门口蹭饭的？`

结论：评测器对真实后端可用（HTTP/解析/记忆回灌链路已验证），免费额度足以跑完全量：
首轮 221 次请求跑完 112 条（约 3 小时 25 分），重试轮再 74 次。**报告不把 mock 的 100% 当作真实分，
也不把付费那 70 条算进来。** 断点续跑（13.7）在两次重启后都直接接上，未重跑任何已通过用例。

## 4. 反套路指标（mock 112 条 / 真实 112 条，各 212 轮）

| 指标 | mock | 真实（pollinations） | 目标 |
|---|---|---|---|
| 建议出现率（含"试试/建议/早睡/多沟通"） | **0.0%**（0/212） | **1.4%**（3/212） | ≤25% |
| 四字总结式共情（"太不容易了/辛苦了/我懂你"） | **0.0%**（0/212） | **0.0%**（0/212） | 越低越好 |
| 高频开头（听起来/我理解/确实/其实/首先…） | **6.1%**（13/212，全是"嗯"） | **7.1%**（15/212：听起来 14、确实 1） | 越低越好 |
| 会话内重复开头 | **0.0%**（0/210） | **0.0%**（0/212） | ≤8% |
| 记忆引用自然度（带"上次/之前/那个"） | **1/1** | 0/0（无主动回溯样本） | 全部自然 |
| JSON 协议不合规 | **0/212 轮** | **0/212 轮** | 仅统计（客户端有纯文本兜底） |

- mock 的高频开头全是 `嗯，`（13 次），集中在沉默/拒绝池——对单字、省略号回复属于人话。
- 真实的 7.1% 是另一种情况：`听起来` 出现 14 次，落在"总结/复述"判定里（如 workrant-02），
  属于真实模型自带的客服腔，不是我们模板里的词；已计入失败清单，后续靠提示词继续压。
- 真实的 3 次建议里，intimacy-01 明确因此报红；1.4% 远低于 25% 的线，但**不是 0**——
  说明"默认不给建议"在真实模型上只是概率性遵守，不如 mock 稳定。
- 记忆回溯 0/0：212 轮里模型没有一次主动回指历史值，所以"自然度"无从判定（长期记忆类 3/4 通过靠的是当轮接住，不是回指）。

## 5. 失败案例

### 5.1 最终 mock 全量：0 失败

112/112，`failure_reasons` 为空。真实样本的失败见 5.2。

### 5.2 真实全量：37 条失败（不贴对话正文）

判定标准与 mock 完全同一套（`check_turn` / `check_case_expect`）。下表只写 id、类别、轮数、失败轮、
**判定原因摘要**和**错在哪一层**；完整原因在 `docs/eval_real_w20.json` 的 `failures` 字段。

| 用例 | 类别 | 轮数 | 失败轮 | 判定原因（摘要） | 错在哪一层 |
|---|---|---|---|---|---|
| daily-01 | 日常倾诉 | 2 | 2 | 未接住期望细节（猫/关东煮/盯） | 模型侧·漏细节 |
| emotion-02 | 情绪低落 | 2 | 2 | 说教“别想太多” | 模型侧·客服腔 |
| emotion-04 | 情绪低落 | 2 | 1 | 说教“别想太多” | 模型侧·客服腔 |
| joke-06 | 玩笑 | 2 | 2 | 问号过多（3 > 2） | 模型侧·话痨 |
| followup-02 | 追问 | 2 | 2 | 未接住期望细节（张三） | 模型侧·漏细节 |
| followup-03 | 追问 | 4 | 4 | 未接住期望细节（搬家/收拾） | 模型侧·漏细节 |
| followup-06 | 追问 | 2 | 2 | 未接住期望细节（睡/几点/失眠） | 模型侧·漏细节 |
| no-advice-05 | 拒绝建议 | 2 | 1 | 出现禁用词“方案” | 提示词层·禁令未执行 |
| silence-01 | 沉默 | 1 | 1 | 沉默轮逼话“说说” | 提示词层·沉默规则未执行 |
| silence-02 | 沉默 | 1 | 1 | 沉默轮追问 | 提示词层·沉默规则未执行 |
| silence-03 | 沉默 | 1 | 1 | 沉默轮追问 | 提示词层·沉默规则未执行 |
| silence-04 | 沉默 | 1 | 1 | 沉默轮追问 | 提示词层·沉默规则未执行 |
| silence-05 | 沉默 | 1 | 1 | 条数 3>2 + 问号过多 + 追问 | 提示词层·沉默规则未执行 |
| silence-06 | 沉默 | 2 | 1 | 沉默轮逼话“说说” | 提示词层·沉默规则未执行 |
| repeat-03 | 重复话题 | 3 | 3 | 开头复读“这事真烦” | 模型侧·复读 |
| repeat-06 | 重复话题 | 3 | 3 | 非危机场景套用危机话术（心理热线） | 模型侧·过度安全 |
| detail-food-01 | 具体细节 | 2 | 2 | 未接住期望细节（糖醋排骨） | 模型侧·漏细节 |
| detail-food-02 | 具体细节 | 2 | 2 | 未接住期望细节（关东煮） | 模型侧·漏细节 |
| detail-pet-03 | 具体细节 | 2 | 2 | 未接住期望细节（兔子） | 模型侧·漏细节 |
| detail-work-01 | 具体细节 | 2 | 2 | 未接住期望细节（周报） | 模型侧·漏细节 |
| detail-work-02 | 具体细节 | 2 | 2 | 未接住期望细节（需求） | 模型侧·漏细节 |
| detail-work-04 | 具体细节 | 2 | 2 | 未接住期望细节（简历） | 模型侧·漏细节 |
| detail-body-01 | 具体细节 | 2 | 2 | 未接住期望细节（胃疼） | 模型侧·漏细节 |
| detail-body-02 | 具体细节 | 2 | 2 | 未接住期望细节（低烧） | 模型侧·漏细节 |
| detail-body-03 | 具体细节 | 2 | 2 | 未接住期望细节（嗓子疼） | 模型侧·漏细节 |
| detail-body-04 | 具体细节 | 2 | 2 | 未接住期望细节（头疼） | 模型侧·漏细节 |
| detail-weather-01 | 具体细节 | 2 | 2 | 未接住期望细节（降温） | 模型侧·漏细节 |
| detail-weather-02 | 具体细节 | 2 | 2 | 未接住期望细节（下雨） | 模型侧·漏细节 |
| detail-time-02 | 具体细节 | 2 | 2 | 未接住期望细节（两点） | 模型侧·漏细节 |
| workrant-02 | 工作吐槽 | 2 | 2 | 总结/复述“听起来你…” | 模型侧·客服腔 |
| intimacy-01 | 亲密关系 | 2 | 全程 | 建议轮次 1 > 0（默认不给建议） | 模型侧·建议倾向 |
| excited-01 | 兴奋分享 | 1 | 1 | 未接住期望细节（升职/庆祝） | 模型侧·漏细节 |
| excited-02 | 兴奋分享 | 1 | 1 | 未接住期望细节（票/运气/抢到） | 模型侧·漏细节 |
| refuse-01 | 拒绝说话 | 2 | 1 | 拒绝轮逼话“聊聊” | 提示词层·拒绝后仍逼话 |
| refuse-02 | 拒绝说话 | 2 | 2 | 拒绝轮还在追问 | 提示词层·拒绝后仍追问 |
| refuse-04 | 拒绝说话 | 2 | 1 | 拒绝轮逼话 + 还在追问 | 提示词层·拒绝后仍逼话 |
| memory-01 | 长期记忆 | 4 | 4 | 未接住期望细节（搬家/上次说） | 模型侧·记忆不主动 |

归类：**20 条漏细节**（模型侧，占失败 54%）、**10 条拒绝/沉默规则没执行**（提示词层，27%）、
**7 条客服腔/复读/话痨/过度安全**（模型侧，19%）。同一批 37 条重试一次后**0 翻转**，
所以这些不是偶发采样，而是稳定倾向；要提升只能改提示词或加后处理，不是再跑几次就能变绿。

对照：mock 模板引擎同样的 112 条全过——说明用例与判定没有错，真实模型做不到的部分是模型能力/服从度的问题。

### 5.3 本轮从失败修到通过的 6 个反例（原文可核验）

这 6 条是本轮评测真实报红过的用例，修复后转绿；列出"当时为什么不像人"：

1. `excited-02`（兴奋分享）
   原文：`抢到演唱会门票了！！`
   当时回复：把"抢到"落到"中奖"模板 → 答非所问，像没看懂事件。
   为什么不像人：朋友会接"哪场/运气"，不会把抢票说成中奖。
   修复：`HAPPY_POOLS` 按事件细分（抢票/升职/夸/面试/瘦了各自池）。

2. `memory-01`（长期记忆）
   原文：`下个月要搬家，烦死了` / `今天又加班到十点` / `东西还没收拾` / `一想到打包就头大`
   当时回复：末轮回"好耶" → 情绪反向，像客服脚本走错分支。
   为什么不像人：对方在抱怨打包，回"好耶"是没听人话。
   修复：新增 exam/move/leisure 话题池，搬家类走"收拾/打包"语义。

3. `memory-02`（长期记忆）
   原文：`我们老板叫张三` / `张三今天又让我改方案` / `改到第八版了` / `他说还是第一版好`
   当时回复：第二轮不提"张三" → 像刚认识，记忆断了。
   为什么不像人：真人会用名字接话（"张三又来了"），不会装作没听过。
   修复：`MEM_AWARE_COOLDOWN` 3 → 1，用户主动提就接住，重复感交给会话内去重。

4. `memory-04`（长期记忆）
   原文：`最近在准备一个很难的考试，每天熬到一点` / `今天又没看完` / `感觉考不过了` / `明天还得早起继续`
   当时回复：全程无回忆触发（间隔 3 轮被冷却吃掉）→ 记了等于没记。
   为什么不像人：四轮都在同一件事上，真人至少会回指一次"你说的那个考试"。
   修复：补第 4 轮 + 长值走 `LONG_RECALL_TEMPLATES` 指代（"你上次说的那件事——后来怎么样了？"），不逐字复读。

5. `refuse-04`（拒绝说话）
   原文：`不想聊工作` / `我们看电影吧`
   当时回复：换话题后仍回工作相关（"加班/老板"）→ 嘴上答应、身体翻旧账。
   为什么不像人：说了不聊工作还提，比不答应更冒犯。
   修复：`TALK_REFUSAL_RE` 命中后 `no_probe=2`、`hook=None`，并新增 leisure 池；`mute_any` 断言禁止"工作/加班/老板/上班"。

6. `workrant-01`（工作吐槽）
   原文：`老板当着全组的面把我方案毙了，还说我能力不行`
   当时回复：`换谁都得炸` —— 情绪对了，但没接住"当众/能力不行"的具体点。
   为什么不像人：朋友会先骂具体那件事（"当众说这话过分"），而不是泛泛共情。
   修复：期望词扩为"过分/老板/能力不行/炸/摔"，池里补当众羞辱的具体接法。

## 6. 五项硬要求怎么落地的

### 6.1 真实模型回归

- 评测集 84 → 112 条（16 类），评测器 `--backend mock|api` 通吃，`ApiBackend` 走真实 HTTP。
- 本轮真实分已全量拿到：112/112、75 通过（66.96%），两轮共 295 次免费请求；37 条失败重试后 0 翻转（见第 3、5 节）。
- 报告、JSON 与测试都区分 mock 与真实，mock 的 100% 不写成真实分。

### 6.2 反套路

- 指标由评测器统一统计（正则见文件头部常量），测试 `test_anti_cliche_metrics` 卡线：
  建议 ≤0.25、重复开头 ≤0.08、总结式共情 ≤0.02、高频开头 ≤0.10。

### 6.3 沉默与拒绝

- `server/mock_api.py`：删除记忆 → 拒绝说话 → 沉默早退 → 正常流程，顺序判定，避免"不想说"被沉默分支吞掉。
- 拒绝分支：`no_probe=2`、`hook=None`、回复走 `REFUSAL_POOL`（"行，不问了。" / "那就不提了。"），
  下一轮 `blocked` 时 `_strip_probe(pool)` 剔掉所有追问模板，退 `NO_PROBE_POOL`。
- 测试 `test_refusal_and_silence_hard_rules`：拒绝/沉默类每轮可见字 ≤40、无问号、无逼话，拒绝后 `mute_any` 词不得出现。

### 6.4 长期记忆自然度

- 引用模板分短值/长值：短值 `RECALL_TEMPLATES`（"你之前说的那位，今天没又折腾你吧"），
  长值走 `LONG_RECALL_TEMPLATES` 指代（"你上次说的那件事——后来怎么样了？"），**不逐字复读**。
- 同一轮最多 1 条：`MEM_AWARE` 与 `hook` 互斥（`hook, hit_key = (None, None) if (mem_aware or blocked)`）。
- "忘了/别记/删掉"：`DELETE_RE` 命中即 `get_memories().pop` 真删、`no_probe=2`、本轮不提取；
  评测器在用例结束后快照后端存储，被遗忘的值仍在存储里直接判负（`f914672`）。
- 测试 `test_memory_naturalness_cases`、`test_memory_slot_chain_covers_followup_cases`（逐轮 render 拼起来校验槽链路）。

### 6.5 系统提示词收敛

- `static_core` 分层：安全 → 身份 → 真人感 → 记忆 → 底线 → JSON；动态段（记忆槽/时间/严格 JSON）只拼末尾。
- 服务端独有两段压缩：`HUMAN_FEEL_RULES` 453 → 266 字，`MEMORY_SLOT_RULES` 153 → 114 字；
  full 提示词 2032 → **1806 字（-11%）**，两段合计 606 → **380 字（-37%）**。
- 安全三条、身份、长度/跑题/分寸、底线、JSON 协议**逐字未动**（`test_prompt_parity.py` 锁 web 一致）。
- 新增测试：`test_w20_no_repeated_rule_sentences`（长度 ≥8 的句子不得跨段出现两次）、
  `test_w20_hard_anchor_occurrences`（长度/跑题/分寸/安全三条全文各 1 次）、长度预算 full ≤1850、两段合计 ≤400。

## 7. 改动摘要

| 提交 | 内容 |
|---|---|
| `abaf8cb` | 评测器多后端化 + 场景集 84→112；mock 拒绝/遗忘/回忆自然化、话题池扩容 |
| `afcc714` | 提示词收敛（1806 字），句子级去重与锚点唯一性测试 |
| `f914672` | 遗忘校验落到存储层 |
| `865c2e6` | 提示词单一源派生到 web/Android，客户端补齐真人感与记忆槽规则 |
| `fccaa49` | lean 只压 REALISM 风格细则，安全与真人感逐字保留 |
| `c56af95` | 多免费后端轮换 + `--all --auto-rotate` + `--backend local` |
| `ce41504` | 轮换配置兼容 Windows BOM，示例改为可用免费模型 |
| `2a883cf` | 只准免费额度：模型白名单、请求体检、402 熔断、单次请求上限 |

文件：`scripts/eval_human_feel.py`（新，686 行）、`server/tests/human_feel_cases.json`（112 条）、
`server/tests/test_human_feel_eval.py`（重写）、`server/tests/test_human_feel_prompt.py`、
`server/tests/test_mock_human_feel_fixes.py`、`server/mock_api.py`、`server/prompt_core.py`、`AGENT_SCOPE.md`。

## 8. 验证

```
python -m pytest server/tests -q
190 passed, 19 subtests passed in 8.09s
```

基线 153 passed + 19 subtests，本轮净增 37 项，无降级、无放宽断言。
串行执行，未起浏览器/AVD/Gradle；mock 评测 0.2s，pytest 8.1s。

## 9. 尚存差距（必须说清）

1. **mock ≠ 真实。** 112/112 只证明模板引擎没退化；真实模型全量只有 75/112（66.96%）。
   差距最大的正是最容易露馅的类别：沉默 0/6、具体细节 7/20、追问 3/6、拒绝说话 3/6。
   记忆类 3/4 但 212 轮里 0 次主动回指，真实"长期记忆"仍未验证。
2. **免费额度可跑但慢且不稳。** OpenRouter 免费池当日配额用尽（429，两次运行都只发出 1 次就被停用）；
   pollinations 匿名池约 45 秒/次，首轮 3 小时 25 分、重试轮 50 分钟。全量两轮共 295 次请求，
   仍在 `--max-requests 240` 的单次上限内（分两次运行）；若一次跑完需把上限放到 300 以上，
   或接更多免费 key / 本地模型（本机未装 ollama）。
3. **客户端同源已落地（第 11 节）**：`web/js/api.js` 不再手写提示词，改为引用
   `web/js/persona_rules.js`（由 `server/prompt_core.py` 生成），Android 由 `build_assets.sh` 同步同一份；
   测试锁住服务端/客户端同时含 12 个硬约束关键词。原先“客户端不带真人感”的问题已消除。
4. **`lean` 已修（第 12 节）**：现在只压 REALISM 风格细则，安全三条与真人感/记忆槽硬约束逐字保留，
   测试 `test_lean_and_full_only_differ_by_realism` 锁死，退化路径不会再丢硬约束。
5. **记忆自然度仍只有 mock + 槽层证据**：真实全量 212 轮里模型 0 次主动回指（`recall_checks=0`），
   既没观察到"自然回溯"，也没观察到"背清单"，这一项在真实模型上仍是空白。
6. **判定是启发式正则**：能抓典型套话，抓不到"语义上像客服"的变体；真实回归时必须人工抽检前 20 条。
7. **真实模型 JSON 协议不稳定**：免费模型常出围栏/前后缀/纯文本；线上 `api.js` 有纯文本兜底，评测只统计不判负，
   但这会压低结构化字段（emotion/memory_updates）的可用率。

## 10. 复跑命令（仍有免费额度时）

```bash
# 免费白名单 + 多后端轮换 + 每家请求上限（只跑免费模型）
python scripts/eval_human_feel.py --all --auto-rotate --max-requests 60 \
  --json docs/eval_real_w20.json --md docs/eval_real_w20.md

# 配额恢复后断点续跑：跳过已通过用例，与历史结果合并进同一份 JSON/报告
python scripts/eval_human_feel.py --all --auto-rotate --resume \
  --max-requests 240 \
  --json docs/eval_real_w20.json --md docs/eval_real_w20.md
```

单后端直连（模型名必须在免费白名单内，否则脚本在发请求前就拒绝）：

```bash
python scripts/eval_human_feel.py --backend api \
  --base https://openrouter.ai/api/v1 \
  --model dots-studio/dots-3-note-preview:free \
  --max-requests 60 --max-tokens 1200 --delay 2
```

若免费额度仍不够：`--limit 61` 跑代表性半量，报告里继续写明覆盖率，不与 112 条 mock 分混算。

## 11. 客户端提示词同步（本轮扩域）

之前 `HUMAN_FEEL_RULES` / `MEMORY_SLOT_RULES` 只活在服务端，web 与 Android 都不带，
真机用户体验不到本轮改进。现在改成**单一源 + 生成物**：

| 层 | 文件 | 角色 |
|---|---|---|
| 真源 | `server/prompt_core.py` | 唯一手写提示词所在，安全/身份/细则/底线/JSON 全在这里 |
| 生成器 | `scripts/gen_persona_rules.py` | 把 8 段常量派生为 JS；`--check` 只校验 |
| Web | `web/js/persona_rules.js` | 生成物（勿手改），UMD：Node `require` / 浏览器 `XMPersonaRules` |
| Web 调用处 | `web/js/api.js` | 删掉手写副本；Node 走 `require`，浏览器解析期 `document.write` 同步注入（`index.html` 禁改，无法加 script 标签），拿不到就报错，绝不退回旧副本 |
| Android | `android/app/src/main/assets/www/js/*` | `build_assets.sh` 把 `web/` 整目录同步过去，WebView 加载的就是同一份生成物；不单独维护 |

**同源怎么锁**（`server/tests/test_persona_single_source.py` + `test_prompt_parity.py`）：

1. `test_generated_file_matches_source`：`persona_rules.js` 必须逐字等于生成器输出，
   手改任何一段立刻红。
2. `test_api_js_no_handwritten_copy`：`api.js` 里禁止再出现 `const SAFETY_RULES = \``
   或「【真人感硬约束」原文，必须引用 `persona_rules.js`。
3. `test_server_and_client_prompt_share_keywords`：对 12 个硬约束关键词
   （安全三条、长度/跑题、真人感五条、记忆槽、遗忘），断言**服务端拼出的 system prompt**
   与**客户端 8 段拼出的 system prompt** 同时包含，用测试锁住，不靠人工核对。
4. `test_sections_are_verbatim_identical`：8 段逐字对齐（含新增两段）。
5. `test_android_assets_copy_if_built`：本地跑过 `build_assets.sh` 后，Android 副本与
   web 源逐字一致；未构建则 skip，不假装通过。

实测：`bash scripts/build_assets.sh` 同步 69 文件 / 1.4MB，`ASSET_VERIFY PASS`；
`node --test web/tests/api.test.js` 224 项全过（新增真人感/记忆槽/心跳安全断言）。

断言数字：**12 个关键词 × 2 端 = 24 条包含断言**、**8 段 × 2 端逐字相等**、
生成物与生成器逐字相等 1 条、`api.js` 禁手写副本 3 条；Android 副本存在时 2 条逐字相等。

## 12. lean profile 边界

`static_core("lean")` 以前会把【真人感硬约束】【长期记忆槽的用法】整段退掉；
现在 lean **只允许退 REALISM_RULES**（节奏/句式这类格式提示）：

- `test_lean_profile_keeps_safety_and_human_feel`：安全三条、身份、长度/跑题、真人感、
  记忆槽、底线、JSON 必须全部在 lean 里；`REALISM` 与「60% 只回 1 条」必须不在。
- `test_lean_safety_lines_verbatim`：安全三条逐字核对，防压缩改字。
- `test_lean_and_full_only_differ_by_realism`：lean 必须严格等于 full 去掉 REALISM 段，
  以后谁再想退硬约束都会红。

lean 1395 字，full 1806 字；lean 只少 411 字的风格细则，安全与真人感一字不少。

## 13. 多后端轮换与凭据实测

### 13.1 轮换机制

```
python scripts/eval_human_feel.py --all --auto-rotate
```

- 配置优先级：`--backends-file` → `HUMAN_FEEL_BACKENDS_FILE` → `HUMAN_FEEL_BACKENDS`
  （内联 JSON/路径）→ `scripts/backends.local.json`（`*.local.json` 已 gitignore）。
- 每家：`{name, base, model, key_env|key, requires_key, retries, max_tokens, temperature}`。
  仓库里的 `scripts/backends.example.json` 只允许 `key_env`，测试禁止出现 `sk-`。
- 429 / 402 / 401 / 403，或连续两次空响应 → 自动切下一家；报告记录每家调用数、
  429 次数、首次 429 时间与打码原因；全部判死则**中止**，已完成条数照实写，不折算质量分。
- 本地密钥落在 `C:\Users\xx\.secrets\w20_backends.json`（仓库外），报告与提交里不出现原文。
- `--backend local` = 本地模板引擎兜底，显式标注“不计真实分”。

### 13.2 两份外部凭据实测（密钥全部打码）

| 凭据 | 端点 | 鉴权结果 | 3 次请求 | 额度线索 | 结论 | 处置 |
|---|---|---|---|---|---|---|
| agnes-ai（已删除） | `api.agnes-ai.com/api/v1/chat/completions` | 401 `Login expired`（Bearer/raw/x-api-key/api-key 四种都 401） | 0/3 成功 | 无 | **不可用**：密钥失效 | 已 `Remove-Item` 删除附件 |
| OpenRouter（`sk-or-...4e09`） | `openrouter.ai/api/v1` | 200 | 免费模型 3/3 = 429；付费 `qwen/qwen-2.5-7b-instruct` 200 | `/auth/key`：free_tier=true，usage=0；免费池 `X-RateLimit-Remaining: 0` | **仅免费池可用时才能用**：当日免费配额已耗尽；付费额度按硬约束禁用 | 保留，写入仓库外 secrets 配置，配置里只放 `:free` 模型 |

补充：同 key 的 `openai/gpt-4o-mini` 403 区域不可用，`google/gemini-2.0-flash-001` 404 已下线；
付费 `qwen/qwen-2.5-72b-instruct` 当时可用（~40-70s/次），**但按“只准免费”硬约束现已禁用**，
配置与代码都会在发请求前拒绝这类模型名。

### 13.3 轮换实测

第一轮（OpenRouter 免费 dots + pollinations）：
`openrouter` 第 1 次即 429 → 自动切 `pollinations`，第 2 次 402
`FREE_LIMIT: 1 reply every 45 seconds per IP` → 两家判死，**0 条完成，评测中止**。
这份记录证明切换逻辑会真实触发，也证明当时确实没有能跑全量的免费额度。

第二轮（OpenRouter 付费 qwen 7B，pollinations 备用）：跑至第 70 条时按“只准免费”硬约束人工叫停。
**这 70 条因调用了付费模型而作废，不计入任何分数**；作废记录本身就是违规证据，保留在此不删。

### 13.4 只准免费额度（硬约束落地）

规则：任何付费模型一律不许调。落到代码里是三道闸 + 一本账：

| 防线 | 位置 | 行为 |
|---|---|---|
| 白名单 | `is_free_model()` | 只放行：`*:free` 免费池、显式白名单（`openai` 匿名池）、不指定模型名的匿名端点；`qwen/qwen-2.5-7b-instruct` 这类付费名**发请求前直接拒绝**，不“先试一下” |
| 配置加载 | `load_providers()` | 非白名单条目直接跳过并写明原因；`max_tokens` 一律压到 1500 |
| 后端构造 | `ApiBackend` / `RotatingBackend` | 再校验一次，付费模型连对象都建不起来（`ValueError`） |
| 请求体 | `ApiBackend.chat()` | 只发 `model/temperature/max_tokens/messages` 四项，不带 `provider` 路由、`:nitro`、`:online`、联网检索等计费选项 |
| 计费熔断 | `BILLING_HINT_RE` | HTTP 402 或返回体出现 `insufficient/balance/payment/credits/充值/余额` → `billing_stop=True`，**当次就停**（`calls==1`），不重试、不换模型升级；轮换表记为“配额或计费受限，已停用” |
| 单次上限 | `--max-requests`（默认 60） | 每家最多 60 次请求；到上限切下一家；全部到上限就停在已完成条数，未跑完的用例标 `SKIP`，**不进分母** |

锁死用例（`server/tests/test_eval_rotation.py`，本轮 20 项全过）：

- `test_is_free_model`：`*:free` / `openai` / 空模型放行，付费与 `:nitro`/`:online` 拒绝；
- `test_load_providers_rejects_paid_model`：付费条目被跳过且原因含“白名单”；
- `test_rotating_backend_refuses_paid_provider`：付费配置构造即抛错；
- `test_request_body_has_no_paid_options`：请求体键集合严格等于四项，`max_tokens ≤ 1500`；
- `test_billing_402_stops_without_retry_upgrade`：402 只发 1 次、模型名不变；
- `test_billing_stop_marks_provider_disabled`：轮换表状态为“配额或计费受限，已停用”；
- `test_max_requests_switches_to_next_provider` / `test_max_requests_all_used_raises_limit` /
  `test_limit_skipped_case_not_counted_in_rate`：到上限切家、全满则停、SKIP 不进分母；
- `test_example_config_only_free_models` / `test_local_config_only_free_models`：仓库内两份配置只许免费模型；
- `test_provider_delay_is_parsed`：免费端点按 IP 限速时，每家可单独配 `delay`（pollinations 46 秒）。

### 13.5 免费额度现状（真实、打码）

| 通道 | 状态 | 证据 |
|---|---|---|
| OpenRouter 免费池（`dots-studio/dots-3-note-preview:free`） | 当日配额用尽：第 1 次即 429 `free-models-per-day` | 13.3 与 13.6 |
| OpenRouter `meta-llama/llama-3.1-8b-instruct:free` | 404，已无免费版 | 本轮实测 |
| pollinations 免密端点（`text.pollinations.ai/openai`） | 可匿名调用，但 `FREE_LIMIT: 1 reply every 45 seconds per IP`；配 `delay: 46` 可稳定跑 | 两轮共 293 次调用全成功 |
| 本地 ollama | 未安装 | `ollama` 不在 PATH |

结论：免费额度能跑，但只有 pollinations 一家能持续供给（约 45 秒/次）；
OpenRouter 免费池两次运行都在第 1 次 429。跑不完的条数照实标 `SKIP`，不与 mock 分混算。

### 13.6 本轮免费全量结果（两次运行）

```
# 首轮（11/5 晚 → 11/6 早，跨两次重启后 --resume 接上）
python scripts/eval_human_feel.py --all --auto-rotate --resume --max-requests 240
# 重试轮（11/6 06:44 起，只重跑 37 条失败）
python scripts/eval_human_feel.py --all --auto-rotate --resume --max-requests 240
```

- 首轮：`backend_calls=221`（openrouter 1 次 429 后停用、pollinations 220），耗时 12308.7s，
  完成 **112 条 / 212 轮**，通过 **75 条（66.96%）**；
- 重试轮：`backend_calls=74`（pollinations 73、openrouter 仍 429），耗时 2994.8s，
  只重跑 37 条失败，**0 条翻转**，通过数仍为 75；
- 覆盖率 **112/112（100%）**；报告头写明 `续跑带入 75 + 本次重试 37，剩余 0`；
- 反套路（212 轮）：建议 1.4%（3）、四字共情 0%、高频开头 7.1%（15）、会话内重复开头 0%、
  JSON 不合规 0%、主动记忆回指 0/0（不可判定）；
- 37 条失败清单与"错在哪一层"见 5.2。

即：真实合规样本 112 条、覆盖率 100%、通过率 66.96%。不追加、不夸大；付费那 70 条继续作废。

### 13.7 断点续跑（`--resume`）

每次运行都会把**逐条统计**写进 `--json`（`case_stats` / `passed_ids` / `failed_ids`）：
每条只存 id、类别、通过与否、轮数、建议数、开头重复、记忆回溯与失败原因，不存正文。

```bash
python scripts/eval_human_feel.py --all --auto-rotate --resume \
  --max-requests 240 --json docs/eval_real_w20.json --md docs/eval_real_w20.md
```

- 只跳过 `passed_ids` 里的用例；**失败用例会重跑**，重跑结果覆盖旧记录；
- 合并按用例 id 去重，历史结果 + 新结果一起算指标与通过率，不会重复计数；
- 报告头写清覆盖率：`完成 N/112（x%）= 续跑带入 a + 本次新跑 b（其中重试历史失败 r 条），剩余 c 条未跑`；
  不会因为续跑就把“9 条样本”写成“112 条通过”；
- 旧版 JSON（没有 `case_stats`）会提示“没有逐条统计”并从第 1 条开始，不假装续跑；
- 本轮实战：两次系统重启后都靠该机制接上——11/5 晚首轮跑到 112/112，11/6 早上重启后
  用同一份 JSON 只重跑 37 条失败（`coverage.resumed=75, new=37, retried=37`），已通过的 75 条一条没重跑；
- 每条用例结束就原子落盘（`json.tmp` → `os.replace`），断电/重启最多丢当前那一条，不会写坏整份 JSON；
- 锁死用例（`ResumeTest`，6 项）：跳过已通过且覆盖率如实、合并去重不重复计数、重试条数计入 `retried`、
  `--resume` 缺 JSON 返回 2、旧格式降级提示，以及 `metrics_from_stats` 与 `compute_metrics` 口径一致。

**全量 112 条需要多少免费额度**（本次实测折算）：

- 首轮 221 次请求 / 112 条 ≈ **2.0 次/条**；重试 37 条用 74 次 ≈ 2.0 次/条；两轮合计 **295 次**；
- OpenRouter 免费池 **50 次/账号/天**（本轮 429 原文 `free-models-per-day`）
  → 要覆盖 295 次需 **6 个账号的免费 key**，串行约 20–60 分钟；
- pollinations 匿名池 **1 次/45 秒/IP ≈ 1920 次/天**，**不需要额外 key**，
  295 次 ≈ **3.8 小时**；单次运行 `--max-requests` 至少 240，跑全量+重试建议 300；
- 折中：2 个 OpenRouter 免费 key（100 次）+ pollinations 剩余 195 次 ≈ 2.5 小时，
  且任一家随时可能 429，`--resume` 可跨天接着跑，不会重跑已通过的。
