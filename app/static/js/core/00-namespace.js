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

    // ── Figures ──────────────────────────────────────────────────────────────
    // A score, percentage or money figure that was not measured is said so in
    // words; it is never drawn as 0. Zero stays zero when it was measured.
    const NOT_SCORED = 'Not scored';
    const NOT_RECORDED = 'Not recorded';

    function isMissing(value) {
        if (value === null || value === undefined) { return true; }
        if (typeof value === 'string' && value.trim() === '') { return true; }
        return typeof value === 'number' ? !isFinite(value) : isNaN(Number(value));
    }

    const figure = {
        NOT_SCORED:   NOT_SCORED,
        NOT_RECORDED: NOT_RECORDED,
        missing:      isMissing,

        // percent(0.62, 100) -> "62%"; percent(62) -> "62%"; percent(null) -> "Not scored"
        percent: function (value, scale, missing, digits) {
            if (isMissing(value)) { return missing || NOT_SCORED; }
            const shown = Number(value) * (scale || 1);
            return (digits ? shown.toFixed(digits) : String(Math.round(shown))) + '%';
        },

        // score(72, '/100') -> "72/100"; score(undefined) -> "Not scored"
        score: function (value, suffix, missing) {
            if (isMissing(value)) { return missing || NOT_SCORED; }
            return String(Math.round(Number(value) * 10) / 10) + (suffix || '');
        },

        // number(value) -> localised number, or the missing wording
        number: function (value, missing) {
            if (isMissing(value)) { return missing || NOT_SCORED; }
            return Number(value).toLocaleString();
        },

        // money(1200) -> "£1,200"; money(null) -> "Not recorded"
        money: function (value, missing) {
            if (isMissing(value)) { return missing || NOT_RECORDED; }
            const cm = global.currencyManager;
            if (cm && typeof cm.format === 'function') { return cm.format(Number(value)); }
            return Number(value).toLocaleString();
        },

        // num(null) -> NaN, so a comparison on a missing figure is false
        // instead of reading as 0
        num: function (value) {
            return isMissing(value) ? NaN : Number(value);
        },

        // barWidth(null) -> 0: an unmeasured bar is empty, and the figure
        // beside it says so in words
        barWidth: function (value, scale) {
            return isMissing(value) ? 0 : Math.min(100, Math.max(0, Number(value) * (scale || 1)));
        },

        // chartValue(null) -> null: a chart leaves a gap rather than plotting 0
        chartValue: function (value) {
            return isMissing(value) ? null : Number(value);
        }
    };

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
        },

        figure: figure
    };

    // ── Expose globally ──────────────────────────────────────────────────────
    global.Platform = Platform;

    // Legacy shim: pages that still reference `window.Platform` directly
    // will find it. No other global is created by this file.

}(window));
