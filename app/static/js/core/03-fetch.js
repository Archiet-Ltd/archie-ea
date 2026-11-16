/**
 * core/03-fetch.js — Unified HTTP fetch wrapper
 *
 * Requires: core/00-namespace.js, core/01-logger.js
 *
 * Replaces:
 *   - app/static/js/shared/api-fetch.js   (window.apiFetch)
 *   - app/static/scripts/api-client.js    (APIClient class + 7 sub-classes)
 *   - Inline fetch() calls in data-table.js, capability_map, etc.
 *   - Duplicate CSRF token extraction in every file
 *
 * Design decisions:
 *   - Single function: Platform.fetch(url, options)
 *   - Convenience methods: Platform.fetch.get / .post / .put / .patch / .delete
 *   - CSRF token injected automatically on mutating methods
 *   - Plain-object bodies auto-serialised to JSON
 *   - Non-ok responses throw a structured PlatformError
 *   - 204 No Content returns null
 *   - Integrates with Platform.toast for user-visible errors (opt-out via silent:true)
 *   - Integrates with Alpine loading store if present
 *
 * Usage:
 *   const data = await Platform.fetch('/api/applications');
 *   const result = await Platform.fetch('/api/applications', {
 *       method: 'POST',
 *       body: { name: 'MyApp' }
 *   });
 *   // Convenience:
 *   const data   = await Platform.fetch.get('/api/applications', { page: 1 });
 *   const result = await Platform.fetch.post('/api/applications', { name: 'x' });
 *   await Platform.fetch.put('/api/applications/1', { name: 'y' });
 *   await Platform.fetch.patch('/api/applications/1', { status: 'active' });
 *   await Platform.fetch.delete('/api/applications/1');
 */

