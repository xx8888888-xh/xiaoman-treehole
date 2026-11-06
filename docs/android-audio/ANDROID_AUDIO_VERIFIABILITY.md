# Android 音频链路 · 结论收口与可验证性判定

- 日期：2026-11-06
- 基线：`origin/main` @ `17fd464`（已包含 `fix/audio-focus-noisy` 的合并 `ba85ae2`）
- P0 修复：`docs/audio-verifiability` @ `4c7b557`（只改 CI 脚本与 workflow，未改产品代码）
- 改动边界：文档/脚本之外的**产品代码与断言均未改动、未放宽/未 skip 任何检查**
- 数据来源：GitHub Actions `android-ci` #110 / #113–#120、`android` #9、`ci` #102 的真实日志（`gh run view <id> --log`）

---

## 0. 结论（TL;DR）

| 问题 | 判定 | 关键依据 |
|---|---|---|
| `android-ci` 最近几次 run 为什么红 | **三次红全部倒在 `emulator_noisy_check.py` 这一新增检查上，且是三种不同的 CI 自身问题；不是音频功能回归** | #117 `IndexError: list index out of range`（断言文案里的 `c.split(" ", 2)[2]` 越界）；#118 dumpsys 层 `ReceiverList` 已 PASS、仅 logcat 探针行被缓冲挤掉；#119 `ModuleNotFoundError: No module named 'websockets'`（noisy check 排在 `pip3 install` 之前） |
| 断言到底跑了什么 | #117/#118 的聊天链路 **11/11 真跑过且全过**；#117/#118/#119 的浮窗检查 **全部没跑到**（noisy check 先 exit 1），所以这三次的浮窗状态是“未执行”，既不是绿也不是红 | `[CHAT-SUMMARY] 11/11 通过`；`ERROR: NoisyGuard registration check failed` → `##[error]The process '/usr/bin/sh' failed with exit code 1` |
| 真机能不能出声，CI 能否证明 | **当前 CI 无法证明可闻性，而且原因有两条、彼此独立**：镜像没有 TTS 引擎（`available()=False` → `speak()` 直接返回 `False`，根本没创建 AudioTrack）；AVD 又带 `-no-audio`。CI 能证明的只是“三条 WebView 都注入了桥且可调用”，不是“有声音” | `[FLOAT-PASS] ... available()=False speak()返回=False(boolean) err=-`；AVD 参数 `-no-window -gpu swiftshader_indirect -no-snapshot -memory 3072 -no-audio` |
| 浮窗 3 条像素断言仍红，是否是本次改动引入 | **不是。** 合并前的 `origin/main` 基线 #110（`2163963`）同 3 条、同 `diff=0px`、同 frame 坐标；这 3 条断言的代码在 `2163963` 与当前 HEAD 之间**逐字节一致**；AVD 配置也一致。定位到截图路径（`dumpsys` frame 存在 + `[probe] pet-ready` 通过，但 screencap 在该 frame 内的像素差恒 0） | #110 `[FLOAT-SUMMARY] 18/21`；#113 `19/22`；#114 `19/23`；#116 `23/26`；#120 `23/26`；各 run 的 3 条 `[FLOAT-FAIL]` 完全相同 |
| 本轮是否改产品代码 | **是（已授权）**。`95e9f12` 只改 `web/css/pet.css` / `web/css/style.css` / `web/js/app.js` 的兼容写法；`4c7b557` 只改 CI 取证脚本与 workflow。断言与阈值零改动 | `git show 95e9f12 --stat`；`git diff 9c9d0c2 HEAD -- scripts/emulator_float_check.py` 中三条断言与 `PET_MIN_DIFF=6000` 未变 |
| 浮窗 3 条像素红的真实根因 | **不是 screencap 抓不到，也不是 Live2D 没画**：浮窗页在 CI 的 WebView 83 上 `#petRoot/#petStage/canvas` 布局为 0x0（`inset:0` 不被解析），canvas 背板有 476,535 个非透明像素但元素无尺寸、从未合成到屏幕 → **屏幕上确实看不到桌宠，三条断言是真失败**。**已按根因修复（`95e9f12`）并在 #125/#126 转绿；#127/#128 浮窗 41/41 全绿** | 根因见 run #121–#123 `[FLOAT-PROBE]`（§3.6）；转绿证据见 #125/#126 `[FLOAT-PASS] 桌宠可见 diff=99669px` / `可拖动 diff=101929px` / `收起后复现 diff=102129px`；全绿证据见 #128 `[FLOAT-SUMMARY] 41/41 通过` |

一句话：**#117–#119 的红是“新加的验收脚本自己没跑通”，不是“音频坏了”；#120 修复后 noisy 已 `PASS 3 / FAIL 0 / SKIP 0`、chat `11/11`。浮窗 3 条像素红的根因是 CI 的 WebView 83 不解析 `inset:0` → 浮窗页 0×0 → 屏幕上确实不可见（§3.6），已按根因修产品代码（`95e9f12`），#125/#126 实测 3 条像素断言自然转绿（`diff=99669px` / `101929px` / `102129px`，阈值 `PET_MIN_DIFF=6000` 未动）。同一轮暴露的“双击展开迷你窗”6 条红来自 adb `input tap` 两枪间隔 493ms > 380ms（#126 实证），已改为页内 `window.__tap` 注入（实测 152.1ms），#127/#128 浮窗 **`41/41` 全绿**（§3.8 / §3.10）。可闻性仍未证明。**

---

## 1. `android-ci` 真实结果（#110、#113–#128）

### 1.1 run 总表（`gh run list --workflow android-ci.yml`）

| run | 时间(UTC) | 分支 / commit | web-tests | build-apk | emulator-smoke | chat 断言 | float 断言 | 红在哪 |
|---|---|---|---|---|---|---|---|---|
| #110 | 11-05 14:30 | main / `2163963`（合并前基线） | **fail** | success | **fail** | 未执行到（当时 workflow 无 chat 步骤） | **18/21** | 3 条像素 + web-tests（`test_lifeline_ui.py:68` Playwright click 30s 超时；旧版前端初始化红，后续由 `157bb4c` 的 `api.js` 惰性化修复） |
| #113 | 11-05 15:46 | fix/audio-focus-noisy / `157bb4c` | success | success | **fail** | **10/10 通过** | **19/22** | 3 条像素 |
| #114 | 11-05 22:45 | fix/audio-focus-noisy / `51724e0` | success | success | **fail** | **11/11 通过** | **19/23** | 3 条像素 + 双击展开迷你窗 `frame=None`（adb 双击时序，后由 `42be337` 修） |
| #115 | 11-05 22:59 | fix/audio-focus-noisy / `42be337` | — | — | — | — | — | **cancelled**（被 #116 取代，不计） |
| #116 | 11-05 22:59 | fix/audio-focus-noisy / `4cfcebf` | success | success | **fail** | **11/11 通过** | **23/26** | 3 条像素（本轮分支终态） |
| #117 | 11-05 23:41 | main / `724236a` | success | success | **fail** | **11/11 通过** | 未执行 | `emulator_noisy_check.py` 自身 `IndexError` |
| #118 | 11-06 00:08 | main / `f5dba85` | success | success | **fail** | **11/11 通过** | 未执行 | noisy 的 logcat 探针断言（dumpsys 层已 PASS） |
| #119 | 11-06 00:27 | main / `17fd464` | success | success | **fail** | 未执行 | 未执行 | `ModuleNotFoundError: No module named 'websockets'` |
| #120 | 11-06 01:48 | docs/audio-verifiability / `4c7b557` | success | success | **fail** | **11/11 通过** | **23/26** | noisy `PASS 3 / FAIL 0 / SKIP 0`；唯一红是 3 条**既有**像素断言 |
| #125 | 11-06 12:02 | docs/audio-verifiability / `95e9f12`（产品修复） | success | success | **fail** | **11/11 通过** | **32/38** | 3 条像素断言**全绿**；红 6 条全在「双击展开迷你窗」链（adb 两枪间隔，§3.8） |
| #126 | 11-06 12:13 | docs/audio-verifiability / `8123c04`（补双击诊断） | success | success | **fail** | **11/11 通过** | **32/38** | 同上；`[FLOAT-DIAG]` 实测两枪间隔 493ms > 380ms |
| #127 | 11-06 12:26 | docs/audio-verifiability / `a39ef6d`（CDP/页内双击注入） | success | success | **success** | **11/11 通过** | **41/41** | 6 条双击链红全绿；CDP 触摸超时→页内 `__tap`，间隔 152.1ms |
| #128 | 11-06 12:34 | docs/audio-verifiability / `ba0929e`（`window.__tap` 暴露） | success | success | **success** | **11/11 通过** | **41/41** | 复核一致；本次作为最终验收 run |

