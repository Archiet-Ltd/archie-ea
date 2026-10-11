/**
 * core-public.js — GENERATED FILE, do not edit directly.
 *
 * Built by `python scripts/build_js.py` from the numbered files in
 * app/static/js/core/, concatenated in load order. Edit the source file
 * under app/static/js/core/ and rerun the build; `--check` fails CI if this
 * file has drifted from its sources.
 *
 * Source order:
 *   00. 00-namespace.js
 *   01. 01-logger.js
 *   02. 02-sanitize.js
 *   03. 03-fetch.js
 *   04. 04-toast.js
 *   05. 05-error.js
 */

// >>> app/static/js/core/00-namespace.js
/**
 * core/00-namespace.js — Platform namespace + load-order guard
 *
 * MUST be the FIRST script loaded on every page (before any other platform JS).
 *
 * Establishes the single global namespace `window.Platform` so that all
 * subsequent modules attach to it instead of polluting `window` directly.
 *
 * Load-order contract
 * -------------------
 * 00-namespace  → defines Platform + registry
 * 01-logger     → Platform.log
 * 02-sanitize   → Platform.sanitize
 * 03-fetch      → Platform.fetch
 * 04-toast      → Platform.toast
 * 05-error      → Platform.error
 * 07-dialog    → Platform.confirm / Platform.alert
 * ui/modal      → Platform.modal
 * ui/pagination → Platform.pagination
 * ui/filter     → Platform.filter
 * ui/selection  → Platform.selection
 * ui/table      → Platform.table
 *
 * Feature modules (app/static/js/<domain>/*.js) load after all core + ui.
 *
 * Rules enforced here
 * -------------------
 * 1. Only ONE instance of Platform may exist per page.
 * 2. Each module calls Platform.register(name, api) exactly once.
 * 3. Duplicate registration throws in development, warns in production.
 * 4. No module may attach directly to window (except the Platform alias).
 */

(function (global) {
    'use strict';

    // ── Guard: only initialise once ─────────────────────────────────────────
    if (global.Platform) {
        // Already loaded — do not re-initialise.
        return;
    }

    // ── Environment detection ────────────────────────────────────────────────
    const isDev = (
        global.location &&
        (global.location.hostname === 'localhost' ||
         global.location.hostname === '127.0.0.1' ||
         global.location.hostname.endsWith('.local'))
    );

    // ── Module registry ──────────────────────────────────────────────────────
    const _registry = Object.create(null);

    /**
     * Register a named module on the Platform namespace.
     * @param {string} name  - Unique module name (e.g. 'log', 'fetch', 'modal')
     * @param {object} api   - Public API object to expose as Platform[name]
     */
    function register(name, api) {
        if (typeof name !== 'string' || !name) {
            throw new TypeError('[Platform] register: name must be a non-empty string');
        }
        if (_registry[name]) {
            const msg = '[Platform] Duplicate module registration: "' + name + '". ' +
                      'Each module must register exactly once.';
            if (isDev) {
                throw new Error(msg);
            } else {
                // Production: warn but don't crash.
                if (global.console && global.console.warn) {
                    global.console.warn(msg);  // console-ok: build/load-order defect (a module bundled twice) with no user-actionable remedy; this is the namespace bootstrap, so Platform.toast does not exist yet, and dev throws instead.
                }
                return;
            }
        }
        _registry[name] = true;
        Platform[name] = api;
    }

    /**
     * Check whether a module has been registered.
     * @param {string} name
     * @returns {boolean}
     */
    function has(name) {
        return Boolean(_registry[name]);
    }

    /**
     * Assert that a required module is loaded. Throws if missing.
     * Feature modules call this to declare their dependencies.
     * @param {...string} names - Module names that must already be registered
     */
    function require(/* ...names */) {
        const names = Array.prototype.slice.call(arguments);
        names.forEach(function (name) {
            if (!_registry[name]) {
                throw new Error(
                    '[Platform] Required module "' + name + '" is not loaded. ' +
                    'Check your script load order.'
                );
            }
        });
    }

    // ── Version ──────────────────────────────────────────────────────────────
    const VERSION = '1.0.0';

    // ── Public Platform object ───────────────────────────────────────────────
    const Platform = {
        VERSION:  VERSION,
        isDev:    isDev,
        register: register,
        has:      has,
        require:  require,

        // Convenience: emit a CustomEvent on document (used by all modules)
        emit: function (eventName, detail) {
            const ev = new CustomEvent(eventName, {
                detail:  detail || {},
                bubbles: true
            });
            global.document.dispatchEvent(ev);
        },

        // Convenience: subscribe to a document-level CustomEvent
        on: function (eventName, handler) {
            global.document.addEventListener(eventName, handler);
        },

        // Convenience: unsubscribe
        off: function (eventName, handler) {
            global.document.removeEventListener(eventName, handler);
        }
    };

    // ── Expose globally ──────────────────────────────────────────────────────
    global.Platform = Platform;

    // Legacy shim: pages that still reference `window.Platform` directly
    // will find it. No other global is created by this file.

}(window));
// <<< app/static/js/core/00-namespace.js

