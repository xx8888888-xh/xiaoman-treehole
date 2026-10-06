# 项目规则 · 小满树洞（xiaoman-treehole）

> 本文件是 AI 助手的**长期记忆**，每次会话开始前必须读一遍并遵守。
>
> ⚠️ **固化说明（2026-10-06）**：本文件是 `/workspace/.trae/rules/project_rules.md` 的**仓库内同源副本**。
> 沙箱重置会清掉仓库外的 `.trae/rules/`，故以本副本为准；运行 `bash scripts/setup_dsh.sh` 会自动把它还原到
> `/workspace/.trae/rules/project_rules.md`。**规则只在本文件改**，改完重跑脚本即同步生效。

## 铁律 1：执行者优先级 —— 能让 DSH 干就让 DSH 干

**DeepSeek Harness (dsh) 是免费的，必须优先承担实际执行工作。**

| 角色 | 承担者 | 工作内容 |
|---|---|---|
| 规划层 | 助手（付费模型） | 需求解析、架构设计、接口契约、任务拆解、验收标准制定、代码评审、疑难 bug 定位、最终集成 |
| 执行层 | **DSH（免费）** | 基础代码编写、函数实现、文件生成、样板代码、批量重构、单测编写、文档草拟、数据整理 |

执行纪律：
- 任务下发前先判断：**这块能不能交给 DSH？** 能，就走 DSH。
- 助手只做 DSH 做不了的事：跨文件复杂推理、性能敏感实现、契约设计、验收判定。
- 下发 DSH 的任务必须是**契约明确的小块**（函数签名 + 输入输出 + 错误语义），否则先由助手拆解。
- 大量基础代码任务走**多进程并行**：多个 `dsh --profile headless` 进程同时跑，或直连转发端口。
- 助手不得因为"顺手"而自己写基础代码；这是成本违规。
- **例外（2026-10-06 实测）**：当 OpenRouter 免费额度耗尽（`free_model_daily_requests` 打满）时，
  DSH 全量 429、不可用；此时助手直接执行，并在 DEVLOG 记录"因额度耗尽转助手执行"。

### 已验证的运行事实（2026-10-05 实测，接手必看）

1. **`dsh` 命令本环境默认不可用，已装包装脚本修复；环境被重置时先跑一键脚本重建。**
   非交互 shell 的 PATH 默认指向 Node 22.16.0，而 DSH 0.2.0-rc.2 依赖 `import.meta.main`（需 Node ≥ 24.2），
   直接跑 `dsh` 会**静默无输出、无报错**。已安装 `/app/bin/dsh` 包装脚本（PATH 最前），功能有三：
   - 固定用 Node 24 运行 DSH；
   - 默认导出 `DSH_PERMISSION_MODE=danger-full-access`（见下条）；
   - 从 `/workspace/.secrets/onerouter.key` 注入 `ONEROUTER_API_KEY`（OneRouter 免费模型凭据，密钥不入库）。
   ⚠️ **沙箱可能被重置**（实测：`/app/bin/dsh`、`/root/.dsh`、Node24 曾被清空）。发现 `dsh --version` 无输出或
   `dsh: command not found`，**不要手工重装**，直接跑：
   ```bash
   cd /workspace/xiaoman-treehole && bash scripts/setup_dsh.sh
   ```
   该脚本幂等重建全部环境（Node24 + dsh + 包装脚本 + provider 配置 + 插件 + AI 长期记忆还原）。见「铁律 5」「铁律 7」。

2. **DSH 能力边界（实测确认）——DSH 完全可用，文件+命令都能干：**
   - ✅ 文件读写工具（`tool-fs`）：可在仓库内创建/修改源码、脚本、文档。
   - ✅ shell/bash 工具（`tool-bash`）：可执行命令、跑测试、跑 git、起服务。
   - ⚠️ **前提**：必须处于 `danger-full-access` 权限模式。默认 `workspace-write` 下审批策略为 `ask`，
     而 headless 没有审批通道 → bash 工具报 `Sandbox backend unavailable ... fails closed`，表现为"shell 不可用"。
     包装脚本已默认设为 `danger-full-access`，因此**直接 `dsh headless ...` 即可**，无需再加环境变量。
   - **修正上一轮的错误结论**：此前记为"DSH shell 不可用、只能写文件"是**误判**（被默认审批模式误导）。
     正确分工：**写代码 / 写脚本 / 写文档 / 跑测试 / 跑 git 等执行类杂活，全部优先交给 DSH；助手只做规划、契约、验收与疑难定位。**