补充事实（拉取时点，2026-11-06）：

- `#117–#119` 的红全部来自 noisy 检查自身；**#120 是本轮 P0 修复后的验证 run**：三个探针第一次真正按顺序跑完（依赖 → noisy → chat → float），noisy `PASS 3 / FAIL 0 / SKIP 0`，chat `11/11`，float 执行完并给出 `23/26`。job 仍红只因为那 3 条既有像素断言（§3），不是音频问题。
- 旁证：`android` #9（main `ba85ae2`）success，其中 `:app:testDebugUnitTest` 输出 `BUILD SUCCESSFUL`（该任务此时包含 `AudioFocusPolicyTest` / `NoisyGuardTest` / `ReminderSchedulingTest`，Gradle 任务成功即无测试失败）；`ci` #102（main `17fd464`）success（`pytest 188 passed, 2 skipped, 19 subtests passed`，脚本矩阵 `12/12 passed`，`node:test` 与 Biome 亦通过）。
- 也就是说：**单测与 web 门是绿的**。`#110–#119` 里唯一红的是模拟器 smoke，其中 `#117–#119` 红在新加的 noisy 检查；`#120` 起 noisy 已转 `PASS 3 / FAIL 0 / SKIP 0`，模拟器 smoke 只剩 3 条既有像素红。

### 1.2 #117：断言文案自己越界

失败的 job：`emulator-smoke`（step `Run emulator & smoke test with screenshots`）。关键行：

```text
[CHAT-SUMMARY] 11/11 通过
[NOISY-PASS] 应用进程存活（pid=1974） | com.***.treehole pid=1974
Traceback (most recent call last):
IndexError: list index out of range
ERROR: NoisyGuard registration check failed
##[error]The process '/usr/bin/sh' failed with exit code 1
```

越界点是 `724236a` 版本 `scripts/emulator_noisy_check.py` 第 75 行：

```python
check("dumpsys 有输出", bool(sources), "来源=" + ",".join(c.split(" ", 2)[2] for c, _ in sources))
```

`sources` 里含 `"dumpsys activity"`（只有两段），`split(" ", 2)` 得到 2 个元素，取 `[2]` 必然 `IndexError`。这是**断言文案的 bug**，不是产品问题；`f5dba85` 已改成直接拼 `c`。

### 1.3 #118：操作系统层证据 PASS，只有 logcat 探针行没抓到

失败的 job：`emulator-smoke`。关键行：

```text
[CHAT-SUMMARY] 11/11 通过
[NOISY-PASS] 应用进程存活（pid=1993） | com.***.treehole pid=1993
[NOISY-PASS] dumpsys 有输出 | 来源=dumpsys activity broadcasts, dumpsys activity broadcasts --full, dumpsys activity
[NOISY-PASS] dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY | dumpsys activity broadcasts | ReceiverList{8f4fc0 1993 com.***.treehole/10130/u0 remote:4f75d43} app=1993:com.***.treehole/u0a130 pid=1993 uid=10130 user=0 Filter #0: BroadcastFilter{5d1a4f9} Action: "android.media.AUDIO_BECOMING_NOISY" *
[NOISY-FAIL] logcat：[probe] noisy-receiver registered | logcat 里没有该探针（可能缓冲区已滚动）
ERROR: NoisyGuard registration check failed
```

判定：

- **注册这件事在操作系统层已经被证明**（`ReceiverList` 里有本进程 pid、uid、包名与 `AUDIO_BECOMING_NOISY` filter）。
- 红的是“logcat 里必须还能找到注册那一行”这条交叉印证。脚本头注释自己写的是“交叉印证，不单独作为通过依据”，但代码把它做成了硬失败，**注释与实现不一致**。
- 因此 #118 的红是**采集时序/日志缓冲问题**，不是 `registerReceiver` 没执行。

### 1.4 #119：noisy check 跑在 `pip install` 之前

失败 job：`emulator-smoke`。关键行：

```text
Traceback (most recent call last):
  File ".../scripts/emulator_noisy_check.py", line 29, in <module>
ModuleNotFoundError: No module named 'websockets'
ERROR: NoisyGuard registration check failed
##[error]The process '/usr/bin/sh' failed with exit code 1
```

链路是确定的：

1. `emulator_noisy_check.py:29` 是 `from android_cdp import PKG, sh`（`4c7b557` 起已改为自带标准库实现，不再 import）；
2. `scripts/android_cdp.py:20` 是 `from websockets.sync.client import connect`；
3. `17fd464` 把 noisy check 移到“紧跟冷启动”（workflow 第 210 行），而 `pip3 install --quiet --user requests websockets` 在第 221 行。

所以 #119 的 `emulator-smoke` 在 **chat/float 之前** 就退出，**聊天和浮窗一个断言都没跑**。这是一条纯 CI 编排问题，已在 `4c7b557` 修复（见 §4.0），不需要动产品代码。

### 1.5 小结：这三次红说明了什么、没说明什么

- 说明：在 `#117–#119` 期间，`android-ci` **不具备判定音频回归的能力**——新检查连续三次都以不同方式自己失败，把后续 chat/float 全挡掉了；`#120` 修复后已恢复（§1.6）。
- 没说明：没有任何一条日志显示“TTS 调用失败 / 音频焦点申请失败 / 停读没生效”。相反，#117/#118 的 chat 链路 11/11 全过，说明前端初始化、发送、接收、TTS 桥注入都正常。
- 结论：**未见音频回归。**

### 1.6 #120：P0 修复后的验证 run（关键证据）

- run：`42146247414`，ref `docs/audio-verifiability` @ `4c7b557`，手动 `workflow_dispatch`。
- job：`build-apk` success、`web-tests` success、`emulator-smoke` **failure**（唯一红是 3 条既有像素断言）、`assemble-release` skipped。

```text
probe deps ok, PIL 13.0.0
[NOISY-PASS] 应用进程存活 | com.***.treehole pid=2574
[NOISY-PASS] dumpsys：ReceiverList 里注册了 ACTION_AUDIO_BECOMING_NOISY | dumpsys activity broadcasts | ReceiverList{8565fc3 2574 com.***.treehole/10130/u0 remote:e5e6f72} ... Action: "android.media.AUDIO_BECOMING_NOISY" *
[NOISY-PASS] logcat：[probe] noisy-receiver registered | [probe] noisy-receiver registered
NoisyGuard 注册验收：PASS 3 / FAIL 0 / SKIP 0
[CHAT-SUMMARY] 11/11 通过
[FLOAT-PASS] FloatWindowService 运行中（ServiceRecord） | record=1
[FLOAT-PASS] 桌宠窗口已注册（140x180dp frame） | frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 桌宠可见（frame 内 Live2D 差分≥6000px） | diff=0px frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程恢复前：桌宠正常运行 | frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程被杀后重开：桌宠自动恢复 | frame=(673, 799, 1058, 1294) record=1
[FLOAT-SUMMARY] 23/26 通过
ERROR: float window check failed
```

