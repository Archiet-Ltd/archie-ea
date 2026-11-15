/* flask-basecoat b327186c2: runtime core and the menu, popover, select, tabs, dialog, disclosure and toggle behaviours, vendored by scripts/vendor_basecoat_ui.py. They act only on elements carrying the library's data-slot names. */
/* ---- core ---- */
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

/* ---- components/menu ---- */
/* Dropdown Menu, Context Menu and Menubar, with submenus, checkbox and radio items.
   Keyboard: arrows move between items (looping), Home/End, type-ahead, Enter/Space activates, ArrowRight opens a
   submenu, ArrowLeft closes it, Escape closes the menu and returns focus to the trigger. */
(function (FB) {
  'use strict';

  const ITEM_SEL = '[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]';

  /* Radix renders the indicator icon only while checked. */
  function setIndicator(item, on, icon) {
    const ind = item.querySelector('[class*="item-indicator"]');
    if (!ind) return;
    const icons = window.FlaskBasecoatIcons || {};
    ind.innerHTML = on ? (icons[icon] || '') : '';
  }

  function setupMenu(kind, content, opts) {
    const trigger = FB.pairWith(content, `[data-slot="${kind}-trigger"]`);
    if (!trigger) return null;
    const id = FB.ensureId(content, kind);
    content.setAttribute('data-floating', '');
    content.setAttribute('data-popper-var', kind);
    trigger.setAttribute('aria-controls', id);
    content.setAttribute('aria-labelledby', FB.ensureId(trigger, `${kind}-trigger`));
    const side = content.getAttribute('data-side') || opts.side || 'bottom';
    const align = content.getAttribute('data-align') || opts.align || 'start';
    let layer = null, anchorPoint = null;

    const items = () => FB.qsa(content, ITEM_SEL).filter((i) => i.closest(`[data-slot$="-content"]`) === content);
    const onKey = FB.roving(items, { orientation: 'vertical', loop: true });

    function reposition() {
      if (anchorPoint) {
        // Keep the menu inside the viewport, flipping like Radix does near the edges.
        const r = content.getBoundingClientRect();
        const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
        let x = anchorPoint.x, y = anchorPoint.y;
        if (x + r.width > vw - 8) x = Math.max(8, anchorPoint.x - r.width);
        if (y + r.height > vh - 8) y = Math.max(8, anchorPoint.y - r.height);
        content.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
        content.setAttribute('data-side', y < anchorPoint.y ? 'top' : 'bottom'); content.setAttribute('data-align', 'start');
      } else FB.float(trigger, content, { side, align, sideOffset: Number(content.getAttribute('data-side-offset') || 4), alignOffset: Number(content.getAttribute('data-align-offset') || 0) });
    }
    function open(point) {
      anchorPoint = point || null;
      if (FB.isOpen(content)) { reposition(); return; }
      if (content.parentNode !== document.body) document.body.appendChild(content);
      FB.setState(content, 'open'); FB.setState(trigger, 'open');
      trigger.setAttribute('aria-expanded', 'true');
      reposition();
      layer = { content, trigger, modal: false, reposition, onDismiss: (reason) => close(reason !== 'outside') };
      FB.pushLayer(layer);
      const first = items()[0];
      (first || content).focus({ preventScroll: true });
      FB.emit(content, 'open', { kind });
    }
    function close(refocus) {
      if (!FB.isOpen(content)) return;
      closeSubmenus();
      FB.setState(content, 'closed'); FB.setState(trigger, 'closed');
      trigger.setAttribute('aria-expanded', 'false');
      if (layer) { FB.removeLayer(layer); layer = null; }
      if (refocus !== false && kind !== 'context-menu') trigger.focus({ preventScroll: true });
      FB.emit(content, 'close', { kind });
    }
    function activate(item) {
      if (item.getAttribute('aria-disabled') === 'true' || item.hasAttribute('data-disabled')) return;
      if (item.getAttribute('role') === 'menuitemcheckbox') {
        const checked = item.getAttribute('aria-checked') === 'true';
        item.setAttribute('aria-checked', String(!checked));
        FB.setState(item, checked ? 'unchecked' : 'checked');
        setIndicator(item, !checked, 'check');
        FB.emit(item, 'change', { checked: !checked, value: item.getAttribute('data-value') });
        return;
      }
      if (item.getAttribute('role') === 'menuitemradio') {
        const group = item.closest('[role="group"]') || content;
        FB.qsa(group, '[role="menuitemradio"]').forEach((r) => { r.setAttribute('aria-checked', 'false'); FB.setState(r, 'unchecked'); setIndicator(r, false, 'circle'); });
        item.setAttribute('aria-checked', 'true'); FB.setState(item, 'checked'); setIndicator(item, true, 'circle');
        FB.emit(item, 'change', { value: item.getAttribute('data-value') });
        close();
        return;
      }
      if (item.hasAttribute('aria-haspopup')) { openSub(item); return; }
      FB.emit(item, 'select', { value: item.getAttribute('data-value') });
      const link = item.matches('a[href]') ? item : item.querySelector('a[href]');
      close();
      if (link && link !== item) link.click();
    }

    /* submenus: a sub-content follows its sub-trigger inside the menu */
    const subs = FB.qsa(content, `[data-slot="${kind}-sub-content"]`).map((sc) => {
      const st = FB.pairWith(sc, `[data-slot="${kind}-sub-trigger"]`);
      if (!st) return null;
      sc.setAttribute('data-floating', ''); sc.setAttribute('data-popper-var', kind);
      st.setAttribute('aria-controls', FB.ensureId(sc, `${kind}-sub`));
      let timer = 0;
      const subItems = () => FB.qsa(sc, ITEM_SEL);
      const subKey = FB.roving(subItems, { orientation: 'vertical', loop: true });
      const api = {
        trigger: st, content: sc,
        open() {
          if (FB.isOpen(sc)) return;
          if (sc.parentNode !== document.body) document.body.appendChild(sc);
          FB.setState(sc, 'open'); FB.setState(st, 'open'); st.setAttribute('aria-expanded', 'true');
          FB.float(st, sc, { side: 'right', align: 'start', sideOffset: 0 });
        },
        close() { if (!FB.isOpen(sc)) return; FB.setState(sc, 'closed'); FB.setState(st, 'closed'); st.setAttribute('aria-expanded', 'false'); },
      };
      st.addEventListener('pointerenter', () => { clearTimeout(timer); timer = setTimeout(api.open, 100); });
      st.addEventListener('pointerleave', (e) => { clearTimeout(timer); if (!sc.contains(e.relatedTarget)) timer = setTimeout(api.close, 200); });
      sc.addEventListener('pointerleave', (e) => { if (e.relatedTarget !== st && !sc.contains(e.relatedTarget)) timer = setTimeout(api.close, 200); });
      sc.addEventListener('pointerenter', () => clearTimeout(timer));
      st.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); api.open(); const f = subItems()[0]; f && f.focus(); });
      st.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); api.open(); const f = subItems()[0]; f && f.focus(); } });
      sc.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); api.close(); st.focus(); return; }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); api.close(); close(); return; }
        if (subKey(e)) { e.stopPropagation(); return; }
        if (e.key === 'Enter' || e.key === ' ') { const it = e.target.closest(ITEM_SEL); if (it) { e.preventDefault(); e.stopPropagation(); activate(it); } }
      });
      sc.addEventListener('click', (e) => { const it = e.target.closest(ITEM_SEL); if (it && sc.contains(it)) { e.stopPropagation(); activate(it); } });
      sc.addEventListener('pointermove', (e) => { const it = e.target.closest(ITEM_SEL); if (it && document.activeElement !== it) it.focus(); });
      return api;
    }).filter(Boolean);
    function closeSubmenus() { subs.forEach((s) => s.close()); }

    content.addEventListener('keydown', (e) => {
      if (onKey(e)) return;
      if (e.key === 'Enter' || e.key === ' ') { const it = e.target.closest(ITEM_SEL); if (it) { e.preventDefault(); activate(it); } }
      if (e.key === 'Tab') { e.preventDefault(); close(); }
    });
    content.addEventListener('click', (e) => { const it = e.target.closest(ITEM_SEL); if (it && content.contains(it)) activate(it); });
    content.addEventListener('pointermove', (e) => { const it = e.target.closest(ITEM_SEL); if (it && it.closest('[data-slot$="-content"]') === content && document.activeElement !== it) it.focus(); });
    content.addEventListener('pointerleave', () => { if (document.activeElement && content.contains(document.activeElement) && !subs.some((s) => FB.isOpen(s.content))) content.focus(); });

    if (kind === 'context-menu') {
      trigger.addEventListener('contextmenu', (e) => { e.preventDefault(); open({ x: e.clientX, y: e.clientY }); });
    } else {
      trigger.addEventListener('click', (e) => { e.preventDefault(); FB.isOpen(content) ? close() : open(); });
      trigger.addEventListener('keydown', (e) => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    }
    const api = { trigger, content, open, close, isOpen: () => FB.isOpen(content) };
    content.fbOpen = open; content.fbClose = close; trigger.fbOpen = open; trigger.fbClose = close;
    return api;
  }

  FB.register('dropdown-menu', { selector: '[data-slot="dropdown-menu-content"]', init: (el) => setupMenu('dropdown-menu', el, {}) });
  FB.register('context-menu', { selector: '[data-slot="context-menu-content"]', init: (el) => setupMenu('context-menu', el, {}) });

  /* Menubar: a row of menus; hovering across triggers while one is open switches menus. */
  FB.register('menubar', {
    selector: '[data-slot="menubar"]',
    init(bar) {
      // Each trigger inside the bar is followed (in document order) by its content.
      const triggersInBar = FB.qsa(bar, '[data-slot="menubar-trigger"]');
      const menus = FB.qsa(document, '[data-slot="menubar-content"]')
        .filter((c) => { const t = FB.qsa(document, '[data-slot="menubar-trigger"]').filter((x) => !x.fbPaired && (x.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING)).pop(); return t && triggersInBar.includes(t); })
        .map((c) => setupMenu('menubar', c, { side: 'bottom', align: 'start' })).filter(Boolean);
      const triggers = () => menus.map((m) => m.trigger);
      const nav = FB.roving(triggers, { orientation: 'horizontal', loop: true, typeahead: false });
      menus.forEach((m, i) => {
        m.trigger.setAttribute('tabindex', i === 0 ? '0' : '-1');
        m.trigger.addEventListener('pointerenter', () => { if (menus.some((o) => o !== m && o.isOpen())) { menus.forEach((o) => o !== m && o.close(false)); m.open(); } });
        m.trigger.addEventListener('keydown', (e) => { if (nav(e)) { const wasOpen = m.isOpen(); if (wasOpen) { m.close(false); const t = document.activeElement; const next = menus.find((o) => o.trigger === t); next && next.open(); } } });
        m.content.addEventListener('keydown', (e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            const sub = e.target.closest('[data-slot="menubar-sub-content"]');
            if (sub) return;
            const idx = menus.indexOf(m); const n = menus[(idx + (e.key === 'ArrowRight' ? 1 : -1) + menus.length) % menus.length];
            e.preventDefault(); m.close(false); n.trigger.focus(); n.open();
          }
        });
      });
    },
  });
})(window.FlaskBasecoat);

