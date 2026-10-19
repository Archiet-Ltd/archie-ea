/* The Twin map's keyboard layer: one real button per element, placed over the
 * drawing at the rectangles the drawing engine reports.
 *
 * The engine paints the picture; this paints nothing of its own. The buttons are
 * transparent, so what a person sees is the engine's element, and what a keyboard
 * or a pointer reaches is a button with a name, a pressed state and a focus ring.
 * They stay outside the element that describes the picture to a screen reader.
 *
 * Buttons are kept in the order of the picture (top band first, left to right),
 * which is the order Tab visits them. All text is written with textContent.
 */
(function (global) {
    'use strict';

    var BUTTON_CLASS = 'intel-node absolute block rounded-md bg-transparent p-0 ' +
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
        'focus-visible:ring-offset-2 focus-visible:ring-offset-background';

    function create(host, handlers) {
        var buttons = {};

        function button(id) {
            if (buttons[id]) return buttons[id];
            var el = document.createElement('button');
            el.type = 'button';
            el.className = BUTTON_CLASS;
            el.setAttribute('data-node', String(id));
            var name = document.createElement('span');
            name.className = 'intel-node-name sr-only';
            var centre = document.createElement('span');
            centre.className = 'intel-node-centre sr-only';
            el.appendChild(name);
            el.appendChild(centre);
            el.addEventListener('click', function () { handlers.onSelect(id); });
            buttons[id] = el;
            return el;
        }

        return {
            /* `bounds` is the engine's getNodeBounds(), already in picture order. */
            render: function (model, bounds, selectedId) {
                var byId = {};
                model.nodes.forEach(function (node) { byId[node.id] = node; });
                var wanted = {};
                var order = [];
                bounds.forEach(function (rect) {
                    var node = byId[rect.id];
                    if (!node) return;
                    wanted[rect.id] = true;
                    var el = button(rect.id);
                    el.setAttribute('data-layer', node.band);
                    el.style.left = rect.x + 'px';
                    el.style.top = rect.y + 'px';
                    el.style.width = rect.width + 'px';
                    el.style.height = rect.height + 'px';
                    el.setAttribute('aria-pressed', rect.id === selectedId ? 'true' : 'false');
                    if (node.name) el.setAttribute('title', node.name); else el.removeAttribute('title');
                    el.querySelector('.intel-node-name').textContent = node.name || 'Not recorded';
                    el.querySelector('.intel-node-centre').textContent =
                        rect.id === model.centreId ? ' (centre of the map)' : '';
                    order.push(el);
                });
                Object.keys(buttons).forEach(function (id) {
                    if (wanted[id]) return;
                    if (buttons[id].parentNode) buttons[id].parentNode.removeChild(buttons[id]);
                    delete buttons[id];
                });
                // Tab order follows the picture. Only a button that is out of place
                // is moved, and a move drops focus, so give it back afterwards.
                var focused = document.activeElement;
                var held = focused && host.contains(focused) ? focused : null;
                order.forEach(function (el, i) {
                    if (host.children[i] !== el) host.insertBefore(el, host.children[i] || null);
                });
                if (held && document.activeElement !== held) held.focus();
            },
            select: function (selectedId) {
                Object.keys(buttons).forEach(function (id) {
                    buttons[id].setAttribute('aria-pressed', String(id) === String(selectedId) ? 'true' : 'false');
                });
            },
            focus: function (id) {
                if (buttons[id]) buttons[id].focus();
            }
        };
    }

    global.IntelligenceNodeOverlay = { create: create };
})(window);