- 这证明：依赖先装、noisy 三态、chat、float **确实按顺序执行了**；logcat 探针在 `logcat -c` + 重启触发 + `--pid` 拉取后**真的拿到了**（#118 的缺失是采集窗口问题，已在采集侧修好）。
- 这仍然**不证明**可闻性（`available()=False` 未变，见 §2.1），也不代表浮窗像素红已解决（§3）。

### 1.7 三路取图与双击注入取证 run（#121–#128）

| run | commit | 本轮加什么 | float 结果 | 关键结论 |
|---|---|---|---|---|
| #121 | `627b26a` | `[FLOAT-PROBE]`：screencap + CDP `Page.captureScreenshot` + canvas `getImageData` | `23/26`（仍 3 条像素红） | canvas 有 47.6 万非透明像素；CDP 截图 819B 全透明 → 两种取图源都空 |
| #122 | `adfa963` | 补 `innerW/H`、`#petRoot/#petStage/canvas` 的 `clientWidth/Height` 与 rect；主界面 WebView 对照组 | `23/26` | 浮窗页视口 140x180 正常，但 `#petRoot/#petStage/canvas` 全为 0x0；主界面 `#puppet` 85x196 正常 |
| #123 | `db544ff` | 补 `navigator.userAgent`、`CSS.supports('inset','0')`、`#petRoot` 计算样式 | `23/26` | UA `Chrome/83.0.4103.120`；`inset` 支持 `false`；`#petRoot` 无有效偏移 → 收缩为 0x0（根因，见 §3.6） |
| #124 | `23133e2` | 文档收口（无脚本变化） | `23/26` | 基线复核，仍 3 条像素红 |
| **#125** | `95e9f12`（产品修复） | `pet.css` 两处 `inset:0` 拆成显式四边；`style.css`/`app.js` 同步 | **`32/38`**，3 条像素断言**全部转绿** | `桌宠可见 diff=99669px`、`可拖动 diff=101929px`、`收起后复现 diff=101904px`；`[FLOAT-PROBE] layer` 仍 `content=0px`（CDP 透明页限制，仅取证，不参与判定） |
| **#126** | `8123c04`（补双击诊断） | `[FLOAT-DIAG]` 打印 ⑥ 期间 pet 探针（不改断言） | **`32/38`**，3 条像素断言再次全绿 | `收起后复现 diff=102129px`；`[FLOAT-DIAG]` 实证两次 `input tap` 间隔 **493ms > 380ms**，双击链未触发 |
| **#127** | `a39ef6d`（换双击注入源） | CDP `Input.dispatchTouchEvent` → 超时 → 页内 `window.__tap`（`PointerEvent` 直达产品监听） | **`41/41` 全绿** | `inject_gap=page_gap=152.1ms`；`pet-tap→pet-double→pet-panel` 同一毫秒级窗口内；6 条新红全绿 |
| **#128** | `ba0929e`（`window.__tap` 暴露） | 同上，最终验收 | **`41/41` 全绿** | `[FLOAT-SUMMARY] 41/41 通过`；`双击触发探针`/`TTS 注入`/`迷你窗展开 1164750px`/`桌宠隐藏 hidden_diff=0px` 均 PASS |

> #121–#124 走的是「桌宠不可见」降级分支（26 条断言）；#125 起桌宠可见，进入双击交互链（38 条断言），因此分母变化。拆出的 6 条红全部来自 adb 双击时序（§3.8），已在 #127/#128 用页内 `window.__tap` 注入修掉并把断言总数走到 41 条（`41/41`），与 `inset` 修复无关。

`build-apk` / `web-tests` 在 #121–#128 均 success；#127/#128 起 `emulator-smoke` 也 success。取证图随 `emulator-screenshots` artifact 上传（`emulator_04_float_layer.png`、`emulator_04_float_crop.png`、`emulator_04_float_sidebyside.png`）。#125 起 `emulator_04_float_pet.png` 与 screencap 一致且有内容（744KB）。

---

## 2. “真机能不能出声”：判定与可验证方法

### 2.1 判定：当前 CI 证明不了可闻性

两条独立原因，缺一不可地说明“为什么 CI 的绿不能当可闻性证据”：

1. **镜像没有 TTS 引擎。** `available()` 的实现是 `queryIntentServices("android.intent.action.TTS_SERVICE")`，空列表即 false（`TtsController.java:143-152`）。#116 的浮窗桥断言实测：

   ```text
   [FLOAT-PASS] 桌宠 WebView JS 桥可调用 AndroidTTS.speak（返回 boolean） | speak=function available=function available()=False speak()返回=False(boolean) err=-
   [FLOAT-PASS] 迷你窗 WebView JS 桥可调用 AndroidTTS.speak（返回 boolean） | speak=function available=function available()=False speak()返回=False(boolean) err=-
   ```

   `speak()` 在 `!hasEngine()` 时**直接返回 false，连 `TextToSpeech` 实例都不会创建**（`TtsController.java:80-90`）。没有引擎 → 没有合成 → 没有 AudioTrack。CI 的 AVD 是 `api-level: 30 / arch: x86_64 / profile: pixel_4`（默认镜像，非 `google_apis`）。#120 复测同一结论：两条桥断言仍为 `available()=False speak()返回=False(boolean)`。

2. **AVD 带 `-no-audio`。** 即使换上带引擎的镜像，`-no-audio` 也会让模拟器不接宿主音频后端；信号可以进混音器，但到不了任何可听输出。

因此：

- **已证明**：三条 WebView（主界面 / 桌宠 `/www/pet.html` / 迷你窗 `/www/index.html?float=1`）都注入了 `window.AndroidTTS`，`speak/speakRate/stop/available` 都是 `function`，调用返回 boolean。
- **未证明**：真的发出可听见的声音。桥“可调用”与“有声音”之间隔着“引擎存在 + 合成成功 + 音频进混音器 + 输出到扬声器”四段。

### 2.2 不依赖手工听力的三种验证路径

> 原则：把“可闻”拆成可观测的信号链，任何一段缺失都要如实记为“未证明”，不能拿上一段的绿替代下一段。

**路径 A：AudioTrack / 混音器层探针（云端可做，但需要带 TTS 引擎的镜像）**

1. 先用 `adb shell pm list packages | grep -i tts` 确认镜像里真的有引擎；没有就换 `google_apis` 镜像或预装引擎 APK。
2. 通过 CDP 在浮窗 WebView 里调一次 `AndroidTTS.speak("probe")`，同时抓：
   - `adb shell dumpsys audio` → 查找 `AudioPlaybackConfiguration` 里本 uid / TTS 引擎 uid 的 `state=started`；
   - `adb shell dumpsys media.audio_flinger` → 查找 `Track` / `ACTIVE` 且属于对应 pid；
   - `adb logcat -s XiaoManAudio` → `[probe] audio-focus request granted`。
3. 断言：`available()==True`、`speak()` 返回 `True`、混音器里出现 active track、焦点探针出现。

注意：这条路径证明“PCM 已提交到混音器”，**仍不证明可闻**（`-no-audio` 下没有宿主输出）。它的价值是把失败点从“不知道”缩到“只差输出设备这一段”。

**路径 B：硬件回环录音（必须真机，可无人值守）**

在真机上跑一个 instrumentation 测试（或 debug-only 的 `AudioRecord` 探针）：

1. 申请 `RECORD_AUDIO`（测试专用，不进产品权限）；
2. 开 `AudioRecord`（`MIC`，16kHz 单声道）录 3 秒，同时调 `TtsController.speak("这是一段用于回环检测的中文语音")`；
3. 对录音算 RMS / 峰值，断言：
   - 有声段 RMS 比静音基线高 **≥ 20 dB**（绝对值建议先实测标定，例如 `> -45 dBFS`）；
   - 时长与文本长度匹配（防止只录到环境噪声）；
