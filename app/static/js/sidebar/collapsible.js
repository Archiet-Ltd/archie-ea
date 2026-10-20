/* Sidebar collapsible (design/patterns.json: sidebar-collapsible).
 *
 * One Alpine component for every collapsible item rendered by
 * components/sidebar_nav.html. Open state is remembered per item id in
 * localStorage under archie_sidebar_group_<id>; a collapsible whose child is
 * the current page (active) is always open, whatever was remembered.
 * Opening one item never closes another.
 */
(function () {
    var KEY = 'archie_sidebar_group_';

    function remembered(id) {
        try {
            return window.localStorage.getItem(KEY + id);
        } catch (e) {
            return null;
        }
    }

    function remember(id, open) {
        try {
            window.localStorage.setItem(KEY + id, open ? '1' : '0');
        } catch (e) {
            /* private mode or blocked storage: the state lives for the page only */
        }
    }

    function register() {
        if (!window.Alpine || !window.Alpine.data) return;
        window.Alpine.data('sidebarCollapsible', function (id, serverOpen, active) {
            var stored = remembered(id);
            var initial = active ? true : (stored === null ? !!serverOpen : stored === '1');
            return {
                id: id,
                open: initial,
                toggle: function () {
                    this.open = !this.open;
                    remember(this.id, this.open);
                }
            };
        });
    }

    if (window.Alpine && window.Alpine.data) {
        register();
    } else {
        document.addEventListener('alpine:init', register);
    }
})();
