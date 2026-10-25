/* Vendored from flask-basecoat, branch feat/shadcn-100-compliance, commit b327186c2e2f745310005b18e92a990a9b77f240:
   static/js/src/core.js, then components/chart-d3, chart-format, chart-polar, chart-surface, chart-render and chart.
   One change from the source: ensureStyle gives its style element the page's content security policy nonce. */
/* Flask-Basecoat runtime core.
   Server-rendered shadcn markup carries Radix's data-state contract; this core gives it behaviour:
   component registration and lazy init, open/close state with aria sync, floating positioning,
   dismiss-on-outside/escape layers, focus trapping and roving focus, and a tiny event bus.
   Component modules in ./components/*.js register themselves with FlaskBasecoat.register(). */
(function (global) {
  'use strict';

  const registry = new Map();
  const initialized = new WeakMap();
  const layers = []; // stack of open dismissable layers: {content, onDismiss, trigger, modal}

  function qs(root, sel) { return root.querySelector(sel); }
  function qsa(root, sel) { return Array.from(root.querySelectorAll(sel)); }

  /* ---- state ---- */
  function setState(el, state) {
    if (!el) return;
    el.setAttribute('data-state', state);
  }
  function isOpen(el) { return el && el.getAttribute('data-state') === 'open'; }

  /* ---- pairing: a content element belongs to the nearest unclaimed trigger before it in document order ---- */
  function pairWith(content, selector, opts) {
    opts = opts || {};
    if (opts.adjacent) {
      const prev = content.previousElementSibling;
      return prev && prev.matches(selector) ? prev : null;
    }
    const candidates = qsa(document, selector).filter((el) => !el.fbPaired && !content.contains(el) && (el.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING));
    const trigger = candidates[candidates.length - 1] || null;
    if (trigger) trigger.fbPaired = content;
    return trigger;
  }

  /* ---- ids ---- */
  let counter = 0;
  function ensureId(el, prefix) {
    if (!el.id) el.id = `${prefix || 'fb'}-${(++counter).toString(36)}`;
    return el.id;
  }

  /* ---- floating positioning (fixed coordinates, flip when overflowing) ---- */
  function float(anchor, content, opts) {
    opts = Object.assign({ side: 'bottom', align: 'start', sideOffset: 4, alignOffset: 0 }, opts || {});
    content.setAttribute('data-floating', '');
    const prevDisplay = content.style.display;
    content.style.display = 'block';
    content.style.visibility = 'hidden';
    const a = anchor.getBoundingClientRect();
    const c = content.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    let side = opts.side;
    const fits = {
      bottom: a.bottom + opts.sideOffset + c.height <= vh,
      top: a.top - opts.sideOffset - c.height >= 0,
      right: a.right + opts.sideOffset + c.width <= vw,
      left: a.left - opts.sideOffset - c.width >= 0,
    };
    const opposite = { bottom: 'top', top: 'bottom', left: 'right', right: 'left' };
    if (!fits[side] && fits[opposite[side]]) side = opposite[side];
    let x = 0, y = 0;
    if (side === 'bottom' || side === 'top') {
      y = side === 'bottom' ? a.bottom + opts.sideOffset : a.top - opts.sideOffset - c.height;
      if (opts.align === 'start') x = a.left + opts.alignOffset;
      else if (opts.align === 'end') x = a.right - c.width - opts.alignOffset;
      else x = a.left + a.width / 2 - c.width / 2;
    } else {
      x = side === 'right' ? a.right + opts.sideOffset : a.left - opts.sideOffset - c.width;
      if (opts.align === 'start') y = a.top + opts.alignOffset;
      else if (opts.align === 'end') y = a.bottom - c.height - opts.alignOffset;
      else y = a.top + a.height / 2 - c.height / 2;
    }
    x = Math.max(8, Math.min(x, vw - c.width - 8));
    y = Math.max(8, Math.min(y, vh - c.height - 8));
    content.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    content.style.setProperty('--radix-popper-transform-origin', side === 'bottom' ? 'top' : side === 'top' ? 'bottom' : side === 'left' ? 'right' : 'left');
    content.style.visibility = '';
    content.style.display = prevDisplay;
    content.setAttribute('data-side', side);
    content.setAttribute('data-align', opts.align);
    // Radix exposes the trigger width and available height as CSS variables; components use them.
    const varName = content.getAttribute('data-popper-var') || 'popper';
    content.style.setProperty(`--radix-${varName}-trigger-width`, `${a.width}px`);
    content.style.setProperty(`--radix-${varName}-content-available-height`, `${Math.max(0, (side === 'top' ? a.top : vh - a.bottom) - opts.sideOffset - 8)}px`);
    content.style.setProperty(`--radix-${varName}-content-transform-origin`, content.style.getPropertyValue('--radix-popper-transform-origin'));
    return side;
  }

  /* ---- dismissable layers ---- */
  function pushLayer(layer) {
    layers.push(layer);
    if (layers.length === 1) {
      document.addEventListener('pointerdown', onPointerDown, true);
      document.addEventListener('keydown', onKeyDown, true);
      window.addEventListener('resize', onReposition);
      window.addEventListener('scroll', onReposition, true);
    }
  }
  function removeLayer(layer) {
    const i = layers.indexOf(layer);
    if (i >= 0) layers.splice(i, 1);
    if (!layers.length) {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    }
  }
  function onPointerDown(e) {
    const top = layers[layers.length - 1];
    if (!top) return;
    const inside = (top.content && top.content.contains(e.target)) || (top.trigger && top.trigger.contains(e.target)) || (top.extra && top.extra.some((n) => n.contains(e.target)));
    if (!inside) {
      if (top.modal && top.content && !top.content.contains(e.target)) { e.preventDefault(); }
      top.onDismiss('outside', e);
    }
  }
  function onKeyDown(e) {
    const top = layers[layers.length - 1];
    if (top && e.key === 'Escape') { e.stopPropagation(); top.onDismiss('escape', e); }
  }
  function onReposition() {
    layers.forEach((l) => l.reposition && l.reposition());
  }

  /* ---- focus ---- */
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]):not([disabled]),[contenteditable]';
  function focusables(root) { return qsa(root, FOCUSABLE).filter((el) => el.offsetParent !== null || el === document.activeElement); }
  function trapFocus(container) {
    function handler(e) {
      if (e.key !== 'Tab') return;
      const list = focusables(container);
      if (!list.length) { e.preventDefault(); container.focus(); return; }
      const first = list[0], last = list[list.length - 1];
      if (e.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    container.addEventListener('keydown', handler);
    return () => container.removeEventListener('keydown', handler);
  }
  function focusFirst(container, preferSelector) {
    const target = (preferSelector && qs(container, preferSelector)) || focusables(container)[0] || container;
    if (target && !container.hasAttribute('tabindex') && target === container) container.setAttribute('tabindex', '-1');
    target && target.focus({ preventScroll: true });
  }

  /* roving focus for menus, tabs, radio groups, toggle groups */
  function roving(items, opts) {
    opts = Object.assign({ loop: true, orientation: 'vertical', typeahead: true }, opts || {});
    let typed = '', typedTimer = 0;
    return function onKey(e) {
      const enabled = items().filter((i) => !i.hasAttribute('disabled') && i.getAttribute('aria-disabled') !== 'true' && i.getAttribute('data-disabled') == null);
      if (!enabled.length) return false;
      const idx = enabled.indexOf(document.activeElement);
      const next = (opts.orientation === 'horizontal') ? ['ArrowRight'] : ['ArrowDown'];
      const prev = (opts.orientation === 'horizontal') ? ['ArrowLeft'] : ['ArrowUp'];
      if (opts.orientation === 'both') { next.push('ArrowRight', 'ArrowDown'); prev.push('ArrowLeft', 'ArrowUp'); }
      let target = null;
      if (next.includes(e.key)) target = enabled[idx < 0 ? 0 : (idx + 1 >= enabled.length ? (opts.loop ? 0 : idx) : idx + 1)];
      else if (prev.includes(e.key)) target = enabled[idx < 0 ? enabled.length - 1 : (idx - 1 < 0 ? (opts.loop ? enabled.length - 1 : 0) : idx - 1)];
      else if (e.key === 'Home') target = enabled[0];
      else if (e.key === 'End') target = enabled[enabled.length - 1];
      else if (opts.typeahead && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' ') {
        typed += e.key.toLowerCase();
        clearTimeout(typedTimer);
        typedTimer = setTimeout(() => { typed = ''; }, 600);
        const start = idx + 1;
        const ordered = enabled.slice(start).concat(enabled.slice(0, start));
        target = ordered.find((i) => (i.textContent || '').trim().toLowerCase().startsWith(typed)) || null;
      }
      if (target) { e.preventDefault(); target.focus(); return true; }
      return false;
    };
  }

  /* ---- scroll lock for modals ---- */
  let lockCount = 0, savedOverflow = '', savedPadding = '';
  function lockScroll() {
    if (lockCount++ === 0) {
      const sb = window.innerWidth - document.documentElement.clientWidth;
      savedOverflow = document.body.style.overflow; savedPadding = document.body.style.paddingRight;
      document.body.style.overflow = 'hidden';
      if (sb > 0) document.body.style.paddingRight = `${sb}px`;
      document.body.setAttribute('data-scroll-locked', '1');
    }
  }
  function unlockScroll() {
    if (lockCount > 0 && --lockCount === 0) {
      document.body.style.overflow = savedOverflow; document.body.style.paddingRight = savedPadding;
      document.body.removeAttribute('data-scroll-locked');
    }
  }

  /* ---- registration ---- */
  function register(name, def) {
    registry.set(name, def);
    if (document.readyState !== 'loading') init(name);
  }
  function init(name, root) {
    root = root || document;
    const def = registry.get(name);
    if (!def) return;
    const selector = def.selector || `[data-slot="${name}"]`;
    const nodes = root.matches && root.matches(selector) ? [root] : [];
    nodes.push(...qsa(root, selector));
    nodes.forEach((node) => {
      let set = initialized.get(node);
      if (!set) { set = new Set(); initialized.set(node, set); }
      if (set.has(name)) return;
      set.add(name);
      try { def.init(node, api); } catch (err) { console.error(`[Flask-Basecoat] ${name} init failed`, err); }
    });
  }
  function initAll(root) { registry.forEach((_, name) => init(name, root)); }

  function emit(el, name, detail) {
    el.dispatchEvent(new CustomEvent(`basecoat:${name}`, { bubbles: true, detail }));
  }

  const api = { register, init, initAll, setState, isOpen, ensureId, float, pairWith, pushLayer, removeLayer, trapFocus, focusFirst, focusables, roving, lockScroll, unlockScroll, emit, qs, qsa };
  global.FlaskBasecoat = api;

  document.addEventListener('DOMContentLoaded', () => initAll(document));
  // Content added later (htmx, fetch, Turbo) is initialised on insertion.
  const mo = new MutationObserver((muts) => {
    muts.forEach((m) => m.addedNodes.forEach((n) => { if (n.nodeType === 1) initAll(n); }));
  });
  if (document.body) mo.observe(document.body, { childList: true, subtree: true });
  else document.addEventListener('DOMContentLoaded', () => mo.observe(document.body, { childList: true, subtree: true }));
})(window);

/* Chart geometry primitives: a port of the parts of d3-path 3.1, d3-shape 3.2, d3-scale 4 and d3-array 3
   that Recharts 3.8 draws with (paths rounded to 3 digits, the linear/natural/monotone/step curves, line and
   area generators, stacks with the none/expand/sign offsets, band/point/linear scales and linear ticks).
   d3 is ISC-licensed (Copyright 2010-2023 Mike Bostock). Exposed on window.FlaskBasecoatChart.d3. */
(function () {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});

  /* ---- d3-path ---- */
  function Path(digits) {
    this._x0 = this._y0 = this._x1 = this._y1 = null;
    this._ = '';
    const k = digits == null ? null : Math.pow(10, Math.floor(digits));
    this._r = k == null ? (v) => v : (v) => Math.round(v * k) / k;
  }
  Path.prototype = {
    moveTo(x, y) { this._x0 = this._x1 = +x; this._y0 = this._y1 = +y; this._ += 'M' + this._r(this._x0) + ',' + this._r(this._y0); },
    closePath() { if (this._x1 !== null) { this._x1 = this._x0; this._y1 = this._y0; this._ += 'Z'; } },
    lineTo(x, y) { this._x1 = +x; this._y1 = +y; this._ += 'L' + this._r(this._x1) + ',' + this._r(this._y1); },
    bezierCurveTo(x1, y1, x2, y2, x, y) {
      this._x1 = +x; this._y1 = +y;
      this._ += 'C' + this._r(+x1) + ',' + this._r(+y1) + ',' + this._r(+x2) + ',' + this._r(+y2) + ',' + this._r(this._x1) + ',' + this._r(this._y1);
    },
    toString() { return this._; },
  };

  /* ---- curves ---- */
  function Linear(context) { this._context = context; }
  Linear.prototype = {
    areaStart() { this._line = 0; },
    areaEnd() { this._line = NaN; },
    lineStart() { this._point = 0; },
    lineEnd() { if (this._line || (this._line !== 0 && this._point === 1)) this._context.closePath(); this._line = 1 - this._line; },
    point(x, y) {
      x = +x; y = +y;
      switch (this._point) {
        case 0: this._point = 1; if (this._line) this._context.lineTo(x, y); else this._context.moveTo(x, y); break;
        case 1: this._point = 2; // falls through
        default: this._context.lineTo(x, y); break;
      }
    },
  };

  function Natural(context) { this._context = context; }
  function controlPoints(x) {
    let i; const n = x.length - 1; let m;
    const a = new Array(n), b = new Array(n), r = new Array(n);
    a[0] = 0; b[0] = 2; r[0] = x[0] + 2 * x[1];
    for (i = 1; i < n - 1; ++i) { a[i] = 1; b[i] = 4; r[i] = 4 * x[i] + 2 * x[i + 1]; }
    a[n - 1] = 2; b[n - 1] = 7; r[n - 1] = 8 * x[n - 1] + x[n];
    for (i = 1; i < n; ++i) { m = a[i] / b[i - 1]; b[i] -= m; r[i] -= m * r[i - 1]; }
    a[n - 1] = r[n - 1] / b[n - 1];
    for (i = n - 2; i >= 0; --i) a[i] = (r[i] - a[i + 1]) / b[i];
    b[n - 1] = (x[n] + a[n - 1]) / 2;
    for (i = 0; i < n - 1; ++i) b[i] = 2 * x[i + 1] - a[i + 1];
    return [a, b];
  }
  Natural.prototype = {
    areaStart() { this._line = 0; },
    areaEnd() { this._line = NaN; },
    lineStart() { this._x = []; this._y = []; },
    lineEnd() {
      const x = this._x, y = this._y, n = x.length;
      if (n) {
        if (this._line) this._context.lineTo(x[0], y[0]); else this._context.moveTo(x[0], y[0]);
        if (n === 2) {
          this._context.lineTo(x[1], y[1]);
        } else {
          const px = controlPoints(x), py = controlPoints(y);
          for (let i0 = 0, i1 = 1; i1 < n; ++i0, ++i1) {
            this._context.bezierCurveTo(px[0][i0], py[0][i0], px[1][i0], py[1][i0], x[i1], y[i1]);
          }
        }
      }
      if (this._line || (this._line !== 0 && n === 1)) this._context.closePath();
      this._line = 1 - this._line;
      this._x = this._y = null;
    },
    point(x, y) { this._x.push(+x); this._y.push(+y); },
  };

  function sign(x) { return x < 0 ? -1 : 1; }
  function slope3(that, x2, y2) {
    const h0 = that._x1 - that._x0, h1 = x2 - that._x1,
      s0 = (that._y1 - that._y0) / (h0 || (h1 < 0 && -0)),
      s1 = (y2 - that._y1) / (h1 || (h0 < 0 && -0)),
      p = (s0 * h1 + s1 * h0) / (h0 + h1);
    return (sign(s0) + sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p)) || 0;
  }
  function slope2(that, t) { const h = that._x1 - that._x0; return h ? (3 * (that._y1 - that._y0) / h - t) / 2 : t; }
  function monoPoint(that, t0, t1) {
    const x0 = that._x0, y0 = that._y0, x1 = that._x1, y1 = that._y1, dx = (x1 - x0) / 3;
    that._context.bezierCurveTo(x0 + dx, y0 + dx * t0, x1 - dx, y1 - dx * t1, x1, y1);
  }
  function MonotoneX(context) { this._context = context; }
  MonotoneX.prototype = {
    areaStart() { this._line = 0; },
    areaEnd() { this._line = NaN; },
    lineStart() { this._x0 = this._x1 = this._y0 = this._y1 = this._t0 = NaN; this._point = 0; },
    lineEnd() {
      switch (this._point) {
        case 2: this._context.lineTo(this._x1, this._y1); break;
        case 3: monoPoint(this, this._t0, slope2(this, this._t0)); break;
      }
      if (this._line || (this._line !== 0 && this._point === 1)) this._context.closePath();
      this._line = 1 - this._line;
    },
    point(x, y) {
      let t1 = NaN;
      x = +x; y = +y;
      if (x === this._x1 && y === this._y1) return;
      switch (this._point) {
        case 0: this._point = 1; if (this._line) this._context.lineTo(x, y); else this._context.moveTo(x, y); break;
        case 1: this._point = 2; break;
        case 2: this._point = 3; monoPoint(this, slope2(this, t1 = slope3(this, x, y)), t1); break;
        default: monoPoint(this, this._t0, t1 = slope3(this, x, y)); break;
      }
      this._x0 = this._x1; this._x1 = x;
      this._y0 = this._y1; this._y1 = y;
      this._t0 = t1;
    },
  };
  function ReflectContext(context) { this._context = context; }
  ReflectContext.prototype = {
    moveTo(x, y) { this._context.moveTo(y, x); },
    closePath() { this._context.closePath(); },
    lineTo(x, y) { this._context.lineTo(y, x); },
    bezierCurveTo(x1, y1, x2, y2, x, y) { this._context.bezierCurveTo(y1, x1, y2, x2, y, x); },
  };
  function MonotoneY(context) { this._context = new ReflectContext(context); }
  MonotoneY.prototype = Object.create(MonotoneX.prototype);
  MonotoneY.prototype.point = function (x, y) { MonotoneX.prototype.point.call(this, y, x); };

  function Step(context, t) { this._context = context; this._t = t; }
  Step.prototype = {
    areaStart() { this._line = 0; },
    areaEnd() { this._line = NaN; },
    lineStart() { this._x = this._y = NaN; this._point = 0; },
    lineEnd() {
      if (0 < this._t && this._t < 1 && this._point === 2) this._context.lineTo(this._x, this._y);
      if (this._line || (this._line !== 0 && this._point === 1)) this._context.closePath();
      if (this._line >= 0) { this._t = 1 - this._t; this._line = 1 - this._line; }
    },
    point(x, y) {
      x = +x; y = +y;
      switch (this._point) {
        case 0: this._point = 1; if (this._line) this._context.lineTo(x, y); else this._context.moveTo(x, y); break;
        case 1: this._point = 2; // falls through
        default: {
          if (this._t <= 0) {
            this._context.lineTo(this._x, y);
            this._context.lineTo(x, y);
          } else {
            const x1 = this._x * (1 - this._t) + x * this._t;
            this._context.lineTo(x1, this._y);
            this._context.lineTo(x1, y);
          }
          break;
        }
      }
      this._x = x; this._y = y;
    },
  };

  const CURVES = {
    linear: (c) => new Linear(c),
    natural: (c) => new Natural(c),
    monotoneX: (c) => new MonotoneX(c),
    monotoneY: (c) => new MonotoneY(c),
    step: (c) => new Step(c, 0.5),
    stepBefore: (c) => new Step(c, 0),
    stepAfter: (c) => new Step(c, 1),
  };

  /* Recharts' getCurveFactory: 'monotone' resolves to monotoneX/Y by layout, unknown names fall back to linear. */
  function curveFactory(type, layout) {
    let name = type || 'linear';
    if (name === 'monotone') name = layout === 'vertical' ? 'monotoneY' : 'monotoneX';
    return CURVES[name] || CURVES.linear;
  }

  /* ---- line / area generators (accessors receive the datum) ---- */
  function line(opts) {
    const { x, y, defined, curve } = opts;
    return function (data) {
      const buffer = new Path(3);
      const output = curve(buffer);
      let defined0 = false;
      const n = data.length;
      for (let i = 0; i <= n; ++i) {
        const d = data[i];
        if (!(i < n && defined(d, i)) === defined0) {
          if ((defined0 = !defined0)) output.lineStart(); else output.lineEnd();
        }
        if (defined0) output.point(+x(d, i), +y(d, i));
      }
      return buffer + '' || null;
    };
  }

  function area(opts) {
    const { x0, y0, x1, y1, defined, curve } = opts;
    return function (data) {
      const buffer = new Path(3);
      const output = curve(buffer);
      const n = data.length;
      const x0z = new Array(n), y0z = new Array(n);
      let defined0 = false, j = 0;
      for (let i = 0; i <= n; ++i) {
        const d = data[i];
        if (!(i < n && defined(d, i)) === defined0) {
          if ((defined0 = !defined0)) {
            j = i;
            output.areaStart();
            output.lineStart();
          } else {
            output.lineEnd();
            output.lineStart();
            for (let k = i - 1; k >= j; --k) output.point(x0z[k], y0z[k]);
            output.lineEnd();
            output.areaEnd();
          }
        }
        if (defined0) {
          x0z[i] = +x0(d, i); y0z[i] = +y0(d, i);
          output.point(x1 ? +x1(d, i) : x0z[i], y1 ? +y1(d, i) : y0z[i]);
        }
      }
      return buffer + '' || null;
    };
  }

  /* ---- stack ---- */
  function offsetNone(series, order) {
    const n = series.length;
    if (!(n > 1)) return;
    let s1 = series[order[0]];
    const m = s1.length;
    for (let i = 1; i < n; ++i) {
      const s0 = s1; s1 = series[order[i]];
      for (let j = 0; j < m; ++j) {
        s1[j][1] += s1[j][0] = isNaN(s0[j][1]) ? s0[j][0] : s0[j][1];
      }
    }
  }
  function offsetExpand(series, order) {
    const n = series.length;
    if (!(n > 0)) return;
    for (let j = 0, m = series[0].length; j < m; ++j) {
      let y = 0;
      for (let i = 0; i < n; ++i) y += series[i][j][1] || 0;
      if (y) for (let i = 0; i < n; ++i) series[i][j][1] /= y;
    }
    offsetNone(series, order);
  }
  function offsetSign(series) {
    const n = series.length;
    if (n <= 0) return;
    const m = series[0] && series[0].length;
    if (!m) return;
    for (let j = 0; j < m; ++j) {
      let positive = 0, negative = 0;
      for (let i = 0; i < n; ++i) {
        const col = series[i][j];
        if (col == null) continue;
        const value = isNaN(col[1]) ? col[0] : col[1];
        if (value >= 0) { col[0] = positive; positive += value; col[1] = positive; } else { col[0] = negative; negative += value; col[1] = negative; }
      }
    }
  }
  function offsetPositive(series) {
    const n = series.length;
    if (n <= 0) return;
    const m = series[0] && series[0].length;
    if (!m) return;
    for (let j = 0; j < m; ++j) {
      let positive = 0;
      for (let i = 0; i < n; ++i) {
        const col = series[i][j];
        if (col == null) continue;
        const value = isNaN(col[1]) ? col[0] : col[1];
        if (value >= 0) { col[0] = positive; positive += value; col[1] = positive; } else { col[0] = 0; col[1] = 0; }
      }
    }
  }
  const OFFSETS = { none: offsetNone, expand: offsetExpand, sign: offsetSign, positive: offsetPositive };

  function stack(data, keys, value, offsetType) {
    const sz = keys.map((key) => { const s = []; s.key = key; return s; });
    data.forEach((d, j) => {
      for (let i = 0; i < sz.length; ++i) {
        const p = [0, +value(d, sz[i].key, j)];
        p.data = d;
        sz[i][j] = p;
      }
    });
    const order = sz.map((_, i) => i);
    order.forEach((o, i) => { sz[o].index = i; });
    (OFFSETS[offsetType] || offsetNone)(sz, order);
    return sz;
  }

  /* ---- scales ---- */
  function keyOf(v) { return v !== null && typeof v === 'object' ? v.valueOf() : v; }

  function band(domainValues, rangeValues, opts) {
    const o = Object.assign({ paddingInner: 0, paddingOuter: 0, align: 0.5 }, opts || {});
    const index = new Map();
    const domain = [];
    for (const v of domainValues) { const k = keyOf(v); if (!index.has(k)) { index.set(k, domain.length); domain.push(v); } }
    const r0 = +rangeValues[0], r1 = +rangeValues[1];
    const n = domain.length, reverse = r1 < r0;
    let start = reverse ? r1 : r0;
    const stop = reverse ? r0 : r1;
    const paddingInner = Math.min(1, o.paddingInner);
    const step = (stop - start) / Math.max(1, n - paddingInner + o.paddingOuter * 2);
    start += (stop - start - step * (n - paddingInner)) * o.align;
    const bandwidth = step * (1 - paddingInner);
    let values = [];
    for (let i = 0; i < n; i++) values.push(start + step * i);
    if (reverse) values = values.reverse();
    const scale = (v) => { const i = index.get(keyOf(v)); return i === undefined ? undefined : values[i % values.length]; };
    scale.domain = () => domain.slice();
    scale.range = () => [r0, r1];
    scale.bandwidth = () => bandwidth;
    scale.step = () => step;
    return scale;
  }

  function point(domainValues, rangeValues) {
    return band(domainValues, rangeValues, { paddingInner: 1, paddingOuter: 0, align: 0.5 });
  }

  function normalize(a, b) {
    a = +a; b -= a;
    return b ? (x) => (x - a) / b : () => (isNaN(b) ? NaN : 0.5);
  }
  function interpolateNumber(a, b) { a = +a; b = +b; return (t) => a * (1 - t) + b * t; }

  const e10 = Math.sqrt(50), e5 = Math.sqrt(10), e2 = Math.sqrt(2);
  function tickSpec(start, stop, count) {
    const step = (stop - start) / Math.max(0, count),
      power = Math.floor(Math.log10(step)),
      error = step / Math.pow(10, power),
      factor = error >= e10 ? 10 : error >= e5 ? 5 : error >= e2 ? 2 : 1;
    let i1, i2, inc;
    if (power < 0) {
      inc = Math.pow(10, -power) / factor;
      i1 = Math.round(start * inc); i2 = Math.round(stop * inc);
      if (i1 / inc < start) ++i1;
      if (i2 / inc > stop) --i2;
      inc = -inc;
    } else {
      inc = Math.pow(10, power) * factor;
      i1 = Math.round(start / inc); i2 = Math.round(stop / inc);
      if (i1 * inc < start) ++i1;
      if (i2 * inc > stop) --i2;
    }
    if (i2 < i1 && 0.5 <= count && count < 2) return tickSpec(start, stop, count * 2);
    return [i1, i2, inc];
  }
  function ticks(start, stop, count) {
    stop = +stop; start = +start; count = +count;
    if (!(count > 0)) return [];
    if (start === stop) return [start];
    const reverse = stop < start;
    const [i1, i2, inc] = reverse ? tickSpec(stop, start, count) : tickSpec(start, stop, count);
    if (!(i2 >= i1)) return [];
    const n = i2 - i1 + 1, out = new Array(n);
    if (reverse) {
      if (inc < 0) for (let i = 0; i < n; ++i) out[i] = (i2 - i) / -inc;
      else for (let i = 0; i < n; ++i) out[i] = (i2 - i) * inc;
    } else {
      if (inc < 0) for (let i = 0; i < n; ++i) out[i] = (i1 + i) / -inc;
      else for (let i = 0; i < n; ++i) out[i] = (i1 + i) * inc;
    }
    return out;
  }

  function linear(domainValues, rangeValues) {
    const d = [+domainValues[0], +domainValues[1]], r = [+rangeValues[0], +rangeValues[1]];
    let f;
    if (d[1] < d[0]) { const n = normalize(d[1], d[0]), i = interpolateNumber(r[1], r[0]); f = (x) => i(n(x)); } else { const n = normalize(d[0], d[1]), i = interpolateNumber(r[0], r[1]); f = (x) => i(n(x)); }
    const scale = (x) => (x == null || isNaN(+x) ? undefined : f(+x));
    scale.domain = () => d.slice();
    scale.range = () => r.slice();
    scale.ticks = (count) => ticks(d[0], d[d.length - 1], count == null ? 10 : count);
    return scale;
  }

  NS.d3 = { Path, curveFactory, line, area, stack, band, point, linear, ticks };
})();

