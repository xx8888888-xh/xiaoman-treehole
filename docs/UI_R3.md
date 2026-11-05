# UI 高级感第三轮 · 克制与秩序（w18）

> 基线：`r5-20261105` / `a283342`；分支 `feat/ui-r3`；worktree `treehole-w18`。
> 文件域见 `AGENT_SCOPE.md`。本轮不换设计语言，只做三件事：**收敛尺度、拉开层级、统一细节**。
> 截图全部来自 `.github/workflows/ui-shots.yml` 的云端 artifact（本机不跑 headless 浏览器）。

## 一、字号与间距：从"到处是 px"收敛成 6 + 5 档

| 类别 | 令牌 | 值 | 用途 |
|---|---|---|---|
| 间距 6 档 | `--space-1..6` | 4 / 8 / 12 / 16 / 24 / 32 | 全站 `padding`/`margin`/`gap` 只允许这 6 个值 |
| 字号 5 档 | `--text-display/title/body/label/caption` | 25 / 19 / 16 / 14 / 12.5（大字号 ×1.125） | 页面标题 / 区块标题 / 正文 / 控件标签 / 说明 |
| 行高 4 档 | `--lh-tight/snug/body/loose` | 1.3 / 1.5 / 1.7 / 1.8 | 标题 / 控件 / 正文 / 长阅读 |
| 控件尺寸 | `--tap / --tap-sm / --tap-xs / --icon-size / --avatar / --switch-w / --switch-h` | 44 / 36 / 32 / 20 / 30 / 48 / 28 | 触控目标 ≥44px；图标、头像、开关统一 |
| 圆角 | `--radius-xs/sm/md/lg/xl/pill` | 10 / 12 / 16 / 20 / 24 / 999 | 卡片、字段、气泡、底部面板 |
| 时长 | `--dur-press / state / base / slow` | 100 / 160 / 200 / 240ms | 按压 / 状态 / 面板入场 / 抽屉 |

落地方式：`tokenize_css.js` 按属性逐条替换 → `scan_px.js` 复查。复查后仍剩 17 处 `px`，
全部是"结构尺寸"而非节奏：1px 描边、`border-left: 2/3px` 强调线、开关圆点的 1px 内缩、
`::placeholder` 之外的 `env()` 安全区回退。它们不进间距阶梯。

> 顺手修掉一处真问题：`--icon` 同时被定义为**颜色**和**尺寸**。浅色下后写的尺寸生效，
> 暗色块把颜色覆盖回去后，`width: var(--icon)` 退化成 `auto`，空态内联 SVG 被撑满整屏。
> 尺寸改名 `--icon-size`，并在外观测试里加了"令牌名不许重复"的断言。

## 二、信息层级：每页只留一个焦点

| 页面 | 之前 | 现在 |
|---|---|---|
| 首页空态 | 米色卡片 + 36px 记号 + 19px 标题 + 说明 + 3 个入口，压在开场白下面形成"卡中卡" | 去掉底色与边框，记号 20px 降到 `--text-3`，标题压到 body 档；只有一句邀请 + 三个入口，焦点交还给人像与开场白 |
| 设置页 | 分节标题与字段标签同级、次级说明满屏 | 分节标题固定 caption 档（小字距宽），字段标签 label 档，说明统一 caption；"允许免费通道"从 48 字压到 32 字，只留生效范围与默认值 |
| 记忆页 | 两个实心按钮抢焦点，chips 一行四块高低不齐 | chips 两列等宽；「加上提醒」降为次级（`--line` 描边 + `--surface-2`），「生成我们的友谊卡片」是唯一实心主 CTA，「清空记忆」收成居中文字动作 |

## 三、微交互：统一曲线，禁止循环

| 场景 | 规则 | 令牌 |
|---|---|---|
| 按钮按压 | 0.98 缩放 + 底色降一档，100ms | `--dur-press` / `--press-scale` |
| 开关滑动 | 160ms，`ease-out`；轨道与圆点同一曲线 | `--dur-state` / `--ease-out` |
| 发送态 | 小满生成中（`#typingRow` 可见）→ 发送键留在 `--accent-soft` 底 + `--accent` 字，不转圈、不变灰 | `#app:has(#typingRow:not(.hidden)) .send-btn[disabled]` |
| 语音录制态 | `.inputbar[data-recording="true"]` 输入框描边转 `--accent`（目前只有外观契约，无录音入口） | `.inputbar[data-recording="true"]` |
| 面板入场 | 底部弹层 240ms / 设置 200ms，均为 `ease-out` | `--dur-slow` / `--dur-base` |

