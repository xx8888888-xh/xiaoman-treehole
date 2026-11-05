# CI 分工与触发策略（2026-11-05）

目标：把「可以在无头 Linux runner 上跑」的闸门搬到 GitHub Actions，本地只保留必须碰真机/AVD/模拟器的部分。

## 1. Workflow 一览

| workflow | 触发 | 内容 | 典型耗时 | 是否用 Docker / 浏览器 |
|---|---|---|---|---|
| `ci.yml` | push（main / feat/** / fix/** / zhiqiu/**）、PR→main、手动、被 gates 调用 | Node 22：`npm ci` + `npm test`（基线 181）+ Biome（0 error）；Python 3.12：`compileall` + `build_assets.sh` + `verify_assets.py` + `pytest`（145 passed + 19 subtests） | 3–5 min | 否 |
| `e2e.yml` | push→main、手动、被 gates 调用 | `docker compose` 起 web 8901 / mock 8902 / tts 8903（`XIAOMAN_TTS_PROVIDER=none` 静音）；Playwright Chromium `--mute-audio`；`test_e2e` 60、`test_feature_loop` 43、`test_live2d_original` 24、`test_raf_budget` 8 | 10–15 min | 是 |
| `android.yml` | `workflow_dispatch`、tag `v*`、被 gates 调用 | JDK 21 + Android SDK 34/build-tools 34 → `assembleDebug`（默认，不签名；可选 release 未签名）→ 上传 APK + 体积写入 job summary | 5–8 min | 否 |
| `gates.yml` | 仅 `workflow_dispatch` | 并行复用 ci / e2e / android 三条（一键全套） | 与最慢的 e2e 接近 | 视子流程 |
| `android-ci.yml`（legacy） | 仅 `workflow_dispatch` | 旧的内置模拟器 smoke（AVD + 悬浮窗证据） | 8–12 min | 是（KVM） |

旧的 `quick.yml`（ci.yml 已覆盖）与 `healthcheck.yml`（一次性探针）已删除。

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
| 浏览器 E2E（四套） | ✅（main / 手动） | 调试时 | 需要 Docker + Playwright |
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

## 6. Actions 分钟消耗

公开仓库的标准 Linux runner 不计费（额度层面免费），但仍应控制墙钟时间：

- `ci.yml`：每次 push/PR 都跑，~3–5 min。
- `e2e.yml`：默认只在 main push 与手动跑，~10–15 min；feature 分支不自动跑。
- `android.yml`：默认只在手动/tag 跑，~5–8 min。
- `android-ci.yml` 模拟器：已改成手动，避免每次 push 触发 AVD。
