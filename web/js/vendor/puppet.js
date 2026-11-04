/* Layered-PNG puppet engine — no external dependencies. */
(function () {
  'use strict';

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const GPU_BUDGET = 12 * 1024 * 1024;   // first-screen: textures + back buffer

  class LayeredPuppet {
    constructor(canvas, manifest, opts) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.m = manifest;
      this.base = (opts && opts.base) || '';
      this.images = {};
      this.failed = [];
      this.mode = 'layers';        // layers | static | silhouette | hidden
      this.tierKey = 'full';
      this.requestedTier = (opts && opts.tier) || null;

      this.params = {};
      this.targets = {};
      for (const [k, def] of Object.entries(manifest.params || {})) {
        this.params[k] = def.default;
        this.targets[k] = def.default;
      }
      this.emotion = 'neutral';
      this.eye = { l: 'open', r: 'open' };
      this.mouthFrame = 'closed';
      this.visible = { body: true, haori: true, head: true, eyes: true, mouth: true };
      this.blink = { t: 0, phase: 'open' };
      this.motion = null;
      this.time = 0;
      this.autoIdle = true;
      this._sway = 0;              // lagged body sway (follows gaze)
      this._swayVel = 0;
      this.s = 1;                  // render scale: canvas pixels per art pixel
      this.W = manifest.canvas.width;
      this.H = manifest.canvas.height;
      this.canvas.width = this.W;
      this.canvas.height = this.H;
    }

    /* render at the CSS display size instead of the full art canvas */
    fit() {
      const artW = this.m.canvas.width;
      const artH = this.m.canvas.height;
      const cssH = this.canvas.clientHeight || 720;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const h = Math.max(240, Math.min(1600, Math.round(cssH * dpr)));
      const w = Math.max(120, Math.round(h * artW / artH));
      if (w !== this.W || h !== this.H) {
        this.W = w; this.H = h;
        this.canvas.width = w;
        this.canvas.height = h;
      }
      this.s = h / artH;
    }

    /* ---------- texture tiers (largest tier that fits the 12 MB GPU budget) ---------- */
    pickTier() {
      if (this.requestedTier) return this.requestedTier;
      const tiers = this.m.tiers || {};
      const keys = Object.keys(tiers).sort((a, b) => tiers[b].scale - tiers[a].scale);
      if (!keys.length) return 'full';
      const fb = this.W * this.H * 4;             // canvas back buffer
      for (const k of keys) {
        const t = tiers[k];
        if (!t.decoded || t.decoded + fb <= GPU_BUDGET) return k;
      }
      return keys[keys.length - 1];
    }

    tierInfo() {
      const key = this.pickTier();
      const tiers = this.m.tiers || {};
      if (!tiers[key]) return { key: 'full', scale: 1, path: '' };
      return { key: key, scale: tiers[key].scale, path: tiers[key].path };
    }

    mapPath(p) {
      const tier = this.tierInfo();
      if (!tier.path) return p;
      return p.replace('art/layers/', tier.path + 'layers/');
    }

    _loadOne(path) {
      return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = this.base + path;
      });
    }

    async load() {
      this.fit();
      const tier = this.tierInfo();
      this.tierKey = tier.key;
      this.tierScale = tier.scale;

      const paths = new Set();
      const add = (l) => { if (l && l.file) paths.add(l.file); };
      add(this.m.layers.body);
      add(this.m.layers.haori);
      add(this.m.layers.head);
      add(this.m.layers.hair_front);
      for (const side of ['eye_l', 'eye_r']) {
        const set = this.m.layers[side] || {};
        for (const kind of Object.keys(set)) add(set[kind]);
      }
      for (const kind of Object.keys(this.m.layers.mouth || {})) add(this.m.layers.mouth[kind]);

      const jobs = Array.from(paths).map(async (p) => {
        const img = await this._loadOne(this.mapPath(p));
        if (img) this.images[p] = img;
        else this.failed.push(p);
      });
      await Promise.all(jobs);
      this.loadedBytes = tier.path && this.m.tiers[tier.key]
        ? this.m.tiers[tier.key].bytes : null;
      this.pixelBytes = Object.values(this.images).reduce(
        (n, im) => n + im.naturalWidth * im.naturalHeight * 4, 0);

      if (this.failed.length) {
        console.error('missing textures', this.failed);
        await this.enterFallback();
      }
      return this;
    }

    /* ---------- degradation ladder: static → silhouette → hidden ---------- */
    async enterFallback() {
      const fullPath = (this.m.art && this.m.art.full) || 'art/full.png';
      const full = await this._loadOne(fullPath);
      if (full) { this.full = full; this.mode = 'static'; return; }
      const silPath = (this.m.fallback && this.m.fallback.silhouette) || '';
      const sil = silPath ? await this._loadOne(silPath) : null;
      if (sil) { this.sil = sil; this.mode = 'silhouette'; return; }
      this.mode = 'hidden';
      this.canvas.style.visibility = 'hidden';
    }

    /* ---------- parameter API ---------- */
    setParam(name, value) {
      if (name in this.targets) this.targets[name] = clamp(Number(value), -1, 1);
      if (this.m.params[name]) {
        const p = this.m.params[name];
        this.targets[name] = clamp(Number(value), p.min, p.max);
      }
    }

    getParam(name) { return this.params[name]; }

    setEmotion(name) {
      const e = this.m.emotions[name];
      if (!e) return;
      this.emotion = name;
      this.eye.l = e.eye_l;
      this.eye.r = e.eye_r;
      this.mouthFrame = e.mouth;
      this.blink.phase = 'open';
      this.blink.t = 0;
    }

    setLayerVisible(id, on) { this.visible[id] = !!on; }

    setAutoIdle(on) { this.autoIdle = !!on; }

    blinkNow() {
      if (this.blink.phase !== 'open') return;
      this.blink.phase = 'half_down';
      this.blink.t = 0;
    }

    /* ---------- motions ---------- */
    playMotion(id) {
      const def = (this.m.motions || []).find((x) => x.id === id);
      if (!def) return;
      this.motion = { def: def, t: 0, id: id };
    }

    stopMotion() { this.motion = null; }

    update(dt) {
      this.time += dt;
      for (const k of Object.keys(this.params)) {
        const d = this.targets[k] - this.params[k];
        this.params[k] += d * Math.min(1, dt * 9);
      }

      // body sway follows the gaze with a ~0.42 s lag (first-order smoothing)
      const swayTarget = clamp((this.params.bodySway || 0) * 0.55 + (this.params.gazeX || 0) * 0.45, -1, 1);
      const tau = 0.42;
      this._sway += (swayTarget - this._sway) * (1 - Math.exp(-dt / tau));

      // blink state machine: manual blinks always run, auto only starts new ones
      this.blink.t += dt;
      const phase = this.blink.phase;
      if (phase === 'open') {
        if (this.autoIdle && this.blink.t > 2.8 + Math.random() * 3.2) {
          this.blink.phase = 'half_down'; this.blink.t = 0;
        }
      } else if (phase === 'half_down' && this.blink.t > 0.045) {
        this.blink.phase = 'closed'; this.blink.t = 0;
      } else if (phase === 'closed' && this.blink.t > 0.075) {
        this.blink.phase = 'half_up'; this.blink.t = 0;
      } else if (phase === 'half_up' && this.blink.t > 0.045) {
        this.blink.phase = 'half_open'; this.blink.t = 0;
      } else if (phase === 'half_open' && this.blink.t > 0.050) {
        this.blink.phase = 'open'; this.blink.t = 0;
      }

      if (this.motion) {
        this.motion.t += dt;
        const d = this.motion.def.duration;
        if (this.motion.t > d) {
          if (this.motion.def.loop) this.motion.t = this.motion.t % d;
          else { this.motion = null; }
        }
      }
    }

    /* ---------- rendering ---------- */
    drawLayer(layer) {
      if (!layer) return;
      const img = this.images[layer.file];
      if (!img) return;
      const s = this.s;
      this.ctx.drawImage(img, layer.x * s, layer.y * s, layer.w * s, layer.h * s);
    }

    /* gaze: shift the eye contents but clip to the authored eye silhouette */
    drawGazeEye(layer, img, dx, dy) {
      const s = this.s;
      const lw = Math.round(layer.w * s);
      const lh = Math.round(layer.h * s);
      if (!this._eyeBuf) this._eyeBuf = document.createElement('canvas');
      const off = this._eyeBuf;
      if (off.width !== lw || off.height !== lh) { off.width = lw; off.height = lh; }
      const o = off.getContext('2d');
      o.clearRect(0, 0, lw, lh);
      o.drawImage(img, dx, dy, lw, lh);
      o.globalCompositeOperation = 'destination-in';
      o.drawImage(img, 0, 0, lw, lh);
      o.globalCompositeOperation = 'source-over';
      this.ctx.drawImage(off, layer.x * s, layer.y * s);
    }

    eyeMix(side) {
      const phase = this.blink.phase;
      let kind = this.eye[side];
      if (this.emotion === 'laugh' || phase === 'closed') kind = 'closed';
      else if (phase === 'half_down' || phase === 'half_up' || phase === 'half_open') kind = 'half';
      return { a: kind, b: kind, p: 0 };
    }

    mouthLevels() {
      return (this.m.layers && this.m.layers.mouthLevels) ||
        ['closed', 'open_s', 'open_m', 'open_l'];
    }

    /* continuous open amount 0..1 from the talk motion / lipSync slider */
    mouthOpen() {
      if (this.motion && this.motion.def.id === 'talk') {
        return Math.abs(Math.sin(this.motion.t * Math.PI * 2.1));
      }
      if ((this.emotion === 'talk') && this.params.lipSync <= 0.02) {
        return Math.abs(Math.sin(this.time * Math.PI * 2.1));
      }
      return this.params.lipSync;
    }

    /* cross-fades between the two nearest mouth levels: no hard 3-step jumps */
    mouthMix() {
      const levels = this.mouthLevels();
      const open = this.mouthOpen();
      if (open <= 0.02) return { a: this.mouthFrame, b: this.mouthFrame, p: 0 };
      const f = clamp(open, 0, 1) * (levels.length - 1);
      const i = Math.floor(f);
      const j = Math.min(i + 1, levels.length - 1);
      return { a: levels[i], b: levels[j], p: f - i };
    }

    render() {
      const ctx = this.ctx;
      const s = this.s;
      const W = this.W;
      const H = this.H;
      const p = { x: this.m.pivot.x * s, y: this.m.pivot.y * s };
      const t = this.time;

      if (this.mode === 'hidden') { ctx.clearRect(0, 0, W, H); return; }
      if (this.mode === 'static' && this.full) {
        ctx.clearRect(0, 0, W, H);
        ctx.drawImage(this.full, 0, 0, W, H);
        return;
      }
      if (this.mode === 'silhouette' && this.sil) {
        ctx.clearRect(0, 0, W, H);
        ctx.drawImage(this.sil, 0, 0, W, H);
        return;
      }

      let headTilt = this.params.headTilt;
      let headTurn = this.params.headTurn;
      let headY = 0;
      let bodySway = 0;

      if (this.motion) {
        const mt = this.motion.t / this.motion.def.duration;
        const id = this.motion.def.id;
        if (id === 'nod') {
          headTilt += Math.sin(mt * Math.PI) * 0.45;
          headY += Math.sin(mt * Math.PI) * 10;
        } else if (id === 'shake') {
          headTurn += Math.sin(mt * Math.PI * 2) * 1.0;
        } else if (id === 'idle') {
          bodySway += Math.sin(mt * Math.PI * 2) * 0.35;
        }
      }

      const breath = this.autoIdle ? clamp(this.params.breath + Math.sin(t * 1.45) * 0.14, 0, 1) : this.params.breath;

      ctx.clearRect(0, 0, W, H);

      // body group: breathing + lagged sway around the base
      if (this.visible.body) {
        ctx.save();
        ctx.translate(W / 2, H);
        ctx.scale(1, 1 + (breath - 0.5) * 0.011);
        ctx.rotate(this._sway * 0.015 + bodySway * 0.008 + Math.sin(t * 0.9) * 0.0012);
        ctx.translate(-W / 2, -H);
        this.drawLayer(this.m.layers.body);
        if (this.visible.haori) this.drawLayer(this.m.layers.haori);
        ctx.restore();
      }

      // head group children share the same pivot transform
      ctx.save();
      ctx.translate(p.x + (headTurn * 9 + this._sway * 3) * s, p.y + (headY + (breath - 0.5) * -3) * s);
      ctx.rotate(headTilt * 0.07 + Math.sin(t * 0.8) * 0.004);
      ctx.translate(-p.x, -p.y);

      if (this.visible.head) this.drawLayer(this.m.layers.head);

      if (this.visible.eyes) {
        const gx = this.params.gazeX * 4 * s;
        const gy = this.params.gazeY * 3 * s;
        for (const [side, key] of [['l', 'eye_l'], ['r', 'eye_r']]) {
          const mx = this.eyeMix(side);
          const kinds = mx.p <= 0 ? [mx.a] : mx.p >= 1 ? [mx.b] : [mx.a, mx.b];
          for (let i = 0; i < kinds.length; i += 1) {
            const kind = kinds[i];
            const layer = this.m.layers[key][kind] || this.m.layers[key].open;
            const img = this.images[layer.file];
            if (!img) continue;
            ctx.globalAlpha = kinds.length === 1 ? 1 : (i === 0 ? 1 - mx.p : mx.p);
            if (kind === 'open' && (gx || gy)) this.drawGazeEye(layer, img, gx, gy);
            else this.drawLayer(layer);
            ctx.globalAlpha = 1;
          }
        }
      }

      if (this.visible.mouth) {
        const mx = this.mouthMix();
        const la = this.m.layers.mouth[mx.a] || this.m.layers.mouth.closed;
        const lb = this.m.layers.mouth[mx.b] || la;
        const ia = la && this.images[la.file];
        const ib = lb && this.images[lb.file];
        if (ia) {
          ctx.globalAlpha = 1;
          ctx.drawImage(ia, la.x * s, la.y * s, la.w * s, la.h * s);
        }
        if (ib && mx.p > 0 && lb !== la) {
          ctx.globalAlpha = mx.p;
          ctx.drawImage(ib, lb.x * s, lb.y * s, lb.w * s, lb.h * s);
        }
        ctx.globalAlpha = 1;
      }

      // front hair strands: drawn above eyes and mouth so blinking never breaks the bangs
      if (this.m.layers.hair_front) this.drawLayer(this.m.layers.hair_front);

      ctx.restore();
    }

    /* full-resolution frame for export / thumbnails */
    snapshot() {
      const save = { ctx: this.ctx, W: this.W, H: this.H, s: this.s };
      const off = document.createElement('canvas');
      off.width = this.m.canvas.width;
      off.height = this.m.canvas.height;
      this.ctx = off.getContext('2d');
      this.W = off.width; this.H = off.height; this.s = 1;
      this.render();
      this.ctx = save.ctx; this.W = save.W; this.H = save.H; this.s = save.s;
      return off;
    }

    start() {
      this.fit();
      if (window.ResizeObserver && !this._ro) {
        this._ro = new ResizeObserver(() => this.fit());
        this._ro.observe(this.canvas.parentElement || this.canvas);
      } else if (!this._onResize) {
        this._onResize = () => this.fit();
        window.addEventListener('resize', this._onResize);
      }
      let last = performance.now();
      const loop = (now) => {
        const dt = Math.min(0.05, (now - last) / 1000);
        last = now;
        this.update(dt);
        this.render();
        this._raf = requestAnimationFrame(loop);
      };
      this._raf = requestAnimationFrame(loop);
    }

    stop() { if (this._raf) cancelAnimationFrame(this._raf); this._raf = null; }
  }

  window.LayeredPuppet = LayeredPuppet;
})();
