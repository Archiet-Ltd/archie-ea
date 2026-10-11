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
