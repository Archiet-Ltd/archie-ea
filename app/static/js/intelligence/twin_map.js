/* The Twin map page.
 *
 * One answer from the impact endpoint feeds three things at once: the banded
 * drawing, the always-present table of the same connections and the panel for
 * the selected element. They are all built from the same rows in the same
 * pass, so the picture, the table and the panel cannot disagree.
 *
 * Referenced as x-data="twinMapSurface()"; a top-level window factory, not an
 * Alpine.data() registration (the CSP-safe interpreter never consults those).
 */
function twinMapSurface() {
    var Intelligence = window.Intelligence;
    var controller = null;

    function findNode(model, id) {
        for (var i = 0; i < model.nodes.length; i++) {
            if (model.nodes[i].id === id) return model.nodes[i];
        }
        return null;
    }

    return Object.assign(Intelligence.picker('twin'), Intelligence.drawerState(), {
        state: 'idle',
        busy: false,
        hasGraph: false,
        centreId: null,
        maxDepth: 3,
        includeDerived: true,
        rows: [],
        model: { nodes: [], edges: [], centreId: null },
        selectedId: null,
        panel: null,
        railOpen: true,
        caption: '',
        depthAnnouncement: '',
        notComputed: false,
        recomputing: false,
        recomputeLine: '',
        recomputeFailed: false,

        init() {
            if (window.d3 && window.IntelligenceGraph) {
                var self = this;
                controller = window.IntelligenceGraph.create(this.$refs.canvas, {
                    onSelect: function (id) { self.selectNode(id); }
                });
            }
            var initial = parseInt(this.$el.getAttribute('data-initial-element'), 10);
            if (!isNaN(initial)) {
                this.centreId = initial;
                this.load({ nameTheInput: true });
            }
        },

        onSelect(option) {
            this.centreId = option.id;
            this.selectedId = null;
            this.load({});
        },

        async load(options) {
            var elementId = this.centreId;
            this._loadSeq = (this._loadSeq || 0) + 1;
            var seq = this._loadSeq;
            this.busy = true;
            this.state = 'loading';
            this.notComputed = false;
            this.syncDrawer();
            var payload;
            try {
                payload = await Intelligence.fetchImpact(elementId, {
                    maxDepth: Number(this.maxDepth),
                    includeDerived: this.includeDerived
                });
            } catch (err) {
                if (seq !== this._loadSeq) return;
                this.rows = [];
                this.state = 'error';
                this.hasGraph = false;
                this.busy = false;
                this.syncDrawer();
                return;
            }
            if (seq !== this._loadSeq) return;

            this.rows = Intelligence.buildRows(payload, elementId);
            this.model = Intelligence.buildGraph(this.rows, payload, elementId);
            this.notComputed = (payload.summary || {}).derivation_state === 'not_computed' && this.rows.length > 0;
            var centreName = Intelligence.nameOf(payload.elements || {}, elementId);
            if (options && options.nameTheInput && centreName && !this.term) this.term = centreName;
            this.caption = (centreName ? 'Connections for ' + centreName + ': ' : 'Connections: ') +
                this.model.nodes.length + ' things, ' + this.model.edges.length + ' connections';
            this.statusText = this.rows.length + ' results for ' + (this.term.trim() || centreName || '');

            if (this.selectedId === null || !findNode(this.model, this.selectedId)) {
                this.selectedId = elementId;
            }
            this.updatePanel();
            this.state = this.rows.length ? 'ready' : 'empty';
            this.hasGraph = this.rows.length > 0;
            this.busy = false;
            this.syncDrawer();
            this.remember(elementId);

            var self = this;
            this.$nextTick(function () {
                if (controller && self.hasGraph) {
                    controller.render(self.model, self.selectedId, self.pathFor(self.selectedId));
                }
                Intelligence.refreshIcons();
                if (options && options.focusCentre && controller) controller.focus(elementId);
            });
        },

        /* Keep the address in step so a reload comes back to the same map. */
        remember(elementId) {
            try {
                var url = new URL(window.location.href);
                url.searchParams.set('element', String(elementId));
                window.history.replaceState(null, '', url.toString());
            } catch (err) {
                // The address is a convenience only.
                return;
            }
        },

        selectNode(id) {
            this.selectedId = id;
            this.updatePanel();
            if (controller) controller.select(id, this.pathFor(id));
        },

        primaryRow(id) {
            var found = null;
            for (var i = 0; i < this.rows.length; i++) {
                var row = this.rows[i];
                if (row.elementId !== id) continue;
                if (row.derived) return row;
                if (found === null) found = row;
            }
            return found;
        },

        /* The edges that make up the way from the centre to a node: each step of
           its chain, plus the worked-out connection itself when there is one. */
        pathFor(id) {
            var path = {};
            var row = this.primaryRow(id);
            if (row === null) return path;
            var chain = row.chainIds || [];
            for (var i = 0; i + 1 < chain.length; i++) {
                for (var j = 0; j < this.model.edges.length; j++) {
                    var edge = this.model.edges[j];
                    if (edge.kind === 'explicit' && edge.from === chain[i] && edge.to === chain[i + 1]) {
                        path[edge.key] = true;
                    }
                }
            }
            if (row.derived) path[row.key] = true;
            return path;
        },

        dependentsOf(id) {
            var seen = {};
            var count = 0;
            for (var i = 0; i < this.rows.length; i++) {
                var row = this.rows[i];
                if (row.elementId === id || seen[row.elementId]) continue;
                if (id === this.centreId || (row.chainIds || []).indexOf(id) !== -1) {
                    seen[row.elementId] = true;
                    count += 1;
                }
            }
            return count;
        },

        updatePanel() {
            var node = findNode(this.model, this.selectedId);
            if (node === null) {
                this.panel = null;
                return;
            }
            var row = this.primaryRow(node.id);
            var dependents = this.dependentsOf(node.id);
            this.panel = {
                id: node.id,
                name: node.name,
                isCentre: node.id === this.centreId,
                hasRow: row !== null,
                rowKey: row !== null ? row.key : null,
                derived: row !== null && row.derived,
                ownerName: row !== null ? row.ownerName : null,
                dependentsLine: dependents === 0
                    ? 'Nothing is recorded as depending on this yet'
                    : Intelligence.pluralThings(dependents) + (dependents === 1 ? ' depends' : ' depend') + ' on this',
                detail: row !== null
                    ? row.detail
                    : { derived: false, type: node.type, layer: node.layer, chain: [], depth: null }
            };
        },

        onDepthInput() {
            this.depthAnnouncement = 'Hop depth: ' + this.maxDepth;
        },

        onDepthChange() {
            this.maxDepth = Number(this.maxDepth);
            this.depthAnnouncement = 'Hop depth: ' + this.maxDepth;
            if (this.centreId !== null) this.load({});
        },

        onDerivedChange() {
            if (this.centreId !== null) this.load({});
        },

        recentre(row) {
            this.centreId = row.elementId;
            this.selectedId = null;
            this.term = row.name || '';
            this.load({ focusCentre: true });
        },

        toggleRail() {
            this.railOpen = !this.railOpen;
            this.$nextTick(function () { if (controller) controller.resize(); });
        },

        /* Escape on a node closes the details panel and leaves focus and the
           selection exactly where they are. */
        closeRailFromNode() {
            if (!this.railOpen) return;
            this.railOpen = false;
            this.$nextTick(function () { if (controller) controller.resize(); });
        },

        async recomputeNow() {
            this.recomputing = true;
            this.recomputeLine = '';
            this.recomputeFailed = false;
            try {
                await Intelligence.recompute();
            } catch (err) {
                this.recomputing = false;
                if (Intelligence.failureStatus(err) === 409) {
                    this.recomputeLine = Intelligence.BUSY_LINE;
                } else {
                    this.recomputeLine = Intelligence.ERROR_LINE;
                    this.recomputeFailed = true;
                }
                return;
            }
            this.recomputing = false;
            await this.load({});
        }
    });
}
window.twinMapSurface = twinMapSurface;
