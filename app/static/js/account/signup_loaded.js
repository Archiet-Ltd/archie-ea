/* Sign-up: keep the "Loading your canvases and frameworks" card current while the load runs, then stop. */
(function () {
    'use strict';
    var root = document.querySelector('[data-signup-loaded]');
    if (!root || root.getAttribute('data-state') !== 'loading') { return; }
    var url = root.getAttribute('data-url');
    var timer = window.setInterval(function () {
        window.Platform.fetch.get(url, null, { silent: true }).then(function (body) {
            if (!body || !body.html) { return; }
            root.innerHTML = body.html;
            root.setAttribute('data-state', body.state);
            if (body.state !== 'loading') { window.clearInterval(timer); }
        }).catch(function () { /* swallow-ok: a missed poll is retried on the next tick; the last card stays on screen */ });
    }, 3000);
}());
