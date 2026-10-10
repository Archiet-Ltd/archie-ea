/**
 * CMP-032: Composer Search Module (IIFE)
 *
 * Search, discovery, and viewpoint selection:
 * - Element search/create modal
 * - Quick-add command palette
 * - Canvas search (Ctrl+F)
 * - Viewpoint selection and clearing
 *
 * Usage: ComposerSearch.install(ctx, helpers) in composerApp().init()
 */
let ComposerSearch = (function() {
    'use strict';

    function getMethods(helpers) {
        let csrfToken = helpers.csrfToken;
        let createNode = helpers.createNode;
        let guessLayer = helpers.guessLayer;
        let _toast = helpers._toast;
        // Platform.fetch will handle CSRF automatically; csrfToken helper may still be used elsewhere

        // CMP-058: extract helpers that selectViewpoint() requires at call time
        let UndoStack = helpers.UndoStack;
        let createLink = helpers.createLink;
        let applyLayerBanding = helpers.applyLayerBanding;

        let VIEWPOINT_PALETTE_MAP = helpers.VIEWPOINT_PALETTE_MAP;

        // Ensure Platform.fetch is available globally
        const Platform = window.Platform;

        let methods = {

        selectViewpoint: function(vpId, vpName, layer) {
            let self = this;
            self.vpDropdownOpen = false;
            self.activeViewpoint = vpId;
            self.activeViewpointName = vpName || vpId;
            self.mode = 'view';
            self.viewpointLoading = true;
            self.selectedNode = null;
            self.selectedEdge = null;
            self.scopeFallback = false;
            self._clearNeighborFocus();

            let url = '/archimate/viewpoints-api/' + vpId + '/data';
            if (self.solutionId) url += '?solution_id=' + self.solutionId;
            if (layer) url += (url.indexOf('?') === -1 ? '?' : '&') + 'layer=' + encodeURIComponent(layer);

            Platform.fetch(url, { silent: true })
            .then(function(data) {
                /* ── Invariant 1: Scope required ── */
                if (data.scope_required) {
                    UndoStack.pause();
                    self.graph.clear();
                    self.canvasElements = {};
                    self.elementCount = 0;
                    self.relCount = 0;
                    self.viewpointLoading = false;
                    self.scopeFallback = true;
                    self.statusText = 'Select a solution to view this viewpoint';
                    UndoStack.resume();
                    UndoStack.clear();
                    self.viewpointDirty = false;
                    return;
                }

                /* D4: distinguish a genuine backend failure from a real empty
                 * result — do not silently render "No elements for this
                 * viewpoint" for an error. */
                if (data.error) {
                    UndoStack.pause();
                    self.graph.clear();
                    self.canvasElements = {};
                    self.elementCount = 0;
                    self.relCount = 0;
                    self.viewpointLoading = false;
                    self.statusText = data.error_reason || 'Failed to load viewpoint data';
                    UndoStack.resume();
                    UndoStack.clear();
                    self.viewpointDirty = false;
                    _toast('error', data.error_reason || 'Failed to load viewpoint data');
                    return;
                }

                UndoStack.pause();
                self.graph.clear();
                self.canvasElements = {};

                let elements = data.elements || [];
                let relationships = data.relationships || [];
                self.scopeFallback = (self.solutionId && data.scope === 'enterprise');

                if (data.viewpoint_name) self.activeViewpointName = data.viewpoint_name;

                if (elements.length === 0) {
                    self.elementCount = 0;
                    self.relCount = 0;
                    self.statusText = 'No elements for this viewpoint';
                    self.viewpointLoading = false;
                    UndoStack.resume();
                    UndoStack.clear();
                    self.viewpointDirty = false;
                    return;
                }

                let cellMap = {};
                elements.forEach(function(el) {
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer, 0, 0);
                    self.graph.addCell(node);
                    cellMap[el.id] = node;
                    self.canvasElements[el.id] = el;
                });

                relationships.forEach(function(rel) {
                    let srcCell = cellMap[rel.source_id];
                    let tgtCell = cellMap[rel.target_id];
                    if (!srcCell || !tgtCell) return;
                    let link = createLink(srcCell, tgtCell, rel.type || 'association', rel.id);
                    self.graph.addCell(link);
                });

                /* Apply layer banding layout */
                applyLayerBanding(self.graph);

                self.elementCount = elements.length;
                self.relCount = relationships.length;
                self.viewpointLoading = false;
                self.statusText = self.activeViewpointName + ' — ' + elements.length + ' elements, ' + relationships.length + ' relationships';

                UndoStack.resume();
                UndoStack.clear();
                /* D1 fix: loading/viewing a viewpoint is not an edit — graph.on('add')
                 * fired above for each rendered element/relationship and marked the
                 * canvas dirty purely from viewing it, which would otherwise let the
                 * 30s autosave timer create a new SavedDiagram row for a user who
                 * only looked. Mirror the reset composer.js:2541 does after
                 * loadSavedViewpoint(). */
                self.viewpointDirty = false;
                self.$nextTick(function() {
                    self.fitCanvas();
                    if (window.lucide) lucide.createIcons();
                    if (self.intelligenceEnabled) self.fetchIntelligence();
                    /* GAP-CMP-002/003: Update validation badge + orphan highlights */
                    if (self._diagramChanged) self._diagramChanged();
                    self._selectInitialElement();
                });
            })
            .catch(function(err) {
                UndoStack.resume();
                _toast('error', 'Failed to switch viewpoint');
                // Platform.fetch already logged the error; we keep the inline error state
                self.viewpointLoading = false;
                self.elementCount = 0;
                self.relCount = 0;
                self.statusText = 'Error loading viewpoint';
            });
        },

        clearViewpoint: function() {
            this.vpDropdownOpen = false;
            this.activeViewpoint = null;
            this.activeViewpointName = '';
            this.scopeFallback = false;
            this.mode = 'edit';
            this._clearNeighborFocus();
            this.statusText = 'Free canvas — Edit mode';
            this.$nextTick(function() {
                if (window.lucide) lucide.createIcons();
            });
        },

        doSearch: function() {
            let self = this;
            let type = this.searchTypeFilter || '';
            let q = this.searchQuery.trim();
            let layerFilter = this.searchLayerFilter || '';

            self.searchLoading = true;
            // CMP-059: corrected to archimate-owned search API (stable across blueprint variants)
            let url = '/archimate/api/elements/search?type=' + encodeURIComponent(type) + '&limit=30';
            if (q) url += '&q=' + encodeURIComponent(q);
            if (layerFilter) url += '&layer=' + encodeURIComponent(layerFilter);
            if (self.solutionOnlyFilter && self.solutionId) url += '&solution_id=' + self.solutionId;

            Platform.fetch.get(url, null, { silent: true })
                .then(function(data) {
                    // Platform.fetch returns parsed data directly
                    let resp = data;
                    let dataArray = resp.data || resp || [];
                    self.searchResults = Array.isArray(dataArray) ? dataArray.filter(function(el) {
                        if (self.canvasElements[el.id]) return false;
                        /* Client-side layer filter fallback if API doesn't support it */
                        if (layerFilter && el.layer && el.layer.toLowerCase() !== layerFilter) return false;
                        return true;
                    }) : [];
                    self.searchLoading = false;
                })
                .catch(function() {
                    self.searchResults = [];
                    self.searchLoading = false;
                    _toast('error', 'Element search failed');
                });
        },

        createNewElement: function() {
            let name = (this.newElementName || '').trim();
            if (!name) return;

            let self = this;
            let type = this.searchTypeFilter || 'ApplicationComponent';
            let layer = guessLayer(type);

            self.closeSearch();
            self.statusText = 'Creating...';

            Platform.fetch.post('/api/architecture-assistant/create-element', { name: name, type: type, layer: layer }, { silent: true })
            .then(function(data) {
                let elem = data.element || data;
                if (elem.id) {
                    let el = { id: elem.id, name: elem.name || name, type: elem.type || type, layer: elem.layer || layer };
                    let node = createNode(el.id, el.name, el.type, el.layer, self.dropX, self.dropY);
                    self.graph.addCell(node);
                    self.canvasElements[el.id] = el;
                    self.elementCount++;
                    if (self.solutionId) self.linkElementToSolution(elem.id);
                    self.statusText = 'Created: ' + el.name;
                    self.refreshMaturityOverlay();
                    /* BUG-CMP-004: Check if dropped onto an existing element */
                    if (self._checkDropOverlap) self._checkDropOverlap(node);
                } else {
                    self.statusText = 'Create failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Error: ' + err.message; _toast('error', err.message || 'Operation failed'); });
        },

        openQuickAdd: function() {
            if (this.mode === 'view') return;
            this.quickAddOpen = true;
            this.quickAddQuery = '';
            this.quickAddResults = [];
            let self = this;
            this.$nextTick(function() {
                let el = document.getElementById('quick-add-input');
                if (el) el.focus();
            });
        },

        closeQuickAdd: function() {
            this.quickAddOpen = false;
            this.quickAddQuery = '';
            this.quickAddResults = [];
        },

        closeSearch: function() {
            this.searchOpen = false;
            this.searchQuery = '';
            this.searchResults = [];
            this.newElementName = '';
            this.similarElements = [];
            this.checkingReuse = false;
            this.dragType = null;
        },

        openCanvasSearch: function() {
            this.canvasSearchOpen = true;
            let self = this;
            this.$nextTick(function() {
                let el = document.getElementById('canvas-search-input');
                if (el) el.focus();
            });
        },

        closeCanvasSearch: function() {
            this._clearCanvasSearchHighlights();
            this.canvasSearchOpen = false;
            this.canvasSearchQuery = '';
            this.canvasSearchMatches = [];
            this.canvasSearchIdx = 0;
        },

        doCanvasSearch: function() {
            this._clearCanvasSearchHighlights();
            this.canvasSearchMatches = [];
            this.canvasSearchIdx = 0;
            let q = this.canvasSearchQuery.trim().toLowerCase();
            if (!q) return;
            let self = this;
            this.graph.getElements().forEach(function(cell) {
                let name = ((cell.get('elName') || cell.get('name') || '')).toLowerCase();
                if (name.indexOf(q) !== -1) {
                    self.canvasSearchMatches.push(cell);
                }
            });
            if (this.canvasSearchMatches.length > 0) {
                this._panToCanvasSearchMatch(0);
            }
        },

        /* ── CMP-057: Search and Replace ── */
        openSearchReplace: function() {
            this.searchReplaceOpen = true;
            this.srFindText = '';
            this.srReplaceText = '';
            this.srCaseSensitive = false;
            this.srUseRegex = false;
            this.srMatches = [];
            this.srCurrentIndex = -1;
        },

        closeSearchReplace: function() {
            this.searchReplaceOpen = false;
            this._clearSrHighlights();
            this.srMatches = [];
        },

        srFindMatches: function() {
            let self = this;
            self._clearSrHighlights();
            self.srMatches = [];
            self.srCurrentIndex = -1;
            self.srRegexError = null;

            let query = self.srFindText;
            if (!query) return;

            let flags = self.srCaseSensitive ? '' : 'i';
            let re;
            try {
                re = self.srUseRegex ? new RegExp(query, flags) : new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags);
            } catch(e) {
                /* Returning here left srMatches empty, and the panel renders that
                   as "0 / 0" — the identical answer a valid pattern that matched
                   nothing gives. The user could not tell a typo in their regex
                   from a diagram that does not contain what they searched for. */
                self.srRegexError = 'Invalid regular expression: ' + (e.message || query);
                return;
            }

            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let name = cell.get('elName') || '';
                if (re.test(name)) {
                    self.srMatches.push(cell);
                }
            });

            /* Highlight all matches */
            self.srMatches.forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) {
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 5, rx: 8, attrs: { stroke: '#8b5cf6', 'stroke-width': 3 } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic search-match outline; the match count and jump-to-match still work without it */ }
                }
            });

            if (self.srMatches.length > 0) {
                self.srCurrentIndex = 0;
                self._srScrollToMatch(0);
            }
        },

        _clearSrHighlights: function() {
            let self = this;
            (self.srMatches || []).forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) {
                    try { view.unhighlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 8, attrs: { stroke: '#8b5cf6', 'stroke-width': 3 } } } }); } catch(e) { /* swallow-ok: cosmetic un-highlight when clearing search matches */ }
                }
            });
        },

        _srScrollToMatch: function(idx) {
            let self = this;
            if (idx < 0 || idx >= self.srMatches.length) return;
            let cell = self.srMatches[idx];
            let pos = cell.position();
            self.paper.translate(-pos.x + 300, -pos.y + 200);
        },

        srReplaceNext: function() {
            let self = this;
            if (self.srMatches.length === 0 || self.srCurrentIndex < 0) return;

            let cell = self.srMatches[self.srCurrentIndex];
            let oldName = cell.get('elName') || '';
            let query = self.srFindText;
            let flags = self.srCaseSensitive ? '' : 'i';
            let re;
            try {
                re = self.srUseRegex ? new RegExp(query, flags + 'g') : new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags + 'g');
            } catch(e) {
                /* The button press did nothing at all and said nothing at all. */
                self.srRegexError = 'Invalid regular expression: ' + (e.message || query);
                _toast('error', self.srRegexError);
                return;
            }

            let newName = oldName.replace(re, self.srReplaceText);
            cell.set('elName', newName);
            cell.attr('label/text', newName);

            /* Push undo */
            if (typeof UndoStack !== 'undefined' && UndoStack.push) {
                UndoStack.push({
                    undo: function() { cell.set('elName', oldName); cell.attr('label/text', oldName); },
                    redo: function() { cell.set('elName', newName); cell.attr('label/text', newName); },
                });
            }

            /* Remove from matches and advance */
            self.srMatches.splice(self.srCurrentIndex, 1);
            if (self.srCurrentIndex >= self.srMatches.length) self.srCurrentIndex = 0;
            if (self.srMatches.length > 0) self._srScrollToMatch(self.srCurrentIndex);

            self.viewpointDirty = true;
        },

        srReplaceAll: function() {
            let self = this;
            if (self.srMatches.length === 0) return;

            let query = self.srFindText;
            let flags = self.srCaseSensitive ? '' : 'i';
            let re;
            try {
                re = self.srUseRegex ? new RegExp(query, flags + 'g') : new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), flags + 'g');
            } catch(e) {
                /* The button press did nothing at all and said nothing at all. */
                self.srRegexError = 'Invalid regular expression: ' + (e.message || query);
                _toast('error', self.srRegexError);
                return;
            }

            let count = self.srMatches.length;
            /* Save all old names for undo */
            let oldNames = self.srMatches.map(function(c) { return { cell: c, name: c.get('elName') || '' }; });

            self.srMatches.forEach(function(cell) {
                let oldName = cell.get('elName') || '';
                let newName = oldName.replace(re, self.srReplaceText);
                cell.set('elName', newName);
                cell.attr('label/text', newName);
            });

            /* Push single undo for bulk replace */
            if (typeof UndoStack !== 'undefined' && UndoStack.push) {
                UndoStack.push({
                    undo: function() {
                        oldNames.forEach(function(o) { o.cell.set('elName', o.name); o.cell.attr('label/text', o.name); });
                    },
                    redo: function() {
                        oldNames.forEach(function(o) {
                            let n = o.name.replace(re, self.srReplaceText);
                            o.cell.set('elName', n); o.cell.attr('label/text', n);
                        });
                    },
                });
            }

            self._clearSrHighlights();
            self.srMatches = [];
            self.srCurrentIndex = -1;
            self.viewpointDirty = true;
            self.statusText = 'Replaced ' + count + ' matches';
        },

        /* ── CMP-041: Matrix View ──────────────────────────────────────── */

        relMatrixOpen: false,
        matrixViewOpen: false,
        matrixRowType: 'BusinessProcess',
        matrixColType: 'ApplicationComponent',
        matrixRows: [],
        matrixCols: [],
        matrixCells: {},
        matrixLoading: false,

        toggleRelMatrix: function() {
            this.relMatrixOpen = !this.relMatrixOpen;
            this.matrixViewOpen = this.relMatrixOpen;
            if (this.matrixViewOpen) this.fetchMatrix();
        },

        fetchMatrix: function() {
            let self = this;
            self.matrixLoading = true;
            let url = '/archimate/api/matrix?row_type=' + encodeURIComponent(self.matrixRowType)
                    + '&col_type=' + encodeURIComponent(self.matrixColType);
            if (self.solutionId) url += '&solution_id=' + self.solutionId;
            Platform.fetch.get(url, null, { silent: true })
            .then(function(data) {
                self.matrixRows = data.rows || [];
                self.matrixCols = data.columns || [];
                self.matrixCells = data.intersections || {};
                self.matrixLoading = false;
            })
            .catch(function() {
                self.matrixLoading = false;
                _toast('error', 'Failed to load matrix data');
            });
        },

        matrixCellType: function(rowId, colId) {
            return this.matrixCells[rowId + '_' + colId] || null;
        },

        matrixCellAbbrev: function(rowId, colId) {
            let t = this.matrixCells[rowId + '_' + colId];
            if (!t) return '';
            // First letter of each word: AssociationRelationship → AR
            return t.replace(/Relationship$/, '').split(/(?=[A-Z])/).map(function(w) { return w[0]; }).join('');
        },

        matrixCellClick: function(rowId, colId) {
            let self = this;
            let existingType = self.matrixCells[rowId + '_' + colId];
            if (existingType) {
                _toast('info', existingType + ' relationship exists');
                return;
            }
            // Navigate both elements onto canvas via search
            _toast('info', 'Use element search (Ctrl+K) to add and connect these elements');
        },

        exportMatrixCsv: function() {
            let self = this;
            if (!self.matrixRows.length) { _toast('warning', 'No matrix data to export'); return; }
            let lines = [[''].concat(self.matrixCols.map(function(c) { return '"' + c.name + '"'; })).join(',')];
            self.matrixRows.forEach(function(row) {
                let cells = self.matrixCols.map(function(col) {
                    return '"' + (self.matrixCells[row.id + '_' + col.id] || '') + '"';
                });
                lines.push(['"' + row.name + '"'].concat(cells).join(','));
            });
            let blob = new Blob([lines.join('\n')], { type: 'text/csv' });
            let a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'archimate_matrix_' + self.matrixRowType + '_x_' + self.matrixColType + '.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
        },

        };

        return methods;
    }

    return { getMethods: getMethods };
})();