/* ---- components/popover ---- */
/* Popover, Hover Card and Tooltip: non-modal floating layers anchored to the trigger that precedes them.
   Popover opens on click (dismiss on outside/Escape, focus moves into the content).
   Hover Card opens on pointer hover / focus with open and close delays.
   Tooltip opens on hover / focus with a delay, closes on pointer leave, blur or Escape. */
(function (FB) {
  'use strict';

  function setup(kind, content) {
    const trigger = FB.pairWith(content, `[data-slot="${kind}-trigger"], [data-slot="${kind}-anchor"]`);
    if (!trigger) return;
    const id = FB.ensureId(content, kind);
    content.setAttribute('data-floating', '');
    content.setAttribute('data-popper-var', kind);
    const side = content.getAttribute('data-side') || (kind === 'tooltip' ? 'top' : 'bottom');
    const align = content.getAttribute('data-align') || 'center';
    const sideOffset = Number(content.getAttribute('data-side-offset') || (kind === 'tooltip' ? 0 : 4));
    const alignOffset = Number(content.getAttribute('data-align-offset') || 0);
    if (kind === 'popover') trigger.setAttribute('aria-controls', id);
    if (kind === 'tooltip') trigger.setAttribute('aria-describedby', id);
    let layer = null, openTimer = 0, closeTimer = 0;

    function reposition() { FB.float(trigger, content, { side, align, sideOffset, alignOffset }); }
    function open() {
      clearTimeout(closeTimer);
      if (FB.isOpen(content)) return;
      if (content.parentNode !== document.body) document.body.appendChild(content);
      FB.setState(content, 'open');
      FB.setState(trigger, kind === 'tooltip' ? 'delayed-open' : 'open');
      if (kind === 'popover') trigger.setAttribute('aria-expanded', 'true');
      reposition();
      layer = { content, trigger, modal: false, reposition, onDismiss: () => close() };
      FB.pushLayer(layer);
      if (kind === 'popover') FB.focusFirst(content);
      FB.emit(content, 'open', { kind });
    }
    function close() {
      clearTimeout(openTimer);
      if (!FB.isOpen(content)) return;
      FB.setState(content, 'closed');
      FB.setState(trigger, 'closed');
      if (kind === 'popover') { trigger.setAttribute('aria-expanded', 'false'); trigger.focus({ preventScroll: true }); }
      if (layer) { FB.removeLayer(layer); layer = null; }
      FB.emit(content, 'close', { kind });
    }
    if (kind === 'popover') {
      trigger.addEventListener('click', (e) => { e.preventDefault(); FB.isOpen(content) ? close() : open(); });
      content.addEventListener('click', (e) => { if (e.target.closest('[data-fb-close]')) close(); });
    } else {
      const openDelay = Number(content.getAttribute('data-open-delay') || trigger.getAttribute('data-open-delay') || (kind === 'tooltip' ? 0 : 700));
      const closeDelay = Number(content.getAttribute('data-close-delay') || trigger.getAttribute('data-close-delay') || (kind === 'tooltip' ? 0 : 300));
      const scheduleOpen = () => { clearTimeout(closeTimer); openTimer = setTimeout(open, openDelay); };
      const scheduleClose = () => { clearTimeout(openTimer); closeTimer = setTimeout(close, closeDelay); };
      trigger.addEventListener('pointerenter', scheduleOpen);
      trigger.addEventListener('pointerleave', scheduleClose);
      trigger.addEventListener('focus', open);
      trigger.addEventListener('blur', close);
      content.addEventListener('pointerenter', () => clearTimeout(closeTimer));
      content.addEventListener('pointerleave', scheduleClose);
      if (kind === 'tooltip') trigger.addEventListener('pointerdown', close);
    }
    content.fbOpen = open; content.fbClose = close; trigger.fbOpen = open; trigger.fbClose = close;
    if (content.getAttribute('data-state') === 'open') { FB.setState(content, 'closed'); open(); }
  }

  ['popover', 'hover-card', 'tooltip'].forEach((kind) => {
    FB.register(kind, { selector: `[data-slot="${kind}-content"]`, init: (el) => setup(kind, el) });
  });
})(window.FlaskBasecoat);

