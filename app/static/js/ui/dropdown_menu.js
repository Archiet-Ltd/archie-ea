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
    var ITEMS = '[role="menuitem"]:not([disabled]):not([aria-disabled="true"])';
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
