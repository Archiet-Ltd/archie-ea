/* The element picker shared by Ask and Twin map: an ARIA 1.2 combobox with a
 * listbox popup.
 *
 * Focus never leaves the input. The highlighted option is tracked with
 * aria-activedescendant, ArrowDown / ArrowUp / Home / End move it, Enter
 * chooses it and Escape closes the list and leaves what was typed in place.
 * A polite status region reads out how many matches came back.
 *
 * The picker asks for nothing before a search is accepted: no layer, no type
 * and no vocabulary term. It only sends what was typed.
 *
 * A page factory takes this object with Object.assign and supplies onSelect(option).
 */
(function (global) {
    'use strict';

    var DEBOUNCE_MS = 300;

    function picker(idPrefix) {
        return {
            term: '',
            options: [],
            open: false,
            activeIndex: -1,
            activeId: null,
            statusText: '',
            searchFailed: false,
            optionPrefix: idPrefix + '-option-',

            optionId(option) {
                return this.optionPrefix + option.id;
            },

            setActive(index) {
                this.activeIndex = index;
                this.activeId = index >= 0 && this.options[index] ? this.optionId(this.options[index]) : null;
            },

            close() {
                this.open = false;
                this.setActive(-1);
            },

            onInput() {
                clearTimeout(this._searchTimer);
                this._searchSeq = (this._searchSeq || 0) + 1;
                var typed = this.term.trim();
                this.searchFailed = false;
                if (!typed) {
                    this.options = [];
                    this.statusText = '';
                    this.close();
                    return;
                }
                var self = this;
                this._searchTimer = setTimeout(function () { self.runSearch(typed); }, DEBOUNCE_MS);
            },

            async runSearch(typed) {
                var seq = this._searchSeq;
                var found;
                try {
                    found = await global.Intelligence.searchElements(typed);
                } catch (err) {
                    if (seq !== this._searchSeq) return;
                    this.options = [];
                    this.close();
                    this.searchFailed = true;
                    this.statusText = global.Intelligence.ERROR_LINE;
                    return;
                }
                if (seq !== this._searchSeq) return;
                this.options = found.slice(0, 10);
                this.statusText = global.Intelligence.resultsText(this.options.length, typed);
                this.open = this.options.length > 0;
                this.setActive(-1);
            },

            onKeydown(event) {
                var count = this.options.length;
                switch (event.key) {
                    case 'ArrowDown':
                        if (!count) return;
                        event.preventDefault();
                        if (!this.open) {
                            this.open = true;
                            this.setActive(0);
                        } else {
                            this.setActive((this.activeIndex + 1) % count);
                        }
                        break;
                    case 'ArrowUp':
                        if (!count || !this.open) return;
                        event.preventDefault();
                        this.setActive(this.activeIndex <= 0 ? count - 1 : this.activeIndex - 1);
                        break;
                    case 'Home':
                        if (!this.open || !count) return;
                        event.preventDefault();
                        this.setActive(0);
                        break;
                    case 'End':
                        if (!this.open || !count) return;
                        event.preventDefault();
                        this.setActive(count - 1);
                        break;
                    case 'Enter':
                        if (this.open && this.activeIndex >= 0) {
                            event.preventDefault();
                            this.choose(this.options[this.activeIndex]);
                        }
                        break;
                    case 'Escape':
                        if (this.open) {
                            event.preventDefault();
                            event.stopPropagation();
                            this.close();
                        }
                        break;
                    case 'Tab':
                        this.close();
                        break;
                    default:
                        break;
                }
            },

            choose(option) {
                this.term = option.name;
                this._searchSeq = (this._searchSeq || 0) + 1;
                this.close();
                this.onSelect(option);
            }
        };
    }

    global.Intelligence.picker = picker;
})(window);
