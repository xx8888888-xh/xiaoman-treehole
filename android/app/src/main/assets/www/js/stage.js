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
  const app = new PIXI.Application({
    backgroundAlpha: 0, autoDensity: true, resolution: Math.min(2, window.devicePixelRatio || 1)
  });
  let model = null;
  let lipOpen = 0;            // 当前口型目标值
  let lipHold = 0;            // 剩余保持帧（无分析器时的兜底）
  const MOODS = { happy: "开心", sad: "有点难过", gentle: "温柔模式", surprised: "诶？", shy: "脸红了", neutral: "在听你说" };

  function mount(container) {
    container.appendChild(app.view);
    const resize = () => {
      const w = container.clientWidth, h = container.clientHeight;
      app.renderer.resize(w, h);
      if (model) fit(w, h);
    };
    window.addEventListener("resize", resize);
    loadModel(() => resize());
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
      m.internalModel.motionManager.on("motionFinish", () => {});
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
    const idx = { happy: 0, sad: 1, gentle: 2, surprised: 3, shy: 4, neutral: 5 }[name];
    if (idx == null) return;
    try { model.expression(idx); } catch (e) {}
    const moodEl = document.getElementById("moodText");
    if (moodEl) {
      moodEl.textContent = MOODS[name] || "在听你说";
      const chip = moodEl.parentElement;
      chip.classList.add("pop");
      setTimeout(() => chip.classList.remove("pop"), 350);
    }
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

  return { mount, setEmotion, playMotion, lipFrame };
})();
