/* State for the one provenance drawer both pages open from every "Why?".
 *
 * The drawer is filled from the answer already on the page: the row it was
 * opened for is looked up by key, so there is no second request and no second
 * source of names. It follows the query it depends on. While a new answer is
 * still being fetched the drawer shows the loading state, and if the row it was
 * opened for is not in the new answer it says so instead of showing stale
 * detail.
 *
 * The host page supplies `busy` (an answer is being fetched) and `rows` (the
 * row models of the current answer).
 */
(function (global) {
    'use strict';

    var LOADING_HEADING = 'Tracing the impact chain\u2026';
    var UNNAMED_HEADING = 'Name not available';
    var UNAVAILABLE_HEADING = 'Detail not available';

    /* The recorded links behind a worked-out connection, before any is asked for. */
    function emptyWhy() {
        return { state: 'idle', rule: null, links: [], decisions: [], forId: null };
    }

    /* One drawn link as the drawer shows it. Every value is the server's; a
       missing one stays null so the drawer shows it as not recorded. */
    function whyLink(link) {
        var source = link.source || {};
        var target = link.target || {};
        return {
            position: link.position,
            resolved: link.resolved === true,
            sentence: link.sentence || null,
            sourceName: source.name || null,
            sourceHref: source.href || null,
            targetName: target.name || null,
            targetHref: target.href || null,
            drawnBy: link.drawn_by || null,
            drawnAt: global.Intelligence.timeText(link.drawn_at)
        };
    }

    function drawerState() {
        return {
            drawer: {
                opened: 0,
                state: 'ready',
                kind: 'measured',
                heading: '',
                plainTerms: null,
                staleLine: null,
                detail: global.Intelligence.emptyDetail(),
                why: emptyWhy(),
                forKey: null
            },

            openWhy(key) {
                this.drawer.forKey = key;
                // A new opening always starts with "Full detail" collapsed.
                this.drawer.opened += 1;
                this.syncDrawer();
                global.dispatchEvent(new CustomEvent('open-drawer-provenance'));
            },

            syncDrawer() {
                var drawer = this.drawer;
                if (drawer.forKey === null) return;
                if (this.busy) {
                    drawer.state = 'loading';
                    drawer.kind = '';
                    drawer.heading = LOADING_HEADING;
                    return;
                }
                var row = null;
                for (var i = 0; i < this.rows.length; i++) {
                    if (this.rows[i].key === drawer.forKey) { row = this.rows[i]; break; }
                }
                if (row === null) {
                    drawer.state = 'unavailable';
                    drawer.kind = 'unavailable';
                    drawer.heading = UNAVAILABLE_HEADING;
                    drawer.plainTerms = null;
                    drawer.staleLine = null;
                    drawer.detail = global.Intelligence.emptyDetail();
                    drawer.why = emptyWhy();
                    return;
                }
                drawer.state = 'ready';
                // The kind in the title says what is true of this row: a connection
                // somebody drew is measured, one worked out from others is worked
                // out, and a row that cannot be named is not recorded.
                drawer.kind = row.name === null ? 'missing' : (row.derived ? 'derived' : 'measured');
                drawer.heading = row.name === null ? UNNAMED_HEADING : row.name;
                drawer.plainTerms = row.plainTerms;
                drawer.staleLine = row.staleLine;
                drawer.detail = row.detail;
                this.loadWhy(row);
            },

            /* A worked-out row is explained by the links it was worked out from.
               Asked for once per opening; an answer for a row the drawer has
               since left is dropped. */
            loadWhy(row) {
                var drawer = this.drawer;
                var derivedId = row.derived && row.detail ? row.detail.derivedId : null;
                if (derivedId == null) {
                    drawer.why = emptyWhy();
                    return;
                }
                if (drawer.why.forId === derivedId && drawer.why.state !== 'unavailable') return;
                drawer.why = emptyWhy();
                drawer.why.forId = derivedId;
                drawer.why.state = 'loading';
                global.Intelligence.fetchExplanation(derivedId).then(function (explanation) {
                    if (drawer.why.forId !== derivedId) return;
                    drawer.why.rule = explanation.rule || null;
                    drawer.why.links = (explanation.links || []).map(whyLink);
                    drawer.why.decisions = (explanation.decisions || []).filter(function (d) {
                        return d.href && d.title;
                    });
                    drawer.why.state = 'ready';
                }, function () {
                    if (drawer.why.forId !== derivedId) return;
                    drawer.why.state = 'unavailable';
                });
            }
        };
    }

    global.Intelligence.drawerState = drawerState;
})(window);