/* ---- components/select ---- */
/* Select: a button trigger (role=combobox) opening a listbox; keyboard navigation, type-ahead, Enter/Space selects,
   Escape closes. The selected item's label is copied into [data-slot="select-value"] and the value into a hidden
   input named by the wrapper's data-name (or `name`). Native Select needs no script. */
(function (FB) {
  'use strict';

  FB.register('select', {
    selector: '[data-slot="select-content"]',
    init(content) {
      const trigger = FB.pairWith(content, '[data-slot="select-trigger"]');
      if (!trigger) return;
      const wrapper = trigger; // data-name and data-value live on the trigger
      const valueEl = FB.qs(trigger, '[data-slot="select-value"]');
      const id = FB.ensureId(content, 'select');
      trigger.setAttribute('aria-controls', id);
      content.setAttribute('data-floating', '');
      content.setAttribute('data-popper-var', 'select');
      const name = trigger.getAttribute('data-name') || content.getAttribute('data-name') || trigger.getAttribute('name');
      let input = null;
      if (name) { input = document.createElement('input'); input.type = 'hidden'; input.name = name; trigger.insertAdjacentElement('afterend', input); }
      const placeholder = valueEl ? valueEl.textContent : '';
      const items = () => FB.qsa(content, '[data-slot="select-item"]');
      const nav = FB.roving(items, { orientation: 'vertical', loop: false });
      let layer = null;

      function reposition() { FB.float(trigger, content, { side: content.getAttribute('data-side') || 'bottom', align: content.getAttribute('data-align') || 'start', sideOffset: 4 }); }
      function open() {
        if (FB.isOpen(content)) return;
        if (content.parentNode !== document.body) document.body.appendChild(content);
        FB.setState(content, 'open'); FB.setState(trigger, 'open'); trigger.setAttribute('aria-expanded', 'true');
        reposition();
        layer = { content, trigger, modal: false, reposition, onDismiss: () => close() };
        FB.pushLayer(layer);
        const selected = items().find((i) => i.getAttribute('data-state') === 'checked') || items()[0];
        (selected || content).focus({ preventScroll: true });
      }
      function close() {
        if (!FB.isOpen(content)) return;
        FB.setState(content, 'closed'); FB.setState(trigger, 'closed'); trigger.setAttribute('aria-expanded', 'false');
        if (layer) { FB.removeLayer(layer); layer = null; }
        trigger.focus({ preventScroll: true });
      }
      function select(item) {
        if (item.getAttribute('aria-disabled') === 'true' || item.hasAttribute('data-disabled')) return;
        items().forEach((i) => { const on = i === item; i.setAttribute('aria-selected', String(on)); FB.setState(i, on ? 'checked' : 'unchecked'); });
        const value = item.getAttribute('data-value') || item.getAttribute('value') || item.textContent.trim();
        const label = (FB.qs(item, '[data-slot="select-item-text"]') || item).textContent.trim();
        if (valueEl) { valueEl.textContent = label; }
        trigger.removeAttribute('data-placeholder');
        if (input) input.value = value;
        wrapper.setAttribute('data-value', value);
        FB.emit(wrapper, 'change', { value, label });
        close();
      }
      // Programmatic selection: trigger.fbSetValue('value') picks the item with that value (or label), like a click.
      function setValue(value) {
        const v = value == null ? '' : String(value);
        if (v === '') {
          // no value (Radix value ""): nothing checked, the placeholder is shown
          items().forEach((i) => { i.setAttribute('aria-selected', 'false'); FB.setState(i, 'unchecked'); });
          if (valueEl) valueEl.textContent = placeholder;
          trigger.setAttribute('data-placeholder', '');
          trigger.removeAttribute('data-value');
          if (input) input.value = '';
          return true;
        }
        const item = items().find((i) => (i.getAttribute('data-value') || i.getAttribute('value')) === v) || items().find((i) => i.textContent.trim() === v);
        if (item) select(item);
        return !!item;
      }
      trigger.fbSetValue = setValue; content.fbSetValue = setValue;
      trigger.fbGetValue = () => trigger.getAttribute('data-value');
      const preselected = items().find((i) => i.getAttribute('data-state') === 'checked' || i.getAttribute('aria-selected') === 'true');
      if (preselected) { const label = preselected.textContent.trim(); if (valueEl && !valueEl.textContent.trim()) valueEl.textContent = label; if (input) input.value = preselected.getAttribute('data-value') || preselected.getAttribute('value') || label; trigger.removeAttribute('data-placeholder'); }
      else if (valueEl && !valueEl.textContent.trim()) valueEl.textContent = placeholder;
      items().forEach((i) => { i.setAttribute('tabindex', '-1'); if (!i.hasAttribute('data-state')) FB.setState(i, 'unchecked'); });
      trigger.addEventListener('click', (e) => { e.preventDefault(); if (trigger.disabled) return; FB.isOpen(content) ? close() : open(); });
      trigger.addEventListener('keydown', (e) => { if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); open(); } });
      content.addEventListener('keydown', (e) => {
        if (nav(e)) return;
        if (e.key === 'Enter' || e.key === ' ') { const it = e.target.closest('[data-slot="select-item"]'); if (it) { e.preventDefault(); select(it); } }
        if (e.key === 'Tab') { e.preventDefault(); close(); }
      });
      content.addEventListener('click', (e) => { const it = e.target.closest('[data-slot="select-item"]'); if (it) select(it); });
      content.addEventListener('pointermove', (e) => { const it = e.target.closest('[data-slot="select-item"]'); if (it && document.activeElement !== it) it.focus(); });
      content.fbOpen = open; content.fbClose = close; trigger.fbOpen = open; trigger.fbClose = close; trigger.fbSelect = select;
    },
  });
})(window.FlaskBasecoat);

