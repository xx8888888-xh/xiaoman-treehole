# W11 · 长期记忆槽契约（服务端）

> 分支 `feat/human-feel`（worktree `treehole-w11`），基线 `61384a0`。
> 实现：`server/memory_slots.py`（纯标准库）；提示词注入：`server/prompt_core.system_prompt(memory_section=...)`。
> **不改 `web/**`**：本文是给后续接入方（Web / 安卓 / 代理路由）的契约说明。

## 1. 一句话

一条记忆 = 一个槽。用户文本 →（规则 / 模型 memory_updates）提取 → 槽 → 按当前话题检索 → 渲染成 1-2 行注入下一轮 system。
用户说“忘了/别记/删掉” → 删槽并当场答应，之后不再提。

## 2. 槽字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | str | 是 | 稳定 id（删除/去重/导出用） |
| `key` | str | 是 | 类别：`昵称` `宠物` `老板` `大事` `在忙` `状态` `喜好` `关系` `生日` `记事`（也接受模型自定义键） |
| `value` | str | 是 | 记忆原文片段，≤ 80 字；不做二次改写（防张冠李戴） |
| `kind` | `pin` / `fact` / `preference` / `event` | 是 | 钉子户 / 事实 / 偏好 / 事件 |
| `emotion` | int 1-5 | 是 | 情绪权重：昵称/生日=5，大事/记事=4，宠物/关系/老板=3，负面词 +1，正面词 +1 |
| `confidence` | float 0-1 | 是 | 规则提取 1.0；模型提取 0.9 |
| `source` | `rule` / `model` / `explicit` | 是 | 来源，便于排查“谁记的” |
| `created` | int | 是 | 首次入库（epoch 秒） |
| `updated` | int | 是 | 最近改写 |
| `last_used` | int | 是 | 最近一次注入（0 = 从未） |
| `uses` | int | 是 | 注入次数 |

## 3. 容量、淘汰、优先级

- 每用户上限 `MAX_SLOTS = 64`；钉子户上限 `MAX_PIN_SLOTS = 16`。
- 钉子户：`kind=pin` 或键属于 `PIN_KEYS = 昵称/生日/重要日/大事/宠物`，**不参与淘汰**（超出 16 条时按分数留前 16）。
- 其余槽淘汰分数（分数最低先出局，`prune_slots`）：

```
score = emotion * 2.0 + recency(0~3) + uses * 0.5
recency：<1 天 3.0 / <7 天 2.5 / <30 天 1.5 / <90 天 0.8 / 更久 0
```

这是「LRU + 情绪权重」的混合策略：不是纯时间淘汰，重要情绪事实更耐活；重复被提起的记忆也会加分。

## 4. 写入

### 4.1 规则提取（`extract_slots(text)`）

| 键 | 触发示例 | 说明 |
|---|---|---|
| 昵称 | “你可以叫我阿秋就行” | 去尾缀“就行/就好/吧…” |
| 宠物 | “我家猫叫年糕”“我养了一只橘猫” | 只认“养/有/名字叫”；**路上看到的猫不记** |
| 老板 | “我们老板叫张三”“领导姓李” | 只认“叫/是/姓”，防泛指 |
| 大事 | “我下周要考试”“下个月要搬家” | 时间 + 事件；无时间/无“要”不记 |
| 在忙 | “最近在加班”“正在赶稿” | |
| 状态 | “我最近失眠”“又睡不着” | 反复出现的睡眠/身体状态 |
| 喜好 | “我很喜欢吃辣”“我讨厌香菜” | 保留“喜欢/讨厌 + 对象” |
| 关系 | “我男朋友最近很忙” | 只记伴侣类，不记“我妈”这种泛亲属 |
| 生日 | “我的生日是5月20日” | |
| 记事 | “帮我记住，下周三给我妈打电话” | 显式要求，`kind=pin` |

### 4.2 模型 `memory_updates`

在线路径的模型可以在 JSON 里返回 `memory_updates: {键: 值}`；调用方直接：

```python
store.apply_updates(session_id, payload.get("memory_updates") or {})
```

同键覆盖；`宠物` 的泛指（“猫/狗/兔子”）不会覆盖已存的具体名字（“年糕”）。