硬约束（测试可查）：

1. 全站只有两个 keyframes：`modal-in` / `panel-in`，**没有 `infinite`**，也没有 shimmer / spin / rotate。
2. 所有 `transition` 必须引用 `var(--dur-*)`，不允许裸写毫秒。
3. 骨架屏是**静态纸片**，不做流光；`#stage:has(.stage-canvas)` 或降级卡出现即让位。
4. `html[data-motion="off"]`（极简预设）下：动画、过渡、按压全部关掉，舞台 `rAF` 为 `null`。

## 四、图标：一套 24px 网格 / 1.5px 描边

`web/js/icons.js` 是唯一图标源，`index.html` 只写 `data-icon="名字"`，运行时实例化一次。

| 名字 | 用途 |
|---|---|
| `close` | 抽屉/弹窗关闭 |
| `settings` | 顶栏设置（三组滑杆 + 实心圆点） |
| `memory` | 顶栏记忆（书签） |
| `widget` | 顶栏桌宠（双向箭头） |
| `send` | 发送 |
| `mic` | 语音（录制态契约） |
| `check` | 完成/已保存 |
| `bell` | 提醒 |
| `clock` | 时间/稍后 |

约定：`viewBox 0 0 24 24`、`stroke-width 1.5`、`fill none`、`stroke-linecap/linejoin: round`、颜色一律 `currentColor`。
`×`（U+00D7）保留为无 JS 时的文字兜底，页面里不再出现 emoji（测试扫描 U+1F300–1FAFF 与 U+2600–27BF）。

## 五、双主题：对比度实测（WCAG 2.x 相对亮度）

浅色为暖纸（`--bg #F6F2EC`），深色为暖炭黑（`--bg #15120F`）。正文门槛 7:1，其余前景 4.5:1，交互边界 3:1。

| 前景 / 背景 | 暖纸 | 暖炭黑 | 门槛 |
|---|---|---|---|
| `--text / --bg` | 15.21:1 | 15.88:1 | ≥7 |
| `--text / --panel` | 16.58:1 | 14.90:1 | ≥7 |
| `--text / --bubble-them` | 16.58:1 | 14.40:1 | ≥7 |
| `--text / --bubble-me` | 13.63:1 | 12.58:1 | ≥7 |
| `--text / --field` | 16.58:1 | 14.01:1 | ≥7 |
| `--text-2 / --bg` | 8.34:1 | 9.22:1 | ≥4.5 |
| `--text-2 / --panel` | 9.10:1 | 8.65:1 | ≥4.5 |
| `--text-2 / --surface-2` | 6.72:1 | 7.40:1 | ≥4.5 |
| `--text-3 / --bg` | 7.12:1 | 7.00:1 | ≥4.5 |
| `--text-3 / --panel` | 7.77:1 | 6.57:1 | ≥4.5 |
| `--text-3 / --surface` | 6.46:1 | 6.14:1 | ≥4.5 |
| `--text-3 / --surface-2` | 5.74:1 | 5.62:1 | ≥4.5 |
| `--text-3 / --field` | 7.77:1 | 6.17:1 | ≥4.5 |
| `--icon / --bg` | 15.04:1 | 15.43:1 | ≥4.5 |
| `--icon / --panel` | 16.40:1 | 14.48:1 | ≥4.5 |
| `--icon-accent / --surface-2` | 4.83:1 | 7.19:1 | ≥4.5 |
| `--accent-ink / --accent` | 6.54:1 | 8.03:1 | ≥4.5 |
| `--accent / --accent-soft` | 5.43:1 | 6.95:1 | ≥4.5 |
| `--crisis / --crisis-soft` | 5.40:1 | 6.29:1 | ≥4.5 |
| `--toast-text / --toast-bg` | 14.62:1 | 15.04:1 | ≥4.5 |
| `--line-strong / --bg`（边界） | 4.10:1 | 4.22:1 | ≥3 |

- **最小正文 12.58:1**（暖炭黑 `--text / --bubble-me`）
- 最小小字 5.40:1（暖纸 `--crisis / --crisis-soft`）
- 最小图标 4.83:1（暖纸 `--icon-accent / --surface-2`）
  这组数字由 `node --test tests/appearance.test.js` 直接打印，改色就会立刻失败。

另有两条暗色专属修正：
1. `body.theme-dark` 加 `color-scheme: dark`，原生下拉、日期选择器、滚动条跟随暗色，不再露出白底。
2. `.field input/select:disabled` 显式给 `--text-2` + `--surface` + 更轻描边；离线模式下的字段不再被 UA 画成淡灰。

