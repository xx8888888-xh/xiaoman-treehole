# 质量闸门 R3（w22 / feat/quality-r3）

基线：主干 `r5-20261105`（`a283342`）。本轮只补「闸门本身」，不动业务代码。

## 闸门全景（谁在哪里跑）

| 闸门 | 本地 | 云端 | 归属文件 | 说明 |
|---|---|---|---|---|
| node 单测（`web/tests`） | `scripts/gate.ps1` | `ci.yml` · node-tests | `web/package.json` | 每次 push / PR 硬闸门 |
| biome（白名单） | `gate.ps1` + pre-commit | `ci.yml` · node-tests | `web/package.json` | 只查白名单文件，0 error 才算过 |
| pytest（`server/tests`） | `gate.ps1` | `ci.yml` · python-tests | — | 含 `compileall` 与发布物校验 |
| python 脚本用例 | ❌ 不跑 | `ci.yml` · python-scripts | `.github/workflows/ci.yml` | 要起 `server/mock_api.py`，留给云端 |
| 发布物一致性（`verify_assets`） | `gate.ps1` + pre-commit | `ci.yml` · python-tests | `scripts/verify_assets.py` | web（真源）↔ `assets/www`（发布物） |
| 浏览器 e2e + 截图留档 | ❌ 禁止本地 headless | `e2e.yml` | `.github/workflows/e2e.yml` | 产物名带提交短 sha，保留 7 天 |
| Android 出包 | ❌ 禁止本地 Gradle | `android.yml` / `android-release.yml` | — | 本轮不碰 |
| 视觉走查 / 性能 | ❌ | `ui-shots.yml` / `perf.yml` | — | 本轮不碰 |

本地原则：串行、单进程、不碰浏览器/AVD/Gradle；云端才是硬闸门。

## 1) python 脚本用例进闸门

`ci.yml` 的 `python-scripts` 矩阵已覆盖两个脚本：

```yaml
matrix:
  script: [test_mock_api, test_memory_recall]
```

复核结论：**基线 `a283342` 已包含该矩阵**（来源提交 `c1a6f08 feat(ci): ci 增加 python-scripts 矩阵`），
不是本轮补齐项。仍保留原实现：每个脚本单独一个 job（`fail-fast: false`），
起 `python server/mock_api.py`（不引入 Docker），日志落到 `artifacts/` 并上传 3 天。
也就是说：上一轮「150/152 红着没人拦」的根因是**用例本身红**，不是闸门缺位；
脚本一旦红，`python-scripts` 会直接把整条 ci 打红。

## 2) biome 覆盖面（分档纳入）

实测（Biome 2.x，`biome check <file> --reporter=summary`，基线 `a283342`）：

| 文件 | error | warning | 状态 |
|---|---|---|---|
| `errors.js` | 0 | 2 | 白名单（基线已在） |
| `app.js` | 0 | 89 | 白名单（基线已在） |
| `pet.js` | 0 | 38 | 白名单（基线已在） |
| `float_mode.js` | 0 | 2 | **本轮新增** |
| `friend_card.js` | 0 | 4 | **本轮新增** |
| `memory.js` | 0 | 1 | **本轮新增** |
| `mock_engine.js` | 0 | 14 | **本轮新增** |
| `reminders.js` | 0 | 5 | **本轮新增** |
| `stats.js` | 0 | 10 | **本轮新增** |
| `tts.js` | 0 | 46 | **本轮新增** |
| `widget.js` | 0 | 3 | **本轮新增** |
| `stage.js` | **1** | 20 | 暂缓（error: `lint/suspicious/useIterableCallbackReturn`） |
| `heartbeat.js` | **3** | 11 | 暂缓（3 个 error） |

- 白名单落在 `web/package.json` 的 `lint` 脚本；`npm run lint` 覆盖 21 个源文件 + `tests/`，
  实测 46 个文件 / 0 error / 284 warning / 131 info，退出码 0。
- warning 不拦闸门（Biome 默认只在 error 时非 0），所以「先纳入无 error 的文件」是安全的。
- `stage.js` / `heartbeat.js` 需要改源码才能清零 error，本轮文件域禁止动 `web/js/**`，
  因此只提供观测命令 `npm run lint:staged`（**预期红**，用于看告警量），等后续分支修完再并入 `lint`。
- 纳入顺序：先 0 error（本轮 8 个）→ 再修 `stage.js`（1 error）→ 最后 `heartbeat.js`（3 error）。

## 3) 本地一键闸门 `scripts/gate.ps1`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/gate.ps1
powershell ... -File scripts/gate.ps1 -SkipNode      # 只跑 python 半边
powershell ... -File scripts/gate.ps1 -SkipPython    # 只跑 node 半边
```

- 串行、单进程：node 单测 → biome → pytest → `verify_assets`，**不并行、不开新进程池**。
- `assets/www` 是生成物（`.gitignore`）；干净 worktree 里检测到缺失会先 `bash scripts/build_assets.sh`
  同步（与 `ci.yml` 同口径），已存在时只校验，用来抓工作树漂移。
- 解释器探测顺序：`.venv` → `.venv-win` → PATH；npm 缺失 / `node_modules` 缺失会直接标 FAIL 并给修复命令。
- 每步日志：`artifacts/gate/<step>.log`；末尾打印通过/失败表，有失败则附日志尾部并 `exit 1`。

实测输出（w22，干净 worktree，串行总耗时 ≈25s）：

```
================ 本地闸门结果 ================
PASS   node --test (web)                      14.8s  ...\artifacts\gate\node.log
PASS   biome check（白名单）                        1.4s  ...\artifacts\gate\biome.log
PASS   pytest server/tests                     8.4s  ...\artifacts\gate\pytest.log
PASS   verify_assets.py（web ↔ assets/www）      0.5s  ...\artifacts\gate\assets.log
==============================================
全部闸门通过。
```

## 4) pre-commit 轻检查

`.githooks/pre-commit`（`sh`，无 BOM，LF）：

- 只做两件事：biome 白名单 + `verify_assets`。**不跑** `node --test` / `pytest`，避免拖慢提交。
- 依赖缺失（无 `node_modules` / 无 python / 无 npm）时降级为警告，不硬拦；云端 `ci.yml` 仍是硬闸门。
- 临时跳过：`git commit --no-verify`。
- 启用（每个克隆/worktree 各一次）：

```bash
git config core.hooksPath .githooks
```

> 本轮**不**替所有人改 `core.hooksPath`：并行 worktree 共用同一份仓库 config，
> 擅自启用会把其他任务的提交也纳入轻检查。由使用者按需开启。

## 5) e2e 产物留档

- `e2e.yml` 新增 `Resolve short SHA` 步，产物名：`e2e-artifacts-<7位短sha>`。
- 上传内容：`artifacts/` 全量 —— 截图（`shots/`、`live2d/`、`raf/`）+ 四套件日志 + 关键 JSON。
- 保留期 7 天（`retention-days: 7`）；`if: always()`，红灯也留档。
- Job Summary 末尾标注产物名，便于从 run 页面直接找。

## 维护约定

1. 新增源文件进 `web/js/` 时，先在本地跑 `biome check js/<file>.js --reporter=summary`；
   0 error 就追加进 `package.json` 的 `lint` 白名单，有 error 先修再纳入。
2. `lint` 白名单只增不减；确需移除要在本文件记录原因。
3. 本地只跑 `gate.ps1`（低频），日常提交靠 pre-commit（轻），最终判定一律看云端 run。
4. 红灯不 skip：要么修源码，要么缩小文件域并在此登记，不允许 `continue-on-error` 蒙混。
