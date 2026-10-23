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
                // Leaving the page aborts the cascade's requests cleanly: WebKit
                // logs a console error for any fetch the navigation tears down,
                // and the candidate loop must not start another one.
                this.controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
                if (this.controller) {
                    var abort = function () { self.controller.abort(); };
                    global.addEventListener('pagehide', abort);
                    global.addEventListener('beforeunload', abort);
                }
                this.loadChanges().then(function () { return self.pickElement(); }).then(function (candidates) {
                    return self.loadFirstWithRows(candidates || []);
                }).catch(function () { self.state = 'error'; });
            },

            signal: function () { return this.controller ? this.controller.signal : undefined; },
            aborted: function () { return Boolean(this.controller && this.controller.signal.aborted); },

            loadChanges: function () {
                var self = this;
                var to = new Date();
                var from = new Date(to.getTime() - DAYS * 86400000);
                return Platform.fetch.get(CHANGES_URL, { from: from.toISOString(), to: to.toISOString() }, { silent: true, signal: this.signal() })
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

            /* The element to ask about: the most recently changed application
             * when there is one, otherwise the most connected application-layer
             * element the search returns. Resolution happens in load(), which
             * walks the candidates until one has recorded connections. */
            pickElement: function () {
                var candidate = this.changes.filter(function (c) {
                    return /application/.test(c.tableName || '') && c.what && c.what.charAt(0) !== '#';
                })[0];
                var self = this;
                var terms = candidate ? [candidate.what] : ['a', 'e', 'i', 'o', 's'];
                var seen = {};
                return Promise.all(terms.map(function (t) {
                    return Intelligence.searchElements(t, self.signal()).catch(function () { return []; });
                })).then(function (lists) {
                    var hits = [];
                    lists.forEach(function (list) {
                        list.forEach(function (h) { if (!seen[h.id]) { seen[h.id] = true; hits.push(h); } });
                    });
                    if (candidate) {
                        var exact = hits.filter(function (h) { return String(h.name).toLowerCase() === candidate.what.toLowerCase(); })[0];
                        if (exact) { hits.unshift(exact); }
                    }
                    var apps = hits.filter(function (h) { return h.layer === 'application'; });
                    var pool = (apps.length ? apps : hits).sort(function (a, b) { return (b.relationship_count || 0) - (a.relationship_count || 0); });
                    return pool.slice(0, 8);
                });
            },

            /* Try candidates in order until one answers with rows. */
            loadFirstWithRows: function (candidates) {
                var self = this;
                if (!candidates.length || this.aborted()) { this.state = 'empty'; return Promise.resolve(); }
                var head = candidates[0];
                return this.load(head.id).then(function () {
                    if (self.state === 'ready' || candidates.length === 1 || self.aborted()) { return; }
                    return self.loadFirstWithRows(candidates.slice(1));
                });
            },

            load: function (elementId) {
                var self = this;
                this.state = 'loading';
                return Intelligence.fetchImpact(elementId, { includeDerived: true, maxDepth: 3, signal: this.signal() }).then(function (payload) {
                    self.centreId = elementId;
                    var name = Intelligence.nameOf(payload.elements || {}, elementId) || self.centreName;
                    self.centreName = name;
                    self.questionText = 'What breaks if ' + name + ' fails or is under-resourced?';
                    // The cascade's source tooltip reads sourceConfidence; a row without one
                    // carries null so no text ever reads "undefined".
                    self.rows = Intelligence.buildRows(payload, elementId).map(function (r) {
                        return Object.assign({ sourceConfidence: null }, r);
                    });
                    self.state = self.rows.length ? 'ready' : 'empty';
                }).catch(function () { self.state = 'error'; });
            }
        };
    };
})(window);