/* ---- components/tabs ---- */
/* Tabs: role=tablist with arrow-key navigation (automatic activation), data-state active/inactive on triggers and panels. */
(function (FB) {
  'use strict';

  FB.register('tabs', {
    init(root) {
      const list = FB.qs(root, '[data-slot="tabs-list"]');
      if (!list) return;
      const triggers = () => FB.qsa(list, '[data-slot="tabs-trigger"]');
      const panels = () => FB.qsa(root, '[data-slot="tabs-content"]').filter((p) => p.closest('[data-slot="tabs"]') === root);
      const orientation = root.getAttribute('data-orientation') === 'vertical' ? 'vertical' : 'horizontal';
      triggers().forEach((t, i) => {
        const value = t.getAttribute('data-value') || String(i);
        t.setAttribute('data-value', value);
        const panel = panels().find((p) => (p.getAttribute('data-value') || String(panels().indexOf(p))) === value) || panels()[i];
        if (panel) {
          panel.setAttribute('data-value', value);
          t.setAttribute('aria-controls', FB.ensureId(panel, 'tabs-content'));
          panel.setAttribute('aria-labelledby', FB.ensureId(t, 'tabs-trigger'));
        }
      });
      function activate(trigger, focus) {
        triggers().forEach((t) => {
          const on = t === trigger;
          t.setAttribute('aria-selected', String(on));
          t.setAttribute('tabindex', on ? '0' : '-1');
          FB.setState(t, on ? 'active' : 'inactive');
        });
        const value = trigger.getAttribute('data-value');
        panels().forEach((p) => {
          const on = p.getAttribute('data-value') === value;
          FB.setState(p, on ? 'active' : 'inactive');
          if (on) p.removeAttribute('hidden'); else p.setAttribute('hidden', '');
        });
        if (focus) trigger.focus();
        FB.emit(root, 'change', { value });
      }
      const current = triggers().find((t) => t.getAttribute('data-state') === 'active') || triggers()[0];
      if (current) activate(current, false);
      const nav = FB.roving(triggers, { orientation, loop: true, typeahead: false });
      list.addEventListener('click', (e) => { const t = e.target.closest('[data-slot="tabs-trigger"]'); if (t && !t.disabled) activate(t, false); });
      list.addEventListener('keydown', (e) => { if (nav(e)) { const t = document.activeElement; if (t && t.matches('[data-slot="tabs-trigger"]')) activate(t, false); } });
    },
  });
})(window.FlaskBasecoat);