4. 同时断言 logcat `[probe] audio-focus request granted` 与 `[probe] tts-stop`。

这是**唯一能自动化、又不依赖人耳**的“真的出声了”判定。缺点：需要真机 + 麦克风权限；阈值要按机型标定一次。

**路径 C：应用内 `AudioPlaybackCallback` 探针（真机/云端均可，但只能看到自己 uid）**

注册 `AudioManager.registerAudioPlaybackCallback` 观察本进程的 `AudioPlaybackConfiguration` 是否出现 `PLAYER_STATE_STARTED`。它能证明“我们自己发起了播放”，但 TTS 引擎是另一个应用，跨 uid 通常需要 `MODIFY_AUDIO_ROUTING`，因此**不能单独作为可闻证据**，只适合做辅助。

### 2.3 如果不想上 instrumentation：真机上“点一下”的最小步骤

1. `adb install -r app-debug.apk`
2. 打开 App，等首屏稳定；点一下消息列表区（`#messages` 内任意处，打开 tap-only 5 秒门），或直接点一条已载入的回复气泡（`51724e0` 起支持“点气泡重播”，不需要先过门）
3. 另开一个终端：`adb logcat -c && adb logcat -s XiaoManAudio`，然后再点气泡
4. 期望看到 `[probe] audio-focus request granted`，并且**听到声音**
5. 若无声：`adb shell pm list packages | grep -i tts` 确认引擎；系统设置 → 语言与输入 → TTS 输出，切换引擎/下载中文语音包后重试
6. 拔耳机（在同一段播放中）：期望 `[probe] audio-becoming-noisy → stop`，且 `dumpsys audio` 里本应用播放配置转为 stopped

---

## 3. 浮窗 3 条像素断言：定位结论

### 3.1 哪 3 条、断言原文

来自 `scripts/emulator_float_check.py`（当前 HEAD 与 `origin/main` 相同）：

| # | 断言 | 判定表达式 |
|---|---|---|
| 1 | `桌宠可见（frame 内 Live2D 差分≥6000px）` | `pet_ok = pdiff >= PET_MIN_DIFF`（`PET_MIN_DIFF = 6000`） |
| 2 | `进程恢复前：桌宠正常运行` | `before_ok = p11 is not None and region_diff(base, cur11, p11) >= PET_MIN_DIFF` |
| 3 | `进程被杀后重开：桌宠自动恢复` | `if p12 is not None and region_diff(base, cur12, p12) >= PET_MIN_DIFF: after_ok = True` |

三条都依赖 `screencap` 截图的像素差分 `region_diff(...)`，不是 dumpsys 结构，也不是 logcat 探针。

### 3.2 `origin/main` 同轮对照（合并前基线）

| run | 分支 / commit | float 汇总 | 三条失败行 |
|---|---|---|---|
| #110 | main / `2163963`（**合并前**） | `18/21` | 三条，见下 |
| #113 | branch / `157bb4c` | `19/22` | 同三条 |
| #114 | branch / `51724e0` | `19/23` | 同三条 + 双击 `frame=None` |
| #116 | branch / `4cfcebf` | `23/26` | 同三条 |

#110（`origin/main` 合并前）原文：

```text
[FLOAT-FAIL] 桌宠可见（frame 内 Live2D 差分≥6000px） | diff=0px frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程恢复前：桌宠正常运行 | frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程被杀后重开：桌宠自动恢复 | frame=(673, 799, 1058, 1294) record=1
[FLOAT-SUMMARY] 18/21 通过
```

#116（本轮分支终态）原文：

```text
[FLOAT-FAIL] 桌宠可见（frame 内 Live2D 差分≥6000px） | diff=0px frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程恢复前：桌宠正常运行 | frame=(673, 799, 1058, 1294)
[FLOAT-FAIL] 进程被杀后重开：桌宠自动恢复 | frame=(673, 799, 1058, 1294) record=1
[FLOAT-SUMMARY] 23/26 通过
```

**同 frame、同 `diff=0px`、同 3 条，跨 main 与分支 4 次 run 稳定复现。**

### 3.3 断言代码未被改动（逐字节一致）

```bash
git diff 2163963c4330b7459132a00c1415c6301da960b4 HEAD -- scripts/emulator_float_check.py
```

输出只有**新增**（JS 桥断言、CDP 展开迷你窗、像素失败时的结构降级路径），上述三条断言行没有出现在 `-`/`+` 里；`git show 2163963:scripts/emulator_float_check.py` 与当前 HEAD 的相关行文本一致：

```text
PET_MIN_DIFF = 6000
check("桌宠可见（frame 内 Live2D 差分≥6000px）", pet_ok, f"diff={pdiff}px frame={pframe}")
before_ok = p11 is not None and region_diff(base, cur11, p11) >= PET_MIN_DIFF
check("进程恢复前：桌宠正常运行", before_ok, f"frame={p11}")
if p12 is not None and region_diff(base, cur12, p12) >= PET_MIN_DIFF:
```

AVD 配置也一致（`2163963` 与 `17fd464` 的 workflow 均为）：

```text
api-level: 30 / arch: x86_64 / profile: pixel_4
emulator-options: -no-window -gpu swiftshader_indirect -no-snapshot -memory 3072 -no-audio
```

结论：**不是本次音频/UI 改动引入的。**

### 3.4 定位到哪一层

强证据（可下结论）：

- `dumpsys window` **能拿到桌宠 frame** `(673, 799, 1058, 1294)`（窗口存在、尺寸正确）；
- `[probe] pet-ready` **PASS**（WebView 页面侧已就绪）；
- 同一次 run 里 `[FLOAT-PASS] 桌宠窗口已注册`、`[FLOAT-PASS] 未降级为圆形悬浮球`；
- 唯独该 frame 内 `screencap` 与基线的像素差恒为 `0`。

这三件事同时成立，说明失败点在**“截图像素”这一条取证路径**，不在窗口创建、不在服务、不在 JavaScript。

佐证（断言算法本身可用）：

- `docs/android-battery/ANDROID_BATTERY_REPORT.md` 记录的另一次 AVD 实测（api34 / 720×1280）同一断言得到 `diff=21138px`、frame `(424,426,704,786)`。也就是说 `region_diff` 在正常截图路径下能测到 Live2D 本体；CI 的 api30/pixel_4/swiftshader_indirect 组合才恒 0。

~~推断（尚未证到具体一层）~~（已由 §3.6 落定）：

- ~~最一致的解释是“透明 overlay 里的 Live2D 内容没有被 `screencap` 从软件 GL（swiftshader_indirect）读回”~~：**§3.6 已推翻**——CDP 渲染层截图同样全透明，且浮窗页自身布局为 0×0；不是 GL 读回路径的问题。
- 真因见 §3.6：`web/css/pet.css` 的 `inset: 0` 在 CI 的 WebView 83 上不被解析，浮窗页收缩为 0×0，屏幕上确实没有桌宠。

### 3.5 未放宽、未 skip

- 三条断言仍在，阈值仍为 `6000`，失败仍计 `[FLOAT-FAIL]` 并使 job 退出 1。
- 本轮只在“像素失败”时增加了一条 `[FLOAT-DEGRADE]` 分支：**跳过依赖像素差的拖动/面板尺寸等交互步骤**，改用 dumpsys 结构继续验证；**§3.1 这 3 条目标断言仍然执行、仍然照常 FAIL**，没有改成 PASS、没有 `xfail`、没有提前 return。
- #110 与 #116 的对比可直接复现：`gh run view 42045722234 --log`、`gh run view 42126103792 --log`。

### 3.6 三路取图对照与根因（run #121–#123，2026-11-06）

**结论先说**：`diff=0px` 既不是“Live2D 图层没画”，也不是“screencap 抓不到该图层”，而是**浮窗页在 CI 的 WebView 83 上整体布局成 0×0**：canvas 背板里画了 47.6 万非透明像素，但 canvas 元素的 CSS 尺寸是 0×0，从未参与合成。屏幕上确实看不到桌宠，所以 3 条断言是**真失败**——不改断言、不换阈值、不 skip。

