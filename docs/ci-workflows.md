# CI 分工与触发策略（2026-11-05）

目标：把「可以在无头 Linux runner 上跑」的闸门搬到 GitHub Actions，本地只保留必须碰真机/AVD/模拟器的部分。

## 1. Workflow 一览

| workflow | 触发 | 内容 | 典型耗时 | 是否用 Docker / 浏览器 |
|---|---|---|---|---|
| `ci.yml` | push（main / feat/** / fix/** / zhiqiu/**）、PR→main、手动、被 gates 调用 | Node 22：`npm ci` + `npm test`（基线 181）+ Biome（0 error）；Python 3.12：`compileall` + `build_assets.sh` + `verify_assets.py` + `pytest`（145 passed + 19 subtests）；`python-scripts` 矩阵：`test_mock_api`（12/12）+ `test_memory_recall`（152/152，进程内起 mock，不进 Docker） | 0.5–1 min（四 job 并行，单个 ≤25s） | 否 |
| `e2e.yml` | push→main、手动、被 gates/nightly 调用 | job `browser-e2e`：`docker compose` 起 web 8901 / mock 8902 / tts 8903（`XIAOMAN_TTS_PROVIDER=none` 静音），串行跑核心四套（`test_e2e` 60 / `test_feature_loop` 43 / `test_live2d_original` 24 / `test_raf_budget` 8）；job `ui-suites`（matrix）：8 套已迁脚本逐脚本独立 job（`test_paging` 除外，见 §7） | 核心 5.6–6.3 min；ui 矩阵单 job 1–3.4 min | 是 |
| `android.yml` | `workflow_dispatch`、tag `v*`、被 gates/nightly 调用 | JDK 21 + Android SDK 34/build-tools 34 → `assembleDebug`（默认，不签名；可选 release 未签名）→ 上传 APK + 体积写入 job summary | 1.5–2.5 min（实测 89s） | 否 |
| `android-release.yml` | `workflow_dispatch`、tag `v*`（tag 触发才从 tag commit 读文件） | 四个 Secrets 解出 keystore → `assembleRelease` → `apksigner verify` + `aapt2 dump badging` 断言（包名/versionCode 6/versionName 0.6.0/v2 签名）→ 签名 APK artifact | 1.6 min（实测） | 否 |
| `gates.yml` | 仅 `workflow_dispatch` | 并行复用 ci / e2e / android 三条（一键全套，手动用；**不再挂 cron**，定时统一由 `nightly.yml` 负责，避免重复计费） | 与最慢的 e2e 接近 | 视子流程 |
| `nightly.yml` | `schedule`（只在默认分支生效）+ 手动 | 每日 18:00 UTC（北京时间次日 02:00，周一–周六）跑 `ci.yml` 快闸门；每周日同一时刻跑全套慢闸门（ci + e2e 核心四套 + 9 套 ui 矩阵 + android APK） | 每日 ~0.8 min；每周日 ~22 min | 视子流程 |
| `android-ci.yml`（legacy） | 仅 `workflow_dispatch` | 旧的内置模拟器 smoke（AVD + 悬浮窗证据） | 8–12 min | 是（KVM） |
| `ui-shots.yml` | 仅 `workflow_dispatch`（原 `push: feat/ui-r3` 已收窄） | UI 第三轮云端渲染截图（双主题/桌面/大字号共 8 张），离线 `http.server 8911`，不回放 TTS | 2–3 min | 是（Chromium） |
| `perf.yml` | 仅 `workflow_dispatch`（原 `push: feat/perf-r3` 已收窄） | 性能采样（load / stream / micro / memory 四段），本地 web 8911 + mock 8912，`AI_MODE=mock`；只出数字不设门槛 | 6–10 min | 是（Chromium） |

旧的 `quick.yml`（ci.yml 已覆盖）与 `healthcheck.yml`（一次性探针）已删除。

## 1.1 浏览器硬闸门（本地默认禁止起 headless）

2026-11-05 起，**所有浏览器类验证（E2E / 截图 / rAF 采样 / 视觉对比）只允许在云端跑**，
避免本地 `chrome-headless-shell.exe` 残留把 CPU 打满（曾实测 75%）。

- 统一入口：[`scripts/browser_guard.py`](../scripts/browser_guard.py)。所有 Playwright 脚本在 import 之后第一行调用
  `require_browser_allowed(__file__)`，**本地默认直接退出码 2（"未执行"，不是失败）**。
- 放行条件（任一）：
  - `GITHUB_ACTIONS=true` —— 云端 runner 自动放行（`e2e.yml` 另显式注入 `XIAOMAN_ALLOW_LOCAL_BROWSER=1`，双保险）。
  - `XIAOMAN_ALLOW_LOCAL_BROWSER=1` —— 本地排查时显式 opt-in。
  - `XIAOMAN_BROWSER_GUARD=off` —— 应急总开关（慎用）。
- **不使用 `CI` 作为判据**：本机开发终端/IDE 常把 `CI=true` 全局设上，用它会导致闸门形同虚设（首版踩过）。
- 被拦截时的提示会打印清理命令。本地 opt-in 跑完后**必须显式退出**：

```powershell
# 确认没有残留（正常应无输出）
Get-Process chrome-headless-shell,chrome -ErrorAction SilentlyContinue
# 有残留则强退
Get-Process chrome-headless-shell,chrome -ErrorAction SilentlyContinue | Stop-Process -Force
```

脚本内部的 Playwright 上下文（`with sync_playwright()` / `async with async_playwright()`）会在结束时关闭浏览器；
异常中断（Ctrl+C、断言失败提前 `sys.exit`）才可能留残进程，所以上表清理是兜底动作。

已接入闸门的脚本（13 个）：`test_e2e.py`、`test_feature_loop.py`、`test_live2d_original.py`、`test_raf_budget.py`、
`test_appearance.py`、`test_lifeline_ui.py`、`test_onboarding.py`、`test_paging.py`、`test_phase3_e2e.py`、
`test_stream_e2e.py`、`ui_tour.py`、`ux_walkthrough.py`、`shot.py`。


## 2. 端口 / 主机参数化

`e2e.yml` 在 job 级注入，不写死在脚本里：

```
XIAOMAN_HOST=127.0.0.1
XIAOMAN_WEB_PORT=8901
XIAOMAN_MOCK_PORT=8902
XIAOMAN_TTS_PORT=8903
```

Python 脚本统一经 `scripts/env.sh` / `os.environ` 读取；`test_live2d_original.py --base-url` 与
`test_raf_budget.py --web-url/--mock-base` 由 workflow 用同一组变量拼出。`test_e2e.py` 内剩余的
`127.0.0.1` 仅作为缺省回退，已改为 `HOST` 变量（含 mockBase 与跨源 8999 桩）。

## 3. 云端 vs 本地分工

| 闸门 | 云端 | 本地 | 说明 |
|---|---|---|---|
| Node 单测 / Biome | ✅ | 可选 | 无外部依赖，秒级 |
| Python pytest / 发布物一致性 | ✅ | 可选 | 纯标准库 |
| `test_mock_api` / `test_memory_recall` | ✅ | 可选 | 纯 Python + node 离线引擎；只需 mock 8902，ci.yml 用 `python server/mock_api.py` 起进程，不依赖 Docker |
| 浏览器 E2E（四套） | ✅ 唯一入口（main / 手动） | ❌ 默认禁止（`XIAOMAN_ALLOW_LOCAL_BROWSER=1` 才可临时开） | 需要 Docker + Playwright；本地起 headless 会占满 CPU |
| Gradle 出包 | ✅（按需） | 可选 | 只出 debug 包，分钟数敏感 |
| 真机 / AVD / MuMu 模拟器 | ❌ | ✅ 必须本地 | 悬浮窗、TTS 发声、厂商 ROM、耗电采样 |
| TTS 真发声 / 音频验收 | ❌ | ✅ 必须本地 | 云端全程 `--mute-audio` + provider=none |
| 签名 release / keystore | ✅（`android-release.yml`，4 个 Secrets 已写入） | 可选出包 | keystore 只在仓库外（`C:\Users\xx\.secrets`），云端正向解到 `$RUNNER_TEMP`，不入库 |

## 4. Secrets

`android-release.yml` 需要 4 个仓库 Secrets，**已写入**（值从未出现在日志/回复里）：

| Secret | 用途 |
|---|---|
| `ANDROID_KEYSTORE_B64` | keystore 的 base64（`gh secret set ... < file` 传入），云端解到 `$RUNNER_TEMP/release.keystore` |
| `ANDROID_KEYSTORE_PASSWORD` | keystore 口令 |
| `ANDROID_KEY_ALIAS` | 别名 |
| `ANDROID_KEY_PASSWORD` | 私钥口令 |

keystore 实体只存在仓库外（`C:\Users\xx\.secrets\xiaoman-release.jks`）。`git log --all -- '*keystore*' '*jks*'`、`git ls-files`
与远端 main 树递归抽查均为空（只有生成脚本 `scripts/gen_release_keystore.sh`），`.gitignore` 已含 `*.keystore`、`*.jks`、
`android/keystore.properties`。**不要把这些值贴进 issue/PR/日志。**

## 5. 手动触发

```bash
gh workflow run ci.yml
gh workflow run e2e.yml -f raf_seconds=30
gh workflow run android.yml -f variant=debug
gh workflow run gates.yml
gh run list --limit 10
gh run watch <run-id>
```

## 6. Actions 资源与分钟消耗

公开仓库的标准 Linux runner（`ubuntu-latest`：4 vCPU / 16 GB / 14 GB SSD，Docker 预装）**不计费**，
但仍应控制墙钟时间，避免排队与误触发的分钟数浪费。

**缓存策略（已落实）**

| 缓存 | workflow | key | 命中收益 |
|---|---|---|---|
| npm（`web/package-lock.json`） | `ci.yml` | 内置 `setup-node cache: npm` | 省 20–40s |
| Gradle（`~/.gradle/caches`） | `android.yml` | 内置 `setup-java cache: gradle` | 二次构建约省 30s |
| Playwright Chromium（`~/.cache/ms-playwright`） | `e2e.yml` / `ui-shots.yml` / `perf.yml` / `android-ci.yml`（全部经 `./.github/actions/setup-playwright`） | `${{ runner.os }}-playwright-<version>`（version 只在复合动作里定义） | 省 30–60s（压缩包 ~170MB） |
| pip | 未做 | 无 `requirements.txt` 作为 key，收益小（playwright wheel ~40MB，数秒） | — |

**并行度**

- `ci.yml` 内 4 个 job（node / python / python-scripts×2）天然并行，墙钟 = 最慢的 node ≈ 25s。
- `gates.yml` / `nightly.yml` 的 ci / e2e / android 也并行，墙钟 ≈ 最慢的 e2e，不用串行累加。
- 单个 e2e job 内部是**串行四套**（共用同一套容器；并行会互抢 8901–8903 端口与 CPU）。若以后要并行，需给每套独立端口与独立 compose 项目名。
- `ui-suites` 是 9 个独立 job 的矩阵（`fail-fast: false`），彼此并行；每个 job 自带一套 compose，端口相同但不互相看见（job 隔离）。

**触发策略（防止烧分钟）**

- `e2e.yml` 只在 `push→main`、`workflow_dispatch`、`gates.yml` / `nightly.yml` 调用时跑；feature 分支 push **不触发**。
- 分支需要浏览器验证时用显式手动：`gh workflow run e2e.yml --ref feat/xxx`（或给 PR 打 label 后由人工触发）。
  注意同 ref 的 dispatch 会被 `concurrency.cancel-in-progress` 取消——**逐个跑、跑完再发下一个**。
- `android.yml` 只在手动、`v*` tag 与定时全套跑，默认 debug。
- `android-release.yml` 只吃 `workflow_dispatch` 与 `v*` tag（tag 触发才会从 tag 的 commit 读 workflow 文件）。
- `android-ci.yml`（AVD）已收敛为仅手动。
- `nightly.yml` 的慢闸门只在**默认分支**生效（GitHub 的 schedule 语义），不会跑到 feature 分支，符合"只跑主干"。

**分钟账（私有仓库 2000 分钟/月）**

计费口径 = 每个 job 的墙钟分钟相加（并行 job 分别计费），实测值来自 2026-11-05 的 run：

| 计费单元 | 实测/估算 | 月频次 | 月消耗 |
|---|---|---|---|
| `ci.yml`（4 job 合计） | 0.8 min | 每日 26 次（周一–周六的 nightly）+ 约 10 次 trunk push | ≈ 29 min |
| `e2e.yml` 核心四套（`browser-e2e`） | 5.6–6.3 min（取 6.0） | 每周 4–5 次（周日全套 + trunk push） | ≈ 27 min |
| `e2e.yml` ui 矩阵（8 套，单 job 1–3.4 min） | 合计 ≈ 14.6 min | 每周 1 次（仅周日全套） | ≈ 63 min |
| `android.yml`（debug APK） | 0.8 min | 每周 1 次 | ≈ 3.5 min |
| `android-release.yml`（签名包） | 1.6 min | 打 tag 时（按每月 4 次估） | ≈ 6.5 min |
| `quick.yml`（push 轻量：compileall + 2 个纯 stdlib 单测） | ≈ 0.5 min | 与 push 次数同（按 30 次估） | ≈ 15 min |
| `android-ci.yml`（AVD） | 8–12 min | 仅手动，按 0–2 次估 | 0–24 min |
| `gates.yml`（手动全套） | ≈ 22 min | 按 1 次估 | ≈ 22 min |
| **合计** | | | **≈ 167–191 min/月（约 8–10% 预算）** |

结论：

1. **余量充足**：即使每周全套 + 每日快闸门，也只吃掉 2000 分钟里的约 10%；不需要降低 e2e 频率。
2. **该降频的只有 AVD**：`android-ci.yml` 单次 8–12 min，是单位分钟最贵的闸门，保持"仅手动"；不要给它加 cron。
3. **该合并的是 `quick.yml` 与 `ci.yml`**：两者在 push 上都跑 python 语法/单测，重复 ≈ 0.5 min/次。`quick.yml` 的价值是"1 分钟出红"，
   保留但它只在 `push` 触发、不接 schedule；`ci.yml` 不重复跑 `compileall` 之外的快速项即可（现版本已只差这一层）。
4. **别让三套新 workflow 各装一遍 Playwright**：统一用 `.github/actions/setup-playwright`（见 §9），浏览器压缩包缓存命中后可省 30–60s/job。
5. **定时只留一处**：`gates.yml` 去掉了 cron，定时全部收口到 `nightly.yml`，避免"手动 gates + 定时"两条对同一套慢闸门重复计费。


## 7. 尚未上云的闸门

| 闸门 | 相关脚本 | 为什么还在本地 |
|---|---|---|
| AVD / MuMu 模拟器 | `emulator_adapt_sweep.py`、`emulator_float_check.py`、`emulator_power_probe.py`、`android-ci.yml`(legacy) | 需要 KVM/MuMu、adb、厂商 ROM、悬浮窗与系统权限；云端无真机 |
| Playwright 浏览器（第二批 9 套，**已迁移 8 套**） | `test_appearance.py`、`test_lifeline_ui.py`、`test_onboarding.py`、`test_phase3_e2e.py`、`test_stream_e2e.py`、`ui_tour.py`、`ux_walkthrough.py`、`shot.py` | 已进 `e2e.yml` 的 `ui-suites` 矩阵，逐脚本独立 job + artifact（`ui-<script>-<sha>`，7 天）。云端 run：appearance 42019565634 / lifeline 42021974339 / onboarding 42021167878 / shot 42022702031 / stream 42023342877 / phase3 42026420958 / ui_tour 42027542248 / ux_walkthrough 42028417478 |
| `test_paging.py`（**迁不动，未进矩阵**） | `test_paging.py` | 两项 WebGL 显存断言在当前环境恒红且非 CI 引入：①`stage` 已换成 Canvas 2D 分层立绘（`LayeredPuppet`），不存在 `texImage2D`，探针永远 0；②`2048 档 > 1024 档 × 2.5` 与档位面积比（s070/s050 decoded = 9791028/5001068 ≈ 1.96×）不自洽。本地基线同样 12/14（`docs/UI_R2.md` 已记录）。其它 12 项（分页、窗口恒定、60fps）云端可过，但**不放宽断言、不 skip**，所以不塞进闸门制造永久红 |
| 真实模型 / 真实 TTS | `test_freemodels.py`、`verify_tts.py`、`measure_ttft.py` | 需要真实 API key/额度、音频设备与延迟测量；云端全程静音 |
| release 签名 | `build_apk.sh` | 云端由 `android-release.yml` 出签名包；本地只用于验证 keystore/口令 |
| 真机后台与耗电 | `run_bg_e2e.sh` | 需要真机后台保活、电量采样 |

## 8. 已知红项

- 2026-11-05 复查：`test_memory_recall` 在主干（`feat/containerize-and-config`，含 data-completeness 的修复）已 **152/152 全绿**；
  `main` 上旧的 150/152 只是分支未合并的历史红项。**结论：不需要为它单独推 main**，随主干合并自然转绿。
- 唯一遗留的稳定红项是 §7 的 `test_paging` 两项 WebGL 断言（环境/历史原因，非本次改动引入）。

## 9. 新 workflow 接入契约（`ui-shots.yml` / `perf.yml` / `android-release.yml`）

三套新流程落地时按同一张契约检查，避免各自重复装 Playwright、artifact 命名漂移：

| 检查项 | 要求 |
|---|---|
| 触发 | `workflow_dispatch` 必备；`push` 只精确到需要的分支或 tag（`android-release.yml` 只吃 `v*` tag；`perf.yml` / `ui-shots.yml` 已收窄为仅手动）；**feature 分支默认不接 cron** |
| Playwright | 必须 `uses: ./.github/actions/setup-playwright`，禁止在 job 里手写 `pip install playwright` + `playwright install`，也不得再定义 `PLAYWRIGHT_VERSION`；**版本号只在 `action.yml` 的 `default` 一处**（当前 1.63.0），缓存 key 统一 `${runner.os}-playwright-<version>` |
| artifact 命名 | `<用途>-<commit 短 sha>`（如 `ui-shots-76b9912`、`perf-r3-f89d62c`）；**不要用 `run_number`**（跨分支不可读） |
| artifact 保留 | 截图/JSON 证据 **7 天**；唯一例外是签名 APK（30 天，发布要留档），需在文件里写明理由 |
| artifact 内容 | 截图 + 关键 JSON（`<script>.json` 的 checks/ok/明细），失败时 `::error::` 打印具体断言而不是"step failed" |
| 失败可诊断 | 打印 `grep -aE "FAIL|❌|Traceback|TimeoutError|超时|缺失"` 的前 12 行；容器类 job 失败时 dump compose logs |
| 并发 | 同一 workflow 用 `concurrency: group: <workflow>-<ref>`；需要保留每次结果时 `cancel-in-progress: false` |

现状盘点（2026-11-05）：

| workflow | 位置 | 现状 | 结论 |
|---|---|---|---|
| `android-release.yml` | 已在我方分支并跑绿（42023885689） | 触发 `dispatch` + `v*`；artifact `xiaoman-treehole-release-0.6.0-<sha>`，30 天；无 Playwright | ✅ 合规（保留 30 天是发布留档例外） |
| `perf.yml` | 已合并进 main | 触发仅 `dispatch`（原 `push: feat/perf-r3` 已收窄）；artifact `perf-r3-<sha>`、7 天；Playwright 走 `./.github/actions/setup-playwright`（统一 1.63.0） | ✅ 合规 |
| `ui-shots.yml` | 已合并进 main | 触发仅 `dispatch`（原 `push: feat/ui-r3` 已收窄）；artifact `ui-shots-<sha>`、7 天；Playwright 走 `./.github/actions/setup-playwright`（统一 1.63.0，省 30–60s/job） | ✅ 合规 |
| `android-ci.yml` | 已合并进 main | `web-tests` job 的 Playwright 安装段也换成同一复合动作，版本单源 | ✅ 合规 |
