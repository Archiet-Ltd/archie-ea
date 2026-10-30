/* Sidebar shell behaviour: the expanded/collapsed state on the sidebar root, the trigger and rail
   toggles, Ctrl/Cmd+B, the state kept in a cookie, a drawer below 768px, and the icon rail's tooltips.
   Source: flask-basecoat commit b327186, static/js/src/components/sidebar.js, with the library's
   layer helpers (scroll lock, Escape, focus trap) written out here so the file stands alone.
   Markup: components/ui_sidebar.html. Rules: styles/sidebar-shell.css. */
(function () {
    'use strict';

    var COOKIE = 'sidebar_state';
    var MOBILE = 768;
    var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function init(wrapper) {
        var sidebar = wrapper.querySelector('[data-slot="sidebar"]');
        if (!sidebar) { return; }
        var mode = sidebar.getAttribute('data-collapsible-mode') || 'none';
        var match = document.cookie.match(new RegExp('(?:^|; )' + COOKIE + '=(true|false)'));
        var open = match ? match[1] === 'true' : sidebar.getAttribute('data-state') !== 'collapsed';
        var drawerOpen = false;
        var drawer = null;
        var returnFocus = null;

        function isMobile() { return window.innerWidth < MOBILE; }

        function syncTriggers() {
            var expanded = isMobile() ? drawerOpen : open;
            document.querySelectorAll('[data-slot="sidebar-trigger"]').forEach(function (t) {
                t.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                t.setAttribute('aria-controls', 'sidebar-nav');
            });
        }

        function apply() {
            if (mode === 'none') { return; }
            sidebar.setAttribute('data-state', open ? 'expanded' : 'collapsed');
            sidebar.setAttribute('data-collapsible', open ? '' : mode);
            wrapper.dispatchEvent(new CustomEvent('sidebar', { bubbles: true, detail: { open: open } }));
            hideTip();
            syncTriggers();
        }

        function buildDrawer() {
            drawer = document.createElement('div');
            drawer.setAttribute('data-fb-sidebar-mobile', '');
            drawer.hidden = true;
            // The drawer carries no data-state on its parts: the library's sheet script opens (and moves to <body>) any sheet
            // marked open, which unpins the drawer from its fixed wrapper. This script owns the drawer's state.
            drawer.innerHTML = '<div data-slot="sheet-overlay"></div>' +
                '<div data-slot="sheet-content" data-side="left" role="dialog" aria-modal="true" aria-label="Navigation" data-sidebar="sidebar" data-mobile="true"><div class="flex h-full w-full flex-col" data-fb-mobile-slot></div></div>';
            document.body.appendChild(drawer);
            drawer.firstElementChild.addEventListener('click', function () { setDrawer(false); });
        }

        function setDrawer(next) {
            if (next === drawerOpen) { return; }
            if (!drawer) { buildDrawer(); }
            var slot = drawer.querySelector('[data-fb-mobile-slot]');
            var inner = sidebar.querySelector('[data-slot="sidebar-inner"]') || slot.firstElementChild;
            drawerOpen = next;
            if (next) {
                returnFocus = document.activeElement;
                if (inner && inner.parentNode !== slot) { inner.fbHome = inner.parentNode; slot.appendChild(inner); }
                drawer.hidden = false;
                document.body.style.overflow = 'hidden';
                var first = slot.querySelector(FOCUSABLE);
                if (first) { first.focus(); }
            } else {
                drawer.hidden = true;
                document.body.style.overflow = '';
                if (inner && inner.fbHome) { inner.fbHome.appendChild(inner); }
                if (returnFocus && returnFocus.focus) { returnFocus.focus(); }
            }
            syncTriggers();
        }

        function toggle() {
            if (isMobile()) { setDrawer(!drawerOpen); return; }
            open = !open;
            document.cookie = COOKIE + '=' + open + '; path=/; max-age=' + (60 * 60 * 24 * 7) + '; samesite=lax';
            apply();
        }

        document.addEventListener('click', function (e) {
            var t = e.target.closest('[data-slot="sidebar-trigger"], [data-slot="sidebar-rail"]');
            if (t) { e.preventDefault(); toggle(); }
        });
        document.addEventListener('keydown', function (e) {
            if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {
                var el = e.target;
                if (el && el.isContentEditable) { return; }
                e.preventDefault();
                toggle();
                return;
            }
            if (!drawerOpen) { return; }
            // An open menu inside the drawer takes the first Escape (it has already handled the key).
            if (e.key === 'Escape') { if (!e.defaultPrevented) { e.preventDefault(); setDrawer(false); } return; }
            if (e.key === 'Tab') {
                var items = Array.prototype.filter.call(drawer.querySelectorAll(FOCUSABLE), function (n) { return n.offsetParent !== null; });
                if (!items.length) { return; }
                var firstItem = items[0];
                var lastItem = items[items.length - 1];
                if (e.shiftKey && document.activeElement === firstItem) { e.preventDefault(); lastItem.focus(); }
                else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); firstItem.focus(); }
            }
        });
        window.addEventListener('resize', function () {
            if (drawerOpen && !isMobile()) { setDrawer(false); }
            syncTriggers();
        });
        window.addEventListener('sidebar:open', function () { if (isMobile()) { setDrawer(true); } else if (!open) { toggle(); } });

        /* Tooltips: in the icon rail a menu button shows its label beside the icon. */
        var tip = null;
        function showTip(el) {
            if (!(sidebar.getAttribute('data-collapsible') === 'icon') || drawerOpen) { return; }
            var text = el.getAttribute('data-tooltip');
            if (!text) { return; }
            if (!tip) {
                tip = document.createElement('div');
                tip.className = 'fb-sidebar-tooltip';
                tip.setAttribute('role', 'tooltip');
                tip.id = 'sidebar-tooltip';
                document.body.appendChild(tip);
            }
            tip.textContent = text;
            tip.hidden = false;
            var r = el.getBoundingClientRect();
            tip.style.left = (r.right + 8) + 'px';
            tip.style.top = (r.top + r.height / 2 - tip.offsetHeight / 2) + 'px';
            el.setAttribute('aria-describedby', 'sidebar-tooltip');
        }
        function hideTip() {
            if (tip) { tip.hidden = true; }
            wrapper.querySelectorAll('[aria-describedby="sidebar-tooltip"]').forEach(function (n) { n.removeAttribute('aria-describedby'); });
        }
        wrapper.addEventListener('pointerover', function (e) { var el = e.target.closest('[data-tooltip]'); if (el) { showTip(el); } });
        wrapper.addEventListener('pointerout', function (e) { if (e.target.closest('[data-tooltip]')) { hideTip(); } });
        wrapper.addEventListener('focusin', function (e) { var el = e.target.closest('[data-tooltip]'); if (el) { showTip(el); } });
        wrapper.addEventListener('focusout', hideTip);
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { hideTip(); } });

        wrapper.fbToggle = toggle;
        apply();
    }

    function boot() {
        var wrapper = document.querySelector('[data-slot="sidebar-wrapper"]');
        if (wrapper) { init(wrapper); }
    }
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }
})();
