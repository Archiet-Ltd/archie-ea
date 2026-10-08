/* The application's technology panel: the nodes and system software it runs on.
 *
 * Built on the shared element picker (intelligence/picker.js). The person types
 * a name; the picker offers only nodes and system software, and choosing one
 * records it at once. Every link is an ArchiMate relationship held by the
 * server, so the list is always read back from the server, never kept here.
 */
(function (global) {
    'use strict';

    var SEARCH_URL = '/archimate/api/elements/search';
    var TECHNOLOGY_TYPES = { node: 'Node', systemsoftware: 'System software' };

    function kind(type) {
        return String(type || '').replace(/[_\s]/g, '').toLowerCase();
    }

    function technologyLinks(applicationId) {
        var base = '/architecture/api/applications/' + applicationId + '/technology-links';
        return Object.assign(global.Intelligence.picker('techstack'), {
            links: [],
            loading: true,
            loadError: null,
            saving: false,

            async init() {
                await this.load();
            },

            async load() {
                this.loading = true;
                this.loadError = null;
                try {
                    var data = await Platform.fetch.get(base, null, { silent: true });
                    this.links = (data && data.links) || [];
                } catch (err) {
                    this.links = [];
                    this.loadError = (err && err.message) || 'The technology links could not be loaded.';
                } finally {
                    this.loading = false;
                }
            },

            /* What the picker offers: technology-layer elements whose type is a node
               or system software, minus those already mapped. */
            async searchOptions(term) {
                var resp = await Platform.fetch.get(SEARCH_URL, { q: term, layer: 'technology', limit: 50 }, { silent: true });
                var rows = resp && Array.isArray(resp.data) ? resp.data : [];
                var mapped = this.links.map(function (link) { return link.element_id; });
                return rows.filter(function (row) {
                    return TECHNOLOGY_TYPES[kind(row.type || row.element_type)] && mapped.indexOf(row.id) === -1;
                }).map(function (row) {
                    var label = TECHNOLOGY_TYPES[kind(row.type || row.element_type)];
                    return { id: row.id, name: row.name + ' (' + label + ')' };
                });
            },

            async onSelect(option) {
                this.saving = true;
                try {
                    await Platform.fetch.post(base, { element_id: option.id }, { silent: true });
                    this.term = '';
                    this.statusText = '';
                    Platform.toast.success('Technology mapped.');
                    await this.load();
                } catch (err) {
                    Platform.toast.error((err && err.message) || 'The technology link could not be saved.');
                } finally {
                    this.saving = false;
                }
            },

            async remove(link) {
                try {
                    await Platform.fetch.delete(base + '/' + link.relationship_id, { silent: true });
                    Platform.toast.success('Technology link removed.');
                    await this.load();
                } catch (err) {
                    Platform.toast.error((err && err.message) || 'The technology link could not be removed.');
                }
            }
        });
    }

    global.technologyLinks = technologyLinks;
})(window);
