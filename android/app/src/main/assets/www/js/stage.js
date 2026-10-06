/* ============================================================
 * stage.js · Live2D 舞台（基于 pixi-live2d-display + 手术改造的 Hiyori）
 * ------------------------------------------------------------
 * 能力：
 *  - 加载 web/assets/models/hiyori（含手术后新增的 6 表情 + 4 动作组）
 *  - setEmotion(name)  表情切换；playMotion(group) 动作触发
 *  - speakLipSync(level) TTS 口型驱动（ParamMouthOpenY）
 *  - 点击命中：摸头→害羞，戳身体→惊讶（配合 app 层吐槽语）
 *  - 待机呼吸：视线随机漫游 + 偶发眨眼（内置 idle 已有，此处补充漫游）
 * ============================================================ */

const Stage = (() => {
  let app = null;
  let model = null;
  let lipOpen = 0;            // 当前口型目标值
  let lipHold = 0;            // 剩余保持帧（无分析器时的兜底）
  let resizeHandler = null;   // resize 监听引用，用于销毁
  const MOODS = { happy: "开心", sad: "有点难过", gentle: "温柔模式", surprised: "诶？", shy: "脸红了", neutral: "在听你说" };

  function mount(container) {
    // 懒加载 PIXI.Application：vendor 未就绪时不崩，降级只隐藏舞台
    try {
      if (!window.PIXI) throw new Error("PIXI 未加载");
      app = new PIXI.Application({
        backgroundAlpha: 0, autoDensity: true, resolution: Math.min(2, window.devicePixelRatio || 1)
      });
      container.appendChild(app.view);
    } catch (e) {
      console.warn("Stage 初始化失败，显示兜底占位:", e.message);
      // 可见兜底：不再把舞台整块隐藏，给出明确提示；聊天主流程不受影响
      const tip = document.createElement("div");
      tip.className = "stage-fallback";
      tip.textContent = "小满在打盹…（画面没能加载出来，聊天功能不受影响）";
      container.appendChild(tip);
      return; // 降级：不加载模型、不绑定事件
    }

    resizeHandler = () => {
      const w = container.clientWidth, h = container.clientHeight;
      app.renderer.resize(w, h);
      if (model) fit(w, h);
    };
    window.addEventListener("resize", resizeHandler);
    loadModel(() => resizeHandler());
  }

  function fit(w, h) {
    model.anchor.set(0.5, 0.5);
    const s = Math.min(w / model.internalModel.originalWidth, h / model.internalModel.originalHeight) * 0.96;
    model.scale.set(s);
    model.x = w / 2;
    model.y = h * 0.47;
  }

  function loadModel(done) {
    PIXI.live2d.Live2DModel.from("assets/models/hiyori/Hiyori.model3.json").then(m => {
      model = m;
      app.stage.addChild(m);

      // 点击命中（HitAreas 在 model3.json 中定义：Head / Body）
      m.on("hit", areas => {
        if (areas.includes("Head")) {
          setEmotion("shy"); playMotion("Shake");
          window.App && App.onPoke("head");
        } else if (areas.includes("Body")) {
          setEmotion("surprised"); playMotion("HappyJump");
          window.App && App.onPoke("body");
        }
      });

      // 每帧：口型 + 视线漫游
      const core = () => m.internalModel.coreModel;
      app.ticker.add(() => {
        try {
          // 口型：平滑逼近目标，播语音时由 TTS.onFrame 喂 level
          const c = core();
          const cur = c.getParameterValueById("ParamMouthOpenY");
          const target = lipHold > 0 ? lipOpen : 0;
          c.setParameterValueById("ParamMouthOpenY", cur + (target - cur) * 0.35);
          if (lipHold > 0) lipHold--;
        } catch (e) {}
      });
      // 视线漫游：每 3~6 秒瞟一眼别处
      (function wander() {
        setTimeout(() => {
          if (!model) return;
          const c = core();
          const gx = (Math.random() - 0.5) * 0.9, gy = (Math.random() - 0.5) * 0.5;
          try {
            c.setParameterValueById("ParamEyeBallX", gx);
            c.setParameterValueById("ParamEyeBallY", gy);
            c.setParameterValueById("ParamAngleX", gx * 12);
            c.setParameterValueById("ParamAngleY", gy * 10);
          } catch (e) {}
          wander();
        }, 3000 + Math.random() * 3000);
      })();
      done && done();
    }).catch(err => console.error("Live2D 加载失败:", err));
  }

  function setEmotion(name) {
    if (!model) return;
    // 按 expression name 查找，避免硬编码索引（模型替换后顺序可能变）
    try {
      const names = model.expressionManager?.expressionNames || [];
      const idx = names.indexOf(name);
      if (idx >= 0) model.expression(idx);
      else {
        // 兜底：兼容旧模型索引
        const fallback = { happy: 0, sad: 1, gentle: 2, surprised: 3, shy: 4, neutral: 5 }[name];
        if (fallback != null) model.expression(fallback);
      }
    } catch (e) {}
    const moodEl = document.getElementById("moodText");
    if (moodEl) {
      moodEl.textContent = MOODS[name] || "在听你说";
      const chip = moodEl.parentElement;
      chip.classList.add("pop");
      setTimeout(() => chip.classList.remove("pop"), 350);
    }
  }

  /** 销毁舞台：清理 resize 监听、PIXI ticker、模型资源 */
  function unmount() {
    if (resizeHandler) {
      window.removeEventListener("resize", resizeHandler);
      resizeHandler = null;
    }
    if (app) {
      app.ticker.stop();
      app.destroy(true);
      app = null;
    }
    model = null;
  }

  function playMotion(group) {
    if (!model) return;
    try { model.motion(group, undefined, 2); } catch (e) {}
  }

  /** TTS 播放期间的口型喂入 */
  function lipFrame(level) {
    if (level < 0) { lipHold = 2; lipOpen = 0.3 + Math.random() * 0.3; return; } // 无分析器兜底
    lipOpen = 0.12 + level * 0.75;
    lipHold = 6;
  }

  return { mount, unmount, setEmotion, playMotion, lipFrame };
})();