(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/03-fetch.js');
    }

    let log = global.Platform.log
        ? global.Platform.log.child('fetch')
        : { debug: function(){}, warn: function(){}, error: function(){} };

    // ── CSRF token ───────────────────────────────────────────────────────────
    function getCsrfToken() {
        let meta = global.document.querySelector('meta[name="csrf-token"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    // ── Mutating methods that require CSRF ───────────────────────────────────
    let MUTATING = { POST: true, PUT: true, PATCH: true, DELETE: true };

    // ── Navigation-in-progress flag ──────────────────────────────────────────
    // The browser rejects any in-flight fetch with a plain
    // `TypeError: Failed to fetch` when the document unloads mid-request --
    // indistinguishable, by error shape alone, from a real connection failure.
    // A page-level widget that fires a fetch from x-init (e.g. the dashboard's
    // Executive Summary panel) has no request of its own to cancel and no
    // caller-owned AbortSignal, so the explicit-cancellation guard below never
    // catches it: every reload or "click through before it resolves" logged a
    // false "Network error" that was really just the user leaving the page.
    // pagehide fires before the request is aborted, so the flag is set in time.
    let navigatingAway = false;
    if (global.addEventListener) {
        global.addEventListener('pagehide', function () { navigatingAway = true; });
    }

    // ── Plain-object detection ───────────────────────────────────────────────
    function isPlainObject(v) {
        return (
            v !== null &&
            v !== undefined &&
            typeof v === 'object' &&
            !(v instanceof FormData) &&
            !(v instanceof Blob) &&
            !(v instanceof ArrayBuffer) &&
            !(v instanceof URLSearchParams) &&
            !(v instanceof ReadableStream)
        );
    }

    // ── Loading store integration ────────────────────────────────────────────
    function loadingStart() {
        if (typeof Alpine !== 'undefined' && Alpine.store) {
            // Best-effort UI affordance: a missing/broken loading store must not block the request.
            try { Alpine.store('loading') && Alpine.store('loading').start && Alpine.store('loading').start(); }
            catch(e) { /* swallow-ok: a missing or broken Alpine loading store must not fail the request the user asked for */ }
        }
    }
    function loadingStop() {
        if (typeof Alpine !== 'undefined' && Alpine.store) {
            // Best-effort UI affordance: a missing/broken loading store must not block the request.
            try { Alpine.store('loading') && Alpine.store('loading').stop && Alpine.store('loading').stop(); }
            catch(e) { /* swallow-ok: runs in the completion path of every request; a store failure here must not mask the response or the real error */ }
        }
    }

    // ── Core fetch function ──────────────────────────────────────────────────
    /**
     * @param {string} url
     * @param {object} [options]
     * @param {string}  [options.method]   - HTTP method (default: GET)
     * @param {object|FormData|string} [options.body] - Request body
     * @param {object}  [options.headers]  - Additional headers
     * @param {AbortSignal} [options.signal] - Caller-owned request cancellation
     * @param {boolean} [options.silent]   - Suppress toast on error (default: false)
     * @param {string}  [options.errorMsg] - Custom error message for toast
     * @returns {Promise<*>} Parsed JSON, text, or null (204)
     */
    async function platformFetch(url, options) {
        options = options || {};

        let method = ((options.method || 'GET')).toUpperCase();
        let silent = options.silent === true;
        let headers = Object.assign({}, options.headers || {});
        let body = options.body;

        // Inject CSRF for mutating methods
        if (MUTATING[method]) {
            let token = getCsrfToken();
            if (token) {
                headers['X-CSRFToken'] = token;
            }
        }

        // Auto-serialise plain objects
        if (isPlainObject(body)) {
            if (!headers['Content-Type'] && !headers['content-type']) {
                headers['Content-Type'] = 'application/json';
            }
            body = JSON.stringify(body);
        }

        let fetchOptions = {
            method:      method,
            headers:     headers,
            credentials: 'include'
        };
        if (body !== undefined && body !== null) {
            fetchOptions.body = body;
        }
        if (options.signal !== undefined) {
            fetchOptions.signal = options.signal;
        }

        log.debug(method, url);
        loadingStart();

        try {
            let response;
            try {
                response = await global.fetch(url, fetchOptions); // raw-fetch-ok: core implementation must use native fetch
            } catch (networkErr) {
                // An explicitly cancelled request is still rejected to its caller,
                // but is not a network outage. Unrelated failures (including a
                // generic AbortError with no cancelled caller signal) retain the
                // normal error reporting below.
                if (fetchOptions.signal && fetchOptions.signal.aborted &&
                    (networkErr?.name === 'AbortError' || networkErr === fetchOptions.signal.reason)) {
                    throw networkErr;
                }
                // The document is on its way out -- this is a navigation, not a
                // network outage. Don't toast it and don't count it as a
                // console-hygiene defect.
                if (navigatingAway) {
                    log.debug('Network error (navigating away)', url, networkErr);
                    const navError = new Error(options.errorMsg || 'Navigated away before request completed');
                    navError.type = 'NavigationAbort';
                    navError.originalError = networkErr;
                    throw navError;
                }
                let netMsg = options.errorMsg || ('Network error: ' + (networkErr.message || 'Request failed'));
                if (!silent && global.Platform.toast) {
                    global.Platform.toast.error(netMsg);
                }
                // Only log to console if not silent
                if (!silent) {
                    log.error('Network error', url, networkErr);
                } else {
                    log.debug('Network error (silent)', url, networkErr);
                }
                const netError = new Error(netMsg);
                netError.type = 'NetworkError';
                netError.originalError = networkErr;
                throw netError;
            }

            // Non-ok response
            if (!response.ok) {
                let errData = null;
                // Error body is not guaranteed to be JSON; fall back to statusText/HTTP code below.
                try { errData = await response.json(); }
                catch(e) { /* swallow-ok: the error body may be HTML or empty; the failure itself is still reported below from statusText/HTTP code */ }
                // ARCH-041: a validation 400 from the JSON API carries no top-level
                // "message"/"error" — its detail lives in errData.errors, a
                // {field: [msg, ...]} map (see e.g. app/modules/applications
                // create). Falling straight through to response.statusText showed
                // callers the literal, useless string "Bad Request" instead of the
                // specific reason. Flatten the first message per field into a
                // readable summary here so every generic caller (toast, non-field
                // aware code) gets something actionable; callers that want to
                // render per-field errors still have the full map on err.data.errors.
                let flattenedFieldErrors = null;
                if (errData && errData.errors && typeof errData.errors === 'object') {
                    flattenedFieldErrors = Object.keys(errData.errors)
                        .map(function (field) {
                            let msgs = errData.errors[field];
                            let first = Array.isArray(msgs) ? msgs[0] : msgs;
                            return field + ': ' + first;
                        })
                        .join('; ');
                }
                const errMsg = options.errorMsg ||
                    (errData && (errData.message || errData.error)) ||
                    flattenedFieldErrors ||
                    response.statusText ||
                    ('HTTP ' + response.status);

                // A-08: an expired/invalidated session (e.g. after a server
                // restart) currently fails a write with a bare 400 whose
                // error_type is "csrf" but whose message says the *session*
                // (not the token) is the problem — see the CSRFError
                // handler registered in app/_bootstrap/extensions.py. The old
                // behaviour was to toast a generic error and leave the user
                // looking authenticated while every subsequent write also
                // silently fails. Detect that specific case and hand off to
                // Platform.sessionTimeout's non-dismissable re-auth prompt
                // instead of the ordinary error toast, so the user is told
                // to log back in rather than left to keep clicking a button
                // that will never work. A plain missing-token CSRF error
                // (no "session"/"expired" wording) is a different failure
                // with a different fix (retry with a fresh token) and must
                // not be relabelled as a session expiry.
                const isSessionExpired = errData && errData.error_type === 'csrf' &&
                    /session/i.test(errMsg) && /expir/i.test(errMsg);

                if (isSessionExpired && global.Platform.sessionTimeout &&
                    typeof global.Platform.sessionTimeout.forceReauth === 'function') {
                    global.Platform.sessionTimeout.forceReauth(errMsg);
                } else if (!silent && global.Platform.toast) {
                    global.Platform.toast.error(errMsg);
                }

                // Announce to screen readers (only if not silent)
                if (!silent && typeof Alpine !== 'undefined' && Alpine.store) {
                    try {
                        // Best-effort a11y announcement; the toast above already carries the error visually.
                        const ann = Alpine.store('announcer');
                        if (ann && ann.assertive) ann.assertive('Error: ' + errMsg);
                    } catch(e) { /* swallow-ok: screen-reader mirror of an error the toast above already showed; announcing twice or not at all must not replace the real error */ }
                }

                // Only log to console if not silent
                if (!silent) {
                    log.error('HTTP ' + response.status, url, errMsg);
                } else {
                    log.debug('HTTP ' + response.status + ' (silent)', url, errMsg);
                }
                const httpError = new Error(errMsg);
                httpError.type    = 'HttpError';
                httpError.status  = response.status;
                httpError.data    = errData;
                httpError.response = response;
                throw httpError;
            }

            // 204 No Content
            if (response.status === 204) {
                return null;
            }

            // Parse by content-type
            const ct = response.headers.get('content-type') || '';
            if (ct.indexOf('application/json') !== -1) {
                return response.json();
            }
            return response.text();

        } finally {
            loadingStop();
        }
    }

    // ── Convenience methods ──────────────────────────────────────────────────

    /**
     * GET with optional query-string params object.
     * @param {string} url
     * @param {object} [params]   - Key/value pairs appended as ?key=value
     * @param {object} [options]  - Additional fetch options
     */
    platformFetch.get = function (url, params, options) {
        if (params && typeof params === 'object' && !(params instanceof URLSearchParams)) {
            const qs = new URLSearchParams(params).toString();
            if (qs) url = url + (url.indexOf('?') === -1 ? '?' : '&') + qs;
        }
        return platformFetch(url, Object.assign({ method: 'GET' }, options));
    };

    /**
     * POST with a body.
     * @param {string} url
     * @param {object|FormData} [body]
     * @param {object} [options]
     */
    platformFetch.post = function (url, body, options) {
        return platformFetch(url, Object.assign({ method: 'POST', body: body }, options));
    };

    /**
     * PUT with a body.
     */
    platformFetch.put = function (url, body, options) {
        return platformFetch(url, Object.assign({ method: 'PUT', body: body }, options));
    };

    /**
     * PATCH with a body.
     */
    platformFetch.patch = function (url, body, options) {
        return platformFetch(url, Object.assign({ method: 'PATCH', body: body }, options));
    };

    /**
     * DELETE.
     */
    platformFetch.delete = function (url, options) {
        return platformFetch(url, Object.assign({ method: 'DELETE' }, options));
    };

    // ── Legacy shim ──────────────────────────────────────────────────────────
    // Existing code that calls window.apiFetch() will continue to work.
    if (typeof global.apiFetch === 'undefined') {
        global.apiFetch = platformFetch;
    }

    // ── CSRF safety net for raw fetch() call sites ───────────────────────────
    // Hundreds of legacy call sites use bare fetch() for mutating requests and
    // hand-add X-CSRFToken — or forget to, in which case global CSRFProtect
    // rejects the request with a 400 the user sees as a dead button. Until every
    // site is migrated to Platform.fetch, inject the token for same-origin
    // mutating requests that don't already carry it. Cross-origin requests are
    // left untouched (never leak the token off-origin).
    (function patchFetchForCsrf() {
        const nativeFetch = global.fetch.bind(global);
        global.fetch = function (input, init) {
            try {
                const method = ((init && init.method) ||
                    (input && input.method) || 'GET').toUpperCase();
                if (MUTATING[method]) {
                    const url = (typeof input === 'string') ? input
                        : (input && input.url) || '';
                    const resolved = new global.URL(url, global.location.href);
                    if (resolved.origin === global.location.origin) {
                        const token = getCsrfToken();
                        if (token) {
                            init = init || {};
                            const h = new global.Headers(
                                init.headers || (input && input.headers) || undefined);
                            if (!h.has('X-CSRFToken') && !h.has('X-CSRF-Token')) {
                                h.set('X-CSRFToken', token);
                                init = Object.assign({}, init, { headers: h });
                            }
                        }
                    }
                }
            } catch (e) {
                log.warn('CSRF injection skipped', e);
            }
            return nativeFetch(input, init); // raw-fetch-ok: CSRF safety net must call native fetch to avoid recursion
        };
    }());

    /* The sample company takes a few seconds to load. The first press locks the button and says so;
       a second tap on a slow phone sends nothing. One handler for every page that offers the sample:
       it listens on the document, so a form added later (the chat draws its offer) is covered too. */
    document.addEventListener('submit', function (event) {
        const form = event.target;
        if (!form || !form.matches || !form.matches('[data-sample-form]')) return;
        if (form.dataset.sampleBusy === 'true') { event.preventDefault(); return; }
        form.dataset.sampleBusy = 'true';
        const button = form.querySelector('button[type="submit"]');
        if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
        const line = form.querySelector('[data-sample-busy]');
        if (line) line.hidden = false;
    });

    global.Platform.register('fetch', platformFetch);

}(window));