// >>> app/static/js/core/01-logger.js
/**
 * core/01-logger.js — Unified logging and debugging module
 *
 * Requires: core/00-namespace.js
 *
 * Replaces:
 *   - Scattered console.log / console.error calls across all JS files
 *   - No centralised log level control
 *   - No production log suppression
 *
 * Usage:
 *   Platform.log.debug('Loading data', { page: 1 });
 *   Platform.log.info('User clicked save');
 *   Platform.log.warn('Deprecated function called', 'oldFn');
 *   Platform.log.error('Fetch failed', error);
 *   Platform.log.group('Modal lifecycle');
 *   Platform.log.groupEnd();
 *   Platform.log.time('render');
 *   Platform.log.timeEnd('render');
 *
 * Log levels (ascending severity):
 *   0 = DEBUG   (dev only)
 *   1 = INFO    (dev only)
 *   2 = WARN    (dev + prod)
 *   3 = ERROR   (dev + prod)
 *   4 = SILENT  (nothing)
 */

(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/01-logger.js');
    }

    let LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3, SILENT: 4 };
    let LEVEL_NAMES = ['DEBUG', 'INFO', 'WARN', 'ERROR'];

    // In development show everything; in production only WARN+
    let _currentLevel = global.Platform.isDev ? LEVELS.DEBUG : LEVELS.WARN;

    // Prefix for all log lines so they are easy to grep
    let PREFIX = '[Platform]';

    function _shouldLog(level) {
        return level >= _currentLevel;
    }

    function _format(level, args) {
        let label = LEVEL_NAMES[level] || '?';
        let parts = [PREFIX + '[' + label + ']'];
        for (let i = 0; i < args.length; i++) {
            parts.push(args[i]);
        }
        return parts;
    }

    let log = {
        /**
         * Set the minimum log level.
         * @param {'DEBUG'|'INFO'|'WARN'|'ERROR'|'SILENT'} levelName
         */
        setLevel: function (levelName) {
            let level = LEVELS[levelName.toUpperCase()];
            if (level === undefined) {
                throw new RangeError('[Platform.log] Unknown level: ' + levelName);
            }
            _currentLevel = level;
        },

        getLevel: function () {
            return LEVEL_NAMES[_currentLevel] || 'SILENT';
        },

        debug: function () {
            if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.debug) {
                global.console.debug.apply(global.console, _format(LEVELS.DEBUG, arguments));
            }
        },

        info: function () {
            if (_shouldLog(LEVELS.INFO) && global.console && global.console.info) {
                global.console.info.apply(global.console, _format(LEVELS.INFO, arguments));
            }
        },

        warn: function () {
            if (_shouldLog(LEVELS.WARN) && global.console && global.console.warn) {
                global.console.warn.apply(global.console, _format(LEVELS.WARN, arguments));
            }
        },

        error: function () {
            if (_shouldLog(LEVELS.ERROR) && global.console && global.console.error) {
                global.console.error.apply(global.console, _format(LEVELS.ERROR, arguments));
            }
        },

        group: function (label) {
            if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.group) {
                global.console.group(PREFIX + ' ' + label);
            }
        },

        groupEnd: function () {
            if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.groupEnd) {
                global.console.groupEnd();
            }
        },

        time: function (label) {
            if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.time) {
                global.console.time(PREFIX + ':' + label);
            }
        },

        timeEnd: function (label) {
            if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.timeEnd) {
                global.console.timeEnd(PREFIX + ':' + label);
            }
        },

        /**
         * Create a child logger with a fixed sub-prefix.
         * @param {string} namespace  e.g. 'modal', 'fetch', 'pagination'
         * @returns {object} Logger with same API but prefixed messages
         */
        child: function (namespace) {
            let ns = '[' + namespace + ']';
            return {
                debug: function () {
                    if (_shouldLog(LEVELS.DEBUG) && global.console && global.console.debug) {
                        let args = [PREFIX + ns].concat(Array.prototype.slice.call(arguments));
                        global.console.debug.apply(global.console, args);
                    }
                },
                info: function () {
                    if (_shouldLog(LEVELS.INFO) && global.console && global.console.info) {
                        let args = [PREFIX + ns].concat(Array.prototype.slice.call(arguments));
                        global.console.info.apply(global.console, args);
                    }
                },
                warn: function () {
                    if (_shouldLog(LEVELS.WARN) && global.console && global.console.warn) {
                        let args = [PREFIX + ns].concat(Array.prototype.slice.call(arguments));
                        global.console.warn.apply(global.console, args);
                    }
                },
                error: function () {
                    if (_shouldLog(LEVELS.ERROR) && global.console && global.console.error) {
                        let args = [PREFIX + ns].concat(Array.prototype.slice.call(arguments));
                        global.console.error.apply(global.console, args);
                    }
                }
            };
        }
    };

    global.Platform.register('log', log);

}(window));
// <<< app/static/js/core/01-logger.js

