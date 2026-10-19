/* Dashboard overview: the answer cascade and the recent-changes table.
 *
 * Reads only. Picks the most recently changed application in the last 30
 * days, resolves it to its ArchiMate element and asks the impact question
 * through the same Intelligence API the Ask page uses (core.js). Falls back
 * to the first element the search returns when nothing changed recently.
 */
(function (global) {
    'use strict';

    var CHANGES_URL = '/intelligence/api/history/changes';
    var DAYS = 30;

    function humanTable(name) {
        return String(name || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
    }

    function whenText(iso) {
        if (!iso) { return ''; }
        var d = new Date(iso);
        if (isNaN(d.getTime())) { return iso; }
        return d.toISOString().slice(0, 16).replace('T', ' ');
    }

    function changeName(change) {
        var snap = change.snapshot || change.previous_snapshot || {};
        return snap.name || snap.title || ('#' + change.record_id);
    }

    global.overviewCascade = function () {
        return {
            state: 'loading',
            questionText: 'What breaks if … fails or is under-resourced?',
            centreId: null,
            centreName: '',
            rows: [],
            changes: [],
            changesState: 'loading',

            init: function () {
                var self = this;
                this.loadChanges().then(function () { return self.pickElement(); }).then(function (element) {
                    if (!element) { self.state = 'empty'; return; }
                    self.centreId = element.id;
                    self.centreName = element.name;
                    self.questionText = 'What breaks if ' + element.name + ' fails or is under-resourced?';
                    return self.load(element.id);
                }).catch(function () { self.state = 'error'; });
            },

            loadChanges: function () {
                var self = this;
                var to = new Date();
                var from = new Date(to.getTime() - DAYS * 86400000);
                return Platform.fetch.get(CHANGES_URL, { from: from.toISOString(), to: to.toISOString() }, { silent: true })
                    .then(function (resp) {
                        var list = resp && resp.data && Array.isArray(resp.data.changes) ? resp.data.changes
                            : (resp && Array.isArray(resp.changes) ? resp.changes : []);
                        self.changes = list.slice(0, 10).map(function (c) {
                            return {
                                key: c.table_name + ':' + c.record_id + ':' + c.valid_from,
                                when: whenText(c.recorded_at || c.valid_from),
                                what: changeName(c),
                                table: humanTable(c.table_name),
                                action: c.action || '',
                                by: c.changed_by_name || '',
                                tableName: c.table_name
                            };
                        });
                        self.changesState = self.changes.length ? 'ready' : 'empty';
                    })
                    .catch(function () { self.changesState = 'error'; });
            },

            pickElement: function () {
                var candidate = this.changes.filter(function (c) {
                    return /application/.test(c.tableName || '') && c.what && c.what.charAt(0) !== '#';
                })[0];
                var term = candidate ? candidate.what : '';
                if (!term) {
                    return Intelligence.searchElements('a').then(function (hits) { return hits[0] || null; });
                }
                return Intelligence.searchElements(term).then(function (hits) {
                    var exact = hits.filter(function (h) { return String(h.name).toLowerCase() === term.toLowerCase(); })[0];
                    return exact || hits[0] || null;
                });
            },

            load: function (elementId) {
                var self = this;
                this.state = 'loading';
                return Intelligence.fetchImpact(elementId, { includeDerived: true, maxDepth: 3 }).then(function (payload) {
                    self.centreId = elementId;
                    var name = Intelligence.nameOf(payload.elements || {}, elementId) || self.centreName;
                    self.centreName = name;
                    self.questionText = 'What breaks if ' + name + ' fails or is under-resourced?';
                    self.rows = Intelligence.buildRows(payload, elementId);
                    self.state = self.rows.length ? 'ready' : 'empty';
                }).catch(function () { self.state = 'error'; });
            }
        };
    };
})(window);
