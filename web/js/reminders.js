/* ============================================================
 * reminders.js · 对话式定时提醒
 * ------------------------------------------------------------
 * 用户直接跟小满说"明早八点叫我吃药"→（模型或规则解析）→
 * 入库 → 每分钟 tick 检查到期 → 到期交给 heartbeat 通道送出
 * 存储：localStorage；触发：前端调度（App 在前台时可靠；
 * Android AlarmManager 硬闹钟桥在迭代清单 P1）
 * ============================================================ */

const Reminders = (() => {
  const KEY = "xiaoman_reminders_v1";

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; }
  }
  function save(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list)); }
    catch (e) {
      // 配额满/隐私模式：提醒会丢，必须让 UI 提示用户（而不是静默失败）
      try { if (typeof window !== "undefined" && window.__storageFull) window.__storageFull("提醒"); } catch (e2) {}
    }
  }
  function newId() {
    return "r" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  /**
   * 自然语言时间解析（中文场景常用格式）
   * 支持：HH:MM / 明天HH:MM / 后天HH:MM / X月X日 HH:MM / X点[半/刻/X分] / 周X HH:MM
   * 返回 Date | null
   */
  function parseTime(str) {
    if (!str) return null;
    str = str.trim();
    const now = new Date();
    let y = now.getFullYear(), mo = now.getMonth(), d = now.getDate();
    let hh = null, mi = 0;
    let explicitDay = false;   // 显式日词（今天/明天/周X/X月X日/YYYY-MM-DD）：已过也不顺延到明天
    let sameWeekday = false;   // 周X 恰为今天：时刻已过则顺延到下周同一天
    let noYear = false;        // X月X日 无年份：已过则补到明年

    // ISO / 标准格式 YYYY-MM-DD [HH:MM]
    let m = str.match(/(\d{4})-(\d{1,2})-(\d{1,2})[ T]?(\d{1,2})?:?(\d{2})?/);
    if (m) { y=+m[1]; mo=+m[2]-1; d=+m[3]; hh=m[4]!=null?+m[4]:9; mi=m[5]?+m[5]:0; return _mk(y,mo,d,hh,mi,true); }

    // 相对日：今天/今晚/明天/后天/大后天（与周X 互斥，避免偏移叠加）
    let relMatched = false;
    m = str.match(/(今天|今晚|明天|后天|大后天)/);
    if (m) {
      d += {"今天":0,"今晚":0,"明天":1,"后天":2,"大后天":3}[m[1]];
      relMatched = true; explicitDay = true;
    }
    // 周X：仅在没有相对日词时生效；同日取当天，时刻已过则顺延到下周同一天
    m = str.match(/(?:周|星期)([一二三四五六日天])/);
    if (m && !relMatched) {
      const wi = "一二三四五六日天".indexOf(m[1]);
      const target = wi === 6 ? 0 : wi + 1;
      const offset = (target - now.getDay() + 7) % 7;
      d += offset;
      explicitDay = true; sameWeekday = offset === 0;
    }
    // X月X日（无年份）
    m = str.match(/(\d{1,2})月(\d{1,2})[日号]/);
    if (m) { mo = +m[1]-1; d = +m[2]; explicitDay = true; noYear = true; }
    // 时分：支持中文数字（八点/十点半/九点一刻）与阿拉伯（9:30/19点05）
    const cn = { "一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9 };
    const cnNum = (s) => {
      if (s == null) return null;
      if (/^\d+$/.test(s)) return +s;
      if (s.includes("十")) {
        const [a, b] = s.split("十");
        return (a ? cn[a] : 1) * 10 + (b ? (cn[b] || 0) : 0);
      }
      return cn[s] ?? null;
    };
    m = str.match(/(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*([一二两三四五六七八九十]|\d{1,2})[点:：时](半|一刻|([一二两三四五六七八九]|\d{1,2})分?)?/);
    if (m) {
      hh = cnNum(m[2]);
      if (m[3] === "半") mi = 30;
      else if (m[3] === "一刻") mi = 15;
      else if (m[4]) mi = cnNum(m[4]);
      const period = m[1];
      if (period === "下午" || period === "晚上" || period === "傍晚") { if (hh < 12) hh += 12; }
      if (period === "中午" && hh < 12) hh = 12;
      if (period === "早上" || period === "上午") { if (hh === 12) hh = 0; }
    } else {
      m = str.match(/(\d{1,2}):(\d{2})/);
      if (m) { hh = +m[1]; mi = +m[2]; }
    }
    if (hh == null) return null;
    const t = _mk(y, mo, d, hh, mi, explicitDay);
    // 周X 恰为今天且时刻已过 → 顺延到下周同一天（而不是留在过去立即触发）
    if (sameWeekday && t.getTime() < Date.now()) t.setDate(t.getDate() + 7);
    // 无年份的 X月X日 已过 → 补到明年（跨年场景）
    if (noYear && t.getTime() < Date.now()) t.setFullYear(t.getFullYear() + 1);
    return t;
  }

  function _mk(y, mo, d, hh, mi, explicitDay) {
    const t = new Date(y, mo, d, hh, mi, 0);
    // 仅"裸时刻"（无显式日词）且已过 → 指明天（如现在 9 点说"8点"→ 明天 8 点）
    if (!explicitDay && t.getTime() < Date.now()) t.setDate(t.getDate() + 1);
    return t;
  }

  /** 新增提醒 {text, time(Date|str)} */
  function add(text, time) {
    const when = time instanceof Date ? time : parseTime(time);
    if (!when || !when.getTime || !text) return null;
    const list = load();
    const item = {
      id: newId(), text: String(text).slice(0, 80),
      at: when.getTime(), done: false, createdAt: Date.now()
    };
    list.push(item); save(list);
    return item;
  }

  /** 编辑提醒（只允许改文案与时间；已完成的不改） */
  function update(id, patch) {
    const p = patch || {};
    const list = load();
    const item = list.find(r => r.id === id && !r.done);
    if (!item) return null;
    if ("text" in p) {
      const text = String(p.text || "").trim().slice(0, 80);
      if (!text) return null;
      item.text = text;
    }
    if ("at" in p) {
      const when = p.at instanceof Date ? p.at : parseTime(p.at);
      if (!when) return null;
      item.at = when.getTime();
    }
    item.updatedAt = Date.now();
    save(list);
    return item;
  }

  /** 到期检查：返回到期待送列表（不自动标记 done，由投递方成功后调用 markDone） */
  function due() {
    const now = Date.now();
    const list = load();
    return list.filter(r => !r.done && r.at <= now);
  }

  /** 标记指定 id 的提醒为已完成（记录触发时间，供"已完成"历史回看） */
  function markDone(ids) {
    if (!ids || !ids.length) return;
    const list = load();
    let changed = false;
    const now = Date.now();
    for (const r of list) {
      if (ids.includes(r.id) && !r.done) {
        r.done = true;
        r.doneAt = now;
        changed = true;
      }
    }
    if (changed) save(list);
  }

  /** 即将到来的提醒（按时间升序） */
  function pending() { return load().filter(r => !r.done).sort((a, b) => a.at - b.at); }
  /** 已触发/已完成提醒（最近触发优先）；n 为上限（默认 10） */
  function history(n) {
    const len = n || 10;
    return load()
      .filter(r => r.done)
      .sort((a, b) => (b.doneAt || b.at) - (a.doneAt || a.at))
      .slice(0, len);
  }
  function clearDone() { save(load().filter(r => !r.done)); }
  function cancel(id) { save(load().filter(r => r.id !== id)); }
  function all() { return load().sort((a, b) => a.at - b.at); }

  /** 单条整理：时间戳/文案/完成态白名单化（备份导入用） */
  function normalize(r) {
    if (!r || typeof r !== "object") return null;
    const text = String(r.text == null ? "" : r.text).trim().slice(0, 80);
    const at = Number(r.at);
    if (!text || !Number.isFinite(at) || at <= 0) return null;
    return {
      id: String(r.id || newId()),
      text,
      at,
      done: !!r.done,
      doneAt: Number(r.doneAt) || 0,
      createdAt: Number(r.createdAt) || 0,
      updatedAt: Number(r.updatedAt) || 0,
    };
  }

  /**
   * 合并导入（按 id 去重，其次按 文案+时刻 去重）：
   * 同一提醒两边都有时保留 updatedAt/doneAt 更新的那份，不制造重复提醒。
   */
  function merge(items) {
    const list = load();
    const byId = new Map(list.map(r => [r.id, r]));
    const byBody = new Map(list.map(r => [`${r.text}\u0000${r.at}`, r]));
    let added = 0, updated = 0;
    for (const raw of (items || [])) {
      const r = normalize(raw);
      if (!r) continue;
      const old = byId.get(r.id) || byBody.get(`${r.text}\u0000${r.at}`);
      if (old) {
        const newer = (r.updatedAt || 0) >= (old.updatedAt || 0) && (r.doneAt || 0) >= (old.doneAt || 0);
        if (newer) {
          Object.assign(old, { text: r.text, at: r.at, done: r.done, doneAt: r.doneAt, updatedAt: r.updatedAt });
          updated++;
        }
        continue;
      }
      list.push(r);
      byId.set(r.id, r);
      byBody.set(`${r.text}\u0000${r.at}`, r);
      added++;
    }
    if (added || updated) save(list);
    return { added, updated, total: list.length };
  }

  /** 中文友好显示 */
  function fmt(ts) {
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, "0");
    const sameDay = d.toDateString() === new Date().toDateString();
    const tomorrow = d.toDateString() === new Date(Date.now() + 86400000).toDateString();
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (sameDay) return `今天 ${hm}`;
    if (tomorrow) return `明天 ${hm}`;
    return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  }

  /** 历史回看用的完整时间（含年份，跨年不歧义） */
  function fmtFull(ts) {
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  return { add, update, due, pending, history, clearDone, cancel, all, merge, normalize, parseTime, fmt, fmtFull, markDone, KEY };
})();
if (typeof module === "object" && module.exports) module.exports = Reminders;   // node 单测用
window.Reminders = Reminders;   // const 不挂 window，跨模块守卫需显式挂载