// >>> app/static/js/core/02-sanitize.js
/**
 * core/02-sanitize.js — Unified HTML sanitization and escaping
 *
 * Requires: core/00-namespace.js, core/01-logger.js
 *
 * Replaces:
 *   - app/static/js/safe_html.js          (safeHTML, safeText, escapeHtml globals)
 *   - Inline escapeHtml() in toast-notifications.js
 *   - Inline escapeHtml() in modal_manager.js
 *   - Any direct innerHTML assignments across feature modules
 *
 * Rules:
 *   - NEVER use innerHTML directly — always go through Platform.sanitize.html()
 *   - NEVER trust user-supplied strings in template literals without escapeHtml()
 *   - DOMPurify is the primary sanitizer; a safe fallback is provided if absent
 *
 * Usage:
 *   Platform.sanitize.html(element, '<b>user content</b>');
 *   Platform.sanitize.text(element, 'raw user text');
 *   const safe = Platform.sanitize.escape('user <input>');
 *   const clean = Platform.sanitize.purify('<script>alert(1)</script><b>ok</b>');
 */

(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/02-sanitize.js');
    }

    if (!global.Platform.log) {
        throw new Error('[Platform] core/01-logger.js must be loaded before core/02-sanitize.js');
    }

    let log = global.Platform.log.child('sanitize');

    // ── DOMPurify config ─────────────────────────────────────────────────────
    // Allow standard HTML but strip all event handlers and dangerous protocols.
    let PURIFY_CONFIG = {
        ALLOWED_TAGS: [
            'a', 'abbr', 'b', 'blockquote', 'br', 'button', 'caption',
            'cite', 'code', 'col', 'colgroup', 'dd', 'del', 'details',
            'dfn', 'div', 'dl', 'dt', 'em', 'figcaption', 'figure',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img',
            'input', 'ins', 'kbd', 'label', 'li', 'mark', 'ol', 'p',
            // option/optgroup: 'select' was allowed and its CHILDREN were not,
            // so every dynamically-rendered dropdown kept its box and lost all
            // of its choices. The owner reported two blank dropdowns in the
            // capability mapping dialog on 31 Aug 2026; measured in-browser,
            // all four settings selects rendered with zero options. A select
            // with no options is not a degraded control, it is an unusable one.
            'optgroup', 'option',
            'pre', 'q', 's', 'samp', 'section', 'select', 'small',
            'span', 'strong', 'sub', 'summary', 'sup', 'table',
            'tbody', 'td', 'textarea', 'tfoot', 'th', 'thead', 'time',
            'tr', 'u', 'ul', 'var', 'wbr',
            // Lucide icon host
            'svg', 'path', 'circle', 'rect', 'line', 'polyline',
            'polygon', 'g', 'defs', 'use', 'symbol'
        ],
        ALLOWED_ATTR: [
            'aria-*', 'class', 'data-*', 'disabled', 'for', 'href',
            'id', 'name', 'placeholder', 'readonly', 'role', 'src',
            'style', 'tabindex', 'target', 'title', 'type', 'value',
            // Form state and constraints. Without 'checked'/'selected' a
            // re-rendered checkbox or dropdown loses what the user chose, which
            // reads as the app forgetting their input. All of these are inert
            // -- no script vector -- unlike formaction/srcdoc, which stay out.
            'checked', 'selected', 'multiple', 'required',
            'min', 'max', 'step', 'maxlength', 'minlength', 'pattern',
            'rows', 'cols', 'autocomplete',
            // SVG
            'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap',
            'stroke-linejoin', 'viewBox', 'xmlns', 'width', 'height',
            'cx', 'cy', 'r', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
            'points', 'transform'
        ],
        FORBID_TAGS:  ['script', 'style', 'iframe', 'object', 'embed', 'form'],
        FORBID_ATTR:  ['onerror', 'onload', 'onclick', 'onmouseover'],
        ALLOW_DATA_ATTR: true
    };

    /**
     * Sanitize an HTML string using DOMPurify.
     * Falls back to stripping all tags if DOMPurify is unavailable.
     * @param {string} html
     * @returns {string} Safe HTML string
     */
    function purify(html) {
        if (typeof html !== 'string') {
            return '';
        }
        if (global.DOMPurify && typeof global.DOMPurify.sanitize === 'function') {
            return global.DOMPurify.sanitize(html, PURIFY_CONFIG);
        }
        // Fallback: strip all tags (safe but lossy)
        log.warn('DOMPurify not loaded — stripping all HTML tags as fallback');
        let tmp = global.document.createElement('div');
        tmp.textContent = html;
        return tmp.innerHTML;
    }

    /**
     * Set innerHTML of an element using sanitized HTML.
     * This is the ONLY approved way to set innerHTML on the platform.
     * @param {HTMLElement} el
     * @param {string} html
     */
    // Table-context tags whose children (tr/td) need a table wrapper for
    // DOMPurify to parse correctly.  Without the wrapper the browser's HTML
    // parser strips <tr>/<td> because they are invalid inside a <div>.
    let TABLE_CONTEXT_TAGS = { TBODY: 1, THEAD: 1, TFOOT: 1 };

    function html(el, htmlString) {
        if (!el || !(el instanceof global.Element)) {
            log.warn('sanitize.html: target is not a DOM element', el);
            return;
        }

        // When the target is a table section, wrap the fragment so DOMPurify
        // parses <tr>/<td> in a valid table context, then extract the rows.
        if (TABLE_CONTEXT_TAGS[el.tagName]) {
            let wrapper = '<table><tbody>' + htmlString + '</tbody></table>';
            let clean = purify(wrapper);
            // Extract inner content from the sanitized <table><tbody>…</tbody></table>
            let tmp = global.document.createElement('div');
            tmp.innerHTML = clean;
            let innerTbody = tmp.querySelector('tbody');
            el.innerHTML = innerTbody ? innerTbody.innerHTML : clean;
            return;
        }

        el.innerHTML = purify(htmlString);
    }

    /**
     * Set textContent of an element (always safe — never parses HTML).
     * @param {HTMLElement} el
     * @param {string|null|undefined} text
     */
    function text(el, value) {
        if (!el || !(el instanceof global.Element)) {
            log.warn('sanitize.text: target is not a DOM element', el);
            return;
        }
        el.textContent = String(value !== null && value !== undefined ? value : '');
    }

    /**
     * Escape a string for safe embedding inside a template-literal HTML string.
     * Use this when building HTML strings that will be passed to sanitize.html().
     * @param {string|null|undefined} value
     * @returns {string}
     */
    function escape(value) {
        if (value === null || value === undefined) return '';
        let div = global.document.createElement('div');
        div.textContent = String(value);
        return div.innerHTML;
    }

    let sanitize = {
        html:   html,
        text:   text,
        escape: escape,
        purify: purify
    };

    // ── Legacy global shims (backward-compat, read-only) ────────────────────
    // Existing code that calls safeHTML(), safeText(), escapeHtml() directly
    // will continue to work. New code must use Platform.sanitize.*
    if (typeof global.safeHTML === 'undefined') {
        global.safeHTML = html;
    }
    if (typeof global.safeText === 'undefined') {
        global.safeText = text;
    }
    if (typeof global.escapeHtml === 'undefined') {
        global.escapeHtml = escape;
    }

    global.Platform.register('sanitize', sanitize);

}(window));
// <<< app/static/js/core/02-sanitize.js