/* ---- components/dialog ---- */
/* Dialog, Alert Dialog, Sheet and Drawer: modal layers.
   Markup (server-rendered, same DOM as upstream): a trigger, then an overlay and a content element. The runtime
   pairs each content with the nearest preceding trigger of its kind in document order (FB.pairWith); any element
   with data-fb-open="<content id>" opens it too. Behaviour mirrors Radix Dialog: aria-expanded/data-state on the
   trigger, scroll lock, focus trap, focus restore, Escape and overlay click dismiss (alert-dialog ignores overlay
   click), exit animation before hiding. */
(function (FB) {
  'use strict';

  function setup(kind, content) {
    const id = FB.ensureId(content, kind);
    const trigger = FB.pairWith(content, `[data-slot="${kind}-trigger"]`);
    const overlay = FB.pairWith(content, `[data-slot="${kind}-overlay"]`, { adjacent: true });
    if (trigger) trigger.setAttribute('aria-controls', id);
    const title = FB.qs(content, `[data-slot="${kind}-title"]`);
    const desc = FB.qs(content, `[data-slot="${kind}-description"]`);
    if (title && !content.hasAttribute('aria-labelledby')) content.setAttribute('aria-labelledby', FB.ensureId(title, `${kind}-title`));
    if (desc && !content.hasAttribute('aria-describedby')) content.setAttribute('aria-describedby', FB.ensureId(desc, `${kind}-description`));
    let layer = null, untrap = null, previouslyFocused = null, closing = null, opener = null;

    function finishHide() {
      [overlay, content].forEach((el) => el && el.removeAttribute('data-fb-animating'));
      closing = null;
    }
    function open(from) {
      if (FB.isOpen(content)) return;
      if (closing) { clearTimeout(closing); finishHide(); }
      opener = from || null;
      previouslyFocused = document.activeElement;
      // Portal to <body> so stacking is independent of the trigger's ancestors.
      if (overlay && overlay.parentNode !== document.body) document.body.appendChild(overlay);
      if (content.parentNode !== document.body) document.body.appendChild(content);
      [overlay, content].forEach((el) => el && FB.setState(el, 'open'));
      if (trigger) { FB.setState(trigger, 'open'); trigger.setAttribute('aria-expanded', 'true'); }
      FB.lockScroll();
      untrap = FB.trapFocus(content);
      layer = {
        content, trigger, modal: true,
        onDismiss(reason, e) {
          if (reason === 'outside' && (kind === 'alert-dialog' || (e && content.contains(e.target)))) return;
          close();
        },
      };
      FB.pushLayer(layer);
      FB.focusFirst(content, '[data-autofocus], [autofocus]');
      FB.emit(content, 'open', { kind });
    }
    function close() {
      if (!FB.isOpen(content)) return;
      [overlay, content].forEach((el) => el && (el.setAttribute('data-fb-animating', ''), FB.setState(el, 'closed')));
      if (trigger) { FB.setState(trigger, 'closed'); trigger.setAttribute('aria-expanded', 'false'); }
      FB.unlockScroll();
      if (untrap) { untrap(); untrap = null; }
      if (layer) { FB.removeLayer(layer); layer = null; }
      closing = setTimeout(finishHide, 220);
      // Radix returns focus to the trigger; fall back to whatever had focus before opening.
      const target = (opener && opener.isConnected) ? opener : (trigger && trigger.isConnected ? trigger : previouslyFocused);
      if (target && target.focus) target.focus({ preventScroll: true });
      FB.emit(content, 'close', { kind });
    }
    if (trigger) trigger.addEventListener('click', (e) => { e.preventDefault(); open(trigger); });
    content.addEventListener('click', (e) => {
      const btn = e.target.closest(`[data-slot="${kind}-close"], [data-slot="${kind}-cancel"], [data-slot="${kind}-action"], [data-fb-close]`);
      if (btn && content.contains(btn)) { e.preventDefault(); close(); }
    });
    if (overlay) overlay.addEventListener('click', (e) => { if (e.target === overlay && kind !== 'alert-dialog') close(); });
    content.fbOpen = open; content.fbClose = close;
    if (trigger) { trigger.fbOpen = open; trigger.fbClose = close; }
    if (content.getAttribute('data-state') === 'open') { FB.setState(content, 'closed'); open(); }
    document.addEventListener('click', (e) => {
      const from = e.target.closest && e.target.closest(`[data-fb-open="${id}"]`);
      if (from) { e.preventDefault(); open(from); }
    });
  }

  ['dialog', 'alert-dialog', 'sheet', 'drawer'].forEach((kind) => {
    FB.register(kind, { selector: `[data-slot="${kind}-content"]`, init: (el) => setup(kind, el) });
  });
})(window.FlaskBasecoat);

