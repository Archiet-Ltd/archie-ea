/**
 * core/06-session-timeout.js — Client-side session expiry warning
 *
 * Requires: core/00-namespace.js through core/05-error.js, ui/modal.js (re-auth prompt), components/session_dialogs.html
 *
 * Courtesy warning ahead of the SERVER-enforced idle timeout
 * (app/_bootstrap/session_policy.py). The server is authoritative; this file
 * exists so the user sees it coming instead of losing unsaved work silently.
 *
 * Timer resets on genuine activity (keydown / click / scroll / touchstart /
 * visibilitychange) and on every fetch, and the idle window is read from
 * <body data-session-idle-seconds> so the two halves cannot drift apart.
 *
 * Registered as Platform.sessionTimeout with reset / extend / logout / signedOut methods.
 */
(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/06-session-timeout.js');
    }

    const nativeFetch = global.fetch ? global.fetch.bind(global) : null;

    const log = global.Platform.log
        ? global.Platform.log.child('session')
        : { debug: function(){}, warn: function(){}, error: function(){} };

    // --- Configuration (all values in milliseconds) ---
    // The server (app/_bootstrap/session_policy.py) is the authority. It renders
    // the signed-in person's own idle limit into <body data-session-idle-seconds>
    // (15 minutes for an administrator, 30 for everyone else) and this file only
    // warns ahead of it and says so when it has happened.
    const DEFAULT_IDLE_MS    = 30 * 60 * 1000;
    const MAX_WARNING_BEFORE = 2 * 60 * 1000;         // warn 2 min before the server cuts it
    const LOGIN_URL          = '/account/login';
    const KEEPALIVE_URL      = '/account/session/keepalive';
    const BACKGROUND_HEADER  = 'X-Background-Poll';   // a page's own poll: not the person acting
    const ENDED_CODES        = { idle: 1, revoked: 1, admin_absolute: 1 };

    function readIdleWindow() {
        try {
            const el = global.document.body;
            const raw = el && el.getAttribute('data-session-idle-seconds');
            const secs = raw ? parseInt(raw, 10) : NaN;
            if (!isNaN(secs) && secs > 0) return secs * 1000;
        } catch (e) {
            log.debug('idle window attribute unreadable; using default');
        }
        return DEFAULT_IDLE_MS;
    }

    let IDLE_WINDOW = DEFAULT_IDLE_MS;
    let WARNING_BEFORE = MAX_WARNING_BEFORE;
    let WARNING_DELAY = 0;
    let ACTIVITY_THROTTLE = 60 * 1000;                // at most one keep-alive ping per interval

    function computeWindows() {
        IDLE_WINDOW = readIdleWindow();
        // A short limit (a test configuration) warns at half of it.
        WARNING_BEFORE = Math.min(MAX_WARNING_BEFORE, Math.floor(IDLE_WINDOW / 2));
        WARNING_DELAY = IDLE_WINDOW - WARNING_BEFORE;
        ACTIVITY_THROTTLE = Math.min(60 * 1000, Math.max(1000, Math.floor(IDLE_WINDOW / 4)));
    }

    // --- State ---
    let warningTimerId  = null;
    let logoutTimerId   = null;
    let countdownId     = null;
    let endedShown      = false;
    let modalId         = null;   // re-auth prompt below

    // --- Helpers ---

    function plural(n, unit) { return n + ' ' + unit + (n === 1 ? '' : 's'); }

    function formatRemaining(totalSeconds) {
        if (totalSeconds >= 60) return plural(Math.ceil(totalSeconds / 60), 'minute');
        return plural(Math.max(totalSeconds, 0), 'second');
    }

    function idleLimitText() {
        const secs = Math.round(IDLE_WINDOW / 1000);
        return secs % 60 === 0 ? plural(secs / 60, 'minute') : plural(secs, 'second');
    }

    function byId(id) { return global.document.getElementById(id); }

    function openDialog(id) {
        const el = byId(id);
        if (el && typeof el.fbOpen === 'function') { el.removeAttribute('hidden'); el.fbOpen(); return true; }
        return false;
    }

    function closeDialog(id) {
        const el = byId(id);
        if (el && typeof el.fbClose === 'function') el.fbClose();
    }

    function clearAllTimers() {
        if (warningTimerId !== null) { clearTimeout(warningTimerId); warningTimerId = null; }
        if (logoutTimerId !== null) { clearTimeout(logoutTimerId); logoutTimerId = null; }
        if (countdownId !== null) { clearInterval(countdownId); countdownId = null; }
    }

    function dismissWarning() {
        if (countdownId !== null) { clearInterval(countdownId); countdownId = null; }
        closeDialog('session-warning-dialog');
    }

    function signInHref(code) {
        const nextUrl = global.location.pathname + global.location.search;
        return LOGIN_URL + '?timeout=' + (code === 'idle' ? 'idle' : 'revoked') +
            '&next=' + global.encodeURIComponent(nextUrl);
    }

    // The session is already gone: say so at once, with a way back in.
    function showSignedOut(code) {
        clearAllTimers();
        dismissWarning();
        if (endedShown) return;
        endedShown = true;
        let message = 'You were signed out.';
        if (code === 'idle') {
            message = 'You were signed out after ' + idleLimitText() + ' without activity.';
        } else if (code === 'admin_absolute') {
            message = 'You were signed out because administrator sign-ins end after a fixed time.';
        }
        const msg = byId('session-ended-message');
        if (msg) msg.textContent = message;
        const btn = global.document.querySelector('[data-session-signin]');
        if (btn) btn.setAttribute('data-href', signInHref(code));
        if (!openDialog('session-ended-dialog')) {
            global.location.href = signInHref(code);
        }
    }

    // The clock in this tab can run out while the person works in another tab
    // of the same session. Ask the server before saying anything.
    async function autoLogout() {
        try {
            // raw-fetch-ok: this IS the session probe; Platform.fetch would treat its 401 as an error to toast
            const r = await nativeFetch(KEEPALIVE_URL, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest', [BACKGROUND_HEADER]: '1' }
            });
            if (r.ok && !(r.redirected && /\/account\/login/.test(r.url))) { resetTimer(); return; }
        } catch (e) {
            log.debug('session check failed (network)');
        }
        showSignedOut('idle');
    }

    function resetTimer() {
        if (endedShown) return;
        clearAllTimers();
        dismissWarning();
        computeWindows();
        warningTimerId = setTimeout(showWarning, WARNING_DELAY);
        logoutTimerId  = setTimeout(autoLogout, IDLE_WINDOW + 1000);
    }

    // --- Genuine-activity listeners ---
    // They reset the client timer. The server's stamp only moves when a request
    // is made, so a throttled keep-alive ping follows real interaction.
    let lastActivityPing = 0;

    async function onActivity() {
        if (endedShown) return;
        // While the warning is open only its button counts: a stray click or key
        // press must not quietly hide a warning the person has not answered.
        const warn = byId('session-warning-dialog');
        if (warn && warn.getAttribute('data-state') === 'open') return;
        const now = Date.now();
        resetTimer();
        if (now - lastActivityPing < ACTIVITY_THROTTLE) return;
        lastActivityPing = now;
        try {
            await Platform.fetch.get(KEEPALIVE_URL, null, { silent: true });
        } catch (e) {
            log.debug('keepalive ping failed (non-fatal)');
        }
    }

    function registerActivityListeners() {
        const doc = global.document;
        ['keydown', 'click', 'scroll', 'touchstart'].forEach(function (evt) {
            doc.addEventListener(evt, onActivity, { passive: true });
        });
        doc.addEventListener('visibilitychange', function () {
            if (!doc.hidden) onActivity();
        });
        const stay = doc.querySelector('[data-session-stay]');
        if (stay) stay.addEventListener('click', function () { extendSession(); });
        const signin = doc.querySelector('[data-session-signin]');
        if (signin) {
            signin.addEventListener('click', function (e) {
                e.stopPropagation();
                global.location.href = signin.getAttribute('data-href') || signInHref('idle');
            });
        }
    }

    function showWarning() {
        if (endedShown) return;
        let remainingSeconds = Math.floor(WARNING_BEFORE / 1000);
        const label = byId('session-warning-remaining');
        if (label) label.textContent = formatRemaining(remainingSeconds);
        if (!openDialog('session-warning-dialog')) {
            log.warn('session dialog not available - skipping warning');
            return;
        }
        if (countdownId !== null) clearInterval(countdownId);
        countdownId = setInterval(function () {
            remainingSeconds -= 1;
            if (remainingSeconds <= 0) {
                clearInterval(countdownId);
                countdownId = null;
                return;
            }
            const el = byId('session-warning-remaining');
            if (el) el.textContent = formatRemaining(remainingSeconds);
        }, 1000);
    }

    async function extendSession() {
        // Pressing the button is activity: the ping moves the server's stamp.
        lastActivityPing = Date.now();
        try {
            const r = await Platform.fetch.get(KEEPALIVE_URL, null, { silent: true });
            dismissWarning();
            resetTimer();
            return r;
        } catch (e) {
            // A refused ping means the session is already gone.
            checkSessionEnded();
        }
    }

    // Ask the server whether the session is still alive; used when a request
    // answered 401 without saying why.
    async function checkSessionEnded() {
        try {
            // raw-fetch-ok: this IS the session probe; Platform.fetch would treat its 401 as an error to toast
            const r = await nativeFetch(KEEPALIVE_URL, {
                credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest', [BACKGROUND_HEADER]: '1' }
            });
            if (r.status === 401 || (r.redirected && /\/account\/login/.test(r.url))) showSignedOut(null);
        } catch (e) {
            log.debug('session check failed (network)');
        }
    }

    // --- A-08: reactive re-auth prompt ---
    // Called by core/03-fetch.js the moment a write actually fails because
    // the server-side session/CSRF token is no longer valid (e.g. after a
    // server restart) — distinct from the proactive 30-minutes-before
    // warning above, which fires on a client-side clock that has no idea
    // the server already invalidated the session early. Unlike
    // showWarning(), this prompt is non-dismissable (no backdrop click, no
    // Escape, no "stay logged in" option) because the session is already
    // gone, not merely expiring — offering to "extend" it would just fail
    // again on the next write.
    let reauthShown = false;

    function forceReauth(message) {
        clearAllTimers();
        dismissWarning();

        if (reauthShown) return; // already prompting; don't stack modals
        reauthShown = true;

        if (global.Platform.toast) {
            global.Platform.toast.error(message || 'Your session has expired. Please log in again.');
        }

        const nextUrl = global.location.pathname + global.location.search;
        const loginHref = LOGIN_URL + '?expired=1&next=' + global.encodeURIComponent(nextUrl);

        if (global.Platform.modal && typeof global.Platform.modal.create === 'function') {
            modalId = global.Platform.modal.create({
                id: 'session-expired-reauth',
                title: 'Session Expired',
                size: 'sm',
                content:
                    '<div class="text-center space-y-4">' +
                        '<div class="mx-auto flex items-center justify-center h-12 w-12 rounded-full bg-destructive/10 mb-4">' +
                            '<svg class="h-6 w-6 text-destructive" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">' +
                                '<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />' +
                            '</svg>' +
                        '</div>' +
                        '<p class="text-sm text-muted-foreground">' +
                            'Your session is no longer valid, most likely because it expired or the server restarted. ' +
                            'Any change you just tried to make was not saved. Please log in again to continue.' +
                        '</p>' +
                    '</div>',
                backdrop: false,
                keyboard: false,
                buttons: [
                    {
                        label: 'Log In Again',
                        variant: 'primary',
                        handler: function () {
                            global.location.href = loginHref;
                        },
                        closeOnClick: false
                    }
                ]
            });
            global.Platform.modal.open(modalId);
            log.warn('session invalid — re-auth prompt shown');
        } else {
            // Modal system unavailable — the toast above already told the
            // user; still redirect rather than leaving them stuck.
            global.location.href = loginHref;
        }
    }

    // --- Hook into fetch ---
    // Platform.fetch calls global.fetch internally, so wrapping global.fetch
    // catches both Platform.fetch and any direct call. It does two jobs:
    // a request the person caused counts as activity (a page's own poll, marked
    // with the background header, does not), and any answer of 401 that says the
    // session ended puts the signed-out notice on the page straight away.
    function isBackground(init, input) {
        try {
            const h = new global.Headers((init && init.headers) || (input && input.headers) || undefined);
            return h.get(BACKGROUND_HEADER) === '1';
        } catch (e) {
            return false;
        }
    }

    function hookFetch() {
        if (typeof global.fetch !== 'function') return;
        global.fetch = function (input, init) {
            const background = isBackground(init, input);
            const result = nativeFetch.apply(this, arguments);
            // Best-effort: a failure here must never break the caller's fetch.
            try { if (!background) resetTimer(); } catch (e) { /* swallow-ok: this wraps every fetch on the site; an idle-timer failure must never surface as an error on the caller's unrelated request */ }
            result.then(function (response) {
                if (response && response.status === 401) onUnauthorized(response);
            }, function () { /* swallow-ok: the caller handles its own network failure; this observer only looks for a 401 */ });
            return result;
        };
    }

    function onUnauthorized(response) {
        if (endedShown) return;
        let sameOrigin = true;
        try { sameOrigin = new global.URL(response.url, global.location.href).origin === global.location.origin; } catch (e) { /* swallow-ok: an unreadable URL is treated as ours */ }
        if (!sameOrigin) return;
        response.clone().json().then(function (data) {
            const code = data && data.code;
            if (code && ENDED_CODES[code]) { showSignedOut(code); } else { checkSessionEnded(); }
        }, function () { checkSessionEnded(); });
    }

    // --- Initialization ---
    function init() {
        hookFetch();
        registerActivityListeners();
        resetTimer();
        log.debug('initialized');
    }

    if (global.document.readyState === 'loading') {
        global.document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // --- Public API ---
    const sessionTimeout = {
        reset:       resetTimer,
        extend:      extendSession,
        logout:      autoLogout,
        signedOut:   showSignedOut,
        forceReauth: forceReauth
    };

    global.Platform.register('sessionTimeout', sessionTimeout);

}(window));
