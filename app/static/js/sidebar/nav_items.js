/* Sidebar items that open: Work's five jobs and Model's layers. A closed item keeps its sub-items in a
   template and places them on the page when it is opened, so the page carries only the links in view.
   What a person opens or closes is kept in a cookie named for them (sidebar_items_<id>) and read back
   by the server on the next page, so the sidebar draws as they left it.
   In the icon rail an item opens the sidebar first. Markup: components/admin_sidebar.html. */
(function () {
    'use strict';

    var script = document.currentScript;
    var COOKIE = (script && script.getAttribute('data-cookie')) || '';

    function remember(key, open) {
        if (!COOKIE) { return; }
        var pairs = {};
        var match = document.cookie.match(new RegExp('(?:^|; )' + COOKIE + '=([^;]*)'));
        if (match) {
            decodeURIComponent(match[1]).split(',').forEach(function (part) {
                var bits = part.split(':');
                if (bits[0] && (bits[1] === '0' || bits[1] === '1')) { pairs[bits[0]] = bits[1]; }
            });
        }
        pairs[key] = open ? '1' : '0';
        var value = Object.keys(pairs).map(function (k) { return k + ':' + pairs[k]; }).join(',');
        document.cookie = COOKIE + '=' + encodeURIComponent(value) + '; path=/; max-age=' + (60 * 60 * 24 * 365) + '; samesite=lax';
    }

    function setOpen(item, open) {
        var button = item.querySelector('[data-nav-toggle]');
        var list = item.querySelector('[data-slot="sidebar-menu-sub"]');
        if (!button || !list) { return; }
        if (open && !list.children.length) {
            var template = item.querySelector('template[data-nav-children]');
            if (template) { list.appendChild(template.content.cloneNode(true)); template.remove(); }
        }
        list.hidden = !open;
        item.setAttribute('data-open', open ? 'true' : 'false');
        button.setAttribute('aria-expanded', open ? 'true' : 'false');
        var chevron = button.querySelector('[data-nav-chevron]');
        if (chevron) { chevron.classList.toggle('rotate-90', open); }
        remember(button.getAttribute('data-nav-toggle'), open);
    }

    document.addEventListener('click', function (e) {
        var button = e.target.closest('[data-nav-toggle]');
        if (!button) { return; }
        var item = button.closest('[data-nav-item]');
        if (!item) { return; }
        var sidebar = document.querySelector('[data-slot="sidebar"]');
        var wrapper = document.querySelector('[data-slot="sidebar-wrapper"]');
        var rail = sidebar && sidebar.getAttribute('data-collapsible') === 'icon' && window.innerWidth >= 768;
        if (rail && wrapper && wrapper.fbToggle) {
            // The icon rail has no room for sub-items: open the sidebar on this item.
            wrapper.fbToggle();
            setOpen(item, true);
            return;
        }
        setOpen(item, item.getAttribute('data-open') !== 'true');
    });
})();