/* ---- components/disclosure ---- */
/* Accordion, Collapsible and Trace collapsible: expand/collapse regions with data-state and aria-expanded. */
(function (FB) {
  'use strict';

  function heightVar(content, name) {
    content.style.setProperty(name, `${content.scrollHeight}px`);
  }

  function toggleContent(trigger, content, open, varName) {
    const item = trigger.closest('[data-slot$="-item"], [data-slot="collapsible"], [data-slot="trace-collapsible"]');
    if (open) {
      content.removeAttribute('hidden');
      FB.setState(content, 'open');
      heightVar(content, varName);
    } else {
      heightVar(content, varName);
      FB.setState(content, 'closed');
      content.setAttribute('data-fb-animating', '');
      setTimeout(() => { if (content.getAttribute('data-state') === 'closed') { content.setAttribute('hidden', ''); content.removeAttribute('data-fb-animating'); } }, 220);
    }
    FB.setState(trigger, open ? 'open' : 'closed');
    trigger.setAttribute('aria-expanded', String(open));
    if (item) FB.setState(item, open ? 'open' : 'closed');
  }

  FB.register('accordion', {
    init(root) {
      const type = root.getAttribute('data-type') || 'single';
      const collapsible = root.getAttribute('data-collapsible') !== 'false';
      const items = FB.qsa(root, '[data-slot="accordion-item"]').filter((i) => i.closest('[data-slot="accordion"]') === root);
      const pairs = items.map((item) => {
        const trigger = FB.qs(item, '[data-slot="accordion-trigger"]');
        const content = FB.qs(item, '[data-slot="accordion-content"]');
        if (!trigger || !content) return null;
        trigger.setAttribute('aria-controls', FB.ensureId(content, 'accordion-content'));
        content.setAttribute('aria-labelledby', FB.ensureId(trigger, 'accordion-trigger'));
        return { item, trigger, content };
      }).filter(Boolean);
      const triggers = () => pairs.map((p) => p.trigger);
      const nav = FB.roving(triggers, { orientation: root.getAttribute('data-orientation') === 'horizontal' ? 'horizontal' : 'vertical', loop: true, typeahead: false });
      pairs.forEach((p) => {
        p.trigger.addEventListener('click', (e) => {
          e.preventDefault();
          const isOpen = p.trigger.getAttribute('aria-expanded') === 'true';
          if (type === 'single') {
            pairs.forEach((o) => { if (o !== p && o.trigger.getAttribute('aria-expanded') === 'true') toggleContent(o.trigger, o.content, false, '--radix-accordion-content-height'); });
            if (isOpen && !collapsible) return;
          }
          toggleContent(p.trigger, p.content, !isOpen, '--radix-accordion-content-height');
          FB.emit(root, 'change', { value: p.item.getAttribute('data-value'), open: !isOpen });
        });
        p.trigger.addEventListener('keydown', nav);
      });
    },
  });

  function collapsible(kind) {
    return {
      init(root) {
        // The trigger is usually [data-slot=kind-trigger]; with asChild upstream it keeps the child's slot (sidebar-group-label, sidebar-menu-button).
        const trigger = FB.qs(root, `[data-slot="${kind}-trigger"]`) || FB.qsa(root, '[aria-expanded]').find((t) => t.closest(`[data-slot="${kind}"]`) === root);
        const content = FB.qs(root, `[data-slot="${kind}-content"]`);
        if (!trigger) return;
        if (content) trigger.setAttribute('aria-controls', FB.ensureId(content, `${kind}-content`));
        trigger.addEventListener('click', (e) => {
          e.preventDefault();
          const isOpen = trigger.getAttribute('aria-expanded') === 'true';
          if (content) toggleContent(trigger, content, !isOpen, '--radix-collapsible-content-height');
          else {
            // No panel upstream (state-driven content): only the state flips; CSS swaps [data-fb-preview]/[data-fb-full].
            FB.setState(trigger, isOpen ? 'closed' : 'open');
            trigger.setAttribute('aria-expanded', String(!isOpen));
            FB.setState(root, isOpen ? 'closed' : 'open');
          }
          FB.emit(root, 'change', { open: !isOpen });
        });
      },
    };
  }
  FB.register('collapsible', collapsible('collapsible'));
  FB.register('trace-collapsible', collapsible('trace-collapsible'));
})(window.FlaskBasecoat);

