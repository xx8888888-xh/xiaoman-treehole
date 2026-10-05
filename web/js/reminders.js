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
  function save(list) { localStorage.setItem(KEY, JSON.stringify(list)); }

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

    // ISO / 标准格式 YYYY-MM-DD [HH:MM]
    let m = str.match(/(\d{4})-(\d{1,2})-(\d{1,2})[ T]?(\d{1,2})?:?(\d{2})?/);
    if (m) { y=+m[1]; mo=+m[2]-1; d=+m[3]; hh=m[4]!=null?+m[4]:9; mi=m[5]?+m[5]:0; return _mk(y,mo,d,hh,mi,str); }

    // 相对日：今天/明天/后天/大后天
    m = str.match(/(今天|今晚|明天|后天|大后天)/);
    if (m) {
      const delta = {"今天":0,"今晚":0,"明天":1,"后天":2,"大后天":3}[m[1]];
      d += delta;
    }
    // 周X
    m = str.match(/(?:周|星期)([一二三四五六日天])/);
    if (m) {
      const target = "一二三四五六日天".indexOf(m[1]) === 6 ? 0 : "一二三四五六日天".indexOf(m[1]) + 1;
      const cur = now.getDay();
      d += (target - cur + 7) % 7 || 7;
    }
    // X月X日
    m = str.match(/(\d{1,2})月(\d{1,2})[日号]/);
    if (m) { mo = +m[1]-1; d = +m[2]; }
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
    return _mk(y, mo, d, hh, mi, str);
  }

  function _mk(y, mo, d, hh, mi, raw) {
    const t = new Date(y, mo, d, hh, mi, 0);
    // 已过时间的"明天"语义兜底（说"8点"而现在是9点→指明天）
    if (t.getTime() < Date.now() && !/明天|后天|周|星期|\d{4}-/.test(raw)) {
      t.setDate(t.getDate() + 1);
    }
    return t;
  }

  /** 新增提醒 {text, time(Date|str)} */
  function add(text, time) {
    const when = time instanceof Date ? time : parseTime(time);
    if (!when || !text) return null;
    const list = load();
    const item = {
      id: "r" + Date.now(), text: String(text).slice(0, 80),
      at: when.getTime(), done: false
    };
    list.push(item); save(list);
    return item;
  }

  /** 到期检查：返回到期待送列表并标记 done */
  function due() {
    const now = Date.now();
    const list = load();
    const fired = list.filter(r => !r.done && r.at <= now);
    if (fired.length) {
      for (const r of fired) r.done = true;
      save(list);
    }
    return fired;
  }

  function pending() { return load().filter(r => !r.done); }
  function cancel(id) { save(load().filter(r => r.id !== id)); }
  function all() { return load().sort((a, b) => a.at - b.at); }

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

  return { add, due, pending, cancel, all, parseTime, fmt };
})();
window.Reminders = Reminders;   // const 不挂 window，跨模块守卫需显式挂载
