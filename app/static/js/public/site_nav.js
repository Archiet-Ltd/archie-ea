/*
 * Public site navigation behaviour (components/public_navbar.html).
 *
 * Disclosure pattern, not role="menu": each menu is a button with aria-expanded
 * and aria-controls whose panel follows it in the DOM, so Tab enters it in
 * order. Below the lg breakpoint one Menu button opens the panel that holds the
 * same menus as accordions.
 *
 *   Enter / Space   toggle a menu (native button behaviour)
 *   Escape          close the open menu and return focus to its trigger; with
 *                   no menu open, close the mobile panel and focus Menu
 *   Tab out         focus leaving an open desktop menu closes it
 *   Click outside   closes the open menu
 *   ArrowDown       on a closed trigger: open and focus the first item;
 *                   inside a panel: next item (ArrowUp previous, Home, End)
 *   ArrowLeft/Right between top-level triggers (desktop)
 *
 * Opening one menu closes any other. No inline script, no timers.
 */
(function () {
  'use strict';

  var header = document.querySelector('[data-site-nav]');
  if (!header) { return; }

  var sheetButton = header.querySelector('[data-sheet-trigger]');
  var sheet = document.getElementById('site-nav-panel');
  var triggers = Array.prototype.slice.call(header.querySelectorAll('[data-menu-trigger]'));
  var wide = window.matchMedia('(min-width: 1024px)');

  function panelFor(trigger) {
    return document.getElementById(trigger.getAttribute('aria-controls'));
  }

  function isOpen(trigger) {
    return trigger.getAttribute('aria-expanded') === 'true';
  }

  function setMenu(trigger, open) {
    var panel = panelFor(trigger);
    if (!panel) { return; }
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    panel.hidden = !open;
    var chevron = trigger.querySelector('[data-chevron]');
    if (chevron) { chevron.classList.toggle('rotate-180', open); }
  }

  function closeMenus(except) {
    triggers.forEach(function (t) {
      if (t !== except && isOpen(t)) { setMenu(t, false); }
    });
  }

  function openMenu() {
    return triggers.filter(isOpen)[0] || null;
  }

  function setSheet(open) {
    if (!sheetButton || !sheet) { return; }
    sheetButton.setAttribute('aria-expanded', open ? 'true' : 'false');
    sheet.hidden = !open;
    var closedIcon = sheetButton.querySelector('[data-sheet-icon="closed"]');
    var openIcon = sheetButton.querySelector('[data-sheet-icon="open"]');
    if (closedIcon) { closedIcon.classList.toggle('hidden', open); }
    if (openIcon) { openIcon.classList.toggle('hidden', !open); }
    if (!open) { closeMenus(null); }
  }

  function sheetIsOpen() {
    return !!sheetButton && sheetButton.getAttribute('aria-expanded') === 'true';
  }

  function itemsOf(panel) {
    return Array.prototype.slice.call(panel.querySelectorAll('[data-menu-item], a[href]'))
      .filter(function (el) { return el.offsetParent !== null; });
  }

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function () {
      var open = !isOpen(trigger);
      closeMenus(trigger);
      setMenu(trigger, open);
    });

    trigger.addEventListener('keydown', function (event) {
      var panel = panelFor(trigger);
      if (event.key === 'ArrowDown' && panel) {
        event.preventDefault();
        closeMenus(trigger);
        setMenu(trigger, true);
        var first = itemsOf(panel)[0];
        if (first) { first.focus(); }
      } else if ((event.key === 'ArrowRight' || event.key === 'ArrowLeft') && wide.matches) {
        var tops = triggers.filter(function (t) { return t.hasAttribute('data-nav-top') && t.offsetParent !== null; });
        var at = tops.indexOf(trigger);
        if (at === -1) { return; }
        event.preventDefault();
        var next = tops[(at + (event.key === 'ArrowRight' ? 1 : tops.length - 1)) % tops.length];
        var wasOpen = isOpen(trigger);
        if (wasOpen) { setMenu(trigger, false); setMenu(next, true); }
        next.focus();
      }
    });

    var panel = panelFor(trigger);
    if (!panel) { return; }

    panel.addEventListener('keydown', function (event) {
      var keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
      if (keys.indexOf(event.key) === -1) { return; }
      var items = itemsOf(panel);
      var at = items.indexOf(document.activeElement);
      if (at === -1) { return; }
      event.preventDefault();
      var target = event.key === 'Home' ? 0
        : event.key === 'End' ? items.length - 1
        : event.key === 'ArrowDown' ? Math.min(at + 1, items.length - 1)
        : Math.max(at - 1, 0);
      items[target].focus();
    });
  });

  if (sheetButton) {
    sheetButton.addEventListener('click', function () {
      setSheet(!sheetIsOpen());
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') { return; }
    var open = openMenu();
    if (open) {
      setMenu(open, false);
      open.focus();
      return;
    }
    if (sheetIsOpen()) {
      setSheet(false);
      sheetButton.focus();
    }
  });

  // Tab out of an open desktop menu closes it.
  header.addEventListener('focusout', function (event) {
    if (!wide.matches) { return; }
    var open = openMenu();
    if (!open) { return; }
    var holder = open.parentElement;
    var to = event.relatedTarget;
    if (to && holder.contains(to)) { return; }
    if (to === null) { return; }  // window blur or a click on non-focusable space is handled below
    setMenu(open, false);
  });

  document.addEventListener('click', function (event) {
    var open = openMenu();
    if (open && !open.parentElement.contains(event.target)) { setMenu(open, false); }
    if (sheetIsOpen() && !header.contains(event.target)) { setSheet(false); }
  });

  // Coming back to a page from history (or after a click on a menu link) never
  // shows a menu open over the page.
  window.addEventListener('pageshow', function () {
    closeMenus(null);
    if (sheetButton) { setSheet(false); }
  });

  header.addEventListener('click', function (event) {
    var link = event.target.closest ? event.target.closest('a[href]') : null;
    if (link && header.contains(link)) {
      closeMenus(null);
      if (sheetButton) { setSheet(false); }
    }
  });

  function onResize() {
    closeMenus(null);
    if (sheetButton) { setSheet(false); }
  }
  if (wide.addEventListener) { wide.addEventListener('change', onResize); }
  else if (wide.addListener) { wide.addListener(onResize); }
})();
