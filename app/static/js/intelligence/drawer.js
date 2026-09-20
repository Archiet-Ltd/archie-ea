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

    var LOADING_HEADING = 'Tracing the impact chain…';
    var UNNAMED_HEADING = 'Name not available';
    var UNAVAILABLE_HEADING = 'Detail not available';

    function drawerState() {
        return {
            drawer: {
                opened: 0,
                state: 'ready',
                kind: 'measured',
                heading: '',
                plainTerms: null,
                staleLine: null,
                detail: null,
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
                    drawer.detail = null;
                    return;
                }
                drawer.state = 'ready';
                drawer.kind = row.name === null ? 'missing' : 'measured';
                drawer.heading = row.name === null ? UNNAMED_HEADING : row.name;
                drawer.plainTerms = row.plainTerms;
                drawer.staleLine = row.staleLine;
                drawer.detail = row.detail;
            }
        };
    }

    global.Intelligence.drawerState = drawerState;
})(window);