#### 3.6.1 同一时刻的三路取证（#123，`[FLOAT-PROBE]` 原文）

```text
[FLOAT-PROBE] screencap: full=1080x2280 crop=385x495 changed_vs_base=0px unique=447
[FLOAT-PROBE] layer: src=Page.captureScreenshot 385x495 mode=RGBA content=0px unique=1 819B
[FLOAT-PROBE] canvas: {"ua": "... Chrome/83.0.4103.120 ...", "cssSupport": {"inset": false, "top": true},
  "rootCss": {"top": "0px", "left": "0px", "right": "140px", "bottom": "180px", "w": "0px", "h": "0px",
              "display": "block", "position": "fixed"},
  "viewport": {"innerW": 140, "innerH": 180, ..., "dpr": 2.75, "vis": "visible", "hasFocus": false},
  "root": {"cw": 0, "ch": 0, "rect": [0,0,0,0]}, "stage": {"cw": 0, "ch": 0, "rect": [0,0,0,0]},
  "canvasEl": {"cw": 0, "ch": 0, "rect": [0,8,0,0]},
  "present": true, "w": 626, "h": 1440,
  "state": {"mounted": true, "ready": true, "mode": "preview", ...},
  "alpha_gt0": 480979, "alpha_gt16": 476535, "total": 901440, "alpha_ratio": 0.5286, "dataurl_len": 720150}
[FLOAT-PROBE] control(main index.html): {"innerW": 393, "innerH": 751, "dpr": 2.75, "vis": "hidden",
  "canvases": [{"id": "puppet", "w": 170, "h": 392, "cw": 85, "ch": 196}]}
[FLOAT-PROBE] compare: size=385x495 layer_content=0px screencap_changed=0px overlap=0px -> 两源一致
[FLOAT-PROBE] recovery-before: layer_content=0px src=Page.captureScreenshot ... 819B
[FLOAT-PROBE] recovery-after: layer_content=0px src=Page.captureScreenshot ... 819B
```

（为可读性省略了重复字段；完整原文见 run `42150649869` 的 `emulator-smoke` 日志。）

#### 3.6.2 三路数据怎么读

| 证据源 | 数据 | 说明 |
|---|---|---|
| `adb exec-out screencap` | frame `385×495` 与基线 `changed=0px` | SurfaceFlinger 合成结果里该区域只有壁纸 |
| CDP `Page.captureScreenshot` | `385×495` RGBA、`819B`、`unique=1`、`content=0px` | 直接取 WebView 渲染层也**全透明** → 不是 screencap 单方面盲 |
| 页内 canvas `getImageData` | `alpha_gt16=476535/901440`（52.86%），`dataURL` 720150 字符 | Live2D **确实画进了 canvas 背板**，`ready=true` |
| 布局 | `innerW/H=140/180` 正常；`#petRoot/#petStage/canvas` 全部 `cw=ch=0`；canvas rect `[0,8,0,0]` | 视口正常，但浮窗页根节点被压成 0×0 |
| 对照组主界面 | `#puppet` canvas `cw=85 ch=196` | 同一 WebView 引擎，主界面布局正常 → 不是引擎整体坏 |
| 引擎能力 | `CSS.supports('inset','0')=false`；UA `Chrome/83.0.4103.120` | `inset` 简写自 Chrome 87 才支持 |

#### 3.6.3 根因链

1. `web/css/pet.css:19-30` 的 `#petRoot { position: fixed; inset: 0 }` 与 `#petStage { position: absolute; inset: 0 }` 依赖 CSS `inset` 简写；
2. CI 的 AVD（api30 / pixel_4）自带 WebView 为 `Chrome/83.0.4103.120`，**不解析 `inset`**（`CSS.supports` 为 false），两条声明被整条丢弃 → 元素没有 `top/right/bottom/left` 偏移；
3. `#petRoot` 是 `position:fixed` 且子元素全是绝对定位 → shrink-to-fit 收缩成 `0×0`（计算样式实测 `w/h=0px`）；`#petStage` 同理；
4. `#petStage canvas { height: 300% }` 的百分比高度基于 0 高度父级 → canvas 元素也是 `0×0`（rect `[0,8,0,0]`）；
5. `puppet.fit()` 里 `this.canvas.clientHeight || 720` 的兜底让背板按 720px 画了 `626×1440`，`visibleRect()` 在 `cw/chh=0` 时退回全画布 → 背板里有 47.6 万非透明像素；
6. 但元素没有布局盒 → Chromium 不合成、SurfaceFlinger 也没有内容 → 两个取图源都空。

#### 3.6.4 因此不能走的两条“捷径”

- 不能把 3 条断言改成 CDP 截图判定：CDP 实测 `content=0px`，改了仍红，只是把同一层失败换个说法。
- 不能改成 canvas alpha 判定：`alpha_gt16=476535` 会让断言变绿，但屏幕上桌宠**确实不可见**——这等于用“背板画了”掩盖“没合成”，属于放宽断言强度，禁止。

#### 3.6.5 复现步骤（云端，无需真机）

```bash
gh workflow run android-ci.yml --ref docs/audio-verifiability
gh run view 42150649869 --log | grep -E "FLOAT-PROBE|FLOAT-FAIL|FLOAT-SUMMARY"
```

期望输出（节选）：`layer: ... content=0px unique=1 819B`、`canvas ... "alpha_gt16": 476535`、`root/stage/canvasEl ... cw/ch=0`、`cssSupport.inset=false`、`UA ... Chrome/83.0.4103.120`、`[FLOAT-SUMMARY] 23/26`。

本地同规格 AVD（api30/x86_64/pixel_4，`-gpu swiftshader_indirect`）跑 `python3 scripts/emulator_float_check.py` 可复现同一组 `[FLOAT-PROBE]`；也可用 CDP 直接核：

```bash
adb forward tcp:9225 localabstract:webview_devtools_remote_<app-pid>
curl -s localhost:9225/json/list          # 找到 pet.html
# 在页面执行：CSS.supports('inset','0') / getComputedStyle(petRoot).width / canvas.clientWidth
```

#### 3.6.6 已修复（产品代码 `95e9f12`，用户已授权）

- `web/css/pet.css`：`#petRoot` / `#petStage` 的 `inset: 0` → `top/right/bottom/left: 0` 显式四边。
- `web/css/style.css`：`#app::before`（`inset: 0 0 auto` → `top/right/bottom:auto/left`）、`.stage-skeleton`、`#stage::before`、`.stage-fallback`（`inset: var(--space-3)` → 四边）同步拆分；`42dvh/33dvh/23dvh` 前加 `vh` 兜底。
- `web/js/app.js`：编辑遮罩内联 `inset:0` → 四边显式。
- 修复后 #125/#126 三条像素断言**自然转绿**（`diff=99669px` / `101929px` / `102129px`），**断言与 `PET_MIN_DIFF=6000` 均未动**。
- 未采用的替代：CI 换 WebView ≥ 87 镜像。修复落在产品侧，任何 WebView 83 真机同样受益。

### 3.7 全仓 CSS 兼容扫描（WebView 83 / Chrome 83 基线）

> 扫描命令：`grep -rn "inset:" web/`、`grep -rnE "dvh|:has\(|gap:|:focus-visible" web/css web/js`；`env(safe-area-inset-*)` 属安全区变量（Chrome 69 已支持），不改。

