# 第六轮 · 数据与发布收口（w19 / feat/data-r3）

> 基线：`r5-20261105` / `a283342`。worktree `treehole-w19`，分支 `feat/data-r3`。
> 范围声明见仓库根 `AGENT_SCOPE.md` 的 w19 段。

## 一、五项完成度

| # | 事项 | 状态 | 说明 |
|---|---|---|---|
| 1 | 备份/导出完整性（提醒 + 统计 + 服务端记忆槽） | **完成** | v3 包新增 `alarm`（原生闹钟登记状态）；导入后自动 `syncReminders` 重推；v2/v1 老包可迁移、脏块降级；前端 8 例 + 服务端 5 例往返测试，逐字段比对 |
| 2 | 单条消息删除/编辑 UI | **完成** | 长按 450ms + **左滑 ≥36px** 双入口；删除二次确认；编辑改为「回退旧统计 → 重记新统计」并按新文案保留仍被提到的记忆；服务端删改后记忆槽/历史/磁盘一致 |
| 3 | 统计清空入口 | **完成** | 设置页「记录与隐私」内动态注入「清空使用统计」（不必改 `index.html`），5 秒内二次点击生效；清完走空态分支重绘，图表归零而非 NaN |
| 4 | 中危 / 高危话术重写 | **完成** | 按「不吓人、不承诺、给可执行的一步」重写，落 `docs/SAFETY_COPY.md`，并逐字接入 `server/risk_guard.py` + `web/js/risk.js` |
| 5 | 发布包 0.6.0 | **部分完成（缺云端 secrets）** | versionCode 6 / versionName 0.6.0；keystore 已生成（仓库外）；新增 `.github/workflows/android-release.yml`；**GitHub Secrets 未配置 → signed APK 尚未产出，体积待云端首跑** |

## 二、往返测试（真实 JSON 文件，无 mock）

### 前端 `scripts/test_data_roundtrip.test.js`：8/8 通过

| 用例 | 断言要点 |
|---|---|
| v3 包四块齐全 | `reminders` / `alarm` / `stats` / `server` 都在；alarm 归一化为 `pending` |
| 100% 还原 | 提醒 7 字段（id/text/at/done/doneAt/createdAt/updatedAt）、统计 6 字段（msgs/haha/night/days/perDay/emoji）逐字段 + 类型比对；JSON 真实落盘再读回 |
| 重复导入 | 提醒不翻倍、统计不漂移 |
| v2 老备份 | `version=2`、无新块 → 迁移成功，走真实 `importNow` 落盘 |
| v1 草稿 | `chats` 改名迁移；`reminders/stats/server` 为脏类型时降级不崩 |
| 删除回退 | 删唯一「哈哈」消息后 `haha=0`、对应 emoji 键消失、perDay 减 1；导入即恢复 |
| importNow 重推闹钟 | `syncReminders` 被调用 1 次且内容为合并后的整表；`readAlarm` 归一化正确 |
| 原生注入脚本 | 在 vm 中跑真实 `xm_host_bootstrap.js`：`merge / update / clearDone` 均触发推送 |

### 服务端 `server/tests/test_data_r3.py`：5/5 通过

| 用例 | 断言要点 |
|---|---|
| 导出→删文件→导入 | state 13 字段 + 槽 11 字段逐字段（含类型）一致；`session-*.json` / `slots-*.json` 真实存在且为新实例冷读一致 |
| 旧版无 slots 备份 | 只带 messages/memories 也能导入，不崩 |
| 删除消息 | 历史、内存记忆、记忆槽、磁盘四层一致；不同条消息产生的记忆不被误删 |
| 编辑消息 | 旧文案独有记忆/槽清掉，新文案仍含的保留；冷读一致 |
| 槽文件损坏 | 坏 JSON → 按空库冷读，不阻断对话 |

### 全量闸门

