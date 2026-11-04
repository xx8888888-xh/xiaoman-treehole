/* ============================================================
 * errors.js — 把内部异常翻译成"用户看得懂、能行动"的话
 *
 * 边界要求（产品评审 P0）：断网 / 429 / 5xx / 模型返回坏 JSON /
 * localStorage 满 / IndexedDB 被清空，任何一种都不能白屏或卡死，
 * 必须给出：发生了什么 + 用户能做什么 + 要不要重试。
 *
 * 纯函数，无 DOM 依赖，node 可直接单测。
 * ============================================================ */
const XMErrors = (() => {

  function statusOf(e) {
    if (!e) return 0;
    return Number(e.status || (e.res && e.res.status) || 0);
  }

  function isQuota(e) {
    if (!e) return false;
    const name = String(e.name || "");
    const code = Number(e.code || 0);
    return name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED"
      || code === 22 || code === 1014;
  }

  function isTimeout(e) {
    if (!e) return false;
    return String(e.name || "") === "AbortError"
      || String(e.name || "") === "TimeoutError"
      || /timeout|超时/i.test(String(e.message || ""));
  }

  function isNetwork(e) {
    if (!e) return false;
    const msg = String(e.message || e || "");
    return /Failed to fetch|NetworkError|Load failed|network|断网|离线|ERR_INTERNET/i.test(msg);
  }

  /**
   * @param {any} e 异常或 {status, kind}
   * @returns {{kind:string, title:string, hint:string, retry:boolean}}
   */
  function describe(e) {
    const kindTag = e && e.kind;
    const status = statusOf(e);

    if (kindTag === "parse") {
      return {
        kind: "parse",
        title: "模型回了看不懂的内容",
        hint: "已经先用兜底回复接上了，聊天不会断。",
        retry: false,
      };
    }
    if (kindTag === "idb") {
      return {
        kind: "idb",
        title: "本机记录暂时读不出来",
        hint: "点下面的「重试」再读一次；现在继续聊天也没问题。",
        retry: true,
      };
    }
    if (kindTag === "quota" || isQuota(e)) {
      return {
        kind: "quota",
        title: "手机存储满了",
        hint: "设置 → 数据 → 先导出一份备份，再清空本机记录；或腾出一点空间。",
        retry: false,
      };
    }
    if (status === 429) {
      return {
        kind: "limit",
        title: "说得有点快，服务端限流了",
        hint: "小满会自动排队重发；也可以等十几秒再发一次。",
        retry: true,
      };
    }
    if (status === 401 || status === 403) {
      return {
        kind: "auth",
        title: `AI Key 不被认可（${status}）`,
        hint: "去设置里确认 Key 复制完整、没过期，或先用本机模式聊。",
        retry: false,
      };
    }
    if (status === 404) {
      return {
        kind: "notfound",
        title: "接口地址不对（404）",
        hint: "设置里的接口地址可能多了或少了一段路径，检查后重试。",
        retry: true,
      };
    }
    if (status >= 500) {
      return {
        kind: "server",
        title: `服务端出小差了（${status}）`,
        hint: "这条消息已排队，网络恢复会自动补发；也可以先聊本地模式。",
        retry: true,
      };
    }
    if (isTimeout(e)) {
      return {
        kind: "timeout",
        title: "等太久了，网络没回话",
        hint: "检查一下网络，然后再试一次；消息不会丢。",
        retry: true,
      };
    }
    if (kindTag === "network" || isNetwork(e)) {
      return {
        kind: "network",
        title: "现在连不上网",
        hint: "网络恢复后会自动重发，不影响继续打字。",
        retry: true,
      };
    }
    return {
      kind: "unknown",
      title: "出了点小状况",
      hint: "这次回复没拿到，消息已排进待发；可以点重试或稍后再发。",
      retry: true,
    };
  }

  /** 存储写入失败时的一句话（给 toast / 设置页横幅用） */
  function storageText(where) {
    return `${where || "本机"}没存进去：手机存储满了或被隐私模式限制。先去设置里导出备份，再清理空间。`;
  }

  return { describe, statusOf, isQuota, isTimeout, isNetwork, storageText };
})();
if (typeof module === "object" && module.exports) module.exports = XMErrors;   // node 单测用
window.XMErrors = XMErrors;   // const 不挂 window，跨模块需显式挂载
