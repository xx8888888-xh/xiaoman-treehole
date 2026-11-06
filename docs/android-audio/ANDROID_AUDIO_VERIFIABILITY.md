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
| 本轮是否改产品代码 | **没有**。未发现音频回归，产品代码/断言零改动；只改了 CI 取证脚本与 workflow（`4c7b557`，用于恢复判定能力） | `4c7b557` 只含 `scripts/emulator_noisy_check.py` + `.github/workflows/android-ci.yml` |

一句话：**#117–#119 的红是“新加的验收脚本自己没跑通”，不是“音频坏了”；#120 修复后 noisy 已 `PASS 3 / FAIL 0 / SKIP 0`、chat `11/11`，只剩既有的浮窗像素红。可闻性仍未证明，浮窗像素红在 `origin/main` 合并前就已存在。**

---

## 1. `android-ci` 真实结果（#110、#113–#120）

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

推断（尚未证到具体一层）：

- 最一致的解释是“透明 overlay 里的 Live2D 内容没有被 `screencap` 从软件 GL（swiftshader_indirect）读回”。脚本已内置 `refresh_screen()` 强制整屏重合成，仍为 0，说明不是简单的 damage 未刷新。
- 具体是 SurfaceFlinger 的 damage、WebView 合成层、还是 GL 读回路径，需要再做一次分层取证（见 §4.3 P2-7），本轮不下断言。

### 3.5 未放宽、未 skip

- 三条断言仍在，阈值仍为 `6000`，失败仍计 `[FLOAT-FAIL]` 并使 job 退出 1。
- 本轮只在“像素失败”时增加了一条 `[FLOAT-DEGRADE]` 分支：**跳过依赖像素差的拖动/面板尺寸等交互步骤**，改用 dumpsys 结构继续验证；**§3.1 这 3 条目标断言仍然执行、仍然照常 FAIL**，没有改成 PASS、没有 `xfail`、没有提前 return。
- #110 与 #116 的对比可直接复现：`gh run view 42045722234 --log`、`gh run view 42126103792 --log`。

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
| 桌宠 Live2D 在 `screencap` 截图上可见 | **不能** | 3 条像素断言恒 `diff=0px`（§3） |
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

1. **[云端] `4c7b557`**：noisy 三态 + 依赖前置 + logcat 采集窗口，验收 run #120。
2. **[云端] 下次复跑用**：`gh workflow run android-ci.yml --ref docs/audio-verifiability`，确认 noisy 仍为 `PASS`、chat 仍 `11/11`；float 的 3 条像素红按 §3 保留。

**P1（必须真机）**

3. **[真机] 可闻性回环测试（路径 B）**：instrumentation + `AudioRecord(MIC)` 录 3 秒，断言有声段 RMS 比静音基线高 ≥20 dB，并同时检查 `[probe] audio-focus request granted` / `[probe] tts-stop`。这是唯一不靠人耳的“真的出声”证明。
4. **[真机] 拔耳机验证 `ACTION_AUDIO_BECOMING_NOISY`**：播放中拔耳机，期望 `[probe] audio-becoming-noisy → stop` 且播放停止；注册层已由 #120 证明，这一步只补“广播→行为”的闭环。
5. **[真机] 按 §2.3 的 6 步最小人工路径留一份 logcat 存档**（用户不方便 instrumentation 时的降级证据）。

**P2（可云端，补齐证据链；不替换现有断言）**

6. **[云端] 带 TTS 引擎的镜像上做信号链探针（路径 A）**：换 `google_apis` 镜像或预装引擎，断言 `available()==True`、`speak()==True`、`dumpsys audio` / `media.audio_flinger` 出现 active track。**不要移除 `-no-audio`**：它只能把问题缩到“输出设备”一段，仍不证明可闻。
7. **[云端] 用 CDP `Page.captureScreenshot` 补桌宠 WebView 的像素证据**：绕开 `screencap`/SurfaceFlinger，统计非透明像素，用来分层判定 §3.4 的“推断”；这是新增证据，屏幕级三条断言保持原样。
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
```

本地复现（可选，需要同规格 AVD，当前不主动启动）：

```bash
# AVD: api30 / x86_64 / pixel_4，参数与 CI 一致
# emulator -avd <name> -no-window -gpu swiftshader_indirect -no-snapshot -memory 3072 -no-audio
adb install -r app-debug.apk
adb shell am start -n com.xiaoman.treehole/.MainActivity --ez autostart_float true -f 0x20000000
python scripts/emulator_float_check.py   # 预期同样 3 条 [FLOAT-FAIL]，diff=0px
```

---

## 6. 约束与声明

- 本轮未改产品代码、未改测试断言、未放宽阈值、未加 skip/xfail。
- 本轮只改 `scripts/emulator_noisy_check.py` 与 `.github/workflows/android-ci.yml`（`4c7b557`）：引入的是“取证失败→SKIP（无法判定）”三态，不是把产品断言 skip 掉；三条像素断言仍是硬 FAIL。
- 未移除 `-no-audio`，未新增 AVD 静音豁免。
- 脚本与文档分两个 commit（`4c7b557` CI 修复、本次文档），都推在同一分支 `docs/audio-verifiability`；未 force push、未动他人分支。
- 交付物：本文档 + `scripts/emulator_noisy_check.py` / `.github/workflows/android-ci.yml` 的取证层修复。