| 语法 | 最低版本 | 本仓出现处 | 是否已修 | 影响 |
|---|---|---|---|---|
| `inset` 简写 | Chrome 87 | `pet.css` 2 处（已修）、`style.css:198,269,314,379`（已修）、`app.js:925`（已修） | **全修** | 未修时整条声明丢弃 → 浮窗 0×0（本轮根因）；主界面遮罩/占位层同理 |
| `dvh` | Chrome 108 | `style.css:166,199,299,308` | **已修**（`166` 原有 `100%` 兜底；其余补 `vh`） | 未修时高度退回 `auto`/内容高度，舞台与顶栏留白偏差 |
| `:has()` | Chrome 105 | `style.css:303`（骨架让位，已用相邻兄弟选择器补齐）、`321`、`708` | **部分修** | `303` 已等价；`321` 只在聊天有内容时压缩舞台高度、`708` 只影响发送键配色，均为装饰性，旧 WebView 不生效 |
| flex `gap` | Chrome 84（flex） | `pet.css:76`；`style.css` 37 处；`app.js:930,944` | **未修**（列此备查） | 旧 WebView 下间距塌成 0，仅排版变紧，不影响任何断言/像素门限；用 margin 兜底会与现代浏览器双倍间距，风险大于收益 |
| `:focus-visible` | Chrome 86 | `style.css:212,662` | **未修**（列此备查） | 旧 WebView 无键盘焦点环；触屏主路径不受影响，且有 `[hidden]`/`aria` 兜底 |

结论：本轮只修「会改变布局尺寸、进而影响像素断言」的 `inset` 与 `dvh`；纯装饰类（flex gap / `:focus-visible` / 两处 `:has`）只登记不改，避免扩大产品改动面。

### 3.8 修复后的云端验收（#125–#128）与双击链归因

| 断言 | #121–#124 | #125 | #126 | #128 |
|---|---|---|---|---|
| 桌宠可见（frame 内差分≥6000px） | FAIL `diff=0px` | **PASS `diff=99669px`** | **PASS `diff=99669px`** | **PASS `diff=99669px`** |
| 桌宠可拖动并贴边 | FAIL | **PASS `diff=101929px`** | **PASS `diff=101929px`** | **PASS `diff=101929px`** |
| 收起后桌宠复现 | FAIL | **PASS `diff=101904px`** | **PASS `diff=102129px`** | **PASS `diff=102129px`** |
| float 汇总 | `23/26` | `32/38` | `32/38` | **`41/41`** |

`[FLOAT-PROBE]` 在 #125 的对照：`screencap: crop=385x495 changed_vs_base=99669px unique=36993`；`layer: Page.captureScreenshot 385x495 content=0px unique=1 819B`。**判定源仍是 screencap**，CDP 透明页限制只作归因，未替换断言。

**新增的 6 条红（与 `inset` 修复无关）**：桌宠可见后首次进入「双击展开迷你窗」链路，#126 `[FLOAT-DIAG]` 原文：

```text
[FLOAT-DIAG] after-double: ... 12:13:57.187 [probe] pet-tap:head || 12:13:59.067 [probe] pet-tap:head || 12:13:59.560 [probe] pet-tap:head
```

两枪都到位，但间隔 **493ms**，超过产品双击窗口 `DOUBLE_MS=380`（`pet_core.js:23`），于是被识别为两次单击（两次 `pet-tap`），`pet-double` 不出现 → 后续 6 条（大窗口 frame、双击探针、迷你窗 TTS 注入、迷你窗桥、面板像素、桌宠隐藏）连带红。这与 `42be337` 已记录的「adb 每次 `input` 重启进程，两枪间隔超阈值」是同一问题。

**已修（只换注入源，断言与阈值未动）**：#127/#128 改为 CDP `Input.dispatchTouchEvent` 连发两枪；实测该 AVD 的 WebView 83 **不支持** `Input.dispatchTouchEvent`（`timed out in 10.0s`），自动降级为页内 `window.__tap` 探针——在 `document` 捕获阶段构造并派发 `PointerEvent`，与 `pet.js` 的 `onDown/onUp` 同一监听入口。页面侧同时挂捕获记录器，实测：

```text
[FLOAT-DIAG] cdp-touch 不可用（timed out in 10.0s），降级页内 PointerEvent（同一入口）
[FLOAT-DIAG] double-inject mode=page-__tap inject_gap=152.1ms page_gap=152.1ms
             now_before=32563.5 now_after=32750 downs=2 err=None
[probe] pet-tap:head 12:37:53.126 || [probe] pet-double 12:37:53.303 || [probe] pet-panel 12:37:53.304
```

`inject_gap`/`page_gap` = **152.1ms**（落在要求的 120–200ms，且远小于 380ms）；`now_after-now_before=186.5ms` 也落在窗口内；`pet-double`/`pet-panel` 由产品自己的 `onUp` 判定产生，证明走的是产品真实事件路径。结果 **`41/41` 全绿**（6 条新红全部转绿，`PET_MIN_DIFF` 与判定式未改）。没有绕过双击去直调 `openPanel()`。

`[FLOAT-PASS]` 原文（#128）：`双击触发探针` / `迷你窗 WebView 注入 AndroidTTS` / `迷你窗展开 diff=1164750px` / `展开后桌宠已隐藏 hidden_diff=0px frame=None` / `收起后桌宠复现 diff=102129px`。

### 3.9 布局塌陷的耗电影响（真机旧 WebView）

布局塌陷（`inset` 不解析 → 页面 0×0）与"看不见"是两件事：**窗口仍在，渲染进程也仍在**。真机旧 WebView（≤ 86）上的实际行为：

| 外观预设 | `motion` | 塌陷时的行为 | 耗电影响 |
|---|---|---|---|
| **极简（默认）** | `false` | 挂载时 `renderOnce()` 画一帧后 `freezeFrame()` 停 rAF；无循环 | 近似"一张静止位图"：无持续 GPU 出帧；但 WebView 进程常驻 + 前台服务（`specialUse`）不释放，系统无法进入深度 Doze，属**常驻基线功耗** |
| 默认 / 生动 | `true` | `envBlocked()` 只认 `envPaused` 与 `document.hidden`；塌陷时窗口对 WindowManager **可见**，`document.hidden=false` → 暂停逻辑抓不到；rAF 以满帧跑，但元素 0×0，合成结果全透明 | 前 10s（`IDLE_STOP_MS`）**满帧空转**：每帧仍要光栅化 `626×1440≈90 万`像素背板（`fit()` 在 `clientHeight=0` 时回退 `720`），约 54M 像素/秒的无效填充；10s 后 `enterIdleStop()` 停 rAF，降为每 10s 一帧呼吸（`BREATH_WAKE_MS`） |
| 任意 | 任意 | `styleTimer` 每 15s、`reminderTimer` 每 30s、触摸/气泡/`__petRepaint` 都会唤醒一次；`setMotionEnabled` 若值变化会 `noteActivity()` 重开满帧窗口 | 即使已静止停机，也有 15s/30s 级定时唤醒；提醒命中会再触发 10s 满帧空转 + 气泡动画 |

定性结论：**默认极简预设下，"不可见"不会带来持续跑渲染循环**（只有常驻服务基线功耗）；**一旦用户选了默认/生动（`motion=true`），塌陷期间每次唤醒都会带来最多 10s 的满帧无效渲染**，耗电与"正常显示桌宠"同量级却完全没有可见收益。修复 `inset` 后，这部分无效功耗与"桌宠不可见"一并消失。

真机核对方法（无需人耳）：`adb shell dumpsys gfxinfo com.xiaoman.treehole framestats` 看帧数与空转；`adb shell dumpsys batterystats --charged com.xiaoman.treehole` 对比 `motion=true` 修复前后 30 分钟的 `Uid u0a...` 能耗增量；`adb shell dumpsys window` 确认 overlay 窗口仍 `isVisible=true`。CI 侧 `window.__pet.state()` 已有 `{motion, loop, idle}`，可直接断言 `loop=false`（`motion=false` 时）。

### 3.10 adb `input` 时序不可信（实测数据，勿再踩）

