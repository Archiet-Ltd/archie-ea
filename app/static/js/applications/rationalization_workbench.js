/**
 * RATX-004: Rationalization Workbench Alpine component
 *
 * Filter, paginate, select, and bulk-action scored applications.
 */
'use strict';

/**
 * Coerce a score the server sent into a number, or null when it sent nothing.
 *
 * `|| 0` used to stand in here, which turned an absent dimension into a
 * measured zero — the worst possible score, plotted next to real applications
 * and impossible to tell apart from one. null instead: the template renders an
 * em dash and Chart.js skips the point rather than pulling the polygon to the
 * centre.
 */
function workbenchScore(value) {
    return (value === null || value === undefined || value === '' || isNaN(Number(value)))
        ? null
        : Math.round(Number(value));
}

function workbenchApp() {
    let csrfToken = document.querySelector('meta[name="csrf-token"]');
    csrfToken = csrfToken ? csrfToken.getAttribute('content') : '';

    return {
        /* ── State ────────────────────────────────────── */
        results: [],
        totalCount: 0,
        totalPages: 1,
        page: 1,
        perPage: 200,
        loading: false,
        error: false,

        filters: {
            search: '',
            readiness: '',
            disposition: '',
            reviewStatus: '',
            businessUnit: '',
            lifecycle: '',
            sort: 'health_desc',
            includeUnscored: false
        },

        facets: {
            dispositions: [],
            business_units: [],
            lifecycle_statuses: {}
        },

        selectedIds: {},

        get selectedCount() {
            return Object.keys(this.selectedIds || {}).filter(function(k) { return this.selectedIds[k]; }.bind(this)).length;
        },

        get allSelected() {
            const self = this;
            return self.results.length > 0 && self.results.every(function(r) { return self.selectedIds[r.id]; });
        },

        /* ── Load workbench data ──────────────────────── */
        load: function() {
            const self = this;
            self.loading = true;
            self.error = false;

            const params = new URLSearchParams();
            params.set('page', self.page);
            params.set('per_page', self.perPage);
            if (self.filters.search) params.set('search', self.filters.search);
            if (self.filters.readiness) params.set('readiness', self.filters.readiness);
            if (self.filters.disposition) params.set('disposition', self.filters.disposition);
            if (self.filters.reviewStatus) params.set('review_status', self.filters.reviewStatus);
            if (self.filters.businessUnit) params.set('business_unit', self.filters.businessUnit);
            if (self.filters.lifecycle) params.set('lifecycle', self.filters.lifecycle);
            if (self.filters.includeUnscored) params.set('include_unscored', 'true');
            /* Map JS sort value to API sort_by + sort_dir params */
            if (self.filters.sort) {
                const sortParts = self.filters.sort.match(/^(.+?)_(asc|desc)$/);
                if (sortParts) {
                    params.set('sort_by', sortParts[1]);
                    params.set('sort_dir', sortParts[2]);
                } else {
                    params.set('sort_by', self.filters.sort);
                }
            }

            // Use Platform.fetch.get for automatic query‑param handling and error handling.
            // The component already paints its own inline error state (self.error), so we
            // suppress the global toast with { silent: true } to avoid duplicate messages.
            Platform.fetch.get('/applications/rationalization/api/portfolio-workbench', Object.fromEntries(params), { silent: true })
                .then(function(data) {
                    const dispDefinitions = {
                        'retain': 'Keep as-is, no action needed',
                        'rehost': 'Move to new infrastructure without code changes',
                        'replatform': 'Move with minor optimizations',
                        'refactor': 'Re-architect for cloud-native or modernization',
                        'replace': 'Substitute with a different product or service',
                        'consolidate': 'Merge functionality into another application',
                        'retire': 'Decommission and remove from portfolio',
                        'insufficient_evidence': 'Not enough data for a recommendation'
                    };

                    self.results = (data.applications || data.results || []).map(function(app) {
                        const dispKey = (app.disposition_action || '').toLowerCase();
                        let dispLabel = dispKey.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
                        let dispDesc = dispDefinitions[dispKey] || '';
                        const confValue = app.confidence_score || app.disposition_confidence || null;

                        /* RAT-001: Show actionable label when data is missing */
                        if (dispKey === 'insufficient_evidence' && (!confValue || confValue === 'none')) {
                            dispLabel = 'Needs Data Enrichment';
                            dispDesc = 'Enrich this application with cost, owner, and criticality data to enable scoring';
                        }

                        return {
                            id: app.id || app.app_id || app.application_id,
                            name: app.name || app.app_name || app.application_name || 'Unknown',
                            health: app.overall_health_score || 0,
                            disposition_label: app.disposition_label || dispLabel || '—',
                            disposition_desc: dispDesc,
                            is_ready: app.is_decision_ready || false,
                            review_status: app.review_status || 'draft',
                            confidence: confValue,
                            business_unit: app.business_unit || null
                        };
                    });

                    self.totalCount = data.total || self.results.length;
                    self.totalPages = Math.ceil(self.totalCount / self.perPage) || 1;

                    if (data.facets) {
                        /* API returns dispositions as {key: count} dict — convert to array */
                        const rawDisp = data.facets.dispositions || {};
                        if (Array.isArray(rawDisp)) {
                            self.facets.dispositions = rawDisp;
                        } else {
                            self.facets.dispositions = Object.keys(rawDisp).map(function(k) {
                                return { value: k, label: k.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); }), count: rawDisp[k] };
                            });
                        }
                        const rawBu = data.facets.business_units || {};
                        if (Array.isArray(rawBu)) {
                            self.facets.business_units = rawBu;
                        } else {
                            self.facets.business_units = Object.keys(rawBu).map(function(k) {
                                return { value: k, label: k, count: rawBu[k] };
                            });
                        }
                        self.facets.lifecycle_statuses = data.facets.lifecycle_statuses || {};
                    }

                    self.loading = false;

                    /* Re-initialize Lucide icons for dynamically rendered content */
                    if (window.lucide) {
                        self.$nextTick(function() { window.lucide.createIcons(); });
                    }
                })
                .catch(function(err) {
                    // Platform.fetch already logged the error internally; we must not
                    // call console.error. Surface the failure to the user via the
                    // component's inline error state.
                    self.loading = false;
                    self.error = true;
                });
        },

        /* ── Selection ────────────────────────────────── */
        toggleSelect: function(id) {
            if (this.selectedIds[id]) {
                delete this.selectedIds[id];
            } else {
                this.selectedIds[id] = true;
            }
        },

        toggleAll: function(checked) {
            const self = this;
            if (checked) {
                self.results.forEach(function(r) { self.selectedIds[r.id] = true; });
            } else {
                self.selectedIds = {};
            }
        },

        /* ── Bulk actions ─────────────────────────────── */
        bulkDisposition: '',
        bulkAction: async function(action) {
            const self = this;
            const ids = Object.keys(self.selectedIds).filter(function(k) { return self.selectedIds[k]; }).map(Number);
            if (ids.length === 0) return;

            const labels = { approve: 'Approve', defer: 'Defer', request_data: 'Request Data for', set_disposition: 'Set disposition for' };
            let confirmMsg = (labels[action] || action) + ' ' + ids.length + ' application(s)?';
            if (action === 'set_disposition') {
                if (!self.bulkDisposition) { Platform.toast.warning('Select a disposition first.'); return; }
                confirmMsg = 'Set disposition to "' + self.bulkDisposition + '" for ' + ids.length + ' application(s)?';
            }
            if (!(await Platform.modal.confirm(confirmMsg))) return;

            const payload = { action: action, app_ids: ids };
            if (action === 'set_disposition') payload.disposition = self.bulkDisposition;

            // Platform.fetch.post automatically injects CSRF token and serialises
            // the plain‑object payload to JSON. No manual headers needed.
            Platform.fetch.post('/applications/rationalization/api/bulk-review', payload)
                .then(function(data) {
                    if (data.success) {
                        self.selectedIds = {};
                        self.bulkDisposition = '';
                        self.load();
                    } else {
                        Platform.toast.error('Bulk action failed: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(function(err) {
                    // Platform.fetch already shows a user‑visible toast for the error,
                    // and logs internally. No console call allowed.
                    // The component does not have an inline error state for this action,
                    // so we rely on the global toast (which Platform.fetch already displayed).
                    // No fallback value is introduced.
                });
        },

        /* ── RATA-019: App comparison ────────────────── */
        compareIds: [],
        compareApps: [],
        comparisonRadarChart: null,

        toggleCompare: function(app) {
            const self = this;
            const idx = self.compareIds.indexOf(app.id);
            if (idx >= 0) {
                self.compareIds.splice(idx, 1);
                self.compareApps = self.compareApps.filter(function(ca) { return ca.id !== app.id; });
            } else {
                if (self.compareIds.length >= 4) {
                    Platform.toast.info('Maximum 4 applications can be compared at once.');
                    return;
                }
                self.compareIds.push(app.id);
                // Fetch evidence trail for this app using Platform.fetch.
                // The component paints its own error toast, so we pass { silent: true }
                // to avoid duplicate global toasts.
                Platform.fetch.get('/applications/rationalization/api/evidence-trail/' + app.id, null, { silent: true })
                    .then(function(data) {
                        const scores = data.scores || {};
                        self.compareApps.push({
                            id: app.id,
                            name: app.name,
                            dims: {
                                'Technical Health': workbenchScore(scores.technical_health),
                                'Business Value': workbenchScore(scores.business_value),
                                'Cost Efficiency': workbenchScore(scores.cost_efficiency),
                                'Vendor Risk': workbenchScore(scores.vendor_risk)
                            }
                        });
                        if (self.compareApps.length >= 2) {
                            self.$nextTick(function() { self.renderComparisonChart(); });
                        }
                    })
                    .catch(function(err) {
                        // Drop the selection rather than charting an application whose
                        // scores we do not have.
                        const pending = self.compareIds.indexOf(app.id);
                        if (pending >= 0) { self.compareIds.splice(pending, 1); }
                        Platform.toast.error('Could not load scores for ' + (app.name || 'this application') + ': ' + (err.message || 'request failed') + '. It was left out of the comparison rather than plotted at zero.');
                    });
            }
            if (self.compareApps.length >= 2) {
                self.$nextTick(function() { self.renderComparisonChart(); });
            }
        },

        clearComparison: function() {
            this.compareIds = [];
            this.compareApps = [];
            if (this.comparisonRadarChart) { this.comparisonRadarChart.destroy(); this.comparisonRadarChart = null; }
        },

        renderComparisonChart: function() {
            const self = this;
            if (self.compareApps.length < 2 || typeof Chart === 'undefined') return;
            const ctx = document.getElementById('comparisonRadarChart');
            if (!ctx) return;
            if (self.comparisonRadarChart) self.comparisonRadarChart.destroy();

            const colors = [
                { bg: 'rgba(59, 130, 246, 0.15)', border: 'rgba(59, 130, 246, 0.8)' },
                { bg: 'rgba(16, 185, 129, 0.15)', border: 'rgba(16, 185, 129, 0.8)' },
                { bg: 'rgba(245, 158, 11, 0.15)', border: 'rgba(245, 158, 11, 0.8)' },
                { bg: 'rgba(239, 68, 68, 0.15)', border: 'rgba(239, 68, 68, 0.8)' }
            ];
            const labels = ['Technical Health', 'Business Value', 'Cost Efficiency', 'Vendor Risk'];
            const datasets = self.compareApps.map(function(ca, i) {
                const c = colors[i % colors.length];
                return {
                    label: ca.name,
                    // null, not 0: Chart.js skips an unknown dimension rather than
                    // plotting it at the origin as if it had been measured.
                    data: labels.map(function(l) { return ca.dims[l] != null ? ca.dims[l] : null; }),
                    backgroundColor: c.bg,
                    borderColor: c.border,
                    borderWidth: 2,
                    pointRadius: 3
                };
            });

            self.comparisonRadarChart = new Chart(ctx, {
                type: 'radar',
                data: { labels: labels, datasets: datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    scales: { r: { min: 0, max: 100, ticks: { stepSize: 25, display: false }, grid: { color: 'rgba(128,128,128,0.15)' } } },
                    plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } } }
                }
            });
        },

        /* ── RATA-008: Score breakdown slide-over ──────── */
        breakdownOpen: false,
        breakdownLoading: false,
        breakdownData: null,
        breakdownAppName: null,
        breakdownRadarChart: null,

        openBreakdown: function(appId) {
            const self = this;
            const app = self.results.find(function(r) { return r.id === appId; });
            self.breakdownAppName = app ? app.name : 'Application';
            self.breakdownOpen = true;
            self.breakdownLoading = true;
            self.breakdownData = null;

            // Use Platform.fetch.get for automatic error handling.
            // The component paints its own error toast, so we pass { silent: true } to avoid duplicate global toasts.
            Platform.fetch.get('/applications/rationalization/api/evidence-trail/' + appId, null, { silent: true })
                .then(function(data) {
                    const scores = data.scores || {};
                    const dims = [
                        { name: 'technical', label: 'Technical Health', score: workbenchScore(scores.technical_health) },
                        { name: 'business', label: 'Business Value', score: workbenchScore(scores.business_value) },
                        { name: 'cost', label: 'Cost Efficiency', score: workbenchScore(scores.cost_efficiency) },
                        { name: 'vendor', label: 'Vendor Risk', score: workbenchScore(scores.vendor_risk) }
                    ];

                    // Extract top 3 factors per dimension
                    const topFactors = (data.evidence_trail || []).map(function(dim) {
                        const factors = (dim.sub_factors || dim.factors || [])
                            .sort(function(a, b) { return Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0); })
                            .slice(0, 3)
                            .map(function(f) {
                                return {
                                    name: f.factor || f.name || '—',
                                    raw_value: f.raw_value,
                                    contribution: f.contribution != null ? f.contribution : (f.points != null ? f.points : null)
                                };
                            });
                        return { dimension: dim.dimension || dim.name || '—', factors: factors };
                    });

                    self.breakdownData = {
                        dimensions: dims,
                        top_factors: topFactors,
                        inputs_measured: data.inputs_measured,
                        inputs_total: data.inputs_total,
                        overall_statement: data.overall_statement || ''
                    };
                    self.breakdownLoading = false;

                    // Render mini radar
                    self.$nextTick(function() {
                        const ctx = document.getElementById('breakdownRadarChart');
                        if (!ctx || typeof Chart === 'undefined') return;
                        if (self.breakdownRadarChart) self.breakdownRadarChart.destroy();
                        self.breakdownRadarChart = new Chart(ctx, {
                            type: 'radar',
                            data: {
                                labels: dims.map(function(d) { return d.label; }),
                                datasets: [{
                                    data: dims.map(function(d) { return d.score; }),
                                    backgroundColor: 'rgba(59, 130, 246, 0.15)',
                                    borderColor: 'rgba(59, 130, 246, 0.8)',
                                    borderWidth: 2,
                                    pointBackgroundColor: 'rgba(59, 130, 246, 1)',
                                    pointRadius: 3
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: true,
                                scales: { r: { min: 0, max: 100, ticks: { stepSize: 25, display: false }, grid: { color: 'rgba(128,128,128,0.15)' } } },
                                plugins: { legend: { display: false } }
                            }
                        });
                    });
                })
                .catch(function(err) {
                    // breakdownData stays null so the panel renders nothing rather than
                    // a zeroed radar the user would read as a measured score.
                    self.breakdownData = null;
                    self.breakdownLoading = false;
                    self.breakdownOpen = false;
                    Platform.toast.error('Could not load the score breakdown for ' + (self.breakdownAppName || 'this application') + ': ' + (err.message || 'request failed') + '.');
                });
        },

        /* ── Navigation ───────────────────────────────── */
        goToPlanning: function(appId) {
            window.location.href = '/applications/rationalization/planning/' + appId;
        }
    };
}
