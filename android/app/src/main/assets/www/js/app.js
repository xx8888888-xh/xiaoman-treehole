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
  const CRISIS_RE = /(不想活|想死|活不下去|了结|自杀|自残|伤害自己|没有意义.*活|活着.*没意思|撑不下去)/;
  const CARE_SCRIPT = "……这个我当真了，也想让你当真。你现在的感觉，值得被认真对待，不丢人。先陪我聊一会儿，好吗？我也想让你和更专业的人聊聊——";

  let history = [];          // [{role, content}]
  let busy = false;
  let idleTimers = [];
  let pings = 0;

  // ---------- 工具 ----------
  const $ = id => document.getElementById(id);
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  function loadMem() { try { return JSON.parse(localStorage.getItem(MEM_KEY)) || {}; } catch (e) { return {}; } }
  function saveMem(m) { localStorage.setItem(MEM_KEY, JSON.stringify(m)); renderMem(); }
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
      row.innerHTML = `<div class="care-card">${text}
        <div class="hotline"><span class="pulse"></span>全国心理援助热线 12356 · 24小时</div></div>`;
    } else if (opts.tip) {
      row.innerHTML = `<div class="bubble system-tip">${text}</div>`;
    } else {
      const av = who === "them" ? `<div class="avatar-mini">满</div>` : "";
      const meta = opts.voice ? `<span class="meta">🔊 语音已播</span>` : "";
      row.innerHTML = `${av}<div class="bubble ${who}">${text}${meta}</div>`;
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
      box.innerHTML = `<span class="chip empty">还没有，聊着聊着就有了</span>`;
    } else {
      for (const [k, v] of entries) {
        const c = document.createElement("span");
        c.className = "chip";
        c.innerHTML = `<b>${k}</b> ${v}`;
        box.appendChild(c);
      }
    }
    // 提醒清单（可取消）
    if (window.Reminders) {
      const rbox = $("reminderList");
      const pend = Reminders.pending();
      rbox.innerHTML = pend.length
        ? `<p class="drawer-sub" style="margin-top:10px">定好的提醒</p>` +
          pend.map(r => `<div class="chip" style="justify-content:space-between;width:100%">
            <span>⏰ ${Reminders.fmt(r.at)} · ${r.text}</span>
            <button data-rid="${r.id}" class="r-cancel" style="border:0;background:none;cursor:pointer;color:#e88498">✕</button></div>`).join("")
        : "";
      rbox.querySelectorAll(".r-cancel").forEach(b => {
        b.onclick = () => { Reminders.cancel(b.dataset.rid); renderMem(); toast("提醒取消了"); };
      });
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

  // ---------- 发送主流程 ----------
  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; $("sendBtn").disabled = true;
    resetIdle();

    addMsg(text, "me");
    history.push({ role: "user", content: text });

    // 危机拦截（客户端先行，服务端还有一道）
    if (CRISIS_RE.test(text)) {
      await showTyping(900);
      addMsg(CARE_SCRIPT, "them", { crisis: true });
      Stage.setEmotion("gentle");
      history.push({ role: "assistant", content: CARE_SCRIPT });
      busy = false; $("sendBtn").disabled = false;
      return;
    }

    // 定时提醒（客户端优先截获：可靠+离线可用，模型协议路径作为补充）
    const remindCap = text.match(/(?:提醒我|叫我|记得让我|别忘了让我)(.{1,50})/);
    if (remindCap) {
      const when = Reminders.parseTime(text);
      if (when) {
        let content = remindCap[1] || "";
        content = content.replace(/(\d{1,2}[点:：时](半|\d{1,2}分?)?|\d{1,2}:\d{2}|今天|今晚|明天|后天|大后天|早上|上午|中午|下午|傍晚|晚上|凌晨|周[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]|一下|哈)/g, " ").trim();
        const item = Reminders.add(content || "到时候提醒你", when);
        if (item) {
          const conf = pickConfirm(item);
          await showTyping(700);
          addMsg(conf, "them", { tip: true });
          history.push({ role: "assistant", content: conf, at: Date.now() });
          toast(`已设提醒：${Reminders.fmt(item.at)}`);
          busy = false; $("sendBtn").disabled = false;
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
    }
    busy = false; $("sendBtn").disabled = false;
    armIdle();
  }

  /** 分条发送 + 表情动作 + 语音 + 钩子 */
  async function sendSplit(data, userText) {
    const parts = String(data.reply || "…").split("||").map(s => s.trim()).filter(Boolean);

    for (let i = 0; i < parts.length; i++) {
      const seg = parts[i];
      await showTyping(typingDelay(seg));
      addMsg(seg, "them");
      history.push({ role: "assistant", content: seg, at: Date.now() });
    }

    // 表情 + 动作
    if (data.emotion) Stage.setEmotion(data.emotion);
    if (data.motion && data.motion !== "null") Stage.playMotion(data.motion);

    // 记忆：双写（旧抽屉 + 新索引库）
    if (data.memory_updates && Object.keys(data.memory_updates).length) {
      saveMem({ ...loadMem(), ...data.memory_updates });
      if (window.MemoryStore) MemoryStore.addUpdates(data.memory_updates);
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
    if (parts.length) {
      const spoken = parts[parts.length - 1].replace(/[\uFF0C。！？~…]+$/g, "");
      TTS.speak(spoken, Stage.lipFrame.bind(Stage), () => {
        const last = document.querySelector("#messages .msg-row:last-child .meta");
      });
    }

    // 留存钩子
    if (data.hook) showHook(data.hook);
  }

  function showHook(text) {
    const bar = $("hookBar");
    bar.innerHTML = "";
    const chip = document.createElement("span");
    chip.className = "hook-chip";
    chip.textContent = text;
    chip.onclick = () => { bar.classList.add("hidden"); send(text); };
    bar.appendChild(chip);
    bar.classList.remove("hidden");
  }

  // ---------- 主动开口（克制版） ----------
  function armIdle() {
    resetIdle();
    if (pings >= 2) return;
    idleTimers.push(setTimeout(() => {
      if (busy) return;
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
      sendSplit({ reply: text, emotion: "gentle", motion: "Greeting" });
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
    setTimeout(() => {
      sendSplit({ reply: G[band] || G.深夜, emotion: "gentle", motion: "Greeting" });
    }, 900);
  }

  // ---------- 绑定 ----------
  function bind() {
    $("sendBtn").onclick = () => { const v = $("textInput").value; $("textInput").value = ""; send(v); };
    $("textInput").addEventListener("keydown", e => {
      if (e.key === "Enter") { const v = $("textInput").value; $("textInput").value = ""; send(v); }
    });
    $("micBtn").onclick = () => toast("语音输入接入中，先打字撩她吧");
    $("memoryBtn").onclick = () => { renderMem(); $("memoryDrawer").classList.remove("hidden"); };
    $("memoryClose").onclick = () => $("memoryDrawer").classList.add("hidden");
    $("memoryDrawer").querySelector(".drawer-mask").onclick = () => $("memoryDrawer").classList.add("hidden");
    $("memoryClear").onclick = () => { localStorage.removeItem(MEM_KEY); renderMem(); toast("小满失忆了，重新认识一下吧"); };
    $("settingsBtn").onclick = () => {
      const cfg = API.loadCfg();
      $("cfgApi").value = cfg.apiBase || "http://127.0.0.1:8902";
      $("cfgTts").value = cfg.tts || "server";
      $("cfgVoice").value = cfg.voice || "zh-CN-XiaoyiNeural";
      $("settingsModal").classList.remove("hidden");
    };
    $("settingsClose").onclick = () => $("settingsModal").classList.add("hidden");
    $("settingsModal").querySelector(".drawer-mask").onclick = () => $("settingsModal").classList.add("hidden");
    [$("cfgApi"), $("cfgTts"), $("cfgVoice")].forEach(el => el.addEventListener("change", () => {
      API.saveCfg({ apiBase: $("cfgApi").value.trim(), tts: $("cfgTts").value, voice: $("cfgVoice").value });
      toast("已保存");
    }));
  }

  function init() {
    try {
      bind(); console.log("[probe] bind ok");
      Stage.mount($("stage")); console.log("[probe] mount ok");
      greet(); console.log("[probe] greet ok");
      armIdle();
      // 心跳系统：提醒投递 + 朋友式主动问候（多重频控，不打扰）
      if (window.Heartbeat && window.Reminders) {
        Heartbeat.start(async (data) => {
          if (busy) return;
          await sendSplit(data, null);
          renderMem();
        }, n => history.slice(-n));
        console.log("[probe] heartbeat ok");
      }
    } catch (e) { console.log("[probe] INIT_FAIL", e.message, (e.stack || "").split("\n")[1]); }
  }

  return { init, send, onPoke };
})();

window.addEventListener("DOMContentLoaded", App.init);