3. **下发 DSH 的标准动作：**
   ```bash
   cd /workspace/xiaoman-treehole && dsh headless "<任务：精确到目标 + 期望产物 + 验收点>"
   # 需要多任务并行时：多个 dsh headless 进程同时跑（已实测 4 进程无锁冲突）
   ```
   - 默认模型 `nvidia/nemotron-3.5-lightning:free`（provider `onerouter`，OpenRouter 免费路由），单任务耗时可达数分钟，用"后台 + 轮询"跑，别用阻塞等待。
   - 任务描述要契约明确（目标文件 / 函数签名 / 输入输出 / 验收命令）。
   - DSH 跑完，助手要**独立复核结果**（Read 或重跑测试），不能只信它的自述。

## 铁律 1.5：验收纪律 —— 助手必须做，且必须回问 DSH 确认

**DSH 干完 ≠ 交付完成。** 即便全部执行都交给 DSH，助手也必须做基本验收，缺一不可：

1. **回问确认（必做）**：对 DSH 的关键结论/改动，**必须再问它一次**，要求它自己复述并确认——改了哪些文件、执行了哪些命令、结果如何、有没有失败项。
   目的是排除它"第一遍没做全/说漏/自述与实际不符"。不能只看第一遍输出就收工。
2. **独立复核（必做）**：助手独立 Read 关键文件、重跑关键测试/命令、curl 健康检查等，用客观证据核对 DSH 的说法。
3. **判定与收尾**：给出通过/不通过结论；不通过则把问题回抛给 DSH 修（附上失败证据），DSH 仍搞不定才由助手介入。
4. 收尾（提交、汇报、更新文档）由助手做，但**执行本身优先归 DSH**。

## 铁律 2：每次开始本项目任务，第一步必须拉取云端最新

用户有另一条并行开发线，云端可能已有变更。

```bash
cd /workspace/xiaoman-treehole && git pull --rebase origin main
```

- 在任何分析、部署、测试、开发动作**之前**执行。
- 有本地未提交改动时，先 `git stash` 或先提交，再 pull。
- pull 完先看 `docs/DEVLOG.md` 顶部和 `docs/PROJECT_STATE.md`，确认别人改了什么。
- 另注：远端还有并行开发线分支（如 `origin/zhiqiu/dev`），必要时可参考。

## 铁律 3：活文档

- 每一步操作/变更追加到 `docs/DEVLOG.md`（新条目在顶部）。
- 全局状态同步更新到 `docs/PROJECT_STATE.md`。
- 格式：`[时间] 动作 → 结果 → 触及文件`。

## 铁律 4：备份先行

重大代码变更前先跑 `bash scripts/backup.sh <标签>`，产物进 `backups/`。

## 铁律 5：环境固化 —— 一条命令重建，禁止重复手装

**目标：沙箱被重置后，只跑一条命令就恢复全部环境。**

```bash
cd /workspace/xiaoman-treehole && bash scripts/setup_dsh.sh
```

- 唯一入口 `scripts/setup_dsh.sh`（幂等），固化：Node24 + `@deepseek-ai/dsh@0.2.0-rc.2` +
  `/app/bin/dsh` 包装脚本 + `$DSH_HOME/cordis.patch.yml`（provider/默认模型）+ modsearch 插件 +
  **Python 依赖（edge-tts / playwright + chromium + chromium 系统库 libatk 等）** + 凭据注入 + **AI 长期记忆还原**。
