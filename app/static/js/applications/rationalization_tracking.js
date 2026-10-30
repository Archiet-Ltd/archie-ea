/**
 * RATX-006: Rationalization Tracking Alpine component
 *
 * Portfolio-level outcomes: dependency risk table, retirement sequence.
 */
'use strict';

function trackingApp() {
    const baseUrl = '/applications/rationalization/api';

    return {
        /* ── Dependency Risk state ────────────────────── */
        depResults: [],
        depLoading: false,
        depPage: 1,
        depTotalPages: 1,
        depRiskFilter: '',

        /* ── Retirement Sequence state ────────────────── */
        retWaves: [],
        retLoading: false,

        /* ── RATA-012: Import state ─────────────────── */
        showImportModal: false,
        importFile: null,
        importPreview: [],
        importLoading: false,
        importResult: null,

        /* ── Load dependency risk ─────────────────────── */
        loadDependencyRisk: function() {
            const self = this;
            self.depLoading = true;

            const params = {
                page: self.depPage,
                per_page: 25
            };
            if (self.depRiskFilter) params.risk_level = self.depRiskFilter;

            Platform.fetch.get(baseUrl + '/portfolio-dependencies', params, { silent: true })
                .then(function(data) {
                    self.depResults = (data.apps || data.applications || data.dependencies || []).map(function(d) {
                        return {
                            id: d.app_id || d.application_id || d.id,
                            name: d.app_name || d.application_name || d.name || 'Unknown',
                            blocker_count: d.blocker_count || d.total_blockers || 0,
                            critical_count: d.critical_blocker_count || d.critical_blockers || 0,
                            downstream_count: d.downstream_count || d.downstream_apps || 0,
                            risk_level: d.risk_level || 'unknown'
                        };
                    });
                    self.depTotalPages = data.total_pages || Math.ceil((data.total || self.depResults.length) / 25) || 1;
                    self.depLoading = false;
                })
                .catch(function(err) {
                    // RATX-006: Dependency risk load failed; the UI will show empty results.
                    // The global toast is suppressed via silent:true because this component
                    // paints its own inline error state (empty table).
                    self.depLoading = false;
                    self.depResults = [];
                    // Do NOT swallow the error: rethrow to propagate to the global error handler.
                    throw err;
                });
        },

        /* ── Load benefits (placeholder for future) ───── */
        loadBenefits: function() {
            /* Benefits data comes from server-side context (financial_summary).
               Future enhancement: load per-app benefits table from API. */
        },

        /* ── RATA-012: Handle import file ───────────── */
        handleImportFile: function(event) {
            const self = this;
            const file = event.target.files[0];
            if (!file) return;
            self.importFile = file;

            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const text = e.target.result;
                    let data;
                    if (file.name.endsWith('.json')) {
                        const parsed = JSON.parse(text);
                        data = parsed.dependencies || parsed;
                    } else {
                        // CSV parsing (simple)
                        const lines = text.split('\n').filter(function(l) { return l.trim(); });
                        if (lines.length < 2) { self.importPreview = []; return; }
                        const headers = lines[0].split(',').map(function(h) { return h.trim().replace(/"/g, ''); });
                        data = [];
                        for (let i = 1; i < lines.length; i++) {
                            const vals = lines[i].split(',').map(function(v) { return v.trim().replace(/"/g, ''); });
                            const obj = {};
                            headers.forEach(function(h, idx) { obj[h] = vals[idx] || ''; });
                            obj.source_app_id = parseInt(obj.source_app_id) || 0;
                            obj.target_app_id = parseInt(obj.target_app_id) || 0;
                            data.push(obj);
                        }
                    }
                    self.importPreview = Array.isArray(data) ? data.slice(0, 10) : [];
                } catch (err) {
                    self.importPreview = [];
                    Platform.toast.error('Could not read that file: ' + (err.message || 'unrecognised format'));
                }
            };
            reader.readAsText(file);
        },

        submitImport: function() {
            const self = this;
            self.importLoading = true;

            if (self.importFile && self.importFile.name.endsWith('.csv')) {
                const formData = new FormData();
                formData.append('file', self.importFile);
                // raw-fetch-ok: FormData with file upload requires manual Content-Type header (multipart/form-data)
                // Platform.fetch would automatically set Content-Type to application/json for plain objects,
                // which would break the file upload. We keep raw fetch but remove manual CSRF token handling.
                // However, Platform.fetch can handle FormData correctly (it does not auto‑serialise plain objects).
                // We can use Platform.fetch.post with the FormData body; CSRF token will be injected automatically.
                Platform.fetch.post('/applications/rationalization/api/dependencies/import', formData, { silent: true })
                    .then(function(data) { self.importResult = data; self.importLoading = false; })
                    .catch(function(err) {
                        // RATX-006: Import failed; the UI will show the error in importResult.
                        // The global toast is suppressed via silent:true because this component
                        // paints its own inline error state.
                        self.importResult = { success: false, error: err.message };
                        self.importLoading = false;
                        // Do NOT swallow the error: rethrow to propagate to the global error handler.
                        throw err;
                    });
            } else {
                // JSON import — reconstruct full array from preview source
                const reader = new FileReader();
                reader.onload = function(e) {
                    try {
                        const parsed = JSON.parse(e.target.result);
                        const deps = parsed.dependencies || parsed;
                        Platform.fetch.post('/applications/rationalization/api/dependencies/import', { dependencies: deps }, { silent: true })
                            .then(function(data) { self.importResult = data; self.importLoading = false; })
                            .catch(function(err) {
                                // RATX-006: Import failed; the UI will show the error in importResult.
                                // The global toast is suppressed via silent:true because this component
                                // paints its own inline error state.
                                self.importResult = { success: false, error: err.message };
                                self.importLoading = false;
                                // Do NOT swallow the error: rethrow to propagate to the global error handler.
                                throw err;
                            });
                    } catch (err) {
                        // JSON parsing error before the request; this is a client-side error.
                        // We must surface it to the user via importResult, not via a toast.
                        self.importResult = { success: false, error: 'Invalid JSON' };
                        self.importLoading = false;
                        // Rethrow to propagate to the global error handler.
                        throw err;
                    }
                };
                reader.readAsText(self.importFile);
            }
        },

        /* ── Load retirement sequence ─────────────────── */
        loadRetirementSequence: function() {
            const self = this;
            self.retLoading = true;

            Platform.fetch.get(baseUrl + '/retirement-sequence', null, { silent: true })
                .then(function(data) {
                    self.retWaves = (data.waves || data.sequence || []).map(function(wave) {
                        return {
                            apps: (wave.applications || wave.apps || []).map(function(a) {
                                return { id: a.id || a.application_id, name: a.name || a.application_name || 'Unknown' };
                            })
                        };
                    });
                    self.retLoading = false;
                })
                .catch(function(err) {
                    // RATX-006: Retirement sequence load failed; the UI will show empty waves.
                    // The global toast is suppressed via silent:true because this component
                    // paints its own inline error state (empty list).
                    self.retLoading = false;
                    self.retWaves = [];
                    // Do NOT swallow the error: rethrow to propagate to the global error handler.
                    throw err;
                });
        }
    };
}