// >>> app/static/js/core/03-fetch.js
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

    global.Platform.register('fetch', platformFetch);

}(window));
// <<< app/static/js/core/03-fetch.js

// >>> app/static/js/core/04-toast.js
/**
 * core/04-toast.js — Unified notification utility
 *
 * Requires: core/00-namespace.js, core/01-logger.js, core/02-sanitize.js
 *
 * Replaces:
 *   - app/static/js/shared/toast-notifications.js  (ToastNotification class, window.toast, window.showToast)
 *   - showDataTableToast() in shared/data-table.js
 *   - Inline toast creation in export-utils.js, capability_map/index.js
 *   - alert() calls anywhere on the platform
 *
 * Rules:
 *   - ONE toast container per page (#platform-toast-container)
 *   - Max 5 toasts visible simultaneously (FIFO eviction)
 *   - All message text is escaped before rendering
 *   - No inline JS in toast HTML (event listeners attached programmatically)
 *   - Accessible: role="alert", aria-live="assertive" for errors, "polite" for others
 *
 * Usage:
 *   Platform.toast.success('Saved!');
 *   Platform.toast.error('Save failed', { description: 'DB timeout' });
 *   Platform.toast.warning('Unsaved changes');
 *   Platform.toast.info('Processing...');
 *   Platform.toast.loading('Uploading file...');          // no auto-dismiss
 *   const id = Platform.toast.loading('Working...');
 *   Platform.toast.dismiss(id);                           // manual dismiss
 *   await Platform.toast.promise(myPromise, {
 *       loading: 'Saving...',
 *       success: 'Saved!',
 *       error:   'Failed to save'
 *   });
 */

