// A link that names a field or a card (a #fragment) lands with that field focused:
// the field itself, or the first control inside the card.
(function () {
  'use strict';

  function focusTarget() {
    var id = decodeURIComponent((window.location.hash || '').slice(1));
    if (!id) { return; }
    var target = document.getElementById(id);
    if (!target) { return; }
    var control = /^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(target.tagName)
      ? target
      : target.querySelector('[data-owner-search], input:not([type=hidden]), select, textarea');
    if (control) {
      control.focus({ preventScroll: false });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', focusTarget);
  } else {
    focusTarget();
  }
}());
