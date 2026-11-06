/* Tab strips: every [data-tab-strip] (components/tabs.html panel tabs and section tabs, the older hand-written
   strips) keeps the open tab in view, marks the edge that hides more tabs for css/app.css, and moves between
   tabs with the arrow keys, Home and End. */
(function () {
  'use strict';

  var TAB = '[role="tab"], [data-slot="tabs-trigger"]';
  var OPEN = '[aria-selected="true"], [aria-current="page"], [data-active="true"]';

  function tabs(strip) { return Array.prototype.slice.call(strip.querySelectorAll(TAB)); }

  function fade(strip) {
    var over = strip.scrollWidth - strip.clientWidth;
    var state = 'none';
    if (over > 2) {
      var atStart = strip.scrollLeft <= 1, atEnd = strip.scrollLeft >= over - 1;
      state = atStart ? 'end' : (atEnd ? 'start' : 'both');
    }
    if (strip.getAttribute('data-fade') !== state) strip.setAttribute('data-fade', state);
  }

  // Scroll a tab clear of the faded edges, so neither the tab nor its focus ring sits under the fade.
  function show(strip, tab) {
    if (!tab || strip.scrollWidth <= strip.clientWidth) return fade(strip);
    var box = strip.getBoundingClientRect(), r = tab.getBoundingClientRect(), pad = 32;
    if (r.left < box.left + pad) strip.scrollLeft += r.left - box.left - pad;
    else if (r.right > box.right - pad) strip.scrollLeft += r.right - box.right + pad;
    fade(strip);
  }

  function reveal(strip) { show(strip, strip.querySelector(OPEN)); }

  function init(strip) {
    if (strip.hasAttribute('data-tab-strip-ready')) return;
    strip.setAttribute('data-tab-strip-ready', '');
    strip.addEventListener('scroll', function () { fade(strip); }, { passive: true });
    strip.addEventListener('focusin', function (e) {
      var tab = e.target.closest ? e.target.closest(TAB) : null;
      if (tab && strip.contains(tab)) show(strip, tab);
    });
    strip.addEventListener('keydown', function (e) {
      var keys = { ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last' };
      if (!(e.key in keys) || !e.target.matches(TAB)) return;
      var list = tabs(strip), at = list.indexOf(e.target), to;
      if (keys[e.key] === 'first') to = 0;
      else if (keys[e.key] === 'last') to = list.length - 1;
      else to = (at + keys[e.key] + list.length) % list.length;
      e.preventDefault();
      list[to].focus();
    });
    if (window.MutationObserver) {
      new MutationObserver(function () { reveal(strip); })
        .observe(strip, { attributes: true, subtree: true, attributeFilter: ['aria-selected', 'aria-current', 'data-active'] });
    }
    if (window.ResizeObserver) new ResizeObserver(function () { reveal(strip); }).observe(strip);
    reveal(strip);
  }

  function scan() { Array.prototype.forEach.call(document.querySelectorAll('[data-tab-strip]'), init); }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scan); else scan();
  window.addEventListener('load', scan);
})();