(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/04-toast.js');
    }

    let log = global.Platform.log
        ? global.Platform.log.child('toast')
        : { debug: function(){}, warn: function(){}, error: function(){} };

    let sanitize = global.Platform.sanitize || {
        escape: function(s) {
            let d = global.document.createElement('div');
            d.textContent = String(s || '');
            return d.innerHTML;
        }
    };

    // ── Constants ────────────────────────────────────────────────────────────
    let CONTAINER_ID    = 'platform-toast-container';
    let MAX_TOASTS      = 5;
    let DEFAULT_DURATION = {
        success: 4000,
        info:    4000,
        warning: 5000,
        error:   6000,
        loading: 0       // never auto-dismiss
    };

    let ICONS = {
        success: 'check-circle',
        error:   'x-circle',
        warning: 'alert-triangle',
        info:    'info',
        loading: 'loader-2'
    };

    let ARIA_LIVE = {
        success: 'polite',
        info:    'polite',
        warning: 'polite',
        error:   'assertive',
        loading: 'polite'
    };

    // ── State ────────────────────────────────────────────────────────────────
    let _container = null;
    let _toasts    = [];   // [{ id, element, timerId }]

    // ── Container ────────────────────────────────────────────────────────────
    function _ensureContainer() {
        if (_container && _container.isConnected) return _container;
        _container = global.document.getElementById(CONTAINER_ID);
        if (!_container) {
            _container = global.document.createElement('div');
            _container.id = CONTAINER_ID;
            // Positioned fixed, bottom-right on desktop, top-centre on mobile
            _container.className = [
                'fixed', 'top-16', 'left-1/2', '-translate-x-1/2',
                'sm:top-auto', 'sm:left-auto', 'sm:translate-x-0',
                'sm:bottom-4', 'sm:right-4',
                'z-[9999]',
                'flex', 'flex-col-reverse', 'gap-2',
                'pointer-events-none',
                'max-w-sm', 'w-full'
            ].join(' ');
            _container.setAttribute('aria-label', 'Notifications');
            global.document.body.appendChild(_container);
        }
        return _container;
    }

    // ── Toast element factory ────────────────────────────────────────────────
    function _createToastEl(id, type, message, description, dismissible) {
        let el = global.document.createElement('div');
        el.id = id;
        el.setAttribute('role', 'alert');
        el.setAttribute('aria-live', ARIA_LIVE[type] || 'polite');
        el.setAttribute('aria-atomic', 'true');

        // Base classes
        el.className = [
            'pointer-events-auto',
            'flex', 'items-start', 'gap-3',
            'rounded-lg', 'border', 'p-4', 'shadow-lg',
            'bg-background', 'text-foreground',
            'transition-all', 'duration-300',
            'translate-x-full', 'opacity-0'   // initial hidden state
        ].join(' ');

        // Type-specific border colour
        let borderMap = {
            success: 'border-emerald-500',
            error:   'border-destructive',
            warning: 'border-yellow-500',
            info:    'border-primary',
            loading: 'border-blue-400'
        };
        el.classList.add(borderMap[type] || 'border-border');

        // Icon colour
        let iconColorMap = {
            success: 'text-emerald-500',
            error:   'text-destructive',
            warning: 'text-amber-500',
            info:    'text-primary',
            loading: 'text-blue-400 animate-spin'
        };

        let iconName  = ICONS[type] || 'info';
        let iconColor = iconColorMap[type] || 'text-foreground';
        let safeMsg   = sanitize.escape(message);
        let safeDesc  = description ? sanitize.escape(description) : '';

        // Build inner HTML using safe escaped strings only.
        // iconName and iconColor come from trusted internal maps — not user input.
        let closeBtn = dismissible
            ? '<button class="ml-auto shrink-0 text-muted-foreground hover:text-foreground transition-colors" data-toast-close aria-label="Dismiss notification">' +
              '<i data-lucide="x" class="w-4 h-4"></i></button>'
            : '';

        let innerHtml =
            '<div class="shrink-0 ' + iconColor + '">' +
                '<i data-lucide="' + iconName + '" class="w-5 h-5"></i>' +
            '</div>' +
            '<div class="flex-1 min-w-0">' +
                '<p class="text-sm font-medium leading-snug">' + safeMsg + '</p>' +
                (safeDesc ? '<p class="text-xs text-muted-foreground mt-0.5">' + safeDesc + '</p>' : '') +
            '</div>' +
            closeBtn;

        // Use Platform.sanitize.html if available, else set directly
        // (content is already escaped above — this is belt-and-suspenders)
        if (global.Platform.sanitize && typeof global.Platform.sanitize.html === 'function') {
            global.Platform.sanitize.html(el, innerHtml);
        } else {
            el.innerHTML = innerHtml;
        }

        // Attach close button listener (no inline JS)
        if (dismissible) {
            let btn = el.querySelector('[data-toast-close]');
            if (btn) {
                btn.addEventListener('click', function () { _dismiss(id); });
            }
        }

        return el;
    }

    // ── Dismiss ──────────────────────────────────────────────────────────────
    function _dismiss(id) {
        let idx = _toasts.findIndex(function (t) { return t.id === id; });
        if (idx === -1) return;

        let entry = _toasts[idx];
        if (entry.timerId) clearTimeout(entry.timerId);

        // Animate out
        entry.element.classList.add('translate-x-full', 'opacity-0');
        entry.element.classList.remove('translate-x-0', 'opacity-100');

        setTimeout(function () {
            if (entry.element.parentNode) {
                entry.element.parentNode.removeChild(entry.element);
            }
        }, 300);

        _toasts.splice(idx, 1);
        log.debug('dismissed', id);
    }

    // ── Show ─────────────────────────────────────────────────────────────────
    /**
     * @param {string} message
     * @param {'success'|'error'|'warning'|'info'|'loading'} type
     * @param {object} [options]
     * @param {string}  [options.description]
     * @param {number}  [options.duration]     - ms; 0 = no auto-dismiss
     * @param {boolean} [options.dismissible]  - show close button (default true)
     * @returns {string} toastId
     */
    function _show(message, type, options) {
        options = options || {};
        type = type || 'info';

        let duration    = options.duration !== undefined ? options.duration : DEFAULT_DURATION[type];
        let dismissible = options.dismissible !== false;
        let description = options.description || '';

        // Evict oldest if at capacity
        if (_toasts.length >= MAX_TOASTS) {
            _dismiss(_toasts[0].id);
        }

        let id = 'toast-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
        let el = _createToastEl(id, type, message, description, dismissible);

        _ensureContainer().appendChild(el);

        // Trigger enter animation on next frame
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                el.classList.remove('translate-x-full', 'opacity-0');
                el.classList.add('translate-x-0', 'opacity-100');
            });
        });

        // Re-init Lucide icons inside the new toast
        if (global.lucide && typeof global.lucide.createIcons === 'function') {
            setTimeout(function () { global.lucide.createIcons(); }, 0);
        }

        let timerId = null;
        if (duration > 0) {
            timerId = setTimeout(function () { _dismiss(id); }, duration);
        }

        _toasts.push({ id: id, element: el, timerId: timerId });
        log.debug('show', type, message);
        return id;
    }

    // ── Public API ───────────────────────────────────────────────────────────
    let toast = {
        success: function (msg, opts) { return _show(msg, 'success', opts); },
        error:   function (msg, opts) { return _show(msg, 'error',   opts); },
        warning: function (msg, opts) { return _show(msg, 'warning', opts); },
        info:    function (msg, opts) { return _show(msg, 'info',    opts); },
        loading: function (msg, opts) {
            return _show(msg, 'loading', Object.assign({ duration: 0, dismissible: false }, opts));
        },
        dismiss:    _dismiss,
        dismissAll: function () {
            let ids = _toasts.map(function (t) { return t.id; });
            ids.forEach(_dismiss);
        },

        /**
         * Show a loading toast, then resolve to success/error based on promise.
         * @param {Promise} promise
         * @param {{ loading: string, success: string|Function, error: string|Function }} messages
         */
        promise: async function (promise, messages) {
            messages = messages || {};
            let loadingMsg = messages.loading || 'Loading…';
            let successMsg = messages.success || 'Done!';
            let errorMsg   = messages.error   || 'Something went wrong';

            let loadingId = toast.loading(loadingMsg);
            try {
                let result = await promise;
                _dismiss(loadingId);
                toast.success(typeof successMsg === 'function' ? successMsg(result) : successMsg);
                return result;
            } catch (err) {
                _dismiss(loadingId);
                toast.error(typeof errorMsg === 'function' ? errorMsg(err) : errorMsg, {
                    description: err && err.message ? err.message : undefined
                });
                throw err;
            }
        }
    };

    // ── Legacy shims ─────────────────────────────────────────────────────────
    // window.toast — existing code that calls window.toast.success() etc.
    if (typeof global.toast === 'undefined') {
        global.toast = toast;
    }

    // window.showToast(message, type) — legacy string API
    if (typeof global.showToast === 'undefined') {
        global.showToast = function (message, type) {
            if (message !== null && typeof message === 'object') {
                let text    = message.title || message.message || String(message);
                let variant = message.variant || message.type || type || 'info';
                let ttype   = variant === 'destructive' ? 'error' : (variant || 'info');
                return toast[ttype] ? toast[ttype](text) : toast.info(text);
            }
            let t = type || 'info';
            return toast[t] ? toast[t](message) : toast.info(message);
        };
    }

    // CustomEvent bridge: window.dispatchEvent(new CustomEvent('show-toast', { detail: { message, type } }))
    global.window.addEventListener('show-toast', function (e) {
        let detail  = (e && e.detail) || {};
        let message = detail.message || '';
        let type    = detail.type    || 'info';
        if (message) {
            _show(message, type, { duration: detail.duration });
        }
    });

    global.Platform.register('toast', toast);

}(window));
// <<< app/static/js/core/04-toast.js