| 命令 | 结果 |
|---|---|
| `node --test "web/tests/*.test.js"` | **222 / 222 通过**（基线 222，不降） |
| `node --test scripts/test_data_roundtrip.test.js scripts/test_ui_w19.test.js` | **13 / 13 通过** |
| `python -m pytest server/tests -q` | **158 passed + 19 subtests passed**（基线 153 + 19，新增 5） |

## 三、本机资源采样（串行执行）

采样口径：系统空闲时本身被 Trae/浏览器占用 45–84%，故按「相对空闲基线的增量」评估；
内存取 node/python 进程 WorkingSet 峰值；GPU 无计算路径，0% 占用。

| 任务 | 空闲基线 CPU | 测试期间均值 | 峰值 | **增量峰值** | 进程内存峰值 | 占 15.9 GB |
|---|---|---|---|---|---|---|
| node web 全量（222） | 73.9% | 56.5% | 92.4% | **18.4%** | 551 MB | 3.38% |
| pytest 全量（158） | 84.2% | 78.7% | 100% | **15.8%** | 315 MB | 1.94% |
| node 往返（13） | 44.9% | 62.3% | 74.5% | **29.6%** | 230 MB | 1.41% |

全部串行；未起 headless 浏览器 / AVD / Gradle。

## 四、发布包（0.6.0）

| 项 | 值 |
|---|---|
| versionCode / versionName | `6` / `0.6.0`（`android/app/build.gradle.kts`、`scripts/build_apk.sh` 同步） |
| 签名 keystore | `C:\Users\xx\.secrets\xiaoman-release.jks`（**仓库外**，PKCS12，RSA 2048，10000 天，alias `xiaoman`） |
| 证书 SHA-256 | `C8:90:2C:80:2C:87:32:60:C3:2E:6B:16:CF:AB:BB:3A:4B:00:22:0A:51:27:55:B6:43:B7:E7:A4:AB:22:BD:DB` |
| 配套材料 | `xiaoman-release.b64`（→ `ANDROID_KEYSTORE_B64`）、`xiaoman-release.env`（口令/别名，权限 600） |
| 出包流水线 | `.github/workflows/android-release.yml`（`workflow_dispatch` + `v*` tag；既有 `android.yml` 未改） |
| 签名校验 | 云端 `apksigner verify --print-certs` + `zipalign -c -v 4`，证书 SHA-256 写入 job summary |
| signed APK 体积 | **待云端首跑**（本地无 JDK/Gradle，且禁止本地 Gradle；缺 secrets 时流水线只产出 unsigned 并标 `signed=NO`） |

启用正式签名只需三步（仓库外操作）：
1. Settings → Secrets → Actions 添加上述 4 个 secret；
2. Actions → android-release → Run workflow；
3. job summary 里核对体积 / SHA-256 / 证书指纹，与上表一致。

## 五、遗留与说明

1. **signed APK 尚未产出**：本机无 `gh` 登录态，无法写入 secrets 或触发云端 job。需在仓库设置里补 secrets 后手动跑一次。
2. **关于页版本号**：`web/index.html` 属本轮禁改文件，其中静态文案仍为 `v0.5`；APK 的实际版本以 `build.gradle.kts` 的 0.6.0 为准，建议 UI 轮统一改。
3. **真机 UI 点选**：按硬约束未在本地起浏览器/AVD，删除/编辑菜单与设置页入口只有源码级接线测试（5 例）；交互实测请在云端 e2e.yml 或真机执行。
4. **文件域偏离**：第 1 项必须改 `web/js/backup.js` 与 `web/js/reminders.js`（数据层），已在 `AGENT_SCOPE.md` w19 段登记；未触碰 `web/css/**`、`web/index.html`、`web/js/stage.js`、`pet.js`、`mock_engine.js`、`chat_pager.js`、`package.json`、`ci.yml`、`assets/**`、`android.yml`。
5. **测试入口**：新增测试放在 `scripts/`（`test_data_roundtrip` / `test_ui_w19`）以遵守文件域，因此 222 的闸门数字不变，需要额外跑这两份。