/* Named formatters and render functions for chart specs.

   A chart spec cannot carry JavaScript, so every function-valued prop of an upstream chart (tickFormatter,
   labelFormatter, formatter, Label content, custom dot/shape/tick) is written as a reference
   {"$fn": "<name>", ...arguments}. The registry below maps each name to a function; Python builds the same
   references (flask_basecoat/charts.py, FORMATTERS) and documents them.

   Value formatters             return a value:   (value, ...) => any
     slice {start, end}                       String(value).slice(start, end)
     localeDate {locale, options}             new Date(value).toLocaleDateString(locale, options)
     configLabel                              chartConfig[value]?.label
     configLabelOfFirstDataKey                (_, payload) => chartConfig[payload[0].dataKey].label
   Tooltip row formatters       return HTML nodes for one tooltip row: (value, name, item, index, payload)
     unitValue {unit, wrap}                   label + value + unit ("kcal"), in a wrapper div when wrap is true
     unitValueWithTotal {unit, keys, after}   unitValue plus a "Total" row after the row with index `after`
   Label content                return an SVG element: (labelProps)
     centerText {lines, dominantBaseline}     <text> centred on the polar view box; lines = [{value, className, dy}],
                                              value = {"text": s} | {"sum": key} | {"pick": key, "index": n|{"state"}}
   Shapes / dots / ticks        return an SVG element: (props)
     activeRectangle {index, props}           Rectangle, extra props for the bar at `index`
     activeSector {index, grow}               Sector, outerRadius + grow for the sector at `index`
     activeSectorRing {index, grow, ring}     as activeSector plus a ring from outerRadius+ring[0] to +ring[1]
     payloadDot {r}                           Dot coloured with the data row's fill
     iconDot {icon, size, fill, stroke}       a lucide icon centred on the point
     payloadText {key, fill}                  <text> at the label position showing payload[key]
     stackedTick {keys, labelKey, firstDy}    polar tick text "a/b" with the category below
*/
(function () {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});
  const I = () => NS.internal;

  function idx(v, ctx) { return v && typeof v === 'object' && 'state' in v ? ctx.state[v.state] : v; }

  function valueOf(spec, ctx) {
    const { getValue } = I();
    if (spec == null) return '';
    if ('text' in spec) return spec.text;
    let v;
    if ('sum' in spec) v = ctx.data.reduce((acc, row) => acc + (Number(getValue(row, spec.sum)) || 0), 0);
    else if ('pick' in spec) { const row = ctx.data[idx(spec.index, ctx)]; v = row ? getValue(row, spec.pick) : undefined; }
    else if ('pickSum' in spec) { const row = ctx.data[idx(spec.index, ctx)]; v = row ? spec.pickSum.reduce((acc, k) => acc + (Number(getValue(row, k)) || 0), 0) : undefined; }
    return typeof v === 'number' ? v.toLocaleString() : String(v);
  }

  function el(tag, attrs, children) { return I().h(tag, attrs, children); }
  function tx(s) { return I().txt(s); }

  const VALUE = {
    slice: (o) => (value) => String(value).slice(o.start, o.end),
    localeDate: (o) => (value) => new Date(value).toLocaleDateString(o.locale || 'en-US', o.options || {}),
    configLabel: (o, ctx) => (value) => { const c = ctx.config[value]; return c ? c.label : undefined; },
    configLabelOfFirstDataKey: (o, ctx) => (_, payload) => { const k = payload && payload[0] && payload[0].dataKey; return ctx.config[k].label; },
  };

  /* Tooltip row formatters return a DocumentFragment (HTML). */
  function html(tag, cls, children, style) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (style) Object.keys(style).forEach((k) => n.style.setProperty(k, style[k]));
    (children || []).forEach((c) => n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
    return n;
  }
  function amount(value, unit) {
    return html('div', 'ml-auto flex items-baseline gap-0.5 font-mono font-medium text-foreground tabular-nums', [String(value), html('span', 'font-normal text-muted-foreground', [unit])]);
  }
  const ROW = {
    unitValue: (o, ctx) => (value, name) => {
      const label = (ctx.config[name] && ctx.config[name].label) || name;
      const inner = [String(label), amount(value, o.unit)];
      const frag = document.createDocumentFragment();
      if (o.wrap) frag.appendChild(html('div', 'flex min-w-[130px] items-center text-xs text-muted-foreground', inner));
      else inner.forEach((n) => frag.appendChild(typeof n === 'string' ? document.createTextNode(n) : n));
      return frag;
    },
    unitValueWithTotal: (o, ctx) => (value, name, item, index) => {
      const frag = document.createDocumentFragment();
      frag.appendChild(html('div', 'h-2.5 w-2.5 shrink-0 rounded-[2px] bg-(--color-bg)', [], { '--color-bg': `var(--color-${name})` }));
      frag.appendChild(document.createTextNode(String((ctx.config[name] && ctx.config[name].label) || name)));
      frag.appendChild(amount(value, o.unit));
      if (index === o.after) {
        const total = o.keys.reduce((acc, k) => acc + item.payload[k], 0);
        frag.appendChild(html('div', 'mt-1.5 flex basis-full items-center border-t pt-1.5 text-xs font-medium text-foreground', ['Total', amount(total, o.unit)]));
      }
      return frag;
    },
  };

  const CONTENT = {
    centerText: (o, ctx) => (props) => {
      const vb = props.viewBox;
      if (!(vb && 'cx' in vb && 'cy' in vb)) return null;
      const attrs = { x: vb.cx, y: vb.cy, 'text-anchor': 'middle' };
      if (o.dominantBaseline) attrs['dominant-baseline'] = o.dominantBaseline;
      return el('text', attrs, o.lines.map((line) => el('tspan', { x: vb.cx, y: (vb.cy || 0) + (line.dy || 0), class: line.className }, [tx(valueOf(line.value, ctx))])));
    },
  };

  const SHAPE = {
    activeRectangle: (o) => (props, ctx, S) => (props.index === o.index ? S.Rectangle(Object.assign({}, props, o.props, o.strokeFromPayload ? { stroke: props.payload[o.strokeFromPayload] } : {})) : S.Rectangle(props)),
    activeSector: (o) => (props, ctx, S) => {
      const outer = props.outerRadius || 0;
      return S.Sector(Object.assign({}, props, { outerRadius: props.index === idx(o.index, ctx) ? outer + o.grow : outer }));
    },
    activeSectorRing: (o) => (props, ctx, S) => {
      const outer = props.outerRadius || 0;
      if (props.index === idx(o.index, ctx)) {
        return S.h('g', {}, [
          S.Sector(Object.assign({}, props, { outerRadius: outer + o.grow })),
          S.Sector(Object.assign({}, props, { outerRadius: outer + o.ring[1], innerRadius: outer + o.ring[0] })),
        ]);
      }
      return S.Sector(Object.assign({}, props, { outerRadius: outer }));
    },
    payloadDot: (o) => (props) => I().Dot({ r: o.r, cx: props.cx, cy: props.cy, fill: props.payload.fill, stroke: props.payload.fill }),
    iconDot: (o, ctx) => (props) => {
      if (props.cx == null || props.cy == null) return null;
      const r = o.size;
      return { html: ctx.iconMarkup(o.icon, { x: props.cx - r / 2, y: props.cy - r / 2, width: r, height: r, fill: o.fill, stroke: o.stroke }) };
    },
    payloadText: (o) => (props) => el('text', { cx: props.cx, cy: props.cy, x: props.x, y: props.y, 'text-anchor': props.textAnchor, 'dominant-baseline': props.dominantBaseline, fill: o.fill }, [tx(props.payload[o.key])]),
    stackedTick: (o, ctx) => (props) => {
      const { svgProps, A } = I();
      const row = ctx.data[props.index];
      const y = typeof props.y === 'number' ? props.y : 0;
      const rest = Object.assign({}, props);
      ['x', 'y', 'textAnchor', 'index'].forEach((k) => delete rest[k]);
      const extra = A(svgProps(rest));
      // React passes the remaining tick props through as attributes, including the unknown ones
      if (rest.verticalAnchor != null) extra.verticalanchor = rest.verticalAnchor;
      if (rest.payload != null) extra.payload = '[object Object]';
      delete extra.class;
      const attrs = Object.assign({ x: props.x, y: y + (props.index === 0 ? o.firstDy : 0), 'text-anchor': props.textAnchor, 'font-size': 13, 'font-weight': 500 }, extra);
      if (rest.className) attrs.class = rest.className;
      return el('text', attrs, [
        el('tspan', {}, [tx(row[o.keys[0]])]),
        el('tspan', { class: 'fill-muted-foreground' }, [tx('/')]),
        el('tspan', {}, [tx(row[o.keys[1]])]),
        el('tspan', { x: props.x, dy: '1rem', 'font-size': 12, class: 'fill-muted-foreground' }, [tx(row[o.labelKey])]),
      ]);
    },
  };

  const ALL = Object.assign({}, VALUE, ROW, CONTENT, SHAPE);

  NS.formatters = {
    names: Object.keys(ALL),
    has: (name) => name in ALL,
    /* Resolve a {"$fn": ...} reference to a function bound to the chart context. */
    resolve(ref, ctx) {
      const make = ALL[ref.$fn];
      if (!make) throw new Error(`unknown chart formatter: ${ref.$fn}`);
      const f = make(ref, ctx);
      return SHAPE[ref.$fn] ? (props) => f(props, ctx, ctx.shapes) : f;
    },
    shape(ref, props, ctx) { return NS.formatters.resolve(ref, ctx)(props); },
    register(name, factory) { ALL[name] = factory; },
  };
})();

/* Polar charts for the chart renderer: PieChart, RadarChart and RadialBarChart (ports of Recharts' Pie, Radar,
   RadialBar, PolarGrid, PolarAngleAxis and PolarRadiusAxis, with their selectors). */