## 六、空态、加载与错误

- **首屏舞台**：`.stage-skeleton` 静态纸片剪影（一个身形 + 一行说明条），canvas 或降级卡出现即隐藏；无转圈、无 shimmer。
- **空态**：一句邀请 + 一句说明 + 三个快捷入口，全部 `--text-3`/caption 档，不与开场白抢注意力。
- **错误态**：沿用 `errors.js` 的语义分三类——读不出来（可重试）、存储满（可导出/清空）、网络（可稍后再试）；
  文案只描述"发生了什么 + 你能做什么"，不写"哎呀""糟糕"这类情绪词。

## 七、每轮自评（云端截图自查，低于 8 分就改）

| 轮次 | artifact / 本地路径 | 发现的问题 | 分数 |
|---|---|---|---|
| 1 | `tools/shots`（run 42019493509，manifest 报错但 PNG 已产出） | 空态是米色卡片，记号被 fill 成黑色色块；记忆 chips 四块挤一行；图标 emoji 与描边混用 | 6.5 |
| 2 | `tools/shots2`（run 42021369189） | 双层拱读不出；chips 仍高低不齐；记忆页两个实心按钮互相抢 | 7.0 |
| 3 | `tools/shots3`（run 42022296120） | 空态仍是"卡中卡"；设置图标两组横线像"≡" | 7.5 |
| 4 | `tools/shots4`（run 42023940141） | **暗色空态记号撑满半屏**（`--icon` 令牌重名）；禁用字段不可读 | 6.5 |
| 5 | `tools/shots5`（run 42025124082） | 令牌修好后暗色正常；气泡与纸面几乎贴平 | 8.0 |
| 6 | `tools/shots6`（run 42026117540） | 气泡提亮一档、禁用字段可读；浅色 8.5 / 暗色 8.0 | **8.2** |

截图清单（14 张，390×844 手机为主）：`01-home-empty-{light,dark}`、`02-settings-{light,dark}`、
`03-memory-{light,dark}`、`04-chat-{light,dark}`、`05-desktop-chat-light`(1280×900)、
`06-large-font-empty-light`、`07-settings-bottom-{light,dark}`、`08-memory-bottom-{light,dark}`。

## 八、验证数字与资源占用

| 项目 | 结果 |
|---|---|
| `node --test --test-concurrency=1 "tests/*.test.js"` | **226 / 226 通过**（基线 222，未降） |
| `node --test tests/appearance.test.js` | **18 / 18 通过** |
| 裸 px 扫描（padding/margin/gap/font-size/line-height） | 0 处节奏性 px，剩 17 处结构尺寸 |
| 云端截图 | `ui-shots` 连续 6 次 success |

本会话资源采样（8 核 / 16GB，测试串行）：

- node 峰值 RSS **118MB**（整机 0.7%）；`--test-concurrency=1` → 任一时刻最多 1 个测试子进程 × 1 核 ≈ 整机 12.5%
- GPU **0**：本会话没有启动 headless 浏览器 / AVD / Gradle，截图全部走 GitHub Actions
- 整机内存占用采样 50.5%–56.9%（15.9GB 机型），其中包含同时段其它并行会话，不是本会话增量

## 九、没做的 / 留给下一轮

1. **静止时 rAF 归零只在极简模式成立**。默认/生动模式下 Live2D 舞台保留一个 rAF 跑呼吸与视线；
   `stage.js` 本轮禁止改（文件域外），要真正"静止 0 帧"需要在 stage 里加空闲降帧 / 定格策略。
2. **桌面宽屏右侧仍有留白**：对话列贴左、气泡宽度随内容，右侧空出一段；要更紧凑需在 `chat_pager.js` 里做列宽约束（域外）。
3. **录音入口没有**：`.inputbar[data-recording="true"]` 只有外观契约，需要 `app.js` 接线（域外）。
4. **聊天历史没有骨架屏**：`chat_pager.js` 的加载态在域外，本轮只覆盖舞台首屏。
5. **版本号仍是 `index.html` 里唯一一个文本节点**，没有和 Android `versionName` 共用常量（跨端无法共享）；升级只改一处，但两端仍需各自改。
6. **日期选择器格式随浏览器 locale**：截图里显示 `MM/DD/YYYY, hh:mm AM`，中文系统才是 `YYYY/MM/DD`；原生控件不可控，要统一得自己写选择器。
