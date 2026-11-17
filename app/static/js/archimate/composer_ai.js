/**
 * CMP-032: Composer AI Module (IIFE)
 *
 * Extracted from composerApp() — all AI/ML features:
 * - CMP-016: AI Ambient Suggestions
 * - CMP-017: AI Generate from Description
 * - CMP-018: Validation
 * - CMP-019: Diagram narration and impact
 * - CMP-020: Enterprise intelligence overlays
 * - CMP-021: Baseline-to-target delta and plateau generation
 * - CMP-022: AI Document-to-Model Extraction
 * - CMP-023: Pattern-based generation
 *
 * Usage: ComposerAI.install(ctx, helpers) in composerApp().init()
 */
let ComposerAI = (function() {
    'use strict';

    function getMethods(helpers) {
        let guessLayer = helpers.guessLayer;
        let csrfToken = helpers.csrfToken;
        let createNode = helpers.createNode;
        let createLink = helpers.createLink;
        let layerColor = helpers.layerColor;
        let REL_STYLES = helpers.REL_STYLES;
        let _toast = helpers._toast;

        let methods = {

        /* Explanation panel (CMP-019) */
        explanationPanelOpen: false,
        explanationText: '',
        explanationLoading: false,
        explanationAudience: 'technical',
        impactPanelOpen: false,
        impactData: null,
        /* Delta comparison (CMP-021) */
        deltaMode: false,
        deltaData: null,
        deltaLoading: false,
        deltaCompareVpId: '',
        deltaCompareVpList: [],
        deltaPickerOpen: false,
        plateauSuggestions: [],
        plateauLoading: false,
        /* Pattern-based generation (CMP-023) */
        patternListOpen: false,
        patterns: [],
        selectedPatternId: null,
        patternContext: '',
        patternLoading: false,
        patternInstantiatedElements: [],
        patternInstantiatedRelationships: [],
        savePatternOpen: false,
        savePatternName: '',
        savePatternDescription: '',
        /* Enterprise intelligence overlays (CMP-020) */
        intelligenceEnabled: false,
        intelligenceData: {},
        intelligenceLoading: false,
        /* CMP-045: Heatmap overlay */
        heatmapEnabled: false,
        heatmapMetric: 'maturity',
        heatmapLoading: false,
        /* CMP-046: Derived relationships */
        derivedEnabled: false,
        derivedLoading: false,
        derivedLinks: [],
        /* CMP2-008: Retirement simulation */
        retirementSimActive: false,
        retirementSimSource: null,
        retirementAffectedCount: 0,

        /* Comments panel (CMP-024) */
        commentPanelOpen: false,
        commentElementId: null,
        comments: [],
        newCommentText: '',
        commentsLoading: false,
        /* Audit trail panel (CMP-024) */
        auditPanelOpen: false,
        auditLog: [],
        auditLoading: false,
        /* ── CMP-016: AI Ambient Suggestions ─────────────────── */

        toggleSuggestions: function() {
            this.suggestionsEnabled = !this.suggestionsEnabled;
            if (this.suggestionsEnabled) {
                this.fetchSuggestions();
            } else {
                this.suggestions = [];
                clearTimeout(this._suggestionTimer);
            }
        },

        fetchSuggestions: function() {
            let self = this;
            let cells = this.graph.getCells();
            let elements = [];
            let relationships = [];

            cells.forEach(function(cell) {
                if (cell.isElement()) {
                    let elType = cell.get('elementType') || '';
                    let elLayer = cell.get('layer') || '';
                    let elName = cell.get('label') || '';
                    let elDbId = cell.get('dbId') || null;
                    if (elType) {
                        elements.push({ id: elDbId, type: elType, layer: elLayer, name: elName });
                    }
                } else if (cell.isLink()) {
                    let sourceCell = self.graph.getCell(cell.get('source').id);
                    let targetCell = self.graph.getCell(cell.get('target').id);
                    if (sourceCell && targetCell) {
                        relationships.push({
                            source_type: sourceCell.get('elementType') || '',
                            target_type: targetCell.get('elementType') || '',
                            rel_type: cell.get('relType') || '',
                        });
                    }
                }
            });

            if (elements.length === 0) {
                self.suggestions = [];
                return;
            }

            Platform.fetch.post('/archimate/api/composer/suggestions', {
                elements: elements,
                relationships: relationships,
                viewpoint_type: self.activeViewpoint || '',
            }, { silent: true })
            .then(function(data) {
                let items = [];
                (data.missing_elements || []).forEach(function(s) {
                    items.push({
                        kind: 'element',
                        type: s.type,
                        reason: s.reason,
                        suggested_layer: s.suggested_layer,
                    });
                });
                (data.missing_relationships || []).forEach(function(s) {
                    items.push({
                        kind: 'relationship',
                        source_id: s.source_id,
                        target_id: s.target_id,
                        suggested_type: s.suggested_type,
                        reason: s.reason,
                    });
                });
                self.suggestions = items;
            })
            .catch(function() {
                self.suggestions = [];
                _toast('error', 'Failed to load AI suggestions');
            });
        },

        dismissSuggestion: function(index) {
            this.suggestions.splice(index, 1);
        },

        acceptElementSuggestion: function(suggestion) {
            this.searchOpen = true;
            this.searchTypeFilter = suggestion.type || '';
            this.searchQuery = '';
            this.searchResults = [];
            this.dismissSuggestion(this.suggestions.indexOf(suggestion));
            let self = this;
            this.$nextTick(function() {
                let input = document.querySelector('.search-header-input');
                if (input) input.focus();
                self.doSearch();
            });
        },

        acceptRelSuggestion: function(suggestion) {
            let self = this;
            let sourceId = suggestion.source_id;
            let targetId = suggestion.target_id;
            let relType = suggestion.suggested_type;

            if (!sourceId || !targetId || !relType) return;

            Platform.fetch.post('/archimate/api/relationships', {
                source_element_id: sourceId,
                target_element_id: targetId,
                relationship_type: relType,
                solution_id: self.solutionId || null,
            }, { silent: true })
            .then(function(data) {
                if (data.id) {
                    let sourceCell = null;
                    let targetCell = null;
                    self.graph.getElements().forEach(function(cell) {
                        if (cell.get('elementId') === sourceId) sourceCell = cell;
                        if (cell.get('elementId') === targetId) targetCell = cell;
                    });
                    if (sourceCell && targetCell) {
                        let link = createLink(sourceCell, targetCell, relType, data.id);
                        self.graph.addCell(link);
                        self.relCount++;
                        self.statusText = 'Relationship added';
                    }
                }
            })
            .catch(function() {
                _toast('error', 'Failed to add relationship');
                self.statusText = 'Failed to add relationship';
            });

            self.dismissSuggestion(self.suggestions.indexOf(suggestion));
        },

        /* ── CMP-018: Validation ─────────────────────────────── */
        runValidation: function() {
            let self = this;
            if (self.validationLoading) return;
            self.validationLoading = true;
            self.validationReport = { passed: [], warnings: [], errors: [] };
            self.validationPanelOpen = true;
            self.statusText = 'Validating...';

            /* Collect canvas elements */
            let elements = [];
            let elMap = {};
            self.graph.getElements().forEach(function(cell) {
                let elType = cell.get('elType') || '';
                let name = cell.get('label') || cell.attr('label/text') || '';
                let layer = cell.get('layer') || guessLayer(elType);
                let entry = {
                    id: cell.id,
                    type: elType,
                    layer: layer,
                    name: name,
                };
                elements.push(entry);
                elMap[cell.id] = entry;
            });

            /* Collect canvas relationships */
            let relationships = [];
            self.graph.getLinks().forEach(function(link) {
                let srcId = link.get('source') && link.get('source').id;
                let tgtId = link.get('target') && link.get('target').id;
                let srcEl = elMap[srcId] || {};
                let tgtEl = elMap[tgtId] || {};
                relationships.push({
                    source_type: srcEl.type || '',
                    target_type: tgtEl.type || '',
                    rel_type: link.get('relType') || '',
                    source_name: srcEl.name || '',
                    target_name: tgtEl.name || '',
                });
            });

            let payload = {
                elements: elements,
                relationships: relationships,
                phase: self.validationPhase || '',
                viewpoint_type: self.activeViewpoint || '',
            };

            Platform.fetch.post('/archimate/api/composer/validate', payload, { silent: true })
            .then(function(data) {
                self.validationReport = {
                    passed: data.passed || [],
                    warnings: data.warnings || [],
                    errors: data.errors || [],
                };
                let total = (data.errors || []).length + (data.warnings || []).length;
                if (total === 0) {
                    self.statusText = 'Validation passed';
                } else {
                    self.statusText = total + ' issue(s) found';
                }
            })
            .catch(function(err) {
                _toast('error', 'Validation failed: ' + (err.message || err));
                self.statusText = 'Validation failed';
                self.validationReport = {
                    passed: [],
                    warnings: [],
                    errors: [{ check: 'error', message: 'Validation request failed: ' + err.message, element_ids: [] }],
                };
            })
            .finally(function() {
                self.validationLoading = false;
            });
        },

        highlightValidationElements: function(elementIds) {
            let self = this;
            if (!elementIds || !elementIds.length || !self.graph) return;

            let idSet = {};
            elementIds.forEach(function(id) { idSet[String(id)] = true; });

            /* Dim all elements, then highlight matched ones */
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (!view) return;
                if (idSet[String(cell.id)]) {
                    view.el.style.opacity = '1';
                    view.el.style.filter = 'drop-shadow(0 0 6px #ef4444)';
                } else {
                    view.el.style.opacity = '0.3';
                    view.el.style.filter = '';
                }
            });

            self.graph.getLinks().forEach(function(link) {
                let view = self.paper.findViewByModel(link);
                if (view) view.el.style.opacity = '0.15';
            });

            /* Auto-restore after 3 seconds */
            setTimeout(function() {
                self._clearValidationHighlight();
            }, 3000);
        },

        _clearValidationHighlight: function() {
            let self = this;
            if (!self.graph) return;
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) {
                    view.el.style.opacity = '';
                    view.el.style.filter = '';
                }
            });
            self.graph.getLinks().forEach(function(link) {
                let view = self.paper.findViewByModel(link);
                if (view) view.el.style.opacity = '';
            });
        },

        /* ── CMP-017: AI Generate from Description ────────────── */

        openGenerateModal: function() {
            if (this.mode === 'view') return;
            this.generateModalOpen = true;
            this.generateDescription = '';
            this.generatePhase = '';
            this.generateDomain = '';
            this.generateLoading = false;
            this.generateContextLoading = false;
            this.generateContextPreview = null;
            this.generateUseContext = true;
            this.generateIncludeGaps = true;
            this.generateContextExpanded = false;
            this.generatedElements = [];
            this.generatedRelationships = [];
            this.generateGaps = [];
            this.generateRationale = '';
            this._acceptedNameToId = {};
        },

        /**
         * Fetch a quick context preview (counts of matching portfolio entities).
         * Called on description blur or after a short debounce.
         */
        fetchContextPreview: function() {
            let self = this;
            let desc = (this.generateDescription || '').trim();
            if (!desc || desc.length < 10) {
                self.generateContextPreview = null;
                return;
            }

            self.generateContextLoading = true;
            Platform.fetch.post('/archimate/api/composer/context-preview', {
                description: desc,
                phase: self.generatePhase || '',
                business_domain: self.generateDomain || '',
            }, { silent: true })
            .then(function(data) {
                self.generateContextLoading = false;
                if (!data.error) {
                    self.generateContextPreview = data;
                }
            })
            /* Unsolicited advisory fired by a debounce while the user is still typing
               the description. On failure the preview panel simply does not appear —
               the guard above keeps a 4xx/5xx body from being rendered as counts. */
            .catch(function() { /* swallow-ok: debounced background context preview; a failure leaves the panel hidden and the user is not waiting on it */
                self.generateContextLoading = false;
            });
        },

        /**
         * Debounced context preview — waits 500ms after typing stops.
         */
        _contextPreviewTimer: null,
        debouncedContextPreview: function() {
            let self = this;
            clearTimeout(self._contextPreviewTimer);
            self._contextPreviewTimer = setTimeout(function() {
                self.fetchContextPreview();
            }, 500);
        },

        generateFromDescription: function() {
            let self = this;
            let desc = (this.generateDescription || '').trim();
            if (!desc) return;

            self.generateLoading = true;
            self.generatedElements = [];
            self.generatedRelationships = [];
            self.generateGaps = [];
            self.generateRationale = '';
            self._acceptedNameToId = {};

            // Use context-aware endpoint if enabled, otherwise basic
            let endpoint = self.generateUseContext
                ? '/archimate/api/composer/generate-contextual'
                : '/archimate/api/composer/generate';

            let body = {
                description: desc,
                phase: self.generatePhase || '',
                viewpoint_type: self.activeViewpoint || '',
                solution_id: self.solutionId || null,
            };

            if (self.generateUseContext) {
                body.business_domain = self.generateDomain || '';
                body.options = {
                    reference_existing: true,
                    include_gaps: self.generateIncludeGaps,
                };
            }

            self.statusText = self.generateUseContext
                ? 'Generating with enterprise context...'
                : 'Generating...';

            Platform.fetch.post(endpoint, body, { silent: true })
            .then(function(data) {
                self.generateLoading = false;
                if (data.error) {
                    self.statusText = 'Generate failed: ' + data.error;
                    return;
                }
                self.generatedElements = data.elements || [];
                self.generatedRelationships = data.relationships || [];
                self.generateGaps = data.gaps || [];
                self.generateRationale = data.rationale || '';

                let existing = self.generatedElements.filter(function(e) { return e.category === 'existing'; }).length;
                let isNew = self.generatedElements.filter(function(e) { return e.category === 'new'; }).length;
                let dupes = self.generatedElements.filter(function(e) { return e.category === 'possible_duplicate'; }).length;

                let statusParts = ['Generated ' + self.generatedElements.length + ' elements'];
                if (existing) statusParts.push(existing + ' linked to existing');
                if (isNew) statusParts.push(isNew + ' new');
                if (dupes) statusParts.push(dupes + ' possible duplicates');
                if (data.llm_used) statusParts.push('(AI)');
                self.statusText = statusParts.join(' · ');
            })
            .catch(function(err) {
                _toast('error', 'Generate failed: ' + (err.message || err));
                self.generateLoading = false;
                self.statusText = 'Generate error: ' + err.message;
            });
        },

        /**
         * Resolve a possible duplicate: use the existing element instead.
         */
        useExistingElement: function(index) {
            let el = this.generatedElements[index];
            if (!el || !el.duplicate_match) return;
            el.category = 'existing';
            el.existing_id = el.duplicate_match.id;
            el.name = el.duplicate_match.name;
            el.duplicate_match = null;
            el.duplicate = false;
        },

        /**
         * Resolve a possible duplicate: keep the new element.
         */
        keepNewElement: function(index) {
            let el = this.generatedElements[index];
            if (!el) return;
            el.category = 'new';
            el.duplicate_match = null;
            el.duplicate = false;
        },

        /* ── ENT-119: accepted element name → DB id, for deferred relationship wiring ── */
        _acceptedNameToId: {},

        acceptGenerated: function(index) {
            let self = this;
            let el = this.generatedElements[index];
            if (!el) return;

            let layer = el.layer || guessLayer(el.type);

            /* Remove element from review list — but keep its relationships in
               generatedRelationships so _tryWireReadyRelationships() can pick them up
               once the other endpoint is also accepted. */
            self.generatedElements.splice(index, 1);

            // If this is an existing element (linked from portfolio), add to canvas directly
            if (el.category === 'existing' && el.existing_id) {
                self.statusText = 'Adding existing: ' + el.name + '...';
                let vp = self.paper.translate();
                let s = self.paper.scale().sx;
                let rect = self.paper.el.getBoundingClientRect();
                let cx = (rect.width / 2 - vp.tx) / s;
                let cy = (rect.height / 2 - vp.ty) / s;
                let offset = Object.keys(self.canvasElements).length * 30;

                let node = createNode(el.existing_id, el.name, el.type, layer, cx - 90 + offset, cy - 32 + offset);
                self.graph.addCell(node);
                self.canvasElements[el.existing_id] = { id: el.existing_id, name: el.name, type: el.type, layer: layer };
                self.elementCount++;
                if (self.solutionId) self.linkElementToSolution(el.existing_id);
                self.statusText = 'Linked existing: ' + el.name;

                self._acceptedNameToId[el.name] = el.existing_id;
                self._tryWireReadyRelationships();
                return;
            }

            // New element — create via API
            self.statusText = 'Creating: ' + el.name + '...';
            let elName = el.name;

            Platform.fetch.post('/api/architecture-assistant/create-element', { name: elName, type: el.type, layer: layer }, { silent: true })
            .then(function(data) {
                let d = data.element || data;
                if (d.id) {
                    let vp = self.paper.translate();
                    let s = self.paper.scale().sx;
                    let rect = self.paper.el.getBoundingClientRect();
                    let cx = (rect.width / 2 - vp.tx) / s;
                    let cy = (rect.height / 2 - vp.ty) / s;
                    let offset = Object.keys(self.canvasElements).length * 30;

                    let node = createNode(d.id, d.name, d.type, d.layer || layer, cx - 90 + offset, cy - 32 + offset);
                    self.graph.addCell(node);
                    self.canvasElements[d.id] = d;
                    self.elementCount++;
                    if (self.solutionId) self.linkElementToSolution(d.id);
                    self.statusText = 'Created: ' + d.name;

                    self._acceptedNameToId[elName] = d.id;
                    self._tryWireReadyRelationships();
                } else {
                    self.statusText = 'Create failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Error: ' + (err && err.message); _toast('error', (err && err.message) || 'Operation failed'); });
        },

        /* Wire any pending relationship whose source AND target are both accepted. */
        _tryWireReadyRelationships: function() {
            let self = this;
            let remaining = [];
            self.generatedRelationships.forEach(function(rel) {
                let sourceId = self._acceptedNameToId[rel.source_name];
                let targetId = self._acceptedNameToId[rel.target_name];
                if (sourceId && targetId) {
                    self._wireSingleRelationship(rel, sourceId, targetId);
                } else {
                    remaining.push(rel);
                }
            });
            self.generatedRelationships = remaining;
        },

        rejectGenerated: function(index) {
            let el = this.generatedElements[index];
            if (!el) return;
            let elName = el.name;
            this.generatedElements.splice(index, 1);
            /* Also drop relationships involving the rejected element — user explicitly declined it. */
            this.generatedRelationships = this.generatedRelationships.filter(function(r) {
                return r.source_name !== elName && r.target_name !== elName;
            });
        },

        /* Governance: AI-generated elements are written to the model only after an
           explicit human confirmation. "Place Full Diagram on Canvas" no longer
           persists silently — it summarises what will be created (and how many
           possible duplicates are unresolved) and waits for the architect to approve. */
        acceptAllGenerated: function() {
            let self = this;
            let pending = self.generatedElements.slice();
            if (!pending.length) return;

            let newCount = pending.filter(function(e) {
                return !(e.category === 'existing' && e.existing_id);
            }).length;
            let existingCount = pending.length - newCount;
            let dupCount = pending.filter(function(e) {
                return e.category === 'possible_duplicate';
            }).length;

            let msg = 'This will create ' + newCount + ' new element'
                + (newCount === 1 ? '' : 's') + ' in your architecture model';
            if (existingCount) {
                msg += ' and link ' + existingCount + ' existing element'
                    + (existingCount === 1 ? '' : 's');
            }
            msg += '.';
            if (dupCount) {
                msg += ' ' + dupCount + (dupCount === 1 ? ' element is' : ' elements are')
                    + ' flagged as a possible duplicate and will be created as new'
                    + ' unless you resolve them first (Use existing / Keep new).';
            }
            msg += ' These AI-generated changes are written to your model once you confirm.';

            Platform.confirm({
                title: 'Place ' + pending.length + ' element'
                    + (pending.length === 1 ? '' : 's') + ' into the model?',
                message: msg,
                confirmText: 'Create & place',
                cancelText: 'Keep reviewing',
            }).then(function(ok) {
                if (ok) self._doAcceptAllGenerated();
            });
        },

        /* POST with retry on 429 (rate limit): global write limit is 30/min
           (app/_bootstrap/rate_limiting.py), and a "Place Full Diagram on
           Canvas" of the size the AI generator routinely produces (30+
           elements, plus relationships right behind them) fires enough
           writes in one burst to exceed it. Confirmed in production 10 Sep
           2026 -- a 32-element diagram left one element silently missing,
           the only sign a single toast the architect could easily miss.
           Retries honour Retry-After when the server sends one, else backs
           off a window past the fixed-window limiter's own reset. */
        _postWithRetry: function(url, body, attempt) {
            attempt = attempt || 0;
            return Platform.fetch.post(url, body, { silent: true })
                .catch(function(err) {
                    let status = err && (err.status || (err.response && err.response.status));
                    if (status !== 429 || attempt >= 3) throw err;
                    let retryAfter = err && err.response && err.response.headers
                        && err.response.headers.get && err.response.headers.get('Retry-After');
                    let waitMs = retryAfter ? parseFloat(retryAfter) * 1000 : (attempt + 1) * 2000;
                    return new Promise(function(resolve) { setTimeout(resolve, waitMs); })
                        .then(function() { return this._postWithRetry(url, body, attempt + 1); }.bind(this));
                }.bind(this));
        },

        /* Run async factories with limited concurrency instead of firing them
           all at once -- see _postWithRetry's comment for why unbounded
           parallelism here silently drops elements once a diagram is larger
           than the rate limiter's burst window. */
        _runLimited: function(factories, limit, onEach) {
            return new Promise(function(resolve) {
                let idx = 0, active = 0, doneCount = 0;
                let total = factories.length;
                if (total === 0) { resolve(); return; }
                function next() {
                    while (active < limit && idx < total) {
                        let i = idx++;
                        active++;
                        factories[i]().then(function(result) {
                            onEach(result, i);
                        }).catch(function(err) {
                            onEach({ __error: err }, i);
                        }).finally(function() {
                            active--; doneCount++;
                            if (doneCount === total) resolve();
                            else next();
                        });
                    }
                }
                next();
            });
        },

        _doAcceptAllGenerated: function() {
            let self = this;
            let pending = self.generatedElements.slice();
            let pendingRelationships = self.generatedRelationships.slice();
            self.generatedElements = [];
            self.generatedRelationships = [];
            self._acceptedNameToId = {};
            self.generateModalOpen = false;

            let total = pending.length;
            let created = 0;
            let nameToElementId = {};

            const updateProgress = function() {
                self.statusText = 'Creating ' + created + '\u2009/\u2009' + total + ' elements\u2026';
            };
            updateProgress();

            const placeNode = function(id, name, type, layer, idx) {
                let vp = self.paper.translate();
                let s = self.paper.scale().sx;
                let rect = self.paper.el.getBoundingClientRect();
                let cx = (rect.width / 2 - vp.tx) / s;
                let cy = (rect.height / 2 - vp.ty) / s;
                let node = createNode(id, name, type, layer, cx - 90 + idx * 40, cy - 32 + idx * 50);
                self.graph.addCell(node);
            };

            let factories = pending.map(function(el, idx) {
                let layer = el.layer || guessLayer(el.type);

                if (el.category === 'existing' && el.existing_id) {
                    return function() {
                        return Promise.resolve().then(function() {
                            placeNode(el.existing_id, el.name, el.type, layer, idx);
                            self.canvasElements[el.existing_id] = el;
                            self.elementCount++;
                            nameToElementId[el.name] = el.existing_id;
                            if (self.solutionId) self.linkElementToSolution(el.existing_id);
                        });
                    };
                }

                return function() {
                    return self._postWithRetry('/api/architecture-assistant/create-element',
                        { name: el.name, type: el.type, layer: layer })
                        .then(function(data) {
                            let d = data.element || data;
                            if (d.id) {
                                placeNode(d.id, d.name, d.type, d.layer || layer, idx);
                                self.canvasElements[d.id] = d;
                                self.elementCount++;
                                nameToElementId[el.name] = d.id;
                                if (self.solutionId) self.linkElementToSolution(d.id);
                            }
                        });
                };
            });

            /* 5 concurrent, well under the 30/min write bucket even with
               relationship-wiring writes still to come right after. */
            self._runLimited(factories, 5, function(result) {
                created++;
                if (result && result.__error) _toast('error', 'Failed to create element');
                updateProgress();
            }).then(function() {
                self._finishAcceptAll(pendingRelationships, nameToElementId, total);
            });
        },

        _finishAcceptAll: function(pendingRelationships, nameToElementId, totalElements) {
            let self = this;
            let relCount = pendingRelationships.filter(function(r) {
                return nameToElementId[r.source_name] && nameToElementId[r.target_name];
            }).length;
            self.statusText = 'Created ' + totalElements + ' elements — wiring ' + relCount + ' relationships\u2026';
            /* Apply initial layer-banded layout now so elements aren't stacked while rels are wiring */
            self.reLayout();
            /* Wire relationships; when all done, upgrade to dagre hierarchical layout */
            self._wireGeneratedRelationships(pendingRelationships, nameToElementId, function() {
                if (typeof dagre !== 'undefined') {
                    self.layoutDagre('TB');
                }
                self.statusText = 'Diagram placed — ' + totalElements + ' elements, ' + self.relCount + ' relationships';
                self.viewpointDirty = true;
                /* CMP2-003: Auto-detect additional catalog relationships not in AI output */
                self._autoDetectBulkDebounced();
            });
        },

        /* Returns a promise that always resolves (never rejects) once this
           relationship's create attempt has fully settled, success or
           failure -- so a caller running many of these under _runLimited can
           treat "settled" as "safe to start the next one" regardless of
           outcome. */
        _wireSingleRelationship: function(rel, sourceId, targetId) {
            let self = this;
            let relType = rel.type || rel.relationship_type || 'association';
            return self._postWithRetry('/archimate/api/relationships', {
                source_element_id: sourceId,
                target_element_id: targetId,
                relationship_type: relType,
                solution_id: self.solutionId || null,
            })
            .then(function(data) {
                if (data.id) {
                    let sourceCell = null;
                    let targetCell = null;
                    self.graph.getElements().forEach(function(cell) {
                        if (cell.get('elementId') === sourceId) sourceCell = cell;
                        if (cell.get('elementId') === targetId) targetCell = cell;
                    });
                    if (sourceCell && targetCell) {
                        let link = createLink(sourceCell, targetCell, relType, data.id);
                        self.graph.addCell(link);
                        self.relCount++;
                        self.statusText = rel.source_name + ' \u2192 ' + rel.target_name + ' (' + relType + ')';
                    }
                }
            })
            .catch(function() {
                _toast('error', 'Failed to wire: ' + rel.source_name + ' \u2192 ' + rel.target_name);
            });
        },

        _wireGeneratedRelationships: function(relationships, nameToElementId, onAllDone) {
            let self = this;
            if (!relationships || !relationships.length) {
                self.statusText = 'Done';
                if (onAllDone) onAllDone();
                return;
            }
            let toWire = relationships.filter(function(rel) {
                return nameToElementId[rel.source_name] && nameToElementId[rel.target_name];
            });
            if (toWire.length === 0) {
                self.statusText = 'Done';
                if (onAllDone) onAllDone();
                return;
            }
            /* Same burst-vs-rate-limit problem as element creation: wire with
               bounded concurrency instead of firing every relationship POST
               at once (see _postWithRetry / _runLimited). */
            let factories = toWire.map(function(rel) {
                return function() {
                    let sourceId = nameToElementId[rel.source_name];
                    let targetId = nameToElementId[rel.target_name];
                    return self._wireSingleRelationship(rel, sourceId, targetId);
                };
            });
            self._runLimited(factories, 5, function() {}).then(function() {
                self.statusText = 'Done';
                if (onAllDone) onAllDone();
            });
        },

        /* ── CMP-022: AI Document-to-Model Extraction ───────── */

        openExtractModal: function() {
            if (this.mode === 'view') return;
            this.extractModalOpen = true;
            this.extractText = '';
            this.extractPhase = '';
            this.extractLoading = false;
            this.extractedElements = [];
            this.extractedRelationships = [];
        },

        extractFromText: function() {
            let self = this;
            let txt = (this.extractText || '').trim();
            if (!txt) return;

            self.extractLoading = true;
            self.extractedElements = [];
            self.extractedRelationships = [];
            self.statusText = 'Extracting...';

            Platform.fetch.post('/archimate/api/composer/extract', {
                text: txt,
                target_phase: self.extractPhase || '',
                viewpoint_type: self.activeViewpoint || '',
            }, { silent: true })
            .then(function(data) {
                self.extractLoading = false;
                if (data.error) {
                    self.statusText = 'Extract failed: ' + data.error;
                    return;
                }
                self.extractedElements = data.elements || [];
                self.extractedRelationships = data.relationships || [];
                self.statusText = 'Extracted ' + self.extractedElements.length + ' elements';
            })
            .catch(function(err) {
                _toast('error', 'Extract failed: ' + (err.message || err));
                self.extractLoading = false;
                self.statusText = 'Extract error: ' + err.message;
            });
        },

        acceptExtracted: function(index) {
            let self = this;
            let el = this.extractedElements[index];
            if (!el) return;

            let layer = el.layer || guessLayer(el.suggested_type);
            self.statusText = 'Creating: ' + el.name + '...';

            Platform.fetch.post('/api/architecture-assistant/create-element', { name: el.name, type: el.suggested_type, layer: layer }, { silent: true })
            .then(function(data) {
                let d = data.element || data;
                if (d.id) {
                    let vp = self.paper.translate();
                    let s = self.paper.scale().sx;
                    let rect = self.paper.el.getBoundingClientRect();
                    let cx = (rect.width / 2 - vp.tx) / s;
                    let cy = (rect.height / 2 - vp.ty) / s;
                    let offset = Object.keys(self.canvasElements).length * 30;

                    let node = createNode(d.id, d.name, d.type, d.layer || layer, cx - 90 + offset, cy - 32 + offset);
                    self.graph.addCell(node);
                    self.canvasElements[d.id] = d;
                    self.elementCount++;
                    if (self.solutionId) self.linkElementToSolution(d.id);
                    self.statusText = 'Created: ' + d.name;
                } else {
                    self.statusText = 'Create failed: ' + (data.error || 'unknown');
                }
            })
            .catch(function(err) { self.statusText = 'Error: ' + (err && err.message); _toast('error', (err && err.message) || 'Operation failed'); });

            /* Remove from review list */
            let elName = el.name;
            self.extractedElements.splice(index, 1);
            self.extractedRelationships = self.extractedRelationships.filter(function(r) {
                return r.source_name !== elName && r.target_name !== elName;
            });
        },

        rejectExtracted: function(index) {
            let el = this.extractedElements[index];
            if (!el) return;
            let elName = el.name;
            this.extractedElements.splice(index, 1);
            this.extractedRelationships = this.extractedRelationships.filter(function(r) {
                return r.source_name !== elName && r.target_name !== elName;
            });
        },

        acceptAllExtracted: function() {
            let self = this;
            let pending = self.extractedElements.slice();
            self.extractedElements = [];
            self.extractedRelationships = [];
            self.extractModalOpen = false;
            self.statusText = 'Creating ' + pending.length + ' elements...';

            let created = 0;
            let total = pending.length;

            pending.forEach(function(el, idx) {
                let layer = el.layer || guessLayer(el.suggested_type);
                Platform.fetch.post('/api/architecture-assistant/create-element', { name: el.name, type: el.suggested_type, layer: layer }, { silent: true })
                .then(function(data) {
                    created++;
                    let d = data.element || data;
                    if (d.id) {
                        let vp = self.paper.translate();
                        let s = self.paper.scale().sx;
                        let rect = self.paper.el.getBoundingClientRect();
                        let cx = (rect.width / 2 - vp.tx) / s;
                        let cy = (rect.height / 2 - vp.ty) / s;

                        let node = createNode(d.id, d.name, d.type, d.layer || layer, cx - 90 + idx * 40, cy - 32 + idx * 50);
                        self.graph.addCell(node);
                        self.canvasElements[d.id] = d;
                        self.elementCount++;
                        if (self.solutionId) self.linkElementToSolution(d.id);
                    }
                    if (created === total) {
                        self.statusText = 'Created ' + total + ' elements from text';
                        self.reLayout();
                        /* CMP2-003: Auto-detect catalog relationships for extracted elements */
                        self._autoDetectBulkDebounced();
                    }
                })
                .catch(function() {
                    created++;
                    _toast('error', 'Failed to create element');
                    if (created === total) {
                        self.statusText = 'Created ' + total + ' elements (some errors)';
                        self.reLayout();
                        /* CMP2-003: Auto-detect catalog relationships for extracted elements */
                        self._autoDetectBulkDebounced();
                    }
                });
            });
        },

        /* ── CMP-020: Enterprise intelligence overlays ────── */

        toggleIntelligence: function() {
            this.intelligenceEnabled = !this.intelligenceEnabled;
            if (this.intelligenceEnabled) {
                this.fetchIntelligence();
            } else {
                this.intelligenceData = {};
                this._clearIntelligenceBadges();
            }
        },

        fetchIntelligence: function() {
            let self = this;
            let ids = Object.keys(self.canvasElements);
            if (ids.length === 0) { self.intelligenceData = {}; return; }

            self.intelligenceLoading = true;
            Platform.fetch('/archimate/api/composer/intelligence?element_ids=' + ids.join(','), { silent: true })
            .then(function(data) {
                self.intelligenceData = data.enrichment || {};
                self.intelligenceLoading = false;
                self._applyIntelligenceBadges();
            })
            .catch(function() {
                self.intelligenceLoading = false;
                _toast('error', 'Failed to load intelligence data');
            });
        },

        _applyIntelligenceBadges: function() {
            let self = this;
            let cells = self.graph.getElements();
            cells.forEach(function(cell) {
                let eid = cell.get('elementId') || cell.id;
                let info = self.intelligenceData[String(eid)];
                if (info && info.signals && info.signals.length > 0) {
                    let label = info.signals.map(function(s) {
                        if (s === 'eol_risk') return '⚠ EOL';
                        if (s === 'lifecycle_risk') return '⚠ Lifecycle';
                        if (s === 'vendor_mapped') return '🏢 ' + (info.vendor_name || 'Vendor');
                        if (s === 'solution_usage') return '📦×' + info.solution_usage_count;
                        if (s === 'maturity_gap') return '📊 Gap';
                        if (s === 'strategic') return '⭐ Strategic';
                        return s;
                    }).join(' | ');

                    let badgeColor = '#6366f1';
                    if (info.signals.indexOf('eol_risk') !== -1 || info.signals.indexOf('lifecycle_risk') !== -1) {
                        badgeColor = '#ef4444';
                    } else if (info.signals.indexOf('maturity_gap') !== -1) {
                        badgeColor = '#f59e0b';
                    }

                    cell.attr('intelligenceBadge/text', label);
                    cell.attr('intelligenceBadge/fill', badgeColor);
                    cell.attr('intelligenceBadge/fontSize', 9);
                    cell.attr('intelligenceBadge/fontWeight', 600);
                    cell.attr('intelligenceBadge/refX', '100%');
                    cell.attr('intelligenceBadge/refY', -4);
                    cell.attr('intelligenceBadge/textAnchor', 'end');
                    cell.attr('intelligenceBadge/display', 'block');
                } else {
                    cell.attr('intelligenceBadge/display', 'none');
                }
            });
        },

        _clearIntelligenceBadges: function() {
            let cells = this.graph.getElements();
            cells.forEach(function(cell) {
                cell.attr('intelligenceBadge/display', 'none');
            });
        },

        getIntelligenceTooltip: function(elementId) {
            let info = this.intelligenceData[String(elementId)];
            if (!info) return '';
            let parts = [];
            if (info.vendor_name) parts.push('Vendor: ' + info.vendor_name);
            if (info.lifecycle_stage) parts.push('Lifecycle: ' + info.lifecycle_stage);
            if (info.eol_warning) parts.push('EOL: ' + info.eol_warning);
            if (info.app_criticality) parts.push('Criticality: ' + info.app_criticality);
            if (info.maturity_current) parts.push('Maturity: ' + info.maturity_current + '/' + (info.maturity_target || '?'));
            if (info.strategic_importance) parts.push('Importance: ' + info.strategic_importance);
            if (info.solution_usage_count) parts.push('Used in ' + info.solution_usage_count + ' solution(s)');
            if (info.performance_score) parts.push('Performance: ' + info.performance_score);
            return parts.join('\n');
        },

        /* ── CMP-023: Pattern-based generation ─────────────────── */

        openPatternModal: function() {
            if (this.mode === 'view') return;
            let self = this;
            self.patternListOpen = true;
            self.selectedPatternId = null;
            self.patternContext = '';
            self.patternInstantiatedElements = [];
            self.patternInstantiatedRelationships = [];
            self.patternLoading = true;

            Platform.fetch('/archimate/api/patterns', { silent: true })
            .then(function(data) {
                self.patternLoading = false;
                self.patterns = data.patterns || [];
            })
            .catch(function(err) {
                self.patternLoading = false;
                _toast('error', 'Failed to load patterns');
                self.statusText = 'Failed to load patterns: ' + (err && err.message);
            });
        },

        closePatternModal: function() {
            this.patternListOpen = false;
            this.patternInstantiatedElements = [];
            this.patternInstantiatedRelationships = [];
        },

        applyPattern: function() {
            let self = this;
            if (!self.selectedPatternId || !(self.patternContext || '').trim()) return;

            self.patternLoading = true;
            self.patternInstantiatedElements = [];
            self.patternInstantiatedRelationships = [];

            Platform.fetch.post('/archimate/api/patterns/' + self.selectedPatternId + '/instantiate', { context: self.patternContext.trim() }, { silent: true })
            .then(function(data) {
                self.patternLoading = false;
                if (data.error) {
                    self.statusText = 'Pattern error: ' + data.error;
                    return;
                }
                self.patternInstantiatedElements = data.elements || [];
                self.patternInstantiatedRelationships = data.relationships || [];
                self.statusText = 'Pattern "' + (data.pattern_name || '') + '" ready — ' + self.patternInstantiatedElements.length + ' elements to place';
            })
            .catch(function(err) {
                self.patternLoading = false;
                _toast('error', 'Pattern operation failed');
                self.statusText = 'Pattern error: ' + err.message;
            });
        },

        placePatternOnCanvas: function() {
            let self = this;
            let elements = self.patternInstantiatedElements.slice();
            let relationships = self.patternInstantiatedRelationships.slice();
            if (elements.length === 0) return;

            self.patternListOpen = false;
            self.statusText = 'Placing ' + elements.length + ' pattern elements...';

            let roleToId = {};
            let created = 0;
            let total = elements.length;

            const placeNext = function placeNext(idx) {
                if (idx >= total) {
                    /* All elements placed — now create relationships */
                    self._createPatternRelationships(roleToId, relationships);
                    return;
                }
                let el = elements[idx];
                let layer = guessLayer(el.type);

                Platform.fetch.post('/api/architecture-assistant/create-element', { name: el.label, type: el.type, layer: layer }, { silent: true })
                .then(function(data) {
                    let d = data.element || data;
                    if (d.id) {
                        roleToId[el.role] = d.id;

                        let vp = self.paper.translate();
                        let s = self.paper.scale().sx;
                        let rect = self.paper.el.getBoundingClientRect();
                        let cx = (rect.width / 2 - vp.tx) / s;
                        let cy = (rect.height / 2 - vp.ty) / s;
                        let col = idx % 3;
                        let row = Math.floor(idx / 3);
                        let xPos = cx - 200 + col * 240;
                        let yPos = cy - 100 + row * 160;

                        let node = createNode(d.id, d.name, d.type, d.layer || layer, xPos, yPos);
                        self.graph.addCell(node);
                        self.canvasElements[d.id] = d;
                        self.elementCount++;
                        if (self.solutionId) self.linkElementToSolution(d.id);
                        created++;
                    }
                    placeNext(idx + 1);
                })
                .catch(function() { _toast('warning', 'Failed to place pattern element — skipping'); placeNext(idx + 1); });
            };

            placeNext(0);
        },

        _createPatternRelationships: function(roleToId, relationships) {
            let self = this;
            let created = 0;

            const createNext = function createNext(idx) {
                if (idx >= relationships.length) {
                    self.statusText = 'Pattern placed: ' + Object.keys(roleToId).length + ' elements, ' + created + ' relationships';
                    self.patternInstantiatedElements = [];
                    self.patternInstantiatedRelationships = [];
                    return;
                }
                let rel = relationships[idx];
                let sourceId = roleToId[rel.source_role];
                let targetId = roleToId[rel.target_role];

                if (!sourceId || !targetId) {
                    createNext(idx + 1);
                    return;
                }

                Platform.fetch.post('/archimate/api/relationships', {
                    source_element_id: sourceId,
                    target_element_id: targetId,
                    relationship_type: rel.type,
                    solution_id: self.solutionId || null,
                }, { silent: true })
                .then(function(data) {
                    if (data.id) {
                        created++;
                        /* Add link to canvas */
                        let srcCell = self.graph.getElements().filter(function(c) { return c.get('elementId') === sourceId; })[0];
                        let tgtCell = self.graph.getElements().filter(function(c) { return c.get('elementId') === targetId; })[0];
                        if (srcCell && tgtCell) {
                            let style = REL_STYLES[rel.type] || REL_STYLES.association;
                            let link = new joint.shapes.standard.Link({
                                source: { id: srcCell.id },
                                target: { id: tgtCell.id },
                                relId: data.id,
                                relType: rel.type,
                                attrs: {
                                    line: {
                                        stroke: style.stroke,
                                        strokeWidth: style.strokeWidth,
                                        strokeDasharray: style.strokeDasharray || '',
                                    },
                                },
                                labels: [{
                                    position: 0.5,
                                    attrs: { text: { text: rel.type, fontSize: 9, fill: '#64748b' } },
                                }],
                            });
                            self.graph.addCell(link);
                        }
                    }
                    createNext(idx + 1);
                })
                .catch(function() { _toast('warning', 'Failed to create pattern relationship — skipping'); createNext(idx + 1); });
            };

            createNext(0);
        },

        openSavePatternModal: function() {
            if (this.mode === 'view') return;
            let cells = this._selectedCells.length > 0 ? this._selectedCells : this.graph.getElements();
            if (cells.length === 0) {
                this.statusText = 'No elements on canvas to save as pattern';
                return;
            }
            this.savePatternOpen = true;
            this.savePatternName = '';
            this.savePatternDescription = '';
        },

        closeSavePatternModal: function() {
            this.savePatternOpen = false;
        },

        saveSelectionAsPattern: function() {
            let self = this;
            let name = (self.savePatternName || '').trim();
            if (!name) return;

            let cells = self._selectedCells.length > 0 ? self._selectedCells : self.graph.getElements();
            let elements = [];
            let roleIndex = 0;
            let idToRole = {};

            cells.forEach(function(cell) {
                if (!cell.get('elementId')) return;
                let role = 'element_' + (roleIndex++);
                idToRole[cell.get('elementId')] = role;
                elements.push({
                    role: role,
                    type: cell.get('elType') || 'ApplicationComponent',
                    label: '{context} ' + (cell.get('elName') || 'Element'),
                });
            });

            if (elements.length === 0) {
                self.statusText = 'No elements to save';
                return;
            }

            /* Extract relationships between selected elements */
            let relationships = [];
            let links = self.graph.getLinks();
            links.forEach(function(link) {
                let srcCell = link.getSourceCell();
                let tgtCell = link.getTargetCell();
                if (!srcCell || !tgtCell) return;
                let srcRole = idToRole[srcCell.get('elementId')];
                let tgtRole = idToRole[tgtCell.get('elementId')];
                if (srcRole && tgtRole) {
                    relationships.push({
                        source_role: srcRole,
                        target_role: tgtRole,
                        type: link.get('relType') || 'association',
                    });
                }
            });

            Platform.fetch.post('/archimate/api/patterns', {
                name: name,
                description: (self.savePatternDescription || '').trim() || null,
                pattern_json: { elements: elements, relationships: relationships },
            }, { silent: true })
            .then(function(data) {
                if (data.error) {
                    self.statusText = 'Save pattern failed: ' + data.error;
                    return;
                }
                self.savePatternOpen = false;
                self.statusText = 'Pattern "' + name + '" saved (' + elements.length + ' elements, ' + relationships.length + ' relationships)';
            })
            .catch(function(err) {
                _toast('error', 'Failed to save pattern');
                self.statusText = 'Save pattern error: ' + (err && err.message);
            });
        },

        /* ── CMP-021: Baseline-to-target delta and plateau generation ── */

        openDeltaCompare: function() {
            let self = this;
            if (!self.currentSavedVpId) {
                self.statusText = 'Save the current diagram first to use delta compare';
                return;
            }
            self.deltaPickerOpen = true;
            self.deltaCompareVpId = '';
            self.plateauSuggestions = [];
            /* Fetch available diagrams for baseline selection */
            let url = '/archimate/api/saved-viewpoints';
            if (self.solutionId) url += '?solution_id=' + self.solutionId;
            Platform.fetch(url, { silent: true })
            .then(function(data) {
                let vps = (data.viewpoints || []).filter(function(v) {
                    return v.id !== self.currentSavedVpId;
                });
                self.deltaCompareVpList = vps;
            })
            .catch(function() {
                self.deltaCompareVpList = [];
                _toast('error', 'Failed to load viewpoints for comparison');
            });
        },

        runDelta: function() {
            let self = this;
            if (!self.deltaCompareVpId || !self.currentSavedVpId) return;
            self.deltaLoading = true;
            self.statusText = 'Computing delta...';

            Platform.fetch.post('/archimate/api/composer/delta', {
                baseline_viewpoint_id: parseInt(self.deltaCompareVpId),
                target_viewpoint_id: self.currentSavedVpId,
            }, { silent: true })
            .then(function(data) {
                if (data.error) {
                    self.statusText = 'Delta error: ' + data.error;
                    self.deltaLoading = false;
                    return;
                }
                self.deltaData = data;
                self.deltaMode = true;
                self.deltaPickerOpen = false;
                self.deltaLoading = false;
                self.statusText = 'Delta: ' + data.summary.added_count + ' added, '
                    + data.summary.retired_count + ' retired, '
                    + data.summary.modified_count + ' modified';
                self._applyDeltaStyling();
            })
            .catch(function(err) {
                _toast('error', 'Delta comparison failed');
                self.statusText = 'Delta error: ' + (err && err.message);
                self.deltaLoading = false;
            });
        },

        _applyDeltaStyling: function() {
            let self = this;
            if (!self.deltaData || !self.deltaData.delta) return;
            let delta = self.deltaData.delta;

            /* Build lookup: element_id -> classification */
            let classMap = {};
            (delta.added || []).forEach(function(e) { classMap[e.element_id] = 'added'; });
            (delta.retired || []).forEach(function(e) { classMap[e.element_id] = 'retired'; });
            (delta.modified || []).forEach(function(e) { classMap[e.element_id] = 'modified'; });
            (delta.unchanged || []).forEach(function(e) { classMap[e.element_id] = 'unchanged'; });

            /* Apply visual styles to canvas cells */
            self.graph.getElements().forEach(function(cell) {
                let elId = cell.prop('elementId');
                let cls = classMap[elId];
                if (cls === 'added') {
                    cell.attr('body/stroke', '#22c55e');
                    cell.attr('body/strokeWidth', 3);
                    cell.attr('body/strokeDasharray', '');
                    cell.attr('body/opacity', 1);
                } else if (cls === 'retired') {
                    cell.attr('body/stroke', '#ef4444');
                    cell.attr('body/strokeWidth', 3);
                    cell.attr('body/strokeDasharray', '6,4');
                    cell.attr('body/opacity', 0.7);
                } else if (cls === 'modified') {
                    cell.attr('body/stroke', '#f59e0b');
                    cell.attr('body/strokeWidth', 3);
                    cell.attr('body/strokeDasharray', '');
                    cell.attr('body/opacity', 1);
                } else {
                    /* unchanged */
                    cell.attr('body/opacity', 0.4);
                }
            });
        },

        _clearDeltaStyling: function() {
            let self = this;
            self.graph.getElements().forEach(function(cell) {
                let elId = cell.prop('elementId');
                let elData = self.canvasElements[elId];
                let layer = elData ? ((elData.layer || '').toLowerCase() || guessLayer(elData.type)) : 'application';
                let lc = layerColor(layer);
                cell.attr('body/stroke', lc.stroke);
                cell.attr('body/strokeWidth', 1.5);
                cell.attr('body/strokeDasharray', '');
                cell.attr('body/opacity', 1);
            });
        },

        exitDeltaMode: function() {
            this.deltaMode = false;
            this.deltaData = null;
            this.deltaCompareVpId = '';
            this.plateauSuggestions = [];
            this._clearDeltaStyling();
            this.statusText = 'Delta mode exited';
        },

        generatePlateaus: function() {
            let self = this;
            if (!self.deltaData || !self.deltaData.delta) return;
            self.plateauLoading = true;

            Platform.fetch.post('/archimate/api/composer/plateaus', { delta: self.deltaData.delta }, { silent: true })
            .then(function(data) {
                if (data.error) {
                    self.statusText = 'Plateau error: ' + data.error;
                    self.plateauLoading = false;
                    return;
                }
                self.plateauSuggestions = data.plateaus || [];
                self.plateauLoading = false;
                self.statusText = 'Generated ' + self.plateauSuggestions.length + ' plateau(s)';
            })
            .catch(function(err) {
                _toast('error', 'Plateau computation failed');
                self.statusText = 'Plateau error: ' + (err && err.message);
                self.plateauLoading = false;
            });
        },

        highlightPlateauElements: function(elementIds) {
            let self = this;
            /* Dim everything first */
            self.graph.getElements().forEach(function(cell) {
                cell.attr('body/opacity', 0.3);
            });
            /* Highlight plateau elements */
            self.graph.getElements().forEach(function(cell) {
                let elId = cell.prop('elementId');
                if (elementIds.indexOf(elId) !== -1) {
                    cell.attr('body/opacity', 1);
                    cell.attr('body/strokeWidth', 3);
                }
            });
        },

        /* ── CMP-019 — Diagram narration and impact ─────────── */

        explainDiagram: function() {
            let self = this;
            self.explanationLoading = true;
            self.explanationPanelOpen = true;
            self.impactPanelOpen = false;

            let elements = [];
            let relationships = [];

            self.graph.getElements().forEach(function(cell) {
                let elType = cell.get('elType') || '';
                elements.push({
                    id: cell.get('elementId') || cell.id,
                    name: cell.get('elName') || '(unnamed)',
                    type: elType,
                    layer: cell.get('elLayer') || guessLayer(elType),
                });
            });

            self.graph.getLinks().forEach(function(link) {
                let srcCell = self.graph.getCell(link.get('source').id);
                let tgtCell = self.graph.getCell(link.get('target').id);
                if (srcCell && tgtCell) {
                    relationships.push({
                        source_name: srcCell.get('elName') || '(unnamed)',
                        target_name: tgtCell.get('elName') || '(unnamed)',
                        type: link.get('relType') || 'association',
                    });
                }
            });

            Platform.fetch.post('/archimate/api/composer/explain', {
                elements: elements,
                relationships: relationships,
                audience: self.explanationAudience,
            }, { silent: true })
            .then(function(data) {
                self.explanationText = data.narration || 'No narration generated.';
                self.explanationLoading = false;
            })
            .catch(function() {
                _toast('error', 'Explanation generation failed');
                self.explanationText = 'Failed to generate explanation.';
                self.explanationLoading = false;
            });
        },

        explainImpact: function(elementId) {
            let self = this;
            self.impactPanelOpen = true;
            self.explanationPanelOpen = false;
            self.impactData = null;

            let elements = [];
            let relationships = [];

            self.graph.getElements().forEach(function(cell) {
                elements.push({
                    id: cell.get('elementId') || cell.id,
                    name: cell.get('elName') || '(unnamed)',
                    type: cell.get('elType') || '',
                });
            });

            self.graph.getLinks().forEach(function(link) {
                let srcCell = self.graph.getCell(link.get('source').id);
                let tgtCell = self.graph.getCell(link.get('target').id);
                if (srcCell && tgtCell) {
                    relationships.push({
                        source_id: srcCell.get('elementId') || srcCell.id,
                        target_id: tgtCell.get('elementId') || tgtCell.id,
                        type: link.get('relType') || 'association',
                    });
                }
            });

            Platform.fetch.post('/archimate/api/composer/impact', {
                element_id: elementId,
                elements: elements,
                relationships: relationships,
            }, { silent: true })
            .then(function(data) {
                self.impactData = data;
                self._highlightImpact(data.affected_element_ids || []);
            })
            .catch(function() {
                _toast('error', 'Impact computation failed');
                self.impactData = { impact_narrative: 'Failed to compute impact.', affected_element_ids: [], hop_details: [] };
            });
        },

        explainRelationship: function(relData) {
            let self = this;
            Platform.fetch.post('/archimate/api/composer/explain-relationship', relData, { silent: true })
            .then(function(data) {
                self.statusText = data.explanation || 'No explanation available.';
            })
            .catch(function() {
                _toast('error', 'Failed to explain relationship');
                self.statusText = 'Failed to explain relationship.';
            });
        },

        explainImpactFromCtx: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            let elId = cell.get('elementId') || cell.id;
            this.explainImpact(elId);
        },

        explainRelFromCtx: function() {
            this.relCtxMenuOpen = false;
            let link = this.relCtxMenuLink;
            if (!link) return;
            let srcCell = this.graph.getCell(link.get('source').id);
            let tgtCell = this.graph.getCell(link.get('target').id);
            if (!srcCell || !tgtCell) return;
            this.explainRelationship({
                source_name: srcCell.get('elName') || '(unnamed)',
                target_name: tgtCell.get('elName') || '(unnamed)',
                relationship_type: link.get('relType') || 'association',
                source_type: srcCell.get('elType') || '',
                target_type: tgtCell.get('elType') || '',
            });
        },

        _highlightImpact: function(affectedIds) {
            let self = this;
            self._clearImpactHighlight();
            if (!affectedIds || affectedIds.length === 0) return;
            let idSet = {};
            affectedIds.forEach(function(id) { idSet[String(id)] = true; });
            self.graph.getElements().forEach(function(cell) {
                let cellElId = String(cell.get('elementId') || cell.id);
                let view = self.paper.findViewByModel(cell);
                if (!view) return;
                if (idSet[cellElId]) {
                    view.el.classList.add('impact-affected');
                } else {
                    view.el.classList.add('impact-dimmed');
                }
            });
        },

        _clearImpactHighlight: function() {
            let self = this;
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (!view) return;
                view.el.classList.remove('impact-affected', 'impact-dimmed');
            });
        },

        /* ── CMP-045: Heatmap overlay ──────────────────────── */

        toggleHeatmap: function(metric) {
            let self = this;
            if (self.heatmapEnabled && self.heatmapMetric === metric) {
                self._clearHeatmap();
                self.heatmapEnabled = false;
                self.statusText = 'Heatmap disabled';
                return;
            }
            self.heatmapMetric = metric || 'maturity';
            self.heatmapEnabled = true;
            self.heatmapLoading = true;
            self.statusText = 'Loading heatmap (' + self.heatmapMetric + ')...';

            let ids = Object.keys(self.canvasElements);
            if (ids.length === 0) {
                self.heatmapLoading = false;
                self.statusText = 'No elements on canvas';
                return;
            }

            Platform.fetch('/archimate/api/composer/intelligence?element_ids=' + ids.join(','), { silent: true })
            .then(function(data) {
                self.heatmapLoading = false;
                let enrichment = data.enrichment || {};
                self._applyHeatmap(enrichment, self.heatmapMetric);
                self.statusText = 'Heatmap: ' + self.heatmapMetric;
            })
            .catch(function() {
                _toast('error', 'Heatmap failed');
                self.heatmapLoading = false;
                self.statusText = 'Heatmap failed';
            });
        },

        _heatmapColor: function(value) {
            // 0 = red (#ef4444), 50 = amber (#f59e0b), 100 = green (#22c55e)
            // Returns fill colour on a 3-stop gradient
            if (value === null || value === undefined) return '#9ca3af'; // grey for no data
            let v = Math.max(0, Math.min(100, value));
            let r, g, b;
            if (v <= 50) {
                let t = v / 50;
                r = Math.round(239 + (245 - 239) * t);
                g = Math.round(68 + (158 - 68) * t);
                b = Math.round(68 + (11 - 68) * t);
            } else {
                let t2 = (v - 50) / 50;
                r = Math.round(245 + (34 - 245) * t2);
                g = Math.round(158 + (197 - 158) * t2);
                b = Math.round(11 + (94 - 11) * t2);
            }
            return 'rgb(' + r + ',' + g + ',' + b + ')';
        },

        _getMetricValue: function(info, metric) {
            if (!info) return null;
            if (metric === 'maturity') {
                let m = info.maturity_score || info.maturity;
                if (m !== undefined && m !== null) return m;
                if (info.signals && info.signals.indexOf('maturity_gap') !== -1) return 25;
                return null;
            }
            if (metric === 'risk') {
                if (info.signals) {
                    if (info.signals.indexOf('eol_risk') !== -1) return 15;
                    if (info.signals.indexOf('lifecycle_risk') !== -1) return 30;
                }
                return info.risk_score !== undefined ? info.risk_score : null;
            }
            if (metric === 'adoption') {
                return info.solution_usage_count ? Math.min(100, info.solution_usage_count * 20) : null;
            }
            return null;
        },

        _applyHeatmap: function(enrichment, metric) {
            let self = this;
            let cells = self.graph.getElements();
            cells.forEach(function(cell) {
                let eid = String(cell.get('elementId') || cell.id);
                let info = enrichment[eid];
                let val = self._getMetricValue(info, metric);
                let color = self._heatmapColor(val);

                // Store original fill so we can restore
                if (!cell.get('_heatmapOrigFill')) {
                    cell.set('_heatmapOrigFill', cell.attr('body/fill'));
                }
                cell.attr('body/fill', color);
                cell.attr('body/fillOpacity', 0.85);
            });
        },

        _clearHeatmap: function() {
            let cells = this.graph.getElements();
            cells.forEach(function(cell) {
                let origFill = cell.get('_heatmapOrigFill');
                if (origFill) {
                    cell.attr('body/fill', origFill);
                    cell.attr('body/fillOpacity', 1);
                    cell.set('_heatmapOrigFill', null);
                }
            });
        },

        /* ── CMP-046: Derived relationships ────────────────── */

        toggleDerived: function() {
            let self = this;
            if (self.derivedEnabled) {
                self._clearDerived();
                self.derivedEnabled = false;
                self.statusText = 'Derived relationships hidden';
                return;
            }
            self.derivedEnabled = true;
            self.derivedLoading = true;
            self.statusText = 'Computing derived relationships...';

            let elements = [];
            let relationships = [];
            self.graph.getElements().forEach(function(cell) {
                let eid = cell.get('elementId');
                if (!eid) return;
                elements.push({
                    id: eid,
                    name: cell.get('elName') || '',
                    type: cell.get('elType') || '',
                    layer: cell.get('elLayer') || '',
                });
            });
            self.graph.getLinks().forEach(function(link) {
                let relId = link.get('relId');
                if (!relId) return;
                relationships.push({
                    id: relId,
                    source_id: link.get('sourceElementId'),
                    target_id: link.get('targetElementId'),
                    type: link.get('relType') || 'Association',
                });
            });

            Platform.fetch.post('/archimate/api/composer/derived-relationships', { elements: elements, relationships: relationships }, { silent: true })
            .then(function(data) {
                self.derivedLoading = false;
                let derived = data.derived || [];
                self._renderDerived(derived);
                self.statusText = derived.length + ' derived relationships found';
            })
            .catch(function() {
                _toast('error', 'Derived computation failed');
                self.derivedLoading = false;
                self.statusText = 'Derived computation failed';
            });
        },

        _renderDerived: function(derived) {
            let self = this;
            self._clearDerived();

            let cellMap = {};
            self.graph.getElements().forEach(function(cell) {
                let eid = cell.get('elementId');
                if (eid) cellMap[String(eid)] = cell;
            });

            derived.forEach(function(d) {
                let srcCell = cellMap[String(d.source_id)];
                let tgtCell = cellMap[String(d.target_id)];
                if (!srcCell || !tgtCell) return;

                let link = new joint.shapes.standard.Link({
                    source: { id: srcCell.id },
                    target: { id: tgtCell.id },
                    attrs: {
                        line: {
                            stroke: '#a855f7',
                            strokeWidth: 1.5,
                            strokeDasharray: '6,3',
                            targetMarker: { type: 'path', d: 'M 10 -5 0 0 10 5 z', fill: '#a855f7' },
                        },
                    },
                    labels: [{
                        attrs: {
                            text: { text: d.type + ' (derived)', fontSize: 8, fill: '#a855f7', fontWeight: 600 },
                            rect: { fill: 'white', rx: 3, ry: 3 },
                        },
                        position: 0.5,
                    }],
                });
                link.set('_isDerived', true);
                self.graph.addCell(link);
                self.derivedLinks.push(link);
            });
        },

        _clearDerived: function() {
            let self = this;
            self.derivedLinks.forEach(function(link) {
                link.remove();
            });
            self.derivedLinks = [];
        },

        /* ── CMP-044: Gap analysis overlay ── */
        showGapAnalysisOverlay: function() {
            let self = this;
            if (!self.deltaResults) { self._toast('Run Compare Baseline first', 'warning'); return; }

            self.gapOverlayActive = true;
            let results = self.deltaResults;

            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let eid = cell.get('elementId');
                let view = self.paper.findViewByModel(cell);
                if (!view) return;

                let status = null;
                if (results.added && results.added.some(function(e) { return e.id == eid; })) status = 'added';
                else if (results.removed && results.removed.some(function(e) { return e.id == eid; })) status = 'removed';
                else if (results.changed && results.changed.some(function(e) { return e.id == eid; })) status = 'changed';

                if (status === 'added') {
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#22c55e', 'stroke-width': 3 } } } }); } catch(e) { /* swallow-ok: cosmetic gap-overlay outline; the opacity tint already separates added, removed and changed elements */ }
                } else if (status === 'removed') {
                    view.vel.attr({ opacity: 0.3 });
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#ef4444', 'stroke-width': 3, 'stroke-dasharray': '4,4' } } } }); } catch(e) { /* swallow-ok: cosmetic gap-overlay outline; the opacity tint already separates added, removed and changed elements */ }
                } else if (status === 'changed') {
                    try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 6, attrs: { stroke: '#f59e0b', 'stroke-width': 2, 'stroke-dasharray': '6,3' } } } }); } catch(e) { /* swallow-ok: cosmetic gap-overlay outline; the opacity tint already separates added, removed and changed elements */ }
                } else {
                    view.vel.attr({ opacity: 0.2 });
                }
            });
        },

        clearGapOverlay: function() {
            let self = this;
            self.gapOverlayActive = false;
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (!view) return;
                view.vel.attr({ opacity: 1 });
                try { view.unhighlight(null, { highlighter: { name: 'stroke' } }); } catch(e) { /* swallow-ok: cosmetic un-highlight while clearing the gap overlay */ }
            });
        },

        /* ── CMP2-009: Real-time ArchiMate compliance score ────── */
        complianceScore: 100,
        complianceDeductions: [],
        compliancePanelOpen: false,
        _complianceTimer: null,

        /* One pass over the drawing. The quality figure, the list of deductions behind it
           and the toolbar's error and warning counts all come from the issues found here,
           so the figure can never read 100% beside a warning it counts. Each issue is
           listed; the penalty it costs is capped per rule, so a capped issue lists at 0. */
        computeComplianceScore: function() {
            let self = this;
            let score = 100;
            let deductions = [];
            let elements = self.graph.getElements().filter(function(el) {
                return !el.get('isLayerZone') && !el.get('isAnnotation');
            });
            let links = self.graph.getLinks();
            let nameOf = function(el) {
                return el.get('elName') || el.get('attrs/label/text') || 'Unnamed';
            };
            /* Add one issue; its penalty is whatever is left under the rule's cap. */
            let spent = {};
            let issue = function(severity, rule, each, cap, elementId, message) {
                let pen = Math.max(0, Math.min(each, cap - (spent[rule] || 0)));
                spent[rule] = (spent[rule] || 0) + pen;
                score -= pen;
                deductions.push({ severity: severity, rule: rule, penalty: pen, elementId: elementId, message: message });
            };

            /* (a) Self-relationships are errors */
            links.forEach(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                if (src && tgt && src.id && tgt.id && src.id === tgt.id) {
                    let cell = link.getSourceElement();
                    issue('error', 'Self-relationship', 10, 20, cell ? cell.id : null,
                        (cell ? nameOf(cell) : 'An element') + ' is related to itself');
                }
            });

            /* (b) Orphan elements: no connected links, only judged once 2+ elements exist */
            if (elements.length >= 2) {
                elements.forEach(function(el) {
                    if (self.graph.getConnectedLinks(el).length === 0) {
                        issue('warning', 'Orphan element', 5, 25, el.id, nameOf(el) + ' has no relationships');
                    }
                });
            }

            /* (c) Duplicate names: one issue per duplicated name */
            let seen = {};
            let reported = {};
            elements.forEach(function(el) {
                let name = (el.get('elName') || '').trim().toLowerCase();
                if (!name) return;
                if (!seen[name]) { seen[name] = el; return; }
                if (!reported[name]) {
                    reported[name] = true;
                    issue('warning', 'Duplicate name', 2, 10, seen[name].id, nameOf(seen[name]) + ' is named more than once');
                }
            });

            /* (d) Layer coverage — only check if >= 5 elements */
            if (elements.length >= 5) {
                let layers = {};
                elements.forEach(function(el) {
                    let layer = (el.get('elLayer') || el.get('layer') || '').toLowerCase();
                    if (!layer && guessLayer) {
                        layer = (guessLayer(el.get('elType') || el.get('type') || '') || '').toLowerCase();
                    }
                    if (layer) layers[layer] = true;
                });
                if (!layers['motivation']) issue('warning', 'Missing layer', 15, 15, null, 'No Motivation layer elements present');
                if (!layers['business']) issue('warning', 'Missing layer', 10, 25, null, 'No Business layer elements present');
                if (!layers['application']) issue('warning', 'Missing layer', 10, 35, null, 'No Application layer elements present');
            }

            /* (e) Cross-layer connectivity: BusinessProcess should connect to Application layer */
            elements.forEach(function(el) {
                let elType = (el.get('elType') || el.get('type') || '').replace(/\s+/g, '');
                if (elType !== 'BusinessProcess') return;
                let hasAppLink = self.graph.getConnectedLinks(el).some(function(link) {
                    let source = link.getSourceElement();
                    let target = link.getTargetElement();
                    let other = (source && source.id === el.id) ? target : source;
                    if (!other) return false;
                    let otherType = (other.get('elType') || other.get('type') || '').replace(/\s+/g, '');
                    return otherType === 'ApplicationComponent' || otherType === 'ApplicationService';
                });
                if (!hasAppLink) {
                    issue('warning', 'Cross-layer gap', 5, 20, el.id, nameOf(el) + ' has no link to Application layer');
                }
            });

            /* Clamp 0–100; a drawing with any issue is never perfect. */
            score = Math.max(0, Math.min(100, score));
            if (deductions.length > 0) score = Math.min(score, 99);

            self.complianceScore = score;
            self.complianceDeductions = deductions;
            self.validationErrors = deductions.filter(function(d) { return d.severity === 'error'; }).length;
            self.validationWarnings = deductions.length - self.validationErrors;
        },

        highlightComplianceElement: function(elementId) {
            if (!elementId) return;
            let cell = this.graph.getCell(elementId);
            if (!cell) return;
            let view = this.paper.findViewByModel(cell);
            if (view) {
                this._highlightCell(view);
                /* Scroll element into view */
                let bbox = cell.getBBox();
                if (bbox && this.paper) {
                    this.paper.setOrigin(
                        -bbox.x * (this._currentScale || 1) + this.paper.el.clientWidth / 2 - bbox.width / 2,
                        -bbox.y * (this._currentScale || 1) + this.paper.el.clientHeight / 2 - bbox.height / 2
                    );
                }
            }
        },
        /* ── CMP2-008: What-if retirement simulation ─────────── */
        simulateRetirement: function(cell) {
            if (!cell || !cell.isElement()) return;
            let self = this;

            /* Clear any previous simulation first */
            self.clearRetirementSimulation();

            self.retirementSimActive = true;
            self.retirementSimSource = cell;

            /* BFS: build map of cellId → hopDistance (max 3 hops) */
            let hopMap = {};
            let queue = [{ cellId: cell.id, hop: 0 }];
            hopMap[cell.id] = 0;

            let links = self.graph.getLinks();

            while (queue.length > 0) {
                const current = queue.shift();
                if (current.hop >= 3) continue;

                const nextHop = current.hop + 1;
                links.forEach(function(link) {
                    let sourceId = link.get('source') && link.get('source').id;
                    let targetId = link.get('target') && link.get('target').id;
                    let neighborId = null;

                    if (sourceId === current.cellId) neighborId = targetId;
                    else if (targetId === current.cellId) neighborId = sourceId;

                    if (neighborId && hopMap[neighborId] === undefined) {
                        hopMap[neighborId] = nextHop;
                        queue.push({ cellId: neighborId, hop: nextHop });
                    }
                });
            }

            /* Apply classes to all elements */
            self.graph.getElements().forEach(function(el) {
                let view = self.paper.findViewByModel(el);
                if (!view || !view.el) return;
                if (el.get('isLayerZone') || el.get('isAnnotation')) return;

                const hop = hopMap[el.id];
                if (el.id === cell.id) {
                    view.el.classList.add('retirement-source');
                } else if (hop === 1) {
                    view.el.classList.add('retirement-hop-1');
                } else if (hop === 2) {
                    view.el.classList.add('retirement-hop-2');
                } else if (hop === 3) {
                    view.el.classList.add('retirement-hop-3');
                } else {
                    view.el.classList.add('retirement-unaffected');
                }
            });

            /* Count affected (hops 1-3, excluding source) */
            let affectedCount = 0;
            for (const key in hopMap) {
                if (hopMap.hasOwnProperty(key) && key !== cell.id) {
                    affectedCount++;
                }
            }
            self.retirementAffectedCount = affectedCount;
            self.statusText = 'Retirement simulation: ' + affectedCount + ' elements affected';
        },

        clearRetirementSimulation: function() {
            let self = this;
            self.retirementSimActive = false;
            self.retirementSimSource = null;
            self.retirementAffectedCount = 0;

            const classes = ['retirement-source', 'retirement-hop-1', 'retirement-hop-2', 'retirement-hop-3', 'retirement-unaffected'];
            self.graph.getElements().forEach(function(el) {
                let view = self.paper.findViewByModel(el);
                if (!view || !view.el) return;
                classes.forEach(function(cls) {
                    view.el.classList.remove(cls);
                });
            });
            self.statusText = 'Ready';
        },

        /* ── CMP2-004: Live metrics overlay ───────────────────── */

        toggleMetricsOverlay: function(type) {
            let self = this;
            if (type === 'off' || self.metricsOverlayType === type) {
                self.clearMetricsOverlay();
                self.metricsOverlayType = 'off';
                self.statusText = 'Metrics overlay disabled';
                return;
            }
            self.metricsOverlayType = type;
            self.fetchAndApplyMetrics();
        },

        fetchAndApplyMetrics: function() {
            let self = this;
            let ids = Object.keys(self.canvasElements);
            if (ids.length === 0) {
                self.statusText = 'No elements on canvas';
                return;
            }
            self.metricsLoading = true;
            self.statusText = 'Loading metrics (' + self.metricsOverlayType + ')...';

            Platform.fetch('/archimate/api/composer/element-metrics?element_ids=' + ids.join(','), { silent: true })
            .then(function(data) {
                self.metricsLoading = false;
                self.metricsData = data.metrics || {};
                self.clearMetricsOverlay();
                self.applyMetricsOverlay();
                self.statusText = 'Metrics: ' + self.metricsOverlayType;
            })
            .catch(function() {
                _toast('error', 'Metrics overlay failed');
                self.metricsLoading = false;
                self.statusText = 'Metrics overlay failed';
            });
        },

        _metricsColor: function(type, value) {
            // Returns [bgColor, textColor, label]
            if (value === null || value === undefined) {
                return ['#6b7280', '#ffffff', 'N/A'];
            }
            if (type === 'maturity') {
                const lvl = parseInt(value, 10);
                if (lvl >= 4) return ['#16a34a', '#ffffff', 'M' + lvl];
                if (lvl >= 2) return ['#d97706', '#ffffff', 'M' + lvl];
                return ['#dc2626', '#ffffff', 'M' + lvl];
            }
            if (type === 'lifecycle') {
                let lc = String(value).toLowerCase();
                if (lc === 'active' || lc === 'current' || lc === 'production') return ['#16a34a', '#ffffff', value];
                if (lc === 'planned' || lc === 'development' || lc === 'pilot') return ['#d97706', '#ffffff', value];
                if (lc === 'phasing out' || lc === 'phasing_out' || lc === 'retiring' || lc === 'deprecated') return ['#dc2626', '#ffffff', value];
                if (lc === 'retired') return ['#991b1b', '#ffffff', value];
                return ['#6b7280', '#ffffff', value];
            }
            if (type === 'risk') {
                const risk = String(value).toLowerCase();
                if (risk === 'low') return ['#16a34a', '#ffffff', 'Low'];
                if (risk === 'medium') return ['#d97706', '#ffffff', 'Med'];
                if (risk === 'high') return ['#dc2626', '#ffffff', 'High'];
                if (risk === 'critical') return ['#991b1b', '#ffffff', 'Crit'];
                return ['#6b7280', '#ffffff', value];
            }
            if (type === 'cost') {
                const cost = parseFloat(value);
                if (isNaN(cost)) return ['#6b7280', '#ffffff', 'N/A'];
                let label;
                if (cost >= 1000000) { label = '$' + (cost / 1000000).toFixed(1) + 'M'; }
                else if (cost >= 1000) { label = '$' + (cost / 1000).toFixed(0) + 'k'; }
                else { label = '$' + cost.toFixed(0); }
                // Color by cost band: green < 100k, amber 100k-500k, red > 500k
                if (cost < 100000) return ['#16a34a', '#ffffff', label];
                if (cost < 500000) return ['#d97706', '#ffffff', label];
                return ['#dc2626', '#ffffff', label];
            }
            return ['#6b7280', '#ffffff', 'N/A'];
        },

        applyMetricsOverlay: function() {
            let self = this;
            const type = self.metricsOverlayType;
            if (type === 'off') return;

            self.graph.getElements().forEach(function(cell) {
                let eid = String(cell.get('elementId') || cell.id);
                const data = self.metricsData[eid];
                const value = data ? data[type] : null;
                const colorInfo = self._metricsColor(type, value);
                const bg = colorInfo[0];
                const fg = colorInfo[1];
                let label = colorInfo[2];

                let view = self.paper.findViewByModel(cell);
                if (!view || !view.el) return;

                // Create badge element
                const badge = document.createElement('div');
                badge.className = 'metrics-badge';
                badge.textContent = label;
                badge.style.cssText = 'position:absolute;top:-4px;right:-4px;z-index:10;'
                    + 'background:' + bg + ';color:' + fg + ';'
                    + 'font-size:9px;font-weight:600;line-height:1;'
                    + 'padding:2px 5px;border-radius:8px;white-space:nowrap;'
                    + 'pointer-events:none;box-shadow:0 1px 3px rgba(0,0,0,0.25);'
                    + 'border:1.5px solid rgba(255,255,255,0.7);';

                // Position the badge relative to the element's SVG bounding box
                const svgEl = view.el;
                svgEl.style.position = 'relative';
                svgEl.style.overflow = 'visible';

                // Use foreignObject for HTML badge inside SVG
                const ns = 'http://www.w3.org/2000/svg';
                const fo = document.createElementNS(ns, 'foreignObject');
                fo.setAttribute('class', 'metrics-badge-fo');
                fo.setAttribute('width', '80');
                fo.setAttribute('height', '20');
                // Position top-right of the element bounding box
                let bbox = cell.getBBox();
                fo.setAttribute('x', String(bbox.width - 20));
                fo.setAttribute('y', '-10');
                fo.setAttribute('style', 'overflow:visible;pointer-events:none;');

                const div = document.createElement('div');
                div.className = 'metrics-badge';
                div.textContent = label;
                div.setAttribute('style',
                    'background:' + bg + ';color:' + fg + ';'
                    + 'font-size:9px;font-weight:600;line-height:1;display:inline-block;'
                    + 'padding:2px 5px;border-radius:8px;white-space:nowrap;'
                    + 'box-shadow:0 1px 3px rgba(0,0,0,0.25);'
                    + 'border:1.5px solid rgba(255,255,255,0.7);'
                );

                fo.appendChild(div);
                svgEl.appendChild(fo);
            });
        },

        clearMetricsOverlay: function() {
            let self = this;
            // Remove all foreignObject badges
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (!view || !view.el) return;
                const badges = view.el.querySelectorAll('.metrics-badge-fo');
                for (let i = 0; i < badges.length; i++) {
                    badges[i].parentNode.removeChild(badges[i]);
                }
            });
        },

        };

        return methods;
    }

    return { getMethods: getMethods };
})();
