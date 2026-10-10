/* ============================================================
 * app.js · 对话编排与"活人感"引擎
 * ------------------------------------------------------------
 * 活人感五件套（来自前期实验验证，见 docs）：
 *  1. 分条发送   —— 长回复按 || 切成 2~3 条，像微信连发
 *  2. 真实延迟   —— "正在输入"气泡 + 与字数相关的延迟
 *  3. 记忆系统   —— memory_updates 存 localStorage，抽屉可视化
 *  4. 主动开口   —— 空闲 60s/150s 各轻声问候一次，之后闭嘴不烦人
 *  5. 留存钩子   —— 末尾钩子以气泡芯片呈现，点击即发
 * 安全层：客户端正则先行拦截危机信号 → 关怀卡片 + 12356 热线
 * ============================================================ */

const App = (() => {
  const MEM_KEY = "xiaoman_memory";
  // 危机词表：与 mock_engine.js / mock_api.py 保持同一集合（安全网口径一致）
  const CRISIS_RE = /(不想活|想死|活不下去|自杀|自残|了结|伤害自己|撑不下去|活着.*没意思|没有意思.*活|没有意义.*活|跳楼|结束自己|煤气|遗书|不想醒来|去死|寻短见|解脱|不想活着|活着没劲|死了算了)/;
  const CARE_SCRIPT = "……这个我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。先陪我聊一会儿，好吗？我也想让你和更专业的人聊聊——";

  let history = [];          // [{role, content, at}]
  let busy = false;
  let sending = false;       // 发送互斥锁：防止 greet/心跳/poke/hook 与用户发送并发
  let sendQueue = [];        // 等待发送的消息队列
  let lockDepth = 0;         // 发送锁重入深度（同一调用链嵌套获取时直接放行，避免自锁）
  let idleTimers = [];
  let pings = 0;
  const MAX_HISTORY = 30;    // 历史滑动窗口上限

  // ---------- 工具 ----------
  const $ = id => document.getElementById(id);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  function loadMem() { try { return JSON.parse(localStorage.getItem(MEM_KEY)) || {}; } catch (e) { return {}; } }
  function saveMem(m) { localStorage.setItem(MEM_KEY, JSON.stringify(m)); renderMem(); }
  /** 泛指宠物值判定（与 mock_api.py/_pet_generic、mock_engine.js/petGeneric 三端同源）：
   *  "那只猫/我家猫/一只狗"→true（零信息泛指）；"橘猫/英短猫"→false（具体值）。
   *  P0-2 遗留守卫：防止泛指覆盖具体导致记忆退化。暴露 window 供 E2E 单测 */
  function genericPet(v) {
    const core = String(v).replace(/[一这那每某该个小条只家我有]/g, "");
    return /^(?:猫|狗|兔子?)$/.test(core);
  }
  window.__genericPet = genericPet;
  function timeBand() {
    const h = new Date().getHours();
    if (h < 5) return "凌晨";
    if (h < 9) return "清晨";
    if (h < 12) return "上午";
    if (h < 14) return "中午";
    if (h < 18) return "下午";
    if (h < 23) return "晚上";
    return "深夜";
  }
  function toast(msg, ms = 2200) {
    const t = $("toast");
    t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
  }

  // ---------- 渲染 ----------
  function addMsg(text, who, opts = {}) {
    const row = document.createElement("div");
    row.className = `msg-row ${who}`;
    if (opts.crisis) {
      const card = document.createElement("div");
      card.className = "care-card";
      card.textContent = text;
      const hotline = document.createElement("div");
      hotline.className = "hotline";
      const pulse = document.createElement("span");
      pulse.className = "pulse";
      hotline.appendChild(pulse);
      hotline.append("全国心理援助热线 12356 · 24小时");
      card.appendChild(hotline);
      row.appendChild(card);
    } else if (opts.tip) {
      const bubble = document.createElement("div");
      bubble.className = "bubble system-tip";
      bubble.textContent = text;
      row.appendChild(bubble);
    } else {
      if (who === "them") {
        const av = document.createElement("div");
        av.className = "avatar-mini";
        av.textContent = "满";
        row.appendChild(av);
      }
      const bubble = document.createElement("div");
      bubble.className = `bubble ${who}`;
      bubble.textContent = text;
      if (opts.voice) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = "🔊 语音已播";
        bubble.appendChild(meta);
      }
      row.appendChild(bubble);
    }
    $("typingRow").before(row);
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    return row;
  }

  function renderMem() {
    const mem = loadMem();
    const box = $("memoryChips");
    box.innerHTML = "";
    const entries = Object.entries(mem);
    if (!entries.length) {
      const empty = document.createElement("span");
      empty.className = "chip empty";
      empty.textContent = "还没有，聊着聊着就有了";
      box.appendChild(empty);
    } else {
      for (const [k, v] of entries) {
        const c = document.createElement("span");
        c.className = "chip";
        const b = document.createElement("b");
        b.textContent = k;
        c.appendChild(b);
        c.append(" " + v);
        box.appendChild(c);
      }
    }
    // 提醒清单（可取消）
    if (window.Reminders) {
      const rbox = $("reminderList");
      const pend = Reminders.pending();
      rbox.innerHTML = "";
      if (pend.length) {
        const sub = document.createElement("p");
        sub.className = "drawer-sub";
        sub.style.marginTop = "10px";
        sub.textContent = "定好的提醒";
        rbox.appendChild(sub);
        for (const r of pend) {
          const div = document.createElement("div");
          div.className = "chip row";
          const span = document.createElement("span");
          span.textContent = `⏰ ${Reminders.fmt(r.at)} · ${r.text}`;
          const btn = document.createElement("button");
          btn.dataset.rid = r.id;
          btn.className = "r-cancel";
          btn.setAttribute("aria-label", "取消提醒");
          btn.textContent = "✕";
          btn.onclick = () => { Reminders.cancel(btn.dataset.rid); renderMem(); toast("提醒取消了"); };
          div.appendChild(span);
          div.appendChild(btn);
          rbox.appendChild(div);
        }
      }
    }
  }

  // ---------- 打字延迟（活人感核心之一） ----------
  async function showTyping(ms) {
    $("typingRow").classList.remove("hidden");
    $("chatScroll").scrollTop = $("chatScroll").scrollHeight;
    await sleep(ms);
    $("typingRow").classList.add("hidden");
  }
  const typingDelay = text => Math.min(2800, 550 + text.length * 42);

  // 提醒确认语（朋友口吻，不机械）
  function pickConfirm(item) {
    const T = Reminders.fmt(item.at);
    const C = item.text;
    const pools = [
      `好，${T}我喊你${C}，赖床就连环call`,
      `记上了：${T} 提醒你${C}||到点我找你，不许装死`,
      `行，${T}叫你${C}。团子作证`,
    ];
    return pools[Math.floor(Math.random() * pools.length)];
  }

  // ---------- 发送互斥锁（可重入） ----------
  async function withSendLock(fn) {
    // 最外层调用：等待当前发送完成并占锁；嵌套调用（同一调用链）直接放行
    if (lockDepth === 0) {
      while (sending) await sleep(50);
      sending = true;
    }
    lockDepth++;
    try {
      return await fn();
    } finally {
      lockDepth--;
      if (lockDepth === 0) {
        sending = false;
        // 处理队列中等待的消息
        if (sendQueue.length) {
          const next = sendQueue.shift();
          next();
        }
      }
    }
  }

  // ---------- 发送主流程 ----------
  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("sendBtn").disabled = true;
    resetIdle();

    await withSendLock(async () => {
      addMsg(text, "me");
      history.push({ role: "user", content: text, at: Date.now() });
      // 滑动窗口：保留最近 MAX_HISTORY 条
      if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);

      // 危机拦截（客户端先行，服务端还有一道）
      if (CRISIS_RE.test(text)) {
        await showTyping(900);
        addMsg(CARE_SCRIPT, "them", { crisis: true });
        Stage.setEmotion("gentle");
        history.push({ role: "assistant", content: CARE_SCRIPT, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
        return;
      }

      // 定时提醒（客户端优先截获：可靠+离线可用，模型协议路径作为补充）
      // 先提取时间词之前的内容作为提醒文案，避免贪婪匹配丢失内容
      const remindCap = text.match(/(?:提醒我|叫我|记得让我|别忘了让我)\s*(.+)/);
      if (remindCap) {
        const when = Reminders.parseTime(text);
        if (when) {
          // 只取触发词之后的部分作为提醒内容（中文无 \b 词边界，直接替换时间/语气词）
          let content = remindCap[1] || "";
          content = content.replace(/(凌晨|早上|上午|中午|下午|傍晚|晚上|深夜)?\s*(\d{1,2}[点:：时](半|一刻|\d{1,2}分?)?|\d{1,2}:\d{2})/g, " ");
          content = content.replace(/(今天|今晚|明天|后天|大后天|周[一二三四五六日天]|星期[一二三四五六日天]|\d{1,2}月\d{1,2}[日号])/g, " ");
          content = content.replace(/(一下|哈|吧|呢|嘛|啦)/g, " ").replace(/\s+/g, " ").trim();
          const item = Reminders.add(content || "到时候提醒你", when);
          if (item) {
            const conf = pickConfirm(item);
            await showTyping(700);
            addMsg(conf, "them", { tip: true });
            history.push({ role: "assistant", content: conf, at: Date.now() });
            if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
            toast(`已设提醒：${Reminders.fmt(item.at)}`);
            return;
          }
        }
      }

      try {
        await showTyping(650 + Math.random() * 500);   // 读消息的停顿
        const data = await API.chat(history, {
          memSection: window.MemoryStore ? MemoryStore.renderContext(text) : "",
          now: new Date()
        });
        await sendSplit(data, text);
      } catch (e) {
        console.error(e);
        await showTyping(800);
        addMsg("（信号飘走了…你还在吗？再发一次试试）", "them", { tip: false });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
      }
    });
    busy = false; $("sendBtn").disabled = false;
    armIdle();
  }

  /** 分条发送 + 表情动作 + 语音 + 钩子（带发送锁） */
  async function sendSplit(data, userText) {
    await withSendLock(async () => {
      // 支持模型返回的 crisis 字段（Defect 13）
      if (data.crisis) {
        await showTyping(900);
        addMsg(CARE_SCRIPT, "them", { crisis: true });
        Stage.setEmotion("gentle");
        history.push({ role: "assistant", content: CARE_SCRIPT, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
        return;
      }

      const parts = String(data.reply || "…").split("||").map(s => s.trim()).filter(Boolean);
      // 段数上限：防止模型输出过多段导致刷屏
      const limitedParts = parts.slice(0, 5);

      let lastRow = null;
      for (let i = 0; i < limitedParts.length; i++) {
        const seg = limitedParts[i];
        await showTyping(typingDelay(seg));
        lastRow = addMsg(seg, "them");
        history.push({ role: "assistant", content: seg, at: Date.now() });
        if (history.length > MAX_HISTORY) history = history.slice(-MAX_HISTORY);
      }

      // 表情 + 动作
      if (data.emotion) Stage.setEmotion(data.emotion);
      if (data.motion && data.motion !== "null") Stage.playMotion(data.motion);

      // 记忆：双写（旧抽屉 + 新索引库）
      if (data.memory_updates && Object.keys(data.memory_updates).length) {
        // 泛指不覆盖具体（P0-2 遗留守卫·前端兜底，三端同源判定）：
        // 服务端已挡在线路径；这里收敛其余来源（离线引擎/未来协议漂移）
        const upd = { ...data.memory_updates };
        const cur = loadMem();
        if (upd["宠物"] && cur["宠物"] && genericPet(upd["宠物"]) && !genericPet(cur["宠物"])) {
          delete upd["宠物"];
        }
        if (Object.keys(upd).length) {
          saveMem({ ...cur, ...upd });
          if (window.MemoryStore) MemoryStore.addUpdates(upd);
        }
      }

      // 模型路径的提醒协议（补充：客户端没截获但模型识别到了）
      if (Array.isArray(data.reminders) && data.reminders.length) {
        for (const r of data.reminders) {
          const item = Reminders.add(r.text, r.time);
          if (item) toast(`已设提醒：${Reminders.fmt(item.at)}`);
        }
        renderMem();
      }

      // 语音（只念最后一条短消息，避免长篇朗读）
      if (limitedParts.length) {
        const spoken = limitedParts[limitedParts.length - 1].replace(/[\uFF0C。！？~…]+$/g, "");
        TTS.speak(spoken, Stage.lipFrame.bind(Stage), () => {
          // 播放完成后在最后一条气泡标记语音已播（用 addMsg 返回的行引用，避免命中 typingRow）
          const bubble = lastRow && lastRow.querySelector(".bubble");
          if (bubble && !bubble.querySelector(".meta")) {
            const meta = document.createElement("span");
            meta.className = "meta";
            meta.textContent = "🔊 语音已播";
            bubble.appendChild(meta);
          }
        });
      }

      // 留存钩子
      if (data.hook) showHook(data.hook);
    });
  }

  function showHook(text) {
    const bar = $("hookBar");
    bar.innerHTML = "";
    const chip = document.createElement("span");
    chip.className = "hook-chip";
    chip.textContent = text;   // textContent 本身即安全，再 escapeHtml 会显示成字面实体
    chip.onclick = () => { bar.classList.add("hidden"); send(text); };
    bar.appendChild(chip);
    bar.classList.remove("hidden");
  }

  // ---------- 主动开口（克制版） ----------
  function armIdle() {
    resetIdle();
    if (pings >= 2) return;
    idleTimers.push(setTimeout(async () => {
      if (busy) { armIdle(); return; }   // 用户正在聊：让位，稍后再试（不吞掉这次空闲问候）
      pings++;
      const band = timeBand();
      const lines = {
        凌晨: "还没睡呀？……我陪你待一会儿||睡不着是因为有事，还是单纯刷手机停不下来",
        深夜: "咦，这个点还来，今天过得怎么样呀",
        清晨: "早呀！你居然起得比我还早",
        中午: "吃午饭了没？别又糊弄一顿",
        下午: "忙什么呢，休息一下眼睛嘛",
        晚上: "今天有什么想说的吗，我在呢"
      };
      const text = lines[band] || "在忙吗？我在呢";
      try {
        await sendSplit({ reply: text, emotion: "gentle", motion: "Greeting" });
      } catch (e) {
        console.warn("空闲问候失败:", e.message);
      }
      armIdle();   // 排下一次（pings>=2 时自然停止），恢复"60s/150s 各一次"的节奏
    }, pings === 0 ? 60000 : 150000));
  }
  function resetIdle() { idleTimers.forEach(clearTimeout); idleTimers = []; }

  // ---------- 摸头/戳身体反应 ----------
  const POKE_LINES = {
    head: ["干嘛摸我头啦……头发要乱了||……不过，再摸一下也行", "喂喂，把你当朋友才让你摸头的哦"],
    body: ["哇！吓我一跳||怎么突然戳我，想引起我注意就直说嘛", "痒痒痒！住手啦哈哈"]
  };
  let pokeIdx = 0, lastPoke = 0;
  function onPoke(part) {
    const now = Date.now();
    if (now - lastPoke < 2500) return;   // 防连点刷屏
    lastPoke = now;
    const lines = POKE_LINES[part];
    const text = lines[pokeIdx++ % lines.length];
    sendSplit({ reply: text, emotion: part === "head" ? "shy" : "surprised" });
  }

  // ---------- 开场 ----------
  function greet() {
    const mem = loadMem();
    const band = timeBand();
    const name = mem["昵称"] ? `${mem["昵称"]}，` : "";
    const G = {
      凌晨: `${name}这个点还没睡？||先别急着说话，我就陪着你。想聊的时候再开口`,
      深夜: `${name}来啦。今天辛苦啦||我是小满，深夜树洞营业中——今天有什么想倒出来的吗`,
      清晨: `早呀${name || "呀"}！一大早就来找我，今天有什么期待的事吗`,
      中午: `${name}中午好呀，吃饭了没`,
      下午: `${name}下午好。忙里偷闲来找我啦`,
      晚上: `${name}晚上好呀。一天过去了，有想说的吗`
    };
    let reply = G[band] || G.深夜;
    // P0-1 再见面开场引用：钉子户里的大事（近事件）优先，其次宠物
    // 不需要严格判定"隔天"——首日铺设完成前（无大事无宠物）自然不会引用
    const event = mem["大事"];
    if (event) {
      reply += `||对了，你上次说${event}——怎么样啦？我一直记着呢`;
    } else if (mem["宠物"]) {
      reply += `||还有，你家${mem["宠物"]}最近乖不乖？`;
    }
    setTimeout(() => {
      sendSplit({ reply, emotion: "gentle", motion: "Greeting" });
    }, 900);
  }

  // ---------- 抽屉/弹窗：焦点管理 + Esc 关闭 + 退出动画 ----------
  const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  let returnFocus = null;
  const panelOf = el => el.querySelector(".drawer-panel, .modal-panel") || el;
  const openPanels = () => [...document.querySelectorAll(".drawer:not(.hidden), .modal:not(.hidden)")];

  function openPanel(el, trigger) {
    returnFocus = trigger || document.activeElement;
    el.classList.remove("hidden");
    const panel = panelOf(el);
    if (!panel.hasAttribute("tabindex")) panel.setAttribute("tabindex", "-1");
    panel.focus();
  }

  function closePanel(el) {
    if (el.classList.contains("hidden")) return;
    el.classList.add("is-closing");
    // 等退出动画放完再真正隐藏（prefers-reduced-motion 下动画被压到极短，同样成立）
    setTimeout(() => {
      el.classList.add("hidden");
      el.classList.remove("is-closing");
      if (returnFocus && returnFocus.focus) returnFocus.focus();
      returnFocus = null;
    }, 200);
  }

  // Esc 关闭最上层；Tab 在面板内循环，避免焦点跑到背后的页面上
  function onKeydown(e) {
    const open = openPanels();
    if (!open.length) return;
    const panel = panelOf(open[open.length - 1]);
    if (e.key === "Escape") { e.preventDefault(); closePanel(open[open.length - 1]); return; }
    if (e.key !== "Tab") return;
    const items = [...panel.querySelectorAll(FOCUSABLE)].filter(x => !x.disabled && x.offsetParent !== null);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (document.activeElement === panel || !panel.contains(document.activeElement)) {
      e.preventDefault(); (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  // ---------- 绑定 ----------
  function bind() {
    // 统一提交入口：busy 时不吞字（保留输入并提示），避开 IME 合成态误发送
    function submitText() {
      const input = $("textInput");
      const v = input.value;
      if (!v.trim()) return;
      if (busy) { toast("等我把这句说完嘛"); return; }
      input.value = "";
      send(v);
    }
    $("sendBtn").onclick = submitText;
    $("textInput").addEventListener("keydown", e => {
      // isComposing / keyCode 229：中文输入法候选确认回车，不算发送
      if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) submitText();
    });
    $("micBtn").onclick = () => toast("语音输入接入中，先打字撩她吧");
    $("memoryBtn").onclick = () => { renderMem(); openPanel($("memoryDrawer"), $("memoryBtn")); };
    $("memoryClose").onclick = () => closePanel($("memoryDrawer"));
    $("memoryDrawer").querySelector(".drawer-mask").onclick = () => closePanel($("memoryDrawer"));
    $("memoryClear").onclick = () => { localStorage.removeItem(MEM_KEY); if (window.MemoryStore) MemoryStore.clear(); renderMem(); toast("小满失忆了，重新认识一下吧"); };
    $("settingsBtn").onclick = () => {
      const cfg = API.loadCfg();
      $("cfgApi").value = cfg.apiBase || "http://127.0.0.1:8902";
      $("cfgTts").value = cfg.tts || "server";
      $("cfgVoice").value = cfg.voice || "zh-CN-XiaoyiNeural";
      openPanel($("settingsModal"), $("settingsBtn"));
    };
    $("settingsClose").onclick = () => closePanel($("settingsModal"));
    $("settingsModal").querySelector(".drawer-mask").onclick = () => closePanel($("settingsModal"));
    [$("cfgApi"), $("cfgTts"), $("cfgVoice")].forEach(el => el.addEventListener("change", () => {
      API.saveCfg({ apiBase: $("cfgApi").value.trim(), tts: $("cfgTts").value, voice: $("cfgVoice").value });
      toast("已保存");
    }));
    // 抽屉/弹窗：Esc 关闭 + Tab 焦点循环
    document.addEventListener("keydown", onKeydown);
    // 舞台键盘等价操作：摸头（Live2D 命中区只能点击，键盘走同一反应）
    $("stage").addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") { e.preventDefault(); onPoke("head"); }
    });
  }

  function init() {
    // 事件绑定独立兜底：即便舞台/心跳初始化失败，输入与发送也必须可用
    try {
      bind(); console.log("[probe] bind ok");
    } catch (e) { console.error("[probe] BIND_FAIL", e.message); return; }

    // 舞台初始化独立兜底：PIXI/模型失败只降级隐藏舞台，不影响对话
    try {
      Stage.mount($("stage")); console.log("[probe] mount ok");
    } catch (e) { console.warn("[probe] STAGE_FAIL", e.message); }

    try { greet(); console.log("[probe] greet ok"); } catch (e) { console.warn("[probe] GREET_FAIL", e.message); }
    armIdle();

    // 心跳系统：提醒投递 + 朋友式主动问候（多重频控，不打扰）
    if (window.Heartbeat && window.Reminders) {
      try {
        Heartbeat.start(async (data) => {
          if (busy) {
            // 提醒不能被 busy 静默丢弃：抛错让心跳保留待下轮重投；闲聊则直接跳过
            if (data && data.kind === "reminder") throw new Error("busy: 提醒稍后重投");
            return;
          }
          await sendSplit(data, null);
          renderMem();
        }, n => history.slice(-n));
        console.log("[probe] heartbeat ok");
      } catch (e) { console.warn("[probe] HEARTBEAT_FAIL", e.message); }
    }
  }

  return { init, send, onPoke };
})();

window.addEventListener("DOMContentLoaded", App.init);