| run | 注入方式 | 两枪实测间隔 | 结果 |
|---|---|---|---|
| #126 | `adb shell "input tap; input tap"` | **493ms**（logcat 12:13:57.…→12:13:59.…） | 两次单击，`pet-double` 不出现 |
| #127 | CDP `Input.dispatchTouchEvent` | 命令超时 10s（WebView 83 未实现 Input 域） | 降级 |
| #127/#128 | 页内 `window.__tap`（`PointerEvent` → `document` 捕获） | **152.1ms**（页面侧 `performance.now()` 实测；宿主侧 152.1ms；总跨度 186.5ms） | `41/41` 全绿 |

结论：**任何用 `adb shell input tap` 两次模拟双击的用例都不可靠**——每条命令都会重启 `app_process`，在 CI AVD 上单次启动成本约 0.2–0.5s，天然超出 380ms 双击窗口；且这个间隔随机器负载漂移，无法用 `sleep` 校准。可靠做法是页内派发 `PointerEvent`（或 CDP 触摸，需 WebView 支持），并在页面侧用 `performance.now()` 记录两枪间隔、用 logcat `pet-double` 反证产品路径已执行。禁止为了变绿把断言降级为 `pet-tap` 或直接调 `openPanel()`。

---

## 4. CI 能证明什么、不能证明什么（P0 修复后）

> 标注：**[云端]** = GitHub Actions / 本地脚本可完成；**[真机]** = 必须 Android 真机 + adb。

### 4.0 P0 修复内容与验收（commit `4c7b557`）

| 问题 | 修复 | 验收证据（#120） |
|---|---|---|
| #117 `c.split(" ", 2)[2]` 越界 | 来源名只按**命令串结构**生成，不再假设固定列；解析异常/无 `ReceiverList` 结构一律记 `[NOISY-SKIP] 无法判定（解析失败）` | `[NOISY-PASS] ... 来源=dumpsys activity broadcasts ...`，无 Traceback |
| #119 `ModuleNotFoundError: websockets` | ① noisy 脚本改为**只依赖标准库**（不再 `import android_cdp`）；② workflow 把 `requests/websockets/pillow` 安装与 import 自检移到**任何探针之前** | `probe deps ok, PIL 13.0.0` 出现在 noisy 之前 |
| #118 logcat 探针被缓冲挤掉 | `logcat -c` 清空 → `force-stop/start` 触发一次干净注册 → `logcat -d --pid=<pid> -s XiaoManAudio` 拉取（失败回退无 `--pid`）；拿不到只记 `SKIP`，不判红、也不假装通过 | `[NOISY-PASS] logcat：[probe] noisy-receiver registered` |

验收 run：`42146247414`（#120，`workflow_dispatch`，ref `docs/audio-verifiability` @ `4c7b557`）
结果：`build-apk` success / `web-tests` success / `emulator-smoke` failure（**仅** 3 条既有像素断言）/ `assemble-release` skipped；
`NoisyGuard 注册验收：PASS 3 / FAIL 0 / SKIP 0`、`[CHAT-SUMMARY] 11/11 通过`、`[FLOAT-SUMMARY] 23/26 通过`。
**判定能力已恢复**：三个检查第一次按“依赖 → noisy → chat → float”顺序真正跑完并各自给出结论。

### 4.1 能力边界矩阵

| 命题 | CI 能否证明 | 依据 |
|---|---|---|
| 前端初始化、聊天发送/接收、消息落列表 | **能** | #[120] `[CHAT-SUMMARY] 11/11 通过` |
| `NoisyGuard` 在操作系统层注册了 `AUDIO_BECOMING_NOISY` | **能** | #[120] `[NOISY-PASS] dumpsys ... ReceiverList{...} Action: "android.media.AUDIO_BECOMING_NOISY"` |
| 注册探针日志可被采集 | **能（#120）/ 采不到时只记 SKIP** | #[120] `[NOISY-PASS] logcat`；#118 的缺失已定位为采集窗口问题 |
| 三条 WebView 注入了 `AndroidTTS` 且可调用 | **能** | `speak/available` 为 `function`，调用返回 boolean |
| 浮窗服务、窗口、进程恢复链 | **能** | #[120] `23/26` 中除 3 条像素外的结构断言全过 |
| 桌宠 Live2D 画布是否真的绘制 | **能（背板）/ 不能（屏幕）** | #123 canvas `alpha_gt16=476535`（52.86%，`ready=true`）；但 canvas 元素 `cw=ch=0`，从未合成 → 屏幕上不可见（§3.6） |
| WebView 渲染层能否取到桌宠内容（CDP `Page.captureScreenshot`） | **不能** | #121/#123 均为 385×495、819B、`unique=1`、`content=0px`（全透明） |
| 浮窗页布局是否正常（CI 的 WebView） | **不能** | #123 `#petRoot/#petStage` 均 0×0；`CSS.supports('inset','0')=false`、UA `Chrome/83.0.4103.120`；主界面 `#puppet` 85×196 正常 |
| TTS 引擎可用（能合成） | **不能** | `available()=False`（`queryIntentServices(TTS_SERVICE)` 为空） |
| 真机可闻 | **不能** | 无引擎 + `-no-audio`，无 AudioTrack、无输出设备 |
| 拔耳机后立即停读的行为闭环 | **不能** | CI 只能证明 receiver 注册；物理拔插必须真机 |
| 浮窗里真的发声 | **不能** | 同“TTS 引擎可用 / 真机可闻”两条 |

### 4.2 SKIP 语义（新增三态）

- `PASS`：证据成立；`FAIL`：能判定且判定不成立；`SKIP`：**无法判定**（解析失败 / dumpsys 无输出 / logcat 采集窗口内没拿到 / 应用未启动导致无取证对象）。
- 脚本总会打印 `NoisyGuard 注册验收：PASS n / FAIL n / SKIP n`；只有 `FAIL` 让退出码为 1，`SKIP` 不判红。
- `SKIP` **不是**“把断言 skip 掉”：产品断言（三条像素、bridge、聊天）仍是硬 PASS/FAIL；`SKIP` 只用于“取证失败、无法判定”的采集项，且必须在汇总里显式计数，不能用绿色掩盖。
- 取证文件落盘：`noisy_dumpsys.txt`、`noisy_logcat.txt`，失败时随 `uiautomator-dump` artifact 上传。

### 4.3 剩余行动清单

**P0（已完成，云端）**

0. **[已完成 · 产品代码 `95e9f12`] 修浮窗页 `inset` 兼容**：`web/css/pet.css` 两处 `inset: 0` → `top/right/bottom/left: 0`；`style.css` 四处与 `app.js` 一处同步拆分，`dvh` 补 `vh` 兜底。验收 #125/#126：3 条像素断言自然转绿（`diff=99669px`/`101929px`/`102129px`），断言代码与 `PET_MIN_DIFF=6000` 未动。全仓扫描见 §3.7，耗电影响见 §3.9。
0b. **[已完成 · 取证侧 `a39ef6d`/`ba0929e`] 双击链注入方式**：#126 实测 adb `input tap` 两枪间隔 493ms > `DOUBLE_MS=380`，导致「双击展开迷你窗」6 条断言红。已改为 CDP `Input.dispatchTouchEvent`（该 AVD 的 WebView 83 不支持，超时）→ 自动降级页内 `window.__tap`（`PointerEvent` 派发到 `document` 捕获阶段，与 `pet.js` `onDown/onUp` 同一入口）；页面侧用 `performance.now()` 记录两枪间隔 **152.1ms**，logcat 出现 `pet-double`/`pet-panel` 反证产品路径执行。#127/#128 `41/41` 全绿，断言与阈值未改；**禁止**绕过双击直接调 `openPanel()`。详见 §3.8/§3.10。
1. **[云端] `4c7b557`**：noisy 三态 + 依赖前置 + logcat 采集窗口，验收 run #120。
2. **[云端] 下次复跑用**：`gh workflow run android-ci.yml --ref docs/audio-verifiability`，确认浮窗 `41/41`（含 3 条像素断言与双击链）、noisy `PASS`、chat `11/11`；双击注入方式见 §3.10，勿改回 adb `input tap`。