(function () {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});
  const I = () => NS.internal;
  const S = () => NS.surface;

  const CHART_DEFAULTS = {
    PieChart: { layout: 'centric', startAngle: 0, endAngle: 360, cx: '50%', cy: '50%', innerRadius: 0, outerRadius: '80%' },
    RadarChart: { layout: 'centric', startAngle: 90, endAngle: -270, cx: '50%', cy: '50%', innerRadius: 0, outerRadius: '80%' },
    RadialBarChart: { layout: 'radial', startAngle: 0, endAngle: 360, cx: '50%', cy: '50%', innerRadius: 0, outerRadius: '80%' },
  };
  const ANGLE_DEFAULTS = { allowDecimals: false, allowDuplicatedCategory: false, allowDataOverflow: false, angle: 0, axisLine: true, axisLineType: 'polygon', hide: false, label: false, niceTicks: 'auto', orientation: 'outer', reversed: false, scale: 'auto', tick: true, tickLine: true, tickSize: 8, type: 'auto' };
  const RADIUS_DEFAULTS = { allowDataOverflow: false, allowDecimals: false, allowDuplicatedCategory: true, angle: 0, axisLine: true, hide: false, niceTicks: 'auto', label: false, orientation: 'right', reversed: false, scale: 'auto', stroke: '#ccc', tick: true, tickCount: 5, tickLine: true, type: 'auto' };

  function buildPolar(chart) {
    const L = I(), SU = S();
    const { getPercentValue, getValue, resolveAxis, bandSizeOfAxis, isNil, polarToCartesian, mathSign } = L;
    const spec = chart.spec, ctx = chart.ctx;
    const W = chart.width, H = chart.height;
    const chartName = spec.component;
    const props = Object.assign({}, CHART_DEFAULTS[chartName], SU.resolveProps(spec.props, ctx));
    const layout = props.layout;
    const margin = SU.margins(props);
    const data = ctx.data;
    const items = spec.series.map((s, idx) => {
      const p = Object.assign({}, SU.SERIES_DEFAULTS[s.component], SU.resolveProps(s, ctx));
      return { component: s.component, props: p, dataKey: p.dataKey, stackId: p.stackId == null ? undefined : String(p.stackId), id: `${s.component.toLowerCase()}-${idx}`, spec: s, index: idx, data: s.component === 'Pie' ? (p.data || data) : data, cells: s.cells };
    });
    const legend = spec.legend ? SU.buildLegendModel(spec, items, ctx, margin, W, H) : null;
    const legendBox = legend ? chart.measureLegend(legend) : null;
    const off = { left: margin.left, right: margin.right, top: margin.top, bottom: margin.bottom };
    if (legend && legendBox) {
      const { layout: ll, align, verticalAlign } = legend.settings;
      if ((ll === 'vertical' || (ll === 'horizontal' && verticalAlign === 'middle')) && align !== 'center') off[align] += legendBox.width || 0;
      else if ((ll === 'horizontal' || (ll === 'vertical' && align === 'center')) && verticalAlign !== 'middle') off[verticalAlign] += legendBox.height || 0;
    }
    off.width = Math.max(W - off.left - off.right, 0);
    off.height = Math.max(H - off.top - off.bottom, 0);
    const maxRadius = Math.min(Math.abs(W - off.left - off.right), Math.abs(H - off.top - off.bottom)) / 2;
    const innerRadius = getPercentValue(props.innerRadius, maxRadius, 0);
    const outerRadius = getPercentValue(props.outerRadius, maxRadius, maxRadius * 0.8);
    const viewBox = { cx: getPercentValue(props.cx, W, W / 2), cy: getPercentValue(props.cy, H, H / 2), innerRadius, outerRadius, startAngle: props.startAngle, endAngle: props.endAngle, clockWise: false };
    const m = { kind: 'polar', layout, chartName, offset: off, width: W, height: H, items, legend, legendBox, margin, data, polarViewBox: viewBox, props };

    if (chartName === 'PieChart') {
      items.forEach((it) => { it.sectors = pieSectors(it, off); });
      m.tooltipEventType = 'item';
      return m;
    }
    // polar axes
    const angleSpec = spec.axes.find((a) => a.component === 'PolarAngleAxis');
    const radiusSpec = spec.axes.find((a) => a.component === 'PolarRadiusAxis');
    const angleAxis = Object.assign({ axisType: 'angleAxis' }, ANGLE_DEFAULTS, angleSpec ? SU.resolveProps(angleSpec, ctx) : { implicit: true }, { allowDuplicatedCategory: false });
    const radiusAxis = Object.assign({ axisType: 'radiusAxis' }, RADIUS_DEFAULTS, radiusSpec ? SU.resolveProps(radiusSpec, ctx) : { implicit: true });
    if (!angleSpec) { angleAxis.tickCount = undefined; }
    [angleAxis, radiusAxis].forEach((ax) => {
      if (ax.type === 'auto') ax.type = L.isCategoricalAxis(layout, ax.axisType) ? 'category' : 'number';
      ax.tickFormatterFn = SU.fn(ax.tickFormatter, ctx);
    });
    angleAxis.range = [props.startAngle, props.endAngle];
    radiusAxis.range = [innerRadius, outerRadius];
    const visible = items.filter((i) => !i.props.hide);
    const axisCtx = { layout, data, items: visible, stackGroups: undefined, stackOffset: 'none', chartName, hasBar: items.some((i) => i.component === 'RadialBar'), polar: true };
    resolveAxis(angleAxis, axisCtx);
    resolveAxis(radiusAxis, axisCtx);
    // PolarAngleAxis keeps one tick per angle
    const seen = new Map();
    angleAxis.ticks_.forEach((t) => { const k = (t.coordinate + 360) % 360; if (!seen.has(k)) seen.set(k, t); });
    angleAxis.uniqueTicks = Array.from(seen.values());
    m.angleAxis = angleAxis; m.radiusAxis = radiusAxis;
    m.tooltipAxis = layout === 'centric' ? angleAxis : radiusAxis;
    m.tooltipTicks = m.tooltipAxis.tooltipTicks;

    if (chartName === 'RadarChart') {
      const bandSize = L.isCategoricalAxis(layout, 'radiusAxis') ? bandSizeOfAxis(radiusAxis, radiusAxis.ticks_, false) : bandSizeOfAxis(angleAxis, angleAxis.ticks_, false);
      items.forEach((it) => {
        const angleBand = angleAxis.type !== 'number' ? (bandSize || 0) : 0;
        it.points = data.map((entry, i) => {
          const name = getValue(entry, angleAxis.dataKey, i);
          const value = getValue(entry, it.dataKey);
          const sc = angleAxis.scale(name);
          const angle = (sc == null ? 0 : sc) + angleBand;
          const pv = Array.isArray(value) ? value[value.length - 1] : value;
          const r = isNil(pv) ? 0 : radiusAxis.scale(pv);
          const radius = r == null ? 0 : r;
          return Object.assign(polarToCartesian(viewBox.cx, viewBox.cy, radius, angle), { name, value, cx: viewBox.cx, cy: viewBox.cy, radius, angle, payload: entry });
        });
      });
      return m;
    }

    // RadialBarChart
    const bars = visible.filter((i) => i.component === 'RadialBar');
    const catAxis = L.isCategoricalAxis(layout, 'radiusAxis') ? radiusAxis : angleAxis;
    const numAxis = catAxis === radiusAxis ? angleAxis : radiusAxis;
    const groups = {};
    bars.filter((b) => b.stackId != null).forEach((b) => { (groups[b.stackId] = groups[b.stackId] || []).push(b); });
    const sizeList = Object.keys(groups).map((sid) => ({ stackId: sid, dataKeys: groups[sid].map((b) => b.dataKey), barSize: groups[sid][0].props.barSize != null ? groups[sid][0].props.barSize : props.barSize }))
      .concat(bars.filter((b) => b.stackId == null).map((b) => ({ stackId: undefined, dataKeys: [b.dataKey].filter((k) => k != null), barSize: b.props.barSize != null ? b.props.barSize : props.barSize })));
    const barBand = bandSizeOfAxis(catAxis, catAxis.itemTicks, true);
    const barBandSize = (barBand != null ? barBand : props.maxBarSize) || 0;
    const axisBand = bandSizeOfAxis(catAxis, catAxis.itemTicks, false);
    let positions = SU.barPositionList(props.barGap != null ? props.barGap : 4, props.barCategoryGap != null ? props.barCategoryGap : '10%', barBandSize !== axisBand ? barBandSize : axisBand, sizeList, props.maxBarSize);
    if (barBandSize !== axisBand && positions) positions = positions.map((p) => Object.assign({}, p, { position: Object.assign({}, p.position, { offset: p.position.offset - barBandSize / 2 }) }));
    // stacks (by the categorical axis' tooltip value, as combineDisplayedStackedData)
    let stackGroups;
    const stacked = bars.filter((b) => b.stackId != null);
    if (stacked.length) {
      stackGroups = {};
      const rows = data.map((entry) => { const row = {}; stacked.forEach((it) => { row[it.id] = getValue(entry, it.dataKey, 0); }); return row; });
      Object.keys(groups).forEach((sid) => { stackGroups[sid] = { stackedData: NS.d3.stack(rows, groups[sid].map((b) => b.id), (d, k) => Number(getValue(d, k, 0)), props.stackOffset || 'none') }; });
    }
    const baseValue = SU.baseValueOfBar(numAxis);
    bars.forEach((it) => {
      const pos = positions && positions.find((p) => p.stackId === it.stackId && it.dataKey != null && p.dataKeys.includes(it.dataKey));
      const stackedData = stackGroups && it.stackId != null ? stackGroups[it.stackId].stackedData.find((sd) => sd.key === it.id) : undefined;
      const stackedDomain = stackedData ? numAxis.scale.domain() : null;
      if (!pos) { it.sectors = []; return; }
      it.sectors = data.map((entry, index) => {
        let value = stackedData ? SU.truncateByDomain([stackedData[index][0], stackedData[index][1]], stackedDomain) : getValue(entry, it.dataKey);
        if (!stackedData && !Array.isArray(value)) value = [baseValue, value];
        let innerR, outerR, startA, endA, bg;
        if (layout === 'radial') {
          const s = angleAxis.scale(value[0]), e = angleAxis.scale(value[1]);
          startA = s == null ? props.startAngle : s;
          endA = e == null ? props.endAngle : e;
          const t = radiusAxis.type === 'category' ? radiusAxis.itemTicks[index] : null;
          innerR = t ? t.coordinate + pos.position.offset : null;
          if (innerR != null) {
            outerR = innerR + pos.position.size;
            const minPointSize = it.props.minPointSize || 0;
            const delta = endA - startA;
            if (Math.abs(minPointSize) > 0 && Math.abs(delta) < Math.abs(minPointSize)) endA += mathSign(delta || minPointSize) * (Math.abs(minPointSize) - Math.abs(delta));
            bg = { background: { cx: viewBox.cx, cy: viewBox.cy, innerRadius: innerR, outerRadius: outerR, startAngle: props.startAngle, endAngle: props.endAngle } };
          }
        }
        return Object.assign({}, entry, bg, { payload: entry, value: stackedData ? value : value[1], cx: viewBox.cx, cy: viewBox.cy, innerRadius: innerR, outerRadius: outerR, startAngle: startA, endAngle: endA }, it.cells && it.cells[index] ? it.cells[index] : {});
      });
    });
    return m;
  }

  function pieSectors(it, offset) {
    const { getPercentValue, getValue, isNum, mathSign, polarToCartesian } = I();
    const p = it.props;
    const data = it.data || [];
    const minAngle = Math.abs(p.minAngle);
    const sign0 = mathSign(p.endAngle - p.startAngle);
    const deltaAngle = sign0 * Math.min(Math.abs(p.endAngle - p.startAngle), 360);
    const absDelta = Math.abs(deltaAngle);
    const paddingAngle = data.length <= 1 ? 0 : (p.paddingAngle || 0);
    const notZero = data.filter((e) => getValue(e, p.dataKey, 0) !== 0).length;
    const totalPadding = (absDelta >= 360 ? notZero : notZero - 1) * paddingAngle;
    const realTotalAngle = absDelta - notZero * minAngle - totalPadding;
    const sum = data.reduce((r, e) => { const v = getValue(e, p.dataKey, 0); return r + (isNum(v) ? v : 0); }, 0);
    if (!(sum > 0)) return [];
    const maxPieRadius = Math.min(offset.width, offset.height) / 2;
    const presentation = I().svgProps(S().seriesSvgProps(it));
    let prev;
    return data.map((entry, i) => {
      const val = getValue(entry, p.dataKey, 0);
      const name = getValue(entry, p.nameKey, i);
      const coordinate = {
        cx: offset.left + getPercentValue(p.cx, offset.width, offset.width / 2),
        cy: offset.top + getPercentValue(p.cy, offset.height, offset.height / 2),
        innerRadius: getPercentValue(p.innerRadius, maxPieRadius, 0),
        outerRadius: getPercentValue(p.outerRadius, maxPieRadius, maxPieRadius * 0.8),
        maxRadius: p.maxRadius || Math.sqrt(offset.width * offset.width + offset.height * offset.height) / 2,
      };
      const percent = (isNum(val) ? val : 0) / sum;
      const withCell = Object.assign({}, entry, it.cells && it.cells[i] ? it.cells[i] : {});
      const color = withCell != null && typeof withCell.fill === 'string' ? withCell.fill : p.fill;
      const start = i ? prev.endAngle + mathSign(deltaAngle) * paddingAngle * (val !== 0 ? 1 : 0) : p.startAngle;
      const end = start + mathSign(deltaAngle) * ((val !== 0 ? minAngle : 0) + percent * realTotalAngle);
      const midAngle = (start + end) / 2;
      const middleRadius = (coordinate.innerRadius + coordinate.outerRadius) / 2;
      const tooltipPayload = [{ name, value: val, payload: withCell, dataKey: p.dataKey, type: p.tooltipType, color, fill: color }];
      prev = Object.assign({}, presentation, { percent, cornerRadius: typeof p.cornerRadius === 'string' ? parseFloat(p.cornerRadius) : p.cornerRadius, name, tooltipPayload, midAngle, middleRadius, tooltipPosition: polarToCartesian(coordinate.cx, coordinate.cy, middleRadius, midAngle) }, withCell, coordinate, { value: val, dataKey: p.dataKey, startAngle: start, endAngle: end, payload: withCell, paddingAngle: mathSign(deltaAngle) * paddingAngle });
      return prev;
    });
  }

  /* ---------------------------------------------------------------- rendering */
  function renderPolar(chart, m, active) {
    const SU = S();
    const layers = SU.newLayers();
    const z0 = [];
    const labelsLater = [];
    const spec = chart.spec;
    if (m.chartName === 'PieChart') {
      m.items.forEach((it) => renderPie(chart, m, it, layers, labelsLater, active));
    } else {
      // render order follows the upstream JSX: grid, series, axes
      if (spec.grid) layers[SU.Z.grid].push(renderPolarGrid(chart, m));
      m.items.forEach((it) => {
        if (it.props.hide) return;
        if (it.component === 'Radar') renderRadar(chart, m, it, layers, labelsLater, active);
        else if (it.component === 'RadialBar') renderRadialBar(chart, m, it, layers, labelsLater, active);
      });
      spec.axes.forEach((a) => {
        if (a.component === 'PolarAngleAxis' && !m.angleAxis.hide) renderAngleAxis(chart, m, layers);
        if (a.component === 'PolarRadiusAxis' && !m.radiusAxis.hide) renderRadiusAxis(chart, m, layers, labelsLater);
      });
    }
    if (active && active.cursor && spec.tooltip && spec.tooltip.cursor !== false && m.layout === 'centric' && m.chartName !== 'PieChart') {
      const L = I();
      const coord = active.coordinate;
      const a = L.polarToCartesian(coord.cx, coord.cy, coord.innerRadius, coord.angle), b = L.polarToCartesian(coord.cx, coord.cy, coord.outerRadius, coord.angle);
      const extra = typeof spec.tooltip.cursor === 'object' ? L.svgProps(spec.tooltip.cursor) : {};
      layers[SU.Z.cursorLine].push(L.Curve(Object.assign({ stroke: '#ccc', pointerEvents: 'none', width: m.offset.width, height: m.offset.height, points: [a, b] }, extra, { className: 'recharts-tooltip-cursor' })));
    }
    labelsLater.forEach((l) => layers[SU.Z.label].push(l));
    return { layers, z0 };
  }

  function pieLabelContextEntries(sectors) {
    return sectors.map((e) => ({ value: e.value, payload: e.payload, clockWise: false, parentViewBox: undefined, viewBox: { cx: e.cx, cy: e.cy, innerRadius: e.innerRadius, outerRadius: e.outerRadius, startAngle: e.startAngle, endAngle: e.endAngle, clockWise: false }, fill: e.fill }));
  }

  function renderPie(chart, m, it, layers, labelsLater, active) {
    const L = I(), SU = S();
    const { h, Shape, svgProps, Text, Curve, polarToCartesian, getValue } = L;
    const p = it.props;
    const sectors = it.sectors || [];
    const kids = sectors.map((entry, i) => {
      if (entry.startAngle === 0 && entry.endAngle === 0 && sectors.length !== 1) return null;
      const isActive = !!active && active.itemIndex === i && active.graphicalItem === it.id;
      const sp = Object.assign({}, entry, { stroke: entry.stroke, tabIndex: -1, 'data-recharts-item-index': i, 'data-recharts-item-id': `recharts-pie-${chart.uid}-${it.index}` });
      const props = Object.assign({}, svgProps(sp), { cx: entry.cx, cy: entry.cy, innerRadius: entry.innerRadius, outerRadius: entry.outerRadius, startAngle: entry.startAngle, endAngle: entry.endAngle, cornerRadius: entry.cornerRadius, index: i, isActive, payload: entry.payload });
      return h('g', { class: 'recharts-layer recharts-pie-sector', tabindex: -1 }, [Shape('sector', p.shape || null, props, chart.ctx)]);
    });
    const inner = [h('g', { class: 'recharts-layer' }, kids)];
    // label prop
    if (p.label) {
      const offsetRadius = (typeof p.label === 'object' && typeof p.label.offsetRadius === 'number' && p.label.offsetRadius) || 20;
      const pieProps = svgProps(SU.seriesSvgProps(it));
      const custom = typeof p.label === 'object' && !p.label.$fn ? svgProps(p.label) : {};
      const customLine = typeof p.labelLine === 'object' ? svgProps(p.labelLine) : {};
      const groups = sectors.map((entry, i) => {
        const mid = (entry.startAngle + entry.endAngle) / 2;
        const endPoint = polarToCartesian(entry.cx, entry.cy, entry.outerRadius + offsetRadius, mid);
        const anchor = endPoint.x > entry.cx ? 'start' : endPoint.x < entry.cx ? 'end' : 'middle';
        const labelProps = Object.assign({}, pieProps, svgProps(entry), { stroke: 'none' }, custom, { index: i, textAnchor: anchor }, endPoint);
        const lineProps = Object.assign({}, pieProps, svgProps(entry), { fill: 'none', stroke: entry.fill }, customLine, { index: i, points: [polarToCartesian(entry.cx, entry.cy, entry.outerRadius, mid), endPoint] });
        let labelEl;
        if (p.label.$fn) labelEl = SU.fn(p.label, chart.ctx)(Object.assign({}, labelProps, { payload: entry.payload, cx: entry.cx, cy: entry.cy }));
        else labelEl = Text(Object.assign({}, labelProps, { alignmentBaseline: 'middle', className: ['recharts-pie-label-text', typeof p.label === 'object' ? p.label.className : ''].filter(Boolean).join(' ') }), getValue(entry, p.dataKey));
        const line = p.labelLine ? Curve(Object.assign({}, lineProps, { type: 'linear', className: ['recharts-pie-label-line', typeof p.labelLine === 'object' ? p.labelLine.className : ''].filter(Boolean).join(' ') })) : null;
        labelsLater.push(h('g', { class: 'recharts-layer' }, [line, labelEl]));
        return null;
      });
      void groups;
      inner.push(h('g', { class: 'recharts-layer recharts-pie-labels' }, []));
    }
    const ctxEntries = pieLabelContextEntries(sectors);
    (it.spec.labelList || []).forEach((ll) => labelsLater.push(SU.LabelList(ll, ctxEntries, chart.ctx)));
    (it.spec.labels || []).forEach((lb) => {
      const el = SU.Label(Object.assign({}, SU.resolveProps(lb, chart.ctx), { viewBox: m.polarViewBox }), chart.ctx);
      if (el) inner.push(el);
    });
    layers[SU.Z.area].push(h('g', { class: 'recharts-layer recharts-pie', tabindex: p.rootTabIndex }, inner));
  }

  function renderPolarGrid(chart, m) {
    const L = I(), SU = S();
    const { h, svgProps, A, polarToCartesian } = L;
    const g = Object.assign({ gridType: 'polygon', radialLines: true }, SU.resolveProps(chart.spec.grid, chart.ctx));
    const vb = m.polarViewBox;
    const polarAngles = Array.isArray(g.polarAngles) ? g.polarAngles : m.angleAxis.ticks_.map((t) => t.coordinate);
    const polarRadius = Array.isArray(g.polarRadius) ? g.polarRadius : m.radiusAxis.ticks_.map((t) => t.coordinate);
    if (!(vb.outerRadius > 0)) return null;
    const inputs = Object.assign({}, g);
    ['gridType', 'radialLines', 'angleAxisId', 'radiusAxisId', 'cx', 'cy', 'innerRadius', 'outerRadius', 'polarAngles', 'polarRadius', 'zIndex', 'component'].forEach((k) => delete inputs[k]);
    const props = Object.assign({ cx: vb.cx, cy: vb.cy, innerRadius: vb.innerRadius, outerRadius: vb.outerRadius }, inputs);
    const polygonD = (radius) => {
      let d = '';
      polarAngles.forEach((angle, i) => { const pt = polarToCartesian(props.cx, props.cy, radius, angle); d += (i ? 'L ' : 'M ') + pt.x + ',' + pt.y; });
      return d + 'Z';
    };
    const shapeEl = (radius, fill) => {
      const base = Object.assign({ stroke: '#ccc', fill: 'none' }, svgProps(Object.assign({}, props, { radius })));
      if (fill !== undefined) base.fill = fill;
      const cls = base.className; delete base.className;
      if (g.gridType === 'circle') {
        const a = A(base);
        return h('circle', Object.assign(a, { class: ['recharts-polar-grid-concentric-circle', cls].filter(Boolean).join(' '), cx: props.cx, cy: props.cy, r: radius }));
      }
      return h('path', Object.assign(A(base), { class: ['recharts-polar-grid-concentric-polygon', cls].filter(Boolean).join(' '), d: polygonD(radius) }));
    };
    const concentric = [];
    if (polarRadius && polarRadius.length) {
      const maxR = Math.max(...polarRadius);
      if (props.fill && props.fill !== 'none') concentric.push(shapeEl(maxR, undefined));
      polarRadius.forEach((r) => concentric.push(shapeEl(r, 'none')));
    }
    const kids = [h('g', { class: 'recharts-polar-grid-concentric' }, concentric)];
    if (polarAngles && polarAngles.length && g.radialLines) {
      const lp = Object.assign({ stroke: '#ccc' }, svgProps(props));
      kids.push(h('g', { class: 'recharts-polar-grid-angle' }, polarAngles.map((angle) => {
        const s = polarToCartesian(props.cx, props.cy, props.innerRadius, angle), e = polarToCartesian(props.cx, props.cy, props.outerRadius, angle);
        return h('line', Object.assign(A(lp), { x1: s.x, y1: s.y, x2: e.x, y2: e.y }));
      })));
    }
    return h('g', { class: 'recharts-polar-grid' }, kids);
  }

  function renderAngleAxis(chart, m, layers) {
    const L = I(), SU = S();
    const { h, svgProps, A, Text, Polygon, Dot, polarToCartesian } = L;
    const ax = m.angleAxis;
    const vb = m.polarViewBox;
    const ticks = ax.uniqueTicks;
    if (!ticks || !ticks.length) return;
    const userProps = SU.resolveProps(chart.spec.axes.find((a) => a.component === 'PolarAngleAxis'), chart.ctx);
    const props = Object.assign({}, ANGLE_DEFAULTS, userProps, vb, { radius: vb.outerRadius });
    const base = svgProps(props);
    delete base.className;
    const children = [];
    if (props.axisLine) {
      const lp = Object.assign({}, base, { fill: 'none' }, typeof props.axisLine === 'object' ? svgProps(props.axisLine) : {});
      if (props.axisLineType === 'circle') children.push(Dot(Object.assign({}, lp, { className: 'recharts-polar-angle-axis-line', cx: vb.cx, cy: vb.cy, r: vb.outerRadius })));
      else children.push(Polygon(Object.assign({}, lp, { className: 'recharts-polar-angle-axis-line', points: ticks.map((t) => polarToCartesian(vb.cx, vb.cy, vb.outerRadius, t.coordinate)) })));
    }
    const eps = 1e-5, COS45 = Math.cos(45 * Math.PI / 180);
    const custom = typeof props.tick === 'object' && !props.tick.$fn ? svgProps(props.tick) : {};
    const items = ticks.map((entry, i) => {
      const size = props.tickSize || 8;
      const p1 = polarToCartesian(vb.cx, vb.cy, vb.outerRadius, entry.coordinate);
      const p2 = polarToCartesian(vb.cx, vb.cy, vb.outerRadius + (props.orientation === 'inner' ? -1 : 1) * size, entry.coordinate);
      const line = { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y };
      const cos = Math.cos(-entry.coordinate * Math.PI / 180), sin = Math.sin(-entry.coordinate * Math.PI / 180);
      const textAnchor = cos > eps ? (props.orientation === 'outer' ? 'start' : 'end') : cos < -eps ? (props.orientation === 'outer' ? 'end' : 'start') : 'middle';
      const verticalAnchor = Math.abs(cos) <= COS45 ? (sin > 0 ? 'start' : 'end') : 'middle';
      const tp = Object.assign({}, base, { textAnchor, verticalAnchor, stroke: 'none', fill: props.stroke }, custom, { index: i, payload: entry, x: line.x2, y: line.y2 });
      const value = ax.tickFormatterFn ? ax.tickFormatterFn(entry.value, i) : entry.value;
      let textEl = null;
      if (props.tick) {
        if (props.tick.$fn) textEl = SU.fn(props.tick, chart.ctx)(tp);
        else textEl = Text(Object.assign({}, tp, { className: 'recharts-polar-angle-axis-tick-value' }), value);
      }
      return h('g', { class: ['recharts-layer recharts-polar-angle-axis-tick', typeof props.tick === 'object' && props.tick.className].filter(Boolean).join(' ') }, [
        props.tickLine ? h('line', Object.assign(A(Object.assign({}, base, { fill: 'none' }, typeof props.tickLine === 'object' ? svgProps(props.tickLine) : {})), { class: 'recharts-polar-angle-axis-tick-line' }, line)) : null,
        textEl,
      ]);
    });
    children.push(h('g', { class: 'recharts-layer recharts-polar-angle-axis-ticks' }, items));
    layers[SU.Z.axis].push(h('g', { class: ['recharts-layer recharts-polar-angle-axis angleAxis', userProps.className].filter(Boolean).join(' ') }, children));
  }

  function renderRadiusAxis(chart, m, layers, labelsLater) {
    const L = I(), SU = S();
    const { h, svgProps, A, Text, polarToCartesian } = L;
    const ax = m.radiusAxis;
    const vb = m.polarViewBox;
    const ticks = ax.ticks_;
    if (!ticks || !ticks.length) return;
    const userProps = SU.resolveProps(chart.spec.axes.find((a) => a.component === 'PolarRadiusAxis'), chart.ctx);
    const props = Object.assign({}, RADIUS_DEFAULTS, userProps, vb);
    const angle = props.angle;
    const kids = [];
    if (props.axisLine) {
      const ext = ticks.reduce((r, e) => [Math.min(r[0], e.coordinate), Math.max(r[1], e.coordinate)], [Infinity, -Infinity]);
      const p0 = polarToCartesian(vb.cx, vb.cy, ext[0], angle), p1 = polarToCartesian(vb.cx, vb.cy, ext[1], angle);
      const others = Object.assign({}, props); delete others.cx; delete others.cy; delete others.angle; delete others.axisLine;
      const lp = Object.assign({}, svgProps(others), { fill: 'none' }, typeof props.axisLine === 'object' ? svgProps(props.axisLine) : {}, { x1: p0.x, y1: p0.y, x2: p1.x, y2: p1.y });
      delete lp.className;
      kids.push(h('line', Object.assign({ class: 'recharts-polar-radius-axis-line' }, A(lp))));
    }
    if (props.tick) {
      const textAnchor = { left: 'end', right: 'start' }[props.orientation] || 'middle';
      const others = Object.assign({}, props); delete others.angle; delete others.tickFormatter; delete others.stroke; delete others.tick;
      const axisProps = svgProps(others);
      delete axisProps.className;
      const custom = typeof props.tick === 'object' ? svgProps(props.tick) : {};
      kids.push(h('g', { class: 'recharts-layer recharts-polar-radius-axis-ticks' }, ticks.map((entry, i) => {
        const c = polarToCartesian(vb.cx, vb.cy, entry.coordinate, props.angle);
        const tp = Object.assign({ textAnchor, transform: `rotate(${90 - angle}, ${c.x}, ${c.y})` }, axisProps, { stroke: 'none', fill: props.stroke }, custom, { index: i }, c, { payload: entry });
        return h('g', { class: ['recharts-layer recharts-polar-radius-axis-tick', typeof props.tick === 'object' && props.tick.className].filter(Boolean).join(' ') }, [Text(Object.assign({}, tp, { className: 'recharts-polar-radius-axis-tick-value' }), ax.tickFormatterFn ? ax.tickFormatterFn(entry.value, i) : entry.value)]);
      })));
    }
    const maxT = ticks.reduce((a, b) => ((b.coordinate || 0) > (a.coordinate || 0) ? b : a));
    const minT = ticks.reduce((a, b) => ((b.coordinate || 0) < (a.coordinate || 0) ? b : a));
    const labelVB = { cx: vb.cx, cy: vb.cy, startAngle: angle, endAngle: angle, innerRadius: minT.coordinate || 0, outerRadius: maxT.coordinate || 0, clockWise: false };
    const spec = chart.spec.axes.find((a) => a.component === 'PolarRadiusAxis');
    (spec.labels || []).forEach((lb) => {
      const el = SU.Label(Object.assign({}, SU.resolveProps(lb, chart.ctx), { viewBox: labelVB }), chart.ctx);
      if (el) kids.push(el);
    });
    layers[SU.Z.axis].push(h('g', { class: ['recharts-layer recharts-polar-radius-axis radiusAxis', userProps.className].filter(Boolean).join(' ') }, kids));
  }

  function renderRadar(chart, m, it, layers, labelsLater, active) {
    const L = I(), SU = S();
    const { h, svgProps, Polygon } = L;
    const p = it.props;
    const base = svgProps(SU.seriesSvgProps(it));
    const polygon = Polygon(Object.assign({}, base, { id: `recharts-radar-${chart.uid}-${it.index}`, points: it.points, connectNulls: p.connectNulls }));
    layers[SU.Z.area].push(h('g', { class: ['recharts-layer recharts-radar', p.className].filter(Boolean).join(' ') }, [
      h('g', { class: 'recharts-layer recharts-radar-polygon' }, [polygon]),
    ]));
    const dotBase = Object.assign({}, base);
    delete dotBase.id;
    SU.dotsLayer(chart, it, it.points, 'recharts-radar-dots', 'recharts-radar-dot', dotBase, layers);
    SU.activePointLayer(chart, it, it.points, SU.legendColor(p.stroke, p.fill), active, layers);
  }

  function renderRadialBar(chart, m, it, layers, labelsLater, active) {
    const L = I(), SU = S();
    const { h, svgProps, Shape } = L;
    const p = it.props;
    const sectors = it.sectors || [];
    const others = SU.seriesSvgProps(it);
    delete others.cornerRadius; delete others.id;
    const baseProps = svgProps(others);
    delete baseProps.className;
    const kids = [];
    if (p.background) {
      kids.push(h('g', { class: 'recharts-layer recharts-radial-bar-background' }, []));
      const bgProps = typeof p.background === 'object' ? svgProps(p.background) : {};
      sectors.forEach((entry, i) => {
        if (!entry.background) return;
        const rest = Object.assign({}, entry); delete rest.value; delete rest.background;
        const sp = Object.assign({ cornerRadius: p.cornerRadius }, rest, { fill: '#eee' }, entry.background, bgProps, { index: i });
        const attrs = Object.assign({}, svgProps(sp), { cx: sp.cx, cy: sp.cy, innerRadius: sp.innerRadius, outerRadius: sp.outerRadius, startAngle: sp.startAngle, endAngle: sp.endAngle, cornerRadius: sp.cornerRadius, className: 'recharts-radial-bar-background-sector ' + String(bgProps.className), isActive: false });
        layers[SU.Z.barBackground].push(Shape('sector', typeof p.background === 'object' ? p.background : null, attrs, chart.ctx));
      });
    }
    const sectorEls = sectors.map((entry, i) => {
      const sp = Object.assign({}, baseProps, { cornerRadius: p.cornerRadius }, entry);
      const attrs = Object.assign({}, svgProps(sp), { cx: entry.cx, cy: entry.cy, innerRadius: entry.innerRadius, outerRadius: entry.outerRadius, startAngle: entry.startAngle, endAngle: entry.endAngle, cornerRadius: typeof p.cornerRadius === 'string' ? parseInt(p.cornerRadius, 10) : p.cornerRadius, forceCornerRadius: p.forceCornerRadius, cornerIsExternal: p.cornerIsExternal, className: 'recharts-radial-bar-sector ' + String(entry.className), isActive: false, index: i });
      return Shape('sector', p.shape || null, attrs, chart.ctx);
    });
    kids.push(h('g', { class: 'recharts-layer recharts-radial-bar-sectors' }, sectorEls));
    layers[SU.Z.bar].push(h('g', { class: ['recharts-layer recharts-area', p.className].filter(Boolean).join(' ') }, kids));
    const entries = sectors.map((s) => ({ value: s.value, payload: s.payload, parentViewBox: undefined, clockWise: false, viewBox: { cx: s.cx, cy: s.cy, innerRadius: s.innerRadius, outerRadius: s.outerRadius, startAngle: s.startAngle, endAngle: s.endAngle, clockWise: false }, fill: s.fill }));
    (it.spec.labelList || []).forEach((ll) => labelsLater.push(SU.LabelList(ll, entries, chart.ctx)));
  }

  NS.polar = { buildPolar, renderPolar };
})();

/* Chart surface: turns a chart spec plus the measured container size and the tooltip state into the element
   tree Recharts renders (svg.recharts-surface with its z-index layers), the tooltip model and the legend model.
   Cartesian charts (AreaChart, BarChart, LineChart) and polar charts (PieChart, RadarChart, RadialBarChart). */
