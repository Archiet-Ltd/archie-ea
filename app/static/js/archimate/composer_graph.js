/**
 * CMP-032: Composer Graph Manipulation Module (IIFE)
 *
 * Core graph operations: relationship CRUD, context menus, alignment,
 * copy/paste, nesting, rendering toggles, layout algorithms.
 *
 * Usage: ComposerGraph.install(ctx, helpers) in composerApp().init()
 */
let ComposerGraph = (function() {
    'use strict';

    function getMethods(helpers) {
        let csrfToken = helpers.csrfToken;
        let createNode = helpers.createNode;
        let createLink = helpers.createLink;
        let layerColor = helpers.layerColor;
        let guessLayer = helpers.guessLayer;
        let REL_STYLES = helpers.REL_STYLES;
        let markerPath = helpers.markerPath;
        let markerFill = helpers.markerFill;
        let _toast = helpers._toast;

        let UndoStack = helpers.UndoStack;
        let isContainerType = helpers.isContainerType;
        let getNestingDepth = helpers.getNestingDepth;
        let applyLayerBanding = helpers.applyLayerBanding;
        let createLayerZone = helpers.createLayerZone;

        let methods = {

        createRelationship: function(relType) {
            let self = this;

            /* Association warning — suggest more specific type (inline prompt) */
            if (relType === 'association' && !self._associationConfirmed && self.relPickerTypes.length > 1) {
                let hasSpecific = self.relPickerTypes.some(function(rt) {
                    let t = rt.type || rt;
                    return t !== 'association' && (rt.tier === 'standard' || !rt.tier);
                });
                if (hasSpecific) {
                    self.associationWarning = true;
                    return;
                }
            }
            self._associationConfirmed = false;
            self.associationWarning = false;

            self.relPickerOpen = false;

            let pendingLink = self._pendingLink;
            if (!pendingLink) return;

            /* Change-type mode: restyle existing link without creating a new relationship */
            if (self._isChangeType) {
                self._isChangeType = false;
                let style = REL_STYLES[relType] || REL_STYLES.association;
                let mp = markerPath(style.targetMarker);
                let mf = markerFill(style.targetMarker, style.stroke);
                pendingLink.attr('line/stroke', style.stroke);
                pendingLink.attr('line/strokeWidth', style.strokeWidth);
                pendingLink.attr('line/strokeDasharray', style.strokeDasharray || '');
                if (mp) {
                    pendingLink.attr('line/targetMarker', { type: 'path', d: mp, fill: mf, stroke: style.stroke, strokeWidth: 1 });
                } else {
                    pendingLink.attr('line/targetMarker', { type: 'path', d: '' });
                }
                if (style.sourceMarker) {
                    let smpC = markerPath(style.sourceMarker);
                    let smfC = markerFill(style.sourceMarker, style.stroke);
                    if (smpC) pendingLink.attr('line/sourceMarker', { type: 'path', d: smpC, fill: smfC, stroke: style.stroke, strokeWidth: 1 });
                } else {
                    pendingLink.removeAttr('line/sourceMarker');
                }
                let changeLabelText = relType;
                if (relType === 'access' && self.accessMode) {
                    changeLabelText = 'access (' + self.accessMode + ')';
                    pendingLink.set('accessMode', self.accessMode);
                }
                if (relType === 'flow' && self.flowLabel) {
                    changeLabelText = self.flowLabel;
                    pendingLink.set('flowLabel', self.flowLabel);
                }
                pendingLink.label(0, {
                    attrs: {
                        text: { text: changeLabelText, fontSize: 10, fontWeight: 500, fill: '#64748b' },
                        rect: { fill: '#fff', stroke: '#e2e8f0', rx: 3, ry: 3 },
                    },
                    position: { distance: 0.5, offset: -12 },
                });
                pendingLink.set('relType', relType);
                if (self.selectedEdge) {
                    self.selectedEdge.relType = relType;
                    self.selectedEdge.accessMode = relType === 'access' ? self.accessMode : '';
                }
                self.statusText = 'Type changed to: ' + relType;
                self._pendingLink = null;
                self.accessMode = 'readwrite';
                self.flowLabel = '';
                return;
            }

            Platform.fetch.post('/archimate/api/relationships', {
                source_element_id: self.relPickerSourceId,
                target_element_id: self.relPickerTargetId,
                relationship_type: relType,
                solution_id: self.solutionId || null,
                access_mode: relType === 'access' ? self.accessMode : undefined,
                flow_label: relType === 'flow' ? self.flowLabel : undefined,
                description: pendingLink.get('description') || undefined,
                custom_label: pendingLink.get('customLabel') || undefined,
            }, { silent: true })
                .then(function(data) {
                if (data.id) {
                    let style = REL_STYLES[relType] || REL_STYLES.association;
                    let mp = markerPath(style.targetMarker);
                    let mf = markerFill(style.targetMarker, style.stroke);
                    pendingLink.attr('line/stroke', style.stroke);
                    pendingLink.attr('line/strokeWidth', style.strokeWidth);
                    pendingLink.attr('line/strokeDasharray', style.strokeDasharray || '');
                    if (mp) {
                        pendingLink.attr('line/targetMarker', { type: 'path', d: mp, fill: mf, stroke: style.stroke, strokeWidth: 1 });
                    }
                    if (style.sourceMarker) {
                        let smp = markerPath(style.sourceMarker);
                        let smf = markerFill(style.sourceMarker, style.stroke);
                        if (smp) pendingLink.attr('line/sourceMarker', { type: 'path', d: smp, fill: smf, stroke: style.stroke, strokeWidth: 1 });
                    }
                    /* Build label text based on relationship type */
                    let labelText = relType;
                    if (relType === 'access' && self.accessMode) {
                        labelText = 'access (' + self.accessMode + ')';
                        pendingLink.set('accessMode', self.accessMode);
                    }
                    if (relType === 'flow' && self.flowLabel) {
                        labelText = self.flowLabel;
                        pendingLink.set('flowLabel', self.flowLabel);
                    }

                    pendingLink.label(0, {
                        attrs: {
                            text: { text: labelText, fontSize: 10, fontWeight: 500, fill: '#64748b' },
                            rect: { fill: '#fff', stroke: '#e2e8f0', rx: 3, ry: 3 },
                        },
                        position: { distance: 0.5, offset: -12 },
                    });
                    pendingLink.set('relType', relType);
                    pendingLink.set('relId', data.id);
                    self.relCount++;
                    self.statusText = relType + ' relationship created';
                    let srcName = self.relPickerSourceCell ? self.relPickerSourceCell.get('elName') : '';
                    let tgtName = self.relPickerTargetCell ? self.relPickerTargetCell.get('elName') : '';
                    self.logAuditEvent('relationship_added', 'relationship', data.id, relType, null, srcName + ' → ' + tgtName);

                    /* Check for practitioner mistake warnings */
                    let warning = self._checkPractitionerWarning(
                        relType,
                        self.relPickerSourceCell ? self.relPickerSourceCell.get('elType') : '',
                        self.relPickerTargetCell ? self.relPickerTargetCell.get('elType') : '',
                        self.relPickerSourceCell ? self.relPickerSourceCell.get('elLayer') : '',
                        self.relPickerTargetCell ? self.relPickerTargetCell.get('elLayer') : ''
                    );
                    if (warning) {
                        self.statusText = '\u26a0\ufe0f ' + warning;
                    }
                    /* GAP-CMP-002/003: Update validation badge + orphan highlights */
                    if (self._diagramChanged) self._diagramChanged();
                } else {
                    pendingLink.remove();
                    self.statusText = data.error || 'Relationship failed';
                }
            })
            .catch(function(err) {
                /* The metamodel refused this type for this pair: keep the
                   picker open on the same two elements, say why, and name the
                   types that are allowed so the person can pick one of them. */
                let refusal = err && err.status === 400 && err.data && Array.isArray(err.data.valid_types)
                    ? err.data : null;
                if (refusal) {
                    self._pendingLink = pendingLink;
                    self.relPickerRejection = {
                        message: refusal.error || ('ArchiMate 3.2 does not allow ' + relType + ' here'),
                        validTypes: refusal.valid_types,
                    };
                    self.relPickerOpen = true;
                    self.statusText = 'Not allowed: ' + relType;
                    _toast('error', self.relPickerRejection.message);
                    return;
                }
                pendingLink.remove();
                _toast('error', 'Failed to create relationship: ' + (err.message || err));
                self.statusText = 'Error: ' + err.message;
            });

            self.relPickerRejection = null;
            self._pendingLink = null;
            self.accessMode = 'readwrite';
            self.flowLabel = '';
        },

        viewRelProperties: function() {
            let link = this.relCtxMenuLink;
            this.relCtxMenuOpen = false;
            this.relCtxMenuLink = null;
            if (!link) return;
            let srcCell = this.graph.getCell(link.get('source').id);
            let tgtCell = this.graph.getCell(link.get('target').id);
            if (this.selectedLink && this.selectedLink !== link) {
                this._unhighlightLink(this.selectedLink);
            }
            this.selectedLink = link;
            this._highlightLink(link);
            this.selectedNode = null;
            this.selectedEdge = {
                relType: link.get('relType') || 'association',
                relId: link.get('relId') || null,
                source: srcCell ? (srcCell.get('elName') || '?') : '?',
                target: tgtCell ? (tgtCell.get('elName') || '?') : '?',
                sourceId: srcCell ? srcCell.get('elementId') : null,
                targetId: tgtCell ? tgtCell.get('elementId') : null,
                routingStyle: link.get('routingStyle') || 'manhattan',
                accessMode: link.get('accessMode') || '',
                flowLabel: link.get('flowLabel') || '',
                customLabel: link.get('customLabel') || '',
                description: link.get('description') || '',
            };
            let self = this;
            this.$nextTick(function() {
                if (window.lucide) lucide.createIcons();
            });
        },

        closeRelCtxMenu: function() {
            this.relCtxMenuOpen = false;
            this.relCtxMenuLink = null;
        },

        /* Reposition a context menu so it never escapes the viewport.
           Call inside $nextTick after the menu is visible and measurable.
           Prefers: right of cursor, below cursor.
           Flips:   left of cursor if near right edge, above cursor if near bottom edge.
           Always clamps to a PAD-pixel margin from every viewport edge. */
        _fitCtxMenu: function(menuEl, cursorX, cursorY) {
            let PAD = 8;
            let W = window.innerWidth;
            let H = window.innerHeight;
            let r = menuEl.getBoundingClientRect();
            let w = r.width  || menuEl.offsetWidth;
            let h = r.height || menuEl.offsetHeight;
            let x = (cursorX + w + PAD > W) ? cursorX - w : cursorX;
            let y = (cursorY + h + PAD > H) ? cursorY - h : cursorY;
            x = Math.max(PAD, Math.min(x, W - w - PAD));
            y = Math.max(PAD, Math.min(y, H - h - PAD));
            return { x: x, y: y };
        },

        deleteRelationship: async function() {
            this.relCtxMenuOpen = false;
            let link = this.relCtxMenuLink;
            if (!link) return;

            let self = this;
            let srcCell = self.graph.getCell((link.get('source') || {}).id);
            let tgtCell = self.graph.getCell((link.get('target') || {}).id);
            let srcName = srcCell ? srcCell.get('elName') : '?';
            let tgtName = tgtCell ? tgtCell.get('elName') : '?';

            let warnings = [];
            if (srcCell && self.graph.getConnectedLinks(srcCell).length <= 1) {
                warnings.push('"' + srcName + '" will have no connections');
            }
            if (tgtCell && self.graph.getConnectedLinks(tgtCell).length <= 1) {
                warnings.push('"' + tgtName + '" will have no connections');
            }

            let msg = 'Delete relationship: ' + (link.get('relType') || '') + '\n' + srcName + ' \u2192 ' + tgtName;
            if (warnings.length > 0) {
                msg += '\n\nWarning: ' + warnings.join('; ');
            }
            if (!(await Platform.modal.confirm(msg))) return;

            let relId = link.get('relId');
            let relType = link.get('relType') || '';
            link.remove();
            self.relCount = Math.max(0, self.relCount - 1);
            self.selectedEdge = null;
            self.selectedLink = null;
            self.statusText = 'Relationship deleted';
            self.logAuditEvent('relationship_removed', 'relationship', relId, relType, srcName + ' → ' + tgtName, null);

            if (relId) {
                Platform.fetch.delete('/archimate/api/relationships/' + relId, { silent: true })
                    .catch(function(err) {
                        /* The link is already gone from the canvas — the user needs to know the
                           delete did NOT persist, or it will silently reappear on next reload. */
                        _toast('error', 'Relationship removed on canvas but not saved — it may reappear on reload');
                    });
            }
        },

        reverseRelationship: function() {
            this.relCtxMenuOpen = false;
            let link = this.relCtxMenuLink;
            if (!link) return;

            let src = link.get('source');
            let tgt = link.get('target');
            link.set('source', tgt);
            link.set('target', src);
            this.statusText = 'Relationship reversed';
        },

        toggleRelRouting: function() {
            this.relCtxMenuOpen = false;
            let link = this.relCtxMenuLink || this.selectedLink;
            if (!link) return;

            let currentRouter = (link.get('router') || {}).name || 'manhattan';
            if (currentRouter === 'manhattan') {
                link.router('smooth');
                link.connector('rounded', { radius: 20 });
                this.statusText = 'Routing: smooth';
            } else {
                link.router('manhattan', { step: 12, padding: 36 });
                link.connector('rounded', { radius: 8 });
                this.statusText = 'Routing: manhattan';
            }

            if (this.selectedEdge) {
                this.selectedEdge.routingStyle = currentRouter === 'manhattan' ? 'smooth' : 'manhattan';
            }
        },

        _highlightLink: function(link) {
            let view = this.paper.findViewByModel(link);
            if (!view || !view.el) return;
            let line = view.el.querySelector('[joint-selector="line"]');
            if (line) {
                line.setAttribute('data-original-stroke', line.getAttribute('stroke') || '#64748b');
                line.setAttribute('data-original-stroke-width', line.getAttribute('stroke-width') || '1.5');
                line.setAttribute('stroke', '#3b82f6');
                line.setAttribute('stroke-width', '3');
            }
            /* MM-06: Add vertex + segment tools so architects can reshape lines */
            if (this.mode !== 'view' && joint.linkTools) {
                try {
                    let tools = new joint.dia.ToolsView({
                        tools: [
                            new joint.linkTools.Vertices({ vertexAdding: true }),
                            new joint.linkTools.Segments(),
                        ]
                    });
                    view.addTools(tools);
                } catch(e) { /* swallow-ok: optional JointJS vertex and segment tools; without them the line is simply not reshapeable, which is the view-mode behaviour anyway */ }
            }
        },

        _unhighlightLink: function(link) {
            if (!this.paper) return;
            let view = this.paper.findViewByModel(link);
            if (!view || !view.el) return;
            let line = view.el.querySelector('[joint-selector="line"]');
            if (line) {
                let origStroke = line.getAttribute('data-original-stroke') || '#64748b';
                let original = line.getAttribute('data-original-stroke-width') || '1.5';
                line.setAttribute('stroke', origStroke);
                line.setAttribute('stroke-width', original);
                line.removeAttribute('data-original-stroke');
            }
            /* Remove link tools on deselect */
            try { view.removeTools(); } catch(e) { /* swallow-ok: cosmetic removal of link tools on deselect */ }
        },

        renameElement: function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            let self = this;
            let oldName = cell.get('elName') || '';
            /* Open save-name modal instead of native prompt (CSP-safe) */
            self.saveNameValue = oldName;
            self._saveNamePromptHint = 'Enter a new name for this element';
            self.saveNameOpen = true;
            self._saveNameCallback = function(newName) {
                if (!newName || !newName.trim() || newName.trim() === oldName) return;
                cell.attr('nameLabel/text', newName.trim());
                cell.set('elName', newName.trim());
                self.statusText = 'Renamed: ' + newName.trim();
            };
        },

        inspectElement: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            this.selectedEdge = null;
            this.selectedNode = {
                elementId: cell.get('elementId'),
                label: cell.get('elName') || '(unnamed)',
                elType: cell.get('elType') || '',
                layer: cell.get('elLayer') || '',
                description: '',
            };
            this.$nextTick(function() {
                if (window.lucide) lucide.createIcons();
            });
        },

        deleteElement: async function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            let name = cell.get('elName') || '(unnamed)';
            if (!(await Platform.modal.confirm('Remove "' + name + '" from canvas?\n(Element stays in catalog)'))) return;

            let elId = cell.get('elementId');
            let elType = cell.get('elType') || '';
            cell.remove();
            if (elId) delete this.canvasElements[elId];
            this.elementCount = Math.max(0, this.elementCount - 1);
            this.statusText = 'Removed: ' + name;
            this.logAuditEvent('element_removed', 'element', elId, name, elType, null);

            // Wave 7: Propagate stale to downstream dependents
            if (elId && this.solutionId) {
                let self = this;
                Platform.fetch.post('/api/solutions/' + self.solutionId + '/elements/' + elId + '/propagate-stale', {})
                    .then(function(data) {
                    if (data.stale_count > 0) {
                        self._staleElementIds = data.stale_ids || [];
                        self.staleCount = data.stale_count;
                        self.statusText = data.stale_count + ' downstream element(s) potentially stale after removing "' + name + '"';
                        // Highlight stale elements on canvas with amber border
                        data.stale_ids.forEach(function(sid) {
                            let staleCell = self.graph.getElements().find(function(c) { return c.get('elementId') === sid; });
                            if (staleCell) {
                                staleCell.attr('body/stroke', '#f59e0b');
                                staleCell.attr('body/strokeWidth', 3);
                                staleCell.attr('body/strokeDasharray', '6 3');
                            }
                        });
                    }
                }).catch(function() {
                    /* The element was already removed from the canvas — this call only
                       checks for downstream impact, but a silent failure here leaves the
                       architect unaware that dependents might now be stale. */
                    _toast('error', 'Could not check downstream impact of removing "' + name + '"');
                });
            }
        },

        confirmBulkDelete: function() {
            let cells = this._selectedCells.slice();
            let self = this;
            cells.forEach(function(cell) {
                let elId = cell.get('elementId');
                cell.remove();
                if (elId) {
                    delete self.canvasElements[elId];
                    self.elementCount = Math.max(0, self.elementCount - 1);
                }
            });
            self._selectedCells = [];
            self.bulkDeleteConfirmOpen = false;
            self.statusText = 'Removed ' + cells.length + ' element(s) from canvas';
            /* GAP-CMP-002/003: Update validation badge + orphan highlights */
            if (self._diagramChanged) self._diagramChanged();
        },

        embedInParent: function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let child = this.ctxMenuCell;
            if (!child) return;

            let self = this;
            let elements = this.graph.getElements().filter(function(el) {
                return el.id !== child.id;
            });

            /* Find the element directly under the child by position overlap */
            let childBBox = child.getBBox();
            let parent = null;
            elements.forEach(function(el) {
                let elBBox = el.getBBox();
                if (elBBox.containsPoint(childBBox.center())) {
                    parent = el;
                }
            });

            if (!parent) {
                this.statusText = 'Drop the child onto a parent element first';
                return;
            }

            let parentType = parent.get('elType') || '';
            if (!isContainerType(parentType)) {
                this.statusText = parentType + ' does not support nesting';
                return;
            }

            let depth = getNestingDepth(parent);
            if (depth >= 3) {
                this.statusText = 'Nesting depth limit: max 3 levels';
                return;
            }

            /* Switch parent to container if needed */
            if (parent.get('renderingMode') !== 'white_box') {
                self._switchToContainer(parent);
                parent = self._getReplacementCell();
                if (!parent) return;
            }

            parent.embed(child);
            self._autoResizeContainer(parent);

            this.statusText = 'Nested "' + (child.get('elName') || '') + '" inside "' + (parent.get('elName') || '') + '"';
        },

        unembedFromParent: function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let child = this.ctxMenuCell;
            if (!child) return;
            let parent = child.getParentCell();
            if (!parent) {
                this.statusText = 'Element is not nested';
                return;
            }
            parent.unembed(child);

            /* Reset parent opacity if no more children */
            if (parent.getEmbeddedCells().length === 0) {
                let pView = this.paper.findViewByModel(parent);
                if (pView) {
                    let body = pView.el.querySelector('[joint-selector="body"]');
                    if (body) body.setAttribute('fill-opacity', '1');
                }
            }

            this.statusText = 'Unembedded "' + (child.get('elName') || '') + '"';
        },

        toggleRenderingMode: function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let cell = this.ctxMenuCell;
            if (!cell) return;

            let elType = cell.get('elType') || '';
            if (!isContainerType(elType)) {
                this.statusText = elType + ' does not support white-box rendering';
                return;
            }

            let currentMode = cell.get('renderingMode') || 'black_box';
            if (currentMode === 'black_box') {
                this._switchToContainer(cell);
                this.statusText = 'Expanded to white-box: ' + (cell.get('elName') || '');
            } else {
                this._switchToNode(cell);
                this.statusText = 'Collapsed to black-box: ' + (cell.get('elName') || '');
            }
        },

        selectAll: function() {
            if (this.mode === 'view') return;
            this._clearSelection();
            let self = this;
            this.graph.getElements().forEach(function(cell) {
                self._selectedCells.push(cell);
                let view = self.paper.findViewByModel(cell);
                if (view) self._highlightCell(view);
            });
            this.statusText = 'Selected ' + this._selectedCells.length + ' element(s)';
        },

        reLayout: function() {
            if (this.mode === 'view') return;
            applyLayerBanding(this.graph);
            this.fitCanvas();
            this.statusText = 'Auto-layout applied';
        },

        /* ── CMP-039: Persistent layer zone swimlanes ──────── */
        /* ── CMP-069: Empty-canvas feedback for Lanes button ── */
        toggleLayerZones: function() {
            let self = this;

            if (self.layerZonesActive && self.layerZoneCells.length) {
                /* Remove all zones */
                self.layerZoneCells.forEach(function(c) { c.remove(); });
                self.layerZoneCells = [];
                self.layerZonesActive = false;
                self.viewpointDirty = true;
                self.statusText = 'Layer zones hidden';
                return;
            }

            /* CMP-069: Check if canvas has real elements before creating lanes */
            let realElements = self.graph.getElements().filter(function(cell) {
                return !cell.get('isLayerZone') && !cell.get('isAnnotation');
            });
            if (realElements.length === 0) {
                _toast('info', 'Add elements to the canvas first, then use Lanes to organise them by ArchiMate layer');
                self.statusText = 'Lanes require elements — add ArchiMate elements first';
                return;
            }

            /* Create 6 horizontal bands — standard ArchiMate layer order */
            let ZONE_DEFS = [
                { layer: 'motivation',     y: 0    },
                { layer: 'strategy',       y: 180  },
                { layer: 'business',       y: 360  },
                { layer: 'application',    y: 540  },
                { layer: 'technology',     y: 720  },
                { layer: 'implementation', y: 900  },
            ];

            self.layerZoneCells = [];
            ZONE_DEFS.forEach(function(def) {
                let zone = createLayerZone(def.layer, -60, def.y, 1520, 160);
                self.graph.addCell(zone);
                zone.toBack();
                self.layerZoneCells.push(zone);
            });

            self.layerZonesActive = true;
            self.viewpointDirty = true;
            self.statusText = 'Layer zones active — drop elements into bands';
        },

        _detectZoneMembership: function(cell) {
            let self = this;
            if (!self.layerZonesActive || !self.layerZoneCells.length) return;

            let pos = cell.position();
            let size = cell.size();
            let cx = pos.x + size.width / 2;
            let cy = pos.y + size.height / 2;

            for (let i = 0; i < self.layerZoneCells.length; i++) {
                let zone = self.layerZoneCells[i];
                let zp = zone.position();
                let zs = zone.size();
                if (cx >= zp.x + 38 && cx <= zp.x + zs.width &&
                    cy >= zp.y && cy <= zp.y + zs.height) {

                    let newLayer = zone.get('zoneLayer');
                    let oldLayer = cell.get('elLayer');
                    if (newLayer && newLayer !== oldLayer) {
                        cell.set('elLayer', newLayer);
                        /* Re-style element to match new layer colour */
                        let c = layerColor(newLayer);
                        let attrs = {};
                        if (cell.attr('body')) {
                            attrs.body = { fill: c.fill, stroke: c.accent || c.stroke };
                        }
                        if (cell.attr('accentBar')) {
                            attrs.accentBar = { fill: c.accent || c.stroke };
                        }
                        if (cell.attr('nameLabel')) {
                            attrs.nameLabel = { fill: c.text };
                        }
                        if (Object.keys(attrs).length) { cell.attr(attrs); }
                        self.statusText = 'Layer updated: ' + newLayer;
                    }
                    return;
                }
            }
        },

        alignSelected: function(direction) {
            if (this.mode === 'view') return;
            let cells = this._selectedCells;
            if (cells.length < 2) { this.statusText = 'Select 2+ elements to align'; return; }

            if (direction === 'left') {
                let minX = Infinity;
                cells.forEach(function(c) { minX = Math.min(minX, c.position().x); });
                cells.forEach(function(c) { c.position(minX, c.position().y); });
            } else if (direction === 'center-h') {
                let sum = 0;
                cells.forEach(function(c) { sum += c.position().x + c.size().width / 2; });
                let cx = sum / cells.length;
                cells.forEach(function(c) { c.position(cx - c.size().width / 2, c.position().y); });
            } else if (direction === 'top') {
                let minY = Infinity;
                cells.forEach(function(c) { minY = Math.min(minY, c.position().y); });
                cells.forEach(function(c) { c.position(c.position().x, minY); });
            } else if (direction === 'right') {
                let maxRight = -Infinity;
                cells.forEach(function(c) { maxRight = Math.max(maxRight, c.position().x + c.size().width); });
                cells.forEach(function(c) { c.position(maxRight - c.size().width, c.position().y); });
            } else if (direction === 'bottom') {
                let maxBottom = -Infinity;
                cells.forEach(function(c) { maxBottom = Math.max(maxBottom, c.position().y + c.size().height); });
                cells.forEach(function(c) { c.position(c.position().x, maxBottom - c.size().height); });
            }
            this.statusText = 'Aligned ' + cells.length + ' elements';
        },

        distributeSelected: function(direction) {
            if (this.mode === 'view') return;
            let cells = this._selectedCells;
            if (cells.length < 3) { this.statusText = 'Select 3+ elements to distribute'; return; }

            if (direction === 'horizontal') {
                let sorted = cells.slice().sort(function(a, b) { return a.position().x - b.position().x; });
                let first = sorted[0].position().x;
                let last = sorted[sorted.length - 1].position().x;
                let step = (last - first) / (sorted.length - 1);
                sorted.forEach(function(c, i) { c.position(first + i * step, c.position().y); });
            } else if (direction === 'vertical') {
                let sorted = cells.slice().sort(function(a, b) { return a.position().y - b.position().y; });
                let first = sorted[0].position().y;
                let last = sorted[sorted.length - 1].position().y;
                let step = (last - first) / (sorted.length - 1);
                sorted.forEach(function(c, i) { c.position(c.position().x, first + i * step); });
            }
            this.statusText = 'Distributed ' + cells.length + ' elements';
        },

        _showAlignGuides: function(movingCell) {
            this._clearAlignGuides();
            let svgEl = this.paper.el.querySelector('svg');
            if (!svgEl) return;
            let movingBBox = movingCell.getBBox();
            let TOLERANCE = 8;
            let guides = [];
            let self = this;

            let mLeft = movingBBox.x, mCenterX = movingBBox.x + movingBBox.width / 2, mRight = movingBBox.x + movingBBox.width;
            let mTop = movingBBox.y, mCenterY = movingBBox.y + movingBBox.height / 2, mBottom = movingBBox.y + movingBBox.height;

            this.graph.getElements().forEach(function(el) {
                if (el.id === movingCell.id) return;
                let b = el.getBBox();
                let sLeft = b.x, sCenterX = b.x + b.width / 2, sRight = b.x + b.width;
                let sTop = b.y, sCenterY = b.y + b.height / 2, sBottom = b.y + b.height;

                [[mLeft, sLeft, 'v'], [mLeft, sCenterX, 'v'], [mLeft, sRight, 'v'],
                 [mCenterX, sLeft, 'v'], [mCenterX, sCenterX, 'v'], [mCenterX, sRight, 'v'],
                 [mRight, sLeft, 'v'], [mRight, sCenterX, 'v'], [mRight, sRight, 'v']
                ].forEach(function(pair) {
                    if (Math.abs(pair[0] - pair[1]) < TOLERANCE) guides.push({ type: 'v', x: pair[1] });
                });
                [[mTop, sTop, 'h'], [mTop, sCenterY, 'h'], [mTop, sBottom, 'h'],
                 [mCenterY, sTop, 'h'], [mCenterY, sCenterY, 'h'], [mCenterY, sBottom, 'h'],
                 [mBottom, sTop, 'h'], [mBottom, sCenterY, 'h'], [mBottom, sBottom, 'h']
                ].forEach(function(pair) {
                    if (Math.abs(pair[0] - pair[1]) < TOLERANCE) guides.push({ type: 'h', y: pair[1] });
                });
            });

            let seen = {};
            guides.forEach(function(g) {
                let key = g.type + ':' + (g.x || g.y);
                if (seen[key]) return;
                seen[key] = true;
                let line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('stroke', '#3b82f6');
                line.setAttribute('stroke-width', '1');
                line.setAttribute('stroke-dasharray', '4,2');
                line.setAttribute('opacity', '0.8');
                line.setAttribute('class', 'align-guide');
                if (g.type === 'v') {
                    line.setAttribute('x1', g.x); line.setAttribute('y1', -9999);
                    line.setAttribute('x2', g.x); line.setAttribute('y2', 99999);
                } else {
                    line.setAttribute('x1', -9999); line.setAttribute('y1', g.y);
                    line.setAttribute('x2', 99999); line.setAttribute('y2', g.y);
                }
                svgEl.appendChild(line);
                self._guideLines.push(line);
            });
        },

        _clearAlignGuides: function() {
            this._guideLines.forEach(function(line) {
                if (line.parentNode) line.parentNode.removeChild(line);
            });
            this._guideLines = [];
        },

        /* BUG-CMP-002: Persist a single metadata field on a relationship via PUT */
        _persistRelMetadata: function(relId, payload) {
            if (!relId) return;
            Platform.fetch.put('/archimate/api/relationships/' + relId, payload, { silent: true })
                .catch(function(err) {
                    _toast('error', 'Failed to save relationship property: ' + (err.message || err));
                });
        },

        /* GAP-INT-001: Persist connection specification to backend + update annotation */
        updateConnectionSpec: function() {
            if (!this.selectedLink || !this.selectedEdge) return;
            let spec = this.selectedEdge._connSpec || {};
            /* Filter out empty fields */
            const cleanSpec = {};
            Object.keys(spec).forEach(function(k) { if (spec[k]) cleanSpec[k] = spec[k]; });
            let hasSpec = Object.keys(cleanSpec).length > 0;

            this.selectedLink.set('connectionSpec', hasSpec ? cleanSpec : null);

            /* Persist to backend */
            let relId = this.selectedLink.get('relId');
            if (relId) {
                this._persistRelMetadata(relId, { connection_spec: hasSpec ? cleanSpec : null });
            }

            /* GAP-INT-003: Update annotation card on canvas */
            this._updateAnnotationCard(this.selectedLink);
            this.statusText = 'Connection spec updated';
        },

        /* GAP-INT-003: Render or update a floating annotation label on a relationship link */
        _updateAnnotationCard: function(link) {
            if (!link) return;
            let spec = link.get('connectionSpec') || {};
            let hasSpec = Object.keys(spec).length > 0;

            /* Build annotation text lines */
            const lines = [];
            if (spec.data_name) lines.push('Data: ' + spec.data_name);
            if (spec.transfer_strategy) lines.push('Strategy: ' + spec.transfer_strategy);
            if (spec.interface_type) lines.push('Interface: ' + spec.interface_type);
            if (spec.iam_method) lines.push('IAM: ' + spec.iam_method);
            if (spec.file_format) lines.push('Format: ' + spec.file_format);
            if (spec.file_name_pattern) lines.push('File: ' + spec.file_name_pattern);
            if (spec.protocol) lines.push('Protocol: ' + spec.protocol);

            /* Find existing annotation label on this link */
            const existingLabels = link.labels() || [];
            let annotIdx = -1;
            for (let i = 0; i < existingLabels.length; i++) {
                if (existingLabels[i].attrs && existingLabels[i].attrs._isAnnotation) {
                    annotIdx = i;
                    break;
                }
            }

            if (!hasSpec || lines.length === 0) {
                /* Remove annotation label if spec is empty */
                if (annotIdx >= 0) link.removeLabel(annotIdx);
                return;
            }

            const text = lines.join('\n');

            /* Strategy-based border color */
            let headerColor = '#64748b'; /* default gray */
            if (spec.transfer_strategy === 'PUSH') headerColor = '#16a34a';
            if (spec.transfer_strategy === 'PULL') headerColor = '#2563eb';
            if (spec.transfer_strategy === 'Manual') headerColor = '#d97706';

            const visible = this.showAnnotations !== false;

            const labelDef = {
                attrs: {
                    _isAnnotation: true,
                    text: {
                        text: text,
                        fontSize: 9,
                        fontFamily: 'Public Sans, Inter, system-ui, sans-serif',
                        fontWeight: 400,
                        fill: '#334155',
                        textAnchor: 'start',
                        textVerticalAnchor: 'top',
                        lineHeight: 14,
                        display: visible ? 'block' : 'none',
                    },
                    rect: {
                        fill: '#ffffff',
                        stroke: headerColor,
                        strokeWidth: 1,
                        rx: 4,
                        ry: 4,
                        refWidth: 10,
                        refHeight: 6,
                        display: visible ? 'block' : 'none',
                    },
                },
                position: {
                    distance: 0.5,
                    offset: { x: 10, y: 20 },
                },
            };

            if (annotIdx >= 0) {
                link.label(annotIdx, labelDef);
            } else {
                link.appendLabel(labelDef);
            }
        },

        /* GAP-INT-003: Toggle annotation card visibility on all links */
        toggleAnnotations: function() {
            this.showAnnotations = !this.showAnnotations;
            let self = this;
            this.graph.getLinks().forEach(function(link) {
                const labels = link.labels() || [];
                for (let i = 0; i < labels.length; i++) {
                    if (labels[i].attrs && labels[i].attrs._isAnnotation) {
                        if (self.showAnnotations) {
                            link.label(i, { attrs: { text: { display: 'block' }, rect: { display: 'block' } } });
                        } else {
                            link.label(i, { attrs: { text: { display: 'none' }, rect: { display: 'none' } } });
                        }
                    }
                }
            });
            this.statusText = self.showAnnotations ? 'Annotations visible' : 'Annotations hidden';
        },

        setSelectedLinkAccessMode: function(mode) {
            if (!this.selectedLink || this.mode == 'view') return;
            this.selectedLink.set('accessMode', mode);
            let labelText = 'access (' + mode + ')';
            this.selectedLink.label(0, {
                attrs: {
                    text: { text: labelText, fontSize: 10, fontWeight: 500, fill: '#64748b' },
                    rect: { fill: '#fff', stroke: '#e2e8f0', rx: 3, ry: 3 },
                },
                position: { distance: 0.5, offset: -12 },
            });
            if (this.selectedEdge) this.selectedEdge.accessMode = mode;
            /* BUG-CMP-002: Persist access_mode to backend */
            let relId = this.selectedLink.get('relId');
            this._persistRelMetadata(relId, { access_mode: mode });
            this.statusText = 'Access mode: ' + mode;
        },

        setSelectedLinkRouting: function(routingName) {
            if (!this.selectedLink || this.mode == 'view') return;
            let link = this.selectedLink;
            if (routingName === 'manhattan') {
                link.router('manhattan', { step: 12, padding: 36 });
                link.connector('rounded', { radius: 8 });
            } else {
                link.router('normal');
                link.connector('smooth');
            }
            link.set('routingStyle', routingName);
            if (this.selectedEdge) this.selectedEdge.routingStyle = routingName;
            this.statusText = 'Routing: ' + routingName;
        },

        setDefaultRouting: function(routingName) {
            this.defaultRouting = routingName;
            this.statusText = 'Default routing: ' + routingName;
        },

        changeRelType: function() {
            if (!this.selectedLink || this.mode == 'view') return;
            let link = this.selectedLink;
            let srcCell = this.graph.getCell((link.get('source') || {}).id);
            let tgtCell = this.graph.getCell((link.get('target') || {}).id);
            if (!srcCell || !tgtCell) return;

            let srcElementId = srcCell.get('elementId');
            let tgtElementId = tgtCell.get('elementId');
            if (!srcElementId || !tgtElementId) return;

            this._pendingLink = link;
            this._isChangeType = true;
            this.relPickerSourceCell = srcCell;
            this.relPickerTargetCell = tgtCell;
            this.relPickerSourceId = srcElementId;
            this.relPickerTargetId = tgtElementId;

            let paperRect = this.paper.el.getBoundingClientRect();
            this.relPickerX = Math.max(10, paperRect.left + paperRect.width / 2 - 110);
            this.relPickerY = Math.max(10, paperRect.top + paperRect.height / 2 - 100);

            this.relPickerTypes = [];
            this.relPickerInvalidTypes = [];
            this.associationWarning = false;
            this.relPickerOpen = true;

            let self = this;
            Platform.fetch.get('/archimate/api/valid-relationship-types', {
                source_id: srcElementId,
                target_id: tgtElementId,
            }, { silent: true })
                .then(function(data) {
                let validDetailed = data.valid_types_detailed || [];
                self.relPickerTypes = validDetailed.length > 0
                    ? validDetailed
                    : (data.valid_types || ['association']).map(function(t) {
                        return { type: t, tier: 'standard', description: '' };
                    });

                let ALL_REL_TYPES = [
                    'composition', 'aggregation', 'assignment', 'realization',
                    'serving', 'access', 'influence', 'triggering', 'flow',
                    'specialization', 'association',
                ];
                let validSet = {};
                self.relPickerTypes.forEach(function(v) { validSet[v.type || v] = true; });
                self.relPickerInvalidTypes = ALL_REL_TYPES.filter(function(t) {
                    return !validSet[t];
                });
            })
            // fabricated-ok: falls back to a single type tagged tier:'fallback' and raises an error toast
            .catch(function() {
                self.relPickerTypes = [{ type: 'association', tier: 'fallback', description: '' }];
                self.relPickerInvalidTypes = [];
                _toast('error', 'Failed to load relationship types');
            });
        },

        changeRelTypeFromCtx: function() {
            this.relCtxMenuOpen = false;
            let link = this.relCtxMenuLink;
            if (!link) return;
            if (this.selectedLink && this.selectedLink !== link) {
                this._unhighlightLink(this.selectedLink);
            }
            this.selectedLink = link;
            this._highlightLink(link);
            this.changeRelType();
        },

        reverseSelectedRel: function() {
            if (!this.selectedLink || this.mode == 'view') return;
            let link = this.selectedLink;
            let src = link.get('source');
            let tgt = link.get('target');
            link.set('source', tgt);
            link.set('target', src);

            if (this.selectedEdge) {
                let tmp = this.selectedEdge.source;
                this.selectedEdge.source = this.selectedEdge.target;
                this.selectedEdge.target = tmp;
                let tmpId = this.selectedEdge.sourceId;
                this.selectedEdge.sourceId = this.selectedEdge.targetId;
                this.selectedEdge.targetId = tmpId;
            }
            this.statusText = 'Relationship reversed';
        },

        deleteSelectedRel: function() {
            if (!this.selectedLink || this.mode == 'view') return;
            this.relCtxMenuLink = this.selectedLink;
            this.deleteRelationship();
        },

        /* ── CMP-054: Presentation mode (CMP-061: fixed load + dismiss) ── */
        enterPresentationMode: function() {
            let self = this;
            self.presentationIndex = 0;

            function activateWithSlides(slides) {
                self.presentationSlides = slides;
                self.presentationActive = true;
                if (slides.length > 0 && slides[0].id) {
                    self.loadSavedViewpoint(slides[0].id);
                }
                self.statusText = 'Presentation: slide 1 of ' + slides.length;
            }

            Platform.fetch.get('/archimate/api/saved-viewpoints', {}, { silent: true })
                .then(function(data) {
                    let vps = data.viewpoints || data || [];
                    let slides = vps.map(function(v) {
                        return { id: v.id, name: v.name || v.viewpoint_name || 'Untitled' };
                    });
                    if (slides.length === 0) {
                        slides = [{ id: null, name: 'Current Diagram' }];
                    }
                    activateWithSlides(slides);
                })
                .catch(function() {
                    activateWithSlides([{ id: null, name: 'Current Diagram' }]);
                });

            /* Add keyboard handler */
            self._presentationKeyHandler = function(e) {
                if (!self.presentationActive) return;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                    e.preventDefault();
                    self.presentationNext();
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    self.presentationPrev();
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    self.exitPresentationMode();
                }
            };
            document.addEventListener('keydown', self._presentationKeyHandler);
        },

        exitPresentationMode: function() {
            this.presentationActive = false;
            this.presentationSlides = [];
            this.presentationIndex = 0;
            if (this._presentationKeyHandler) {
                document.removeEventListener('keydown', this._presentationKeyHandler);
                this._presentationKeyHandler = null;
            }
            this.statusText = 'Ready';
        },

        presentationNext: function() {
            let self = this;
            if (!self.presentationSlides || self.presentationSlides.length === 0) return;
            self.presentationIndex = (self.presentationIndex + 1) % self.presentationSlides.length;
            let slide = self.presentationSlides[self.presentationIndex];
            if (slide.id) self.loadSavedViewpoint(slide.id);
            self.statusText = 'Slide ' + (self.presentationIndex + 1) + ' of ' + self.presentationSlides.length + ': ' + slide.name;
        },

        presentationPrev: function() {
            let self = this;
            if (!self.presentationSlides || self.presentationSlides.length === 0) return;
            self.presentationIndex = self.presentationIndex <= 0 ? self.presentationSlides.length - 1 : self.presentationIndex - 1;
            let slide = self.presentationSlides[self.presentationIndex];
            if (slide.id) self.loadSavedViewpoint(slide.id);
            self.statusText = 'Slide ' + (self.presentationIndex + 1) + ' of ' + self.presentationSlides.length + ': ' + slide.name;
        },

        /* ── CMP-055: Style templates ── */
        _builtinStyleTemplates: [
            { name: 'Corporate', fill: '#f0f4f8', stroke: '#334155', fontFamily: 'inherit', fontSize: 12, lineWidth: 1.5 },
            { name: 'High Contrast', fill: '#ffffff', stroke: '#000000', fontFamily: 'inherit', fontSize: 13, lineWidth: 2 },
            { name: 'Print Friendly', fill: '#fafafa', stroke: '#555555', fontFamily: 'Georgia, serif', fontSize: 11, lineWidth: 1 },
        ],

        loadStyleTemplates: function() {
            let self = this;
            try {
                let stored = localStorage.getItem('composer_style_templates');
                self.customStyleTemplates = stored ? JSON.parse(stored) : [];
            } catch(e) {
                self.customStyleTemplates = [];
            }
        },

        getAllStyleTemplates: function() {
            return this._builtinStyleTemplates.concat(this.customStyleTemplates || []);
        },

        applyStyleTemplate: function(templateName) {
            let self = this;
            let templates = self.getAllStyleTemplates();
            let tmpl = null;
            for (let i = 0; i < templates.length; i++) {
                if (templates[i].name === templateName) { tmpl = templates[i]; break; }
            }
            if (!tmpl) return;

            let targets = self._selectedCells && self._selectedCells.length > 0
                ? self._selectedCells
                : self.graph.getElements().filter(function(c) { return !c.get('isLayerZone') && !c.get('isAnnotation'); });

            targets.forEach(function(cell) {
                cell.attr('body/fill', tmpl.fill);
                cell.attr('body/stroke', tmpl.stroke);
                cell.attr('body/strokeWidth', tmpl.lineWidth);
                cell.attr('label/fontFamily', tmpl.fontFamily);
                cell.attr('label/fontSize', tmpl.fontSize);
            });

            self.viewpointDirty = true;
            self.statusText = 'Applied "' + tmpl.name + '" to ' + targets.length + ' elements';
        },

        saveCustomStyleTemplate: function() {
            let self = this;
            if (!self.newStyleTemplateName || !self.newStyleTemplateName.trim()) return;

            /* Get style from first selected element */
            let src = self._selectedCells && self._selectedCells.length > 0 ? self._selectedCells[0] : null;
            let tmpl = {
                name: self.newStyleTemplateName.trim(),
                fill: src ? (src.attr('body/fill') || '#f0f4f8') : '#f0f4f8',
                stroke: src ? (src.attr('body/stroke') || '#334155') : '#334155',
                fontFamily: src ? (src.attr('label/fontFamily') || 'inherit') : 'inherit',
                fontSize: src ? (src.attr('label/fontSize') || 12) : 12,
                lineWidth: src ? (src.attr('body/strokeWidth') || 1.5) : 1.5,
            };

            if (!self.customStyleTemplates) self.customStyleTemplates = [];
            /* Replace if same name exists */
            let idx = -1;
            for (let i = 0; i < self.customStyleTemplates.length; i++) {
                if (self.customStyleTemplates[i].name === tmpl.name) { idx = i; break; }
            }
            if (idx >= 0) {
                self.customStyleTemplates[idx] = tmpl;
            } else {
                self.customStyleTemplates.push(tmpl);
            }

            let persisted = true;
            try {
                localStorage.setItem('composer_style_templates', JSON.stringify(self.customStyleTemplates));
            } catch(e) { persisted = false; }

            self.newStyleTemplateName = '';
            self.styleTemplateSaveOpen = false;
            if (persisted) {
                _toast('info', 'Style template "' + tmpl.name + '" saved');
            } else {
                /* Template is only in memory now — it looked saved but will vanish on
                   reload, so the user has to be told rather than finding out later. */
                _toast('error', 'Could not save style template "' + tmpl.name + '" — storage unavailable');
            }
        },

        deleteCustomStyleTemplate: function(name) {
            let self = this;
            self.customStyleTemplates = (self.customStyleTemplates || []).filter(function(t) { return t.name !== name; });
            /* The row vanishes from the list the moment this runs, so a storage
               failure read as "deleted" — and the template came back on the next
               reload. saveStyleTemplate() above already reports the mirror-image
               failure; this one has to as well. */
            try {
                localStorage.setItem('composer_style_templates', JSON.stringify(self.customStyleTemplates));
            } catch(e) {
                _toast('error', 'Could not delete style template "' + name + '" — storage unavailable, it will reappear on reload');
            }
        },

        resetElementStyles: function() {
            let self = this;
            let targets = self._selectedCells && self._selectedCells.length > 0
                ? self._selectedCells
                : self.graph.getElements().filter(function(c) { return !c.get('isLayerZone') && !c.get('isAnnotation'); });

            let layerColorFn = helpers.layerColor;
            targets.forEach(function(cell) {
                let layer = (cell.get('elLayer') || '').toLowerCase();
                let colors = layerColorFn(layer);
                cell.attr('body/fill', colors.fill);
                cell.attr('body/stroke', colors.stroke);
                cell.attr('body/strokeWidth', 1.5);
                cell.attr('label/fontFamily', 'inherit');
                cell.attr('label/fontSize', 12);
            });
            self.viewpointDirty = true;
            self.statusText = 'Reset styles on ' + targets.length + ' elements';
        },

        // QA-CMP-010: Force-directed layout (spring algorithm, no D3 dependency)
        forceDirectedLayout: function(elementNodes, relationshipLinks) {
            let ITERATIONS = 150;
            let REPULSION = 8000;
            let ATTRACTION = 0.05;
            let DAMPING = 0.85;
            let IDEAL_LENGTH = 200;

            if (!elementNodes || elementNodes.length === 0) return {};

            // Initialise positions
            let pos = {};
            elementNodes.forEach(function(el, i) {
                let angle = (2 * Math.PI * i) / elementNodes.length;
                let radius = Math.max(200, elementNodes.length * 30);
                pos[el.id] = {
                    x: 500 + radius * Math.cos(angle),
                    y: 400 + radius * Math.sin(angle),
                    vx: 0,
                    vy: 0,
                };
            });

            for (let iter = 0; iter < ITERATIONS; iter++) {
                let forces = {};
                elementNodes.forEach(function(el) { forces[el.id] = { fx: 0, fy: 0 }; });

                // Repulsion between all node pairs
                for (let i = 0; i < elementNodes.length; i++) {
                    for (let j = i + 1; j < elementNodes.length; j++) {
                        let ai = elementNodes[i].id, aj = elementNodes[j].id;
                        let dx = pos[ai].x - pos[aj].x;
                        let dy = pos[ai].y - pos[aj].y;
                        let dist = Math.sqrt(dx * dx + dy * dy) || 1;
                        let force = REPULSION / (dist * dist);
                        forces[ai].fx += force * dx / dist;
                        forces[ai].fy += force * dy / dist;
                        forces[aj].fx -= force * dx / dist;
                        forces[aj].fy -= force * dy / dist;
                    }
                }

                // Attraction along edges
                (relationshipLinks || []).forEach(function(link) {
                    let src = link.source_id || link.sourceId;
                    let tgt = link.target_id || link.targetId;
                    if (!pos[src] || !pos[tgt]) return;
                    let dx = pos[tgt].x - pos[src].x;
                    let dy = pos[tgt].y - pos[src].y;
                    let dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    let force = ATTRACTION * (dist - IDEAL_LENGTH);
                    forces[src].fx += force * dx / dist;
                    forces[src].fy += force * dy / dist;
                    forces[tgt].fx -= force * dx / dist;
                    forces[tgt].fy -= force * dy / dist;
                });

                // Update velocities and positions
                elementNodes.forEach(function(el) {
                    let p = pos[el.id];
                    p.vx = (p.vx + forces[el.id].fx) * DAMPING;
                    p.vy = (p.vy + forces[el.id].fy) * DAMPING;
                    p.x += p.vx;
                    p.y += p.vy;
                    // Clamp to canvas
                    p.x = Math.max(50, Math.min(2000, p.x));
                    p.y = Math.max(50, Math.min(1500, p.y));
                });
            }

            // Return {elementId: {x, y}} map
            let result = {};
            elementNodes.forEach(function(el) {
                result[el.id] = { x: Math.round(pos[el.id].x), y: Math.round(pos[el.id].y) };
            });
            return result;
        },

        // QA-CMP-010: Apply force-directed layout to current canvas
        applyForceDirectedLayout: function() {
            let self = this;
            let elements = self.graph.getElements().filter(function(c) {
                return !c.get('isLayerZone') && !c.get('isAnnotation');
            });
            let links = self.graph.getLinks();

            let nodes = elements.map(function(el) { return { id: el.id }; });
            let edges = links.map(function(lk) {
                return { source_id: lk.get('source').id, target_id: lk.get('target').id };
            });

            let positions = ComposerGraph.getMethods({
                csrfToken: helpers.csrfToken, createNode: helpers.createNode,
                createLink: helpers.createLink, layerColor: helpers.layerColor,
                guessLayer: helpers.guessLayer, REL_STYLES: helpers.REL_STYLES,
                _toast: helpers._toast, UndoStack: helpers.UndoStack,
                isContainerType: helpers.isContainerType,
                getNestingDepth: helpers.getNestingDepth,
                applyLayerBanding: helpers.applyLayerBanding,
                createLayerZone: helpers.createLayerZone,
            }).forceDirectedLayout(nodes, edges);

            self.graph.startBatch('force-layout');
            elements.forEach(function(el) {
                let p = positions[el.id];
                if (p) el.set('position', { x: p.x, y: p.y });
            });
            self.graph.stopBatch('force-layout');
            self.viewpointDirty = true;
            self.statusText = 'Force-directed layout applied to ' + elements.length + ' elements';
        },

        // QA-CMP-010: Sugiyama layered layout (rank assignment + crossing minimization)
        sugiyamaLayout: function(elementNodes, relationshipLinks) {
            if (!elementNodes || elementNodes.length === 0) return {};

            let LAYER_HEIGHT = 160;
            let NODE_WIDTH = 200;
            let H_PADDING = 60;

            // Build adjacency: id → set of successors
            let successors = {};
            let predecessors = {};
            elementNodes.forEach(function(el) {
                successors[el.id] = [];
                predecessors[el.id] = [];
            });
            (relationshipLinks || []).forEach(function(link) {
                let src = link.source_id || link.sourceId;
                let tgt = link.target_id || link.targetId;
                if (successors[src] !== undefined && predecessors[tgt] !== undefined) {
                    successors[src].push(tgt);
                    predecessors[tgt].push(src);
                }
            });

            // Assign ranks (longest path from sources)
            let ranks = {};
            let queue = elementNodes.filter(function(el) {
                return predecessors[el.id].length === 0;
            }).map(function(el) { return el.id; });

            queue.forEach(function(id) { ranks[id] = 0; });

            let visited = new Set(queue);
            let head = 0;
            while (head < queue.length) {
                let cur = queue[head++];
                (successors[cur] || []).forEach(function(nxt) {
                    let newRank = (ranks[cur] || 0) + 1;
                    if (ranks[nxt] === undefined || newRank > ranks[nxt]) {
                        ranks[nxt] = newRank;
                    }
                    if (!visited.has(nxt)) {
                        visited.add(nxt);
                        queue.push(nxt);
                    }
                });
            }
            // Assign rank 0 to any unreachable nodes
            elementNodes.forEach(function(el) {
                if (ranks[el.id] === undefined) ranks[el.id] = 0;
            });

            // Group by rank
            let byRank = {};
            elementNodes.forEach(function(el) {
                let r = ranks[el.id];
                if (!byRank[r]) byRank[r] = [];
                byRank[r].push(el.id);
            });

            // Position nodes: rank → y, position within rank → x
            let result = {};
            Object.keys(byRank).forEach(function(rank) {
                let rankNum = parseInt(rank, 10);
                let nodes = byRank[rank];
                let totalWidth = nodes.length * (NODE_WIDTH + H_PADDING);
                let startX = Math.max(50, 960 - totalWidth / 2);
                nodes.forEach(function(id, idx) {
                    result[id] = {
                        x: Math.round(startX + idx * (NODE_WIDTH + H_PADDING)),
                        y: Math.round(80 + rankNum * LAYER_HEIGHT),
                    };
                });
            });

            return result;
        },

        // QA-CMP-010: Apply Sugiyama layout to current canvas
        applySugiyamaLayout: function() {
            let self = this;
            let elements = self.graph.getElements().filter(function(c) {
                return !c.get('isLayerZone') && !c.get('isAnnotation');
            });
            let links = self.graph.getLinks();

            let nodes = elements.map(function(el) { return { id: el.id }; });
            let edges = links.map(function(lk) {
                return { source_id: lk.get('source').id, target_id: lk.get('target').id };
            });

            let positions = ComposerGraph.getMethods({
                csrfToken: helpers.csrfToken, createNode: helpers.createNode,
                createLink: helpers.createLink, layerColor: helpers.layerColor,
                guessLayer: helpers.guessLayer, REL_STYLES: helpers.REL_STYLES,
                _toast: helpers._toast, UndoStack: helpers.UndoStack,
                isContainerType: helpers.isContainerType,
                getNestingDepth: helpers.getNestingDepth,
                applyLayerBanding: helpers.applyLayerBanding,
                createLayerZone: helpers.createLayerZone,
            }).sugiyamaLayout(nodes, edges);

            self.graph.startBatch('sugiyama-layout');
            elements.forEach(function(el) {
                let p = positions[el.id];
                if (p) el.set('position', { x: p.x, y: p.y });
            });
            self.graph.stopBatch('sugiyama-layout');
            self.viewpointDirty = true;
            self.statusText = 'Sugiyama layout applied to ' + elements.length + ' elements';
        },

        /* ── New Diagram: clears canvas + resets saved viewpoint state ──
         * C-04: the confirm dialog now names exactly what will be lost
         * (element/relationship counts), and the clear is pushed onto
         * UndoStack as a single undoable action instead of wiping undo
         * history — Ctrl+Z (or the Undo toolbar button) restores the
         * cleared canvas. The server-side SavedDiagram row for a
         * previously-saved diagram (currentSavedVpId) is NOT deleted by
         * this action — it stays retrievable from "Open" regardless of
         * undo. */
        newDiagram: async function() {
            if (this.mode === 'view') return;
            let self = this;
            const elementCount = this.graph ? this.graph.getElements().length : 0;
            const linkCount = this.graph ? this.graph.getLinks().length : 0;
            const hasContent = elementCount > 0;
            if (hasContent) {
                const unsavedPrefix = this.viewpointDirty ? 'You have unsaved changes. ' : '';
                const msg = unsavedPrefix + elementCount + ' element' + (elementCount === 1 ? '' : 's') +
                    ' and ' + linkCount + ' relationship' + (linkCount === 1 ? '' : 's') +
                    ' will be cleared from the canvas. This can be undone with Ctrl+Z. Continue?';
                /* C-04: destructive path is styled as destructive (not the
                 * prominent primary button), and Cancel — the safe default —
                 * receives focus, per Platform.confirm's showCancel behavior. */
                const ok = await Platform.modal.confirm({
                    title: 'Clear canvas?',
                    message: msg,
                    confirmText: 'Clear canvas',
                    cancelText: 'Keep working',
                    variant: 'destructive',
                });
                if (!ok) return;
            }

            let preClearSnapshot = (hasContent && this.graph) ? this.graph.toJSON() : null;
            let preClearVpId = this.currentSavedVpId;
            let preClearTabId = this.activeTabId;
            let preClearVpName = this.activeViewpointName;
            let preClearDirty = this.viewpointDirty;

            if (this.graph) { this.graph.clear(); }
            this.canvasElements = {};
            this.elementCount = 0;
            this.relCount = 0;
            this.currentSavedVpId = null;
            this.activeTabId = null;
            this.activeViewpointName = '';
            this.viewpointDirty = false;
            this.selectedNode = null;
            this.selectedEdge = null;
            this.selectedLink = null;
            this._selectedCells = [];
            this.statusText = 'New diagram — drag elements from the palette to start';
            let url = new URL(window.location);
            url.searchParams.delete('viewpoint_id');
            window.history.replaceState({}, '', url);

            if (preClearSnapshot && typeof UndoStack !== 'undefined') {
                UndoStack.push({
                    undo: function() {
                        self.graph.fromJSON(preClearSnapshot);
                        self.currentSavedVpId = preClearVpId;
                        self.activeTabId = preClearTabId;
                        self.activeViewpointName = preClearVpName;
                        self.viewpointDirty = preClearDirty;
                        self.elementCount = self.graph.getElements().length;
                        self.relCount = self.graph.getLinks().length;
                        self.statusText = 'Restored the diagram cleared by "New diagram"';
                    },
                    redo: function() {
                        self.graph.clear();
                        self.currentSavedVpId = null;
                        self.activeTabId = null;
                        self.activeViewpointName = '';
                        self.viewpointDirty = false;
                        self.elementCount = 0;
                        self.relCount = 0;
                    },
                });
            }
        },

        /* ── Auto-detect existing relationships between canvas elements ── */
        autoDetectRelationships: function() {
            let self = this;
            let elements = self.graph.getElements();
            let elementIds = elements.map(function(el) { return el.get('elementId'); }).filter(Boolean);
            if (elementIds.length < 2) {
                _toast('info', 'Need at least 2 catalogued elements on canvas');
                return;
            }
            let existingPairs = {};
            self.graph.getLinks().forEach(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                if (src && tgt && src.id && tgt.id) {
                    let srcEl = self.graph.getCell(src.id);
                    let tgtEl = self.graph.getCell(tgt.id);
                    if (srcEl && tgtEl) {
                        existingPairs[srcEl.get('elementId') + '-' + tgtEl.get('elementId')] = true;
                    }
                }
            });
            let cellMap = {};
            elements.forEach(function(el) {
                let eid = el.get('elementId');
                if (eid) cellMap[eid] = el;
            });
            self.statusText = 'Scanning for existing relationships...';
            Platform.fetch.get('/archimate/api/relationships', {
                per_page: 200,
                element_ids: elementIds.join(','),
            }, { silent: true })
                .then(function(data) {
                let rels = data.relationships || data.items || [];
                let added = 0;
                rels.forEach(function(rel) {
                    let srcId = rel.source_id || rel.source_element_id;
                    let tgtId = rel.target_id || rel.target_element_id;
                    if (!srcId || !tgtId) return;
                    if (!cellMap[srcId] || !cellMap[tgtId]) return;
                    let key = srcId + '-' + tgtId;
                    let reverseKey = tgtId + '-' + srcId;
                    if (existingPairs[key] || existingPairs[reverseKey]) return;
                    let link = createLink(cellMap[srcId], cellMap[tgtId], rel.type || rel.relationship_type || 'association', rel.id);
                    /* BUG-CMP-002: Populate relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) link.set('customLabel', rel.custom_label);
                    self.graph.addCell(link);
                    existingPairs[key] = true;
                    added++;
                });
                self.relCount = self.graph.getLinks().length;
                if (added > 0) {
                    _toast('success', 'Found ' + added + ' existing relationship(s)');
                    self.statusText = 'Added ' + added + ' catalogued relationship(s)';
                    self._pushUndo();
                } else {
                    _toast('info', 'No additional relationships found in catalog');
                    self.statusText = 'No new relationships to add';
                }
            })
            .catch(function(err) {
                _toast('error', 'Failed to scan relationships');
                self.statusText = 'Scan failed: ' + err.message;
            });
        },

        /* ── CMP2-003: Auto-detect relationships for a single newly-placed element ── */
        _autoDetectForElement: function(cell) {
            let self = this;
            const newEid = cell && cell.get ? cell.get('elementId') : null;
            if (!newEid) return;  /* unsaved element — skip */
            let elements = self.graph.getElements();
            let elementIds = elements.map(function(el) { return el.get('elementId'); }).filter(Boolean);
            if (elementIds.length < 2) return;  /* need at least 2 elements */

            let existingPairs = {};
            self.graph.getLinks().forEach(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                if (src && tgt && src.id && tgt.id) {
                    let srcEl = self.graph.getCell(src.id);
                    let tgtEl = self.graph.getCell(tgt.id);
                    if (srcEl && tgtEl) {
                        existingPairs[srcEl.get('elementId') + '-' + tgtEl.get('elementId')] = true;
                    }
                }
            });
            let cellMap = {};
            elements.forEach(function(el) {
                let eid = el.get('elementId');
                if (eid) cellMap[eid] = el;
            });

            Platform.fetch.get('/archimate/api/relationships', {
                per_page: 200,
                element_ids: elementIds.join(','),
            }, { silent: true })
                .then(function(data) {
                let rels = data.relationships || data.items || [];
                let added = 0;
                rels.forEach(function(rel) {
                    let srcId = rel.source_id || rel.source_element_id;
                    let tgtId = rel.target_id || rel.target_element_id;
                    if (!srcId || !tgtId) return;
                    /* Only draw relationships involving the newly-placed element */
                    if (srcId !== newEid && tgtId !== newEid) return;
                    if (!cellMap[srcId] || !cellMap[tgtId]) return;
                    let key = srcId + '-' + tgtId;
                    let reverseKey = tgtId + '-' + srcId;
                    if (existingPairs[key] || existingPairs[reverseKey]) return;
                    let link = createLink(cellMap[srcId], cellMap[tgtId], rel.type || rel.relationship_type || 'association', rel.id);
                    /* BUG-CMP-002: Populate relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) link.set('customLabel', rel.custom_label);
                    self.graph.addCell(link);
                    existingPairs[key] = true;
                    added++;
                });
                self.relCount = self.graph.getLinks().length;
                if (added > 0) {
                    _toast('success', 'Found ' + added + ' existing relationship(s)');
                    self.statusText = 'Auto-linked ' + added + ' catalogued relationship(s)';
                    self._pushUndo();
                }
            })
            /* swallow-ok: automatic relationship auto-detect the user never asked for — it runs on drop, adds links when it can, and claims nothing when it cannot */
            .catch(function() {});
        },

        /* CMP2-003: Debounced wrapper (500ms) to avoid hammering API on bulk imports */
        _autoDetectForElementDebounced: function(cell) {
            let self = this;
            if (self._autoDetectTimer) clearTimeout(self._autoDetectTimer);
            self._autoDetectTimer = setTimeout(function() {
                self._autoDetectForElement(cell);
            }, 500);
        },

        /* CMP2-003: Debounced bulk auto-detect (500ms) — for multi-element operations */
        _autoDetectBulkDebounced: function() {
            let self = this;
            if (self._autoDetectBulkTimer) clearTimeout(self._autoDetectBulkTimer);
            self._autoDetectBulkTimer = setTimeout(function() {
                self._autoDetectBulk();
            }, 500);
        },

        /* CMP2-003: Silent bulk auto-detect — like autoDetectRelationships but no "need 2 elements" toast */
        _autoDetectBulk: function() {
            let self = this;
            let elements = self.graph.getElements();
            let elementIds = elements.map(function(el) { return el.get('elementId'); }).filter(Boolean);
            if (elementIds.length < 2) return;

            let existingPairs = {};
            self.graph.getLinks().forEach(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                if (src && tgt && src.id && tgt.id) {
                    let srcEl = self.graph.getCell(src.id);
                    let tgtEl = self.graph.getCell(tgt.id);
                    if (srcEl && tgtEl) {
                        existingPairs[srcEl.get('elementId') + '-' + tgtEl.get('elementId')] = true;
                    }
                }
            });
            let cellMap = {};
            elements.forEach(function(el) {
                let eid = el.get('elementId');
                if (eid) cellMap[eid] = el;
            });

            Platform.fetch.get('/archimate/api/relationships', {
                per_page: 200,
                element_ids: elementIds.join(','),
            }, { silent: true })
                .then(function(data) {
                let rels = data.relationships || data.items || [];
                let added = 0;
                rels.forEach(function(rel) {
                    let srcId = rel.source_id || rel.source_element_id;
                    let tgtId = rel.target_id || rel.target_element_id;
                    if (!srcId || !tgtId) return;
                    if (!cellMap[srcId] || !cellMap[tgtId]) return;
                    let key = srcId + '-' + tgtId;
                    let reverseKey = tgtId + '-' + srcId;
                    if (existingPairs[key] || existingPairs[reverseKey]) return;
                    let link = createLink(cellMap[srcId], cellMap[tgtId], rel.type || rel.relationship_type || 'association', rel.id);
                    /* BUG-CMP-002: Populate relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) link.set('customLabel', rel.custom_label);
                    self.graph.addCell(link);
                    existingPairs[key] = true;
                    added++;
                });
                self.relCount = self.graph.getLinks().length;
                if (added > 0) {
                    _toast('success', 'Found ' + added + ' existing relationship(s)');
                    self.statusText = 'Auto-linked ' + added + ' catalogued relationship(s)';
                    self._pushUndo();
                }
            })
            /* swallow-ok: automatic relationship auto-detect the user never asked for — it runs on bulk import, adds links when it can, and claims nothing when it cannot */
            .catch(function() {});
        },

        /* ── CMP2-002: Bulk import from portfolio ───────────── */
        openBulkImport: function() {
            let self = this;
            if (self.mode !== 'edit') {
                _toast('info', 'Switch to Edit mode to import elements');
                return;
            }
            self.bulkImportOpen = true;
            self.bulkImportQuery = '';
            self.bulkImportResults = [];
            self.bulkImportSelected = {};
            self.bulkImportLoading = false;
            self.bulkImportLayerFilter = '';
        },

        searchBulkImport: function() {
            let self = this;
            let q = (self.bulkImportQuery || '').trim();
            if (q.length < 1 && !self.bulkImportLayerFilter) {
                self.bulkImportResults = [];
                return;
            }
            self.bulkImportLoading = true;
            Platform.fetch.get('/archimate/api/elements/search', {
                limit: 100,
                q: q.length > 0 ? q : undefined,
                layer: self.bulkImportLayerFilter || undefined,
                solution_id: self.solutionId || undefined,
            }, { silent: true })
                .then(function(data) {
                const items = data.elements || data.items || data.results || [];
                /* Mark items already on canvas as disabled */
                items.forEach(function(el) {
                    el._onCanvas = !!self.canvasElements[el.id];
                });
                self.bulkImportResults = items;
                self.bulkImportLoading = false;
            })
            .catch(function() {
                self.bulkImportResults = [];
                self.bulkImportLoading = false;
                _toast('error', 'Failed to search portfolio elements');
            });
        },

        toggleBulkSelect: function(id) {
            let sel = Object.assign({}, this.bulkImportSelected);
            if (sel[id]) {
                delete sel[id];
            } else {
                sel[id] = true;
            }
            this.bulkImportSelected = sel;
        },

        toggleBulkSelectAll: function() {
            let self = this;
            const selectable = self.bulkImportResults.filter(function(el) { return !el._onCanvas; });
            const allSelected = selectable.length > 0 && selectable.every(function(el) { return self.bulkImportSelected[el.id]; });
            let sel = {};
            if (!allSelected) {
                selectable.forEach(function(el) { sel[el.id] = true; });
            }
            self.bulkImportSelected = sel;
        },

        get bulkImportCount() {
            return Object.keys(this.bulkImportSelected || {}).length;
        },

        bulkImportAddToCanvas: function() {
            let self = this;
            const selectedIds = Object.keys(self.bulkImportSelected);
            if (selectedIds.length === 0) return;

            /* Gather selected element data from results */
            const toAdd = [];
            const resultMap = {};
            self.bulkImportResults.forEach(function(el) { resultMap[el.id] = el; });
            selectedIds.forEach(function(id) {
                const el = resultMap[id];
                if (el && !el._onCanvas) toAdd.push(el);
            });
            if (toAdd.length === 0) {
                _toast('info', 'All selected elements are already on canvas');
                self.bulkImportOpen = false;
                return;
            }

            /* Calculate grid layout from viewport center */
            const cols = Math.ceil(Math.sqrt(toAdd.length));
            const hSpacing = 240;
            const vSpacing = 160;
            const paper = self.paper;
            let cx = 400;
            let cy = 300;
            if (paper) {
                const area = paper.getComputedSize();
                const vp = paper.translate();
                const zoom = paper.scale().sx || 1;
                cx = (area.width / 2 - vp.tx) / zoom;
                cy = (area.height / 2 - vp.ty) / zoom;
            }
            /* Offset so the grid is centered on the viewport center */
            const totalW = (cols - 1) * hSpacing;
            const totalRows = Math.ceil(toAdd.length / cols);
            const totalH = (totalRows - 1) * vSpacing;
            let startX = cx - totalW / 2;
            const startY = cy - totalH / 2;

            self.graph.startBatch('bulk-import');
            toAdd.forEach(function(el, idx) {
                const row = Math.floor(idx / cols);
                const col = idx % cols;
                let x = startX + col * hSpacing;
                let y = startY + row * vSpacing;
                let layer = (el.layer || '').toLowerCase() || 'application';
                const node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer, x, y);
                self.graph.addCell(node);
                self.canvasElements[el.id] = { id: el.id, name: el.name, type: el.type, layer: layer };
            });
            self.graph.stopBatch('bulk-import');

            self.elementCount = self.graph.getElements().length;
            self.viewpointDirty = true;
            self.bulkImportOpen = false;
            _toast('success', 'Added ' + toAdd.length + ' element(s) to canvas');
            self.statusText = 'Imported ' + toAdd.length + ' element(s) from portfolio';

            /* Auto-detect relationships for the newly imported elements */
            if (typeof self._autoDetectBulkDebounced === 'function') {
                self._autoDetectBulkDebounced();
            }

            if (typeof self._pushUndo === 'function') {
                self._pushUndo();
            }
        },

        };

        return methods;
    }

    return { getMethods: getMethods };
})();