- ⚠️ 沙箱重置会同时清掉 **Node24/dsh、Python 包（edge-tts、playwright、chromium）与 chromium 系统库**（已实测）。
  故重置后先跑本脚本，再起服务/跑测试；否则 8903 健康检查会显示 `edge_tts:false`、Playwright 测试报
  `libatk-1.0.so.0: cannot open shared object file` 直接启动失败。
- **新增任何环境依赖，必须先写进 `setup_dsh.sh` 再执行**；禁止只在本机手装、不落脚本（否则下次重置又要重来）。
- 密钥规则：只从 `/workspace/.secrets/onerouter.key`（chmod 600）读取，**绝不写入仓库、绝不提交**。
  首次写入：`ONEROUTER_API_KEY=sk-or-v1-xxx bash scripts/setup_dsh.sh`。
- 模型规则（用户明示）：**只允许调用免费模型**。默认 `nvidia/nemotron-3.5-lightning:free`（provider `onerouter`），
  备选见 `cordis.patch.yml` 的 models 列表（均已实测支持工具调用）。**不得配置/使用付费模型。**
- 新增 provider/模型：改 `setup_dsh.sh` 内嵌的 cordis.patch.yml heredoc 块（唯一真源），再重跑脚本使生效。
- ⚠️ **免费额度硬约束（2026-10-06 实测）**：OpenRouter 免费档 `free-models-per-day = 50`，**按 key 跨任务共享**，
  次日 00:00 UTC 重置。两个并行 DSH 任务约 20 分钟即打满 → 之后全量 HTTP 429、任务中途夭折并留下半成品。
  查询额度（不消耗配额）：`curl -s https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $(cat /workspace/.secrets/onerouter.key)"`。
  **纪律**：① DSH 任务切成"小步、契约小、单任务串行"，别一上来就下发"修 28/54 条"这种大任务；
  ② 下发前先查额度，额度不足时不要硬发（额度耗尽则由助手直接执行并记录）；③ DSH 中途夭折后**必须先验证半成品**（语法 + 跑测试）再决定取舍，
  它可能在文件里留下语法错误或逻辑死锁（本轮就踩到 `app.js` 语法错误 + 发送自锁死锁）。

## 铁律 6：每笔更改都要新开分支并推送云端（用户明示，长期规矩）

用户 2026-10-06 明示：**本地很多内容不持久化，以后每笔更改都要「新开一个分支并提交/推送到云端」，尽量别让改动留在本地。**

- **推送入口（唯一）**：`bash scripts/git_push.sh [branch]` —— 默认推当前分支；读 `/workspace/.secrets/github.token`、`push -u origin <branch>`（远端无则新建）、**拒绝推 main/master**。
- **凭据**：GitHub Fine-grained PAT（Contents: Read and write）放 `/workspace/.secrets/github.token`（chmod 600，**仓库外、绝不入库、绝不写进 git config**）。
  首次写入：把 PAT 内容写入该文件即可。若该文件丢失，push 会失败并提示，需用户重新提供。
- **沙箱事实（2026-10-06 实测）**：仓库公开 → `git fetch/ls-remote` 可读；但 HTTPS push **必须**有 PAT（无 credential helper / `.git-credentials` / `.netrc`），SSH 无 key 且 22 端口不通。
- **纪律**：① 改完先本地 commit；② 新开分支（`fix/...` 或 `feat/...`）；③ 跑 `scripts/git_push.sh` 推云端；④ 绝不直接推 main/master。

## 铁律 7：变更留痕 + 文档维护 + 沙箱重置固化（用户明示，2026-10-06）

用户 2026-10-06 明示：**每一步提交代码 / 修复 / 改代码都要有迹可循；每次修改都要写文档说明；文档要定期维护；本地沙箱会重置导致记忆缺失、软件失效，必须固化。**