/* ---- components/toggles ---- */
/* Checkbox, Switch, Radio Group, Toggle and Toggle Group.
   Each button-based control keeps aria-checked / aria-pressed and data-state in sync and mirrors its value into a
   hidden form input named by data-name (or the `name` attribute) so plain HTML forms submit it. */
(function (FB) {
  'use strict';

  function hiddenInput(control, name) {
    if (!name) return null;
    let input = control.fbInput;
    if (!input) {
      input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      control.insertAdjacentElement('afterend', input);
      control.fbInput = input;
    }
    return input;
  }

  function setChecked(control, checked, indicatorSel) {
    control.setAttribute('aria-checked', checked === 'mixed' ? 'mixed' : String(checked));
    FB.setState(control, checked === 'mixed' ? 'indeterminate' : checked ? 'checked' : 'unchecked');
    const ind = indicatorSel && FB.qs(control, indicatorSel);
    if (ind) FB.setState(ind, checked ? 'checked' : 'unchecked');
    const name = control.getAttribute('name') || control.getAttribute('data-name');
    const input = hiddenInput(control, name);
    if (input) { input.value = control.getAttribute('value') || 'on'; input.disabled = !checked; }
    FB.emit(control, 'change', { checked });
  }

  ['checkbox', 'switch'].forEach((kind) => {
    FB.register(kind, {
      init(el) {
        const indicator = kind === 'checkbox' ? '[data-slot="checkbox-indicator"]' : '[data-slot="switch-thumb"]';
        if (kind === 'checkbox') {
          // The indicator exists in the DOM; Radix only renders it when checked. CSS hides it when unchecked.
          let ind = FB.qs(el, indicator);
          if (!ind) {
            ind = document.createElement('span');
            ind.setAttribute('data-slot', 'checkbox-indicator');
            ind.className = 'cn-checkbox-indicator grid place-content-center text-current transition-none';
            ind.style.pointerEvents = 'none';
            el.appendChild(ind);
          }
          if (!ind.firstElementChild && window.FlaskBasecoatIcons) ind.innerHTML = window.FlaskBasecoatIcons.check;
          // The indicator mirrors the control's state so the closed-state CSS hides it while unchecked.
          const state = el.getAttribute('aria-checked');
          FB.setState(ind, state === 'mixed' ? 'indeterminate' : state === 'true' ? 'checked' : 'unchecked');
        }
        const name = el.getAttribute('name') || el.getAttribute('data-name');
        const initial = el.getAttribute('aria-checked') === 'mixed' ? 'mixed' : el.getAttribute('aria-checked') === 'true';
        if (name) { const input = hiddenInput(el, name); input.value = el.getAttribute('value') || 'on'; input.disabled = !initial; }
        el.addEventListener('click', (e) => {
          e.preventDefault();
          if (el.disabled || el.hasAttribute('data-disabled')) return;
          const checked = el.getAttribute('aria-checked') === 'true';
          setChecked(el, !checked, indicator);
        });
        el.addEventListener('keydown', (e) => { if (e.key === ' ') { e.preventDefault(); el.click(); } else if (e.key === 'Enter' && kind === 'switch') { e.preventDefault(); el.click(); } });
        // Click on the associated <label for=id> toggles the control (buttons are not labelable, so emulate it).
        // A click on the control itself bubbles to a wrapping label; it has already toggled, so the label ignores it.
        if (el.id) FB.qsa(document, `label[for="${el.id}"]`).forEach((label) => label.addEventListener('click', (e) => { if (el.contains(e.target)) return; e.preventDefault(); el.click(); el.focus(); }));
      },
    });
  });

  FB.register('radio-group', {
    init(group) {
      const items = () => FB.qsa(group, '[data-slot="radio-group-item"]');
      const name = group.getAttribute('name') || group.getAttribute('data-name');
      let input = null;
      if (name) { input = document.createElement('input'); input.type = 'hidden'; input.name = name; group.appendChild(input); }
      function select(item, focus) {
        items().forEach((r) => {
          const on = r === item;
          r.setAttribute('aria-checked', String(on));
          FB.setState(r, on ? 'checked' : 'unchecked');
          r.setAttribute('tabindex', on ? '0' : '-1');
          let ind = FB.qs(r, '[data-slot="radio-group-indicator"]');
          if (on && !ind) {
            ind = document.createElement('span');
            ind.setAttribute('data-slot', 'radio-group-indicator');
            ind.className = 'cn-radio-group-indicator relative flex items-center justify-center';
            r.appendChild(ind);
          }
          if (ind) FB.setState(ind, on ? 'checked' : 'unchecked');
        });
        if (input) input.value = item.getAttribute('value') || '';
        if (focus) item.focus();
        FB.emit(group, 'change', { value: item.getAttribute('value') });
      }
      const checked = items().find((r) => r.getAttribute('aria-checked') === 'true');
      items().forEach((r, i) => r.setAttribute('tabindex', (checked ? r === checked : i === 0) ? '0' : '-1'));
      if (checked && input) input.value = checked.getAttribute('value') || '';
      const nav = FB.roving(items, { orientation: 'both', loop: true, typeahead: false });
      group.addEventListener('click', (e) => { const r = e.target.closest('[data-slot="radio-group-item"]'); if (r && !r.disabled) { e.preventDefault(); select(r, false); } });
      group.addEventListener('keydown', (e) => {
        const r = e.target.closest('[data-slot="radio-group-item"]');
        if (!r) return;
        if (nav(e)) { select(document.activeElement, false); return; }
        if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); select(r, false); }
      });
      items().forEach((r) => { if (r.id) FB.qsa(document, `label[for="${r.id}"]`).forEach((l) => l.addEventListener('click', (e) => { if (r.contains(e.target)) return; e.preventDefault(); select(r, true); })); });
    },
  });

  FB.register('toggle', {
    init(el) {
      if (el.closest('[data-slot="toggle-group"]')) return;
      el.addEventListener('click', (e) => {
        e.preventDefault();
        if (el.disabled) return;
        const on = el.getAttribute('aria-pressed') === 'true';
        el.setAttribute('aria-pressed', String(!on));
        FB.setState(el, on ? 'off' : 'on');
        FB.emit(el, 'change', { pressed: !on });
      });
    },
  });

  FB.register('toggle-group', {
    init(group) {
      const multiple = group.getAttribute('role') === 'toolbar' || group.getAttribute('data-type') === 'multiple';
      const items = () => FB.qsa(group, '[data-slot="toggle-group-item"]');
      items().forEach((it, i) => it.setAttribute('tabindex', i === 0 ? '0' : '-1'));
      function set(item, on) {
        FB.setState(item, on ? 'on' : 'off');
        if (item.getAttribute('role') === 'radio') item.setAttribute('aria-checked', String(on)); else item.setAttribute('aria-pressed', String(on));
      }
      group.addEventListener('click', (e) => {
        const it = e.target.closest('[data-slot="toggle-group-item"]');
        if (!it || it.disabled) return;
        e.preventDefault();
        const on = it.getAttribute('data-state') === 'on';
        if (multiple) set(it, !on);
        else items().forEach((o) => set(o, o === it ? !on : false));
        FB.emit(group, 'change', { value: values() });
      });
      const valueOf = (o) => o.getAttribute('data-value') || o.getAttribute('value');
      const values = () => items().filter((o) => o.getAttribute('data-state') === 'on').map(valueOf);
      // Programmatic value (a controlled ToggleGroup): group.fbSetValue('30d') or (['a', 'b']); '' or [] presses none.
      group.fbSetValue = (value) => {
        const want = (Array.isArray(value) ? value : [value]).filter((v) => v !== '' && v != null).map(String);
        items().forEach((o) => set(o, want.includes(valueOf(o))));
      };
      group.fbGetValue = () => (multiple ? values() : (values()[0] || ''));
      const nav = FB.roving(items, { orientation: group.getAttribute('data-orientation') === 'vertical' ? 'vertical' : 'horizontal', loop: true, typeahead: false });
      group.addEventListener('keydown', (e) => { if (!nav(e) && (e.key === ' ' || e.key === 'Enter')) { const it = e.target.closest('[data-slot="toggle-group-item"]'); if (it) { e.preventDefault(); it.click(); } } });
    },
  });
})(window.FlaskBasecoat);
