/* ============================================================
 * pet_core.js · 桌面宠物悬浮窗的纯逻辑（可单测，不碰 DOM/原生）
 * ------------------------------------------------------------
 * 被 pet.js（悬浮窗内的 Live2D 宠物页）引用：
 *  - gesture：按下/抬起位移与时长 → tap / long / drag
 *  - isDoubleTap：两次点击间隔 → 双击判定
 *  - appearance：外观预设 → 宠物开关（零动画/大字号/灰阶）
 *  - reminderDecision / recordNotice：提醒节流（全局冷却 + 单条冷却 + 每小时上限）
 *  - suppressByPresence：主 App 在前台时让主程序负责提醒，避免双弹
 *  - clampPosition / bubbleLayout：拖动边界与气泡卡片定位（像素）
 *  - clipRect：桌宠画布的取景 clip（可见区 + 抗锯齿余量）
 *  - interactionFor：点头部/身体 → 表情、动作与一句短话
 * 所有函数无副作用、不读写 localStorage（持久化由调用方负责）。
 * ============================================================ */
(function (root) {
  "use strict";

  const KEYS = Object.freeze({
    notice: "xiaoman_pet_notice_v1",
    presence: "xiaoman_app_visible_v1",
  });

  const TAP_SLOP_PX = 12;
  const DOUBLE_MS = 380;
  const LONG_MS = 620;

  const NOTICE = Object.freeze({
    globalCooldownMs: 120000,     // 任意两条提醒气泡至少间隔 2 分钟
    perIdCooldownMs: 10 * 60000,  // 同一条提醒 10 分钟内不重复
    hourlyMax: 3,               // 每小时最多 3 条气泡（防骚扰）
    hourMs: 3600000,
  });

  function num(v, d) {
    const n = Number(v);
    return Number.isFinite(n) ? n : (d || 0);
  }

  /** 外观预设 → 宠物需要的三个开关（缺省安全值：零动画、标准字号、灰阶） */
  function appearance(prefs) {
    const p = prefs || {};
    return {
      motion: p.motion === true,
      largeFont: p.font === "large" || p.font === "xlarge",
      grayIcons: p.iconsGray !== false,
      flat: p.flat === true,
    };
  }

  /** 指针手势分类：位移超阈值 = drag；按住超时长 = long；否则 tap */
  function gesture(dx, dy, ms, cfg) {
    const c = cfg || {};
    const slop = num(c.slopPx, TAP_SLOP_PX);
    const longMs = num(c.longMs, LONG_MS);
    if (Math.abs(num(dx)) > slop || Math.abs(num(dy)) > slop) return "drag";
    if (num(ms) >= longMs) return "long";
    return "tap";
  }

  function isDoubleTap(lastTapAt, now, gap) {
    const last = num(lastTapAt);
    const n = num(now);
    if (last <= 0 || n <= last) return false;
    return n - last <= num(gap, DOUBLE_MS);
  }

  /** 主 App 最近 alive 时间戳 → 是否仍在前后台活跃（避免主程序与宠物双弹提醒） */
  function suppressByPresence(presenceTs, now, windowMs) {
    const ts = num(presenceTs);
    if (ts <= 0) return false;
    return num(now) - ts < num(windowMs, 90000) && num(now) - ts >= 0;
  }

  /**
   * 从到期待办中挑一条可播报的提醒；返回 null 表示本轮应保持安静。
   * @param list    Reminders.due() 的结果（{id,text,at,done}）
   * @param now     Date.now()
   * @param state   上次持久化状态 {lastAt, ids:{id:ts}, recent:[ts]}
   * @param opts    覆盖冷却参数（单测用）
   * @returns {id,text,at,recent}|null
   */
  function reminderDecision(list, now, state, opts) {
    const c = Object.assign({}, NOTICE, opts || {});
    const s = state || {};
    const n = num(now);
    const ids = s.ids && typeof s.ids === "object" ? s.ids : {};

    if (n - num(s.lastAt) < num(c.globalCooldownMs)) return null;

    const recent = (Array.isArray(s.recent) ? s.recent : [])
      .map((t) => num(t))
      .filter((t) => t > 0 && n - t < num(c.hourMs) && n - t >= 0);
    if (recent.length >= num(c.hourlyMax)) return null;

    const due = (Array.isArray(list) ? list : []).filter((r) => {
      if (!r || r.done || r.id == null) return false;
      if (num(r.at) > n) return false;
      const last = num(ids[r.id]);
      return n - last >= num(c.perIdCooldownMs) || last <= 0;
    });
    if (!due.length) return null;
    due.sort((a, b) => num(a.at) - num(b.at));
    const r = due[0];
    return { id: r.id, text: String(r.text == null ? "" : r.text), at: num(r.at), recent };
  }

  /** 播报后写回的新状态（清理过期 id，保留最近一小时的播报时间） */
  function recordNotice(state, decision, now, opts) {
    const c = Object.assign({}, NOTICE, opts || {});
    const s = state || {};
    const n = num(now);
    const ids = Object.assign({}, s.ids && typeof s.ids === "object" ? s.ids : {});
    if (decision && decision.id != null) ids[decision.id] = n;
    for (const k of Object.keys(ids)) {
      if (n - num(ids[k]) >= num(c.perIdCooldownMs)) delete ids[k];
    }
    const recent = (Array.isArray(s.recent) ? s.recent : [])
      .map((t) => num(t))
      .filter((t) => t > 0 && n - t < num(c.hourMs) && n - t >= 0)
      .concat(decision ? [n] : [])
      .slice(-20);
    return { lastAt: n, ids: ids, recent: recent };
  }

  /** 气泡文案：加上"提醒："前缀并截断，避免顶栏溢出错位 */
  function bubbleText(text, max) {
    const raw = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    const body = raw || "到时间啦";
    const full = `提醒：${body}`;
    const m = num(max, 46);
    return full.length > m ? `${full.slice(0, m - 1)}…` : full;
  }

  /** 窗口坐标钳制（含贴边后不越界） */
  function clampPosition(x, y, w, h, sw, sh) {
    const maxX = Math.max(0, num(sw) - num(w));
    const maxY = Math.max(0, num(sh) - num(h));
    return {
      x: Math.max(0, Math.min(maxX, Math.round(num(x)))),
      y: Math.max(0, Math.min(maxY, Math.round(num(y)))),
    };
  }

  /**
   * 取景 clip：在画布后备像素坐标里框出"窗口真正看得见的一段"，四边各留 margin。
   * 可见矩形按 floor/ceil 向外取整后再加 margin，保证 clip 完整包含可见区；
   * margin 只需覆盖抗锯齿与取整误差，越大约会为看不见的像素白付光栅成本。
   * @param box {modelW,modelH, canvasW,canvasH, stageW,stageH, canvasTop, margin}
   */
  function clipRect(box) {
    const b = box || {};
    const mw = num(b.modelW);
    const mh = num(b.modelH);
    const cw = num(b.canvasW);
    const ch = num(b.canvasH);
    const sw = num(b.stageW);
    const sh = num(b.stageH);
    if (!mw || !mh || !cw || !ch || !sw || !sh) return { x: 0, y: 0, w: mw, h: mh };
    const top = num(b.canvasTop, 8);
    const margin = Math.max(0, num(b.margin, 2));
    const k = mh / ch;                               // 后备像素 / CSS 像素
    const vw = Math.min(cw, sw);                     // 可见宽度（CSS）
    const vh = Math.min(ch, Math.max(0, sh - top));  // 可见高度（CSS）
    const left = (cw - vw) / 2;                      // 画布在 stage 内水平居中
    const x = Math.max(0, Math.floor(left * k) - margin);
    const right = Math.ceil((left + vw) * k) + margin;
    const y = 0;                                     // 画布顶边已在 stage 内，y=0 起均可见
    return {
      x: x,
      y: y,
      w: Math.min(mw - x, right - x),
      h: Math.min(mh - y, Math.ceil(vh * k) + margin),
    };
  }

  /**
   * 气泡卡片定位：优先放在宠物上方，空间不足放下方；水平居中对齐宠物并钳制。
   * @param pet    {x,y,width,height} 像素
   * @param screen {width,height} 像素
   * @param bubble {width,height} 像素
   * @param gap    与宠物的间距
   */
  function bubbleLayout(pet, screen, bubble, gap) {
    const p = pet || {};
    const s = screen || {};
    const b = bubble || {};
    const g = num(gap, 8);
    const bw = num(b.width);
    const bh = num(b.height);
    const sw = num(s.width);
    const sh = num(s.height);
    let x = num(p.x) + num(p.width) / 2 - bw / 2;
    x = Math.max(g, Math.min(sw - bw - g, x));
    let y = num(p.y) - bh - g;
    if (y < g) {
      y = Math.min(sh - bh - g, num(p.y) + num(p.height) + g);
    }
    return { x: Math.round(x), y: Math.round(Math.max(g, y)) };
  }

  const HEAD_LINES = ["诶嘿，被你发现了", "摸头会害羞的…", "今天也辛苦了", "我在呢，慢慢说"];
  const BODY_LINES = ["痒——", "想聊天就双击我", "拖我也不会跑哦", "我一直都在这里"];
  const HEAD_EMOTIONS = ["shy", "happy", "gentle", "shy"];
  const BODY_EMOTIONS = ["surprised", "happy", "gentle", "neutral"];

  /** 点击位置（宠物窗口内的相对 y 比例）→ 命中区与反应 */
  function interactionFor(yRatio, seed) {
    const r = Math.max(0, Math.min(1, num(yRatio, 0.4)));
    const s = Math.abs(num(seed, 0));
    const head = r < 0.62;
    const lines = head ? HEAD_LINES : BODY_LINES;
    const emos = head ? HEAD_EMOTIONS : BODY_EMOTIONS;
    const idx = Math.floor(s * lines.length) % lines.length;
    return {
      area: head ? "head" : "body",
      emotion: emos[idx],
      motion: head ? "Shake" : "HappyJump",
      line: lines[idx],
    };
  }

  const api = {
    KEYS, TAP_SLOP_PX, DOUBLE_MS, LONG_MS, NOTICE,
    appearance, gesture, isDoubleTap, suppressByPresence,
    reminderDecision, recordNotice, bubbleText,
    clampPosition, bubbleLayout, clipRect, interactionFor,
  };

  root.PetCore = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
