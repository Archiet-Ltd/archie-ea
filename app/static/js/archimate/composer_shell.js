/**
 * Composer editor shell: the state and actions behind components/diagram/*.
 *
 * The toolbar, the element palette, the finder, the legend, the status line and the
 * properties sheet are template components. Every action here calls the one engine
 * (ComposerRenderer through composerApp); nothing in this file draws.
 *
 * Usage: ComposerShell.getMethods(helpers) is merged into composerApp().
 */
let ComposerShell = (function() {
    'use strict';

    /* The 60 ArchiMate 3.2 element types in the eight groups the standard teaches. */
    let GROUPS = [
        { key: 'strategy', label: 'Strategy', layer: 'strategy',
          types: ['Resource', 'Capability', 'ValueStream', 'CourseOfAction'] },
        { key: 'business', label: 'Business', layer: 'business',
          types: ['BusinessActor', 'BusinessRole', 'BusinessCollaboration', 'BusinessInterface', 'BusinessProcess',
                  'BusinessFunction', 'BusinessInteraction', 'BusinessEvent', 'BusinessService', 'BusinessObject',
                  'Contract', 'Representation', 'Product'] },
        { key: 'application', label: 'Application', layer: 'application',
          types: ['ApplicationComponent', 'ApplicationCollaboration', 'ApplicationInterface', 'ApplicationFunction',
                  'ApplicationInteraction', 'ApplicationProcess', 'ApplicationEvent', 'ApplicationService', 'DataObject'] },
        { key: 'technology', label: 'Technology', layer: 'technology',
          types: ['Node', 'Device', 'SystemSoftware', 'TechnologyCollaboration', 'TechnologyInterface', 'Path',
                  'CommunicationNetwork', 'TechnologyFunction', 'TechnologyProcess', 'TechnologyInteraction',
                  'TechnologyEvent', 'TechnologyService', 'Artifact'] },
        { key: 'physical', label: 'Physical', layer: 'physical',
          types: ['Equipment', 'Facility', 'DistributionNetwork', 'Material'] },
        { key: 'motivation', label: 'Motivation', layer: 'motivation',
          types: ['Stakeholder', 'Driver', 'Assessment', 'Goal', 'Outcome', 'Principle', 'Requirement', 'Constraint',
                  'Meaning', 'Value'] },
        { key: 'implementation', label: 'Implementation and Migration', layer: 'implementation',
          types: ['WorkPackage', 'Deliverable', 'ImplementationEvent', 'Plateau', 'Gap'] },
        { key: 'other', label: 'Other', layer: 'other',
          types: ['Grouping', 'Location'] },
    ];

    /* Relationship names as a person reads them, keyed by the relType the engine stores. */
    let RELATION_LABELS = {
        composition: 'Composition', aggregation: 'Aggregation', assignment: 'Assignment',
        realization: 'Realization', serving: 'Serving', access: 'Access', influence: 'Influence',
        triggering: 'Triggering', flow: 'Flow', specialization: 'Specialization', association: 'Association',
    };

    function humanise(type) { return String(type || '').replace(/([a-z])([A-Z])/g, '$1 $2'); }

    function isDrawn(cell) {
        return !!cell.get('elementId') && !cell.get('isLayerZone') && !cell.get('isAnnotation') && !cell.get('isNote');
    }

    function getMethods(helpers) {
        let REL_STYLES = helpers.REL_STYLES;
        let markerPath = helpers.markerPath;
        let markerFill = helpers.markerFill;
        let UndoStack = helpers.UndoStack;
        let PALETTE = helpers.PALETTE;

        return {
            /* ── state ─────────────────────────────────────── */
            shellRailOpen: true,
            shellRailTab: 'add',
            shellPan: false,
            shellOpenGroups: { business: true, application: true, technology: true },
            finderFilter: '',
            finderItems: [],
            shellElementTotal: 0,
            shellRelTotal: 0,
            legendTypes: [],
            canUndoNow: false,
            canRedoNow: false,
            connectorStyle: 'manhattan',

            /* ── init: called once from composerApp().init() ─ */
            shellInit: function() {
                let self = this;
                /* The rail starts open on a desktop for building; it starts closed on a phone, and when the page
                   opens as a read-only look at one element's neighbourhood, where the room goes to the drawing. */
                self.shellRailOpen = window.matchMedia('(min-width: 768px)').matches
                    && !(window.__COMPOSER_CONFIG__ || {}).focusElement;
                UndoStack.onChange = function() {
                    self.canUndoNow = UndoStack.canUndo();
                    self.canRedoNow = UndoStack.canRedo();
                };
                let refresh = function() { self.shellRefresh(); };
                self.graph.on('add remove reset change:elName change:relType', function() { clearTimeout(self._shellTimer); self._shellTimer = setTimeout(refresh, 60); });
                self.graph.on('add', function(cell) {
                    if (cell.isLink() && self.connectorStyle !== 'manhattan' && cell.get('routingStyle') === 'manhattan') {
                        self.shellRoute(cell, self.connectorStyle);
                    }
                });
            },

            /* The finder and the legend read the graph, which Alpine cannot watch, so they are
               rebuilt here whenever a cell is added, removed or renamed. */
            shellRefresh: function() {
                let self = this;
                if (!self.graph) return;
                let items = self.graph.getElements().filter(isDrawn).map(function(c) {
                    return {
                        id: c.id,
                        name: c.get('elName') || 'Unnamed element',
                        type: humanise(c.get('elType')),
                        elType: c.get('elType'),
                        layer: (c.get('elLayer') || 'other').toLowerCase(),
                    };
                });
                items.sort(function(a, b) { return a.name.localeCompare(b.name); });
                self.finderItems = items;
                /* The status line counts what the graph holds. The page's own elementCount and relCount are
                   adjusted by hand in many places (the relationship picker adds the line first and counts it later),
                   which left the status line behind the diagram. */
                self.shellElementTotal = items.length;
                self.shellRelTotal = self.graph.getLinks().length;
                let seen = {};
                self.graph.getLinks().forEach(function(l) {
                    let t = (l.get('relType') || '').toLowerCase();
                    if (t) seen[t] = true;
                });
                self.legendTypes = Object.keys(seen).sort().map(function(t) {
                    return { type: t, label: RELATION_LABELS[t] || humanise(t) };
                });
            },

            /* ── palette ───────────────────────────────────── */
            get shellGroups() {
                let q = (this.paletteFilter || '').trim().toLowerCase();
                let labels = {};
                Object.keys(PALETTE).forEach(function(layer) {
                    PALETTE[layer].forEach(function(t) { labels[t.type] = t.label; });
                });
                return GROUPS.map(function(g) {
                    let items = g.types.map(function(t) {
                        return { type: t, label: humanise(t), short: labels[t] || humanise(t), layer: g.layer };
                    });
                    if (q) {
                        items = items.filter(function(i) {
                            return i.label.toLowerCase().indexOf(q) !== -1 || i.type.toLowerCase().indexOf(q) !== -1;
                        });
                    }
                    return { key: g.key, label: g.label, layer: g.layer, count: items.length, total: g.types.length, items: items };
                }).filter(function(g) { return g.count > 0; });
            },
            get shellTypeTotal() {
                return GROUPS.reduce(function(n, g) { return n + g.types.length; }, 0);
            },
            shellGroupIsOpen: function(key) {
                return !!(this.paletteFilter || '').trim() || !!this.shellOpenGroups[key];
            },
            shellToggleGroup: function(key) {
                let next = Object.assign({}, this.shellOpenGroups);
                next[key] = !next[key];
                this.shellOpenGroups = next;
            },
            shellAddType: function(item) {
                if (this.mode === 'view') return;
                let canvas = document.getElementById('composer-canvas');
                let rect = canvas.getBoundingClientRect();
                let s = this.paper.scale();
                let t = this.paper.translate();
                let jitter = (this.finderItems.length % 5) * 24;
                this._beginAddAt(item, (rect.width / 2 - t.tx) / s.sx + jitter, (rect.height / 2 - t.ty) / s.sy + jitter);
            },

            /* The view's name: the open saved diagram, else the viewpoint, else untitled. */
            get viewTitle() {
                let tab = (this.viewpointTabs || []).filter(function(t) { return t.id === this.activeTabId; }, this)[0];
                return (tab && tab.name) || (this.focusView && this.focusView.name) || this.activeViewpointName || 'Untitled view';
            },

            /* ── history: placing an element and drawing a relationship can be undone and redone ── */
            shellRecordPlacement: function(node) {
                let self = this;
                UndoStack.push({
                    /* Undo takes the element off this view; it stays in the model, as Delete does. */
                    undo: function() { node.remove(); self.statusText = 'Removed from this view: ' + (node.get('elName') || 'element'); },
                    redo: function() { self.graph.addCell(node); self.statusText = 'Put back on this view: ' + (node.get('elName') || 'element'); },
                });
            },
            shellRecordRelationship: function(link, payload) {
                let self = this;
                UndoStack.push({
                    /* A relationship is a model record, so undo deletes it and redo records it again. */
                    undo: function() {
                        let relId = link.get('relId');
                        link.remove();
                        if (relId) {
                            window.Platform.fetch.delete('/archimate/api/relationships/' + relId, { silent: true })
                                .catch(function() { helpers._toast('error', 'The relationship was removed here but not in the model; it may reappear on reload.'); });
                        }
                        self.statusText = 'Relationship undone';
                    },
                    redo: function() {
                        window.Platform.fetch.post('/archimate/api/relationships', payload, { silent: true })
                            .then(function(data) {
                                if (!data || !data.id) throw new Error((data && data.error) || 'not recorded');
                                link.set('relId', data.id);
                                self.graph.addCell(link);
                                self.statusText = 'Relationship restored';
                            })
                            .catch(function(err) { helpers._toast('error', 'Could not restore the relationship: ' + ((err && err.message) || err)); });
                    },
                });
            },

            /* ── tools ─────────────────────────────────────── */
            get shellTool() {
                return this.connectModeActive ? 'connect' : (this.shellPan ? 'pan' : 'select');
            },
            shellSetTool: function(tool) {
                if (this.mode === 'view' && tool === 'connect') return;
                if (tool === 'connect') {
                    this.shellPan = false;
                    if (!this.connectModeActive) this.toggleConnectMode();
                    return;
                }
                if (this.connectModeActive) this.toggleConnectMode();
                this.shellPan = tool === 'pan';
                if (this.paper) this.paper.el.style.cursor = this.shellPan ? 'grab' : '';
            },

            /* ── connector style: straight, right-angled, curved ── */
            shellRoute: function(link, style) {
                if (style === 'manhattan') {
                    link.router('manhattan', { step: 12, padding: 36 });
                    link.connector('rounded', { radius: 8 });
                } else if (style === 'smooth') {
                    link.router('normal');
                    link.connector('smooth');
                } else {
                    link.router('normal');
                    link.connector('normal');
                }
                link.set('routingStyle', style);
            },
            shellSetConnector: function(style) {
                let self = this;
                self.connectorStyle = style;
                self.defaultRouting = style;
                self.graph.getLinks().forEach(function(l) { self.shellRoute(l, style); });
                self.statusText = {
                    straight: 'Straight lines', manhattan: 'Right-angled lines', smooth: 'Curved lines',
                }[style] || 'Lines changed';
            },

            /* ── selection ─────────────────────────────────── */
            get hasSelection() {
                return this.mode === 'edit' && this._selectedCells.length > 0;
            },
            get hasProperties() {
                return !!(this.selectedNode || this.selectedEdge);
            },
            shellOpenProperties: function() {
                if (!this.hasProperties) return;
                window.openDrawer('composer-properties');
            },
            get selectedRelations() {
                let self = this;
                let node = self.selectedNode;
                if (!node || !self.graph) return [];
                let cell = self.graph.getElements().filter(function(c) {
                    return String(c.get('elementId')) === String(node.elementId);
                })[0];
                if (!cell) return [];
                return self.graph.getConnectedLinks(cell).map(function(l) {
                    let outgoing = (l.get('source') || {}).id === cell.id;
                    let other = self.graph.getCell(outgoing ? (l.get('target') || {}).id : (l.get('source') || {}).id);
                    let t = (l.get('relType') || 'association').toLowerCase();
                    return {
                        key: l.id,
                        label: RELATION_LABELS[t] || humanise(t),
                        direction: outgoing ? 'to' : 'from',
                        other: other ? (other.get('elName') || 'Unnamed element') : 'an element no longer in this view',
                    };
                });
            },

            /* ── finder ────────────────────────────────────── */
            get finderRows() {
                let q = (this.finderFilter || '').trim().toLowerCase();
                if (!q) return this.finderItems;
                return this.finderItems.filter(function(i) {
                    return i.name.toLowerCase().indexOf(q) !== -1 || i.type.toLowerCase().indexOf(q) !== -1;
                });
            },
            shellFocus: function(id) {
                let self = this;
                let cell = self.graph.getCell(id);
                if (!cell) return;
                let view = self.paper.findViewByModel(cell);
                if (view) self.paper.trigger('element:pointerclick', view, {});
                /* Selecting opens the detail panel, which narrows the canvas: centre once that layout has settled. */
                self.$nextTick(function() {
                    window.requestAnimationFrame(function() {
                        let box = cell.getBBox();
                        let canvas = document.getElementById('composer-canvas');
                        let s = self.paper.scale();
                        self.paper.translate(
                            canvas.clientWidth / 2 - box.center().x * s.sx,
                            canvas.clientHeight / 2 - box.center().y * s.sy
                        );
                        self._scheduleMiniMapUpdate();
                    });
                });
            },

            /* ── legend glyph: the line as the engine draws it ── */
            relGlyph: function(type) {
                let style = REL_STYLES[type] || REL_STYLES.association;
                let dash = style.strokeDasharray ? ' stroke-dasharray="' + style.strokeDasharray + '"' : '';
                let end = markerPath(style.targetMarker);
                let start = style.sourceMarker ? markerPath(style.sourceMarker) : '';
                let hollow = markerFill(style.targetMarker, style.stroke) !== style.stroke;
                return '<svg width="44" height="14" viewBox="0 0 44 14" fill="none" stroke="currentColor" stroke-width="1.25" aria-hidden="true">' +
                    '<path d="M4 7H38"' + dash + '/>' +
                    (end ? '<path transform="translate(38 7) rotate(180)" d="' + end + '" fill="' + (hollow ? 'none' : 'currentColor') + '"/>' : '') +
                    (start ? '<path transform="translate(4 7)" d="' + start + '" fill="currentColor"/>' : '') +
                    '</svg>';
            },
        };
    }

    return { getMethods: getMethods, GROUPS: GROUPS };
})();
