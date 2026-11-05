/* ============================================================
 * perf.js · 性能探针（只读埋点，不会改变应用行为）
 * ------------------------------------------------------------
 * 由云端 perf 脚本按需注入（scripts/perf_r3.py），页面本身不加载：
 *   - 包一层 ChatPager.push / older / patch / markVoice，记录每次调用耗时
 *   - 汇总 count / avg / p95 / max，供"对话路径有没有 O(n) 全量重排"取证
 * 幂等：重复注入只生效一次；未加载页面时不做任何事。
 * ============================================================ */
var PerfProbe = (() => {
  const buckets = Object.create(null);
  const targets = [
    ["push", "push"], ["older", "older"], ["patch", "patch"], ["markVoice", "markVoice"],
  ];

  function record(name, ms) {
    const b = buckets[name] || (buckets[name] = { n: 0, ms: 0, max: 0, list: [] });
    b.n += 1;
    b.ms += ms;
    if (ms > b.max) b.max = ms;
    b.list.push(ms);
    if (b.list.length > 2000) b.list.shift();
  }

  function p95(list) {
    if (!list.length) return 0;
    const sorted = list.slice().sort((a, b) => a - b);
    return sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)];
  }

  function wrap(pager) {
    for (const [key, bucket] of targets) {
      const fn = pager && pager[key];
      if (typeof fn !== "function" || fn.__perfWrapped) continue;
      const wrapped = function () {
        const t0 = performance.now();
        const out = fn.apply(this, arguments);
        record(bucket, performance.now() - t0);
        return out;
      };
      wrapped.__perfWrapped = true;
      pager[key] = wrapped;
    }
  }

  wrap(typeof window !== "undefined" ? window.ChatPager : null);

  function stats() {
    const out = {};
    for (const name of Object.keys(buckets)) {
      const b = buckets[name];
      out[name] = {
        n: b.n,
        avgMs: b.n ? Math.round((b.ms / b.n) * 100) / 100 : 0,
        p95Ms: Math.round(p95(b.list) * 100) / 100,
        maxMs: Math.round(b.max * 100) / 100,
      };
    }
    return out;
  }

  function reset() {
    for (const k of Object.keys(buckets)) delete buckets[k];
  }

  const api = { stats, reset };
  if (typeof window !== "undefined") window.PerfProbe = api;
  if (typeof module === "object" && module.exports) module.exports = api;
  return api;
})();