// >>> app/static/js/core/05-error.js
/**
 * core/05-error.js — Unified error handling module
 *
 * Requires: core/00-namespace.js, core/01-logger.js, core/04-toast.js
 *
 * Replaces:
 *   - Scattered try/catch with console.error across all feature modules
 *   - alert() calls used as error UI
 *   - Inconsistent error shapes (some have .message, some .error, some .detail)
 *   - No global unhandled-rejection handler
 *
 * Design:
 *   - Platform.error.handle(err, context)  — central handler for caught errors
 *   - Platform.error.boundary(fn, context) — wraps async functions safely
 *   - Platform.error.normalise(err)        — extracts a clean message from any error shape
 *   - Global window.onerror + unhandledrejection listeners log silently in prod
 *
 * Usage:
 *   // Wrap an async handler
 *   button.addEventListener('click', Platform.error.boundary(async () => {
 *       const data = await Platform.fetch.post('/api/save', payload);
 *       Platform.toast.success('Saved!');
 *   }, 'save-button'));
 *
 *   // Handle a caught error explicitly
 *   try {
 *       await riskyOperation();
 *   } catch (err) {
 *       Platform.error.handle(err, 'riskyOperation');
 *   }
 *
 *   // Normalise any error shape to a string
 *   const msg = Platform.error.normalise(err);
 */