### 7.1 变更留痕（三件套，缺一不可）
1. **原子提交**：一处逻辑改动一个 commit，信息用 `类型(范围): 摘要`（feat/fix/docs/chore/refactor），正文写清"为什么"与"验证方式"。
2. **DEVLOG 追加**：每笔改动在 `docs/DEVLOG.md` **顶部**追加一条 `[日期] 动作 → 结果 → 触及文件`，含验证命令与结果。
3. **总账/状态更新**：涉及审计缺陷的，同步更新 `docs/AUDIT_REMEDIATION.md` 对应行；遗留项被修掉后从 §九 移出并订正计数。全局状态变化更新 `docs/PROJECT_STATE.md`。
4. **云端落地**：按铁律 6 新开分支并 `scripts/git_push.sh` 推送——本地不持久，**只有推上云端才算真正"留痕"**。

### 7.2 文档索引（各司其职，禁止重复内容）
| 文档 | 职责 | 何时更新 |
|---|---|---|
| `docs/PROJECT_STATE.md` | 全局状态权威源（完成度/快照/风险/交接） | 里程碑或状态变化 |
| `docs/DEVLOG.md` | 操作日志（追加式，新条目在顶部） | 每笔变更 |
| `docs/AUDIT_REMEDIATION.md` | 审计缺陷总账（148 条逐条：修复/验证/遗留） | 修 / 验 / 遗留变动时 |
| `docs/AI_RULES.md` | AI 长期记忆 / 项目规则（本文件，仓库内同源副本） | 规则变化时 |
| `docs/live2d_surgery.md`、`docs/USER_REVIEW.md`、`docs/MODEL_COMPARISON.md` | 专项说明 | 对应专项变化时 |

### 7.3 定期维护（每次会话收尾执行，不另设定时任务）
- 收尾前核对：本轮改动三件套是否齐全（commit + DEVLOG + 总账/状态）。
- 订正过期表述：文档里的"最后更新"时间、测试基线数字、遗留清单必须与实际一致。
- 严禁"只改代码不写文档"，也严禁"文档写了但没推云端"。

### 7.4 沙箱重置恢复清单（一条命令 + 一步校验）
沙箱重置会清空 `/app/bin/dsh`、`/root/.dsh`、Node24、Python 包（edge-tts/playwright）、chromium 系统库，**也会清掉仓库外的 `/workspace/.trae/rules/`**。恢复步骤：
1. `cd /workspace/xiaoman-treehole && bash scripts/setup_dsh.sh`
   —— 重建 Node24 + dsh + 包装脚本 + provider + 插件 + Python 依赖 + chromium 系统库，**并把仓库内 `docs/AI_RULES.md` 还原到 `/workspace/.trae/rules/project_rules.md`**（恢复 AI 长期记忆）。
2. 凭据：`/workspace/.secrets/{onerouter.key,github.token}` 属仓库外、不入库，重置后**必须由用户重新提供**（写入即用，chmod 600）。
3. 校验：`dsh --version` 有输出、`python3 -c "import edge_tts, playwright"` 无报错、`bash scripts/dev_up.sh` 三服务就绪。
4. 文档侧：`docs/` 全部随 git 入库，重置后 `git pull` 即可恢复，无需手工重建。

## 本地部署速查

```bash
# 服务：mock_api(8902) / tts_server(8903) / 静态站(8901)
cd /workspace/xiaoman-treehole
bash scripts/dev_up.sh                # 一键起三服务（幂等）
bash scripts/dev_down.sh              # 一键停

# 测试
python3 scripts/test_mock_api.py      # Mock 契约（8 用例）
python3 scripts/test_lifeline_ui.py   # UI 集成（Playwright）
python3 scripts/test_e2e.py           # 端到端（Playwright）
python3 scripts/verify_tts.py         # TTS→ASR 回环
python3 scripts/test_freemodels.py    # 免费模型可用性
```

## 关键约束（用户明示）

1. 全程自主决策，不问用户；任务完成前不停。
2. 沙箱资源有限（无 GPU、2 核、内存紧张），重活走"后台 + 轮询"。
3. 用户的钱要省：付费模型只做规划与验收。

> ⚠️ 2026-10-06 更新（zhiqiu）：原默认 nemotron-3-ultra-550b:free 与 qwen3.8-27b:free 均已从 OpenRouter 下架，现默认 nvidia/nemotron-3.5-lightning:free（备选见 web/js/api.js 注释）。Spacebunny provider 亦已下架。
