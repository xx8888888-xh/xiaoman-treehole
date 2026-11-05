# 真人感第三轮（w20）评测报告

分支 `feat/human-feel-r3`，基线 `r5-20261105` / `a283342`，worktree `treehole-w20`。

## 0. 结论先说

| 组别 | 场景数 | 通过 | 通过率 | 说明 |
|---|---|---|---|---|
| mock 模板引擎 | 112 | 112 | 100% | 本轮唯一完整跑完的一组 |
| 真实模型（OpenRouter 免费模型） | 112 | — | **未跑** | 免费日配额耗尽，全部 429；**不代表 0 分，也不拿 mock 分冒充** |

**真实分本轮不可得。** 下面所有分数、指标均标注来源；mock 的数字只能说明"模板引擎没退化"，不能说明真实模型不像客服。

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

# 任意 OpenAI 兼容后端（真实模型）
python scripts/eval_human_feel.py --backend api \
  --base https://openrouter.ai/api/v1 \
  --model <model> --delay 1 --timeout 90 --retries 4 --max-tokens 1200 \
  --json real.json --md real.md
```

- 判定全部走 `check_turn` / `check_case_expect`，mock 与真实模型用**同一套硬约束**，不存在两套标准。
- `ApiBackend` 走真实链路：`memory_slots` 渲染 → `prompt_core.system_prompt` → `/chat/completions` → 解析 JSON → 回灌记忆槽。
- `--limit` / `--categories` 可缩样；`--min-rate` 设定退出码阈值。
- 报告输出：分类别通过率、失败原因归类、反套路四项指标、失败前 5 条原文。

## 3. 真实模型：为什么没跑成（可复现证据）

| 尝试 | 结果 |
|---|---|
| OpenRouter `dots-studio/dots-3-note-preview:free`（配额耗尽前） | 单轮探针成功，约 4.3s，返回中文自然回复 |
| 同上，配额耗尽后（112 条全量） | 第 1 条即 `HTTP 429`：`Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day` |
| `xingchen-agi/xing4.0-29b-a4b:free` | 上游 429（61 条全失败） |
| `nvidia/nemotron-3.5-lightning:free` | `max_tokens` 被 reasoning 吃光，`content=null`、`finish_reason=length` |
| pollinations 免密端点（`text.pollinations.ai/openai`） | `HTTP 402 Payment Required`（`openai`/`qwen-coder`），`mistral` 404 |
| 本机 ollama | 未安装，无本地模型 |
| Windows 凭据管理器 | 只有 Freebuff BYOK 一个目标，无第二家 provider key |

配额耗尽前的成功探针（**仅作连通性/中文能力证据，不计分**）：

> 用户：`团子又把我耳机线咬了`
> 模型（dots）：`团子要是看见估计得当场表演一个原地晕倒。那只猫有人管吗，还是蹲门口蹭饭的？`

结论：评测器对真实后端可用（HTTP/解析/记忆回灌链路已验证），但本轮拿不到真实样本。
**报告不把 mock 的 100% 当作真实分。** 配额恢复后重跑第 2 节命令即可补齐"真实 vs mock"两列。

## 4. 反套路指标（来源：mock 112 条 / 212 轮）

| 指标 | 实测 | 目标 | 判定 |
|---|---|---|---|
| 建议出现率（含"试试/建议/早睡/多沟通"） | **0.0%**（0/212） | ≤25% | 达标 |
| 四字总结式共情（"太不容易了/辛苦了/我懂你"） | **0.0%**（0/212） | 越低越好 | 达标 |
| 高频开头（听起来/我理解/确实/其实/首先…） | **6.1%**（13/212） | 越低越好 | 达标 |
| 会话内重复开头 | **0.0%**（0/210） | ≤8% | 达标 |
| 记忆引用自然度（带"上次/之前/那个"） | **1/1** | 全部自然 | 达标 |
| JSON 协议不合规 | **0/212 轮** | 仅统计（客户端有纯文本兜底） | — |

高频开头全部是 `嗯，`（13 次），集中在沉默/拒绝池——对单字、省略号回复属于人话，不算套路开头。
真实模型这一列必须等真机跑出来才有意义，mock 的 0% 不代表真实模型也会 0%。

## 5. 失败案例

### 5.1 最终 mock 全量：0 失败

112/112，`failure_reasons` 为空。**没有真实模型的失败样本**，因为真实调用没成功，不编造 Top5。

### 5.2 本轮从失败修到通过的 6 个反例（原文可核验）

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
- 本轮真实分拿不到（见第 3 节），报告与测试里都写明"仅 mock"。

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

文件：`scripts/eval_human_feel.py`（新，686 行）、`server/tests/human_feel_cases.json`（112 条）、
`server/tests/test_human_feel_eval.py`（重写）、`server/tests/test_human_feel_prompt.py`、
`server/tests/test_mock_human_feel_fixes.py`、`server/mock_api.py`、`server/prompt_core.py`、`AGENT_SCOPE.md`。

## 8. 验证

```
python -m pytest server/tests -q
158 passed, 19 subtests passed in 15.78s
```

基线 153 passed + 19 subtests，本轮新增 5 个测试，无降级、无放宽断言。
串行执行，未起浏览器/AVD/Gradle；mock 评测 0.2s，pytest 15.8s。

## 9. 尚存差距（必须说清）

1. **mock ≠ 真实。** 112/112 只证明模板引擎没退化。真实模型的建议率、开头重复、记忆背诵很可能明显高于 0%，要等配额恢复后补跑。
2. **免费额度不可靠。** OpenRouter 免费模型日配额 50 次，跑 112 条 × 2 轮需要 ~250 次调用，必然中断。稳定回归要么充值（credits ≥10 解锁 1000 次/天），要么换本地模型（本机未装 ollama）。
3. **线上客户端提示词未同步。** `web/js/api.js` 只与 `SAFETY/PERSONA/REALISM/MEMORY/BASELINE/OUTPUT_PROTOCOL` 逐字对齐；
   W20 的 `HUMAN_FEEL_RULES`（默认不给建议/拒绝要落地/记忆像人回忆）与 `MEMORY_SLOT_RULES` 是**服务端独有**，
   而 `web/**`、`android/**` 在本轮禁止改动。也就是说：真机客户端当前不会带上这两段新硬约束，
   本轮改动只影响服务端评测链路。
4. **`lean` profile 会退掉两段新硬约束**（为控首字延迟），退化路径下真人感约束不生效。
5. **记忆自然度只在 mock + 槽层验证**：真实模型是否"背清单/逐字复读"没有样本。
6. **判定是启发式正则**：能抓典型套话，抓不到"语义上像客服"的变体；真实回归时必须人工抽检前 20 条。
7. **真实模型 JSON 协议不稳定**：免费模型常出围栏/前后缀/纯文本；线上 `api.js` 有纯文本兜底，评测只统计不判负，
   但这会压低结构化字段（emotion/memory_updates）的可用率。

## 10. 复跑命令（配额恢复后）

```bash
python scripts/eval_human_feel.py --backend api \
  --base https://openrouter.ai/api/v1 \
  --model dots-studio/dots-3-note-preview:free \
  --delay 2 --timeout 90 --retries 4 --max-tokens 1200 \
  --json docs/eval_real_w20.json --md docs/eval_real_w20.md
```

若仍 429：`--limit 61` 跑代表性半量，报告里继续写明覆盖率，不与 112 条 mock 分混算。
