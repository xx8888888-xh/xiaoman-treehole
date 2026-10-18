/* ============================================================
 * friend_card.js · 友谊卡片（P2-3 邀请机制·口碑裂变入口）
 * ------------------------------------------------------------
 * 脱敏铁律：卡片只含聚合数字 + 小满身份，零聊天原文/昵称/人名/
 * 宠物名/事件值——卡片是产品广告，不是聊天截图。
 * 数据全部来自本机 localStorage 既有键（P1-1/P2-1，零新增持久化）：
 *   xiaoman_first_met / xiaoman_rounds / xiaoman_ms_done / xiaoman_stats_v1
 * 纯 2D canvas 900×1200 零依赖（离线可用、APK WebView 兼容，
 * 不碰 WebGL stage——规避 preserveDrawingBuffer 与模型姿态不可控）。
 * draw() 返回落卡文案账本并挂 window.__cardTexts / __cardShareResult
 * （E2E 脱敏审计与分享链断言用，先例同 __genericPet）。
 * ============================================================ */
const FriendCard = (() => {
  const W = 900, H = 1200;
  const FONT = '"PingFang SC","HarmonyOS Sans SC","Microsoft YaHei",-apple-system,sans-serif';
  const INK = "#5A4A4F", INK_L = "#7A6A6F", PINK = "#F7A8B8", PINK_D = "#E88498";
  // 尾部固定文案池（非用户数据，3 选 1）
  const TAILS = [
    "朋友不一定是人，但陪伴一定是真的",
    "树洞里存着的每一句话，只有你自己知道",
    "有人认真听你说话，这件事本身就很珍贵"
  ];

  // ---------- 数据组装（全部真实、零原文、零新增持久化） ----------
  function collect() {
    const out = { days: -1, rounds: 0, msgs: 0, haha: 0, emoji: null, badge: null };
    try {
      const t = parseInt(localStorage.getItem("xiaoman_first_met") || "0", 10) || 0;
      if (t) out.days = Math.floor((Date.now() - t) / 86400000);
      out.rounds = parseInt(localStorage.getItem("xiaoman_rounds") || "0", 10) || 0;
      const snap = window.Stats ? Stats.snapshot() : {};
      out.msgs = snap.msgs || 0;
      out.haha = snap.haha || 0;
      out.emoji = snap.emoji || null;
      const done = JSON.parse(localStorage.getItem("xiaoman_ms_done") || "[]");
      out.badge = (Array.isArray(done) && done.length) ? Math.max(...done) : null;
    } catch (e) { /* 数据层异常 → 用默认零值，卡片照常生成 */ }
    return out;
  }

  // ---------- 绘制原语 ----------
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  function star(ctx, x, y, s, color) {   // 四角星（手绘感）
    ctx.save();
    ctx.translate(x, y);
    ctx.beginPath();
    ctx.moveTo(0, -s);
    ctx.quadraticCurveTo(0, 0, s, 0);
    ctx.quadraticCurveTo(0, 0, 0, s);
    ctx.quadraticCurveTo(0, 0, -s, 0);
    ctx.quadraticCurveTo(0, 0, 0, -s);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();
  }
  function cloud(ctx, x, y, s, color) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.ellipse(x, y, 46 * s, 18 * s, 0, 0, Math.PI * 2);
    ctx.ellipse(x - 30 * s, y + 6 * s, 26 * s, 13 * s, 0, 0, Math.PI * 2);
    ctx.ellipse(x + 32 * s, y + 4 * s, 28 * s, 14 * s, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
  /** Q 版小满头像：圆脸/粉发双马尾/刘海发缝/腮红（纯 2D 手绘，零资源依赖；
   *  VLM 评审迭代：去呆毛（被读成"果柄"）→双马尾+刘海三瓣+发顶高光） */
  function avatar(ctx, cx, cy, r) {
    const s = r / 100;
    ctx.save();
    // 柔光底：外层淡粉晕圈 + 白圆（替代硬描边，去机械感）
    ctx.beginPath(); ctx.arc(cx, cy, r + 7 * s, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(247,168,184,0.28)"; ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = "#FFFFFF"; ctx.fill();
    // 双马尾（脸两侧粉长发，萌系少女识别点）
    ctx.fillStyle = PINK;
    ctx.beginPath(); ctx.ellipse(cx - 82 * s, cy + 26 * s, 22 * s, 58 * s, 0.18, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(cx + 82 * s, cy + 26 * s, 22 * s, 58 * s, -0.18, 0, Math.PI * 2); ctx.fill();
    // 后发（粉，包住脸后层）
    ctx.beginPath(); ctx.ellipse(cx, cy + 8 * s, 78 * s, 84 * s, 0, 0, Math.PI * 2);
    ctx.fillStyle = PINK; ctx.fill();
    // 脸
    ctx.beginPath(); ctx.ellipse(cx, cy + 14 * s, 62 * s, 66 * s, 0, 0, Math.PI * 2);
    ctx.fillStyle = "#FFE8D6"; ctx.fill();
    // 刘海（三瓣圆弧=手绘发缝，替代呆毛）
    ctx.beginPath();
    ctx.moveTo(cx - 62 * s, cy + 14 * s);
    ctx.quadraticCurveTo(cx - 60 * s, cy - 66 * s, cx, cy - 70 * s);
    ctx.quadraticCurveTo(cx + 60 * s, cy - 66 * s, cx + 62 * s, cy + 14 * s);
    ctx.quadraticCurveTo(cx + 44 * s, cy - 16 * s, cx + 22 * s, cy - 24 * s);
    ctx.quadraticCurveTo(cx + 8 * s, cy - 14 * s, cx, cy - 24 * s);
    ctx.quadraticCurveTo(cx - 8 * s, cy - 14 * s, cx - 22 * s, cy - 24 * s);
    ctx.quadraticCurveTo(cx - 44 * s, cy - 16 * s, cx - 62 * s, cy + 14 * s);
    ctx.closePath(); ctx.fillStyle = PINK; ctx.fill();
    // 发顶高光（淡白弧，头发立体感）
    ctx.beginPath();
    ctx.moveTo(cx - 34 * s, cy - 52 * s);
    ctx.quadraticCurveTo(cx, cy - 68 * s, cx + 34 * s, cy - 52 * s);
    ctx.lineWidth = 8 * s; ctx.strokeStyle = "rgba(255,255,255,0.5)";
    ctx.lineCap = "round"; ctx.stroke();
    // 眼睛 + 高光（瞳孔加大+双高光，去呆滞）
    ctx.fillStyle = INK;
    ctx.beginPath(); ctx.arc(cx - 25 * s, cy + 22 * s, 10 * s, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(cx + 25 * s, cy + 22 * s, 10 * s, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#FFFFFF";
    ctx.beginPath(); ctx.arc(cx - 22 * s, cy + 19 * s, 3.6 * s, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(cx + 28 * s, cy + 19 * s, 3.6 * s, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(cx - 28 * s, cy + 25 * s, 1.8 * s, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(cx + 22 * s, cy + 25 * s, 1.8 * s, 0, Math.PI * 2); ctx.fill();
    // 腮红（更淡更小，晕染感）
    ctx.fillStyle = "rgba(247,168,184,0.38)";
    ctx.beginPath(); ctx.ellipse(cx - 40 * s, cy + 42 * s, 9 * s, 6 * s, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(cx + 40 * s, cy + 42 * s, 9 * s, 6 * s, 0, 0, Math.PI * 2); ctx.fill();
    // 微笑嘴
    ctx.beginPath();
    ctx.moveTo(cx - 11 * s, cy + 47 * s);
    ctx.quadraticCurveTo(cx, cy + 56 * s, cx + 11 * s, cy + 47 * s);
    ctx.lineWidth = 4.5 * s; ctx.strokeStyle = PINK_D; ctx.stroke();
    ctx.restore();
  }

  // ---------- 生成（返回账本 texts，供脱敏审计） ----------
  function draw() {
    const d = collect();
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");
    const texts = [];
    const label = (s, x, y, font, color, align) => {
      s = String(s);
      texts.push(s);                                   // 账本：所有落卡文案
      ctx.font = font; ctx.fillStyle = color;
      ctx.textAlign = align || "center";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(s, x, y);
    };

    // 背景：奶油→樱粉渐变
    const g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#FFF9F2"); g.addColorStop(0.55, "#FFEFE6"); g.addColorStop(1, "#FFD9E3");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // 手绘装饰：圆点簇 + 四角星 + 云
    ctx.fillStyle = "rgba(247,168,184,0.30)";
    [[92, 74, 10], [124, 108, 6], [70, 130, 5], [806, 92, 9], [776, 60, 5], [840, 132, 6], [96, 1096, 7], [812, 1080, 6], [770, 1116, 5]].forEach(([x, y, r]) => {
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    });
    star(ctx, 176, 64, 16, "rgba(156,217,196,0.5)");
    star(ctx, 736, 168, 13, "rgba(247,168,184,0.4)");
    star(ctx, 60, 236, 11, "rgba(232,132,152,0.35)");
    cloud(ctx, 148, 320, 1.0, "rgba(255,255,255,0.55)");
    cloud(ctx, 760, 300, 0.8, "rgba(255,255,255,0.5)");

    // 头部：Q 版小满 + 标题
    avatar(ctx, 450, 176, 98);
    label("小满树洞", 450, 348, "bold 46px " + FONT, INK);
    label("· 友谊卡片 ·", 450, 390, "bold 26px " + FONT, PINK_D);

    // 手绘点线分隔（三圆点，替代机械虚线）
    ctx.fillStyle = "rgba(232,132,152,0.55)";
    [[402, 424, 5], [450, 430, 7], [498, 424, 5]].forEach(([x, y, r]) => {
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    });

    // 主数字：相识天数（days<0 优雅降级为文字行）
    if (d.days >= 0) {
      label("我们已经相识", 450, 480, "27px " + FONT, INK_L);
      const big = String(d.days);
      ctx.font = "bold 118px " + FONT; ctx.fillStyle = PINK_D;
      const w = ctx.measureText(big).width;
      ctx.textAlign = "left";
      ctx.fillText(big, 450 - (w + 14) / 2, 600);
      texts.push(big);
      label("天", 450 + (w - 14) / 2 + 30, 600, "bold 34px " + FONT, INK_L);
      // 主数字两侧小装饰（补中部留白，手绘呼应）
      star(ctx, 450 - (w + 14) / 2 - 56, 566, 13, "rgba(247,168,184,0.45)");
      star(ctx, 450 + (w - 14) / 2 + 92, 566, 13, "rgba(156,217,196,0.45)");
    } else {
      label("刚认识不久", 450, 560, "bold 72px " + FONT, PINK_D);
    }

    // 2×2 数据格（真实数字直填，零虚构；贴纸质感=粉描边+柔投影）
    const cells = [
      ["聊了", String(d.msgs), "句"],
      ["笑出声", String(d.haha), "次"],
      ["来找过小满", String(d.rounds), "次"],
      ["招牌表情", d.emoji || "还没有，快发一个~", ""],
    ];
    cells.forEach((c, i) => {
      const gx = 60 + (i % 2) * 400, gy = 668 + Math.floor(i / 2) * 154;
      ctx.save();
      ctx.shadowColor = "rgba(232,132,152,0.22)";
      ctx.shadowBlur = 16; ctx.shadowOffsetY = 7;
      roundRect(ctx, gx, gy, 380, 132, 26);
      ctx.fillStyle = "rgba(255,255,255,0.92)"; ctx.fill();
      ctx.restore();
      roundRect(ctx, gx, gy, 380, 132, 26);
      ctx.lineWidth = 2.5; ctx.strokeStyle = "rgba(247,168,184,0.5)"; ctx.stroke();
      label(c[0], gx + 190, gy + 42, "24px " + FONT, INK_L);
      const main = c[1] + (c[2] || "");
      label(main, gx + 190, gy + 104, "bold 50px " + FONT, INK);
    });

    // 里程碑徽章（已达成的最大里程碑；无则不画）
    if (d.badge) {
      const txt = "🏆 聊满 " + d.badge + " 次";
      ctx.font = "bold 30px " + FONT;
      const bw = ctx.measureText(txt).width + 76;
      ctx.save();
      ctx.shadowColor = "rgba(232,132,152,0.3)";
      ctx.shadowBlur = 14; ctx.shadowOffsetY = 5;
      roundRect(ctx, 450 - bw / 2, 1000, bw, 58, 29);
      const bg = ctx.createLinearGradient(0, 1000, 0, 1058);
      bg.addColorStop(0, PINK); bg.addColorStop(1, PINK_D);
      ctx.fillStyle = bg; ctx.fill();
      ctx.restore();
      label(txt, 450, 1039, "bold 30px " + FONT, "#FFFFFF");
    }

    // 尾部：固定文案池随机（非用户数据）+ 底注（加深保对比度）
    label(TAILS[Math.floor(Math.random() * TAILS.length)], 450, 1108, "29px " + FONT, INK);
    label("小满是 AI 朋友 · 聊天数据只存在你的手机上", 450, 1148, "20px " + FONT, INK);
    label("小满树洞 · 开源", 450, 1178, "bold 20px " + FONT, PINK_D);

    const url = cv.toDataURL("image/png");
    window.__cardTexts = texts;                        // 暴露账本供 E2E 脱敏审计
    return { url, texts, data: d };
  }

  // ---------- 预览 / 分享 ----------
  let lastUrl = null;
  function render(imgEl) {
    const r = draw();
    lastUrl = r.url;
    if (imgEl) imgEl.src = r.url;
    return r;
  }
  /** dataURL → Blob（atob 手转：CSP connect-src 不放行 data:，fetch 会被拦；纯本地零网络） */
  function toBlob(url) {
    const comma = url.indexOf(",");
    const mime = (url.slice(0, comma).match(/^data:([^;]+)/) || [])[1] || "image/png";
    const bin = atob(url.slice(comma + 1));
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return new Blob([arr], { type: mime });
  }
  /** 保存/分享：navigator.share(files) 优先 → 降级 a[download] */
  async function share() {
    if (!lastUrl) draw();
    let res = "failed";
    try {
      const file = new File([toBlob(lastUrl)], "xiaoman-friend-card.png", { type: "image/png" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: "我和小满的友谊卡片" });
        res = "shared";
      }
    } catch (e) { /* 无 share API / 面板取消 → 落 download */ }
    if (res === "failed") {
      try {
        const a = document.createElement("a");
        a.href = lastUrl;
        a.download = "小满友谊卡片.png";
        document.body.appendChild(a); a.click(); a.remove();
        res = "downloaded";
      } catch (e) { /* 极端环境（禁下载）→ toast 由调用方兜底 */ }
    }
    window.__cardShareResult = res;
    return res;
  }

  return { collect, draw, render, share, TAILS };
})();
window.FriendCard = FriendCard;
