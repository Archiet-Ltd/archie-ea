/* A form field that chooses one architecture element of a given type by typing its name.
 *
 * Built on the shared element picker (intelligence/picker.js) and the element
 * search the rest of the product uses. The person types a name, picks a match,
 * and the form sends the chosen element's id. The server turns that element
 * into the goal or capability record it belongs to, in the person's own
 * organisation, so nothing here decides what is allowed.
 *
 * Use it as x-data="elementChoice('prefix', 'Goal')" on the form, with the
 * entity_picker macro for the box and a hidden input bound to chosenId.
 */
(function (global) {
    'use strict';

    var SEARCH_URL = '/archimate/api/elements/search';

    function elementChoice(prefix, elementType) {
        var base = global.Intelligence.picker(prefix);
        var parentOnInput = base.onInput;
        return Object.assign(base, {
            chosenId: '',
            chosenName: '',

            onInput: function () {
                this.chosenId = '';
                this.chosenName = '';
                parentOnInput.call(this);
            },

            async searchOptions(term) {
                var resp = await Platform.fetch.get(
                    SEARCH_URL, { q: term, type: elementType, limit: 10 }, { silent: true });
                var rows = resp && Array.isArray(resp.data) ? resp.data : [];
                return rows.map(function (row) { return { id: row.id, name: row.name }; });
            },

            onSelect: function (option) {
                this.chosenId = String(option.id);
                this.chosenName = option.name;
            }
        });
    }

    global.elementChoice = elementChoice;
})(window);
