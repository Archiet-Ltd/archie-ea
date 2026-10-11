/**
 * BIZBOK Capability x Value-Stream-Stage grid.
 *
 * Alpine.data component backing app/templates/value_streams/detail.html.
 * Fetches the grid (capabilities x stages x mapping cells) from
 * GET /value-streams/<id>/grid and lets the user click a cell to
 * create/update/clear the CapabilityValueStreamMapping via the JSON API
 * at /value-streams/api/mapping (POST/PUT to upsert, DELETE to clear).
 */
(function () {
    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    function jsonHeaders() {
        return {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken()
        };
    }

    // Registered on `alpine:init`, not at script-evaluation time: this file is
    // loaded from the template body while Alpine itself is deferred, so a bare
    // `Alpine.data(...)` threw `ReferenceError: Alpine is not defined` and the
    // whole BIZBOK grid never registered -- x-data="bizbokGrid(...)" then failed
    // with `bizbokGrid is not a function`, leaving the grid permanently blank.
    function registerBizbokGrid() {
    Alpine.data('bizbokGrid', function (config) {
        config = config || {};
        return {
            valueStreamId: config.valueStreamId,

            // Grid state
            loading: true,
            loadError: false,
            stages: [],
            capabilities: [],
            cells: {},

            // Capability picker
            capabilitySearch: '',
            capabilityResults: [],

            // Cell edit modal state
            activeCell: null,
            cellForm: {
                support_type: 'primary',
                support_level: 3,
                capability_contribution: 50,
                impact_level: 'medium',
                stage_criticality: 'medium'
            },
            saving: false,

            // Stage edit/delete modal state (populated by openStageEditModal /
            // openStageDeleteModal below, called from the swimlane card buttons)
            stageEditForm: {},
            stageDeleteForm: {},

            // AI mapping-suggestion panel state. Advisory only: suggestions
            // are never written automatically — "Apply" reuses the exact
            // same POST /value-streams/api/mapping call as saveCell() above.
            aiLoading: false,
            aiError: null,
            aiSummary: null,
            aiSuggestions: [],
            aiApplyingIndex: null,

            init() {
                this.loadGrid();
            },

            openStageEditModal(stage) {
                this.stageEditForm = Object.assign({}, stage);
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('stage-edit-modal');
                }
            },

            openStageDeleteModal(stage) {
                this.stageDeleteForm = Object.assign({}, stage);
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('stage-delete-modal');
                }
            },

            cellKey(capabilityId, stageId) {
                return capabilityId + ':' + stageId;
            },

            cellLabel(capabilityId, stageId) {
                var cell = this.cells[this.cellKey(capabilityId, stageId)];
                if (!cell || !cell.support_level) return '—';
                return String(cell.support_level);
            },

            // Capability maturity as recorded on the capability itself.
            // Unassessed renders as an em dash, never as a level.
            maturityLabel(cap) {
                var current = cap && cap.current_maturity_level;
                var target = cap && cap.target_maturity_level;
                var label = (current === null || current === undefined) ? '—' : 'L' + current;
                if (target !== null && target !== undefined) {
                    label += ' → L' + target;
                }
                return label;
            },

            // Capabilities mapped to one stage, for the swimlane card.
            capabilitiesForStage(stageId) {
                var self = this;
                return this.capabilities.filter(function (cap) {
                    var cell = self.cells[self.cellKey(cap.id, stageId)];
                    return cell && cell.support_level;
                });
            },

            cellClass(capabilityId, stageId) {
                var cell = this.cells[this.cellKey(capabilityId, stageId)];
                if (!cell || !cell.support_level) {
                    return 'bg-muted border-border text-muted-foreground hover:border-primary/50';
                }
                var level = cell.support_level;
                if (level >= 5) return 'bg-emerald-500/70 border-emerald-600/60 text-emerald-950';
                if (level >= 4) return 'bg-emerald-500/40 border-emerald-500/50 text-emerald-800';
                if (level >= 2) return 'bg-amber-500/25 border-amber-500/40 text-amber-800';
                return 'bg-destructive/20 border-destructive/30 text-destructive';
            },

            async loadGrid() {
                this.loading = true;
                this.loadError = false;
                try {
                    var data = await Platform.fetch('/value-streams/' + this.valueStreamId + '/grid');
                    this.stages = data.stages || [];
                    this.capabilities = data.capabilities || [];
                    this.cells = data.cells || {};
                } catch (err) {
                    // Platform.fetch already shows a toast unless silent:true.
                    // We also need to paint inline error state, so keep loadError true.
                    this.loadError = true;
                } finally {
                    this.loading = false;
                }
            },

            async searchCapabilities() {
                var q = this.capabilitySearch.trim();
                if (!q) {
                    this.capabilityResults = [];
                    return;
                }
                // Guard against out-of-order responses: a slow earlier request
                // resolving after a faster later one must not clobber it.
                var requestId = (this._searchRequestId = (this._searchRequestId || 0) + 1);
                try {
                    var data = await Platform.fetch.get(
                        '/value-streams/' + this.valueStreamId + '/api/unmapped-capabilities',
                        { q: q },
                        { silent: true } // We'll show our own toast on error
                    );
                    if (requestId !== this._searchRequestId) return; // stale response
                    this.capabilityResults = data.capabilities || [];
                } catch (err) {
                    if (requestId !== this._searchRequestId) return; // stale response
                    // Platform.fetch threw; we must surface the failure to the user.
                    this.capabilityResults = [];
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Capability search failed — this is a lookup failure, not an empty result.');
                    }
                }
            },

            selectCapabilityToAdd(cap) {
                this.capabilitySearch = '';
                this.capabilityResults = [];
                if (this.stages.length === 0) {
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Add a stage before mapping capabilities.');
                    }
                    return;
                }
                // Add as a row immediately (no mapping yet) and open the first
                // stage's cell so the user sets an initial support level.
                if (!this.capabilities.some(function (c) { return c.id === cap.id; })) {
                    this.capabilities.push(cap);
                }
                this.openCellModal(cap, this.stages[0]);
            },

            openCellModal(cap, stage) {
                var existing = this.cells[this.cellKey(cap.id, stage.id)];
                this.activeCell = {
                    capabilityId: cap.id,
                    capabilityName: cap.name,
                    stageId: stage.id,
                    stageName: stage.name,
                    mappingId: existing ? existing.mapping_id : null
                };
                this.cellForm = {
                    support_type: (existing && existing.support_type) || 'primary',
                    support_level: (existing && existing.support_level) || 3,
                    capability_contribution: (existing && existing.capability_contribution) != null
                        ? existing.capability_contribution : 50,
                    impact_level: (existing && existing.impact_level) || 'medium',
                    stage_criticality: (existing && existing.stage_criticality) || 'medium'
                };
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('cell-edit-modal');
                }
            },

            async saveCell() {
                if (!this.activeCell) return;
                this.saving = true;
                try {
                    var data = await Platform.fetch.post('/value-streams/api/mapping', {
                        capability_id: this.activeCell.capabilityId,
                        value_stream_id: this.valueStreamId,
                        value_stream_stage_id: this.activeCell.stageId,
                        support_type: this.cellForm.support_type,
                        support_level: this.cellForm.support_level,
                        capability_contribution: this.cellForm.capability_contribution,
                        impact_level: this.cellForm.impact_level,
                        stage_criticality: this.cellForm.stage_criticality
                    });
                    // Platform.fetch already threw on non‑ok, so data is the parsed success response.
                    this.cells[this.cellKey(this.activeCell.capabilityId, this.activeCell.stageId)] = {
                        mapping_id: data.mapping.id,
                        support_type: data.mapping.support_type,
                        support_level: data.mapping.support_level,
                        capability_contribution: data.mapping.capability_contribution,
                        impact_level: data.mapping.impact_level,
                        stage_criticality: data.mapping.stage_criticality
                    };
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.success('Mapping saved.');
                    }
                    if (window.Platform && window.Platform.modal) {
                        window.Platform.modal.close('cell-edit-modal');
                    }
                } catch (err) {
                    // Platform.fetch already shows a toast unless silent:true.
                    // We keep the existing toast call for consistency, but the error is already visible.
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Failed to save mapping: ' + err.message);
                    }
                } finally {
                    this.saving = false;
                }
            },

            async clearCell() {
                if (!this.activeCell) return;
                this.saving = true;
                try {
                    var data = await Platform.fetch.delete('/value-streams/api/mapping', {
                        capability_id: this.activeCell.capabilityId,
                        value_stream_id: this.valueStreamId,
                        value_stream_stage_id: this.activeCell.stageId
                    });
                    // Platform.fetch already threw on non‑ok, so data is the parsed success response.
                    delete this.cells[this.cellKey(this.activeCell.capabilityId, this.activeCell.stageId)];
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.success('Mapping cleared.');
                    }
                    if (window.Platform && window.Platform.modal) {
                        window.Platform.modal.close('cell-edit-modal');
                    }
                } catch (err) {
                    // Platform.fetch already shows a toast unless silent:true.
                    // We keep the existing toast call for consistency, but the error is already visible.
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Failed to clear mapping: ' + err.message);
                    }
                } finally {
                    this.saving = false;
                }
            },

            async suggestMappings() {
                this.aiLoading = true;
                this.aiError = null;
                try {
                    var data = await Platform.fetch.post('/value-streams/api/' + this.valueStreamId + '/ai-suggest-mappings', null);
                    // Platform.fetch already threw on non‑ok, so data is the parsed success response.
                    this.aiSummary = data.summary || null;
                    this.aiSuggestions = (data.suggestions || []).map(function (s) {
                        return Object.assign({}, s, { applied: false });
                    });
                } catch (err) {
                    // Platform.fetch already shows a toast unless silent:true.
                    // We also need to paint inline error state, so set aiError.
                    this.aiError = err.message || 'AI mapping suggestions failed';
                } finally {
                    this.aiLoading = false;
                }
            },

            // Resolve a capability name (from an AI suggestion) to its id.
            // Checks the grid's already-loaded rows first, then falls back
            // to the same unmapped-capabilities search the "add row" picker
            // uses, matching the name exactly (case-insensitive).
            async resolveCapabilityByName(name) {
                var existing = this.capabilities.find(function (c) { return c.name === name; });
                if (existing) return existing;
                try {
                    var data = await Platform.fetch.get(
                        '/value-streams/' + this.valueStreamId + '/api/unmapped-capabilities',
                        { q: name, limit: 10 },
                        { silent: true } // We'll handle errors via thrown exception
                    );
                    var results = data.capabilities || [];
                    var lower = name.trim().toLowerCase();
                    return results.find(function (c) { return (c.name || '').trim().toLowerCase() === lower; }) || null;
                } catch (err) {
                    // Rethrow, and note that silent:true above means nothing has
                    // told the user yet -- applySuggestion owns that toast.
                    //
                    // A null return from this helper is MEANINGFUL: it says the
                    // named capability does not exist. Swallowing a lookup failure
                    // here would return that same null and report a capability as
                    // missing when the request simply failed, which the user cannot
                    // tell apart and would act on.
                    throw err;
                }
            },

            async applySuggestion(index) {
                var suggestion = this.aiSuggestions[index];
                if (!suggestion || suggestion.applied) return;
                var stage = this.stages.find(function (s) { return s.name === suggestion.stage; });
                if (!stage) {
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Stage "' + suggestion.stage + '" is no longer on this value stream.');
                    }
                    return;
                }
                this.aiApplyingIndex = index;
                try {
                    var cap = await this.resolveCapabilityByName(suggestion.capability);
                    if (!cap) {
                        if (window.Platform && window.Platform.toast) {
                            window.Platform.toast.error('Could not find capability "' + suggestion.capability + '".');
                        }
                        return;
                    }
                    if (!this.capabilities.some(function (c) { return c.id === cap.id; })) {
                        this.capabilities.push(cap);
                    }
                    // Reuse the exact same write path as a manual cell edit
                    // (saveCell above) — the AI suggestion never writes on
                    // its own, only this click does.
                    //
                    // support_level: 3, capability_contribution: 50,
                    // impact_level/stage_criticality: 'medium' are not
                    // invented by this code path: they are the same
                    // shared "unset" defaults the manual add-capability
                    // form starts from (openCellModal above) and that
                    // upsert_mapping_cell() (value_stream_service.py)
                    // substitutes server-side whenever these fields are
                    // omitted, mirroring the CapabilityValueStreamMapping
                    // column defaults in app/models/unified_capability.py.
                    // The AI only chose the capability + stage; the
                    // strength/impact fields are left at the system
                    // default until a human tunes them via the cell
                    // editor, same as any manually-added row.
                    var data = await Platform.fetch.post('/value-streams/api/mapping', {
                        capability_id: cap.id,
                        value_stream_id: this.valueStreamId,
                        value_stream_stage_id: stage.id,
                        support_type: 'primary',
                        support_level: 3,
                        capability_contribution: 50,
                        impact_level: 'medium',
                        stage_criticality: 'medium'
                    });
                    // Platform.fetch already threw on non‑ok, so data is the parsed success response.
                    this.cells[this.cellKey(cap.id, stage.id)] = {
                        mapping_id: data.mapping.id,
                        support_type: data.mapping.support_type,
                        support_level: data.mapping.support_level,
                        capability_contribution: data.mapping.capability_contribution,
                        impact_level: data.mapping.impact_level,
                        stage_criticality: data.mapping.stage_criticality
                    };
                    suggestion.applied = true;
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.success('Mapping applied.');
                    }
                } catch (err) {
                    // Platform.fetch already shows a toast unless silent:true.
                    // We keep the existing toast call for consistency, but the error is already visible.
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Failed to apply mapping: ' + err.message);
                    }
                } finally {
                    this.aiApplyingIndex = null;
                }
            }
        };
    });
    }

    if (window.Alpine) {
        registerBizbokGrid();
    } else {
        document.addEventListener('alpine:init', registerBizbokGrid, { once: true });
    }
}());
