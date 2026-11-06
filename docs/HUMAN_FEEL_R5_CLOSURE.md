# 真人感 W20 · R5 收口：锚点口径、归档证据与记忆回带实测

> 本轮不发起新的真实模型请求（免费日额度已 429 耗尽）。数据来自：
> ① 离线归档 `docs/eval_real_w20.json`；② 上一轮真实免费模型 22 例定向复测（两段运行，共 52 请求）。

## 1. 锚点口径改动（只动“具体细节”，最近 2 轮）

- 代码：[`scripts/eval_human_feel.py`](../scripts/eval_human_feel.py) 新增
  `ANCHOR_WINDOW_CATEGORIES = ("具体细节",)` 与 `_expect_pool()`。
- 规则：`具体细节` 类的 `expect_any` 在**最近 2 轮**内出现即算接住；显式
  `expect_scope` 仍优先。
- **不动**：追问、拒绝说话、拒绝建议、沉默、情绪低落等其他类，仍只看末轮。
- 语义依据：锚点要求是“接住过一次”，不是“每轮重复”。

离线断言（`python scripts/offline_rubric_audit.py --anchor-window`，10/10 PASS）：
- 前轮接住、末轮换说法：旧口径红 → 新口径绿；
- 两轮都没接住：仍红；
- 追问类：不受影响，仍红；
- `expect_scope=any`：行为不变。

## 2. 历史 112 条归档复算

- 归档结果：**75/112（66.96%）**，漂移 **0**。
- 具体细节历史红 **13 条**，且全部**只有末轮 `expect_any` 一个失败原因**。
- 归档只存失败片段，**没有上一轮回复原文**，因此：
  - **可确认翻绿：0 条**；
  - **潜在上限：13 条**（未证实，**不计入通过率**）：
    `detail-food-01`、`detail-food-02`、`detail-pet-03`、
    `detail-work-01`、`detail-work-02`、`detail-work-04`、
    `detail-body-01`、`detail-body-02`、`detail-body-03`、`detail-body-04`、
    `detail-weather-01`、`detail-weather-02`、`detail-time-02`。
  - 没有“回复其实没接住却被翻绿”的条目可列：无法证明前一轮接住，一律不翻。

## 3. 有完整原文的 12 条具体细节：4 → 6

上一轮真实请求里，12 条具体细节全部拿到完整两轮原文（网络恢复后补跑了
`detail-food-03`、`detail-work-04`）：

- 旧口径（只看末轮）：**4/12**（food-03、pet-01、pet-03、weather-01）
- 新口径（最近 2 轮）：**6/12**
- **确认翻绿 2 条**，且前一轮确实接住：
  - `detail-food-02` 第 1 轮：
    > 关东煮？这大晚上的太合适了 走啊，现在就去，我请客
  - `detail-food-04` 第 1 轮：
    > 这心意太实在了，夏天就该有箱西瓜才完整……
- 两轮都没接住的 `food-01`、`weather-02`、`time-01`、`work-04` 仍红，没有假绿。

## 4. 建议口径锁死（followup-04 假红修复）

- `followup-04` 显式加 `"max_advice_turns": 1`（criteria 本来就允许“最多一条具体建议”）。
- **追问/具体细节全局默认仍是 0**，未改回 1。
- 离线断言（`python scripts/offline_rubric_audit.py --advice-scope`，5/5 PASS）：
  - 追问、具体细节全局默认 `(0, 0)`；
  - `followup-04` 显式配置 1、生效 1；
  - 其他类默认不被抬高。

## 5. 归档补证据：失败片段带前 1–2 轮回复原文

- [`scripts/eval_human_feel.py`](../scripts/eval_human_feel.py) 新增 `enrich_failures()`：
  每个失败记录附 `prev_replies`（失败轮之前最多 2 轮助手回复原文）；
  首轮失败 / 用例级失败（`turn=0`）取不到时为空列表，不编造。
- `case_stats()` 落盘改用 `enrich_failures(res)`；旧归档没有该字段时
  `--anchor-window` 仍按“潜在/未证实”处理，不崩。
- 测试：`server/tests/test_human_feel_eval.py` 新增 2 条断言——
  - 首轮/用例级失败：`prev_replies == []`，不崩；
  - 走真实 rubric 的末轮失败：`prev_replies` 确实落盘为上一轮原文。
- mock 语料：`pytest server/tests/test_human_feel_eval.py` **8 passed**，
  mock 112 仍 112/112。

## 6. 跨 1–2 轮主动记忆回带实测

**实际主动回带：1 次。**

| 用例 | 主动回带 | 原文摘句 |
|---|---|---|
| `followup-03` | **1**（第 3 轮，距“我下个月要搬家”已过 2 轮） | 「行，阿秋。**搬家**这事儿拖到最后最难受，你打算找搬家公司还是自己弄？」 |
| `followup-01` | 0 | 用户没提“年糕”的轮次，回复从未主动带回 |
| `followup-02` | 0 | 第 2 轮用户提“张三”，回复只说「这名儿听着就压人」，名字没回带 |
| `followup-04` | 0 | 只跑到第 1 轮即 429 |
| `memory-01~04` | **0 数据** | 免费日额度 429，未执行，不下结论 |

- 名字主动回带：**0/2**（张三、年糕）。
- 同轮复述另计、不算主动回带：年糕（followup-01 第 4 轮）、张三（followup-02 第 1 轮）、搬家（followup-03 第 1 轮）。

## 7. 下一轮要补的证据清单

1. `memory-01~04`：额度恢复后同模型同用例补跑，验证名字/计划/时间三类跨 1–2 轮回带。
2. `followup-05/06`：同上补跑，凑齐追问 6 条。
3. 追问类是否也采用“最近 2 轮”锚点窗口：**当前未改**，需单独决策并量化翻绿。
4. 用新归档格式（`prev_replies`）重跑后，重新复算 13 条潜在上限，给出“确认翻绿/仍红”实数。
5. 名字主动回带 0/2 的归因：模型限制 vs 提示词措辞，需更多样本。
6. 本轮提交的 CI run 号与结果（以实际 CI 为准）。
