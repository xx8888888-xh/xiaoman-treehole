# CI 分工与触发策略（2026-11-05）

目标：把「可以在无头 Linux runner 上跑」的闸门搬到 GitHub Actions，本地只保留必须碰真机/AVD/模拟器的部分。

## 1. Workflow 一览

| workflow | 触发 | 内容 | 典型耗时 | 是否用 Docker / 浏览器 |
|---|---|---|---|---|
| `ci.yml` | push（main / feat/** / fix/** / zhiqiu/**）、PR→main、手动、被 gates 调用 | Node 22：`npm ci` + `npm test`（基线 181）+ Biome（0 error）；Python 3.12：`compileall` + `build_assets.sh` + `verify_assets.py` + `pytest`（145 passed + 19 subtests）；`python-scripts` 矩阵：`test_mock_api`（12/12）+ `test_memory_recall`（152/152，进程内起 mock，不进 Docker） | 0.5–1 min（四 job 并行，单个 ≤25s） | 否 |
| `e2e.yml` | push→main、手动、被 gates 调用 | `docker compose` 起 web 8901 / mock 8902 / tts 8903（`XIAOMAN_TTS_PROVIDER=none` 静音）；Playwright Chromium `--mute-audio`；`test_e2e` 60、`test_feature_loop` 43、`test_live2d_original` 24、`test_raf_budget` 8 | 6–7 min（实测 340–376s） | 是 |
| `android.yml` | `workflow_dispatch`、tag `v*`、被 gates 调用 | JDK 21 + Android SDK 34/build-tools 34 → `assembleDebug`（默认，不签名；可选 release 未签名）→ 上传 APK + 体积写入 job summary | 1.5–2.5 min（实测 89s） | 否 |
| `gates.yml` | 仅 `workflow_dispatch` | 并行复用 ci / e2e / android 三条（一键全套） | 与最慢的 e2e 接近 | 视子流程 |
| `android-ci.yml`（legacy） | 仅 `workflow_dispatch` | 旧的内置模拟器 smoke（AVD + 悬浮窗证据） | 8–12 min | 是（KVM） |

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
| 签名 release / keystore | ❌（不配置 secrets） | ✅ | 云端只出 debug/unsigned |

## 4. Secrets

当前四条 workflow **不需要任何 secret**。若以后需要私有镜像凭据，建议：

- `REGISTRY_USERNAME` / `REGISTRY_PASSWORD`（或在 Settings → Secrets → Actions 配 PAT）
- 若要在云端签名 release：`ANDROID_KEYSTORE_B64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS`、`ANDROID_KEY_PASSWORD`

不要把 keystore、token 写进仓库；`android-ci.yml` 只是引用 secrets，缺省时自动跳过签名。

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
| Playwright Chromium（`~/.cache/ms-playwright`） | `e2e.yml` | `${{ runner.os }}-playwright-${PLAYWRIGHT_VERSION}` | 省 30–60s（压缩包 ~170MB） |
| pip | 未做 | 无 `requirements.txt` 作为 key，收益小（playwright wheel ~40MB，数秒） | — |

**并行度**

- `ci.yml` 内 4 个 job（node / python / python-scripts×2）天然并行，墙钟 = 最慢的 node ≈ 25s。
- `gates.yml` 的 ci / e2e / android 也并行，墙钟 ≈ 最慢的 e2e（~6.5 min），不用串行累加。
- 单个 e2e job 内部是**串行四套**（共用同一套容器；并行会互抢 8901–8903 端口与 CPU）。若以后要并行，需给每套独立端口与独立 compose 项目名。

**触发策略（防止烧分钟）**

- `e2e.yml` 只在 `push→main`、`workflow_dispatch`、`gates.yml` 调用时跑；feature 分支 push **不触发**。
- 分支需要浏览器验证时用显式手动：`gh workflow run e2e.yml --ref feat/xxx`（或给 PR 打 label 后由人工触发）。
- `android.yml` 只在手动与 `v*` tag 跑，默认 debug。
- `android-ci.yml`（AVD）已收敛为仅手动。
- 每次 e2e ≈ **6–7 min**（实测 340–376s，含 compose build 与 Chromium 安装）；命中浏览器缓存后约 5–6 min。
  按每天 3 次 trunk push 估算，e2e 约 20 min/天；ci 每次 <1 min。


## 7. 尚未上云的闸门

| 闸门 | 相关脚本 | 为什么还在本地 |
|---|---|---|
| AVD / MuMu 模拟器 | `emulator_adapt_sweep.py`、`emulator_float_check.py`、`emulator_power_probe.py`、`android-ci.yml`(legacy) | 需要 KVM/MuMu、adb、厂商 ROM、悬浮窗与系统权限；云端无真机 |
| Playwright 浏览器（已禁止本地，待迁移下一批） | `test_appearance.py`、`test_lifeline_ui.py`、`test_onboarding.py`、`test_paging.py`、`test_phase3_e2e.py`、`test_stream_e2e.py`、`ui_tour.py`、`ux_walkthrough.py`、`shot.py` | 已有浏览器硬闸门，本地跑不起来；但端口/断言尚未参数化，所以**还没进 `e2e.yml`**（当前 e2e 只覆盖四套） |
| 真实模型 / 真实 TTS | `test_freemodels.py`、`verify_tts.py`、`measure_ttft.py` | 需要真实 API key/额度、音频设备与延迟测量；云端全程静音 |
| release 签名 | `build_apk.sh` | 需要 keystore；云端只出 debug/unsigned |
| 真机后台与耗电 | `run_bg_e2e.sh` | 需要真机后台保活、电量采样 |

## 8. 已知红项（真实代码问题，非 CI 环境）

- `main` 的 `python-scripts (test_memory_recall)` 为 **150/152**，失败项：
  - `G11-2① 介绍轮即时接住宠物名`：`我家猫叫年糕...` 的回复只含“猫”，不含“年糕”。
  - `G11-2② 冷却窗口内不刻意复读`：第二轮反而带出“年糕”。
- 根因在 `server/mock_api.py` 的即时回提路径（新提取的“宠物”被排除在 memory-aware 分支之外）；`feat/data-completeness`（w16）已包含该修复，其 CI 为 **152/152 全绿**。
- 按边界要求未修改 `server/**`；不放宽断言、不跳过用例。合并 data-completeness 后此红项会自动转绿。