### 4.3 显式记住

“记住/帮我记一下” → `kind=pin`、`source=explicit` 由调用方传；容量满时钉子户优先保留。

## 5. 删除（用户否定）

`delete_from_text(sid, text)` 识别：

- “忘了我的生日吧” → 删 `生日`（类别指代）
- “把年糕删了” → 删值为 `年糕` 的槽
- “别记我老板的事了” → 删 `老板`
- “删掉这个” → 删最近更新的那条（`__last__`）
- “我自己会记得，别忘了带钥匙” → **不删**（“别忘了”是提醒，不是否定）

删除后返回 `[{key, value}]`；调用方应当场答应（“行，删了”），提示词层 `MEMORY_SLOT_RULES` 保证之后不再提。

## 6. 检索与注入

### 6.1 检索评分（`recall(sid, query, limit=6)`）

```
overlap = 查询与 key+value 的汉字二元组重合数 + 类别别名命中（+2）
         （别名例：说“我家猫”能命中 宠物 槽；说“加班”能命中 在忙）
非相关槽直接跳过（overlap=0 不注入，避免每轮背昵称）
score = min(overlap,6)*1.4 + emotion*0.8 + recency + uses*0.3 + (pin ? 4 : 0)
```

- 查询为空 → 返回钉子户 + 最近使用的槽。
- 一条都不相关 → 退回最近 1-2 条钉子户，保证“还记得你”。
- 注入后才更新 `last_used/uses`（`touch=False` 可只读）。

### 6.2 注入格式

```text
【你记得的关于 ta 的事】
- 大事：下周要考试（今天）
- 宠物：年糕（3 天前）
（自然地用，别背诵，别一次全提）
```

`memory_slots.render()` 只返回 `- 键：值（时间）` 行；标题和“自然使用”提示由 `prompt_core.system_prompt()` 统一加。
提示词 `MEMORY_RULES` 要求每轮最多带 1 条；服务端最多给 6 条候选，由模型取舍。

### 6.3 注入位置（控首字延迟）

```python
import prompt_core as pc, memory_slots as ms

store = ms.SlotStore(os.environ.get("XIAOMAN_DATA_DIR"))
lines = store.render(session_id, user_text, limit=6)
system = pc.system_prompt(memory_section=lines)   # 动态段只拼在静态核心末尾
```

静态核心 `CORE_PROMPT` 不随会话变化，可被上游做前缀缓存；记忆段追加在最后，不会重排前文。

## 7. 落盘与隐私

- `SlotStore(root)`：`root` 为数据目录时落盘为 `slots-<sha1(session_id)[:16]>.json`；`root=None` 纯内存（测试/无落盘）。
- 写入流程：临时文件 → `os.replace` 原子替换；损坏 JSON 按空库处理，不阻断对话。
- 文件只存 `key/value/时间/权重`，不存整段聊天原文；`session_id` 不落明文（sha1 前 16 位）。
- 不做云端同步；迁移用 `export()/import_()`（带 `version=1`），便于后续备份功能复用。

## 8. 接入契约（后续分支照此接）

| 时机 | 调用 | 说明 |
|---|---|---|
| 收到用户消息、调用模型前 | `store.ingest(sid, text)` | 先处理删除，再提取；返回 `{updates, deleted}` |
| 模型返回后 | `store.apply_updates(sid, payload.get("memory_updates"))` | 模型补充/纠正 |
| 组装 system 时 | `store.render(sid, text)` → `pc.system_prompt(memory_section=...)` | 动态段末尾拼接 |
| 用户说“忘了/别记” | 依据 `ingest()["deleted"]` 回复“行，删了” | 不要假装没听见 |

**不要**在 `web/**` 另建一套记忆真源；Web 侧已有 `memory*.js` 继续负责本地展示/备份，服务端槽是提示词真源，冲突时以用户最新说法为准（`upsert` 同键覆盖）。

## 9. 测试

`server/tests/test_memory_slots.py`（17 例）：提取、垃圾不落库、同键覆盖、泛指不覆盖具体、相关检索/别名、注入格式、容量淘汰、用户否定删除、落盘往返/损坏容错、导出导入、完整链路注入 system。