(function (global) {
    'use strict';

    if (!global.Platform) {
        throw new Error('[Platform] core/00-namespace.js must be loaded before core/05-error.js');
    }

    const log = global.Platform.log
        ? global.Platform.log.child('error')
        : { warn: function(){}, error: function(){} };

    // ── Normalise any error shape to a human-readable string ─────────────────
    /**
     * Extract a clean message from any error-like value.
     * Handles: Error objects, fetch response shapes, Flask JSON error bodies,
     *          plain strings, and unknown objects.
     * @param {*} err
     * @returns {string}
     */
    function normalise(err) {
        if (!err) return 'An unknown error occurred';

        // Standard Error object
        if (err instanceof Error) {
            return err.message || err.toString();
        }

        // String
        if (typeof err === 'string') return err;

        // Flask/API JSON body shapes
        if (typeof err === 'object') {
            return (
                err.message  ||
                err.error    ||
                err.detail   ||
                err.msg      ||
                (err.errors && JSON.stringify(err.errors)) ||
                JSON.stringify(err)
            );
        }

        return String(err);
    }

    // ── Central error handler ────────────────────────────────────────────────
    /**
     * Handle a caught error: log it and show a toast (unless it was already
     * shown by Platform.fetch).
     *
     * @param {*}      err      - The error to handle
     * @param {string} [context] - Descriptive label for logging (e.g. function name)
     * @param {object} [options]
     * @param {boolean} [options.silent]   - Suppress toast (default: false)
     * @param {string}  [options.toastMsg] - Override toast message
     */
    function handle(err, context, options) {
        options = options || {};
        const msg = normalise(err);
        const label = context ? ('[' + context + '] ') : '';

        log.error(label + msg, err);

        // HttpError/NetworkError are server-visible failures already (the
        // server logged the request); only ship genuinely client-only errors
        // (a thrown JS bug) to avoid double-recording the same failure twice.
        const isServerVisible = err && (err.type === 'HttpError' || err.type === 'NetworkError');
        if (!isServerVisible) {
            _reportToServer({
                message: label + msg,
                location: context || (global.location ? global.location.pathname : 'unknown'),
                stack: (err instanceof Error) ? err.stack : null
            });
        }

        // Platform.fetch already shows a toast for HttpError / NetworkError.
        // Avoid double-toasting those.
        const alreadyToasted = (
            err &&
            (err.type === 'HttpError' || err.type === 'NetworkError')
        );

        if (!options.silent && !alreadyToasted && global.Platform.toast) {
            global.Platform.toast.error(options.toastMsg || msg);
        }
    }

    // ── Async boundary wrapper ───────────────────────────────────────────────
    /**
     * Wrap an async function so that any thrown error is automatically handled.
     * Returns a synchronous function suitable for event listeners.
     *
     * @param {Function} fn       - Async function to wrap
     * @param {string}   [context] - Context label for error logging
     * @param {object}   [options] - Passed to handle()
     * @returns {Function}
     */
    function boundary(fn, context, options) {
        return function () {
            const args = arguments;
            const self = this;
            let result;
            try {
                result = fn.apply(self, args);
            } catch (syncErr) {
                handle(syncErr, context, options);
                return;
            }
            // If fn returned a Promise, catch async errors
            if (result && typeof result.then === 'function') {
                result.catch(function (asyncErr) {
                    handle(asyncErr, context, options);
                });
            }
            return result;
        };
    }

    // ── Global unhandled error listeners ────────────────────────────────────
    // Log silently — do NOT show toasts for unhandled errors (too noisy).
    //
    // P-07: these handlers used to pass the raw Error/rejection value
    // straight to log.error() as a positional arg. The browser devtools log itself
    // renders an Error fine interactively, but anything downstream that
    // stringifies the arguments (log capture, a headless test harness
    // reading console text, a future log-shipping hook) calls String()/
    // JSON.stringify() on it — and Error.message/.stack are *non-enumerable*,
    // so both produce the literal word "Object" / "[object Object]" with the
    // real diagnostic content silently dropped. Serialise explicitly instead
    // so the logged string always carries message, stack, and context.
    function _serialiseRejectionReason(reason) {
        if (reason instanceof Error) {
            return {
                message: reason.message || String(reason),
                stack: reason.stack || null,
                name: reason.name || 'Error'
            };
        }
        if (reason && typeof reason === 'object') {
            try {
                return { message: normalise(reason), stack: null, detail: JSON.parse(JSON.stringify(reason)) };
            } catch (e) {
                return { message: normalise(reason), stack: null };
            }
        }
        return { message: String(reason), stack: null };
    }

    // Alpine rejects a transition promise with {isFromCancelledTransition: true}
    // whenever one transition supersedes another -- a toast replacing a toast, an
    // x-show toggled twice before the first finished. That is Alpine's internal
    // "superseded" signal, not a failure: nothing went wrong and there is nothing
    // for anyone to act on. Reporting it as [Platform][error] invents an error
    // that did not happen, which is the same sin as inventing data, and it buries
    // real rejections in noise that scales with how much the UI is used.
    function _isCancelledAlpineTransition(reason) {
        return Boolean(reason)
            && typeof reason === 'object'
            && reason.isFromCancelledTransition === true;
    }

    // WebKit rejects some cancelled transitions and aborted resource loads with
    // a bare DOM Event (no message, no stack) rather than an Error; Chromium
    // does not, which is why the browser-compatibility gate saw '[object Event]'
    // only under webkit. A genuine application error always rejects with an
    // Error, so a rejection whose reason IS an Event carries nothing actionable
    // and is the same benign noise as a cancelled transition — suppress it
    // rather than let it surface as an uncatchable '[object Event]'.
    function _isBareEventRejection(reason) {
        if (!reason || typeof reason !== 'object') return false;
        if (typeof Event !== 'undefined' && reason instanceof Event) return true;
        // Cross-realm safety: an Event from another frame fails instanceof.
        return Object.prototype.toString.call(reason) === '[object Event]';
    }

    // ── Ship errors to the server so silent client-side breakage is visible
    // somewhere other than a browser console nobody is watching ─────────────
    //
    // Deliberately a bare XHR-free `fetch`, not Platform.fetch: Platform.fetch
    // itself calls Platform.error.handle on failure, and reporting an error
    // via the same pipe that can itself throw is the infinite-loop case this
    // guards against below with _reporting.
    let _reporting = false;
    let _reportedFingerprints = Object.create(null);

    function _reportToServer(payload) {
        if (_reporting) return; // never let a failed report re-enter this function
        // Client-side dedup mirrors the server's fingerprinting so a tight
        // error loop (e.g. a broken render firing every animation frame)
        // sends one report, not thousands, before the server even sees it.
        let key = (payload.location || '') + ':' + String(payload.message || '').slice(0, 120);
        if (_reportedFingerprints[key]) return;
        _reportedFingerprints[key] = true;

        _reporting = true;
        try {
            global.fetch('/api/client-error', { // raw-fetch-ok: Platform.fetch itself calls Platform.error.handle on failure; using it here to report an error would be able to re-enter this very function
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: payload.message,
                    location: payload.location,
                    stack: payload.stack || null,
                    level: 'ERROR',
                    url: global.location ? global.location.href : ''
                }),
                credentials: 'same-origin',
                keepalive: true
            }).catch(function () { /* swallow-ok: best-effort telemetry; reporting a failure here would re-enter this reporter */ })
              .finally(function () { _reporting = false; });
        } catch (e) {
            _reporting = false;
        }
    }

    global.window.addEventListener('unhandledrejection', function (event) {
        if (_isCancelledAlpineTransition(event.reason) || _isBareEventRejection(event.reason)) {
            // Always prevented, dev included. The usual reason to let a rejection
            // through in dev is so devtools still shows it -- but there is nothing
            // here worth showing, and leaving it unprevented surfaces a bare
            // "Object" / "[object Event]" in the console and in Playwright's
            // pageerror channel, which is what made the browser gates fail on
            // pages that merely animate or abort an in-flight load.
            event.preventDefault();
            return;
        }
        const serialised = _serialiseRejectionReason(event.reason);
        log.error(
            'Unhandled promise rejection: ' + serialised.message,
            'stack=' + (serialised.stack || 'n/a'),
            serialised.detail !== undefined ? serialised.detail : ''
        );
        _reportToServer({
            message: 'Unhandled promise rejection: ' + serialised.message,
            location: global.location ? global.location.pathname : 'unknown',
            stack: serialised.stack
        });
        // Prevent the browser from logging a duplicate uncaught error
        // only in development (so devtools still shows it).
        if (!global.Platform.isDev) {
            event.preventDefault();
        }
    });

    const _origOnError = global.window.onerror;
    global.window.onerror = function (message, source, lineno, colno, error) {
        const loc = source + ':' + lineno + ':' + colno;
        const stack = (error && error.stack) ? error.stack : 'n/a';
        log.error('Uncaught error: ' + message, 'at ' + loc, 'stack=' + stack);
        _reportToServer({
            message: 'Uncaught error: ' + message,
            location: loc,
            stack: stack !== 'n/a' ? stack : null
        });
        if (typeof _origOnError === 'function') {
            return _origOnError.apply(this, arguments);
        }
        return false;
    };

    // ── Public API ───────────────────────────────────────────────────────────
    const errorModule = {
        normalise: normalise,
        handle:    handle,
        boundary:  boundary
    };

    global.Platform.register('error', errorModule);

}(window));
// <<< app/static/js/core/05-error.js
