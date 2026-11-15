/* The Add dialog on a product's page: find the element the organisation already holds, and join it.
 *
 * Built on the shared element picker (intelligence/picker.js). The person types a few letters and sees
 * the organisation's existing elements of the layer's types. Choosing one joins it; nothing is created.
 * Creating a new element is offered only once a search has found no element of that exact name, and any
 * names close to the typed one are shown first as "Did you mean ...?". The server holds every join, so
 * nothing about an element is kept here.
 */
(function (global) {
    'use strict';

    var SEARCH_URL = '/archimate/api/elements/search';

    function fold(value) {
        return String(value || '').split(/\s+/).filter(Boolean).join(' ').toLowerCase();
    }

    function productAdd(types, labels) {
        return Object.assign(global.Intelligence.picker('add-' + types[0]), {
            types: types,
            labels: labels,
            chosenType: types[0],
            searched: false,
            exact: false,
            similar: [],
            typed: '',

            typeLabel(type) {
                return this.labels[type] || type;
            },

            get canCreate() {
                return this.searched && !this.exact && this.typed !== '';
            },

            get createLabel() {
                return 'Create a new ' + this.typeLabel(this.chosenType).toLowerCase() + " named '" + this.typed + "'";
            },

            onInput() {
                this.searched = false;
                this.similar = [];
                global.Intelligence.picker('x').onInput.call(this);
            },

            async searchOptions(term) {
                var resp = await Platform.fetch.get(
                    SEARCH_URL, { q: term, types: this.types.join(','), limit: 20, similar: 1 }, { silent: true });
                var rows = resp && Array.isArray(resp.data) ? resp.data : [];
                var wanted = fold(term);
                var self = this;
                this.typed = term;
                this.exact = rows.some(function (row) { return fold(row.name) === wanted; });
                this.similar = ((resp && resp.similar) || []).filter(function (row) { return fold(row.name) !== wanted; });
                this.searched = true;
                return rows.map(function (row) {
                    return { id: row.id, name: row.name + ' (' + self.typeLabel(row.type) + ')' };
                });
            },

            onSelect(option) {
                this.join(option.id);
            },

            join(id) {
                this.$refs.existing.value = id;
                this.$refs.newName.value = '';
                this.$refs.addForm.requestSubmit();
            },

            create() {
                this.$refs.existing.value = '';
                this.$refs.newName.value = this.typed;
                this.$refs.addForm.requestSubmit();
            }
        });
    }

    global.productAdd = productAdd;
})(window);