(function () {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});
  const Z_LAYERS = [-100, -50, 100, 200, 300, 400, 500, 600, 1000, 1100, 1200, 2000];
  const Z = { grid: -100, barBackground: -50, area: 100, cursorRectangle: 200, bar: 300, line: 400, axis: 500, scatter: 600, activeBar: 1000, cursorLine: 1100, activeDot: 1200, label: 2000 };

  function I() { return NS.internal; }

  /* ---------------------------------------------------------------- function-valued props */
  function fn(ref, ctx) {
    if (ref && typeof ref === 'object' && ref.$fn) return NS.formatters.resolve(ref, ctx);
    return typeof ref === 'function' ? ref : undefined;
  }
  function stateValue(v, ctx) {
    if (v && typeof v === 'object' && !Array.isArray(v) && 'state' in v && Object.keys(v).length === 1) return ctx.state[v.state];
    return v;
  }
  /* Resolve {"state": name} references and $template strings against the chart state. */
  function resolveProps(obj, ctx) {
    const out = {};
    for (const k of Object.keys(obj || {})) {
      let v = stateValue(obj[k], ctx);
      if (v && typeof v === 'object' && v.$template) v = v.$template.replace(/\{(\w+)\}/g, (_, n) => ctx.state[n]);
      out[k] = v;
    }
    return out;
  }

  /* ---------------------------------------------------------------- shared helpers */
  function tooltipNameProp(name, dataKey) {
    if (name) return String(name);
    if (typeof dataKey === 'string') return dataKey;
    return undefined;
  }
  const legendColor = (stroke, fill) => (stroke && stroke !== 'none' ? stroke : fill);

  function viewBoxToTrapezoid(vb) {
    if (vb == null) return vb;
    if ('upperWidth' in vb) return vb;
    return { x: vb.x, y: vb.y, width: vb.width, height: vb.height, upperWidth: vb.width, lowerWidth: vb.width };
  }

  /* recharts/cartesian/getCartesianPosition */
  function cartesianPosition(viewBox, position, offset, parentViewBox, clamp) {
    const { x, y, height, upperWidth, lowerWidth } = viewBoxToTrapezoid(viewBox);
    const upperX = x, lowerX = x + (upperWidth - lowerWidth) / 2, middleX = (upperX + lowerX) / 2;
    const midHeightWidth = (upperWidth + lowerWidth) / 2, centerX = upperX + upperWidth / 2;
    const vSign = height >= 0 ? 1 : -1, vOff = vSign * offset, vEnd = vSign > 0 ? 'end' : 'start', vStart = vSign > 0 ? 'start' : 'end';
    const hSign = upperWidth >= 0 ? 1 : -1, hOff = hSign * offset, hEnd = hSign > 0 ? 'end' : 'start', hStart = hSign > 0 ? 'start' : 'end';
    const pv = parentViewBox;
    if (position === 'top') {
      const r = { x: upperX + upperWidth / 2, y: y - vOff, horizontalAnchor: 'middle', verticalAnchor: vEnd };
      if (clamp && pv) { r.height = Math.max(y - pv.y, 0); r.width = upperWidth; }
      return r;
    }
    if (position === 'bottom') {
      const r = { x: lowerX + lowerWidth / 2, y: y + height + vOff, horizontalAnchor: 'middle', verticalAnchor: vStart };
      if (clamp && pv) { r.height = Math.max(pv.y + pv.height - (y + height), 0); r.width = lowerWidth; }
      return r;
    }
    if (position === 'left') {
      const r = { x: middleX - hOff, y: y + height / 2, horizontalAnchor: hEnd, verticalAnchor: 'middle' };
      if (clamp && pv) { r.width = Math.max(r.x - pv.x, 0); r.height = height; }
      return r;
    }
    if (position === 'right') {
      const r = { x: middleX + midHeightWidth + hOff, y: y + height / 2, horizontalAnchor: hStart, verticalAnchor: 'middle' };
      if (clamp && pv) { r.width = Math.max(pv.x + pv.width - r.x, 0); r.height = height; }
      return r;
    }
    const size = clamp && pv ? { width: midHeightWidth, height } : {};
    const m = (o) => Object.assign(o, size);
    switch (position) {
      case 'insideLeft': return m({ x: middleX + hOff, y: y + height / 2, horizontalAnchor: hStart, verticalAnchor: 'middle' });
      case 'insideRight': return m({ x: middleX + midHeightWidth - hOff, y: y + height / 2, horizontalAnchor: hEnd, verticalAnchor: 'middle' });
      case 'insideTop': return m({ x: upperX + upperWidth / 2, y: y + vOff, horizontalAnchor: 'middle', verticalAnchor: vStart });
      case 'insideBottom': return m({ x: lowerX + lowerWidth / 2, y: y + height - vOff, horizontalAnchor: 'middle', verticalAnchor: vEnd });
      case 'insideTopLeft': return m({ x: upperX + hOff, y: y + vOff, horizontalAnchor: hStart, verticalAnchor: vStart });
      case 'insideTopRight': return m({ x: upperX + upperWidth - hOff, y: y + vOff, horizontalAnchor: hEnd, verticalAnchor: vStart });
      case 'insideBottomLeft': return m({ x: lowerX + hOff, y: y + height - vOff, horizontalAnchor: hStart, verticalAnchor: vEnd });
      case 'insideBottomRight': return m({ x: lowerX + lowerWidth - hOff, y: y + height - vOff, horizontalAnchor: hEnd, verticalAnchor: vEnd });
      default: return m({ x: centerX, y: y + height / 2, horizontalAnchor: 'middle', verticalAnchor: 'middle' });
    }
  }

  /* recharts/component/Label getAttrsOfPolarLabel */
  function polarLabelAttrs(vb, offset, position) {
    const { polarToCartesian } = I();
    const midAngle = (vb.startAngle + vb.endAngle) / 2;
    if (position === 'outside') {
      const p = polarToCartesian(vb.cx, vb.cy, vb.outerRadius + offset, midAngle);
      return { x: p.x, y: p.y, textAnchor: p.x >= vb.cx ? 'start' : 'end', verticalAnchor: 'middle' };
    }
    if (position === 'center') return { x: vb.cx, y: vb.cy, textAnchor: 'middle', verticalAnchor: 'middle' };
    if (position === 'centerTop') return { x: vb.cx, y: vb.cy, textAnchor: 'middle', verticalAnchor: 'start' };
    if (position === 'centerBottom') return { x: vb.cx, y: vb.cy, textAnchor: 'middle', verticalAnchor: 'end' };
    const r = (vb.innerRadius + vb.outerRadius) / 2;
    const p = polarToCartesian(vb.cx, vb.cy, r, midAngle);
    return { x: p.x, y: p.y, textAnchor: 'middle', verticalAnchor: 'middle' };
  }
  const isPolarVB = (vb) => vb != null && 'cx' in vb && I().isNum(vb.cx);

  let radialLineId = 0;
  /* Label: props as the Label component receives them. Returns an element or null. */
  function Label(rawProps, ctx) {
    const { h, txt, Text, svgProps, isNil, A, polarToCartesian, mathSign } = I();
    const props = Object.assign({ angle: 0, offset: 5, position: 'middle', textBreakAll: false }, I().stripUndef(rawProps));
    const vb = props.viewBox;
    if (!vb || (isNil(props.value) && isNil(props.children) && !props.content)) return null;
    if (props.content) {
      const f = fn(props.content, ctx);
      if (f) return f(Object.assign({}, props, { viewBox: vb }), ctx);
      return null;
    }
    let label = isNil(props.children) ? props.value : props.children;
    const formatter = fn(props.formatter, ctx);
    if (formatter) label = formatter(label);
    const attrs = svgProps(props);
    let pos;
    if (isPolarVB(vb)) {
      if (props.position === 'insideStart' || props.position === 'insideEnd' || props.position === 'end') {
        const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, clockWise } = vb;
        const radius = (innerRadius + outerRadius) / 2;
        const sign0 = mathSign(endAngle - startAngle);
        const deltaAngle = sign0 * Math.min(Math.abs(endAngle - startAngle), 360);
        const sign = deltaAngle >= 0 ? 1 : -1;
        let labelAngle, direction;
        if (props.position === 'insideStart') { labelAngle = startAngle + sign * props.offset; direction = clockWise; } else if (props.position === 'insideEnd') { labelAngle = endAngle - sign * props.offset; direction = !clockWise; } else { labelAngle = endAngle + sign * props.offset; direction = clockWise; }
        direction = deltaAngle <= 0 ? direction : !direction;
        const sp = polarToCartesian(cx, cy, radius, labelAngle);
        const ep = polarToCartesian(cx, cy, radius, labelAngle + (direction ? 1 : -1) * 359);
        const path = `M${sp.x},${sp.y}
    A${radius},${radius},0,1,${direction ? 0 : 1},
    ${ep.x},${ep.y}`;
        const id = isNil(props.id) ? `recharts-radial-line-${++radialLineId}` : props.id;
        const a = A(attrs);
        a['dominant-baseline'] = 'central';
        a.class = ['recharts-radial-bar-label', props.className].filter(Boolean).join(' ');
        delete a.classname;
        return h('text', a, [h('defs', {}, [h('path', { id, d: path })]), h('textPath', { 'xlink:href': '#' + id }, [txt(label)])]);
      }
      pos = polarLabelAttrs(vb, props.offset, props.position);
    } else {
      const parent = isPolarVB(props.parentViewBox) ? undefined : props.parentViewBox;
      const r = cartesianPosition(vb, props.position, props.offset, parent, true);
      pos = { x: r.x, y: r.y, textAnchor: r.horizontalAnchor, verticalAnchor: r.verticalAnchor };
      if (r.width !== undefined) pos.width = r.width;
      if (r.height !== undefined) pos.height = r.height;
    }
    const tp = Object.assign({ className: ['recharts-label', props.className || ''].join(' ').trim() }, attrs, pos);
    if (attrs.className) tp.className = attrs.className;
    const validAnchor = ['start', 'middle', 'end', 'inherit'].includes(attrs.textAnchor);
    tp.textAnchor = validAnchor ? attrs.textAnchor : pos.textAnchor;
    tp.breakAll = props.textBreakAll;
    // Text receives every svg prop of the label (x, y, width, height, offset, fontSize, fill ...)
    return Text(tp, label);
  }

  /* LabelList over entries ({x,y,width,height,value,payload,viewBox,parentViewBox,fill}). */
  function LabelList(listProps, entries, ctx) {
    const { h, isNil, svgProps, getValue } = I();
    if (!entries || !entries.length) return null;
    const lp = resolveProps(listProps, ctx);
    const { dataKey, valueAccessor } = lp;
    const others = Object.assign({}, lp);
    ['valueAccessor', 'dataKey', 'clockWise', 'id', 'textBreakAll', 'zIndex'].forEach((k) => delete others[k]);
    const kids = entries.map((entry, index) => {
      let value;
      if (isNil(dataKey)) {
        const v = Array.isArray(entry.value) ? entry.value[entry.value.length - 1] : entry.value;
        value = v === null || v === undefined || typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? v : undefined;
      } else {
        value = getValue(entry.payload, dataKey);
      }
      return Label(Object.assign({}, svgProps(entry), others, {
        fill: lp.fill != null ? lp.fill : entry.fill,
        parentViewBox: entry.parentViewBox,
        value,
        textBreakAll: lp.textBreakAll,
        viewBox: entry.viewBox,
        index,
        zIndex: 0,
      }), ctx);
    });
    void valueAccessor;
    return h('g', { class: 'recharts-layer recharts-label-list' }, kids);
  }

  /* ---------------------------------------------------------------- cartesian */
  const X_DEFAULTS = { allowDataOverflow: false, allowDecimals: true, allowDuplicatedCategory: true, angle: 0, axisLine: true, height: 30, hide: false, includeHidden: false, interval: 'preserveEnd', label: false, minTickGap: 5, mirror: false, orientation: 'bottom', padding: { left: 0, right: 0 }, reversed: false, scale: 'auto', tick: true, tickCount: 5, tickLine: true, tickSize: 6, type: 'category', niceTicks: 'auto', stroke: '#666', tickMargin: 2 };
  const Y_DEFAULTS = Object.assign({}, X_DEFAULTS, { orientation: 'left', padding: { top: 0, bottom: 0 }, type: 'number', width: 60 });
  delete Y_DEFAULTS.height;
  const IMPLICIT_X = { allowDataOverflow: false, allowDecimals: true, allowDuplicatedCategory: true, height: 30, hide: true, orientation: 'bottom', padding: { left: 0, right: 0 }, reversed: false, scale: 'auto', tick: true, tickCount: 5, type: 'category', interval: 'preserveEnd', minTickGap: 5, angle: 0, implicit: true };
  const IMPLICIT_Y = { allowDataOverflow: false, allowDecimals: true, allowDuplicatedCategory: true, domain: [0, 'auto'], hide: true, orientation: 'left', padding: { top: 0, bottom: 0 }, reversed: false, scale: 'auto', tick: true, tickCount: 5, type: 'number', width: 60, interval: 'preserveEnd', minTickGap: 5, angle: 0, implicit: true };

  const SERIES_DEFAULTS = {
    Bar: { activeBar: false, background: false, hide: false, label: false, legendType: 'rect', minPointSize: 0, zIndex: Z.bar },
    Area: { activeDot: true, connectNulls: false, dot: false, fill: '#3182bd', fillOpacity: 0.6, hide: false, legendType: 'line', stroke: '#3182bd', strokeWidth: 1, type: 'linear', label: false, zIndex: Z.area },
    Line: { activeDot: true, connectNulls: false, dot: true, fill: '#fff', hide: false, label: false, legendType: 'line', stroke: '#3182bd', strokeWidth: 1, zIndex: Z.line, type: 'linear' },
    Pie: { cx: '50%', cy: '50%', dataKey: 'value', endAngle: 360, fill: '#808080', hide: false, innerRadius: 0, label: false, labelLine: true, legendType: 'rect', minAngle: 0, nameKey: 'name', outerRadius: '80%', paddingAngle: 0, rootTabIndex: 0, startAngle: 0, stroke: '#fff', zIndex: Z.area },
    Radar: { activeDot: true, dot: false, hide: false, label: false, legendType: 'rect', zIndex: Z.area },
    RadialBar: { background: false, cornerIsExternal: false, cornerRadius: 0, forceCornerRadius: false, hide: false, label: false, legendType: 'rect', minPointSize: 0, zIndex: Z.bar },
  };
  /* Props a series component consumes that are not passed on to its SVG elements. */
  const SERIES_META = new Set(['component', 'labelList', 'labels', 'children', 'cells', 'shape', 'activeShape', 'dot', 'activeDot', 'label', 'labelLine', 'background', 'stackId', 'dataKey', 'type', 'zIndex', 'hide', 'legendType', 'connectNulls', 'baseValue', 'minPointSize', 'maxBarSize', 'barSize', 'activeBar', 'nameKey', 'xAxisId', 'yAxisId', 'isAnimationActive', 'animationDuration', 'animationBegin', 'animationEasing']);

  function margins(props, polar) {
    const m = props.margin;
    if (m == null) return { top: 5, right: 5, bottom: 5, left: 5 };
    return { top: m.top || 0, right: m.right || 0, bottom: m.bottom || 0, left: m.left || 0 };
  }

  /* Legend payload as Recharts collects it from the graphical items, sorted by value. */
  function legendPayload(spec, items, ctx) {
    const { getValue, sortByKey } = I();
    let out = [];
    items.forEach((it) => {
      const p = it.props;
      if (it.component === 'Bar') out.push({ inactive: p.hide, dataKey: p.dataKey, type: p.legendType, color: p.fill, value: tooltipNameProp(p.name, p.dataKey), payload: p });
      else if (it.component === 'Area' || it.component === 'Line' || it.component === 'Radar') out.push({ inactive: p.hide, dataKey: p.dataKey, type: p.legendType, color: legendColor(p.stroke, p.fill), value: tooltipNameProp(p.name, p.dataKey), payload: p });
      else if (it.component === 'Pie') {
        it.data.forEach((entry, i) => {
          const name = getValue(entry, p.nameKey, p.name);
          const cell = it.cells && it.cells[i];
          const color = cell && cell.fill ? cell.fill : (entry && typeof entry === 'object' && 'fill' in entry ? entry.fill : p.fill);
          out.push({ value: tooltipNameProp(name, p.dataKey), color, payload: entry, type: p.legendType });
        });
      } else if (it.component === 'RadialBar') {
        ctx.data.forEach((entry) => out.push({ type: p.legendType, value: entry.name, color: entry.fill, payload: entry }));
      }
    });
    out = sortByKey(out, 'value');
    return out;
  }

  function buildCartesian(chart) {
    const L = I();
    const { h, isNum, getValue, range, Text, svgProps, A, Rectangle, Curve, Dot, Shape, getTicks, resolveAxis, bandSizeOfAxis, getPercentValue, round, isNil } = L;
    const spec = chart.spec, ctx = chart.ctx;
    const W = chart.width, H = chart.height;
    const props = resolveProps(spec.props, ctx);
    const chartName = spec.component;
    const layout = props.layout || 'horizontal';
    const margin = margins(props);
    const data = ctx.data;
    const stackOffset = props.stackOffset || 'none';
    const items = spec.series.map((s, idx) => {
      const p = Object.assign({}, SERIES_DEFAULTS[s.component], resolveProps(s, ctx));
      return { component: s.component, props: p, dataKey: p.dataKey, stackId: p.stackId == null ? undefined : String(p.stackId), id: `${s.component.toLowerCase()}-${idx}`, spec: s, index: idx };
    });
    const hasBar = items.some((i) => i.component === 'Bar');

    // axes
    const xs = spec.axes.filter((a) => a.component === 'XAxis').map((a) => Object.assign({ axisType: 'xAxis' }, X_DEFAULTS, resolveProps(a, ctx)));
    const ys = spec.axes.filter((a) => a.component === 'YAxis').map((a) => Object.assign({ axisType: 'yAxis' }, Y_DEFAULTS, resolveProps(a, ctx)));
    const xAxis = xs[0] || Object.assign({ axisType: 'xAxis' }, IMPLICIT_X);
    const yAxis = ys[0] || Object.assign({ axisType: 'yAxis' }, IMPLICIT_Y);
    [xAxis, yAxis].forEach((ax) => { ax.tickFormatterFn = fn(ax.tickFormatter, ctx); });

    // legend size (rendered before the offset is known, like Recharts' second pass)
    const legend = spec.legend ? buildLegendModel(spec, items, ctx, margin, W, H) : null;
    const legendBox = legend ? chart.measureLegend(legend) : null;

    // offset
    const off = { left: margin.left, right: margin.right, top: margin.top, bottom: margin.bottom };
    ys.forEach((y) => { if (!y.hide && !y.mirror) { if (y.orientation === 'left') off.left += isNum(y.width) ? y.width : 60; else if (y.orientation === 'right') off.right += isNum(y.width) ? y.width : 60; } });
    xs.forEach((x) => { if (!x.hide && !x.mirror) { if (x.orientation === 'top') off.top += x.height; else if (x.orientation === 'bottom') off.bottom += x.height; } });
    if (legend && legendBox) {
      const { layout: ll, align, verticalAlign } = legend.settings;
      if ((ll === 'vertical' || (ll === 'horizontal' && verticalAlign === 'middle')) && align !== 'center') off[align] += legendBox.width || 0;
      else if ((ll === 'horizontal' || (ll === 'vertical' && align === 'center')) && verticalAlign !== 'middle') off[verticalAlign] += legendBox.height || 0;
    }
    off.width = Math.max(W - off.left - off.right, 0);
    off.height = Math.max(H - off.top - off.bottom, 0);

    // stacks
    const numericAxisType = layout === 'horizontal' ? 'yAxis' : 'xAxis';
    const stacked = items.filter((i) => (i.component === 'Area' || i.component === 'Bar') && i.stackId != null && !i.props.hide);
    let stackGroups;
    if (stacked.length) {
      stackGroups = {};
      const byId = {};
      stacked.forEach((it) => { (byId[it.stackId] = byId[it.stackId] || []).push(it); });
      const rows = data.map((entry) => { const row = {}; stacked.forEach((it) => { row[it.id] = getValue(entry, it.dataKey, 0); }); return row; });
      for (const sid of Object.keys(byId)) {
        const keys = byId[sid].map((i) => i.id);
        const sd = NS.d3.stack(rows, keys, (d, key) => Number(getValue(d, key, 0)), stackOffset);
        stackGroups[sid] = { stackedData: sd, items: byId[sid] };
      }
    }
    const visibleItems = items.filter((i) => !i.props.hide);
    const axisCtx = { layout, data, items: visibleItems, stackGroups, stackOffset, chartName, hasBar };
    const padX = xAxis.padding || {}, padY = yAxis.padding || {};
    xAxis.range = [off.left + (padX.left || 0), off.left + off.width - (padX.right || 0)];
    yAxis.range = layout === 'horizontal' ? [off.top + off.height - (padY.bottom || 0), off.top + (padY.top || 0)] : [off.top + (padY.top || 0), off.top + off.height - (padY.bottom || 0)];
    resolveAxis(xAxis, Object.assign({}, axisCtx, { stackGroups: numericAxisType === 'xAxis' ? stackGroups : undefined }));
    resolveAxis(yAxis, Object.assign({}, axisCtx, { stackGroups: numericAxisType === 'yAxis' ? stackGroups : undefined }));
    const catAxis = layout === 'horizontal' ? xAxis : yAxis;
    const numAxis = layout === 'horizontal' ? yAxis : xAxis;

    const model = { kind: 'cartesian', layout, offset: off, width: W, height: H, chartName, xAxis, yAxis, catAxis, numAxis, items, legend, legendBox, margin, data };

    // bars: positions
    const bars = visibleItems.filter((i) => i.component === 'Bar');
    let barPositions;
    if (bars.length) {
      const totalSize = layout === 'horizontal' ? off.width : off.height;
      const getBarSize = (self) => { const s = self != null ? self : props.barSize; return isNil(s) ? undefined : getPercentValue(s, totalSize, 0); };
      const groups = {};
      bars.filter((b) => b.stackId != null).forEach((b) => { (groups[b.stackId] = groups[b.stackId] || []).push(b); });
      const sizeList = Object.keys(groups).map((sid) => ({ stackId: sid, dataKeys: groups[sid].map((b) => b.dataKey), barSize: getBarSize(groups[sid][0].props.barSize) }))
        .concat(bars.filter((b) => b.stackId == null).map((b) => ({ stackId: undefined, dataKeys: [b.dataKey].filter((k) => k != null), barSize: getBarSize(b.props.barSize) })));
      const bandSize = bandSizeOfAxis(catAxis, catAxis.itemTicks, true);
      const barBandSize = (bandSize != null ? bandSize : props.maxBarSize) || 0;
      barPositions = barPositionList(props.barGap != null ? props.barGap : 4, props.barCategoryGap != null ? props.barCategoryGap : '10%', barBandSize, sizeList, props.maxBarSize);
      model.barBandSize = barBandSize;
    }

    // per item geometry
    items.forEach((it) => {
      if (it.props.hide) return;
      if (it.component === 'Bar') it.rects = barRects(it, model, barPositions, stackGroups);
      else if (it.component === 'Area') Object.assign(it, areaPoints(it, model, stackGroups));
      else if (it.component === 'Line') it.points = linePoints(it, model);
    });

    // tooltip axis
    model.tooltipAxis = catAxis;
    model.tooltipTicks = catAxis.tooltipTicks;
    model.tooltipBandSize = bandSizeOfAxis(catAxis, catAxis.tooltipTicks, false);
    model.viewBox = { x: off.left, y: off.top, width: off.width, height: off.height };
    return model;
  }

  function barPositionList(barGap, barCategoryGap, bandSize, sizeList, maxBarSize) {
    const { getPercentValue, isWellBehaved } = I();
    const len = sizeList.length;
    if (len < 1) return undefined;
    let realBarGap = getPercentValue(barGap, bandSize, 0, true);
    if (isWellBehaved(sizeList[0].barSize)) {
      let useFull = false;
      let fullBarSize = bandSize / len;
      let sum = sizeList.reduce((r, e) => r + (e.barSize || 0), 0);
      sum += (len - 1) * realBarGap;
      if (sum >= bandSize) { sum -= (len - 1) * realBarGap; realBarGap = 0; }
      if (sum >= bandSize && fullBarSize > 0) { useFull = true; fullBarSize *= 0.9; sum = len * fullBarSize; }
      const offset = ((bandSize - sum) / 2) >> 0;
      let prev = { offset: offset - realBarGap, size: 0 };
      return sizeList.map((e) => {
        const pos = { offset: prev.offset + prev.size + realBarGap, size: useFull ? fullBarSize : (e.barSize || 0) };
        prev = pos;
        return { stackId: e.stackId, dataKeys: e.dataKeys, position: pos };
      });
    }
    const offset = getPercentValue(barCategoryGap, bandSize, 0, true);
    if (bandSize - 2 * offset - (len - 1) * realBarGap <= 0) realBarGap = 0;
    let originalSize = (bandSize - 2 * offset - (len - 1) * realBarGap) / len;
    if (originalSize > 1) originalSize >>= 0;
    const size = isWellBehaved(maxBarSize) ? Math.min(originalSize, maxBarSize) : originalSize;
    return sizeList.map((e, i) => ({ stackId: e.stackId, dataKeys: e.dataKeys, position: { offset: offset + (originalSize + realBarGap) * i + (originalSize - size) / 2, size } }));
  }

  function baseValueOfBar(numericAxis) {
    const domain = numericAxis.scale.domain();
    if (numericAxis.type === 'number') {
      const mn = Math.min(domain[0], domain[1]), mx = Math.max(domain[0], domain[1]);
      if (mn <= 0 && mx >= 0) return 0;
      if (mx < 0) return mx;
      return mn;
    }
    return domain[0];
  }
  function truncateByDomain(value, domain) {
    const { isNum } = I();
    if (!domain || domain.length !== 2 || !isNum(domain[0]) || !isNum(domain[1])) return value;
    const mn = Math.min(domain[0], domain[1]), mx = Math.max(domain[0], domain[1]);
    const r = [value[0], value[1]];
    if (!isNum(value[0]) || value[0] < mn) r[0] = mn;
    if (!isNum(value[1]) || value[1] > mx) r[1] = mx;
    if (r[0] > mx) r[0] = mx;
    if (r[1] < mn) r[1] = mn;
    return r;
  }
  function stackedOf(it, stackGroups) {
    if (!stackGroups || it.stackId == null || !stackGroups[it.stackId]) return undefined;
    return stackGroups[it.stackId].stackedData.find((sd) => sd.key === it.id);
  }

  function barRects(it, m, positions, stackGroups) {
    const { getValue, isNum, isNil, mathSign } = I();
    const pos = positions && positions.find((p) => p.stackId === it.stackId && it.dataKey != null && p.dataKeys.includes(it.dataKey));
    if (!pos) return [];
    const position = pos.position;
    const { layout, xAxis, yAxis, offset } = m;
    const numericAxis = layout === 'horizontal' ? yAxis : xAxis;
    const stackedData = stackedOf(it, stackGroups);
    const stackedDomain = stackedData ? numericAxis.scale.domain() : null;
    const baseValue = baseValueOfBar(numericAxis);
    const stackedBarStart = numericAxis.scale(baseValue);
    const cells = it.spec.cells;
    const hasCustomShape = it.props.shape != null;
    const parentViewBox = { x: 0, y: 0, width: m.width, height: m.height };
    const cateCoord = (axis, ticks, entry, index) => {
      if (axis.type === 'category') return ticks[index] ? ticks[index].coordinate + position.offset : null;
      const value = getValue(entry, axis.dataKey, axis.scale.domain()[index]);
      if (isNil(value)) return null;
      const sc = axis.scale(value);
      return isNum(sc) ? sc - m.barBandSize / 2 + position.offset : null;
    };
    return m.data.map((entry, index) => {
      let value, x, y, width, height, background;
      if (stackedData) {
        const u = stackedData[index];
        if (u == null) return null;
        value = truncateByDomain([u[0], u[1]], stackedDomain);
      } else {
        value = getValue(entry, it.dataKey);
        if (!Array.isArray(value)) value = [baseValue, value];
      }
      const minPointSize = isNum(it.props.minPointSize) ? it.props.minPointSize : 0;
      if (layout === 'horizontal') {
        const b = yAxis.scale(value[0]), c = yAxis.scale(value[1]);
        if (b == null || c == null) return null;
        x = cateCoord(xAxis, xAxis.itemTicks, entry, index);
        y = c != null ? c : b;
        width = position.size;
        const ch = b - c;
        height = Number.isNaN(ch) ? 0 : ch;
        background = { x, y: offset.top, width, height: offset.height };
        if (Math.abs(minPointSize) > 0 && Math.abs(height) < Math.abs(minPointSize)) { const delta = mathSign(height || minPointSize) * (Math.abs(minPointSize) - Math.abs(height)); y -= delta; height += delta; }
      } else {
        const b = xAxis.scale(value[0]), c = xAxis.scale(value[1]);
        if (b == null || c == null) return null;
        x = b;
        y = cateCoord(yAxis, yAxis.itemTicks, entry, index);
        width = c - b;
        height = position.size;
        background = { x: offset.left, y, width: offset.width, height };
        if (Math.abs(minPointSize) > 0 && Math.abs(width) < Math.abs(minPointSize)) width += mathSign(width || minPointSize) * (Math.abs(minPointSize) - Math.abs(width));
      }
      if (x == null || y == null || width == null || height == null || (!hasCustomShape && (width === 0 || height === 0))) return null;
      return Object.assign({}, entry, { stackedBarStart, x, y, width, height, value: stackedData ? value : value[1], payload: entry, background, tooltipPosition: { x: x + width / 2, y: y + height / 2 }, parentViewBox, originalDataIndex: index }, cells && cells[index] ? resolveProps(cells[index], m) : {});
    }).filter(Boolean);
  }

  function cateCoordOfLine(axis, ticks, bandSize, entry, index, dataKey) {
    const { isNil, getValue, isNum } = I();
    if (axis.type === 'category') {
      if (!axis.allowDuplicatedCategory && axis.dataKey && !isNil(entry[axis.dataKey])) {
        const t = ticks.find((tk) => tk.value === entry[axis.dataKey]);
        if (t) return t.coordinate + bandSize / 2;
      }
      return ticks[index] ? ticks[index].coordinate + bandSize / 2 : null;
    }
    const value = getValue(entry, !isNil(dataKey) ? dataKey : axis.dataKey);
    const sc = axis.scale(value);
    return isNum(sc) ? sc : null;
  }

  function areaPoints(it, m, stackGroups) {
    const { getValue, isNum } = I();
    const { layout, xAxis, yAxis } = m;
    const stackedData = stackedOf(it, stackGroups);
    const hasStack = stackedData && stackedData.length;
    const numericAxis = layout === 'horizontal' ? yAxis : xAxis;
    const domain = numericAxis.scale.domain();
    let baseValue = it.props.baseValue;
    if (!isNum(baseValue)) {
      if (numericAxis.type === 'number') {
        const mx = Math.max(domain[0], domain[1]), mn = Math.min(domain[0], domain[1]);
        baseValue = baseValue === 'dataMin' ? mn : baseValue === 'dataMax' ? mx : (mx < 0 ? mx : Math.max(mn, 0));
      } else baseValue = baseValue === 'dataMax' ? domain[1] : domain[0];
    }
    const catAxis = layout === 'horizontal' ? xAxis : yAxis;
    const bandSize = I().bandSizeOfAxis(catAxis, catAxis.itemTicks, false);
    let isRange = false;
    const points = m.data.map((entry, index) => {
      let va;
      if (hasStack) va = [stackedData[index][0], stackedData[index][1]];
      else { const raw = getValue(entry, it.dataKey); if (!Array.isArray(raw)) va = [baseValue, raw]; else { va = raw; isRange = true; } }
      const v1 = va && va[1] != null ? va[1] : null;
      const isBreak = v1 == null || (hasStack && !it.props.connectNulls && getValue(entry, it.dataKey) == null);
      if (layout === 'horizontal') {
        const yv = isBreak ? null : yAxis.scale(v1);
        return { x: cateCoordOfLine(xAxis, xAxis.itemTicks, bandSize, entry, index), y: yv == null ? null : yv, value: va, payload: entry };
      }
      const xv = isBreak ? null : xAxis.scale(v1);
      return { x: xv == null ? null : xv, y: cateCoordOfLine(yAxis, yAxis.itemTicks, bandSize, entry, index), value: va, payload: entry };
    });
    let baseLine;
    if (hasStack || isRange) {
      baseLine = points.map((e) => {
        const x = Array.isArray(e.value) ? e.value[0] : null;
        if (layout === 'horizontal') return { x: e.x, y: x != null && e.y != null ? yAxis.scale(x) : null, payload: e.payload };
        return { x: x != null ? xAxis.scale(x) : null, y: e.y, payload: e.payload };
      });
    } else {
      baseLine = layout === 'horizontal' ? yAxis.scale(baseValue) : xAxis.scale(baseValue);
    }
    return { points, baseLine: baseLine == null ? 0 : baseLine, isRange };
  }

  function linePoints(it, m) {
    const { getValue, isNil } = I();
    const { layout, xAxis, yAxis } = m;
    const catAxis = layout === 'horizontal' ? xAxis : yAxis;
    const bandSize = I().bandSizeOfAxis(catAxis, catAxis.itemTicks, false);
    return m.data.map((entry, index) => {
      const value = getValue(entry, it.dataKey);
      if (layout === 'horizontal') {
        const x = cateCoordOfLine(xAxis, xAxis.itemTicks, bandSize, entry, index);
        const y = isNil(value) ? null : yAxis.scale(value);
        return { x, y: y == null ? null : y, value, payload: entry };
      }
      const x = isNil(value) ? null : xAxis.scale(value);
      const y = cateCoordOfLine(yAxis, yAxis.itemTicks, bandSize, entry, index);
      if (x == null || y == null) return null;
      return { x, y, value, payload: entry };
    }).filter(Boolean);
  }

  /* ---------------------------------------------------------------- rendering: cartesian */
  function renderCartesian(chart, m, active) {
    const L = I();
    const { h, Text, svgProps, A, Rectangle, Curve, Dot, Shape, getTicks, isNum } = L;
    const spec = chart.spec, ctx = chart.ctx;
    const layers = newLayers();
    const z0 = [];
    const { offset: off, layout } = m;
    const labelsLater = [];

    // grid
    if (spec.grid) layers[Z.grid].push(renderCartesianGrid(chart, m));

    // series
    m.items.forEach((it) => {
      if (it.props.hide) return;
      if (it.component === 'Bar') renderBar(chart, m, it, layers, labelsLater, active);
      else if (it.component === 'Area') renderArea(chart, m, it, layers, labelsLater, active);
      else if (it.component === 'Line') renderLine(chart, m, it, layers, labelsLater, active);
    });

    // axes, in spec order
    spec.axes.forEach((raw, i) => {
      const axis = raw.component === 'XAxis' ? m.xAxis : m.yAxis;
      if (axis.implicit || axis.hide) return;
      renderCartesianAxis(chart, m, axis, layers);
    });

    // cursor
    if (active && active.cursor) {
      const cur = spec.tooltip && spec.tooltip.cursor;
      if (cur !== false && cur !== undefined || (spec.tooltip && !('cursor' in spec.tooltip))) {
        renderCursor(chart, m, active, layers);
      }
    }

    labelsLater.forEach((l) => layers[Z.label].push(l));
    return { layers, z0 };
  }

  function newLayers() { const o = {}; Z_LAYERS.forEach((z) => { o[z] = []; }); return o; }

  function renderCartesianGrid(chart, m) {
    const L = I();
    const { h, svgProps, A, getTicks } = L;
    const g = Object.assign({ horizontal: true, vertical: true, stroke: '#ccc', fill: 'none', verticalFill: [], horizontalFill: [], syncWithTicks: false }, resolveProps(chart.spec.grid, chart.ctx));
    const off = m.offset;
    const x = off.left, y = off.top, width = off.width, height = off.height;
    if (!(width > 0) || !(height > 0)) return null;
    const gridTicks = (axis, sizeKeyOrientation) => {
      if (!axis || !axis.scale) return [];
      return getTicks({ tick: axis.tick === undefined ? true : axis.tick, ticks: axis.ticks_, viewBox: { x: 0, y: 0, width: m.width, height: m.height }, minTickGap: axis.minTickGap === undefined ? 5 : axis.minTickGap, orientation: axis.orientation || sizeKeyOrientation, interval: axis.interval === undefined ? 'preserveEnd' : axis.interval, tickFormatter: axis.tickFormatterFn, unit: axis.unit, angle: axis.angle }, undefined, undefined);
    };
    const coordsOf = (ticks, min, max) => {
      if (g.syncWithTicks) return ticks.map((t) => t.coordinate);
      let hasMin = false, hasMax = false;
      const vals = ticks.map((t) => { if (t.coordinate === min) hasMin = true; if (t.coordinate === max) hasMax = true; return t.coordinate; });
      if (!hasMin) vals.push(min);
      if (!hasMax) vals.push(max);
      return vals;
    };
    const lineProps = svgProps(g);
    delete lineProps.className;
    const kids = [];
    if (g.fill && g.fill !== 'none') kids.push(h('rect', { x, y, ry: g.ry, width, height, stroke: 'none', fill: g.fill, 'fill-opacity': g.fillOpacity, class: 'recharts-cartesian-grid-bg' }));
    const hp = g.horizontal ? coordsOf(gridTicks(m.yAxis, 'left'), off.top, off.top + off.height) : [];
    const vp = g.vertical ? coordsOf(gridTicks(m.xAxis, 'bottom'), off.left, off.left + off.width) : [];
    const base = Object.assign({}, lineProps, { x, y, width, height });
    if (g.horizontal && hp.length) {
      kids.push(h('g', { class: 'recharts-cartesian-grid-horizontal' }, hp.map((e) => h('line', Object.assign(A(base), { x1: x, y1: e, x2: x + width, y2: e, fill: 'none' })))));
    }
    if (g.vertical && vp.length) {
      kids.push(h('g', { class: 'recharts-cartesian-grid-vertical' }, vp.map((e) => h('line', Object.assign(A(base), { x1: e, y1: y, x2: e, y2: y + height, fill: 'none' })))));
    }
    return h('g', { class: 'recharts-cartesian-grid' }, kids);
  }

  function tickLineCoord(entry, x, y, width, height, orientation, tickSize, mirror, tickMargin) {
    const sign = mirror ? -1 : 1;
    const ts = entry.tickSize || tickSize;
    const tc = I().isNum(entry.tickCoord) ? entry.tickCoord : entry.coordinate;
    let x1, x2, y1, y2, tx, ty;
    switch (orientation) {
      case 'top': x1 = x2 = entry.coordinate; y2 = y + +!mirror * height; y1 = y2 - sign * ts; ty = y1 - sign * tickMargin; tx = tc; break;
      case 'left': y1 = y2 = entry.coordinate; x2 = x + +!mirror * width; x1 = x2 - sign * ts; tx = x1 - sign * tickMargin; ty = tc; break;
      case 'right': y1 = y2 = entry.coordinate; x2 = x + +mirror * width; x1 = x2 + sign * ts; tx = x1 + sign * tickMargin; ty = tc; break;
      default: x1 = x2 = entry.coordinate; y2 = y + +mirror * height; y1 = y2 + sign * ts; ty = y1 + sign * tickMargin; tx = tc; break;
    }
    return { line: { x1, y1, x2, y2 }, tick: { x: tx, y: ty } };
  }

  function renderCartesianAxis(chart, m, axis, layers) {
    const L = I();
    const { h, Text, svgProps, A } = L;
    const off = m.offset;
    const isX = axis.axisType === 'xAxis';
    let x, y, width, height;
    if (isX) { x = off.left; width = off.width; height = axis.height; y = axis.orientation === 'top' ? off.top - height : m.height - off.bottom; if (axis.orientation === 'top' && axis.mirror) y = off.top; if (axis.orientation === 'bottom' && axis.mirror) y = m.height - off.bottom; } else { y = off.top; height = off.height; width = typeof axis.width === 'number' ? axis.width : 60; x = axis.orientation === 'left' ? off.left - (axis.mirror ? 0 : width) : m.width - off.right - (axis.mirror ? width : 0); }
    if ((width != null && width <= 0) || (height != null && height <= 0)) return;
    const typeName = axis.axisType;
    const fontSize = chart.axisFontSize();
    const finalTicks = L.getTicks({ tick: axis.tick, ticks: axis.ticks_, viewBox: { x: 0, y: 0, width: m.width, height: m.height }, minTickGap: axis.minTickGap, orientation: axis.orientation, interval: axis.interval, tickFormatter: axis.tickFormatterFn, unit: axis.unit, angle: axis.angle }, fontSize.fontSize, fontSize.letterSpacing);
    // svg props of the axis component, as CartesianAxis spreads them on lines and labels
    const axisProps = svgProps(Object.assign({}, axis, { x, y, width, height }));
    delete axisProps.className; delete axisProps.ticks;
    const anchor = { left: axis.mirror ? 'start' : 'end', right: axis.mirror ? 'end' : 'start' }[axis.orientation] || 'middle';
    const vAnchor = axis.orientation === 'left' || axis.orientation === 'right' ? 'middle' : axis.orientation === 'top' ? (axis.mirror ? 'start' : 'end') : (axis.mirror ? 'end' : 'start');
    const textAnchor = ['start', 'middle', 'end', 'inherit'].includes(axisProps.textAnchor) ? axisProps.textAnchor : anchor;
    const coords = finalTicks.map((entry) => Object.assign({ entry }, tickLineCoord(entry, x, y, width, height, axis.orientation, axis.tickSize, axis.mirror, axis.tickMargin)));
    const tickLineObj = typeof axis.tickLine === 'object' && axis.tickLine ? axis.tickLine : {};
    const tickLines = coords.map(({ line }) => h('g', { class: 'recharts-layer recharts-cartesian-axis-tick' }, [
      axis.tickLine ? h('line', Object.assign(A(Object.assign({}, axisProps, { fill: 'none' }, svgProps(tickLineObj))), line, { class: ['recharts-cartesian-axis-tick-line', tickLineObj.className].filter(Boolean).join(' ') })) : null,
    ]));
    const customTick = axis.tick && typeof axis.tick === 'object' && !axis.tick.$fn ? svgProps(axis.tick) : {};
    const labels = coords.map(({ entry, tick }, i) => {
      const tp = Object.assign({ verticalAnchor: vAnchor }, axisProps, { textAnchor, stroke: 'none', fill: axis.stroke }, tick, { index: i, payload: entry, visibleTicksCount: finalTicks.length }, { angle: axisProps.angle != null ? axisProps.angle : 0 }, customTick);
      const value = `${axis.tickFormatterFn ? axis.tickFormatterFn(entry.value, i) : entry.value}${axis.unit || ''}`;
      let el = null;
      if (axis.tick) {
        if (axis.tick && axis.tick.$fn) el = fn(axis.tick, chart.ctx)(Object.assign({}, tp, { className: 'recharts-cartesian-axis-tick-value' }), chart.ctx);
        else el = Text(Object.assign({}, tp, { className: ['recharts-cartesian-axis-tick-value', typeof axis.tick === 'object' ? axis.tick.className : ''].filter(Boolean).join(' ') }), value);
      }
      return h('g', { class: 'recharts-layer recharts-cartesian-axis-tick-label' }, [el]);
    });
    if (labels.length) layers[Z.label].push(h('g', { class: `recharts-cartesian-axis-tick-labels recharts-${typeName}-tick-labels` }, labels));
    const axisLine = axis.axisLine ? (() => {
      const lp = Object.assign({}, svgProps(Object.assign({}, axis, { x, y, width, height })), svgProps(typeof axis.axisLine === 'object' ? axis.axisLine : {}), { fill: 'none' });
      delete lp.className;
      let c;
      if (axis.orientation === 'top' || axis.orientation === 'bottom') { const need = +((axis.orientation === 'top' && !axis.mirror) || (axis.orientation === 'bottom' && axis.mirror)); c = { x1: x, y1: y + need * height, x2: x + width, y2: y + need * height }; } else { const need = +((axis.orientation === 'left' && !axis.mirror) || (axis.orientation === 'right' && axis.mirror)); c = { x1: x + need * width, y1: y, x2: x + need * width, y2: y + height }; }
      return h('line', Object.assign(A(lp), c, { class: 'recharts-cartesian-axis-line' }));
    })() : null;
    layers[Z.axis].push(h('g', { class: `recharts-layer recharts-cartesian-axis recharts-${typeName} ${typeName}` }, [
      axisLine,
      h('g', { class: `recharts-cartesian-axis-ticks recharts-${typeName}-ticks` }, [
        tickLines.length ? h('g', { class: `recharts-cartesian-axis-tick-lines recharts-${typeName}-tick-lines` }, tickLines) : null,
      ]),
    ]));
  }

  function seriesSvgProps(it) {
    const p = {};
    for (const k of Object.keys(it.props)) if (!SERIES_META.has(k)) p[k] = it.props[k];
    return p;
  }

  function renderBar(chart, m, it, layers, labelsLater, active) {
    const L = I();
    const { h, svgProps, Shape } = L;
    const p = it.props;
    const base = svgProps(seriesSvgProps(it));
    delete base.id;
    if (!('name' in base)) base.name = String(p.name);
    const rects = it.rects || [];
    if (p.background) {
      const bgProps = typeof p.background === 'object' ? svgProps(p.background) : {};
      layers[Z.barBackground].push(h('g', { class: 'recharts-layer' }, rects.map((entry, i) => {
        if (!entry.background) return null;
        const rest = Object.assign({}, entry); delete rest.value; delete rest.background; delete rest.tooltipPosition;
        const bp = Object.assign({}, rest, { fill: '#eee' }, entry.background, bgProps, { className: 'recharts-bar-background-rectangle' });
        return Shape('rectangle', p.background, Object.assign({}, svgProps(bp), { className: bp.className, isActive: false }), chart.ctx, { active: 'recharts-active-bar', inactive: 'recharts-inactive-bar' });
      })));
    }
    const rectEls = rects.map((entry, i) => {
      const props = Object.assign({}, base, svgProps(entry), { x: entry.x, y: entry.y, width: entry.width, height: entry.height, index: i, payload: entry.payload, isActive: false });
      if (p.radius !== undefined) props.radius = p.radius;
      const option = p.shape;
      const shape = option ? Shape('rectangle', option, Object.assign({}, props, { payload: entry.payload, fill: props.fill, index: i }), chart.ctx, { active: 'recharts-active-bar', inactive: 'recharts-inactive-bar' })
        : Shape('rectangle', null, props, chart.ctx, { active: 'recharts-active-bar', inactive: 'recharts-inactive-bar' });
      return h('g', { class: 'recharts-layer recharts-bar-rectangle' }, [shape]);
    });
    layers[Z.bar].push(h('g', { class: 'recharts-layer recharts-bar', id: `recharts-bar-${chart.uid}-${it.index}` }, [
      h('g', { class: 'recharts-layer recharts-bar-rectangles' }, [h('g', { class: 'recharts-layer' }, rectEls)]),
    ]));
    const entries = rects.map((e) => {
      const vb = { x: e.x, y: e.y, width: e.width, lowerWidth: e.width, upperWidth: e.width, height: e.height };
      return Object.assign({}, vb, { value: e.value, payload: e.payload, parentViewBox: e.parentViewBox, viewBox: vb, fill: e.fill });
    });
    (it.spec.labelList || []).forEach((ll) => labelsLater.push(LabelList(ll, entries, chart.ctx)));
    if (p.label) labelsLater.push(LabelList(p.label === true ? {} : p.label, entries, chart.ctx));
  }

  function activePointLayer(chart, it, points, mainColor, active, layers) {
    const L = I();
    const { h, svgProps, Dot } = L;
    if (!active || !active.payloads || it.props.activeDot === false) return;
    const pt = points.find((q) => q && active.payloads.includes(q.payload));
    if (!pt || pt.x == null || pt.y == null) return;
    const ad = it.props.activeDot;
    const props = Object.assign({ index: active.index, dataKey: it.dataKey, cx: pt.x, cy: pt.y, r: 4, fill: mainColor != null ? mainColor : 'none', strokeWidth: 2, stroke: '#fff', payload: pt.payload, value: pt.value }, typeof ad === 'object' ? svgProps(ad) : {});
    layers[Z.activeDot].push(h('g', { class: 'recharts-layer recharts-active-dot' }, [Dot(props)]));
  }

  function dotsLayer(chart, it, points, cls, dotCls, baseProps, layers) {
    const L = I();
    const { h, svgProps, Dot } = L;
    const dot = it.props.dot;
    if (!points || !(dot || points.length === 1)) return;
    const custom = dot && typeof dot === 'object' && !dot.$fn ? svgProps(dot) : {};
    const dots = points.map((entry, i) => {
      const dp = Object.assign({ r: 3 }, baseProps, custom, { index: i, cx: entry.x == null ? undefined : entry.x, cy: entry.y == null ? undefined : entry.y, dataKey: it.dataKey, value: entry.value, payload: entry.payload });
      if (dot && dot.$fn) return fn(dot, chart.ctx)(dp, chart.ctx);
      return Dot(Object.assign({}, dp, { className: [dotCls, dot && typeof dot === 'object' ? dot.className : ''].filter(Boolean).join(' ') }));
    });
    layers[Z.scatter].push(h('g', { class: ['recharts-layer', cls].join(' ') }, dots));
  }

  function renderArea(chart, m, it, layers, labelsLater, active) {
    const L = I();
    const { h, svgProps, Curve } = L;
    const p = it.props;
    const points = it.points || [];
    if (!points.length) return;
    const geom = { height: m.offset.height, width: m.offset.width };
    const props = Object.assign(seriesSvgProps(it), geom);
    const all = svgProps(props);
    delete all.id;
    const kids = [];
    if (points.length > 1) {
      kids.push(h('g', { class: 'recharts-layer' }, [
        Curve(Object.assign({}, all, { id: `recharts-area-${chart.uid}-${it.index}`, points, connectNulls: p.connectNulls, type: p.type, baseLine: it.baseLine, layout: m.layout, stroke: 'none', className: 'recharts-area-area' })),
        p.stroke !== 'none' ? Curve(Object.assign({}, all, { className: 'recharts-area-curve', layout: m.layout, type: p.type, connectNulls: p.connectNulls, fill: 'none', points })) : null,
        p.stroke !== 'none' && it.isRange && Array.isArray(it.baseLine) ? Curve(Object.assign({}, all, { className: 'recharts-area-curve', layout: m.layout, type: p.type, connectNulls: p.connectNulls, fill: 'none', points: it.baseLine })) : null,
      ]));
    }
    layers[Z.area].push(h('g', { class: 'recharts-layer recharts-area' }, kids));
    dotsLayer(chart, it, points, 'recharts-area-dots', 'recharts-area-dot', all, layers);
    activePointLayer(chart, it, points, legendColor(p.stroke, p.fill), active, layers);
    const entries = points.map((pt) => { const vb = { x: pt.x == null ? 0 : pt.x, y: pt.y == null ? 0 : pt.y, width: 0, lowerWidth: 0, upperWidth: 0, height: 0 }; return Object.assign({}, vb, { value: pt.value, payload: pt.payload, parentViewBox: undefined, viewBox: vb, fill: undefined }); });
    (it.spec.labelList || []).forEach((ll) => labelsLater.push(LabelList(ll, entries, chart.ctx)));
  }

  function renderLine(chart, m, it, layers, labelsLater, active) {
    const L = I();
    const { h, svgProps, Curve, Shape } = L;
    const p = it.props;
    const points = it.points || [];
    const geom = { height: m.offset.height, width: m.offset.width };
    const others = Object.assign(seriesSvgProps(it), geom);
    const lineProps = svgProps(others);
    const kids = [];
    if (points.length > 1) {
      const curveProps = Object.assign({}, lineProps, { fill: 'none', className: 'recharts-line-curve', id: `recharts-line-${chart.uid}-${it.index}`, points, type: p.type, layout: m.layout, connectNulls: p.connectNulls });
      const d = L.curvePath(p.type, points, undefined, m.layout, p.connectNulls);
      const len = chart.pathLength(d);
      curveProps.strokeDasharray = p.strokeDasharray != null ? String(p.strokeDasharray) : `${len}px ${len}px`;
      kids.push(Shape('curve', null, curveProps, chart.ctx));
    }
    layers[Z.line].push(h('g', { class: 'recharts-layer recharts-line' }, kids));
    const dotBase = Object.assign({}, lineProps);
    delete dotBase.id;
    dotsLayer(chart, it, points, 'recharts-line-dots', 'recharts-line-dot', dotBase, layers);
    activePointLayer(chart, it, points, p.stroke, active, layers);
    const entries = points.map((pt) => { const vb = { x: pt.x == null ? 0 : pt.x, y: pt.y == null ? 0 : pt.y, width: 0, lowerWidth: 0, upperWidth: 0, height: 0 }; return Object.assign({}, vb, { value: pt.value, payload: pt.payload, viewBox: vb, parentViewBox: undefined, fill: undefined }); });
    (it.spec.labelList || []).forEach((ll) => labelsLater.push(LabelList(ll, entries, chart.ctx)));
  }

  function renderCursor(chart, m, active, layers) {
    const L = I();
    const { svgProps, Rectangle, Curve, polarToCartesian } = L;
    const cursor = chart.spec.tooltip.cursor;
    const extra = typeof cursor === 'object' && cursor ? svgProps(cursor) : {};
    const off = m.offset;
    const coord = active.coordinate;
    const base = { stroke: '#ccc', pointerEvents: 'none', width: off.width, height: off.height };
    if (m.chartName === 'BarChart') {
      const half = m.tooltipBandSize / 2;
      const r = { stroke: 'none', fill: '#ccc', x: m.layout === 'horizontal' ? coord.x - half : off.left + 0.5, y: m.layout === 'horizontal' ? off.top + 0.5 : coord.y - half, width: m.layout === 'horizontal' ? m.tooltipBandSize : off.width - 1, height: m.layout === 'horizontal' ? off.height - 1 : m.tooltipBandSize };
      layers[Z.cursorRectangle].push(Rectangle(Object.assign({}, base, r, extra, { className: ['recharts-tooltip-cursor', cursor && cursor.className].filter(Boolean).join(' ') })));
      return;
    }
    let points;
    if (m.layout === 'horizontal') points = [{ x: coord.x, y: off.top }, { x: coord.x, y: off.top + off.height }];
    else if (m.layout === 'vertical') points = [{ x: off.left, y: coord.y }, { x: off.left + off.width, y: coord.y }];
    else if (m.layout === 'centric') { const a = polarToCartesian(coord.cx, coord.cy, coord.innerRadius, coord.angle), b = polarToCartesian(coord.cx, coord.cy, coord.outerRadius, coord.angle); points = [a, b]; }
    if (!points) return;
    layers[Z.cursorLine].push(Curve(Object.assign({}, base, { points }, extra, { className: ['recharts-tooltip-cursor', cursor && cursor.className].filter(Boolean).join(' ') })));
  }

  /* ---------------------------------------------------------------- legend model */
  function buildLegendModel(spec, items, ctx, margin, W, H) {
    const lg = Object.assign({ align: 'center', iconSize: 14, inactiveColor: '#ccc', itemSorter: 'value', layout: 'horizontal', verticalAlign: 'bottom' }, resolveProps(spec.legend, ctx));
    const payload = legendPayload(spec, items, ctx);
    const maxWidth = W - (margin.left || 0) - (margin.right || 0);
    const style = { position: 'absolute', width: (lg.layout === 'horizontal' ? (lg.width || maxWidth) : lg.width) || 'auto', height: lg.height || 'auto' };
    if (lg.layout === 'horizontal' || lg.width) style.width = (lg.width || maxWidth) + 'px';
    if (style.height !== 'auto') style.height += 'px';
    const pos = {};
    if (lg.align === 'center' && lg.layout === 'vertical') pos.left = 'calc'; else if (lg.align === 'right') pos.right = (margin.right || 0) + 'px'; else pos.left = (margin.left || 0) + 'px';
    if (lg.verticalAlign === 'middle') pos.top = 'calc'; else if (lg.verticalAlign === 'bottom') pos.bottom = (margin.bottom || 0) + 'px'; else pos.top = (margin.top || 0) + 'px';
    return { settings: { layout: lg.layout, align: lg.align, verticalAlign: lg.verticalAlign }, props: lg, payload, style: Object.assign(style, pos) };
  }

  /* ---------------------------------------------------------------- tooltip */
  function calculateActiveTickIndex(coordinate, ticks, unsortedTicks, axisType, rangeV) {
    const { mathSign } = I();
    const len = ticks ? ticks.length : 0;
    if (len <= 1 || coordinate == null) return 0;
    if (axisType === 'angleAxis' && rangeV != null && Math.abs(Math.abs(rangeV[1] - rangeV[0]) - 360) <= 1e-6) {
      for (let i = 0; i < len; i++) {
        const before = i > 0 ? unsortedTicks[i - 1].coordinate : unsortedTicks[len - 1].coordinate;
        const cur = unsortedTicks[i].coordinate;
        const after = i >= len - 1 ? unsortedTicks[0].coordinate : unsortedTicks[i + 1].coordinate;
        let same;
        if (mathSign(cur - before) !== mathSign(after - cur)) {
          const diff = [];
          if (mathSign(after - cur) === mathSign(rangeV[1] - rangeV[0])) {
            same = after;
            const curInRange = cur + rangeV[1] - rangeV[0];
            diff[0] = Math.min(curInRange, (curInRange + before) / 2);
            diff[1] = Math.max(curInRange, (curInRange + before) / 2);
          } else {
            same = before;
            const afterInRange = after + rangeV[1] - rangeV[0];
            diff[0] = Math.min(cur, (afterInRange + cur) / 2);
            diff[1] = Math.max(cur, (afterInRange + cur) / 2);
          }
          const sameInterval = [Math.min(cur, (same + cur) / 2), Math.max(cur, (same + cur) / 2)];
          if ((coordinate > sameInterval[0] && coordinate <= sameInterval[1]) || (coordinate >= diff[0] && coordinate <= diff[1])) return unsortedTicks[i].index;
        } else {
          const mn = Math.min(before, after), mx = Math.max(before, after);
          if (coordinate > (mn + cur) / 2 && coordinate <= (mx + cur) / 2) return unsortedTicks[i].index;
        }
      }
    } else if (ticks) {
      for (let i = 0; i < len; i++) {
        const curr = ticks[i], next = ticks[i + 1], prev = ticks[i - 1];
        if (i === 0 && next != null && coordinate <= (curr.coordinate + next.coordinate) / 2) return curr.index;
        if (i === len - 1 && prev != null && coordinate > (curr.coordinate + prev.coordinate) / 2) return curr.index;
        if (i > 0 && i < len - 1 && prev != null && next != null && coordinate > (curr.coordinate + prev.coordinate) / 2 && coordinate <= (curr.coordinate + next.coordinate) / 2) return curr.index;
      }
    }
    return -1;
  }

  NS.surface = {
    Z, Z_LAYERS, fn, resolveProps, stateValue, tooltipNameProp, legendColor, cartesianPosition, Label, LabelList,
    buildCartesian, renderCartesian, newLayers, calculateActiveTickIndex, buildLegendModel, legendPayload,
    dotsLayer, activePointLayer, seriesSvgProps, SERIES_DEFAULTS, margins, baseValueOfBar, truncateByDomain,
    barPositionList, isPolarVB,
  };
})();

