/**
 * ui/dropdown_menu.js — keyboard model for components/dropdown_menu.html
 *
 * Follows the WAI-ARIA menu button pattern (the model shadcn's DropdownMenu
 * uses): opening moves focus to the first item; ArrowDown/ArrowUp, Home/End
 * and typing a first letter move between items; Enter or Space chooses;
 * Escape closes and returns focus to the trigger; after an item is chosen
 * focus returns to the trigger, or to the subject the action moved (a board
 * card) when the macro names one with data-menu-subject.
 *
 * One delegated listener serves every menu on the page. Open and close stay
 * with the macro's Alpine state; this file only moves focus.
 */
(function (global) {
    'use strict';

    var doc = global.document;
    var ITEMS = '[role="menuitem"]:not([disabled]):not([aria-disabled="true"]), [role="menuitemcheckbox"]:not([disabled])';
    var typed = '';
    var typedAt = 0;

    function rootOf(node) {
        return node && node.closest ? node.closest('[data-dropdown-menu]') : null;
    }

    function triggerOf(root) {
        return root.querySelector('[data-menu-trigger]');
    }

    function panelOf(root) {
        return root.querySelector('[role="menu"]');
    }

    function itemsOf(root) {
        var panel = panelOf(root);
        return panel ? Array.prototype.slice.call(panel.querySelectorAll(ITEMS)) : [];
    }

    function isOpen(root) {
        var t = triggerOf(root);
        return !!t && t.getAttribute('aria-expanded') === 'true';
    }

    function setOpen(root, open) {
        var alpine = global.Alpine;
        if (alpine && alpine.$data) {
            alpine.$data(root).menuOpen = open;
        }
    }

    // A menu inside a scrolling table would be cut off at the table's edge and
    // cover the buttons of the rows below. When any ancestor clips, the open
    // panel is placed against the viewport instead, beside its button.
    function clippedByAncestor(panel) {
        for (var el = panel.parentElement; el && el !== doc.body && el !== doc.documentElement; el = el.parentElement) {
            var cs = global.getComputedStyle(el);
            if (/(auto|scroll|hidden|clip)/.test(cs.overflowX + cs.overflowY)) return true;
        }
        return false;
    }

    function place(root) {
        var panel = panelOf(root);
        var trigger = triggerOf(root);
        if (!panel || !trigger || !isOpen(root) || !clippedByAncestor(panel)) return;
        var t = trigger.getBoundingClientRect();
        var w = panel.offsetWidth;
        var h = panel.offsetHeight;
        var vw = doc.documentElement.clientWidth;
        var vh = global.innerHeight;
        var left = Math.min(Math.max(8, t.right - w), Math.max(8, vw - w - 8));
        var top = t.bottom + 4;
        if (top + h > vh - 8 && t.top - 4 - h >= 8) top = t.top - 4 - h;
        panel.style.position = 'fixed';
        panel.style.left = left + 'px';
        panel.style.top = top + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
        panel.style.margin = '0';
        lift(root);
    }

    // Sticky cells of later rows carry their own stacking level and would paint
    // over the open panel: lift the open row's own levels while the menu is open.
    function lift(root) {
        if (root._lifted) return;
        var lifted = [];
        for (var el = root.parentElement; el && el !== doc.body; el = el.parentElement) {
            var cs = global.getComputedStyle(el);
            if (cs.position !== 'static' && cs.zIndex !== 'auto') {
                lifted.push([el, el.style.zIndex]);
                el.style.zIndex = '60';
            }
        }
        if (!lifted.length) return;
        root._lifted = lifted;
        var trigger = triggerOf(root);
        var watch = new global.MutationObserver(function () {
            if (isOpen(root)) return;
            root._lifted.forEach(function (pair) { pair[0].style.zIndex = pair[1]; });
            root._lifted = null;
            watch.disconnect();
        });
        watch.observe(trigger, { attributes: true, attributeFilter: ['aria-expanded'] });
    }

    function placeSoon(root) {
        global.setTimeout(function () { place(root); }, 0);
    }

    // The fixed panel would stay behind when the table scrolls: close it.
    doc.addEventListener('scroll', function (e) {
        var open = doc.querySelectorAll('[data-dropdown-menu] [data-menu-trigger][aria-expanded="true"]');
        for (var i = 0; i < open.length; i++) {
            var root = rootOf(open[i]);
            var panel = panelOf(root);
            if (panel && panel.style.position === 'fixed' && !panel.contains(e.target)) setOpen(root, false);
        }
    }, true);

    // Focus the first item once Alpine has shown the panel.
    function focusFirstSoon(root) {
        global.setTimeout(function () {
            if (!isOpen(root)) return;
            var items = itemsOf(root);
            if (items.length) items[0].focus();
        }, 30);
    }

    function focusTrigger(root) {
        var t = triggerOf(root);
        if (t && !t.disabled) t.focus();
        return !!t && doc.activeElement === t;
    }

    // Give focus back after a choice. A subject that the action moved is found
    // again by its id, because the board re-renders it in the new column.
    function returnFocus(root, subjectAttr, subjectId, before) {
        var tries = 0;
        function attempt() {
            tries += 1;
            if (subjectAttr) {
                var now = doc.querySelector('[' + subjectAttr + '="' + subjectId + '"]');
                if (now && (now !== before || tries >= 12)) {
                    now.focus();
                    return;
                }
                if (tries < 20) {
                    global.setTimeout(attempt, 50);
                    return;
                }
            }
            if (doc.body.contains(root)) focusTrigger(root);
        }
        global.setTimeout(attempt, 50);
    }

    function typeahead(root, key) {
        var now = Date.now();
        typed = (now - typedAt > 700 ? '' : typed) + key.toLowerCase();
        typedAt = now;
        var items = itemsOf(root);
        if (!items.length) return;
        var current = items.indexOf(doc.activeElement);
        // Repeating one letter cycles through the items that start with it.
        var needle = /^(.)\1+$/.test(typed) ? typed.charAt(0) : typed;
        var order = items.slice(current + 1).concat(items.slice(0, current + 1));
        for (var i = 0; i < order.length; i++) {
            if (order[i].textContent.trim().toLowerCase().indexOf(needle) === 0) {
                order[i].focus();
                return;
            }
        }
    }

    doc.addEventListener('click', function (e) {
        var root = rootOf(e.target);
        if (!root) return;
        var trigger = triggerOf(root);
        if (trigger && trigger.contains(e.target)) {
            placeSoon(root);
            focusFirstSoon(root);
            return;
        }
        var item = e.target.closest('[role="menuitem"]');
        if (!item || !panelOf(root).contains(item)) return;
        var attr = root.getAttribute('data-menu-subject');
        var subject = attr ? root.closest('[' + attr + ']') : null;
        returnFocus(root, attr, subject ? subject.getAttribute(attr) : null, subject);
    });

    doc.addEventListener('keydown', function (e) {
        var root = rootOf(e.target);
        if (!root) return;
        var trigger = triggerOf(root);
        var onTrigger = !!trigger && trigger.contains(e.target);

        if (onTrigger) {
            if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !trigger.disabled) {
                e.preventDefault();
                if (!isOpen(root)) setOpen(root, true);
                placeSoon(root);
                focusFirstSoon(root);
            }
            return;
        }

        if (!isOpen(root)) return;
        var items = itemsOf(root);
        var idx = items.indexOf(doc.activeElement);

        if (e.key === 'Escape') {
            e.preventDefault();
            setOpen(root, false);
            focusTrigger(root);
        } else if (e.key === 'Tab') {
            setOpen(root, false);
        } else if (e.key === 'ArrowDown' && items.length) {
            e.preventDefault();
            items[(idx + 1) % items.length].focus();
        } else if (e.key === 'ArrowUp' && items.length) {
            e.preventDefault();
            items[(idx - 1 + items.length) % items.length].focus();
        } else if (e.key === 'Home' && items.length) {
            e.preventDefault();
            items[0].focus();
        } else if (e.key === 'End' && items.length) {
            e.preventDefault();
            items[items.length - 1].focus();
        } else if ((e.key === ' ' || e.key === 'Enter') && idx >= 0) {
            // Buttons choose natively; a link needs the click made for it.
            if (items[idx].tagName === 'A') {
                e.preventDefault();
                items[idx].click();
            } else if (e.key === ' ') {
                e.preventDefault();
                items[idx].click();
            }
        } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== ' ') {
            typeahead(root, e.key);
        }
    });
})(window);
