/**
 * CMP-032: Composer Persistence Module (IIFE)
 *
 * Extracted from composerApp() — all save/load/export features:
 * - Save/load viewpoints (CRUD)
 * - CMP-015: Version Snapshots
 * - CMP-015: Diagram Templates
 * - CMP-028: Auto-persist (localStorage)
 * - Export: PNG, PDF, SVG, PPTX (CMP2-005)
 * - Link element to solution
 *
 * Usage: ComposerPersistence.install(ctx, helpers) in composerApp().init()
 */
let ComposerPersistence = (function() {
    'use strict';

    function getMethods(helpers) {
        let csrfToken = helpers.csrfToken;
        let createNode = helpers.createNode;
        let createLink = helpers.createLink;
        let createContainerNode = helpers.createContainerNode;
        let isContainerType = helpers.isContainerType;
        let layerColor = helpers.layerColor;
        let guessLayer = helpers.guessLayer;
        let _toast = helpers._toast;
        let createLayerZone = helpers.createLayerZone;
        let createAnnotation = helpers.createAnnotation;
        let UndoStack = helpers.UndoStack;
        let applyCustomLabel = helpers.applyCustomLabel;
        let applyImportedElementPresentation = helpers.applyImportedElementPresentation;

        function _customPropsObjectToArray(props) {
            let arr = [];
            Object.keys(props || {}).forEach(function(key) {
                let value = props[key];
                if (value === undefined || value === null || value === '') return;
                arr.push({ key: key, value: String(value) });
            });
            return arr;
        }

        function _customPropsArrayToObject(arr) {
            let obj = {};
            (arr || []).forEach(function(item) {
                if (!item || !item.key) return;
                obj[item.key] = item.value;
            });
            return obj;
        }

        function _rememberImportedElementProps(self, element) {
            if (!element || !element.id) return;
            let existing = _customPropsArrayToObject(self.customProperties[String(element.id)] || []);
            let merged = Object.assign({}, existing, element.custom_properties || {});
            if (element.rendering_mode) {
                merged.lucid_rendering_mode = element.rendering_mode;
            }
            if (!Object.keys(merged).length) return;
            self.customProperties[String(element.id)] = _customPropsObjectToArray(merged);
            self.customProperties = Object.assign({}, self.customProperties);
        }

        function _applySavedImportedPresentation(self, cellMap, elementMap) {
            Object.keys(cellMap || {}).forEach(function(elementId) {
                let cell = cellMap[elementId];
                if (!cell) return;
                let savedProps = _customPropsArrayToObject(self.customProperties[String(elementId)] || []);
                if (!savedProps.lucid_rendering_mode && elementMap[elementId] && elementMap[elementId].rendering_mode) {
                    savedProps.lucid_rendering_mode = elementMap[elementId].rendering_mode;
                }
                applyImportedElementPresentation(cell, savedProps);
            });
        }

        function _resetImportCanvas(self) {
            self.selectedNode = null;
            self.selectedEdge = null;
            if (typeof self._clearNeighborFocus === 'function') self._clearNeighborFocus();
            if (typeof self._clearSelection === 'function') self._clearSelection();
            self.graph.clear();
            self.canvasElements = {};
            self.customProperties = {};
            self.layerZoneCells = [];
            self.elementCount = 0;
            self.relCount = 0;
        }

        function _formatLucidchartFileSize(bytes) {
            let size = Number(bytes || 0);
            if (size < 1024) return size + ' B';
            if (size < 1024 * 1024) return (size / 1024).toFixed(1).replace(/\.0$/, '') + ' KB';
            return (size / (1024 * 1024)).toFixed(1).replace(/\.0$/, '') + ' MB';
        }

        function _setDisabled(el, disabled) {
            if (!el) return;
            if (disabled) {
                el.setAttribute('disabled', 'disabled');
                el.classList.add('opacity-60', 'cursor-not-allowed', 'pointer-events-none');
            } else {
                el.removeAttribute('disabled');
                el.classList.remove('opacity-60', 'cursor-not-allowed', 'pointer-events-none');
            }
        }

        function _escapeHtml(value) {
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        /* Serialize layer zones + annotations + custom properties into description blob */
        function _serializeCanvasExt(self) {
            let ext = { _canvas_ext: 1 };
            let hasData = false;

            if (self.layerZoneCells && self.layerZoneCells.length) {
                ext.swimlanes = self.layerZoneCells.map(function(cell) {
                    let p = cell.position(); let s = cell.size();
                    let sz = { layer: cell.get('zoneLayer'), x: Math.round(p.x), y: Math.round(p.y), width: s.width, height: s.height };
                    /* A canvas zone carries which box it is, so the restore
                       path below can round-trip it through createLayerZone's
                       zoneDef argument. Absent (not an empty string) on
                       every non-canvas swimlane, so JSON.stringify drops the
                       key and today's saved viewpoints serialize exactly as
                       before. */
                    let boxKey = cell.get('boxKey');
                    if (boxKey) { sz.box_key = boxKey; }
                    return sz;
                });
                hasData = true;
            }

            let annotCells = self.graph.getElements().filter(function(c) { return c.get('isAnnotation'); });
            if (annotCells.length) {
                ext.annotations = annotCells.map(function(c) {
                    let p = c.position(); let s = c.size();
                    return { text: c.get('annotText') || '', x: Math.round(p.x), y: Math.round(p.y), w: s.width, h: s.height };
                });
                hasData = true;
            }

            if (self.customProperties && Object.keys(self.customProperties).length) {
                ext.custom_properties = self.customProperties;
                hasData = true;
            }

            /* ENT-104: persist per-element colour overrides */
            let fills = {};
            self.graph.getElements().forEach(function(cell) {
                let cf = cell.get('customFill');
                let id = cell.get('elementId');
                if (cf && id) { fills[id] = cf; }
            });
            if (Object.keys(fills).length) {
                ext.custom_fills = fills;
                hasData = true;
            }

            /* CMP2-001: persist per-element state (current/target/both) */
            let states = {};
            self.graph.getElements().forEach(function(cell) {
                let st = cell.get('elState');
                let id = cell.get('elementId');
                if (st && id && st !== 'both') { states[id] = st; }
            });
            if (Object.keys(states).length) {
                ext.element_states = states;
                hasData = true;
            }

            return hasData ? JSON.stringify(ext) : null;
        }

        /* Legacy alias for backward-compat */
        function _serializeZones(self) { return _serializeCanvasExt(self); }

        /* BA-01: the canvas emits one entry per CELL, and two cells can reference
           the same element or relationship (routine on an AI-generated diagram).
           saved_diagram_elements / saved_diagram_relationships each carry a
           UNIQUE(diagram_id, <entity>_id), so an un-deduplicated payload was
           rejected by the database and the autosave PUT 500'd — then retried the
           same payload forever, so the work was never persisted. Last entry wins:
           the list is built by walking the graph, so the last occurrence is the
           most recently touched cell, i.e. the position the user just dragged to.
           The server dedupes too; this is belt and braces so the request is
           well-formed before it leaves the browser. */
        function _dedupeById(items, key) {
            let byId = {};
            let order = [];
            (items || []).forEach(function(item) {
                if (!item) return;
                let id = item[key];
                if (id === null || id === undefined || id === '') return;
                let k = String(id);
                if (!(k in byId)) order.push(k);
                byId[k] = item;
            });
            return order.map(function(k) { return byId[k]; });
        }

        /* BA-01: turn a rejected save into something the user can act on.
           The old handler was `.catch(function() {` — it discarded the error
           object entirely, which is why a permanently-failing payload looked
           like nothing at all. */
        function _saveErrorDetail(err) {
            if (!err) return '';
            let data = err.data || err.body || null;
            let msg = (data && (data.error || data.detail)) || err.message || '';
            return String(msg).slice(0, 200);
        }

        let methods = {

        _autoSave: function() {
            /* C-03: this used to no-op ("return") whenever currentSavedVpId
             * was unset — i.e. for every diagram the user had not yet
             * manually named via Save. Those diagrams got ZERO server
             * persistence; the only backing store was a single localStorage
             * slot with no device sync and no locking. Autosave now creates
             * the SavedDiagram row itself on first autosave tick so a
             * diagram is server-persisted from the moment it has content,
             * not from the moment the user remembers to click Save.
             * localStorage remains an additional crash-recovery fallback
             * (see the 10s snapshot below), never the system of record. */
            let self = this;
            if (!self.viewpointDirty || self._creatingAutosave) return;
            /* BA-01: a payload the server rejected on its merits (4xx) will be
               rejected identically on every retry. Stop the loop rather than
               burning ticks on a save that cannot succeed. The user has been
               toasted with the server's reason and pointed at Save; a
               successful manual Save clears the block. */
            if (self._autoSaveBlocked) return;

            let elements = self.graph.getElements().filter(function(c) { return !c.get('isLayerZone') && !c.get('isAnnotation'); });
            if (elements.length === 0) return;

            if (!self.currentSavedVpId) {
                self._autoCreateSavedDiagram(elements);
                return;
            }

            let elData = elements.map(function(cell) {
                let pos = cell.position();
                let size = cell.size();
                let item = {
                    element_id: cell.get('elementId'),
                    x: Math.round(pos.x), y: Math.round(pos.y),
                    width: size.width, height: size.height,
                    rendering_mode: cell.get('renderingMode') || 'black_box',
                };
                /* GAP-INT-002: Persist zone type for Grouping/Location */
                if (cell.get('zoneType') && cell.get('zoneType') !== 'default') {
                    item.zone_type = cell.get('zoneType');
                }
                return item;
            }).filter(function(e) { return e.element_id; });
            elData = _dedupeById(elData, 'element_id');

            let relData = self.graph.getLinks().map(function(link) {
                let relId = link.get('relId');
                if (!relId) return null;
                return {
                    relationship_id: relId,
                    waypoints: link.vertices() || null,
                    routing_style: link.get('routingStyle') || 'manhattan',
                    label: link.get('customLabel') || null,
                };
            }).filter(function(r) { return r; });
            relData = _dedupeById(relData, 'relationship_id');

            let payload = {
                name: self.activeViewpointName || 'My Viewpoint',
                viewpoint_type: self.activeViewpoint || null,
                solution_id: self.solutionId || null,
                elements: elData,
                relationships: relData,
                description: _serializeZones(self),
            };

            self._saving = true;
            self._saveFailed = false;
            Platform.fetch.put('/archimate/api/saved-viewpoints/' + self.currentSavedVpId, payload, { silent: true })
            .then(function(data) {
                self._saving = false;
                if (data.id) {
                    self.viewpointDirty = false;
                    self.lastSavedAt = Date.now();
                    self._autosaveLabel = 'just now';
                    self._saveFailed = false;
                    self._autoSaveFailCount = 0;
                    self._autoSaveBlocked = false;
                    self._saveErrorDetail = '';
                    // Server copy is current — drop the redundant local snapshot so a
                    // deleted-then-restored diagram cannot re-materialise (see saveViewpoint).
                    self._clearAutosave();
                }
            })
            .catch(function(err) {
                self._saving = false;
                self._saveFailed = true;
                self._autoSaveFailCount = (self._autoSaveFailCount || 0) + 1;
                let status = err && err.status;
                let detail = _saveErrorDetail(err);
                self._saveErrorDetail = detail;
                /* BA-01: a 4xx means the server understood the request and
                   refused it — retrying the same payload is guaranteed to fail
                   the same way, which is how autosave used to fail forever
                   while saying only "retrying...". Escalate immediately, say
                   what the server said, and stop the loop. */
                if (status >= 400 && status < 500) {
                    self._autoSaveBlocked = true;
                    _toast('error', 'Auto-save rejected — your changes are NOT saved. '
                        + (detail || 'The server refused this diagram.')
                        + ' Use Save to retry once the diagram is corrected.');
                } else if (self._autoSaveFailCount === 3) {
                    _toast('warning', 'Changes not saved — retrying...');
                } else if (self._autoSaveFailCount > 3) {
                    _toast('error', 'Auto-save failed after multiple attempts'
                        + (detail ? ' — ' + detail : ''));
                }
            });
        },

        /* C-03: creates the SavedDiagram row the first time an unsaved
         * canvas has content, so autosave is server-side from the start
         * instead of requiring a manual "Save" first. Uses a generated
         * name (never silently invented data — it is clearly labelled
         * "Unsaved" and the user can rename via Save at any time). */
        _autoCreateSavedDiagram: function(elements) {
            let self = this;
            self._creatingAutosave = true;

            let elData = elements.map(function(cell) {
                let pos = cell.position();
                let size = cell.size();
                return {
                    element_id: cell.get('elementId'),
                    name: cell.get('elName') || '',
                    el_type: cell.get('elType') || '',
                    layer: cell.get('elLayer') || '',
                    x: Math.round(pos.x), y: Math.round(pos.y),
                    width: size.width, height: size.height,
                    rendering_mode: cell.get('renderingMode') || 'black_box',
                };
            }).filter(function(e) { return e.element_id; });
            elData = _dedupeById(elData, 'element_id');

            let relData = self.graph.getLinks().map(function(link) {
                let relId = link.get('relId');
                if (!relId) return null;
                let srcCell = self.graph.getCell((link.get('source') || {}).id);
                let tgtCell = self.graph.getCell((link.get('target') || {}).id);
                return {
                    relationship_id: relId,
                    source_element_id: srcCell ? srcCell.get('elementId') : null,
                    target_element_id: tgtCell ? tgtCell.get('elementId') : null,
                    rel_type: link.get('relType') || 'association',
                    waypoints: link.vertices() || null,
                    routing_style: link.get('routingStyle') || 'manhattan',
                    label: link.get('customLabel') || null,
                };
            }).filter(function(r) { return r; });
            relData = _dedupeById(relData, 'relationship_id');

            let stamp = new Date().toLocaleString();
            let payload = {
                name: self.activeViewpointName || ('Unsaved diagram — ' + stamp),
                viewpoint_type: self.activeViewpoint || null,
                solution_id: self.solutionId || null,
                elements: elData,
                relationships: relData,
                description: _serializeZones(self),
            };

            Platform.fetch.post('/archimate/api/saved-viewpoints', payload, { silent: true })
            .then(function(data) {
                self._creatingAutosave = false;
                if (data && data.id) {
                    self.currentSavedVpId = data.id;
                    self.activeViewpointName = payload.name;
                    self.viewpointDirty = false;
                    self.lastSavedAt = Date.now();
                    self._autosaveLabel = 'just now';
                    self._saveFailed = false;
                    self._autoSaveFailCount = 0;
                    self._autoSaveBlocked = false;
                    self._saveErrorDetail = '';
                    // Now persisted server-side under a real id; the local snapshot has
                    // served its purpose and must not linger to re-create a deleted row.
                    self._clearAutosave();
                }
            })
            .catch(function(err) {
                self._creatingAutosave = false;
                self._saveFailed = true;
                self._autoSaveFailCount = (self._autoSaveFailCount || 0) + 1;
                let status = err && err.status;
                let detail = _saveErrorDetail(err);
                self._saveErrorDetail = detail;
                /* BA-01: see _autoSave — a 4xx will never succeed on retry. */
                if (status >= 400 && status < 500) {
                    self._autoSaveBlocked = true;
                    _toast('error', 'Auto-save rejected — your work is only in this browser. '
                        + (detail || 'The server refused this diagram.'));
                } else if (self._autoSaveFailCount === 1) {
                    _toast('warning', 'Could not save your work to the server — keeping a local browser backup only. Retrying...'
                        + (detail ? ' (' + detail + ')' : ''));
                }
            });
        },

        saveViewpoint: function() {
            let self = this;
            let elements = self.graph.getElements().filter(function(c) { return !c.get('isLayerZone') && !c.get('isAnnotation'); });
            if (elements.length === 0) {
                _toast('info', 'Add elements to the canvas before saving.');
                return;
            }

            let defaultName = self.currentSavedVpId
                ? (self.activeViewpointName || 'My Viewpoint')
                : 'My Viewpoint';

            /* Open the save-name modal instead of native prompt */
            self.saveNameValue = defaultName;
            self.saveNameOpen = true;
            self._saveNameCallback = function(name) {
                let elData = elements.map(function(cell) {
                    let pos = cell.position();
                    let size = cell.size();
                    let elItem = {
                        element_id: cell.get('elementId'),
                        // identity fields so the server can materialize imported
                        // (string-id) elements as real ArchiMateElement rows
                        name: cell.get('elName') || '',
                        el_type: cell.get('elType') || '',
                        layer: cell.get('elLayer') || '',
                        x: Math.round(pos.x),
                        y: Math.round(pos.y),
                        width: size.width,
                        height: size.height,
                        rendering_mode: cell.get('renderingMode') || 'black_box',
                    };
                    /* GAP-INT-002: Persist zone type for Grouping/Location */
                    if (cell.get('zoneType') && cell.get('zoneType') !== 'default') {
                        elItem.zone_type = cell.get('zoneType');
                    }
                    return elItem;
                }).filter(function(e) { return e.element_id; });
                elData = _dedupeById(elData, 'element_id');

                /* Collect relationship data with waypoints */
                let relData = self.graph.getLinks().map(function(link) {
                    let relId = link.get('relId');
                    if (!relId) return null;
                    let vertices = link.vertices() || [];
                    let srcCell = self.graph.getCell((link.get('source') || {}).id);
                    let tgtCell = self.graph.getCell((link.get('target') || {}).id);
                    return {
                        relationship_id: relId,
                        // endpoints + type so the server can materialize imported
                        // (string-id) relationships as real ArchiMateRelationship rows
                        source_element_id: srcCell ? srcCell.get('elementId') : null,
                        target_element_id: tgtCell ? tgtCell.get('elementId') : null,
                        rel_type: link.get('relType') || 'association',
                        waypoints: vertices.length > 0 ? vertices : null,
                        routing_style: link.get('routingStyle') || 'manhattan',
                        label: link.get('customLabel') || null,
                    };
                }).filter(function(r) { return r; });
                relData = _dedupeById(relData, 'relationship_id');

                let payload = {
                    name: name.trim(),
                    viewpoint_type: self.activeViewpoint || null,
                    solution_id: self.solutionId || null,
                    elements: elData,
                    relationships: relData,
                    description: _serializeZones(self),
                };

                let method = 'POST';
                let url = '/archimate/api/saved-viewpoints';
                if (self.currentSavedVpId) {
                    method = 'PUT';
                    url = '/archimate/api/saved-viewpoints/' + self.currentSavedVpId;
                }

                self.statusText = 'Saving...';

                let saveCall = method === 'PUT' ? Platform.fetch.put : Platform.fetch.post;
                saveCall(url, payload, { silent: true })
                .then(function(data) {
                    if (data.id) {
                        // Server materialized imported items into real model rows —
                        // adopt the new DB ids on the canvas so a re-save updates
                        // rather than duplicating.
                        if (data.element_id_map) {
                            self.graph.getElements().forEach(function(cell) {
                                let mapped = data.element_id_map[String(cell.get('elementId'))];
                                if (mapped) cell.set('elementId', mapped);
                            });
                            Object.keys(data.element_id_map).forEach(function(oldId) {
                                if (self.canvasElements[oldId]) {
                                    let entry = self.canvasElements[oldId];
                                    entry.id = data.element_id_map[oldId];
                                    self.canvasElements[data.element_id_map[oldId]] = entry;
                                    delete self.canvasElements[oldId];
                                }
                            });
                        }
                        if (data.relationship_id_map) {
                            self.graph.getLinks().forEach(function(link) {
                                let mapped = data.relationship_id_map[String(link.get('relId'))];
                                if (mapped) link.set('relId', mapped);
                            });
                        }
                        self.currentSavedVpId = data.id;
                        self.activeTabId = data.id;
                        self.activeViewpointName = data.name || name.trim();
                        self.viewpointDirty = false;
                        self.lastSavedAt = Date.now();
                        self._autosaveLabel = 'just now';
                        self.statusText = 'Saved: ' + (data.name || name.trim());
                        /* BA-01: a successful save clears the autosave block so
                           the background loop resumes on the next edit. */
                        self._autoSaveBlocked = false;
                        self._autoSaveFailCount = 0;
                        self._saveFailed = false;
                        self._saveErrorDetail = '';
                        // CMP: the diagram is now safely in the DB, so the localStorage
                        // crash-recovery snapshot is redundant. Clearing it here stops a
                        // later "restore auto-save" from resurrecting a diagram that was
                        // subsequently deleted (which re-POSTed it as a brand-new row).
                        self._clearAutosave();
                        self.loadViewpointTabs();
                    } else {
                        self.statusText = 'Save failed: ' + (data.error || 'unknown');
                    }
                })
                .catch(function(err) {
                    /* BA-01: report what the server actually said. err.message is
                       often just "HTTP 400"; the reason is in err.data.error. */
                    let detail = _saveErrorDetail(err);
                    self._saveErrorDetail = detail;
                    self.statusText = 'Save error: ' + (detail || 'unknown');
                    _toast('error', 'Failed to save viewpoint' + (detail ? ' — ' + detail : ''));
                });
            };
        },

        confirmSaveName: function() {
            let name = (this.saveNameValue || '').trim();
            if (!name) return;
            this.saveNameOpen = false;
            if (this._saveNameCallback) { this._saveNameCallback(name); this._saveNameCallback = null; }
        },

        cancelSaveName: function() {
            this.saveNameOpen = false;
            this._saveNameCallback = null;
            this.statusText = 'Save paused';
        },

        loadSavedViewpoints: function() {
            let self = this;
            self.savedVpOpen = !self.savedVpOpen;
            if (!self.savedVpOpen) return;

            let url = '/archimate/api/saved-viewpoints';
            if (self.solutionId) url += '?solution_id=' + self.solutionId;

            Platform.fetch.get(url, null, { silent: true })
            .then(function(data) {
                self.savedViewpoints = data.viewpoints || [];
            })
            .catch(function() {
                self.savedViewpoints = [];
                _toast('error', 'Failed to load saved viewpoints');
            });
        },

        /* CMP-02: delete a saved viewpoint from the picker. The backend
           DELETE /api/saved-viewpoints/<id> already existed; the UI simply had
           no affordance to reach it, so saved views accumulated with no way to
           remove them. */
        deleteSavedViewpoint: async function(vpId, vpName) {
            let self = this;
            if (!vpId) return;
            let label = vpName || 'this viewpoint';
            let ok = await Platform.modal.confirm({
                title: 'Delete viewpoint?',
                message: 'Delete "' + label + '"? Its diagram layout is removed permanently. '
                    + 'Elements stay in the catalog. This cannot be undone.',
                confirmText: 'Delete viewpoint',
                cancelText: 'Keep it',
                variant: 'destructive',
            });
            if (!ok) return;

            Platform.fetch.delete('/archimate/api/saved-viewpoints/' + vpId, { silent: true })
            .then(function() {
                /* Drop it from every in-memory list so the UI reflects the delete
                   without a round-trip. */
                self.savedViewpoints = (self.savedViewpoints || []).filter(function(v) { return v.id !== vpId; });
                self.viewpointTabs = (self.viewpointTabs || []).filter(function(v) { return v.id !== vpId; });
                /* If the deleted view is the one open on the canvas, detach it so
                   the user isn't editing a diagram that no longer exists. */
                if (self.currentSavedVpId === vpId) {
                    self.currentSavedVpId = null;
                    self.activeViewpointName = '';
                    self.activeTabId = null;
                    self.viewpointDirty = false;
                    /* Clear the canvas too. Detaching the id but leaving the
                       elements on screen is exactly how a delete "comes back":
                       the next edit finds a dirty canvas with no currentSavedVpId
                       and _autoSave POSTs a brand-new "Unsaved diagram" row from
                       the same content, so the deletion looks undone. The
                       elements stay in the catalog; only this view is gone. */
                    if (self.graph) self.graph.clear();
                    self.canvasElements = {};
                    self.elementCount = 0;
                    self.relCount = 0;
                    self._clearAutosave();
                }
                _toast('success', 'Viewpoint "' + label + '" deleted');
            })
            .catch(function() {
                _toast('error', 'Failed to delete viewpoint "' + label + '"');
            });
        },

        /* CMP-09: close a workspace tab. The old inline handler only filtered
           viewpointTabs client-side, so an auto-created "Unsaved diagram — <ts>"
           scratch row stayed on the server and reappeared on the next
           loadViewpointTabs() — the lingering tab the QA pass saw, and why empty
           scratch diagrams accumulated. Closing a scratch tab now deletes its
           server row; closing a named/saved viewpoint just detaches the tab. */
        closeViewpointTab: async function(tab) {
            let self = this;
            if (!tab || tab.id == null) return;
            if (self.viewpointDirty && self.activeTabId === tab.id) {
                if (!(await Platform.modal.confirm('This tab has unsaved changes. Close anyway?'))) return;
            }
            let isScratch = typeof tab.name === 'string' && tab.name.indexOf('Unsaved diagram') === 0;

            self.viewpointTabs = (self.viewpointTabs || []).filter(function(t) { return t.id !== tab.id; });

            /* Delete the server row for an unsaved scratch diagram so it does not
               reappear on reload. Named viewpoints are preserved. */
            if (isScratch) {
                Platform.fetch.delete('/archimate/api/saved-viewpoints/' + tab.id, { silent: true })
                    .catch(function() { /* swallow-ok: tab already removed from the strip; a failed scratch cleanup is not worth interrupting the user, and loadViewpointTabs will simply show it again */ });
            }

            if (self.activeTabId === tab.id) {
                if (self.viewpointTabs.length) {
                    self.activeTabId = self.viewpointTabs[0].id;
                    self.loadSavedViewpoint(self.viewpointTabs[0].id, self.viewpointTabs[0].name);
                } else {
                    self.activeTabId = null;
                    self.newDiagram();
                }
            }
        },

        /* ENT-107: Populate the viewpoint tab strip with most recent viewpoints only. */
        loadViewpointTabs: function() {
            let self = this;
            let url = '/archimate/api/saved-viewpoints';
            if (self.solutionId) url += '?solution_id=' + self.solutionId;
            Platform.fetch.get(url, null, { silent: true })
            .then(function(data) {
                /* Limit to 5 most recent viewpoints to avoid tab overflow */
                let all = (data.viewpoints || []).map(function(v) {
                    return { id: v.id, name: v.name, viewpoint_type: v.viewpoint_type || '' };
                });
                self.viewpointTabs = all.slice(0, 5);
            })
            .catch(function() { /* swallow-ok: the tab strip is a convenience shortcut; loadSavedViewpoints() fetches the same endpoint for the primary list and toasts its own failure, so reporting here would double-toast */ });
        },

        loadSavedViewpoint: function(vpId, vpName) {
            let self = this;
            self.savedVpOpen = false;
            self.viewpointLoading = true;
            self.selectedNode = null;
            self.selectedEdge = null;
            self._clearNeighborFocus();

            Platform.fetch.get('/archimate/api/saved-viewpoints/' + vpId, null, { silent: true })
            .then(function(data) {
                UndoStack.pause();
                self.graph.clear();
                self.canvasElements = {};

                let elements = data.elements || [];
                let relationships = data.relationships || [];

                if (elements.length === 0) {
                    self.elementCount = 0;
                    self.relCount = 0;
                    self.currentSavedVpId = vpId;
                    self.activeTabId = vpId;
                    self.activeViewpointName = data.name || vpName;
                    self.viewpointLoading = false;
                    self.statusText = 'Empty viewpoint';
                    UndoStack.resume();
                    UndoStack.clear();
                    return;
                }

                let cellMap = {};
                let elementMap = {};
                elements.forEach(function(el) {
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let renderMode = el.rendering_mode || 'black_box';
                    let node;
                    if (renderMode === 'white_box' && isContainerType(el.type)) {
                        node = createContainerNode(el.id, el.name, el.type || 'ApplicationComponent', layer,
                                                   el.x || 0, el.y || 0, el.width || 320, el.height || 220);
                    } else {
                        node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer,
                                          el.x || 0, el.y || 0);
                        /* Auto-migrate pre-v4 nodes saved at the old 200×70 default */
                        if (el.width && el.height && el.height > 75) {
                            node.resize(el.width, el.height);
                        }
                    }
                    /* GAP-INT-002: Restore zone type for Grouping/Location */
                    if (el.zone_type) {
                        node.set('zoneType', el.zone_type);
                    }
                    self.graph.addCell(node);
                    cellMap[el.id] = node;
                    elementMap[el.id] = el;
                    self.canvasElements[el.id] = el;
                });

                /* Restore parent-child embedding from composition/aggregation relationships */
                relationships.forEach(function(rel) {
                    let srcCell = cellMap[rel.source_id];
                    let tgtCell = cellMap[rel.target_id];
                    if (!srcCell || !tgtCell) return;
                    let link = createLink(srcCell, tgtCell, rel.type || 'association', rel.id);
                    /* Restore saved waypoints if present */
                    if (rel.waypoints && Array.isArray(rel.waypoints) && rel.waypoints.length > 0) {
                        link.vertices(rel.waypoints);
                    }
                    /* Restore custom annotation label if present */
                    if (rel.label) {
                        link.set('customLabel', rel.label);
                        applyCustomLabel(link, rel.label);
                    }
                    /* BUG-CMP-002: Restore relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) {
                        link.set('customLabel', rel.custom_label);
                        applyCustomLabel(link, rel.custom_label);
                    }
                    /* Sequence View step number (see composer.js layoutSequence) */
                    if (rel.sequence_order != null) link.set('sequenceOrder', rel.sequence_order);
                    if (rel.created_at) link.set('createdAt', Date.parse(rel.created_at) || 0);
                    /* Restore saved routing style if not manhattan (default) */
                    let savedRouting = rel.routing_style || 'manhattan';
                    if (savedRouting === 'smooth' || savedRouting === 'normal') {
                        link.router('normal');
                        link.connector('smooth');
                        link.set('routingStyle', savedRouting);
                    }
                    self.graph.addCell(link);

                    /* Auto-embed for composition/aggregation */
                    let rType = (rel.type || '').toLowerCase();
                    if ((rType === 'composition' || rType === 'aggregation') && srcCell.get('renderingMode') === 'white_box') {
                        srcCell.embed(tgtCell);
                    }
                });

                self.elementCount = elements.length;
                self.relCount = relationships.length;
                self.currentSavedVpId = vpId;
                self.activeTabId = vpId;
                self.activeViewpointName = data.name || vpName;
                self.activeViewpoint = data.viewpoint_type || null;
                self.mode = 'edit';
                self.viewpointLoading = false;
                self.viewpointDirty = false;
                /* GAP-CMP-007: Reset review status on viewpoint load */
                self.viewpointReviewStatus = '';
                UndoStack.resume();
                UndoStack.clear();

                /* CMP-039: Restore layer zones from description canvas extension */
                self.layerZoneCells = [];
                self.layerZonesActive = false;
                let ext = null;
                /* description is plain free text on older viewpoints (pre-canvas-ext) — a
                   parse failure there is expected, not an error, so ext just stays null and
                   the optional swimlane/annotation/property restores below are skipped. */
                try { if (data.description) ext = JSON.parse(data.description); } catch (e) { /* swallow-ok: description is plain free text on pre-canvas-extension viewpoints, so a parse failure is the expected legacy case rather than an error */ }
                if (ext && ext._canvas_ext && Array.isArray(ext.swimlanes) && ext.swimlanes.length) {
                    ext.swimlanes.forEach(function(sz) {
                        /* A serialized box_key round-trips through
                           createLayerZone's zoneDef argument so a restored
                           canvas zone still carries which box it is; a plain
                           (pre-canvas) swimlane has no box_key and restores
                           exactly as before. */
                        let zoneDef = sz.box_key ? { box_key: sz.box_key } : undefined;
                        let zone = createLayerZone(sz.layer, sz.x, sz.y, sz.width || 1400, sz.height || 160, zoneDef);
                        self.graph.addCell(zone);
                        zone.toBack();
                        self.layerZoneCells.push(zone);
                    });
                    self.layerZonesActive = true;
                }

                /* CMP-052: Restore free-form annotations */
                if (ext && ext._canvas_ext && Array.isArray(ext.annotations) && ext.annotations.length) {
                    ext.annotations.forEach(function(a) {
                        let annot = createAnnotation(a.x || 0, a.y || 0, a.text || '', a.w || 180, a.h || 100);
                        self.graph.addCell(annot);
                    });
                    self.annotationCells = self.graph.getElements().filter(function(c) { return c.get('isAnnotation'); });
                }

                /* CMP-043: Restore custom properties */
                if (ext && ext._canvas_ext && ext.custom_properties) {
                    self.customProperties = ext.custom_properties;
                } else {
                    self.loadCustomPropsFromStorage();
                }
                _applySavedImportedPresentation(self, cellMap, elementMap);

                /* ENT-104: Restore per-element colour overrides */
                if (ext && ext._canvas_ext && ext.custom_fills) {
                    Object.keys(ext.custom_fills).forEach(function(eid) {
                        const fill = ext.custom_fills[eid];
                        const cell = cellMap[eid];
                        if (cell && fill) {
                            cell.attr('body/fill', fill);
                            cell.set('customFill', fill);
                        }
                    });
                }

                /* CMP2-001: Restore per-element state (current/target/both) */
                if (ext && ext._canvas_ext && ext.element_states) {
                    Object.keys(ext.element_states).forEach(function(eid) {
                        let st = ext.element_states[eid];
                        let cell = cellMap[eid];
                        if (cell && st) { cell.set('elState', st); }
                    });
                }

                self.statusText = 'Loaded: ' + (data.name || vpName);
                /* ENT-111: Check for stale relationships after loading a viewpoint */
                self.checkRelationshipHealth();

                self.$nextTick(function() {
                    self.fitCanvas();
                    self.refreshMaturityOverlay();
                    /* CMP2-001: Re-apply state overlay after loading viewpoint */
                    if (self.stateViewMode !== 'all') { self.applyStateOverlay(); }
                    if (window.lucide) lucide.createIcons();
                });
            })
            .catch(function(err) {
                _toast('error', 'Failed to load viewpoint — try clicking the tab again');
                self.graph.clear();
                self.canvasElements = {};
                self.elementCount = 0;
                self.relCount = 0;
                self.viewpointLoading = false;
                self.statusText = 'Load error — click tab to retry';
            });
        },

        linkElementToSolution: function(elementId) {
            if (!this.solutionId) return;
            Platform.fetch.post('/solutions/' + this.solutionId + '/archimate-elements', {
                element_id: elementId, element_role: 'primary'
            }, { silent: true }).catch(function() { _toast('error', 'Failed to link element to solution'); });
        },

        restoreAutosave: function() {
            if (!this._pendingAutosaveRestore) return;
            try {
                let data = JSON.parse(this._pendingAutosaveRestore);
                if (data.graph) {
                    this.graph.fromJSON(data.graph);
                    this.elementCount = this.graph.getElements().length;
                    this.relCount = this.graph.getLinks().length;
                    // Re-adopt the saved diagram's id (when the snapshot carried one) so
                    // the next save PUTs to that row instead of creating a duplicate.
                    if (data.currentSavedVpId) this.currentSavedVpId = data.currentSavedVpId;
                    _toast('success', 'Restored ' + this.elementCount + ' elements from auto-save');
                }
            } catch (e) {
                _toast('error', 'Failed to restore auto-save');
            }
            this._pendingAutosaveRestore = null;
            this._showAutosavePrompt = false;
        },

        discardAutosave: function() {
            let key = 'composer_autosave_' + (this.solutionId || 'scratch');
            /* Best-effort cleanup — if storage is unavailable there was nothing
               persisted to discard in the first place. */
            try { localStorage.removeItem(key); } catch(_) { /* swallow-ok: discarding an autosave that storage could not have held in the first place */ }
            this._pendingAutosaveRestore = null;
            this._showAutosavePrompt = false;
        },

        _clearAutosave: function() {
            let key = 'composer_autosave_' + (this.solutionId || 'scratch');
            /* Best-effort cleanup — see discardAutosave() above. */
            try { localStorage.removeItem(key); } catch(_) { /* swallow-ok: clearing an autosave that storage could not have held in the first place */ }
        },

        exportPng: function() {
            if (!this.paper) return;
            let svgEl = this.paper.el.querySelector('svg');
            if (!svgEl) return;

            let titleText = this.activeViewpointName || 'Architecture Diagram';
            let titleHeight = 40;
            let watermarkHeight = 20;
            let pad = 40;
            let exportScale = 2;

            /* Use actual content bounds (graph coords) so the export crops tightly
               to the diagram rather than exporting the full empty canvas viewport. */
            let graphBBox = this.graph.getBBox();
            if (!graphBBox || graphBBox.width === 0) {
                this.statusText = 'Nothing to export';
                return;
            }
            let paperScale = this.paper.scale();
            let paperTrans = this.paper.translate();
            /* Convert graph bbox → SVG/paper pixel coords */
            let vx = paperTrans.tx + graphBBox.x * paperScale.sx - pad;
            let vy = paperTrans.ty + graphBBox.y * paperScale.sy - pad;
            let vw = graphBBox.width  * paperScale.sx + pad * 2;
            let vh = graphBBox.height * paperScale.sy + pad * 2;

            let canvas = document.createElement('canvas');
            canvas.width  = vw * exportScale;
            canvas.height = (vh + titleHeight + watermarkHeight) * exportScale;
            let ctx = canvas.getContext('2d');
            ctx.scale(exportScale, exportScale);

            /* Clone SVG, crop via viewBox to content area, inline fonts */
            let svgClone = svgEl.cloneNode(true);
            svgClone.setAttribute('viewBox', vx + ' ' + vy + ' ' + vw + ' ' + vh);
            svgClone.setAttribute('width',  vw);
            svgClone.setAttribute('height', vh);
            let styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
            styleEl.textContent = '* { font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }';
            svgClone.insertBefore(styleEl, svgClone.firstChild);

            let img = new Image();
            let blob = new Blob([new XMLSerializer().serializeToString(svgClone)], { type: 'image/svg+xml' });
            let url = URL.createObjectURL(blob);

            let self = this;
            img.onload = function() {
                /* White background */
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(0, 0, vw, vh + titleHeight + watermarkHeight);

                /* Title bar */
                ctx.fillStyle = '#1e293b';
                ctx.font = 'bold 16px Inter, sans-serif';
                ctx.textAlign = 'left';
                ctx.fillText(titleText, 16, 26);

                /* Diagram below title */
                ctx.drawImage(img, 0, titleHeight);
                URL.revokeObjectURL(url);

                /* Watermark bottom-right */
                ctx.fillStyle = '#94a3b8';
                ctx.font = '10px Inter, sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText('Entelim', vw - 12, vh + titleHeight + 12);

                canvas.toBlob(function(pngBlob) {
                    let a = document.createElement('a');
                    a.href = URL.createObjectURL(pngBlob);
                    let safeName = titleText.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
                    a.download = safeName + '.png';
                    a.click();
                    URL.revokeObjectURL(a.href);
                    self.statusText = 'PNG exported (2x resolution)';
                });
            };
            img.src = url;
        },

        /* BA-04: one-click, leadership-readable PDF of the current diagram.
           Renders onto a standard ISO page (A4, or A3 when the diagram is very
           wide) with a real title and generation date drawn as PDF text — not a
           screenshot of the app chrome, and no manual browser print step.
           Every failure path is surfaced via _toast; there is no silent no-op. */
        exportPdf: function() {
            let self = this;
            function fail(msg) {
                self.statusText = 'PDF export failed: ' + msg;
                _toast('error', 'PDF export failed — ' + msg);
            }

            if (!this.paper || !this.graph) { fail('the canvas is not ready yet'); return; }
            let jsPDFLib = (window.jspdf && window.jspdf.jsPDF) || window.jsPDF;
            if (!jsPDFLib) { fail('the PDF library did not load'); return; }
            let svgEl = this.paper.el.querySelector('svg');
            if (!svgEl) { fail('the diagram surface could not be read'); return; }

            let graphBBox = this.graph.getBBox();
            if (!graphBBox || !graphBBox.width || !graphBBox.height) {
                self.statusText = 'Nothing on the canvas to export';
                _toast('warning', 'Add elements to the canvas before exporting a PDF.');
                return;
            }

            let titleText = this.activeViewpointName || 'Architecture Diagram';
            let generatedAt = new Date();
            let pad = 40;

            let paperScale = this.paper.scale();
            let paperTrans = this.paper.translate();
            let vx = paperTrans.tx + graphBBox.x * paperScale.sx - pad;
            let vy = paperTrans.ty + graphBBox.y * paperScale.sy - pad;
            let vw = graphBBox.width  * paperScale.sx + pad * 2;
            let vh = graphBBox.height * paperScale.sy + pad * 2;

            /* Render at 2x for print sharpness, but clamp so we never exceed the
               browser canvas limit — past it toDataURL yields "data:," and the
               PDF would embed an empty image. */
            let MAX_CANVAS_PX = 8000;
            let exportScale = Math.min(2, MAX_CANVAS_PX / Math.max(vw, vh));
            if (!(exportScale > 0)) { fail('the diagram is too large to rasterise'); return; }

            let canvas = document.createElement('canvas');
            canvas.width  = Math.round(vw * exportScale);
            canvas.height = Math.round(vh * exportScale);
            let ctx = canvas.getContext('2d');
            if (!ctx) { fail('this browser did not provide a 2D canvas'); return; }
            ctx.scale(exportScale, exportScale);

            let svgClone = svgEl.cloneNode(true);
            svgClone.setAttribute('viewBox', vx + ' ' + vy + ' ' + vw + ' ' + vh);
            svgClone.setAttribute('width',  vw);
            svgClone.setAttribute('height', vh);
            let styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
            styleEl.textContent = '* { font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }';
            svgClone.insertBefore(styleEl, svgClone.firstChild);

            let img = new Image();
            let blob = new Blob([new XMLSerializer().serializeToString(svgClone)], { type: 'image/svg+xml' });
            let url = URL.createObjectURL(blob);

            self.statusText = 'Generating PDF…';

            img.onerror = function() {
                URL.revokeObjectURL(url);
                fail('the diagram could not be rasterised');
            };

            img.onload = function() {
                try {
                    /* Flatten onto white — a transparent PNG would print grey. */
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, vw, vh);
                    ctx.drawImage(img, 0, 0);

                    let imgData = canvas.toDataURL('image/jpeg', 0.95);
                    if (!imgData || imgData.length < 1000) {
                        throw new Error('the rasterised diagram came back empty');
                    }

                    /* Standard ISO page. Wide diagrams get A3 landscape so the
                       elements stay legible once scaled to fit. */
                    let aspect = vw / vh;
                    let format = aspect > 2.2 ? 'a3' : 'a4';
                    let orientation = aspect >= 1 ? 'landscape' : 'portrait';
                    let doc = new jsPDFLib({ orientation: orientation, unit: 'mm', format: format });

                    let pw = doc.internal.pageSize.getWidth();
                    let ph = doc.internal.pageSize.getHeight();
                    let margin = 12;
                    let headerBottom = 26;
                    let footerTop = ph - 10;

                    /* Title + date as real PDF text — selectable and crisp. */
                    doc.setFont('helvetica', 'bold');
                    doc.setFontSize(16);
                    doc.setTextColor(30, 41, 59);
                    doc.text(String(titleText), margin, 14, { maxWidth: pw - 2 * margin });

                    doc.setFont('helvetica', 'normal');
                    doc.setFontSize(9);
                    doc.setTextColor(100, 116, 139);
                    doc.text(generatedAt.toLocaleString(), margin, 20);

                    /* Fit the diagram into the remaining area, preserving aspect.
                       Cap at 2x natural size so we never upscale past what we
                       actually rendered. */
                    let MM_PER_PX = 0.264583;
                    let natW = vw * MM_PER_PX;
                    let natH = vh * MM_PER_PX;
                    let availW = pw - 2 * margin;
                    let availH = footerTop - headerBottom - 4;
                    let fit = Math.min(availW / natW, availH / natH, 2);
                    let drawW = natW * fit;
                    let drawH = natH * fit;
                    let drawX = margin + (availW - drawW) / 2;
                    let drawY = headerBottom + (availH - drawH) / 2;

                    doc.addImage(imgData, 'JPEG', drawX, drawY, drawW, drawH);

                    doc.setFontSize(8);
                    doc.setTextColor(148, 163, 184);
                    doc.text('Entelim', margin, footerTop + 4);
                    doc.text(
                        'Elements: ' + self.graph.getElements().length +
                        '   Relationships: ' + self.graph.getLinks().length,
                        pw - margin, footerTop + 4, { align: 'right' }
                    );

                    let safeName = String(titleText).replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60) || 'diagram';
                    let stamp = generatedAt.toISOString().substring(0, 10);
                    doc.save(safeName + '_' + stamp + '.pdf');

                    self.statusText = 'PDF exported';
                    _toast('success', 'PDF exported');
                } catch (err) {
                    fail(err && err.message ? err.message : String(err));
                } finally {
                    URL.revokeObjectURL(url);
                }
            };
            img.src = url;
        },

        exportReport: function() {
            let self = this;
            self.statusText = 'Generating report…';

            let svgEl = document.querySelector('#composer-canvas svg');
            if (!svgEl) { self.statusText = 'No diagram to export'; return; }

            let jsPDFLib = (window.jspdf && window.jspdf.jsPDF) || window.jsPDF;
            if (!jsPDFLib) { self.statusText = 'PDF library not loaded'; return; }

            let svgData = new XMLSerializer().serializeToString(svgEl);
            let svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
            let url = URL.createObjectURL(svgBlob);
            let img = new Image();

            img.onload = function() {
                try {
                    /* jsPDF landscape A4 */
                    let pdf = new jsPDFLib({ orientation: 'landscape', unit: 'mm', format: 'a4' });
                    let pw = pdf.internal.pageSize.getWidth();
                    let ph = pdf.internal.pageSize.getHeight();
                    let margin = 15;
                    let usable = pw - 2 * margin;

                    /* ── Helper: header + footer on each page ── */
                    let pageNum = 0;
                    function addHeaderFooter() {
                        pageNum++;
                        pdf.setFontSize(8);
                        pdf.setTextColor(150);
                        pdf.text(self.activeViewpointName || 'Architecture Report', margin, 8);
                        pdf.text('Page ' + pageNum, pw - margin, ph - 5, { align: 'right' });
                        pdf.text(new Date().toLocaleDateString(), margin, ph - 5);
                        pdf.setTextColor(0);
                    }

                    /* ── Page 1: Cover ── */
                    addHeaderFooter();
                    pdf.setFontSize(28);
                    pdf.setTextColor(30);
                    pdf.text('Architecture Report', pw / 2, 50, { align: 'center' });

                    pdf.setFontSize(16);
                    pdf.setTextColor(80);
                    pdf.text(self.activeViewpointName || 'Viewpoint', pw / 2, 65, { align: 'center' });

                    pdf.setFontSize(11);
                    pdf.setTextColor(100);
                    pdf.text('Solution: ' + (self.solutionLabel || 'Enterprise'), pw / 2, 80, { align: 'center' });
                    pdf.text('Generated: ' + new Date().toLocaleString(), pw / 2, 88, { align: 'center' });

                    /* Summary stats */
                    let elements = self.graph.getElements().filter(function(c) { return !c.get('isLayerZone') && !c.get('isAnnotation'); });
                    let links = self.graph.getLinks();
                    pdf.setFontSize(12);
                    pdf.setTextColor(60);
                    pdf.text('Elements: ' + elements.length + '    Relationships: ' + links.length, pw / 2, 105, { align: 'center' });

                    /* Layer breakdown */
                    let layerCounts = {};
                    elements.forEach(function(el) {
                        let layer = (el.get('elLayer') || 'unknown').toLowerCase();
                        layerCounts[layer] = (layerCounts[layer] || 0) + 1;
                    });
                    let layerSummary = Object.keys(layerCounts).map(function(k) {
                        return k.charAt(0).toUpperCase() + k.slice(1) + ': ' + layerCounts[k];
                    }).join('    ');
                    pdf.setFontSize(10);
                    pdf.text(layerSummary, pw / 2, 115, { align: 'center' });

                    /* ── Page 2: Diagram ── */
                    pdf.addPage();
                    addHeaderFooter();
                    pdf.setFontSize(14);
                    pdf.text('Diagram', margin, 20);

                    let canvas = document.createElement('canvas');
                    let scale = 2;
                    canvas.width = img.width * scale;
                    canvas.height = img.height * scale;
                    let ctx = canvas.getContext('2d');
                    ctx.scale(scale, scale);
                    ctx.fillStyle = '#ffffff';
                    ctx.fillRect(0, 0, img.width, img.height);
                    ctx.drawImage(img, 0, 0);

                    let imgData = canvas.toDataURL('image/png');
                    let maxW = usable;
                    let maxH = ph - 35;
                    let ratio = Math.min(maxW / img.width, maxH / img.height);
                    let imgW = img.width * ratio;
                    let imgH = img.height * ratio;
                    pdf.addImage(imgData, 'PNG', margin, 25, imgW, imgH);

                    /* ── Page 3: Element Catalog ── */
                    pdf.addPage();
                    addHeaderFooter();
                    pdf.setFontSize(14);
                    pdf.text('Element Catalog', margin, 20);

                    /* Table header */
                    let colWidths = [usable * 0.35, usable * 0.30, usable * 0.20, usable * 0.15];
                    let headers = ['Name', 'Type', 'Layer', 'ID'];
                    let y = 28;
                    pdf.setFontSize(9);
                    pdf.setFont(undefined, 'bold');
                    pdf.setFillColor(240, 240, 240);
                    pdf.rect(margin, y - 4, usable, 7, 'F');
                    let cx = margin;
                    headers.forEach(function(h, i) {
                        pdf.text(h, cx + 2, y);
                        cx += colWidths[i];
                    });
                    pdf.setFont(undefined, 'normal');
                    y += 8;

                    /* Table rows */
                    elements.sort(function(a, b) {
                        let la = (a.get('elLayer') || '').toLowerCase();
                        let lb = (b.get('elLayer') || '').toLowerCase();
                        return la < lb ? -1 : la > lb ? 1 : 0;
                    });
                    elements.forEach(function(el) {
                        if (y > ph - 15) {
                            pdf.addPage();
                            addHeaderFooter();
                            y = 20;
                        }
                        cx = margin;
                        let row = [
                            (el.get('elName') || 'Unnamed').substring(0, 40),
                            (el.get('elType') || '').substring(0, 30),
                            (el.get('elLayer') || 'unknown'),
                            (el.get('elementId') || '').toString().substring(0, 12),
                        ];
                        row.forEach(function(val, i) {
                            pdf.text(val, cx + 2, y);
                            cx += colWidths[i];
                        });
                        y += 6;
                    });

                    /* ── Page N: Relationship Table ── */
                    if (links.length > 0) {
                        pdf.addPage();
                        addHeaderFooter();
                        pdf.setFontSize(14);
                        pdf.text('Relationships', margin, 20);

                        let rColWidths = [usable * 0.30, usable * 0.15, usable * 0.30, usable * 0.25];
                        let rHeaders = ['Source', 'Type', 'Target', 'ID'];
                        y = 28;
                        pdf.setFontSize(9);
                        pdf.setFont(undefined, 'bold');
                        pdf.setFillColor(240, 240, 240);
                        pdf.rect(margin, y - 4, usable, 7, 'F');
                        cx = margin;
                        rHeaders.forEach(function(h, i) {
                            pdf.text(h, cx + 2, y);
                            cx += rColWidths[i];
                        });
                        pdf.setFont(undefined, 'normal');
                        y += 8;

                        links.forEach(function(link) {
                            if (y > ph - 15) {
                                pdf.addPage();
                                addHeaderFooter();
                                y = 20;
                            }
                            let src = link.getSourceCell();
                            let tgt = link.getTargetCell();
                            cx = margin;
                            let rRow = [
                                (src ? (src.get('elName') || 'Unknown') : 'Unknown').substring(0, 30),
                                (link.get('relType') || 'association').substring(0, 15),
                                (tgt ? (tgt.get('elName') || 'Unknown') : 'Unknown').substring(0, 30),
                                (link.get('relId') || '').toString().substring(0, 20),
                            ];
                            rRow.forEach(function(val, i) {
                                pdf.text(val, cx + 2, y);
                                cx += rColWidths[i];
                            });
                            y += 6;
                        });
                    }

                    /* ── Last page: Legend ── */
                    pdf.addPage();
                    addHeaderFooter();
                    pdf.setFontSize(14);
                    pdf.text('Layer Colour Legend', margin, 20);

                    let legendY = 30;
                    let LAYER_COLORS = {
                        'Business': '#fff9c4', 'Application': '#e1f5fe', 'Technology': '#e8f5e9',
                        'Motivation': '#f3e8ff', 'Strategy': '#fce7f3', 'Implementation': '#ffedd5',
                    };
                    Object.keys(LAYER_COLORS).forEach(function(layer) {
                        let hex = LAYER_COLORS[layer];
                        let r = parseInt(hex.slice(1,3), 16);
                        let g = parseInt(hex.slice(3,5), 16);
                        let b = parseInt(hex.slice(5,7), 16);
                        pdf.setFillColor(r, g, b);
                        pdf.rect(margin, legendY - 4, 12, 6, 'F');
                        pdf.setFontSize(11);
                        pdf.setTextColor(60);
                        pdf.text(layer + ' (' + (layerCounts[layer.toLowerCase()] || 0) + ' elements)', margin + 16, legendY);
                        legendY += 10;
                    });

                    pdf.save((self.activeViewpointName || 'architecture-report') + '.pdf');
                    self.statusText = 'Report exported (' + pageNum + ' pages)';
                } catch(e) {
                    self.statusText = 'Export failed: ' + e.message;
                    _toast('error', 'Report export failed: ' + (e.message || e));
                }
                URL.revokeObjectURL(url);
            };
            img.onerror = function() { self.statusText = 'Image render failed'; URL.revokeObjectURL(url); };
            img.src = url;
        },

        exportSvg: function() {
            if (!this.paper) return;
            let svgEl = this.paper.el.querySelector('svg');
            if (!svgEl) return;

            let pad = 40;
            let graphBBox = this.graph.getBBox();
            if (!graphBBox || graphBBox.width === 0) {
                this.statusText = 'Nothing to export';
                return;
            }
            let paperScale = this.paper.scale();
            let paperTrans = this.paper.translate();
            let vx = paperTrans.tx + graphBBox.x * paperScale.sx - pad;
            let vy = paperTrans.ty + graphBBox.y * paperScale.sy - pad;
            let vw = graphBBox.width  * paperScale.sx + pad * 2;
            let vh = graphBBox.height * paperScale.sy + pad * 2;

            let svgClone = svgEl.cloneNode(true);
            svgClone.setAttribute('viewBox', vx + ' ' + vy + ' ' + vw + ' ' + vh);
            svgClone.setAttribute('width',  vw);
            svgClone.setAttribute('height', vh);
            svgClone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            svgClone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');

            /* Inline font-family so SVG is self-contained */
            let styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
            styleEl.textContent = '* { font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }';
            svgClone.insertBefore(styleEl, svgClone.firstChild);

            /* White background rect sized to viewBox */
            let bgRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            bgRect.setAttribute('x', vx);
            bgRect.setAttribute('y', vy);
            bgRect.setAttribute('width',  vw);
            bgRect.setAttribute('height', vh);
            bgRect.setAttribute('fill', '#ffffff');
            svgClone.insertBefore(bgRect, svgClone.firstChild);

            let svgStr = new XMLSerializer().serializeToString(svgClone);
            let blob = new Blob([svgStr], { type: 'image/svg+xml' });
            let a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            let titleText = this.activeViewpointName || 'architecture-diagram';
            let safeName = titleText.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
            a.download = safeName + '.svg';
            a.click();
            URL.revokeObjectURL(a.href);
            this.statusText = 'SVG exported';
        },

        // CMP2-005: Export to PowerPoint with editable shapes and relationship table
        exportToPptx: function() {
            let self = this;
            if (!self.graph) return;
            if (typeof PptxGenJS === 'undefined') {
                self.statusText = 'PowerPoint library not loaded';
                return;
            }

            let elements = self.graph.getElements().filter(function(c) {
                return !c.get('isLayerZone') && !c.get('isAnnotation');
            });
            let links = self.graph.getLinks();

            if (elements.length === 0) {
                self.statusText = 'Nothing to export';
                return;
            }

            self.statusText = 'Generating PowerPoint…';

            try {
                let pptx = new PptxGenJS();
                pptx.author = 'Entelim';
                pptx.company = 'Enterprise Architecture';
                pptx.subject = self.activeViewpointName || 'Architecture Diagram';
                pptx.title = self.activeViewpointName || 'Architecture Diagram';

                let titleText = self.activeViewpointName || 'Architecture Diagram';
                let dateStr = new Date().toLocaleDateString();

                /* ArchiMate layer color mapping (hex for PptxGenJS) */
                let LAYER_FILLS = {
                    business: 'FFF9C4', application: 'E1F5FE', technology: 'E8F5E9',
                    motivation: 'F3E8FF', strategy: 'FCE7F3', implementation: 'FFEDD5',
                    physical: 'FEFCE8',
                };
                let LAYER_ACCENTS = {
                    business: 'EAB308', application: '0284C7', technology: '16A34A',
                    motivation: '7C3AED', strategy: 'DB2777', implementation: 'EA580C',
                    physical: 'CA8A04',
                };
                let DEFAULT_FILL = 'F8FAFC';
                let DEFAULT_ACCENT = '94A3B8';

                /* Relationship line styles for PPTX */
                let REL_DASH = {
                    realization: 'dash', access: 'dot', influence: 'dashDot',
                    flow: 'lgDash', association: 'solid',
                };
                let REL_COLORS = {
                    composition: '475569', aggregation: '475569', assignment: '475569',
                    realization: '64748B', serving: '94A3B8', access: '94A3B8',
                    influence: '8B5CF6', triggering: '7C3AED', flow: '7C3AED',
                    specialization: '64748B', association: 'CBD5E1',
                };

                /* ── Slide 1: Title + Metadata ── */
                let slide1 = pptx.addSlide();
                slide1.background = { color: 'FFFFFF' };
                /* Top accent bar */
                slide1.addShape(pptx.shapes.RECTANGLE, {
                    x: 0, y: 0, w: '100%', h: 0.08, fill: { color: '1E293B' },
                });
                slide1.addText(titleText, {
                    x: 0.8, y: 1.8, w: 8.4, h: 1.0,
                    fontSize: 32, fontFace: 'Segoe UI', bold: true, color: '1E293B',
                });
                /* Metadata lines */
                let metaLines = [
                    { text: 'Viewpoint: ' + (self.activeViewpointName || 'General'), options: { fontSize: 14, color: '475569' } },
                    { text: 'Solution: ' + (self.solutionLabel || 'Enterprise'), options: { fontSize: 14, color: '475569' } },
                    { text: 'Date: ' + dateStr, options: { fontSize: 14, color: '475569' } },
                    { text: 'Elements: ' + elements.length + '  |  Relationships: ' + links.length, options: { fontSize: 14, color: '64748B' } },
                ];
                slide1.addText(metaLines, {
                    x: 0.8, y: 3.0, w: 8.4, h: 1.8, valign: 'top', paraSpaceAfter: 6,
                });
                /* Branding */
                slide1.addText('Entelim Enterprise Architecture Platform', {
                    x: 0.8, y: 4.9, w: 8.4, h: 0.4,
                    fontSize: 10, color: '94A3B8', fontFace: 'Segoe UI',
                });

                /* ── Slide 2: Diagram as editable shapes ── */
                let slide2 = pptx.addSlide();
                slide2.background = { color: 'FFFFFF' };
                slide2.addText(titleText, {
                    x: 0.3, y: 0.1, w: 9.4, h: 0.4,
                    fontSize: 14, fontFace: 'Segoe UI', bold: true, color: '1E293B',
                });

                /* Calculate bounding box for coordinate mapping */
                let graphBBox = self.graph.getBBox();
                if (!graphBBox || graphBBox.width === 0) {
                    self.statusText = 'Nothing to export';
                    return;
                }

                /* Map graph coordinates to slide inches (10 x 5.2 usable area) */
                let slideW = 9.2;
                let slideH = 4.6;
                let slideOffX = 0.4;
                let slideOffY = 0.6;
                let scaleX = slideW / graphBBox.width;
                let scaleY = slideH / graphBBox.height;
                let scale = Math.min(scaleX, scaleY, 0.02); /* cap scale to keep shapes readable */
                /* Ensure minimum shape size: if scale makes shapes too tiny, recalculate */
                let minShapeW = 1.2; /* minimum 1.2 inches wide */
                let avgElemW = 200; /* default JointJS element width */
                if (avgElemW * scale < minShapeW) {
                    scale = minShapeW / avgElemW;
                }

                /* Center the diagram on the slide */
                let diagramW = graphBBox.width * scale;
                let diagramH = graphBBox.height * scale;
                let centerOffX = slideOffX + (slideW - diagramW) / 2;
                let centerOffY = slideOffY + (slideH - diagramH) / 2;

                function toSlideX(gx) { return centerOffX + (gx - graphBBox.x) * scale; }
                function toSlideY(gy) { return centerOffY + (gy - graphBBox.y) * scale; }

                /* Build element ID → slide position map for relationship lines */
                let elemPosMap = {};

                /* Draw elements as editable rectangles */
                elements.forEach(function(el) {
                    let pos = el.position();
                    let sz = el.size();
                    let layer = (el.get('elLayer') || '').toLowerCase();
                    let fillColor = LAYER_FILLS[layer] || DEFAULT_FILL;
                    let accentColor = LAYER_ACCENTS[layer] || DEFAULT_ACCENT;
                    let name = el.get('elName') || 'Unnamed';
                    let elType = (el.get('elType') || '').replace(/([A-Z])/g, ' $1').trim();

                    let sx = toSlideX(pos.x);
                    let sy = toSlideY(pos.y);
                    let sw = sz.width * scale;
                    let sh = sz.height * scale;

                    /* Minimum readable size */
                    if (sw < 1.0) sw = 1.0;
                    if (sh < 0.6) sh = 0.6;

                    /* Element rectangle */
                    slide2.addShape(pptx.shapes.ROUNDED_RECTANGLE, {
                        x: sx, y: sy, w: sw, h: sh,
                        fill: { color: fillColor },
                        line: { color: accentColor, width: 1.5 },
                        rectRadius: 0.05,
                    });

                    /* Accent bar on left edge */
                    slide2.addShape(pptx.shapes.RECTANGLE, {
                        x: sx, y: sy, w: 0.05, h: sh,
                        fill: { color: accentColor }, line: { width: 0 },
                    });

                    /* Element name text */
                    slide2.addText(name, {
                        x: sx + 0.08, y: sy + 0.02, w: sw - 0.15, h: sh * 0.6,
                        fontSize: Math.max(7, Math.min(10, Math.round(sh * 6))),
                        fontFace: 'Segoe UI', bold: true, color: '1A1A1A',
                        valign: 'middle', wordWrap: true,
                    });

                    /* Type label (smaller, below name) */
                    slide2.addText(elType, {
                        x: sx + 0.08, y: sy + sh * 0.55, w: sw - 0.15, h: sh * 0.35,
                        fontSize: Math.max(6, Math.min(8, Math.round(sh * 4))),
                        fontFace: 'Segoe UI', color: '64748B',
                        valign: 'top', wordWrap: true,
                    });

                    /* Store center position for relationship lines */
                    let cellId = el.id;
                    elemPosMap[cellId] = {
                        cx: sx + sw / 2, cy: sy + sh / 2,
                        x: sx, y: sy, w: sw, h: sh,
                    };
                });

                /* Draw relationship lines */
                links.forEach(function(link) {
                    let srcCell = link.getSourceCell();
                    let tgtCell = link.getTargetCell();
                    if (!srcCell || !tgtCell) return;

                    let srcPos = elemPosMap[srcCell.id];
                    let tgtPos = elemPosMap[tgtCell.id];
                    if (!srcPos || !tgtPos) return;

                    let relType = (link.get('relType') || 'association').toLowerCase();
                    let lineColor = REL_COLORS[relType] || 'CBD5E1';
                    let dashType = REL_DASH[relType] || 'solid';

                    /* Calculate line endpoints at shape edges (not centers) */
                    let dx = tgtPos.cx - srcPos.cx;
                    let dy = tgtPos.cy - srcPos.cy;
                    let dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 0.01) return;

                    /* Normalize direction */
                    let nx = dx / dist;
                    let ny = dy / dist;

                    /* Intersect with source shape edge */
                    let srcEx = srcPos.cx + nx * (srcPos.w / 2);
                    let srcEy = srcPos.cy + ny * (srcPos.h / 2);
                    /* Intersect with target shape edge */
                    let tgtEx = tgtPos.cx - nx * (tgtPos.w / 2);
                    let tgtEy = tgtPos.cy - ny * (tgtPos.h / 2);

                    /* Draw line from source edge to target edge */
                    let lineX = Math.min(srcEx, tgtEx);
                    let lineY = Math.min(srcEy, tgtEy);
                    let lineW = Math.abs(tgtEx - srcEx) || 0.01;
                    let lineH = Math.abs(tgtEy - srcEy) || 0.01;

                    /* Determine if line needs to be flipped */
                    let flipH = tgtEx < srcEx;
                    let flipV = tgtEy < srcEy;

                    /* Draw relationship line with arrowhead where applicable */
                    let lineOpts = {
                        x: lineX, y: lineY, w: lineW, h: lineH,
                        line: { color: lineColor, width: 1.5, dashType: dashType },
                        flipH: flipH, flipV: flipV,
                    };
                    if (relType !== 'association') {
                        if (relType === 'composition' || relType === 'aggregation') {
                            lineOpts.line.endArrowType = 'diamond';
                        } else {
                            lineOpts.line.endArrowType = 'arrow';
                        }
                    }
                    slide2.addShape(pptx.shapes.LINE, lineOpts);
                });

                /* ── Slide 3: Element Inventory Table ── */
                let slide3 = pptx.addSlide();
                slide3.background = { color: 'FFFFFF' };
                slide3.addText('Element Inventory', {
                    x: 0.3, y: 0.1, w: 9.4, h: 0.4,
                    fontSize: 16, fontFace: 'Segoe UI', bold: true, color: '1E293B',
                });

                /* Sort elements by layer then name */
                let sortedElements = elements.slice().sort(function(a, b) {
                    let la = (a.get('elLayer') || '').toLowerCase();
                    let lb = (b.get('elLayer') || '').toLowerCase();
                    if (la !== lb) return la < lb ? -1 : 1;
                    let na = (a.get('elName') || '').toLowerCase();
                    let nb = (b.get('elName') || '').toLowerCase();
                    return na < nb ? -1 : na > nb ? 1 : 0;
                });

                /* Build table rows — PptxGenJS supports max ~20 rows per slide */
                let maxRowsPerSlide = 18;
                let elemChunks = [];
                for (let i = 0; i < sortedElements.length; i += maxRowsPerSlide) {
                    elemChunks.push(sortedElements.slice(i, i + maxRowsPerSlide));
                }

                elemChunks.forEach(function(chunk, chunkIdx) {
                    let targetSlide = chunkIdx === 0 ? slide3 : pptx.addSlide();
                    if (chunkIdx > 0) {
                        targetSlide.background = { color: 'FFFFFF' };
                        targetSlide.addText('Element Inventory (continued)', {
                            x: 0.3, y: 0.1, w: 9.4, h: 0.4,
                            fontSize: 16, fontFace: 'Segoe UI', bold: true, color: '1E293B',
                        });
                    }

                    let tableRows = [];
                    /* Header row */
                    tableRows.push([
                        { text: 'Name', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                        { text: 'Type', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                        { text: 'Layer', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                        { text: 'Description', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                    ]);

                    chunk.forEach(function(el, idx) {
                        let layer = (el.get('elLayer') || 'Unknown');
                        let layerLower = layer.toLowerCase();
                        let rowFill = idx % 2 === 0 ? 'F8FAFC' : 'FFFFFF';
                        let layerFill = LAYER_FILLS[layerLower] || DEFAULT_FILL;
                        let elType = (el.get('elType') || '').replace(/([A-Z])/g, ' $1').trim();
                        let name = el.get('elName') || 'Unnamed';
                        /* Get description from element custom properties if available */
                        let desc = el.get('elDescription') || el.get('description') || '';

                        tableRows.push([
                            { text: name, options: { fontSize: 9, fontFace: 'Segoe UI', fill: { color: rowFill } } },
                            { text: elType, options: { fontSize: 9, fontFace: 'Segoe UI', color: '475569', fill: { color: rowFill } } },
                            { text: layer.charAt(0).toUpperCase() + layer.slice(1), options: { fontSize: 9, fontFace: 'Segoe UI', fill: { color: layerFill } } },
                            { text: desc.substring(0, 80), options: { fontSize: 9, fontFace: 'Segoe UI', color: '64748B', fill: { color: rowFill } } },
                        ]);
                    });

                    targetSlide.addTable(tableRows, {
                        x: 0.3, y: 0.6, w: 9.4,
                        colW: [2.8, 2.2, 1.4, 3.0],
                        border: { type: 'solid', pt: 0.5, color: 'E2E8F0' },
                        rowH: 0.28,
                        autoPage: false,
                    });
                });

                /* ── Slide 4: Relationship Matrix ── */
                if (links.length > 0) {
                    let relChunks = [];
                    for (let i = 0; i < links.length; i += maxRowsPerSlide) {
                        relChunks.push(links.slice(i, i + maxRowsPerSlide));
                    }

                    relChunks.forEach(function(chunk, chunkIdx) {
                        let relSlide = pptx.addSlide();
                        relSlide.background = { color: 'FFFFFF' };
                        relSlide.addText(chunkIdx === 0 ? 'Relationship Matrix' : 'Relationship Matrix (continued)', {
                            x: 0.3, y: 0.1, w: 9.4, h: 0.4,
                            fontSize: 16, fontFace: 'Segoe UI', bold: true, color: '1E293B',
                        });

                        let relRows = [];
                        /* Header row */
                        relRows.push([
                            { text: 'Source', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                            { text: 'Relationship Type', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                            { text: 'Target', options: { bold: true, color: 'FFFFFF', fill: { color: '1E293B' }, fontSize: 10, fontFace: 'Segoe UI' } },
                        ]);

                        chunk.forEach(function(link, idx) {
                            let src = link.getSourceCell();
                            let tgt = link.getTargetCell();
                            let relType = link.get('relType') || 'association';
                            let relColor = REL_COLORS[relType.toLowerCase()] || '64748B';
                            let rowFill = idx % 2 === 0 ? 'F8FAFC' : 'FFFFFF';

                            relRows.push([
                                { text: src ? (src.get('elName') || 'Unknown') : 'Unknown', options: { fontSize: 9, fontFace: 'Segoe UI', fill: { color: rowFill } } },
                                { text: relType.charAt(0).toUpperCase() + relType.slice(1), options: { fontSize: 9, fontFace: 'Segoe UI', color: relColor, bold: true, fill: { color: rowFill } } },
                                { text: tgt ? (tgt.get('elName') || 'Unknown') : 'Unknown', options: { fontSize: 9, fontFace: 'Segoe UI', fill: { color: rowFill } } },
                            ]);
                        });

                        relSlide.addTable(relRows, {
                            x: 0.3, y: 0.6, w: 9.4,
                            colW: [3.5, 2.4, 3.5],
                            border: { type: 'solid', pt: 0.5, color: 'E2E8F0' },
                            rowH: 0.28,
                            autoPage: false,
                        });
                    });
                }

                /* Generate and download */
                let safeName = titleText.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
                pptx.writeFile({ fileName: safeName + '.pptx' }).then(function() {
                    self.statusText = 'PowerPoint exported (' + elements.length + ' elements, ' + links.length + ' relationships)';
                }).catch(function(err) {
                    self.statusText = 'PowerPoint export failed: ' + (err.message || err);
                    _toast('error', 'PowerPoint export failed: ' + (err.message || err));
                });
            } catch (e) {
                self.statusText = 'PowerPoint export failed: ' + e.message;
                _toast('error', 'PowerPoint export failed: ' + (e.message || e));
            }
        },

        // CMP-042: Bulk CSV import
        importCsv: function() {
            let self = this;
            let fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = '.csv';
            fileInput.style.display = 'none';
            document.body.appendChild(fileInput);

            fileInput.addEventListener('change', function() {
                let file = fileInput.files[0];
                document.body.removeChild(fileInput);
                if (!file) return;

                if (file.size > 10 * 1024 * 1024) {
                    _toast('error', 'File exceeds 10MB limit');
                    return;
                }

                self.statusText = 'Importing CSV...';
                let formData = new FormData();
                formData.append('file', file);
                formData.append('strategy', 'skip_duplicates');

                Platform.fetch.post('/archimate/api/import/csv', formData, { silent: true })
                .then(function(d) {
                    let msg = d.elements_imported + ' imported, ' + d.elements_skipped + ' skipped';
                    if (d.elements_updated > 0) msg += ', ' + d.elements_updated + ' updated';
                    _toast('success', 'CSV import: ' + msg);
                    self.statusText = msg;

                    // Refresh element search cache so newly imported elements appear
                    if (typeof self.refreshSearchCache === 'function') {
                        self.refreshSearchCache();
                    }
                })
                .catch(function(err) {
                    if (err && err.type === 'HttpError') {
                        _toast('error', (err.data && err.data.error) || 'Import failed');
                        self.statusText = 'Import failed';
                    } else {
                        _toast('error', 'Import error: ' + (err.message || ''));
                        self.statusText = 'Import error';
                    }
                });
            });

            fileInput.click();
        },

        // CMP-038: OEF XML import
        importOef: function() {
            let self = this;
            let fileInput = document.createElement('input');
            fileInput.type = 'file';
            fileInput.accept = '.xml';
            fileInput.style.display = 'none';
            document.body.appendChild(fileInput);

            fileInput.addEventListener('change', function() {
                let file = fileInput.files[0];
                document.body.removeChild(fileInput);
                if (!file) return;

                if (file.size > 10 * 1024 * 1024) {
                    _toast('error', 'File exceeds 10MB limit');
                    return;
                }

                self.statusText = 'Importing OEF XML\u2026';
                let formData = new FormData();
                formData.append('file', file);

                Platform.fetch.post('/archimate/api/import/oef', formData, { silent: true })
                .then(function(d) {
                    self.applyImportedDiagramPayload(d, 'OEF import');
                })
                .catch(function(err) {
                    if (err && err.type === 'HttpError') {
                        _toast('error', (err.data && err.data.error) || 'OEF import failed');
                        self.statusText = 'Import failed';
                    } else {
                        _toast('error', 'Import error: ' + (err.message || ''));
                        self.statusText = 'Import error';
                    }
                });
            });

            fileInput.click();
        },

        applyImportedDiagramPayload: function(data, sourceLabel) {
            let self = this;
            let isLucidImport = String(sourceLabel || '').toLowerCase().indexOf('lucid') !== -1;
            if (
                isLucidImport &&
                (
                    self.graph.getElements().filter(function(cell) { return !cell.get('isLayerZone') && !cell.get('isAnnotation'); }).length > 0 ||
                    self.graph.getLinks().length > 0
                )
            ) {
                _resetImportCanvas(self);
                _toast('info', 'Replaced the current scratch canvas before importing the Lucid diagram.');
            }
            let existingElementCount = self.graph.getElements().filter(function(cell) {
                return !cell.get('isLayerZone') && !cell.get('isAnnotation');
            }).length;
            let cols = Math.max(1, Math.ceil(Math.sqrt((data.elements || []).length || 1)));
            let createNode = ComposerRenderer.createNode;
            let guessLayer = ComposerRenderer.guessLayer;

            (data.elements || []).forEach(function(el, i) {
                let col = i % cols;
                let row = Math.floor(i / cols);
                // Prefer the source layout when the export carried geometry
                // (Standard Import boundingBox / Entelim round-trip); otherwise
                // fall back to a grid (auto-arranged below when relationships exist).
                let hasGeom = Number.isFinite(el.x) && Number.isFinite(el.y);
                let x = hasGeom ? el.x : 40 + col * 240;
                let y = hasGeom ? el.y : 40 + row * 160;
                let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                let node = createNode(el.id, el.name, el.type, layer, x, y);
                if (hasGeom && Number.isFinite(el.w) && Number.isFinite(el.h) && el.w > 20 && el.h > 20) {
                    node.resize(el.w, el.h);
                }
                self.graph.addCell(node);
                _rememberImportedElementProps(self, el);
                applyImportedElementPresentation(node, Object.assign({
                    rendering_mode: el.rendering_mode,
                }, el.custom_properties || {}));
                if (!self.canvasElements[el.id]) {
                    self.canvasElements[el.id] = {
                        id: el.id,
                        name: el.name,
                        type: el.type,
                        layer: layer,
                        rendering_mode: el.rendering_mode || null,
                        custom_properties: el.custom_properties || {},
                    };
                    self.elementCount++;
                }
            });

            let cellMap = {};
            self.graph.getElements().forEach(function(cell) {
                let eid = cell.get('elementId');
                if (eid) cellMap[eid] = cell;
            });
            let createLink = ComposerRenderer.createLink;
            (data.relationships || []).forEach(function(rel) {
                let src = cellMap[rel.source_id];
                let tgt = cellMap[rel.target_id];
                if (!src || !tgt) return;
                let link = createLink(src, tgt, rel.type || 'association', rel.id);
                if (rel.access_mode) link.set('accessMode', rel.access_mode);
                if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                if (rel.connection_spec) link.set('connectionSpec', rel.connection_spec);
                if (rel.custom_label) {
                    link.set('customLabel', rel.custom_label);
                    if (typeof applyCustomLabel === 'function') applyCustomLabel(link, rel.custom_label);
                }
                self.graph.addCell(link);
                self.relCount++;
            });

            let geometryMissing = (data.warnings || []).some(function(w) {
                return (w || '').toLowerCase().indexOf('geometry data') !== -1;
            });
            let usedHierarchicalLayout = false;

            let canHierarchical =
                isLucidImport &&
                geometryMissing &&
                existingElementCount === 0 &&
                (data.relationships || []).length;
            if (canHierarchical && typeof self.layoutDagre === 'function' && typeof dagre !== 'undefined') {
                self.layoutDagre('LR');
                usedHierarchicalLayout = true;
            } else if (canHierarchical && typeof self.applySugiyamaLayout === 'function') {
                // Pure-JS fallback so an import is auto-arranged even if the dagre
                // CDN failed to load.
                self.applySugiyamaLayout();
                usedHierarchicalLayout = true;
            } else if (self.paper && typeof self.paper.scaleContentToFit === 'function') {
                self.paper.scaleContentToFit({ padding: 40, maxScale: 1 });
                self.zoomPercent = Math.round(self.paper.scale().sx * 100);
            }

            let warningCount = data.warnings && data.warnings.length ? data.warnings.length : 0;
            let warningSuffix = warningCount ? ' (' + warningCount + ' warnings)' : '';
            let msg = data.stats.elements + ' elements, ' + data.stats.relationships + ' relationships imported' + warningSuffix;
            _toast('success', sourceLabel + ': ' + msg);
            let lucidNotationPreserved = (data.elements || []).some(function(el) {
                let mode = el.rendering_mode || ((el.custom_properties || {}).lucid_rendering_mode) || '';
                return mode.indexOf('lucid_') === 0;
            });
            if (geometryMissing) {
                _toast(
                    'warning',
                    usedHierarchicalLayout
                        ? 'Layout fidelity warning: Lucidchart geometry was unavailable, so Entelim applied a hierarchical flow layout.'
                        : 'Layout fidelity warning: Lucidchart geometry was unavailable, so imported content was auto-arranged.'
                );
                self.statusText = lucidNotationPreserved
                    ? (
                        usedHierarchicalLayout
                            ? msg + ' — Lucid ArchiMate notation preserved; hierarchical flow layout applied'
                            : msg + ' — Lucid ArchiMate notation preserved; source layout auto-arranged'
                    )
                    : msg + ' — source layout fidelity unavailable';
            } else {
                self.statusText = lucidNotationPreserved
                    ? msg + ' — Lucid ArchiMate notation preserved'
                    : msg;
            }
            self.viewpointDirty = true;

            if (typeof self.refreshSearchCache === 'function') {
                self.refreshSearchCache();
            }
        },

        _lucidchartModalRefs: function() {
            return {
                modal: document.getElementById('lucidchart-import-modal'),
                status: document.getElementById('lucidchart-import-status'),
                statusTitle: document.getElementById('lucidchart-import-status-title'),
                statusDetail: document.getElementById('lucidchart-import-status-detail'),
                authHint: document.getElementById('lucidchart-auth-hint'),
                list: document.getElementById('lucidchart-document-list'),
                uploadInput: document.getElementById('lucidchart-upload-input'),
                uploadDropzone: document.getElementById('lucidchart-upload-dropzone'),
                uploadSelection: document.getElementById('lucidchart-upload-selection'),
                uploadFileName: document.getElementById('lucidchart-upload-file-name'),
                uploadFileMeta: document.getElementById('lucidchart-upload-file-meta'),
                importBtn: document.getElementById('lucidchart-import-selected-btn'),
                uploadBtn: document.getElementById('lucidchart-upload-btn'),
                connectBtn: document.getElementById('lucidchart-connect-btn'),
                refreshBtn: document.getElementById('lucidchart-refresh-btn'),
                clearFileBtn: document.getElementById('lucidchart-clear-file-btn'),
                changeFileBtn: document.getElementById('lucidchart-change-file-btn'),
                summary: document.getElementById('lucidchart-import-summary'),
                summaryTitle: document.getElementById('lucidchart-import-summary-title'),
                summaryMeta: document.getElementById('lucidchart-import-summary-meta'),
                warningList: document.getElementById('lucidchart-import-warning-list'),
            };
        },

        _setLucidchartStatus: function(tone, title, detail) {
            let refs = this._lucidchartModalRefs();
            if (!refs.status) return;
            let tones = {
                info: 'rounded-xl border border-border bg-muted/30 px-4 py-3',
                loading: 'rounded-xl border border-primary/20 bg-primary/5 px-4 py-3',
                success: 'rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-4 py-3',
                warning: 'rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3',
                error: 'rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3',
            };
            refs.status.className = tones[tone] || tones.info;
            if (refs.statusTitle) refs.statusTitle.textContent = title || 'Ready to import';
            if (refs.statusDetail) {
                refs.statusDetail.textContent = detail || 'Select a Lucid export to replace the current scratch canvas, or use the workspace path if your organization is already connected.';
            }
        },

        _setLucidchartBusy: function(isBusy) {
            let refs = this._lucidchartModalRefs();
            [refs.importBtn, refs.uploadBtn, refs.connectBtn, refs.refreshBtn, refs.clearFileBtn, refs.changeFileBtn].forEach(function(btn) {
                _setDisabled(btn, isBusy);
            });
            if (refs.uploadDropzone) {
                refs.uploadDropzone.classList.toggle('opacity-60', !!isBusy);
                refs.uploadDropzone.classList.toggle('pointer-events-none', !!isBusy);
            }
            if (refs.uploadInput) refs.uploadInput.disabled = !!isBusy;
            if (!isBusy) this._renderLucidchartSelectedFile();
        },

        _resetLucidchartImportSummary: function() {
            let refs = this._lucidchartModalRefs();
            if (!refs.summary) return;
            refs.summary.classList.add('hidden');
            if (refs.summaryTitle) refs.summaryTitle.textContent = 'Import complete';
            if (refs.summaryMeta) refs.summaryMeta.textContent = '';
            if (refs.warningList) refs.warningList.innerHTML = '';
        },

        _renderLucidchartImportSummary: function(data, sourceLabel) {
            let refs = this._lucidchartModalRefs();
            if (!refs.summary) return;
            let elements = (data && data.elements) || [];
            let relationships = (data && data.relationships) || [];
            let warnings = (data && data.warnings) || [];
            let source = sourceLabel || 'Lucid import';

            refs.summary.classList.remove('hidden');
            if (refs.summaryTitle) refs.summaryTitle.textContent = 'Import complete';
            if (refs.summaryMeta) {
                refs.summaryMeta.textContent = source + ' — ' + elements.length + ' elements, ' + relationships.length + ' relationships' +
                    (warnings.length ? ', ' + warnings.length + ' warnings' : ', no import warnings');
            }
            if (refs.warningList) {
                refs.warningList.innerHTML = '';
                if (!warnings.length) {
                    let okItem = document.createElement('li');
                    okItem.textContent = 'Entelim imported the diagram without unresolved warnings.';
                    refs.warningList.appendChild(okItem);
                } else {
                    warnings.forEach(function(warning) {
                        let item = document.createElement('li');
                        item.textContent = warning;
                        refs.warningList.appendChild(item);
                    });
                }
            }
        },

        _renderLucidchartSelectedFile: function() {
            let refs = this._lucidchartModalRefs();
            let file = this._lucidchartSelectedFile || null;
            if (!refs.uploadSelection || !refs.importBtn) return;

            if (!file) {
                refs.uploadSelection.classList.add('hidden');
                if (refs.uploadFileName) refs.uploadFileName.textContent = '';
                if (refs.uploadFileMeta) refs.uploadFileMeta.textContent = '';
                _setDisabled(refs.importBtn, true);
                return;
            }

            refs.uploadSelection.classList.remove('hidden');
            if (refs.uploadFileName) refs.uploadFileName.textContent = file.name || 'Lucid export';
            if (refs.uploadFileMeta) refs.uploadFileMeta.textContent = _formatLucidchartFileSize(file.size) + ' • Scratch canvas will be replaced on import';
            _setDisabled(refs.importBtn, false);
        },

        openLucidchartImportModal: function() {
            let refs = this._lucidchartModalRefs();
            if (!refs.modal) return;
            refs.modal.classList.remove('hidden');
            refs.modal.setAttribute('aria-hidden', 'false');
            this._lucidchartSelectedFile = null;
            if (refs.uploadInput) refs.uploadInput.value = '';
            if (refs.authHint) refs.authHint.classList.add('hidden');
            if (refs.list) {
                refs.list.innerHTML = '<div class="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">Workspace import is optional. Connect Lucidchart or load documents when you need it.</div>';
            }
            this._setLucidchartBusy(false);
            this._resetLucidchartImportSummary();
            this._renderLucidchartSelectedFile();
            this.highlightLucidchartDropzone(false);
            this._setLucidchartStatus('info', 'Ready to import', 'Select a Lucid export to replace the current scratch canvas, or use the workspace path if your organization is already connected.');
        },

        closeLucidchartImportModal: function() {
            let refs = this._lucidchartModalRefs();
            if (!refs.modal) return;
            refs.modal.classList.add('hidden');
            refs.modal.setAttribute('aria-hidden', 'true');
        },

        chooseLucidchartUploadFile: function() {
            let refs = this._lucidchartModalRefs();
            if (refs.uploadInput && !refs.uploadInput.disabled) refs.uploadInput.click();
        },

        highlightLucidchartDropzone: function(isActive) {
            let refs = this._lucidchartModalRefs();
            if (!refs.uploadDropzone) return;
            refs.uploadDropzone.classList.toggle('border-primary', !!isActive);
            refs.uploadDropzone.classList.toggle('bg-primary/10', !!isActive);
        },

        _acceptLucidchartFile: function(file) {
            if (!file) return;
            this.highlightLucidchartDropzone(false);
            let lowerName = String(file.name || '').toLowerCase();
            if (!(lowerName.endsWith('.json') || lowerName.endsWith('.lucid'))) {
                this._setLucidchartStatus('error', 'Unsupported file type', 'Choose a Lucid JSON or .lucid export before importing.');
                _toast('error', 'Choose a Lucid JSON or .lucid export');
                return;
            }
            if (file.size > 10 * 1024 * 1024) {
                this._setLucidchartStatus('error', 'File too large', 'Choose a Lucid export smaller than 10 MB.');
                _toast('error', 'File exceeds 10MB limit');
                return;
            }
            this._lucidchartSelectedFile = file;
            this._resetLucidchartImportSummary();
            this._renderLucidchartSelectedFile();
            this._setLucidchartStatus('info', 'Lucid export ready', 'Review the import notes, then import the selected file into the scratch canvas.');
        },

        handleLucidchartFileSelection: function(event) {
            let refs = this._lucidchartModalRefs();
            let file = event && event.target && event.target.files ? event.target.files[0] : null;
            if (refs.uploadInput && !file) refs.uploadInput.value = '';
            this._acceptLucidchartFile(file);
        },

        handleLucidchartFileDrop: function(event) {
            this.highlightLucidchartDropzone(false);
            let refs = this._lucidchartModalRefs();
            if (refs.uploadInput) refs.uploadInput.value = '';
            let file = event && event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files[0] : null;
            this._acceptLucidchartFile(file);
        },

        clearLucidchartSelectedFile: function() {
            let refs = this._lucidchartModalRefs();
            this._lucidchartSelectedFile = null;
            if (refs.uploadInput) refs.uploadInput.value = '';
            this._renderLucidchartSelectedFile();
            this._setLucidchartStatus('info', 'Ready to import', 'Choose another Lucid export, or use the workspace path if your organization is already connected.');
        },

        _renderLucidchartDocuments: function(documents) {
            let self = this;
            let refs = self._lucidchartModalRefs();
            if (!refs.list) return;
            if (!documents || documents.length === 0) {
                refs.list.innerHTML = '<div class="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">No Lucidchart documents available for this organization.</div>';
                return;
            }

            refs.list.innerHTML = documents.map(function(doc) {
                let title = doc.title || doc.name || doc.id;
                let meta = doc.updatedAt || doc.modifiedAt || doc.product || '';
                return '' +
                    '<div class="rounded-lg border border-border bg-background px-3 py-3 flex items-center justify-between gap-3">' +
                        '<div class="min-w-0">' +
                            '<div class="text-sm font-medium text-foreground truncate">' + _escapeHtml(title) + '</div>' +
                            '<div class="text-xs text-muted-foreground truncate">' + _escapeHtml(meta) + '</div>' +
                        '</div>' +
                        '<button type="button" class="toolbar-btn toolbar-btn-sm lucidchart-import-doc-btn" data-document-id="' + _escapeHtml(doc.id) + '" data-document-title="' + _escapeHtml(title) + '">' +
                            '<i data-lucide="download" class="w-3.5 h-3.5"></i> Import' +
                        '</button>' +
                    '</div>';
            }).join('');

            refs.list.querySelectorAll('.lucidchart-import-doc-btn').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    self.importSelectedLucidchartDocument(
                        btn.getAttribute('data-document-id'),
                        btn.getAttribute('data-document-title')
                    );
                });
            });
            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                window.lucide.createIcons({ attrs: { 'stroke-width': 1.75 } });
            }
        },

        loadLucidchartDocuments: function() {
            let self = this;
            let refs = self._lucidchartModalRefs();
            if (!refs.status || !refs.list) return;
            self._setLucidchartStatus('loading', 'Checking workspace connection', 'Entelim is loading Lucidchart documents for this organization.');
            refs.authHint && refs.authHint.classList.add('hidden');
            refs.list.innerHTML = '<div class="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">Loading workspace documents…</div>';

            Platform.fetch.get('/archimate/api/lucidchart/documents', null, { silent: true })
            .then(function(data) {
                if (data.needs_auth) {
                    self._setLucidchartStatus('warning', 'Workspace not connected', 'Upload a Lucid export now, or connect Lucidchart to browse workspace documents later.');
                    if (refs.authHint) refs.authHint.classList.remove('hidden');
                    refs.list.innerHTML = '<div class="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-muted-foreground">No workspace documents available until Lucidchart is connected for this organization.</div>';
                    return;
                }
                self._setLucidchartStatus('info', 'Workspace ready', 'Choose a workspace document to import directly, or continue with file upload.');
                self._renderLucidchartDocuments(data.documents || []);
            })
            .catch(function(err) {
                self._setLucidchartStatus('error', 'Workspace load failed', 'Entelim could not load Lucidchart documents right now.');
                _toast('error', (err && err.data && err.data.error) || ('Lucidchart load error: ' + (err.message || '')));
            });
        },

        connectLucidchart: function() {
            let self = this;
            let refs = self._lucidchartModalRefs();
            self._setLucidchartStatus('loading', 'Starting workspace connection', 'Lucidchart authorization will open in a new browser tab.');
            Platform.fetch.get('/archimate/api/lucidchart/auth/start', null, { silent: true })
            .then(function(data) {
                self._setLucidchartStatus('info', 'Finish sign-in in the new tab', 'After Lucidchart sign-in completes, return here and load workspace documents.');
                window.open(data.authorization_url, '_blank', 'noopener');
            })
            .catch(function(err) {
                self._setLucidchartStatus('error', 'Workspace connection failed', 'Lucidchart authorization did not complete successfully.');
                _toast('error', (err && err.data && err.data.error) || ('Lucidchart authorization error: ' + (err.message || '')));
            });
        },

        importSelectedLucidchartDocument: function(documentId, documentTitle) {
            let self = this;
            let refs = self._lucidchartModalRefs();
            self._setLucidchartBusy(true);
            self._resetLucidchartImportSummary();
            self._setLucidchartStatus('loading', 'Importing workspace document', 'Entelim is converting the selected Lucidchart document into the composer canvas.');
            Platform.fetch.post('/archimate/api/lucidchart/import/' + encodeURIComponent(documentId), null, { silent: true })
            .then(function(data) {
                if (data.needs_auth) {
                    self._setLucidchartStatus('warning', 'Workspace not connected', 'Connect Lucidchart before importing a live workspace document.');
                    if (refs.authHint) refs.authHint.classList.remove('hidden');
                    _toast('warning', 'Connect Lucidchart before importing a live document.');
                    self._setLucidchartBusy(false);
                    return;
                }
                self.applyImportedDiagramPayload(data, 'Lucidchart import');
                self._renderLucidchartImportSummary(data, documentTitle || 'Workspace document');
                self._setLucidchartStatus('success', 'Workspace document imported', 'Review the imported counts and warnings below, then close this window to inspect the diagram.');
                self._setLucidchartBusy(false);
            })
            .catch(function(err) {
                self._setLucidchartStatus('error', 'Workspace import failed', 'Entelim could not import the selected Lucidchart document.');
                _toast('error', (err && err.data && err.data.error) || ('Lucidchart import error: ' + (err.message || '')));
                self._setLucidchartBusy(false);
            });
        },

        importLucidchartUpload: function() {
            let self = this;
            let refs = self._lucidchartModalRefs();
            let file = self._lucidchartSelectedFile || null;
            if (!file) {
                self._setLucidchartStatus('warning', 'No export selected', 'Choose a Lucid JSON or .lucid export before starting the import.');
                _toast('warning', 'Choose a Lucid export before importing');
                return;
            }

            self._setLucidchartBusy(true);
            self._resetLucidchartImportSummary();
            self._setLucidchartStatus('loading', 'Uploading Lucid export', 'Entelim is converting the selected export into the composer canvas.');

            let formData = new FormData();
            formData.append('file', file);
            Platform.fetch.post('/archimate/api/lucidchart/import/upload', formData, { silent: true })
            .then(function(data) {
                self.applyImportedDiagramPayload(data, 'Lucidchart upload');
                self._renderLucidchartImportSummary(data, file.name || 'Lucid export');
                self._lucidchartSelectedFile = null;
                if (refs.uploadInput) refs.uploadInput.value = '';
                self._renderLucidchartSelectedFile();
                self._setLucidchartStatus('success', 'Import complete', 'Review the imported counts and warnings below, then close this window to inspect the diagram.');
                self._setLucidchartBusy(false);
            })
            .catch(function(err) {
                self._setLucidchartStatus('error', 'Upload import failed', 'Entelim could not import the selected Lucid export.');
                _toast('error', (err && err.data && err.data.error) || ('Lucidchart upload error: ' + (err.message || '')));
                self._setLucidchartBusy(false);
            });
        },

        saveSnapshot: async function() {
            let self = this;
            if (!self.currentSavedVpId) {
                self.statusText = 'Save the viewpoint first before creating a snapshot';
                return;
            }
            let name = await Platform.modal.promptText('Snapshot name:', {
                title: 'Create snapshot', defaultValue: 'v' + (self.snapshots.length + 1)
            });
            if (!name || !name.trim()) return;

            self.statusText = 'Creating snapshot...';
            Platform.fetch.post('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/snapshots', { name: name.trim() }, { silent: true })
            .then(function(data) {
                if (data.id) {
                    self.statusText = 'Snapshot created: ' + data.name;
                    self.snapshots.unshift(data);
                } else {
                    self.statusText = 'Snapshot failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Snapshot error: ' + (err.message || ''); _toast('error', 'Snapshot failed'); });
        },

        loadSnapshots: function() {
            let self = this;
            self.snapshotListOpen = !self.snapshotListOpen;
            if (!self.snapshotListOpen) return;
            if (!self.currentSavedVpId) {
                self.snapshots = [];
                return;
            }

            Platform.fetch.get('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/snapshots', null, { silent: true })
            .then(function(data) {
                self.snapshots = data.snapshots || [];
            })
            .catch(function() { self.snapshots = []; _toast('error', 'Failed to load snapshots'); });
        },

        viewSnapshot: function(sid) {
            let self = this;
            if (!self.currentSavedVpId) return;
            self.snapshotListOpen = false;
            self.statusText = 'Loading snapshot...';

            Platform.fetch.get('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/snapshots/' + sid, null, { silent: true })
            .then(function(data) {
                if (!data.data) {
                    self.statusText = 'Snapshot load failed';
                    return;
                }
                let snapData = data.data;
                UndoStack.pause();
                self.graph.clear();
                self.canvasElements = {};

                let elements = snapData.elements || [];
                let relationships = snapData.relationships || [];

                let cellMap = {};
                let elementMap = {};
                elements.forEach(function(el) {
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let renderMode = el.rendering_mode || 'black_box';
                    let node;
                    if (renderMode === 'white_box' && isContainerType(el.type)) {
                        node = createContainerNode(el.id, el.name, el.type || 'ApplicationComponent', layer,
                                                   el.x || 0, el.y || 0, el.width || 320, el.height || 220);
                    } else {
                        node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer,
                                          el.x || 0, el.y || 0);
                        /* Auto-migrate pre-v4 nodes saved at the old 200×70 default */
                        if (el.width && el.height && el.height > 75) {
                            node.resize(el.width, el.height);
                        }
                    }
                    /* GAP-INT-002: Restore zone type for Grouping/Location */
                    if (el.zone_type) {
                        node.set('zoneType', el.zone_type);
                    }
                    self.graph.addCell(node);
                    cellMap[el.id] = node;
                    elementMap[el.id] = el;
                    self.canvasElements[el.id] = el;
                });

                relationships.forEach(function(rel) {
                    let srcCell = cellMap[rel.source_id];
                    let tgtCell = cellMap[rel.target_id];
                    if (!srcCell || !tgtCell) return;
                    let link = createLink(srcCell, tgtCell, rel.type || 'association', rel.id);
                    if (rel.waypoints && Array.isArray(rel.waypoints) && rel.waypoints.length > 0) {
                        link.vertices(rel.waypoints);
                    }
                    if (rel.label) {
                        link.set('customLabel', rel.label);
                        applyCustomLabel(link, rel.label);
                    }
                    /* BUG-CMP-002: Restore relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) {
                        link.set('customLabel', rel.custom_label);
                        applyCustomLabel(link, rel.custom_label);
                    }
                    self.graph.addCell(link);
                });

                self.elementCount = elements.length;
                self.relCount = relationships.length;
                self.mode = 'view';
                self.customProperties = {};
                if (snapData.description) {
                    /* Same legacy-format caveat as loadSavedViewpoint() — plain-text
                       descriptions on older snapshots are expected to fail JSON.parse. */
                    try {
                        let ext = JSON.parse(snapData.description);
                        if (ext && ext._canvas_ext && ext.custom_properties) {
                            self.customProperties = ext.custom_properties;
                        }
                    } catch (e) { /* swallow-ok: same legacy plain-text description case as loadSavedViewpoint; custom properties stay empty */ }
                }
                _applySavedImportedPresentation(self, cellMap, elementMap);
                UndoStack.resume();
                self.statusText = 'Viewing snapshot: ' + data.name;

                self.$nextTick(function() {
                    self.fitCanvas();
                    if (window.lucide) lucide.createIcons();
                });
            })
            .catch(function(err) {
                self.statusText = 'Snapshot load error';
                _toast('error', 'Failed to load snapshot');
            });
        },

        restoreSnapshot: async function(sid) {
            let self = this;
            if (!self.currentSavedVpId) return;
            if (!(await Platform.modal.confirm('Restore this snapshot? Current diagram positions will be replaced.'))) return;

            self.snapshotListOpen = false;
            self.statusText = 'Restoring snapshot...';

            Platform.fetch.post('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/snapshots/' + sid + '/restore', null, { silent: true })
            .then(function(data) {
                if (data.restored) {
                    self.statusText = 'Restored: ' + data.snapshot_name;
                    self.loadSavedViewpoint(self.currentSavedVpId, self.activeViewpointName);
                } else {
                    self.statusText = 'Restore failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Restore error: ' + (err.message || ''); _toast('error', 'Restore failed'); });
        },

        saveAsTemplate: async function() {
            let self = this;
            let elements = self.graph.getElements();
            if (elements.length === 0) {
                self.statusText = 'No elements to save as template';
                return;
            }

            let name = await Platform.modal.promptText('Template name:', { title: 'Save as template' });
            if (!name || !name.trim()) return;

            /* Extract layout structure: types + positions, strip element IDs */
            let templateElements = elements.map(function(cell) {
                let pos = cell.position();
                let size = cell.size();
                return {
                    element_type: cell.get('elementType') || 'ApplicationComponent',
                    element_layer: cell.get('elementLayer') || 'application',
                    placeholder_name: cell.get('elementName') || cell.attr('label/text') || 'Element',
                    x: Math.round(pos.x),
                    y: Math.round(pos.y),
                    width: size.width,
                    height: size.height,
                    rendering_mode: cell.get('renderingMode') || 'black_box',
                    zone_type: cell.get('zoneType') || null,
                };
            });

            let templateData = {
                elements: templateElements,
                element_count: templateElements.length,
            };

            self.statusText = 'Saving template...';
            Platform.fetch.post('/archimate/api/templates', {
                name: name.trim(),
                viewpoint_type: self.activeViewpoint || null,
                template_json: templateData,
            }, { silent: true })
            .then(function(data) {
                if (data.id) {
                    self.statusText = 'Template saved: ' + data.name;
                    self.templates.unshift(data);
                } else {
                    self.statusText = 'Template save failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Template error: ' + (err.message || ''); _toast('error', 'Template operation failed'); });
        },

        loadTemplates: function() {
            let self = this;
            self.templateListOpen = !self.templateListOpen;
            if (!self.templateListOpen) return;

            Platform.fetch.get('/archimate/api/templates', null, { silent: true })
            .then(function(data) {
                self.templates = BUILT_IN_TEMPLATES.concat(data.templates || []);
            })
            .catch(function() {
                self.templates = BUILT_IN_TEMPLATES;
                self.templateListOpen = true;
                _toast('error', 'Failed to load templates from server');
            });
        },

        loadBuiltinTemplate: function(tpl) {
            let self = this;
            self.templateListOpen = false;
            UndoStack.pause();

            let existingBBox = self.graph.getBBox();
            let offsetX = existingBBox && existingBBox.width > 0 ? existingBBox.x + existingBBox.width + 80 : 40;
            let offsetY = 40;

            let cellMap = {};
            tpl.elements.forEach(function(el) {
                let x = offsetX + el.x;
                let y = offsetY + el.y;
                let tplId = '__builtin__' + el.id;
                let node;
                if (el.container && isContainerType(el.type)) {
                    node = createContainerNode(tplId, el.name, el.type, el.layer, x, y, el.w || 320, el.h || 220);
                } else {
                    node = createNode(tplId, el.name, el.type, el.layer, x, y);
                }
                self.graph.addCell(node);
                cellMap[el.id] = node;
                self.elementCount++;
            });

            /* Create relationship links */
            const rels = tpl.relationships || [];
            rels.forEach(function(rel) {
                let src = cellMap[rel.source];
                let tgt = cellMap[rel.target];
                if (src && tgt) {
                    let link = createLink(src, tgt, rel.type || 'association', null);
                    self.graph.addCell(link);
                    self.relCount++;
                }
            });

            UndoStack.resume();
            self.paper.scaleContentToFit({ padding: 40, maxScale: 1 });
            self.zoomPercent = Math.round(self.paper.scale().sx * 100);
            self.statusText = 'Template loaded: ' + tpl.name;
            self._scheduleMiniMapUpdate();
        },

        instantiateTemplate: async function(tid) {
            let self = this;
            let name = await Platform.modal.promptText('New viewpoint name:', { title: 'Create from template' });
            if (!name || !name.trim()) return;

            self.templateListOpen = false;
            self.statusText = 'Creating from template...';

            Platform.fetch.post('/archimate/api/templates/' + tid + '/instantiate', {
                name: name.trim(),
                solution_id: self.solutionId || null,
            }, { silent: true })
            .then(function(data) {
                if (data.id) {
                    self.statusText = 'Created viewpoint: ' + data.name;
                    self.loadSavedViewpoint(data.id, data.name);
                } else {
                    self.statusText = 'Instantiate failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Instantiate error: ' + (err.message || ''); _toast('error', 'Failed to instantiate template'); });
        },

        /* ── BUG-2 FIX: open templates modal pre-focused on portfolio section ── */
        openBulkImport: function() {
            this.templateListOpen = true;
            this.portfolioSectionOpen = true;
            this.loadPortfolioTemplates();
        },

        /* ── CMP2-006: Portfolio-generated templates ── */
        loadPortfolioTemplates: function() {
            let self = this;
            self.portfolioTemplatesLoading = true;
            self.portfolioTemplates = [];

            Platform.fetch.get('/archimate/api/composer/portfolio-templates', null, { silent: true })
            .then(function(data) {
                self.portfolioTemplates = data.portfolio_templates || [];
                self.portfolioTemplatesLoading = false;
                self.$nextTick(function() { if (window.lucide) lucide.createIcons(); });
            })
            .catch(function() {
                self.portfolioTemplates = [];
                self.portfolioTemplatesLoading = false;
                _toast('error', 'Failed to load portfolio templates');
            });
        },

        applyPortfolioTemplate: function(tpl) {
            let self = this;
            self.templateListOpen = false;
            UndoStack.pause();

            let existingBBox = self.graph.getBBox();
            let offsetX = existingBBox && existingBBox.width > 0 ? existingBBox.x + existingBBox.width + 80 : 40;
            let offsetY = 40;

            let nodeMap = {};

            (tpl.elements || []).forEach(function(el) {
                let x = offsetX + (el.x || 0);
                let y = offsetY + (el.y || 0);
                let node = createNode(
                    el.id || ('portfolio_' + Math.random().toString(36).substr(2, 8)),
                    el.name,
                    el.type || 'ApplicationComponent',
                    el.layer || 'application',
                    x, y
                );
                self.graph.addCell(node);
                nodeMap[el.id] = node;
                self.elementCount++;
            });

            /* Add relationships as links between placed elements */
            let _createLink = ComposerRenderer.createLink;
            (tpl.relationships || []).forEach(function(rel) {
                let srcNode = nodeMap[rel.source];
                let tgtNode = nodeMap[rel.target];
                if (srcNode && tgtNode && _createLink) {
                    let link = _createLink(srcNode, tgtNode, rel.type || 'AssociationRelationship', rel.source + '_' + rel.target);
                    if (link) {
                        self.graph.addCell(link);
                    }
                }
            });

            UndoStack.resume();
            self.paper.scaleContentToFit({ padding: 40, maxScale: 1 });
            self.zoomPercent = Math.round(self.paper.scale().sx * 100);
            self.statusText = 'Portfolio template loaded: ' + tpl.name;
            self._scheduleMiniMapUpdate();
            _toast('success', 'Loaded ' + (tpl.element_count || 0) + ' applications from ' + (tpl.domain || 'portfolio'));
        },

        /* ── CMP-051 / CMP2-010: Snapshot visual diff ── */
        compareSnapshots: function(snapshotIdA, snapshotIdB) {
            let self = this;
            if (!self.currentSavedVpId) { _toast('warning', 'Save viewpoint first'); return; }

            self.statusText = 'Loading snapshots for comparison…';
            let baseUrl = '/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/snapshots/';

            Promise.all([
                Platform.fetch.get(baseUrl + snapshotIdA, null, { silent: true }),
                Platform.fetch.get(baseUrl + snapshotIdB, null, { silent: true }),
            ])
            .then(function(results) {
                let snapA = results[0];
                let snapB = results[1];
                self._applySnapshotDiff(snapA, snapB);
            })
            .catch(function(e) {
                _toast('error', 'Compare failed');
                self.statusText = 'Compare failed: ' + (e.message || '');
            });
        },

        /* CMP2-010: Open the version comparison selector overlay */
        openVersionCompare: function() {
            let self = this;
            if (!self.currentSavedVpId) { _toast('warning', 'Save viewpoint first'); return; }
            if (self.snapshots.length < 2) {
                _toast('info', 'Need at least 2 snapshots to compare. Create more snapshots first.');
                return;
            }
            self.versionCompareA = self.snapshots.length > 1 ? self.snapshots[1].id : null;
            self.versionCompareB = self.snapshots[0].id;
            self.versionCompareOpen = true;
        },

        /* CMP2-010: Execute comparison from the version selector */
        executeVersionCompare: function() {
            let self = this;
            if (!self.versionCompareA || !self.versionCompareB) {
                _toast('warning', 'Select two versions to compare');
                return;
            }
            if (self.versionCompareA === self.versionCompareB) {
                _toast('warning', 'Select two different versions');
                return;
            }
            self.versionCompareOpen = false;
            self.compareSnapshots(self.versionCompareA, self.versionCompareB);
        },

        _applySnapshotDiff: function(snapA, snapB) {
            let self = this;
            let elemsA = {};
            let elemsB = {};
            let relsA = {};
            let relsB = {};

            /* Index elements by element_id (snapshot data uses element_id or id) */
            (snapA.elements || []).forEach(function(e) {
                let eid = e.element_id || e.id;
                elemsA[eid] = e;
            });
            (snapB.elements || []).forEach(function(e) {
                let eid = e.element_id || e.id;
                elemsB[eid] = e;
            });

            /* Index relationships by relationship_id or id */
            (snapA.relationships || []).forEach(function(r) {
                let rid = r.relationship_id || r.id;
                relsA[rid] = r;
            });
            (snapB.relationships || []).forEach(function(r) {
                let rid = r.relationship_id || r.id;
                relsB[rid] = r;
            });

            let addedEls = [];
            let removedEls = [];
            let movedEls = [];
            let unchangedEls = [];

            Object.keys(elemsB).forEach(function(eid) {
                if (!elemsA[eid]) {
                    addedEls.push({ id: eid, name: elemsB[eid].name || eid, type: elemsB[eid].type || '' });
                } else {
                    let a = elemsA[eid];
                    let b = elemsB[eid];
                    let dx = (b.x || 0) - (a.x || 0);
                    let dy = (b.y || 0) - (a.y || 0);
                    let dist = Math.abs(dx) + Math.abs(dy);
                    if (dist > 20) {
                        movedEls.push({ id: eid, name: elemsB[eid].name || eid, type: elemsB[eid].type || '', dx: dx, dy: dy });
                    } else {
                        unchangedEls.push({ id: eid, name: elemsB[eid].name || eid });
                    }
                }
            });
            Object.keys(elemsA).forEach(function(eid) {
                if (!elemsB[eid]) {
                    removedEls.push({ id: eid, name: elemsA[eid].name || eid, type: elemsA[eid].type || '' });
                }
            });

            /* Relationship diff */
            let addedRels = [];
            let removedRels = [];

            Object.keys(relsB).forEach(function(rid) {
                if (!relsA[rid]) {
                    addedRels.push({ id: rid, type: relsB[rid].type || 'association', source_id: relsB[rid].source_id, target_id: relsB[rid].target_id });
                }
            });
            Object.keys(relsA).forEach(function(rid) {
                if (!relsB[rid]) {
                    removedRels.push({ id: rid, type: relsA[rid].type || 'association', source_id: relsA[rid].source_id, target_id: relsA[rid].target_id });
                }
            });

            /* Set diff state */
            self.snapshotDiffActive = true;
            self.snapshotDiffStats = {
                added: addedEls.length,
                removed: removedEls.length,
                moved: movedEls.length,
                unchanged: unchangedEls.length,
                relsAdded: addedRels.length,
                relsRemoved: removedRels.length,
            };

            /* CMP2-010: Detailed diff for the summary panel */
            self.diffDetails = {
                addedElements: addedEls,
                removedElements: removedEls,
                movedElements: movedEls,
                unchangedElements: unchangedEls,
                addedRelationships: addedRels,
                removedRelationships: removedRels,
            };
            self.diffSummaryOpen = true;

            /* Build lookup of added/moved/removed element IDs for fast access */
            let addedIds = {};
            addedEls.forEach(function(e) { addedIds[e.id] = true; addedIds[String(e.id)] = true; });
            let movedIds = {};
            let movedMap = {};
            movedEls.forEach(function(e) { movedIds[e.id] = true; movedIds[String(e.id)] = true; movedMap[e.id] = e; movedMap[String(e.id)] = e; });
            let removedIds = {};
            removedEls.forEach(function(e) { removedIds[e.id] = true; removedIds[String(e.id)] = true; });

            /* Build lookup of added/removed relationship IDs */
            let addedRelIds = {};
            addedRels.forEach(function(r) { addedRelIds[r.id] = true; addedRelIds[String(r.id)] = true; });
            let removedRelIds = {};
            removedRels.forEach(function(r) { removedRelIds[r.id] = true; removedRelIds[String(r.id)] = true; });

            /* Clear previous overlay cells */
            self._diffOverlayCells = [];

            /* Apply visual overlays on elements */
            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let eid = cell.get('elementId');
                let view = self.paper.findViewByModel(cell);
                if (!view) return;

                if (addedIds[eid] || addedIds[String(eid)]) {
                    /* Added: green highlight with NEW badge */
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#22c55e', 'stroke-width': 3 } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    self._addDiffBadge(cell, 'NEW', '#22c55e');
                } else if (movedIds[eid] || movedIds[String(eid)]) {
                    /* Moved: blue highlight + displacement arrow */
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#3b82f6', 'stroke-width': 3, 'stroke-dasharray': '6,3' } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    let moveInfo = movedMap[eid] || movedMap[String(eid)];
                    if (moveInfo) {
                        self._addDisplacementArrow(cell, moveInfo.dx, moveInfo.dy);
                    }
                } else if (removedIds[eid] || removedIds[String(eid)]) {
                    /* Removed: red dashed outline at 50% opacity with REMOVED badge */
                    view.vel.attr({ opacity: 0.5 });
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#ef4444', 'stroke-width': 2, 'stroke-dasharray': '4,4' } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    self._addDiffBadge(cell, 'REMOVED', '#ef4444');
                }
            });

            /* Apply visual overlays on relationships (links) */
            self.graph.getLinks().forEach(function(link) {
                let rid = link.get('relId');
                if (!rid) return;
                let view = self.paper.findViewByModel(link);
                if (!view) return;

                if (addedRelIds[rid] || addedRelIds[String(rid)]) {
                    /* Added relationship: green line */
                    link.attr('line/stroke', '#22c55e');
                    link.attr('line/strokeWidth', 3);
                    link.attr('line/strokeDasharray', '');
                } else if (removedRelIds[rid] || removedRelIds[String(rid)]) {
                    /* Removed relationship: red dashed line */
                    link.attr('line/stroke', '#ef4444');
                    link.attr('line/strokeWidth', 2);
                    link.attr('line/strokeDasharray', '6,4');
                    link.attr('line/opacity', 0.6);
                }
            });

            self.statusText = 'Diff: +' + addedEls.length + ' / -' + removedEls.length + ' elements, ~' + movedEls.length + ' moved | +' + addedRels.length + ' / -' + removedRels.length + ' relationships';
        },

        /* CMP2-010: Add a badge label above a cell (SVG foreignObject) */
        _addDiffBadge: function(cell, label, color) {
            let self = this;
            let pos = cell.position();
            let size = cell.size();
            /* Create an SVG text overlay using a small JointJS rect shape */
            let badgeEl = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            badgeEl.classList.add('diff-badge-overlay');
            let rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            let text = document.createElementNS('http://www.w3.org/2000/svg', 'text');

            let bx = pos.x + size.width / 2;
            let by = pos.y - 12;
            let tw = label === 'NEW' ? 34 : 62;

            rect.setAttribute('x', bx - tw / 2);
            rect.setAttribute('y', by - 9);
            rect.setAttribute('width', tw);
            rect.setAttribute('height', 16);
            rect.setAttribute('rx', 4);
            rect.setAttribute('fill', color);
            rect.setAttribute('opacity', '0.9');

            text.setAttribute('x', bx);
            text.setAttribute('y', by + 3);
            text.setAttribute('text-anchor', 'middle');
            text.setAttribute('fill', 'white');
            text.setAttribute('font-size', '9');
            text.setAttribute('font-weight', '700');
            text.setAttribute('font-family', 'Inter, system-ui, sans-serif');
            text.textContent = label;

            badgeEl.appendChild(rect);
            badgeEl.appendChild(text);

            /* Append to the paper's SVG viewport */
            let viewport = self.paper.viewport;
            if (viewport) {
                viewport.appendChild(badgeEl);
                self._diffOverlayCells.push(badgeEl);
            }
        },

        /* CMP2-010: Add a displacement arrow showing where element moved from */
        _addDisplacementArrow: function(cell, dx, dy) {
            let self = this;
            let pos = cell.position();
            let size = cell.size();

            let cx = pos.x + size.width / 2;
            let cy = pos.y + size.height / 2;
            /* Arrow starts from previous position and points to current */
            let fromX = cx - dx;
            let fromY = cy - dy;

            let arrowG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            arrowG.classList.add('diff-badge-overlay');

            /* Arrow line */
            let line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', fromX);
            line.setAttribute('y1', fromY);
            line.setAttribute('x2', cx);
            line.setAttribute('y2', cy);
            line.setAttribute('stroke', '#3b82f6');
            line.setAttribute('stroke-width', '2');
            line.setAttribute('stroke-dasharray', '4,3');
            line.setAttribute('opacity', '0.7');
            arrowG.appendChild(line);

            /* Arrowhead */
            let angle = Math.atan2(cy - fromY, cx - fromX);
            let headLen = 8;
            let p1x = cx - headLen * Math.cos(angle - Math.PI / 6);
            let p1y = cy - headLen * Math.sin(angle - Math.PI / 6);
            let p2x = cx - headLen * Math.cos(angle + Math.PI / 6);
            let p2y = cy - headLen * Math.sin(angle + Math.PI / 6);

            let arrowHead = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
            arrowHead.setAttribute('points', cx + ',' + cy + ' ' + p1x + ',' + p1y + ' ' + p2x + ',' + p2y);
            arrowHead.setAttribute('fill', '#3b82f6');
            arrowHead.setAttribute('opacity', '0.7');
            arrowG.appendChild(arrowHead);

            /* Old position ghost circle */
            let ghost = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            ghost.setAttribute('cx', fromX);
            ghost.setAttribute('cy', fromY);
            ghost.setAttribute('r', '5');
            ghost.setAttribute('fill', 'none');
            ghost.setAttribute('stroke', '#3b82f6');
            ghost.setAttribute('stroke-width', '1.5');
            ghost.setAttribute('stroke-dasharray', '3,2');
            ghost.setAttribute('opacity', '0.5');
            arrowG.appendChild(ghost);

            let viewport = self.paper.viewport;
            if (viewport) {
                viewport.appendChild(arrowG);
                self._diffOverlayCells.push(arrowG);
            }
        },

        clearSnapshotDiff: function() {
            let self = this;
            self.snapshotDiffActive = false;
            self.snapshotDiffStats = null;
            self.diffDetails = null;
            self.diffSummaryOpen = false;

            /* Remove SVG overlay badges and arrows */
            (self._diffOverlayCells || []).forEach(function(el) {
                if (el && el.parentNode) el.parentNode.removeChild(el);
            });
            self._diffOverlayCells = [];

            /* Restore element opacity and remove highlights */
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (!view) return;
                view.vel.attr({ opacity: 1 });
                try { view.unhighlight(null, { highlighter: { name: 'stroke' } }); } catch(e) { /* swallow-ok: cosmetic un-highlight while clearing the snapshot diff */ }
            });

            /* Restore link styles */
            self.graph.getLinks().forEach(function(link) {
                let relId = link.get('relId');
                if (!relId) return;
                let relType = link.get('relType') || 'association';
                const style = ComposerRenderer.REL_STYLES[relType] || ComposerRenderer.REL_STYLES['association'];
                link.attr('line/stroke', style.stroke || '#6b7280');
                link.attr('line/strokeWidth', style.strokeWidth || 1.5);
                link.attr('line/strokeDasharray', style.strokeDasharray || '');
                link.removeAttr('line/opacity');
            });

            self.statusText = 'Ready';
        },

        /* CMP2-010: Toggle the diff overlay visibility without clearing data */
        toggleDiffOverlay: function() {
            let self = this;
            if (!self.snapshotDiffActive) return;
            /* Toggle by clearing or re-applying. We clear and re-apply from diffDetails. */
            let details = self.diffDetails;
            if (!details) return;

            /* Check if overlays are currently visible by checking _diffOverlayCells */
            let overlaysVisible = (self._diffOverlayCells || []).length > 0;

            if (overlaysVisible) {
                /* Hide overlays but keep state */
                (self._diffOverlayCells || []).forEach(function(el) {
                    if (el && el.parentNode) el.parentNode.removeChild(el);
                });
                self._diffOverlayCells = [];

                /* Reset element visuals */
                self.graph.getElements().forEach(function(cell) {
                    let view = self.paper.findViewByModel(cell);
                    if (!view) return;
                    view.vel.attr({ opacity: 1 });
                    try { view.unhighlight(null, { highlighter: { name: 'stroke' } }); } catch(e) { /* swallow-ok: cosmetic un-highlight while hiding the snapshot diff overlay */ }
                });
                self.graph.getLinks().forEach(function(link) {
                    let relId = link.get('relId');
                    if (!relId) return;
                    let relType = link.get('relType') || 'association';
                    const style = ComposerRenderer.REL_STYLES[relType] || ComposerRenderer.REL_STYLES['association'];
                    link.attr('line/stroke', style.stroke || '#6b7280');
                    link.attr('line/strokeWidth', style.strokeWidth || 1.5);
                    link.attr('line/strokeDasharray', style.strokeDasharray || '');
                    link.removeAttr('line/opacity');
                });
                _toast('info', 'Diff overlay hidden — click Toggle to show again');
            } else {
                /* Re-apply overlays from saved state */
                self._reapplyDiffOverlays();
                _toast('info', 'Diff overlay visible');
            }
        },

        /* CMP2-010: Re-apply diff overlays from saved diffDetails state */
        _reapplyDiffOverlays: function() {
            let self = this;
            let details = self.diffDetails;
            if (!details) return;

            let addedIds = {};
            (details.addedElements || []).forEach(function(e) { addedIds[e.id] = true; addedIds[String(e.id)] = true; });
            let movedIds = {};
            let movedMap = {};
            (details.movedElements || []).forEach(function(e) { movedIds[e.id] = true; movedIds[String(e.id)] = true; movedMap[e.id] = e; movedMap[String(e.id)] = e; });
            let removedIds = {};
            (details.removedElements || []).forEach(function(e) { removedIds[e.id] = true; removedIds[String(e.id)] = true; });
            let addedRelIds = {};
            (details.addedRelationships || []).forEach(function(r) { addedRelIds[r.id] = true; addedRelIds[String(r.id)] = true; });
            let removedRelIds = {};
            (details.removedRelationships || []).forEach(function(r) { removedRelIds[r.id] = true; removedRelIds[String(r.id)] = true; });

            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let eid = cell.get('elementId');
                let view = self.paper.findViewByModel(cell);
                if (!view) return;

                if (addedIds[eid] || addedIds[String(eid)]) {
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#22c55e', 'stroke-width': 3 } } } }); } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    self._addDiffBadge(cell, 'NEW', '#22c55e');
                } else if (movedIds[eid] || movedIds[String(eid)]) {
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#3b82f6', 'stroke-width': 3, 'stroke-dasharray': '6,3' } } } }); } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    let mi = movedMap[eid] || movedMap[String(eid)];
                    if (mi) self._addDisplacementArrow(cell, mi.dx, mi.dy);
                } else if (removedIds[eid] || removedIds[String(eid)]) {
                    view.vel.attr({ opacity: 0.5 });
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#ef4444', 'stroke-width': 2, 'stroke-dasharray': '4,4' } } } }); } catch(e) { /* swallow-ok: cosmetic diff outline; the NEW and REMOVED badges and the diff summary line carry the same information */ }
                    self._addDiffBadge(cell, 'REMOVED', '#ef4444');
                }
            });

            self.graph.getLinks().forEach(function(link) {
                let rid = link.get('relId');
                if (!rid) return;
                if (addedRelIds[rid] || addedRelIds[String(rid)]) {
                    link.attr('line/stroke', '#22c55e');
                    link.attr('line/strokeWidth', 3);
                    link.attr('line/strokeDasharray', '');
                } else if (removedRelIds[rid] || removedRelIds[String(rid)]) {
                    link.attr('line/stroke', '#ef4444');
                    link.attr('line/strokeWidth', 2);
                    link.attr('line/strokeDasharray', '6,4');
                    link.attr('line/opacity', 0.6);
                }
            });
        },

        // QA-CMP-008: Collaboration awareness — join/leave/heartbeat
        collaborationJoin: function() {
            let self = this;
            let diagramId = self.savedViewpointId;
            if (!diagramId) return;
            /* Presence ping the user never asked for; a failure leaves the other-editors
               indicator dark and there is nothing to act on, so it stays silent. */
            Platform.fetch.post('/archimate/api/diagrams/' + diagramId + '/editors/join', null, { silent: true })
            .then(function(data) {
                self.collaborationOtherEditors = data.other_editors || 0;
                if (self.collaborationOtherEditors > 0) {
                    self.statusText = self.collaborationOtherEditors +
                        ' other user(s) editing this diagram';
                }
            })
            /* Presence awareness only — a failure here just means the "other editors"
               indicator doesn't light up this time; nothing for the user to act on. */
            .catch(function() { /* swallow-ok: presence awareness only; the other-editors indicator just does not light up and no edit of the user's is affected */ });
        },

        collaborationLeave: function() {
            let self = this;
            let diagramId = self.savedViewpointId;
            if (!diagramId) return;
            /* Fire-and-forget cleanup as the user navigates away — nothing actionable. */
            Platform.fetch.post('/archimate/api/diagrams/' + diagramId + '/editors/leave', null, { silent: true })
            .catch(function() { /* swallow-ok: fire-and-forget presence cleanup as the user navigates away; there is no session left to report into */ });
        },

        collaborationRefresh: function() {
            let self = this;
            let diagramId = self.savedViewpointId;
            if (!diagramId) return;
            /* Presence refresh the user never asked for; see collaborationJoin above. */
            Platform.fetch.get('/archimate/api/diagrams/' + diagramId + '/active-editors', null, { silent: true })
            .then(function(data) {
                self.collaborationOtherEditors = data.count || 0;
            })
            /* Presence awareness only — see collaborationJoin() above. */
            .catch(function() { /* swallow-ok: presence refresh only, see collaborationJoin above */ });
        },

        };

        return methods;
    }

    return { getMethods: getMethods };
})();