/* Chart renderer: draws a declarative chart spec into the same DOM Recharts 3.8 produces inside shadcn's
   ChartContainer (recharts-responsive-container > recharts-wrapper > svg.recharts-surface with the z-index
   layers, plus the tooltip and legend wrappers).

   The layout maths are ports of the Recharts sources (axis domains and nice ticks, band/point scales, tick
   thinning with measured label sizes, bar positions, stacks, curves, sectors, polar axes, label positions,
   tooltip placement), so the numbers match the upstream render. The spec format is documented in
   flask_basecoat/charts.py; function-valued props arrive as {"$fn": name, ...} and are looked up in
   window.FlaskBasecoatChart.formatters (chart-format.js).

   Exposed: window.FlaskBasecoatChart.Chart (one instance per [data-slot=chart]). */
(function () {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});
  const d3 = NS.d3;
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const XHTML_NS = 'http://www.w3.org/1999/xhtml';

  /* ------------------------------------------------------------------ utils */
  const isNil = (v) => v === null || v === undefined;
  const isNum = (v) => typeof v === 'number' && !Number.isNaN(v);
  const isWellBehaved = (v) => typeof v === 'number' && Number.isFinite(v);
  const isNumOrStr = (v) => isNum(v) || typeof v === 'string';
  const isPercent = (v) => typeof v === 'string' && v.length > 0 && v.indexOf('%') === v.length - 1;
  const mathSign = (v) => (v === 0 ? 0 : v > 0 ? 1 : -1);
  const RADIAN = Math.PI / 180;
  const range = (a, b) => { const out = []; for (let i = a; i < b; i++) out.push(i); return out; };

  function getPercentValue(percent, total, def, validate) {
    if (def === undefined) def = 0;
    if (!isNum(percent) && typeof percent !== 'string') return def;
    let value;
    if (isPercent(percent)) {
      if (total == null) return def;
      value = (total * parseFloat(percent.slice(0, -1))) / 100;
    } else {
      value = +percent;
    }
    if (Number.isNaN(value)) value = def;
    if (validate && total != null && value > total) value = total;
    return value;
  }

  function getValue(obj, key, def) {
    if (isNil(obj) || isNil(key)) return def;
    if (typeof key === 'function') return key(obj);
    if (typeof obj !== 'object') return def;
    const parts = String(key).split('.');
    let cur = obj;
    for (const p of parts) {
      if (cur == null || typeof cur !== 'object' || !(p in cur)) return def;
      cur = cur[p];
    }
    return cur === undefined ? def : cur;
  }

  function round(num, precision) {
    const k = Math.pow(10, precision === undefined ? 4 : precision);
    const r = Math.round(num * k) / k;
    return Object.is(r, -0) ? 0 : r;
  }
  /* roundTemplateLiteral: every interpolated number is rounded to 4 decimals. */
  function rt(strings, ...values) {
    return strings.reduce((acc, s, i) => {
      const v = values[i - 1];
      if (typeof v === 'string') return acc + v + s;
      if (v !== undefined) return acc + round(v) + s;
      return acc + s;
    });
  }

  function polarToCartesian(cx, cy, radius, angle) {
    return { x: cx + Math.cos(-RADIAN * angle) * radius, y: cy + Math.sin(-RADIAN * angle) * radius };
  }

  function hasDuplicate(arr) {
    const seen = new Set();
    for (const v of arr) { const k = v != null && typeof v === 'object' ? v.valueOf() : v; if (seen.has(k)) return true; seen.add(k); }
    return false;
  }

  function sortByKey(list, key) {
    return list.map((v, i) => [v, i]).sort((a, b) => {
      const av = a[0] == null ? undefined : a[0][key];
      const bv = b[0] == null ? undefined : b[0][key];
      if (av === bv) return a[1] - b[1];
      if (av === undefined) return 1;
      if (bv === undefined) return -1;
      return av > bv ? 1 : av < bv ? -1 : a[1] - b[1];
    }).map((p) => p[0]);
  }

  function uniqBy(list, fn) {
    const seen = new Set();
    return list.filter((e) => { const k = fn(e); if (seen.has(k)) return false; seen.add(k); return true; });
  }

  /* ------------------------------------------------------------------ text measurement (getStringSize) */
  const sizeCache = new Map();
  function getStringSize(text, style) {
    if (text === undefined || text === null) return { width: 0, height: 0 };
    style = style || {};
    const key = [text, style.fontSize || '', style.fontFamily || '', style.fontWeight || '', style.fontStyle || '', style.letterSpacing || '', style.textTransform || ''].join('|');
    if (sizeCache.has(key)) return sizeCache.get(key);
    let span = document.getElementById('recharts_measurement_span');
    if (!span) {
      span = document.createElement('span');
      span.setAttribute('id', 'recharts_measurement_span');
      span.setAttribute('aria-hidden', 'true');
      document.body.appendChild(span);
    }
    Object.assign(span.style, { position: 'absolute', top: '-20000px', left: 0, padding: 0, margin: 0, border: 'none', whiteSpace: 'pre' }, style);
    span.textContent = String(text);
    const rect = span.getBoundingClientRect();
    const out = { width: rect.width, height: rect.height };
    if (sizeCache.size > 2000) sizeCache.clear();
    sizeCache.set(key, out);
    return out;
  }

  /* ------------------------------------------------------------------ reduceCSSCalc */
  const CSS_UNIT = /^(px|cm|vh|vw|em|rem|%|mm|in|pt|pc|ex|ch|vmin|vmax|Q)$/;
  function cssNum(str) {
    const m = /(-?\d+(?:\.\d+)?)([a-zA-Z%]+)?/.exec(str);
    if (!m) return { num: NaN, unit: '' };
    let num = parseFloat(m[1]); let unit = m[2] || '';
    if (unit !== '' && !CSS_UNIT.test(unit)) { num = NaN; unit = ''; }
    const RATES = { cm: 96 / 2.54, mm: 96 / 25.4, pt: 96 / 72, pc: 96 / 6, in: 96, Q: 96 / (2.54 * 40), px: 1 };
    if (unit in RATES) { num *= RATES[unit]; unit = 'px'; }
    return { num, unit };
  }
  const cssStr = (v) => '' + v.num + v.unit;
  function cssArith(expr) {
    if (expr == null || expr.includes('NaN')) return 'NaN';
    const MUL = /(-?\d+(?:\.\d+)?[a-zA-Z%]*)([*/])(-?\d+(?:\.\d+)?[a-zA-Z%]*)/;
    const ADD = /(-?\d+(?:\.\d+)?[a-zA-Z%]*)([+-])(-?\d+(?:\.\d+)?[a-zA-Z%]*)/;
    let e = expr;
    while (e.includes('*') || e.includes('/')) {
      const m = MUL.exec(e) || [];
      const l = cssNum(m[1] || ''), r = cssNum(m[3] || '');
      if (l.unit !== '' && r.unit !== '' && l.unit !== r.unit) return 'NaN';
      const res = { num: m[2] === '*' ? l.num * r.num : l.num / r.num, unit: l.unit || r.unit };
      if (Number.isNaN(res.num)) return 'NaN';
      e = e.replace(MUL, cssStr(res));
    }
    while (e.includes('+') || /.-\d+(?:\.\d+)?/.test(e)) {
      const m = ADD.exec(e) || [];
      const l = cssNum(m[1] || ''), r = cssNum(m[3] || '');
      if (l.unit !== r.unit) return 'NaN';
      const res = { num: m[2] === '+' ? l.num + r.num : l.num - r.num, unit: l.unit };
      if (Number.isNaN(res.num)) return 'NaN';
      e = e.replace(ADD, cssStr(res));
    }
    return e;
  }
  function reduceCSSCalc(expression) {
    try {
      let e = expression.slice(5, -1).replace(/\s+/g, '');
      const PAREN = /\(([^()]*)\)/;
      let m;
      while ((m = PAREN.exec(e)) != null) e = e.replace(PAREN, cssArith(m[1]));
      e = cssArith(e);
      return e === 'NaN' ? '' : e;
    } catch (err) {
      return '';
    }
  }

  /* ------------------------------------------------------------------ element tree */
  function h(tag, attrs, children) {
    const kids = [];
    (function add(list) {
      for (const c of list) {
        if (c == null || c === false) continue;
        if (Array.isArray(c)) add(c); else kids.push(c);
      }
    })(children || []);
    return { tag, attrs: attrs || {}, children: kids };
  }
  const txt = (s) => ({ text: String(s) });

  /* React's SVG attribute names for the camelCase props Recharts spreads (svgPropertiesNoEvents). */
  const SVG_KEYS = new Set(('className color height id lang max media method min name style target width role tabIndex alignmentBaseline baselineShift clip clipPath clipRule colorInterpolation cursor cx cy d direction display dominantBaseline dx dy fill fillOpacity fillRule filter floodColor floodOpacity fontFamily fontSize fontSizeAdjust fontStretch fontStyle fontVariant fontWeight from fx fy gradientTransform gradientUnits href in letterSpacing lightingColor markerEnd markerMid markerStart mask offset opacity orient orientation overflow paintOrder pathLength pointerEvents points r radius rotate rx ry shapeRendering stopColor stopOpacity stroke strokeDasharray strokeDashoffset strokeLinecap strokeLinejoin strokeMiterlimit strokeOpacity strokeWidth textAnchor textDecoration textLength textRendering to transform vectorEffect visibility wordSpacing writingMode x1 x2 x y1 y2 y z angle').split(' '));
  const CAMEL_ATTRS = { className: 'class', tabIndex: 'tabindex', viewBox: 'viewBox', gradientTransform: 'gradientTransform', gradientUnits: 'gradientUnits', pathLength: 'pathLength', textLength: 'textLength', xlinkHref: 'xlink:href' };
  function attrName(k) {
    if (CAMEL_ATTRS[k]) return CAMEL_ATTRS[k];
    if (k.startsWith('data-') || k.startsWith('aria-')) return k;
    return k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
  }
  /* Filter an arbitrary props object to its SVG attributes, as React would render them (insertion order kept). */
  function svgProps(obj) {
    const out = {};
    if (!obj || typeof obj !== 'object') return out;
    for (const k of Object.keys(obj)) {
      const v = obj[k];
      if (!(SVG_KEYS.has(k) || k.startsWith('data-'))) continue;
      if (v === undefined || v === null || typeof v === 'function' || typeof v === 'boolean' && !k.startsWith('data-')) continue;
      if (typeof v === 'object') continue;
      out[k] = v;
    }
    return out;
  }
  /* props (camelCase) -> attrs (DOM names) */
  function A(props) {
    const out = {};
    for (const k of Object.keys(props)) {
      const v = props[k];
      if (v === undefined || v === null || v === false) continue;
      out[attrName(k)] = v === true ? '' : v;
    }
    return out;
  }

  function toDom(node, parentNs) {
    if (node.text !== undefined) return document.createTextNode(node.text);
    if (node.html !== undefined) { const tpl = document.createElementNS(SVG_NS, 'g'); tpl.innerHTML = node.html; return tpl.firstChild; }
    const el = document.createElementNS(parentNs || SVG_NS, node.tag);
    for (const k of Object.keys(node.attrs)) {
      const v = node.attrs[k];
      if (v === undefined || v === null) continue;
      if (k === 'xlink:href') el.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', String(v));
      else el.setAttribute(k, String(v));
    }
    for (const c of node.children) el.appendChild(toDom(c, parentNs));
    return el;
  }

  /* ------------------------------------------------------------------ Text (recharts/component/Text) */
  const BREAKING = /[ \f\n\r\t\v\u2028\u2029]+/;
  function wordsByLines(p, content) {
    const children = content;
    const plain = () => [{ words: isNil(children) ? [] : String(children).split(BREAKING), width: undefined }];
    if (!(p.width || p.scaleToFit)) return plain();
    const words = isNil(children) ? [] : (p.breakAll ? String(children).split('') : String(children).split(BREAKING));
    const ww = words.map((w) => ({ word: w, width: getStringSize(w, p.style).width }));
    const spaceWidth = p.breakAll ? 0 : getStringSize(' ', p.style).width;
    const lineWidth = p.width;
    const lines = [];
    for (const { word, width } of ww) {
      const cur = lines[lines.length - 1];
      if (cur && width != null && (lineWidth == null || p.scaleToFit || cur.width + width + spaceWidth < Number(lineWidth))) {
        cur.words.push(word);
        cur.width += width + spaceWidth;
      } else {
        lines.push({ words: [word], width });
      }
    }
    return lines;
  }

  const TEXT_OMIT = new Set(['x', 'y', 'lineHeight', 'capHeight', 'fill', 'scaleToFit', 'textAnchor', 'verticalAnchor', 'dx', 'dy', 'angle', 'className', 'breakAll', 'maxLines', 'style', 'index', 'payload', 'visibleTicksCount', 'tickFormatter', 'padding', 'viewBox', 'value', 'position', 'children', 'content', 'parentViewBox', 'labelRef', 'textBreakAll', 'zIndex']);
  function Text(props, content) {
    const p = Object.assign({ angle: 0, breakAll: false, capHeight: '0.71em', fill: '#808080', lineHeight: '1em', scaleToFit: false, textAnchor: 'start', verticalAnchor: 'end', x: 0, y: 0 }, stripUndef(props));
    const lines = wordsByLines(p, content);
    if (!isNumOrStr(p.x) || !isNumOrStr(p.y) || lines.length === 0) return null;
    const x = Number(p.x) + (isNum(p.dx) ? p.dx : 0);
    const y = Number(p.y) + (isNum(p.dy) ? p.dy : 0);
    if (!isWellBehaved(x) || !isWellBehaved(y)) return null;
    let startDy;
    if (p.verticalAnchor === 'start') startDy = reduceCSSCalc(`calc(${p.capHeight})`);
    else if (p.verticalAnchor === 'middle') startDy = reduceCSSCalc(`calc(${(lines.length - 1) / 2} * -${p.lineHeight} + (${p.capHeight} / 2))`);
    else startDy = reduceCSSCalc(`calc(${lines.length - 1} * -${p.lineHeight})`);
    const rest = {};
    for (const k of Object.keys(p)) if (!TEXT_OMIT.has(k)) rest[k] = p[k];
    const textProps = svgProps(rest);
    const transforms = [];
    if (p.angle) transforms.push(`rotate(${p.angle}, ${x}, ${y})`);
    if (transforms.length) textProps.transform = transforms.join(' ');
    const fill = String(p.fill).includes('url') ? '#808080' : p.fill;
    const attrs = Object.assign(A(textProps), { x, y, class: ['recharts-text', p.className].filter(Boolean).join(' '), 'text-anchor': p.textAnchor, fill });
    return h('text', attrs, lines.map((line, i) => h('tspan', { x, dy: i === 0 ? startDy : p.lineHeight }, [txt(line.words.join(p.breakAll ? '' : ' '))])));
  }
  function stripUndef(o) { const out = {}; for (const k of Object.keys(o)) if (o[k] !== undefined) out[k] = o[k]; return out; }

  /* ------------------------------------------------------------------ shapes */
  function rectanglePath(x, y, width, height, radius) {
    const rw = round(width), rh = round(height);
    const maxRadius = Math.min(Math.abs(rw) / 2, Math.abs(rh) / 2);
    const ySign = rh >= 0 ? 1 : -1, xSign = rw >= 0 ? 1 : -1;
    const clockWise = (rh >= 0 && rw >= 0) || (rh < 0 && rw < 0) ? 1 : 0;
    let path;
    if (maxRadius > 0 && Array.isArray(radius)) {
      const nr = [0, 0, 0, 0];
      for (let i = 0; i < 4; i++) { const r = radius[i] == null ? 0 : radius[i]; nr[i] = r > maxRadius ? maxRadius : r; }
      path = rt`M${x},${y + ySign * nr[0]}`;
      if (nr[0] > 0) path += rt`A ${nr[0]},${nr[0]},0,0,${clockWise},${x + xSign * nr[0]},${y}`;
      path += rt`L ${x + width - xSign * nr[1]},${y}`;
      if (nr[1] > 0) path += rt`A ${nr[1]},${nr[1]},0,0,${clockWise},
        ${x + width},${y + ySign * nr[1]}`;
      path += rt`L ${x + width},${y + height - ySign * nr[2]}`;
      if (nr[2] > 0) path += rt`A ${nr[2]},${nr[2]},0,0,${clockWise},
        ${x + width - xSign * nr[2]},${y + height}`;
      path += rt`L ${x + xSign * nr[3]},${y + height}`;
      if (nr[3] > 0) path += rt`A ${nr[3]},${nr[3]},0,0,${clockWise},
        ${x},${y + height - ySign * nr[3]}`;
      path += 'Z';
    } else if (maxRadius > 0 && radius === +radius && radius > 0) {
      const r = Math.min(maxRadius, radius);
      path = rt`M ${x},${y + ySign * r}
            A ${r},${r},0,0,${clockWise},${x + xSign * r},${y}
            L ${x + width - xSign * r},${y}
            A ${r},${r},0,0,${clockWise},${x + width},${y + ySign * r}
            L ${x + width},${y + height - ySign * r}
            A ${r},${r},0,0,${clockWise},${x + width - xSign * r},${y + height}
            L ${x + xSign * r},${y + height}
            A ${r},${r},0,0,${clockWise},${x},${y + height - ySign * r} Z`;
    } else {
      path = rt`M ${x},${y} h ${width} v ${height} h ${-width} Z`;
    }
    return path;
  }

  /* Rectangle: <path class="recharts-rectangle"> with rounded x/y/width/height attributes. */
  function Rectangle(props) {
    const p = Object.assign({ x: 0, y: 0, width: 0, height: 0, radius: 0 }, stripUndef(props));
    const { x, y, width, height, radius } = p;
    if (x !== +x || y !== +y || width !== +width || height !== +height || width === 0 || height === 0) return null;
    const rest = svgProps(p);
    delete rest.radius; delete rest.className;
    const attrs = Object.assign(A(rest), { x: round(x), y: round(y), width: round(width), height: round(height) });
    if (typeof radius === 'number') attrs.radius = radius;
    attrs.class = ['recharts-rectangle', p.className].filter(Boolean).join(' ');
    attrs.d = rectanglePath(x, y, width, height, radius);
    return h('path', attrs);
  }

  function sectorDeltaAngle(s, e) { const sign = mathSign(e - s); return sign * Math.min(Math.abs(e - s), 359.999); }
  function sectorPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle) {
    const angle = sectorDeltaAngle(startAngle, endAngle);
    const tempEnd = startAngle + angle;
    const os = polarToCartesian(cx, cy, outerRadius, startAngle), oe = polarToCartesian(cx, cy, outerRadius, tempEnd);
    let path = rt`M ${os.x},${os.y}
    A ${outerRadius},${outerRadius},0,
    ${+(Math.abs(angle) > 180)},${+(startAngle > tempEnd)},
    ${oe.x},${oe.y}
  `;
    if (innerRadius > 0) {
      const is = polarToCartesian(cx, cy, innerRadius, startAngle), ie = polarToCartesian(cx, cy, innerRadius, tempEnd);
      path += rt`L ${ie.x},${ie.y}
            A ${innerRadius},${innerRadius},0,
            ${+(Math.abs(angle) > 180)},${+(startAngle <= tempEnd)},
            ${is.x},${is.y} Z`;
    } else {
      path += rt`L ${cx},${cy} Z`;
    }
    return path;
  }
  function tangentCircle(cx, cy, radius, angle, sign, isExternal, cornerRadius, cornerIsExternal) {
    const centerRadius = cornerRadius * (isExternal ? 1 : -1) + radius;
    const theta = Math.asin(cornerRadius / centerRadius) / RADIAN;
    const centerAngle = cornerIsExternal ? angle : angle + sign * theta;
    const center = polarToCartesian(cx, cy, centerRadius, centerAngle);
    const circleTangency = polarToCartesian(cx, cy, radius, centerAngle);
    const lineTangencyAngle = cornerIsExternal ? angle - sign * theta : angle;
    const lineTangency = polarToCartesian(cx, cy, centerRadius * Math.cos(theta * RADIAN), lineTangencyAngle);
    return { center, circleTangency, lineTangency, theta };
  }
  function sectorWithCorner(o) {
    const { cx, cy, innerRadius, outerRadius, cornerRadius, forceCornerRadius, cornerIsExternal, startAngle, endAngle } = o;
    const sign = mathSign(endAngle - startAngle);
    const s = tangentCircle(cx, cy, outerRadius, startAngle, sign, false, cornerRadius, cornerIsExternal);
    const e = tangentCircle(cx, cy, outerRadius, endAngle, -sign, false, cornerRadius, cornerIsExternal);
    const outerArcAngle = cornerIsExternal ? Math.abs(startAngle - endAngle) : Math.abs(startAngle - endAngle) - s.theta - e.theta;
    if (outerArcAngle < 0) {
      if (forceCornerRadius) {
        return rt`M ${s.lineTangency.x},${s.lineTangency.y}
        a${cornerRadius},${cornerRadius},0,0,1,${cornerRadius * 2},0
        a${cornerRadius},${cornerRadius},0,0,1,${-cornerRadius * 2},0
      `;
      }
      return sectorPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle);
    }
    let path = rt`M ${s.lineTangency.x},${s.lineTangency.y}
    A${cornerRadius},${cornerRadius},0,0,${+(sign < 0)},${s.circleTangency.x},${s.circleTangency.y}
    A${outerRadius},${outerRadius},0,${+(outerArcAngle > 180)},${+(sign < 0)},${e.circleTangency.x},${e.circleTangency.y}
    A${cornerRadius},${cornerRadius},0,0,${+(sign < 0)},${e.lineTangency.x},${e.lineTangency.y}
  `;
    if (innerRadius > 0) {
      const si = tangentCircle(cx, cy, innerRadius, startAngle, sign, true, cornerRadius, cornerIsExternal);
      const ei = tangentCircle(cx, cy, innerRadius, endAngle, -sign, true, cornerRadius, cornerIsExternal);
      const innerArcAngle = cornerIsExternal ? Math.abs(startAngle - endAngle) : Math.abs(startAngle - endAngle) - si.theta - ei.theta;
      if (innerArcAngle < 0 && cornerRadius === 0) return `${path}L${cx},${cy}Z`;
      path += rt`L${ei.lineTangency.x},${ei.lineTangency.y}
      A${cornerRadius},${cornerRadius},0,0,${+(sign < 0)},${ei.circleTangency.x},${ei.circleTangency.y}
      A${innerRadius},${innerRadius},0,${+(innerArcAngle > 180)},${+(sign > 0)},${si.circleTangency.x},${si.circleTangency.y}
      A${cornerRadius},${cornerRadius},0,0,${+(sign < 0)},${si.lineTangency.x},${si.lineTangency.y}Z`;
    } else {
      path += rt`L${cx},${cy}Z`;
    }
    return path;
  }
  function Sector(props) {
    const p = Object.assign({ cx: 0, cy: 0, innerRadius: 0, outerRadius: 0, startAngle: 0, endAngle: 0, cornerRadius: 0, forceCornerRadius: false, cornerIsExternal: false }, stripUndef(props));
    const { cx, cy, innerRadius, outerRadius, cornerRadius, forceCornerRadius, cornerIsExternal, startAngle, endAngle } = p;
    if (outerRadius < innerRadius || startAngle === endAngle) return null;
    const deltaRadius = outerRadius - innerRadius;
    const cr = getPercentValue(cornerRadius, deltaRadius, 0, true);
    let d;
    if (cr > 0 && Math.abs(startAngle - endAngle) < 360) {
      d = sectorWithCorner({ cx, cy, innerRadius, outerRadius, cornerRadius: Math.min(cr, deltaRadius / 2), forceCornerRadius, cornerIsExternal, startAngle, endAngle });
    } else {
      d = sectorPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle);
    }
    const rest = svgProps(p);
    delete rest.className;
    const attrs = A(rest);
    attrs.class = ['recharts-sector', p.className].filter(Boolean).join(' ');
    attrs.d = d;
    return h('path', attrs);
  }

  function Dot(props) {
    const p = stripUndef(props);
    if (!(isNum(p.cx) && isNum(p.cy) && isNum(p.r))) return null;
    const rest = svgProps(p);
    delete rest.className; delete rest.cx; delete rest.cy; delete rest.r;
    const attrs = A(rest);
    attrs.class = ['recharts-dot', p.className].filter(Boolean).join(' ');
    attrs.cx = p.cx; attrs.cy = p.cy; attrs.r = p.r;
    return h('circle', attrs);
  }

  const validPoint = (pt) => pt != null && pt.x === +pt.x && pt.y === +pt.y;
  function polygonPath(points, connectNulls) {
    let segs = [[]];
    points.forEach((e) => {
      const last = segs[segs.length - 1];
      if (validPoint(e)) last.push(e); else if (last.length > 0) segs.push([]);
    });
    if (validPoint(points[0])) segs[segs.length - 1].push(points[0]);
    if (segs[segs.length - 1].length <= 0) segs = segs.slice(0, -1);
    if (connectNulls) segs = [segs.flat()];
    const path = segs.map((sp) => sp.reduce((acc, pt, i) => rt`${acc}${i === 0 ? 'M' : 'L'}${pt.x},${pt.y}`, '')).join('');
    return segs.length === 1 ? `${path}Z` : path;
  }
  function Polygon(props) {
    const p = stripUndef(props);
    if (!p.points || !p.points.length) return null;
    const others = svgProps(p);
    delete others.points; delete others.className;
    const d = polygonPath(p.points, p.connectNulls);
    const attrs = A(others);
    attrs.fill = d.slice(-1) === 'Z' ? p.fill : 'none';
    if (attrs.fill === undefined) delete attrs.fill;
    attrs.class = ['recharts-polygon', p.className].filter(Boolean).join(' ');
    attrs.d = d;
    return h('path', attrs);
  }

  /* Curve: <path class="recharts-curve"> from points with a d3 curve, optional baseline (area). */
  function curvePath(type, points, baseLine, layout, connectNulls) {
    const curve = d3.curveFactory(type, layout);
    const defined = (pt) => pt != null && isWellBehaved(pt.x) && isWellBehaved(pt.y);
    const fmt = connectNulls ? points.filter(defined) : points;
    if (Array.isArray(baseLine)) {
      const areaPoints = points.map((e, i) => Object.assign({}, e, { base: baseLine[i] }));
      const areaDefined = (d) => d.base != null && defined(d.base) && defined(d);
      const gen = layout === 'vertical'
        ? d3.area({ x0: (d) => d.base.x, x1: (d) => d.x, y0: (d) => d.y, y1: null, defined: areaDefined, curve })
        : d3.area({ x0: (d) => d.x, x1: null, y0: (d) => d.base.y, y1: (d) => d.y, defined: areaDefined, curve });
      return gen(connectNulls ? areaPoints.filter(areaDefined) : areaPoints);
    }
    let gen;
    if (layout === 'vertical' && isNum(baseLine)) gen = d3.area({ x0: () => baseLine, x1: (d) => d.x, y0: (d) => d.y, y1: null, defined, curve });
    else if (isNum(baseLine)) gen = d3.area({ x0: (d) => d.x, x1: null, y0: () => baseLine, y1: (d) => d.y, defined, curve });
    else gen = d3.line({ x: (d) => d.x, y: (d) => d.y, defined, curve });
    return gen(fmt);
  }
  function Curve(props) {
    const p = stripUndef(props);
    if (!p.points || !p.points.length) return null;
    const d = curvePath(p.type || 'linear', p.points, p.baseLine, p.layout, p.connectNulls);
    const rest = svgProps(p);
    delete rest.className; delete rest.points;
    const attrs = A(rest);
    attrs.class = ['recharts-curve', p.className].filter(Boolean).join(' ');
    if (d != null) attrs.d = d;
    return h('path', attrs);
  }

  /* Shape(): user option (object props / custom fn / default) wrapped in its active/inactive layer. */
  function Shape(shapeType, option, props, ctx, classes) {
    let shape;
    const fnRef = option && typeof option === 'object' && option.$fn;
    if (fnRef) {
      shape = NS.formatters.shape(option, props, ctx, { Rectangle, Sector, Curve, h });
    } else if (option && typeof option === 'object') {
      shape = SHAPES[shapeType](Object.assign({}, props, option));
    } else {
      shape = SHAPES[shapeType](props);
    }
    const c = classes || {};
    return h('g', { class: 'recharts-layer ' + (props.isActive ? (c.active || 'recharts-active-shape') : (c.inactive || 'recharts-shape')) }, [shape]);
  }
  const SHAPES = { rectangle: Rectangle, sector: Sector, curve: Curve };

  /* ------------------------------------------------------------------ nice ticks (recharts/util/scale/getNiceTickValues) */
  /* Decimal.js is emulated by rounding intermediate results to 12 significant digits. */
  const dec = (v) => (v === 0 ? 0 : parseFloat(v.toPrecision(12)));
  function digitCount(value) { return value === 0 ? 1 : Math.floor(Math.log10(Math.abs(value))) + 1; }
  function adaptiveStep(roughStep, allowDecimals, correction) {
    if (roughStep <= 0) return 0;
    const dc = digitCount(roughStep);
    const dcv = Math.pow(10, dc);
    const ratio = dec(roughStep / dcv);
    const ratioScale = dc !== 1 ? 0.05 : 0.1;
    const amend = dec((Math.ceil(dec(ratio / ratioScale)) + correction) * ratioScale);
    const formatStep = dec(amend * dcv);
    return allowDecimals ? formatStep : Math.ceil(formatStep);
  }
  function calculateStep(min, max, tickCount, allowDecimals, correction) {
    correction = correction || 0;
    if (!Number.isFinite((max - min) / (tickCount - 1))) return { step: 0, tickMin: 0, tickMax: 0 };
    const step = adaptiveStep(dec((max - min) / (tickCount - 1)), allowDecimals, correction);
    let middle;
    if (min <= 0 && max >= 0) middle = 0;
    else { middle = dec((min + max) / 2); middle = dec(middle - (middle % step)); }
    let below = Math.ceil(dec((middle - min) / step));
    let up = Math.ceil(dec((max - middle) / step));
    const scaleCount = below + up + 1;
    if (scaleCount > tickCount) return calculateStep(min, max, tickCount, allowDecimals, correction + 1);
    if (scaleCount < tickCount) {
      up = max > 0 ? up + (tickCount - scaleCount) : up;
      below = max > 0 ? below : below + (tickCount - scaleCount);
    }
    return { step, tickMin: dec(middle - below * step), tickMax: dec(middle + up * step) };
  }
  function rangeStep(start, end, step) {
    const out = []; let num = start, i = 0;
    while (num < end && i < 100000) { out.push(num); num = dec(num + step); i++; }
    return out;
  }
  function tickOfSingleValue(value, tickCount, allowDecimals) {
    let step = 1, middle = value;
    if (!Number.isInteger(value) && allowDecimals) {
      const abs = Math.abs(value);
      if (abs < 1) { step = Math.pow(10, digitCount(value) - 1); middle = dec(Math.floor(value / step) * step); } else if (abs > 1) middle = Math.floor(value);
    } else if (value === 0) middle = Math.floor((tickCount - 1) / 2);
    else if (!allowDecimals) middle = Math.floor(value);
    const mi = Math.floor((tickCount - 1) / 2);
    return range(0, tickCount).map((i) => dec(middle + (i - mi) * step));
  }
  function getNiceTickValues(domain, tickCount, allowDecimals) {
    const [min, max] = domain;
    const count = Math.max(tickCount, 2);
    const cmin = Math.min(min, max), cmax = Math.max(min, max);
    if (cmin === cmax) return tickOfSingleValue(cmin, tickCount, allowDecimals);
    const { step, tickMin, tickMax } = calculateStep(cmin, cmax, count, allowDecimals, 0);
    const values = rangeStep(tickMin, dec(tickMax + 0.1 * step), step);
    return min > max ? values.reverse() : values;
  }
  function getTickValuesFixedDomain(domain, tickCount, allowDecimals) {
    const [min, max] = domain;
    const cmin = Math.min(min, max), cmax = Math.max(min, max);
    if (cmin === cmax) return [cmin];
    const count = Math.max(tickCount, 2);
    const step = adaptiveStep(dec((cmax - cmin) / (count - 1)), allowDecimals, 0);
    let values = [...rangeStep(cmin, cmax, step), cmax];
    if (allowDecimals === false) values = values.map((v) => Math.round(v));
    return min > max ? values.reverse() : values;
  }

  /* ------------------------------------------------------------------ axis domain & scale */
  const isCategoricalAxis = (layout, axisType) => (layout === 'horizontal' && axisType === 'xAxis') || (layout === 'vertical' && axisType === 'yAxis') || (layout === 'centric' && axisType === 'angleAxis') || (layout === 'radial' && axisType === 'radiusAxis');
  const isWellFormedDomain = (v) => Array.isArray(v) && v.length === 2 && isWellBehaved(v[0]) && isWellBehaved(v[1]);
  function makeNumber(v) { if (isNumOrStr(v) || v instanceof Date) { const n = Number(v); if (isWellBehaved(n)) return n; } return undefined; }
  function makeDomain(v) {
    if (Array.isArray(v)) { const a = [makeNumber(v[0]), makeNumber(v[1])]; return isWellFormedDomain(a) ? a : undefined; }
    const n = makeNumber(v);
    return n == null ? undefined : [n, n];
  }
  function mergeDomains(...domains) {
    const all = domains.filter(Boolean);
    if (!all.length) return undefined;
    const flat = all.flat();
    return [Math.min(...flat), Math.max(...flat)];
  }
  function getDomainDefinition(axis) {
    if (axis.domain != null) return axis.domain;
    if (axis.ticks != null) {
      if (axis.type === 'number') { const v = axis.ticks.map(makeNumber).filter((x) => x != null); return [Math.min(...v), Math.max(...v)]; }
      if (axis.type === 'category') return axis.ticks.map(String);
    }
    return [0, 'auto'];
  }
  function parseNumericalUserDomain(userDomain, dataDomain, allowDataOverflow) {
    if (!allowDataOverflow && dataDomain == null) return undefined;
    if (Array.isArray(userDomain) && userDomain.length === 2) {
      const [pmin, pmax] = userDomain;
      let fmin, fmax;
      if (pmin === 'auto') { if (dataDomain != null) fmin = Math.min(...dataDomain); } else if (isNum(pmin)) fmin = pmin;
      else fmin = dataDomain && dataDomain[0];
      if (pmax === 'auto') { if (dataDomain != null) fmax = Math.max(...dataDomain); } else if (isNum(pmax)) fmax = pmax;
      else fmax = dataDomain && dataDomain[1];
      const cand = [fmin, fmax];
      if (isWellFormedDomain(cand)) {
        if (dataDomain == null || allowDataOverflow) return cand;
        return [Math.min(cand[0], dataDomain[0]), Math.max(cand[1], dataDomain[1])];
      }
    }
    return undefined;
  }
  function realScaleType(axis, hasBar, chartName) {
    if (axis.scale === 'auto' || axis.scale == null) {
      if (axis.type === 'category' && (chartName === 'LineChart' || chartName === 'AreaChart' || (chartName === 'ComposedChart' && !hasBar))) return 'point';
      if (axis.type === 'category') return 'band';
      return 'linear';
    }
    return ['band', 'point', 'linear'].includes(axis.scale) ? axis.scale : 'point';
  }
  function makeScale(type, domain, rangeV) {
    if (type === 'band') return d3.band(domain, rangeV);
    if (type === 'point') return d3.point(domain, rangeV);
    return d3.linear(domain, rangeV);
  }
  function bandSizeOfAxis(axis, ticks, isBar) {
    if (axis && axis.scale && axis.scale.bandwidth) {
      const bw = axis.scale.bandwidth();
      if (!isBar || bw > 0) return bw;
    }
    if (axis && ticks && ticks.length >= 2) {
      const ordered = ticks.slice().sort((a, b) => a.coordinate - b.coordinate);
      let bs = Infinity;
      for (let i = 1; i < ordered.length; i++) bs = Math.min((ordered[i].coordinate || 0) - (ordered[i - 1].coordinate || 0), bs);
      return bs === Infinity ? 0 : bs;
    }
    return isBar ? undefined : 0;
  }

  /* Resolve an axis: domain, nice ticks, scale and the three tick lists Recharts keeps. */
  function resolveAxis(axis, ctx) {
    const { layout, data, items, stackGroups, stackOffset, chartName, hasBar } = ctx;
    const isCat = isCategoricalAxis(layout, axis.axisType);
    const applied = !isNil(axis.dataKey)
      ? data.map((e) => getValue(e, axis.dataKey))
      : (items.length ? items.flatMap((it) => data.map((e) => getValue(e, it.dataKey))) : data.slice());
    let domain;
    if (isCat && isNil(axis.dataKey)) {
      domain = range(0, data.length);
    } else if (axis.type === 'category') {
      const cd = applied.filter((v) => isNumOrStr(v) || v instanceof Date);
      if (isCat && (isNil(axis.dataKey) || (axis.allowDuplicatedCategory && hasDuplicate(cd)))) domain = range(0, applied.length);
      else if (axis.allowDuplicatedCategory) domain = cd;
      else domain = uniqBy(cd, (v) => v);
    } else if (stackOffset === 'expand' && axis.axisType !== 'angleAxis' && axis.axisType !== 'radiusAxis') {
      domain = [0, 1];
    } else {
      const def = getDomainDefinition(axis);
      const includeStack = (layout === 'vertical' && axis.axisType === 'xAxis') || (layout === 'horizontal' && axis.axisType === 'yAxis');
      let stackDomain;
      if (includeStack && stackGroups) {
        let lo = Infinity, hi = -Infinity;
        for (const g of Object.values(stackGroups)) {
          for (const series of g.stackedData) {
            for (const pt of series) for (const v of [pt[0], pt[1]]) if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
          }
        }
        stackDomain = [lo === Infinity ? 0 : lo, hi === -Infinity ? 0 : hi];
        if (stackDomain[0] === 0 && stackDomain[1] === 0) stackDomain = undefined;
      }
      let lower, upper;
      const unstacked = ctx.polar ? items : items.filter((it) => it.stackId == null);
      data.forEach((e) => {
        unstacked.forEach((it) => {
          const dd = makeDomain(getValue(e, isNil(axis.dataKey) ? it.dataKey : axis.dataKey));
          if (dd) { lower = lower == null ? dd[0] : Math.min(lower, dd[0]); upper = upper == null ? dd[1] : Math.max(upper, dd[1]); }
        });
      });
      if (!isNil(axis.dataKey)) {
        data.forEach((e) => {
          const dd = makeDomain(getValue(e, axis.dataKey));
          if (dd) { lower = lower == null ? dd[0] : Math.min(lower, dd[0]); upper = upper == null ? dd[1] : Math.max(upper, dd[1]); }
        });
      }
      const dataDomain = isWellBehaved(lower) && isWellBehaved(upper) ? [lower, upper] : undefined;
      const merged = includeStack ? mergeDomains(stackDomain, dataDomain) : mergeDomains(dataDomain);
      domain = parseNumericalUserDomain(def, merged, axis.allowDataOverflow);
    }
    const scaleType = realScaleType(axis, hasBar, chartName);
    let niceTicks;
    if ((axis.niceTicks || 'auto') !== 'none' && scaleType === 'linear' && axis.tickCount) {
      const def = getDomainDefinition(axis);
      const hasAuto = Array.isArray(def) && (def[0] === 'auto' || def[1] === 'auto');
      if (hasAuto && isWellFormedDomain(domain)) niceTicks = getNiceTickValues(domain, axis.tickCount, axis.allowDecimals);
      else if (axis.type === 'number' && isWellFormedDomain(domain)) niceTicks = getTickValuesFixedDomain(domain, axis.tickCount, axis.allowDecimals);
    }
    let fullDomain = domain;
    if (axis.axisType !== 'angleAxis' && axis.type === 'number' && isWellFormedDomain(domain) && niceTicks && niceTicks.length) {
      fullDomain = [Math.min(domain[0], niceTicks[0]), Math.max(domain[1], niceTicks[niceTicks.length - 1])];
    }
    if (scaleType === 'linear' && fullDomain && !isWellFormedDomain(fullDomain)) {
      const nums = fullDomain.filter(isWellBehaved);
      fullDomain = nums.length ? [Math.min(...nums), Math.max(...nums)] : undefined;
    }
    const rangeV = axis.reversed ? [axis.range[1], axis.range[0]] : axis.range;
    const scale = fullDomain ? makeScale(scaleType, fullDomain, rangeV) : null;
    const categoricalDomain = isCat && !isNil(axis.dataKey) && (axis.type === 'number' || (axis.scale && axis.scale !== 'auto')) ? applied : undefined;
    const duplicateDomain = !isNil(axis.dataKey) && isCat && axis.type === 'category' && axis.allowDuplicatedCategory && hasDuplicate(applied) ? applied : undefined;
    Object.assign(axis, { isCategorical: isCat, domain: fullDomain, niceTicks, realScaleType: scaleType, scale, categoricalDomain, duplicateDomain });
    if (!scale) { axis.ticks_ = []; axis.itemTicks = []; axis.tooltipTicks = []; return axis; }
    const bw = scale.bandwidth ? scale.bandwidth() : 0;
    let offset = axis.type === 'category' && scale.bandwidth ? bw / 2 : 0;
    if (axis.axisType === 'angleAxis' && rangeV && rangeV.length >= 2) offset = mathSign(rangeV[0] - rangeV[1]) * 2 * offset;
    const mk = (list, off, valueOf) => list.map((entry, index) => {
      const sc = scale(valueOf ? valueOf(entry) : entry);
      return isWellBehaved(sc) ? { coordinate: sc + off, value: entry, index, offset: off } : null;
    }).filter(Boolean);
    // combineAxisTicks (axis labels, grid, polar grid)
    const userTicks = axis.ticksProp || niceTicks;
    if (userTicks) axis.ticks_ = mk(userTicks, offset, (e) => (duplicateDomain ? duplicateDomain.indexOf(e) : e));
    else if (isCat && categoricalDomain) axis.ticks_ = mk(categoricalDomain, offset);
    else if (scale.ticks) axis.ticks_ = mk(scale.ticks(axis.tickCount), offset);
    else axis.ticks_ = scale.domain().map((entry, index) => { const sc = scale(entry); return isWellBehaved(sc) ? { coordinate: sc + offset, value: duplicateDomain ? duplicateDomain[entry] : entry, index, offset } : null; }).filter(Boolean);
    // combineGraphicalItemTicks (positions of bars, points)
    let goff = 0;
    if (isCat && categoricalDomain) axis.itemTicks = mk(categoricalDomain, goff);
    else if (scale.ticks) axis.itemTicks = mk(scale.ticks(axis.tickCount), goff);
    else axis.itemTicks = scale.domain().map((entry, index) => { const sc = scale(entry); return isWellBehaved(sc) ? { coordinate: sc + goff, value: duplicateDomain ? duplicateDomain[entry] : entry, index, offset: goff } : null; }).filter(Boolean);
    // combineTicksOfTooltipAxis
    if (isCat && categoricalDomain) axis.tooltipTicks = mk(categoricalDomain, offset);
    else axis.tooltipTicks = scale.domain().map((entry, index) => { const sc = scale(entry); return isWellBehaved(sc) ? { coordinate: sc + offset, value: duplicateDomain ? duplicateDomain[entry] : entry, index, offset } : null; }).filter(Boolean);
    return axis;
  }

  /* ------------------------------------------------------------------ cartesian axis tick thinning (getTicks) */
  function isVisible(sign, pos, getSize, start, end) {
    if (sign * pos < sign * start || sign * pos > sign * end) return false;
    const size = getSize();
    return sign * (pos - (sign * size) / 2 - start) >= 0 && sign * (pos + (sign * size) / 2 - end) <= 0;
  }
  function getTicks(props, fontSize, letterSpacing) {
    const { tick, ticks, viewBox, minTickGap, orientation, interval, tickFormatter, unit, angle } = props;
    if (!ticks || !ticks.length || !tick) return [];
    if (isNum(interval)) return ticks.filter((_, i) => i % (interval + 1) === 0);
    const sizeKey = orientation === 'top' || orientation === 'bottom' ? 'width' : 'height';
    const style = { fontSize, letterSpacing };
    const unitSize = unit && sizeKey === 'width' ? getStringSize(unit, style) : { width: 0, height: 0 };
    const getTickSize = (content, index) => {
      const value = typeof tickFormatter === 'function' ? tickFormatter(content.value, index) : content.value;
      if (sizeKey === 'width') {
        const s = getStringSize(value, style);
        return angledWidth({ width: s.width + unitSize.width, height: s.height + unitSize.height }, angle);
      }
      return getStringSize(value, style)[sizeKey];
    };
    const sign = ticks.length >= 2 ? mathSign(ticks[1].coordinate - ticks[0].coordinate) : 1;
    const isW = sizeKey === 'width';
    const b = sign === 1
      ? { start: isW ? viewBox.x : viewBox.y, end: isW ? viewBox.x + viewBox.width : viewBox.y + viewBox.height }
      : { start: isW ? viewBox.x + viewBox.width : viewBox.y + viewBox.height, end: isW ? viewBox.x : viewBox.y };
    let candidates;
    if (interval === 'equidistantPreserveStart') return equidistantTicks(sign, b, getTickSize, ticks, minTickGap);
    if (interval === 'preserveStart' || interval === 'preserveStartEnd') candidates = ticksStart(sign, b, getTickSize, ticks, minTickGap, interval === 'preserveStartEnd');
    else candidates = ticksEnd(sign, b, getTickSize, ticks, minTickGap);
    return candidates.filter((e) => e.isShow);
  }
  function angledWidth(size, angle) {
    const a = (((angle || 0) % 180) + 180) % 180 * RADIAN;
    const th = Math.atan(size.height / size.width);
    const w = a > th && a < Math.PI - th ? size.height / Math.sin(a) : size.width / Math.cos(a);
    return Math.abs(w);
  }
  function ticksEnd(sign, b, getTickSize, ticks, minTickGap) {
    const result = ticks.slice();
    const len = result.length;
    let { start, end } = b;
    for (let i = len - 1; i >= 0; i--) {
      let entry = result[i];
      let size;
      const getSize = () => { if (size === undefined) size = getTickSize(ticks[i], i); return size; };
      if (i === len - 1) {
        const gap = sign * (entry.coordinate + (sign * getSize()) / 2 - end);
        entry = result[i] = Object.assign({}, entry, { tickCoord: gap > 0 ? entry.coordinate - gap * sign : entry.coordinate });
      } else {
        entry = result[i] = Object.assign({}, entry, { tickCoord: entry.coordinate });
      }
      if (isVisible(sign, entry.tickCoord, getSize, start, end)) {
        end = entry.tickCoord - sign * (getSize() / 2 + minTickGap);
        result[i] = Object.assign({}, entry, { isShow: true });
      }
    }
    return result;
  }
  function ticksStart(sign, b, getTickSize, ticks, minTickGap, preserveEnd) {
    const result = ticks.slice();
    const len = result.length;
    let { start, end } = b;
    if (preserveEnd) {
      let tail = ticks[len - 1];
      const tailSize = getTickSize(tail, len - 1);
      const tailGap = sign * (tail.coordinate + (sign * tailSize) / 2 - end);
      tail = result[len - 1] = Object.assign({}, tail, { tickCoord: tailGap > 0 ? tail.coordinate - tailGap * sign : tail.coordinate });
      if (isVisible(sign, tail.tickCoord, () => tailSize, start, end)) {
        end = tail.tickCoord - sign * (tailSize / 2 + minTickGap);
        result[len - 1] = Object.assign({}, tail, { isShow: true });
      }
    }
    const count = preserveEnd ? len - 1 : len;
    for (let i = 0; i < count; i++) {
      let entry = result[i];
      let size;
      const getSize = () => { if (size === undefined) size = getTickSize(ticks[i], i); return size; };
      if (i === 0) {
        const gap = sign * (entry.coordinate - (sign * getSize()) / 2 - start);
        entry = result[i] = Object.assign({}, entry, { tickCoord: gap < 0 ? entry.coordinate - gap * sign : entry.coordinate });
      } else {
        entry = result[i] = Object.assign({}, entry, { tickCoord: entry.coordinate });
      }
      if (isVisible(sign, entry.tickCoord, getSize, start, end)) {
        start = entry.tickCoord + sign * (getSize() / 2 + minTickGap);
        result[i] = Object.assign({}, entry, { isShow: true });
      }
    }
    return result;
  }
  function equidistantTicks(sign, b, getTickSize, ticks, minTickGap) {
    let stepsize = 1, index = 0, start = b.start;
    while (stepsize <= ticks.length) {
      const entry = ticks[index];
      if (entry === undefined) return ticks.filter((_, i) => i % stepsize === 0);
      let size;
      const i = index;
      const getSize = () => { if (size === undefined) size = getTickSize(entry, i); return size; };
      const isShow = index === 0 || isVisible(sign, entry.coordinate, getSize, start, b.end);
      if (!isShow) { index = 0; start = b.start; stepsize += 1; } else { start = entry.coordinate + sign * (getSize() / 2 + minTickGap); index += stepsize; }
    }
    return [];
  }

  /* ------------------------------------------------------------------ exports for the chart module */
  NS.internal = {
    isNil, isNum, isWellBehaved, isNumOrStr, isPercent, mathSign, range, getPercentValue, getValue, round, rt,
    polarToCartesian, sortByKey, uniqBy, getStringSize, reduceCSSCalc, h, txt, svgProps, A, toDom, Text,
    Rectangle, Sector, Dot, Polygon, Curve, Shape, curvePath, rectanglePath, sectorPath,
    getNiceTickValues, getTickValuesFixedDomain, isCategoricalAxis, resolveAxis, bandSizeOfAxis, getTicks,
    SVG_NS, XHTML_NS, stripUndef,
  };
})();