**P1（必须真机）**

3. **[真机] 可闻性回环测试（路径 B）**：instrumentation + `AudioRecord(MIC)` 录 3 秒，断言有声段 RMS 比静音基线高 ≥20 dB，并同时检查 `[probe] audio-focus request granted` / `[probe] tts-stop`。这是唯一不靠人耳的“真的出声”证明。
4. **[真机] 拔耳机验证 `ACTION_AUDIO_BECOMING_NOISY`**：播放中拔耳机，期望 `[probe] audio-becoming-noisy → stop` 且播放停止；注册层已由 #120 证明，这一步只补“广播→行为”的闭环。
5. **[真机] 按 §2.3 的 6 步最小人工路径留一份 logcat 存档**（用户不方便 instrumentation 时的降级证据）。

**P2（可云端，补齐证据链；不替换现有断言）**

6. **[云端] 带 TTS 引擎的镜像上做信号链探针（路径 A）**：换 `google_apis` 镜像或预装引擎，断言 `available()==True`、`speak()==True`、`dumpsys audio` / `media.audio_flinger` 出现 active track。**不要移除 `-no-audio`**：它只能把问题缩到“输出设备”一段，仍不证明可闻。
7. **[云端·已完成] 用 CDP `Page.captureScreenshot` + canvas 读回补桌宠 WebView 的像素证据**：已接入 `[FLOAT-PROBE]`（`627b26a`/`adfa963`/`db544ff`，验收 #121–#123）。结论见 §3.6：**不是 screencap 盲，而是页面 0×0 布局**；两条替代取图源实测同样为空，因此**没有替换**三条屏幕级断言。
8. **[云端] “全程静音”断言与浮窗发声的冲突**：`emulator_float_check.py` 末尾的“全程静音”当前 PASS（浮窗流程未触发 TTS）；一旦浮窗真的发声，它会与“浮窗有声”冲突。需单独立项决定作用域，**不允许静默删掉或放宽**。

### 4.4 明确不建议做的事

- 不要为了让 `android-ci` 变绿而删除/跳过 3 条像素断言，或把 `PET_MIN_DIFF` 调小。
- 不要把 `SKIP` 用在产品断言上；它只描述“取证失败、无法判定”。
- 不要移除 `-no-audio` 来“证明有声”；这只会把“无输出设备”换成“有输出设备但无采集”，仍然证明不了可闻。
- 不要把 #117–#119 的红写成“音频回归”；证据不支持。

---

## 5. 复现命令

```bash
# 1) 看最近 run（含 #117/#118/#119）
gh run list --workflow android-ci.yml --limit 10

# 2) 逐 run 看断言到底跑了什么
gh run view 42131352792 --log | grep -E "NOISY-|CHAT-SUMMARY|FLOAT-SUMMARY|Traceback|ERROR:"
gh run view 42134683134 --log | grep -E "NOISY-|CHAT-SUMMARY|FLOAT-SUMMARY|ERROR:"
gh run view 42136973352 --log | grep -E "NOISY-|Traceback|ModuleNotFound|ERROR:"

# 3) 浮窗 3 条红的同轮对照（合并前 main 基线 vs 分支终态）
gh run view 42045722234 --log | grep -E "FLOAT-FAIL|FLOAT-SUMMARY"
gh run view 42126103792 --log | grep -E "FLOAT-FAIL|FLOAT-SUMMARY"

# 4) 证明三条断言在基线 commit 与 HEAD 之间未变
git diff 2163963c4330b7459132a00c1415c6301da960b4 HEAD -- scripts/emulator_float_check.py

# 5) P0 修复后的云端复跑（#120 就是这条）
gh workflow run android-ci.yml --ref docs/audio-verifiability
gh run view 42146247414 --log | grep -E "NOISY-|NoisyGuard 注册验收|CHAT-SUMMARY|FLOAT-SUMMARY|FLOAT-FAIL"

# 6) 三路取图取证（#121–#123，§3.6）
gh run view 42148539464 --log | grep -E "FLOAT-PROBE|FLOAT-FAIL|FLOAT-SUMMARY"
gh run view 42149633564 --log | grep -E "FLOAT-PROBE"
gh run view 42150649869 --log | grep -E "FLOAT-PROBE"

# 7) 修复后验收（#125 产品修复 / #126 双击诊断 / #127–#128 双击注入，§3.8）
gh workflow run android-ci.yml --ref docs/audio-verifiability
gh run view 42213768208 --log | grep -E "FLOAT-PASS|FLOAT-FAIL|FLOAT-SUMMARY"
gh run view 42215671965 --log | grep -E "FLOAT-DIAG|FLOAT-PASS|FLOAT-FAIL|FLOAT-SUMMARY"
gh run view 42217722680 --log | grep -E "FLOAT-DIAG|FLOAT-PASS|FLOAT-SUMMARY"   # #127
gh run view 42218715534 --log | grep -E "FLOAT-DIAG|FLOAT-PASS|FLOAT-SUMMARY"   # #128 最终验收

# 8) 证明三条断言与阈值没动（产品修复 commit 前后）
git diff 95e9f12^ 95e9f12 --stat        # 只有 web/css + web/js
git diff 9c9d0c2 HEAD -- scripts/emulator_float_check.py | grep -E "PET_MIN_DIFF|check\("
```

本地复现（可选，需要同规格 AVD，当前不主动启动）：

```bash
# AVD: api30 / x86_64 / pixel_4，参数与 CI 一致
# emulator -avd <name> -no-window -gpu swiftshader_indirect -no-snapshot -memory 3072 -no-audio
adb install -r app-debug.apk
adb shell am start -n com.xiaoman.treehole/.MainActivity --ez autostart_float true -f 0x20000000
python scripts/emulator_float_check.py   # 预期同样 3 条 [FLOAT-FAIL]；另见 [FLOAT-PROBE] 三路取证
```

---

## 6. 约束与声明

- 2026-11-06 修复轮（#125/#126）：`95e9f12` 依用户授权改产品代码，只动 `web/css/pet.css`、`web/css/style.css`、`web/js/app.js` 的 `inset`/`dvh` 兼容写法；**三条像素断言的代码、`PET_MIN_DIFF=6000` 与判定式均未改**（`git diff 9c9d0c2 HEAD -- scripts/emulator_float_check.py` 可核）。`8123c04` 只加 `[FLOAT-DIAG]` 打印，不加断言、不改阈值。
- #125/#126 实测：3 条像素断言自然转绿；`emulator-smoke` 当时还红在「双击展开迷你窗」链（adb 两枪间隔 493ms > 380ms，§3.8）。#127/#128 把注入方式换成页内 `window.__tap`（实测 152.1ms）后，**`41/41` 全绿、`emulator-smoke` success**；未用任何绕过手段掩盖。
- 取证结论：不是 screencap 盲，也不是 Live2D 没画，而是 CI 的 WebView 83 不解析 `inset:0` → 浮窗页 0×0 → 屏幕确实不可见（§3.6）；修复落在产品侧（§3.6.6），因此**没有把断言换成 CDP/canvas 源**。
- 本轮未移除 `-no-audio`，未新增 AVD 静音豁免。
- commit 划分：取证脚本 `627b26a` / `adfa963` / `db544ff` / `8123c04` / `a39ef6d` / `ba0929e`；产品修复 `95e9f12`；文档（本次）。都推在同一分支 `docs/audio-verifiability`；未 force push、未动他人分支。
- 交付物：本文档 + `scripts/android_cdp.py`（新增 `screenshot()`）/ `scripts/emulator_float_check.py`（新增 `[FLOAT-PROBE]` 取证与对照组）。