/* Chart: the runtime behind shadcn's ChartContainer.

   A [data-slot="chart"] element carries its spec in a child <script type="application/json" data-chart-spec>
   (written by the `chart` macro, see flask_basecoat/charts.py). The runtime measures the
   .recharts-responsive-container, draws the Recharts DOM into .recharts-wrapper (chart-surface.js and
   chart-polar.js do the layout), keeps it in sync with the container size, and shows the tooltip
   (ChartTooltipContent markup) on hover and the legend (ChartLegendContent markup).

   The older attribute API still works and is translated to a spec:
     <div data-slot="chart" data-chart-type="bar|stacked-bar|line|area|stacked-area|pie|donut|radial"
          data-chart-x="month" data-chart-data='[...]' data-chart-series='[{"key":"desktop","label":"Desktop"}]'
          data-chart-legend data-chart-grid data-chart-y-axis data-chart-x-short data-chart-dots>

   Interactive charts: an element with data-chart-control="<the chart's data-chart value>" and data-chart-state="<name>"
   sets that state value from its basecoat:change event (select, single toggle group) or from data-chart-value on
   click (buttons), then the chart redraws; the other controls bound to the same state show the new value.
   spec.onMedia sets state from a media query (upstream useIsMobile effects). */
(function (FB) {
  'use strict';
  const NS = (window.FlaskBasecoatChart = window.FlaskBasecoatChart || {});
  let UID = 0;
  let CLIP_UID = 0;

  function L() { return NS.internal; }

  /* ---------------------------------------------------------------- legacy attribute API */
  function parseAttr(root, name, fallback) {
    try { const v = root.getAttribute(name); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; }
  }
  function legacySpec(root) {
    const data = parseAttr(root, 'data-chart-data', null);
    if (!Array.isArray(data) || !data.length) return null;
    const type = root.getAttribute('data-chart-type') || 'bar';
    const xKey = root.getAttribute('data-chart-x') || Object.keys(data[0])[0];
    const series = parseAttr(root, 'data-chart-series', Object.keys(data[0]).filter((k) => k !== xKey && k !== 'fill').map((k) => ({ key: k, label: k })));
    const config = {};
    series.forEach((s, i) => { config[s.key] = { label: s.label || s.key, color: s.color || `var(--chart-${(i % 5) + 1})` }; });
    const stacked = type === 'stacked-bar' || type === 'stacked-area';
    const xAxis = { component: 'XAxis', dataKey: xKey, tickLine: false, axisLine: false, tickMargin: 10 };
    if (root.hasAttribute('data-chart-x-short')) xAxis.tickFormatter = { $fn: 'slice', start: 0, end: 3 };
    const axes = [xAxis];
    if (root.hasAttribute('data-chart-y-axis')) axes.push({ component: 'YAxis', tickLine: false, axisLine: false, tickMargin: 8 });
    const grid = root.hasAttribute('data-chart-no-grid') ? null : { component: 'CartesianGrid', vertical: false };
    const legend = root.hasAttribute('data-chart-legend') ? { content: 'ChartLegendContent' } : null;
    if (type === 'pie' || type === 'donut' || type === 'radial') {
      const key = series[0].key;
      const rows = data.map((d, i) => Object.assign({ fill: `var(--color-${String(d[xKey]).toLowerCase().replace(/\s+/g, '-')}, var(--chart-${(i % 5) + 1}))` }, d));
      data.forEach((d, i) => { config[String(d[xKey]).toLowerCase().replace(/\s+/g, '-')] = { label: String(d[xKey]), color: `var(--chart-${(i % 5) + 1})` }; });
      if (type === 'radial') {
        return { type: 'radial', component: 'RadialBarChart', config, data: rows, props: { innerRadius: 30, outerRadius: 110 }, series: [{ component: 'RadialBar', dataKey: key, background: true }], axes: [], grid: null, tooltip: { cursor: false, content: 'ChartTooltipContent', hideLabel: true, nameKey: xKey }, legend, labels: [] };
      }
      return { type: 'pie', component: 'PieChart', config, data: rows, props: {}, series: [Object.assign({ component: 'Pie', data: rows, dataKey: key, nameKey: xKey }, type === 'donut' ? { innerRadius: 60, strokeWidth: 5 } : {})], axes: [], grid: null, tooltip: { cursor: false, content: 'ChartTooltipContent', hideLabel: true }, legend: legend ? Object.assign({ nameKey: xKey }, legend) : null, labels: [] };
    }
    const comp = { bar: 'Bar', 'stacked-bar': 'Bar', line: 'Line', area: 'Area', 'stacked-area': 'Area' }[type] || 'Bar';
    const chartComp = { Bar: 'BarChart', Line: 'LineChart', Area: 'AreaChart' }[comp];
    const s = series.map((sr, i) => {
      const o = { component: comp, dataKey: sr.key };
      if (comp === 'Bar') { o.fill = `var(--color-${sr.key})`; o.radius = stacked ? (i === 0 ? [0, 0, 4, 4] : i === series.length - 1 ? [4, 4, 0, 0] : 0) : 4; }
      if (comp === 'Line') { o.type = 'natural'; o.stroke = `var(--color-${sr.key})`; o.strokeWidth = 2; o.dot = root.hasAttribute('data-chart-dots') ? { fill: `var(--color-${sr.key})` } : false; }
      if (comp === 'Area') { o.type = 'natural'; o.fill = `var(--color-${sr.key})`; o.fillOpacity = 0.4; o.stroke = `var(--color-${sr.key})`; }
      if (stacked) o.stackId = 'a';
      return o;
    });
    return { type: comp.toLowerCase(), component: chartComp, config, data, props: { accessibilityLayer: true, margin: comp === 'Bar' ? undefined : { left: 12, right: 12 } }, series: s, axes, grid, tooltip: { cursor: false, content: 'ChartTooltipContent', indicator: comp === 'Bar' ? 'dashed' : 'dot' }, legend, labels: [] };
  }

  /* Inject the --color-<key> style the ChartContainer would (only for the attribute API). */
  function ensureStyle(root, spec) {
    if (root.querySelector(':scope > style')) return;
    const id = root.getAttribute('data-chart') || `chart-${++UID}`;
    root.setAttribute('data-chart', id);
    const keys = Object.entries(spec.config).filter(([, c]) => c.theme || c.color);
    if (!keys.length) return;
    const style = document.createElement('style');
    // Entelim change: the page's content security policy admits a style element only with the page's nonce.
    const holder = document.querySelector('style[nonce], script[nonce]');
    if (holder && holder.nonce) style.setAttribute('nonce', holder.nonce);
    style.textContent = ['', '.dark'].map((prefix) => `\n${prefix} [data-chart=${id}] {\n${keys.map(([k, c]) => `  --color-${k}: ${(c.theme && c.theme[prefix ? 'dark' : 'light']) || c.color};`).join('\n')}\n}\n`).join('\n');
    root.insertBefore(style, root.firstChild);
  }

  /* ---------------------------------------------------------------- tooltip and legend content (shadcn chart.tsx) */
  function payloadConfig(config, payload, key) {
    if (typeof payload !== 'object' || payload === null) return undefined;
    const pp = payload.payload && typeof payload.payload === 'object' ? payload.payload : undefined;
    let k = key;
    if (key in payload && typeof payload[key] === 'string') k = payload[key];
    else if (pp && key in pp && typeof pp[key] === 'string') k = pp[key];
    return k in config ? config[k] : config[key];
  }
  function htmlEl(tag, cls, style) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (style) Object.keys(style).forEach((k) => { if (k.startsWith('--')) n.style.setProperty(k, style[k]); else n.style[k] = style[k]; });
    return n;
  }
  function textOf(v) { return v == null ? '' : String(v); }

  function tooltipContent(chart, t, payload, label) {
    const config = chart.ctx.config;
    const indicator = t.indicator || 'dot';
    const hideLabel = !!t.hideLabel, hideIndicator = !!t.hideIndicator;
    const labelFormatter = chart.fn(t.labelFormatter);
    const formatter = chart.fn(t.formatter);
    const nameKey = t.nameKey, labelKey = t.labelKey;
    let tooltipLabel = null;
    if (!hideLabel && payload.length) {
      const item = payload[0];
      const key = `${labelKey != null ? labelKey : (item.dataKey != null ? item.dataKey : (item.name != null ? item.name : 'value'))}`;
      const itemConfig = payloadConfig(config, item, key);
      const value = !labelKey && typeof label === 'string' ? (config[label] && config[label].label != null ? config[label].label : label) : itemConfig && itemConfig.label;
      if (labelFormatter) { tooltipLabel = htmlEl('div', t.$labelClass || 'font-medium'); appendContent(tooltipLabel, labelFormatter(value, payload)); } else if (value) { tooltipLabel = htmlEl('div', t.$labelClass || 'font-medium'); tooltipLabel.textContent = value; }
    }
    const nestLabel = payload.length === 1 && indicator !== 'dot';
    const root = htmlEl('div', t.$class || 'cn-chart-tooltip grid min-w-32 items-start');
    if (!nestLabel && tooltipLabel) root.appendChild(tooltipLabel);
    const list = htmlEl('div', 'grid gap-1.5');
    payload.filter((item) => item.type !== 'none').forEach((item, index) => {
      const key = `${nameKey != null ? nameKey : (item.name != null ? item.name : (item.dataKey != null ? item.dataKey : 'value'))}`;
      const itemConfig = payloadConfig(config, item, key);
      const indicatorColor = t.color != null ? t.color : (item.payload && item.payload.fill != null ? item.payload.fill : item.color);
      const row = htmlEl('div', indicator === 'dot' ? 'flex w-full flex-wrap gap-2 [&>svg]:h-2.5 [&>svg]:w-2.5 [&>svg]:text-muted-foreground items-center' : 'flex w-full flex-wrap items-stretch gap-2 [&>svg]:h-2.5 [&>svg]:w-2.5 [&>svg]:text-muted-foreground');
      if (formatter && item.value !== undefined && item.name) {
        appendContent(row, formatter(item.value, item.name, item, index, item.payload));
      } else {
        if (itemConfig && itemConfig.icon) {
          row.insertAdjacentHTML('beforeend', chart.iconMarkup(itemConfig.icon));
        } else if (!hideIndicator) {
          const cls = ['shrink-0 rounded-[2px] border-(--color-border)'];
          if (indicator === 'dot') cls.push('bg-(--color-bg) h-2.5 w-2.5');
          else if (indicator === 'line') cls.push('bg-(--color-bg) w-1');
          else cls.push('w-0 border-[1.5px] border-dashed bg-transparent');
          if (nestLabel && indicator === 'dashed') cls.push('my-0.5');
          row.appendChild(htmlEl('div', cls.join(' '), { '--color-bg': indicatorColor, '--color-border': indicatorColor }));
        }
        const body = htmlEl('div', `flex flex-1 justify-between leading-none ${nestLabel ? 'items-end' : 'items-center'}`);
        const names = htmlEl('div', 'grid gap-1.5');
        if (nestLabel && tooltipLabel) names.appendChild(tooltipLabel);
        const nm = htmlEl('span', 'text-muted-foreground');
        nm.textContent = textOf(itemConfig && itemConfig.label != null ? itemConfig.label : item.name);
        names.appendChild(nm);
        body.appendChild(names);
        if (item.value != null) {
          const v = htmlEl('span', 'font-mono font-medium text-foreground tabular-nums');
          v.textContent = typeof item.value === 'number' ? item.value.toLocaleString() : String(item.value);
          body.appendChild(v);
        }
        row.appendChild(body);
      }
      list.appendChild(row);
    });
    root.appendChild(list);
    return root;
  }
  function appendContent(parent, content) {
    if (content == null || content === false) return;
    if (content instanceof Node) parent.appendChild(content);
    else parent.appendChild(document.createTextNode(String(content)));
  }

  function legendContent(chart, model) {
    const config = chart.ctx.config;
    const lg = model.props;
    const payload = model.payload;
    if (!payload || !payload.length) return null;
    const root = htmlEl('div', lg.$class || `flex items-center justify-center gap-4 ${lg.verticalAlign === 'top' ? 'pb-3' : 'pt-3'}`);
    payload.filter((item) => item.type !== 'none').forEach((item) => {
      const key = `${lg.nameKey != null ? lg.nameKey : (item.dataKey != null ? item.dataKey : 'value')}`;
      const itemConfig = payloadConfig(config, item, key);
      const row = htmlEl('div', 'flex items-center gap-1.5 [&>svg]:h-3 [&>svg]:w-3 [&>svg]:text-muted-foreground');
      if (itemConfig && itemConfig.icon && !lg.hideIcon) row.insertAdjacentHTML('beforeend', chart.iconMarkup(itemConfig.icon));
      else row.appendChild(htmlEl('div', 'h-2 w-2 shrink-0 rounded-[2px]', { backgroundColor: item.color }));
      if (itemConfig && itemConfig.label != null) row.appendChild(document.createTextNode(String(itemConfig.label)));
      root.appendChild(row);
    });
    return root;
  }

  /* derive / computed functions of interactive specs (see flask_basecoat/charts.py FORMATTERS) */
  const DERIVE = {
    sinceDays(o, rows, state) {
      const reference = new Date(o.reference);
      const days = (o.days && o.days[state[o.state]]) || o.default;
      const start = new Date(reference);
      start.setDate(start.getDate() - days);
      return rows.filter((r) => new Date(r[o.key]) >= start);
    },
    indexOf(o, rows, state) { return rows.findIndex((r) => r[o.key] === state[o.state]); },
  };
  NS.derive = DERIVE;

  /* ---------------------------------------------------------------- the chart */
  class Chart {
    constructor(root, spec) {
      this.root = root;
      this.spec = spec;
      this.uid = ++UID;
      this.clipId = `recharts${++CLIP_UID}-clip`;
      this.state = Object.assign({}, spec.state || {});
      this.active = null;
      this.ctx = this.makeContext();
      this.mount();
    }

    makeContext() {
      const I = L();
      const chart = this;
      const ctx = {
        config: this.spec.config || {},
        state: this.state,
        get data() { return chart.data(); },
        iconMarkup: (name, attrs) => chart.iconMarkup(name, attrs),
        shapes: { Rectangle: I.Rectangle, Sector: I.Sector, Curve: I.Curve, Dot: I.Dot, h: I.h },
      };
      return ctx;
    }

    /* Chart rows, after the interactive filter (if any). */
    data() {
      const spec = this.spec;
      let rows = spec.data || [];
      if (spec.derive && spec.derive.$fn) rows = DERIVE[spec.derive.$fn](spec.derive, rows, this.state);
      return rows;
    }

    /* State values computed from other state (computed: {name: {"$fn": ...}}). */
    applyComputed() {
      const comp = this.spec.computed || {};
      Object.keys(comp).forEach((k) => { this.state[k] = DERIVE[comp[k].$fn](comp[k], this.spec.data || [], this.state); });
    }

    fn(ref) { return NS.surface.fn(ref, this.ctx); }

    iconMarkup(name, attrs) {
      let markup = (this.spec.icons || {})[name] || '';
      if (attrs && markup) {
        const extra = Object.keys(attrs).filter((k) => attrs[k] != null).map((k) => ` ${k}="${String(attrs[k]).replace(/"/g, '&quot;')}"`).join('');
        markup = markup.replace(/<svg /, '<svg ').replace(/ fill="none"/, attrs.fill != null ? '' : ' fill="none"').replace(/ stroke="currentColor"/, attrs.stroke != null ? '' : ' stroke="currentColor"');
        markup = markup.replace(/ width="24" height="24"/, '').replace(/^<svg/, `<svg${extra}`);
      }
      return markup;
    }

    mount() {
      const root = this.root;
      let container = root.querySelector(':scope > .recharts-responsive-container');
      if (!container) {
        container = document.createElement('div');
        container.className = 'recharts-responsive-container';
        container.setAttribute('style', 'width: 100%; height: 100%; min-width: 0px;');
        const inner = document.createElement('div');
        inner.setAttribute('style', 'width: 0px; height: 0px; overflow: visible;');
        container.appendChild(inner);
        root.appendChild(container);
      }
      this.container = container;
      this.inner = container.firstElementChild;
      let wrapper = this.inner.querySelector(':scope > .recharts-wrapper');
      if (!wrapper) { wrapper = document.createElement('div'); wrapper.className = 'recharts-wrapper'; this.inner.appendChild(wrapper); }
      this.wrapper = wrapper;
      wrapper.innerHTML = '';
      this.tooltipEl = document.createElement('div');
      this.tooltipEl.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
      this.tooltipEl.setAttribute('tabindex', '-1');
      this.tooltipEl.className = 'recharts-tooltip-wrapper';
      this.tooltipEl.setAttribute('style', 'visibility: hidden; pointer-events: none; position: absolute; top: 0px; left: 0px;');
      if (this.spec.tooltip) wrapper.appendChild(this.tooltipEl);
      const t = this.spec.tooltip;
      if (t && t.defaultIndex != null) this.defaultIndex = Number(t.defaultIndex);
      wrapper.addEventListener('mousemove', (e) => this.onMove(e));
      wrapper.addEventListener('mouseleave', () => this.onLeave());
      // accessibility layer: focus shows the first category, arrows move, Enter toggles, blur hides
      this.kb = { active: false, index: null };
      wrapper.addEventListener('focusin', (e) => { if (e.target === this.svg) this.onFocus(); });
      wrapper.addEventListener('focusout', (e) => { if (e.target === this.svg) this.onBlur(); });
      wrapper.addEventListener('keydown', (e) => this.onKey(e));
      wrapper.addEventListener('touchmove', (e) => { if (e.touches[0]) this.onMove(e.touches[0], e); }, { passive: true });
      this.size = { width: 0, height: 0 };
      const measure = () => {
        const r = container.getBoundingClientRect();
        const w = Math.round(r.width), h = Math.round(r.height);
        if (w !== this.size.width || h !== this.size.height) { this.size = { width: w, height: h }; this.render(); }
      };
      this.measure = measure;
      if (typeof ResizeObserver !== 'undefined') { this.ro = new ResizeObserver(() => measure()); this.ro.observe(container); }
      measure();
    }

    setState(name, value) {
      this.state[name] = value;
      this.mouse = null;
      this.render();
    }

    /* Which interaction shows the tooltip: pointer, then keyboard, then the defaultIndex until the pointer was used. */
    resolveActive() {
      if (this.mouse) return this.mouse;
      if (this.kb && this.kb.active && this.kb.index != null) return this.activeForIndex(this.kb.index, null);
      if (this.defaultIndex != null && !this.pointerSeen) return this.activeForIndex(this.defaultIndex, null);
      return null;
    }

    accessibilityLayer() { return !(this.spec.props && this.spec.props.accessibilityLayer === false); }
    onFocus() {
      if (!this.accessibilityLayer() || !this.spec.tooltip || this.tooltipEventType() !== 'axis' || this.kb.active) return;
      if (this.kb.index == null) { this.kb = { active: true, index: 0 }; this.active = this.resolveActive(); this.draw(); }
    }
    onBlur() {
      if (!this.accessibilityLayer() || !this.kb.active) return;
      this.kb = { active: false, index: this.kb.index };
      this.active = this.resolveActive();
      this.draw();
    }
    onKey(e) {
      if (!this.accessibilityLayer() || !this.spec.tooltip || this.tooltipEventType() !== 'axis' || !this.model) return;
      const key = e.key;
      if (key !== 'ArrowRight' && key !== 'ArrowLeft' && key !== 'Enter') return;
      const current = this.kb.index == null ? -1 : Number(this.kb.index);
      if (!(current >= 0)) return;
      if (key === 'Enter') this.kb = { active: !this.kb.active, index: this.kb.index };
      else {
        const m = this.model;
        const reversed = m.kind === 'cartesian' && (m.layout === 'horizontal' ? m.xAxis.reversed : m.yAxis.reversed);
        const next = current + (key === 'ArrowRight' ? 1 : -1) * (reversed ? -1 : 1);
        if (!m.tooltipTicks || next >= m.tooltipTicks.length || next < 0) return;
        this.kb = { active: true, index: next };
      }
      this.active = this.resolveActive();
      this.draw();
    }

    /* ---- measurement helpers used by the layout ---- */
    axisFontSize() {
      if (this._font) return this._font;
      const I = L();
      const probe = document.createElementNS(I.SVG_NS, 'svg');
      probe.setAttribute('style', 'position:absolute;visibility:hidden;width:0;height:0');
      const t = document.createElementNS(I.SVG_NS, 'text');
      t.setAttribute('class', 'recharts-text recharts-cartesian-axis-tick-value');
      t.textContent = '0';
      const g = document.createElementNS(I.SVG_NS, 'g');
      g.setAttribute('class', 'recharts-cartesian-axis-tick');
      g.appendChild(t);
      probe.appendChild(g);
      this.wrapper.appendChild(probe);
      const cs = getComputedStyle(t);
      this._font = { fontSize: cs.fontSize, letterSpacing: cs.letterSpacing };
      probe.remove();
      return this._font;
    }
    pathLength(d) {
      if (!d) return 0;
      const I = L();
      const probe = document.createElementNS(I.SVG_NS, 'svg');
      probe.setAttribute('style', 'position:absolute;visibility:hidden;width:0;height:0');
      const p = document.createElementNS(I.SVG_NS, 'path');
      p.setAttribute('d', d);
      probe.appendChild(p);
      this.wrapper.appendChild(probe);
      let len = 0;
      try { len = p.getTotalLength(); } catch (e) { len = 0; }
      probe.remove();
      return len;
    }
    measureLegend(model) {
      let el = this.wrapper.querySelector(':scope > .recharts-legend-wrapper');
      if (!el) {
        el = document.createElement('div');
        el.className = 'recharts-legend-wrapper';
        if (this.tooltipEl.parentNode) this.tooltipEl.insertAdjacentElement('afterend', el); else this.wrapper.insertBefore(el, this.wrapper.firstChild);
      }
      el.setAttribute('style', Object.entries(model.style).map(([k, v]) => `${k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase())}: ${v};`).join(' '));
      el.innerHTML = '';
      const content = legendContent(this, model);
      if (content) el.appendChild(content);
      this.legendEl = el;
      const r = el.getBoundingClientRect();
      return { width: r.width, height: r.height };
    }

    /* ---- render ---- */
    render() {
      const I = L();
      this.applyComputed();
      const { width, height } = this.size;
      this.width = width; this.height = height;
      const w = this.wrapper;
      w.setAttribute('width', width);
      w.setAttribute('height', height);
      w.setAttribute('style', `position: relative; cursor: default; width: ${width}px; height: ${height}px;`);
      const old = w.querySelector(':scope > svg.recharts-surface');
      if (!(width > 0 && height > 0)) { if (old) old.remove(); return; }
      const polar = ['PieChart', 'RadarChart', 'RadialBarChart'].includes(this.spec.component);
      this.model = polar ? NS.polar.buildPolar(this) : NS.surface.buildCartesian(this);
      if (!this.spec.legend && this.legendEl) { this.legendEl.remove(); this.legendEl = null; }
      if (this.mouse && this.mouse.pointer) this.mouse = this.computeActive(this.mouse.pointer, this.mouse.itemTarget);
      this.active = this.resolveActive();
      this.draw();
    }

    draw() {
      const I = L();
      const m = this.model;
      const polar = m.kind === 'polar';
      const { layers, z0 } = polar ? NS.polar.renderPolar(this, m, this.active) : NS.surface.renderCartesian(this, m, this.active);
      const { h } = I;
      const svgAttrs = {};
      if (polar) { svgAttrs.cx = m.props.cx; svgAttrs.cy = m.props.cy; }
      const access = this.spec.props && this.spec.props.accessibilityLayer === false ? false : true;
      if (access) { svgAttrs.role = 'application'; svgAttrs.tabindex = 0; }
      Object.assign(svgAttrs, { class: 'recharts-surface', width: m.width, height: m.height, viewBox: `0 0 ${m.width} ${m.height}`, style: 'width: 100%; height: 100%; display: block;' });
      const off = m.offset;
      const kids = [h('title', {}, []), h('desc', {}, [])];
      const layer = (z) => h('g', { tabindex: -1, class: `recharts-zIndex-layer_${z}` }, layers[z]);
      kids.push(layer(-100), layer(-50));
      kids.push(h('defs', {}, [h('clipPath', { id: this.clipId }, [h('rect', { x: off.left, y: off.top, height: off.height, width: off.width })])]));
      if (this.spec.defs && this.spec.defs.length) kids.push(h('defs', {}, this.spec.defs.map(defNode)));
      z0.forEach((n) => kids.push(n));
      NS.surface.Z_LAYERS.filter((z) => z > 0).forEach((z) => kids.push(layer(z)));
      const svgNode = h('svg', svgAttrs, kids);
      const svg = I.toDom(svgNode);
      const old = this.wrapper.querySelector(':scope > svg.recharts-surface');
      const hadFocus = old && document.activeElement === old;
      this.svg = svg;
      if (old) old.replaceWith(svg); else this.wrapper.appendChild(svg);
      if (hadFocus) svg.focus({ preventScroll: true });
      this.drawTooltip();
    }

    /* ---- tooltip ---- */
    tooltipEventType() {
      const t = this.spec.tooltip || {};
      if (this.spec.component === 'PieChart') return t.shared === true ? 'axis' : 'item';
      if (this.spec.component === 'RadarChart') return 'axis';
      if (this.spec.component === 'RadialBarChart') return t.shared === false ? 'item' : 'axis';
      return t.shared === false ? 'item' : 'axis';
    }

    relative(e) {
      const rect = this.wrapper.getBoundingClientRect();
      const sx = this.wrapper.offsetWidth > 0 ? rect.width / this.wrapper.offsetWidth : 1;
      const sy = this.wrapper.offsetHeight > 0 ? rect.height / this.wrapper.offsetHeight : 1;
      return { relativeX: Math.round((e.clientX - rect.left) / sx), relativeY: Math.round((e.clientY - rect.top) / sy) };
    }

    onMove(e) {
      if (!this.spec.tooltip || !this.model) return;
      this.pointerSeen = true;
      const pointer = this.relative(e);
      let itemTarget = null;
      if (this.tooltipEventType() === 'item') {
        const t = e.target && e.target.closest ? e.target.closest('[data-recharts-item-index]') : null;
        itemTarget = t ? { index: Number(t.getAttribute('data-recharts-item-index')), id: t.getAttribute('data-recharts-item-id') } : null;
      }
      this.mouse = this.computeActive(pointer, itemTarget);
      const next = this.resolveActive();
      const prev = this.active;
      const key = (a) => (a ? `${a.index}|${a.itemIndex}|${a.coordinate ? a.coordinate.x : ''},${a.coordinate ? a.coordinate.y : ''}` : '');
      const indexChanged = (next ? `${next.index}|${next.itemIndex}` : '') !== (prev ? `${prev.index}|${prev.itemIndex}` : '');
      this.active = next;
      if (indexChanged) this.draw();
      else if (key(next) !== key(prev)) this.drawTooltip();
    }
    onLeave() {
      this.pointerSeen = true;
      this.mouse = null;
      const next = this.resolveActive();
      if (next || this.active) { this.active = next; this.draw(); }
    }

    computeActive(pointer, itemTarget) {
      const I = L();
      const m = this.model;
      if (this.tooltipEventType() === 'item') {
        if (!itemTarget) return null;
        const it = m.items.find((i) => `recharts-pie-${this.uid}-${i.index}` === itemTarget.id);
        if (!it || !it.sectors[itemTarget.index]) return null;
        const sector = it.sectors[itemTarget.index];
        return { pointer, itemTarget, itemIndex: itemTarget.index, graphicalItem: it.id, index: itemTarget.index, coordinate: sector.tooltipPosition, payload: this.itemPayload(it, itemTarget.index), cursor: false };
      }
      if (m.kind === 'cartesian') {
        const off = m.offset;
        const x = pointer.relativeX, y = pointer.relativeY;
        if (!(x >= off.left && x <= off.left + off.width && y >= off.top && y <= off.top + off.height)) return null;
        const pos = m.layout === 'horizontal' ? x : y;
        const ticks = m.tooltipTicks;
        const ordered = ticks.slice().sort((a, b) => a.coordinate - b.coordinate);
        const idx = NS.surface.calculateActiveTickIndex(pos, ordered, ticks, m.layout === 'horizontal' ? 'xAxis' : 'yAxis', m.tooltipAxis.range);
        const entry = ticks.find((t) => t.index === idx);
        const coordinate = entry ? (m.layout === 'horizontal' ? { x: entry.coordinate, y } : { x, y: entry.coordinate }) : { x: 0, y: 0 };
        return this.finishAxisActive(idx, coordinate, pointer);
      }
      // polar axis tooltip
      const vb = m.polarViewBox;
      const dx = pointer.relativeX - vb.cx, dy = pointer.relativeY - vb.cy;
      const radius = Math.sqrt(dx * dx + dy * dy);
      if (radius < vb.innerRadius || radius > vb.outerRadius || radius === 0) return null;
      let angle = Math.acos(dx / radius) * 180 / Math.PI;
      if (pointer.relativeY > vb.cy) angle = 360 - angle;
      const startCnt = Math.floor(vb.startAngle / 360), endCnt = Math.floor(vb.endAngle / 360);
      const min = Math.min(startCnt, endCnt);
      const sA = vb.startAngle - min * 360, eA = vb.endAngle - min * 360;
      let fa = angle, inRange;
      if (sA <= eA) { while (fa > eA) fa -= 360; while (fa < sA) fa += 360; inRange = fa >= sA && fa <= eA; } else { while (fa > sA) fa -= 360; while (fa < eA) fa += 360; inRange = fa >= eA && fa <= sA; }
      if (!inRange) return null;
      const rangeObj = Object.assign({}, vb, { radius, angle: fa + min * 360 });
      const pos = m.layout === 'centric' ? rangeObj.angle : rangeObj.radius;
      const ticks = m.tooltipTicks;
      const ordered = ticks.slice().sort((a, b) => a.coordinate - b.coordinate);
      const idx = NS.surface.calculateActiveTickIndex(pos, ordered, ticks, m.layout === 'centric' ? 'angleAxis' : 'radiusAxis', m.tooltipAxis.range);
      const entry = ticks.find((t) => t.index === idx);
      let coordinate;
      if (entry) {
        if (m.layout === 'centric') coordinate = Object.assign({}, rangeObj, I.polarToCartesian(vb.cx, vb.cy, rangeObj.radius, entry.coordinate), { angle: entry.coordinate, radius: rangeObj.radius });
        else coordinate = Object.assign({}, rangeObj, I.polarToCartesian(vb.cx, vb.cy, entry.coordinate, rangeObj.angle), { angle: rangeObj.angle, radius: entry.coordinate });
      } else coordinate = { x: 0, y: 0 };
      return this.finishAxisActive(idx, coordinate, pointer);
    }

    /* Clamp the index into the data like combineActiveTooltipIndex, compute payload and label. */
    finishAxisActive(idx, coordinate, pointer) {
      const data = this.ctx.data;
      if (idx == null || !Number.isFinite(Number(idx))) return null;
      const clamped = Math.max(0, Math.min(Number(idx), data.length > 0 ? data.length - 1 : Infinity));
      const ticks = this.model.tooltipTicks;
      const label = ticks && ticks[clamped] ? ticks[clamped].value : undefined;
      const payload = this.axisPayload(clamped, label);
      return { pointer, index: clamped, coordinate, payload, label, cursor: true, payloads: Array.from(new Set(payload.map((p) => p.payload).filter((p) => p != null))) };
    }

    activeForIndex(index, pointer) {
      const m = this.model;
      if (!m || !this.spec.tooltip) return null;
      if (this.tooltipEventType() === 'item') {
        const it = m.items[0];
        if (!it || !it.sectors || !it.sectors[index]) return null;
        return { pointer, itemIndex: index, graphicalItem: it.id, index, coordinate: it.sectors[index].tooltipPosition, payload: this.itemPayload(it, index), cursor: false };
      }
      const tick = m.tooltipTicks && m.tooltipTicks[index];
      if (!tick) return null;
      let coordinate;
      if (m.kind === 'cartesian') coordinate = m.layout === 'horizontal' ? { x: tick.coordinate, y: (m.offset.top + m.height) / 2 } : { x: (m.offset.left + m.width) / 2, y: tick.coordinate };
      else coordinate = { x: 0, y: 0 };
      const a = this.finishAxisActive(index, coordinate, pointer);
      return a;
    }

    itemPayload(it, index) {
      const sector = it.sectors[index];
      const p = it.props;
      const color = p.fill;
      return sector.tooltipPayload.map((item) => ({ stroke: p.stroke, strokeWidth: p.strokeWidth, fill: item.fill != null ? item.fill : color, dataKey: item.dataKey, nameKey: p.nameKey, name: item.name == null ? undefined : String(item.name), hide: p.hide, type: item.type, color: item.color != null ? item.color : color, unit: '', payload: item.payload, value: L().getValue(item.payload, item.dataKey) }));
    }

    axisPayload(index, label) {
      const I = L();
      const m = this.model;
      const data = this.ctx.data;
      const axisKey = m.tooltipAxis && m.tooltipAxis.dataKey;
      const out = [];
      m.items.forEach((it) => {
        const p = it.props;
        const settings = { stroke: p.stroke, strokeWidth: p.strokeWidth, fill: p.fill, dataKey: p.dataKey, name: NS.surface.tooltipNameProp(p.name, p.dataKey), hide: p.hide, type: p.tooltipType, unit: p.unit };
        settings.color = it.component === 'Bar' || it.component === 'RadialBar' ? p.fill : NS.surface.legendColor(p.stroke, p.fill);
        let row;
        if (it.component === 'RadialBar') {
          const sectors = it.sectors || [];
          row = axisKey != null ? sectors.find((s) => I.getValue(s, axisKey) === label) : sectors[index];
        } else if (axisKey != null) row = data.find((r) => I.getValue(r, axisKey) === label);
        else row = data[index];
        const value = I.getValue(row, settings.dataKey);
        out.push(Object.assign({}, settings, { dataKey: settings.dataKey, payload: row, value, name: settings.name }));
      });
      return out.filter((e) => e.value != null && e.hide !== true);
    }

    drawTooltip() {
      const t = this.spec.tooltip;
      const el = this.tooltipEl;
      const a = this.active;
      if (!t || !a || !a.payload || !a.payload.length) {
        el.innerHTML = '';
        el.className = 'recharts-tooltip-wrapper';
        el.setAttribute('style', 'visibility: hidden; pointer-events: none; position: absolute; top: 0px; left: 0px;');
        return;
      }
      const resolved = NS.surface.resolveProps(t, this.ctx);
      const content = tooltipContent(this, resolved, a.payload, a.label);
      el.innerHTML = '';
      el.appendChild(content);
      const prev = el.getAttribute('style') || '';
      if (!/visibility: visible/.test(prev)) el.setAttribute('style', 'visibility: hidden; pointer-events: none; position: absolute; top: 0px; left: 0px;');
      const box = el.getBoundingClientRect();
      const vb = this.model.kind === 'cartesian' ? this.model.viewBox : { x: this.model.offset.left, y: this.model.offset.top, width: this.model.offset.width, height: this.model.offset.height };
      const offset = t.offset != null ? t.offset : 10;
      const pos = (key, dim, vbKey, vbDim) => {
        const negative = a.coordinate[key] - dim - (offset > 0 ? offset : 0);
        const positive = a.coordinate[key] + offset;
        const boundary = positive + dim;
        if (boundary > vb[vbKey] + vb[vbDim]) return Math.max(negative, vb[vbKey]);
        return Math.max(positive, vb[vbKey]);
      };
      if (!(box.width > 0 && box.height > 0)) return;
      const tx = pos('x', box.width, 'x', 'width');
      const ty = pos('y', box.height, 'y', 'height');
      const cls = ['recharts-tooltip-wrapper'];
      cls.push(tx >= a.coordinate.x ? 'recharts-tooltip-wrapper-right' : 'recharts-tooltip-wrapper-left');
      cls.push(ty >= a.coordinate.y ? 'recharts-tooltip-wrapper-bottom' : 'recharts-tooltip-wrapper-top');
      el.className = cls.join(' ');
      const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      el.setAttribute('style', `visibility: visible; pointer-events: none; position: absolute; top: 0px; left: 0px;${reduced ? '' : ' transition: transform 400ms;'} transform: translate(${tx}px, ${ty}px);`);
    }
  }

  function defNode(d) {
    const { h, A } = L();
    const kids = (d.children || []).map(defNode);
    return h(d.tag, A(d.props || {}), kids);
  }

  NS.Chart = Chart;

  FB.register('chart', {
    init(root) {
      let spec = null;
      const script = root.querySelector(':scope > script[data-chart-spec]');
      if (script) { try { spec = JSON.parse(script.textContent); } catch (e) { console.error('[Flask-Basecoat] invalid chart spec', e); return; } }
      if (!spec) { spec = legacySpec(root); if (spec) ensureStyle(root, spec); }
      if (!spec) return; // a static chart shell
      root.fbChart = new Chart(root, spec);
      root.fbRedraw = () => root.fbChart.render();
      const id = root.getAttribute('data-chart');
      if (id) {
        // Controls sharing one state (a ToggleGroup and a Select both bound to `timeRange`, as upstream) follow each
        // other: the value chosen in one is shown by the others, without them emitting a change of their own.
        let syncing = false;
        const sync = (state, value, source) => {
          syncing = true;
          try {
            document.querySelectorAll(`[data-chart-control="${id}"][data-chart-state="${state}"]`).forEach((o) => {
              if (o === source || !o.fbSetValue) return;
              const current = o.fbGetValue ? o.fbGetValue() : null;
              if (current !== value) o.fbSetValue(value);
            });
          } finally { syncing = false; }
        };
        document.addEventListener('basecoat:change', (e) => {
          if (syncing) return;
          const c = e.target.closest && e.target.closest(`[data-chart-control="${id}"]`);
          if (!c) return;
          const state = c.getAttribute('data-chart-state');
          // a single ToggleGroup reports [value] (or [] once its item is pressed again, upstream's "" value)
          let value = e.detail.value;
          if (Array.isArray(value)) value = value.length ? value[0] : '';
          root.fbChart.setState(state, value);
          sync(state, value, c);
        });
        // spec.onMedia: [{query, state: {name: value}}]: state set when the query matches at load and whenever it
        // starts matching (upstream `useIsMobile()` + `useEffect(() => { if (isMobile) setTimeRange("7d") })`).
        (spec.onMedia || []).forEach((m) => {
          if (!window.matchMedia) return;
          const mql = window.matchMedia(m.query);
          const apply = () => {
            if (!mql.matches) return;
            Object.entries(m.state || {}).forEach(([state, value]) => { root.fbChart.setState(state, value); sync(state, value, null); });
          };
          if (mql.addEventListener) mql.addEventListener('change', apply);
          setTimeout(apply, 0); // after the controls on the page have been initialised
        });
        document.addEventListener('click', (e) => {
          const c = e.target.closest && e.target.closest(`[data-chart-control="${id}"][data-chart-value]`);
          if (!c) return;
          root.fbChart.setState(c.getAttribute('data-chart-state'), c.getAttribute('data-chart-value'));
          document.querySelectorAll(`[data-chart-control="${id}"][data-chart-value]`).forEach((b) => {
            if (b.getAttribute('data-chart-state') === c.getAttribute('data-chart-state')) b.setAttribute('data-active', String(b === c));
          });
        });
      }
    },
  });
})(window.FlaskBasecoat);

