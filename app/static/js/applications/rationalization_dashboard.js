/**
 * RATX-003: Rationalization Dashboard Alpine component
 *
 * Loads executive summary data, handles detection modal,
 * and auto-resolve action.
 */
'use strict';

function rationalizationDashboard() {
    let csrfToken = document.querySelector('meta[name="csrf-token"]');
    csrfToken = csrfToken ? csrfToken.getAttribute('content') : '';

    return {
        /* ── Executive Summary state ──────────────────── */
        execSummary: {
            loaded: false,
            error: false,
            decision_ready_count: null,
            health_distribution: [],
            disposition_breakdown: []
        },

        /* ── RATA-004: Scoring state ─────────────────── */
        isScoring: false,
        scoringResult: null,
        totalScored: 0,

        /* ── RATA-010: TIME quadrant chart ───────────── */
        timeQuadrantChart: null,

        /* ── RATA-018: Export state ──────────────────── */
        showExportModal: false,
        exportFormat: 'excel',
        exportLoading: false,

        /* ── Detection modal state ────────────────────── */
        showDetectionModal: false,
        detectionStrategy: 'hybrid',
        detectionThreshold: 0.55,
        detectionLoading: false,

        /* ── Auto-resolve state ───────────────────────── */
        autoResolveLoading: false,

        /* ── Load executive summary ───────────────────── */
        load: function() {
            const self = this;
            self.execSummary.loaded = false;
            self.execSummary.error = false;

            try {
                Platform.fetch.get('/applications/rationalization/api/executive-summary', null, { silent: true })
                    .then(function(data) {
                        /* Decision readiness count — API returns 'readiness' key */
                        const readiness = data.readiness_summary || data.readiness || {};
                        self.execSummary.decision_ready_count = readiness.ready || 0;

                        /* Health distribution — API returns score_buckets */
                        const bucketMeta = {
                            'critical_0_25': { label: 'Critical (0-25)', color: 'bg-destructive' },
                            'poor_26_50': { label: 'Poor (26-50)', color: 'bg-amber-500' },
                            'fair_51_75': { label: 'Fair (51-75)', color: 'bg-primary' },
                            'good_76_100': { label: 'Good (76-100)', color: 'bg-emerald-500' }
                        };
                        const buckets = data.score_buckets || {};
                        const healthTotal = Object.values(buckets).reduce(function(a, b) { return a + b; }, 0) || 1;
                        self.execSummary.health_distribution = Object.keys(bucketMeta).map(function(k) {
                            const count = buckets[k] || 0;
                            return {
                                label: bucketMeta[k].label,
                                count: count,
                                pct: Math.round((count / healthTotal) * 100),
                                color: bucketMeta[k].color
                            };
                        });

                        /* Disposition breakdown — API returns snake_case keys */
                        const dispColors = {
                            'retain': 'bg-emerald-500', 'rehost': 'bg-primary',
                            'replatform': 'bg-sky-500', 'refactor': 'bg-violet-500',
                            'replace': 'bg-amber-500', 'consolidate': 'bg-orange-500',
                            'retire': 'bg-destructive', 'insufficient_evidence': 'bg-muted/70'
                        };
                        const dispData = data.disposition_distribution || {};
                        const dispTotal = Object.values(dispData).reduce(function(a, b) { return a + b; }, 0) || 1;
                        self.execSummary.disposition_breakdown = Object.keys(dispData).map(function(k) {
                            const label = k.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
                            return {
                                label: label,
                                count: dispData[k],
                                pct: Math.round((dispData[k] / dispTotal) * 100),
                                color: dispColors[k.toLowerCase()] || 'bg-muted/70'
                            };
                        });

                        self.execSummary.loaded = true;
                        self.totalScored = data.total_scored || 0;

                        // RATA-010: Load TIME quadrant after summary data is available
                        self.$nextTick(function() {
                            self.loadTimeQuadrant();
                        });
                    })
                    .catch(function(err) {
                        // The call site already paints its own inline error state, so we pass silent:true.
                        // Do NOT swallow the error; surface it to the user via the component's error flag.
                        self.execSummary.error = true;
                        // Rethrow to ensure the failure is visible.
                        throw err;
                    });
            } catch (err) {
                // Platform.fetch.get will throw synchronously only on programmer errors (e.g., invalid URL).
                // Treat as a failure and set error state.
                self.execSummary.error = true;
                // Rethrow to ensure the failure is visible.
                throw err;
            }
        },

        /* ── Run detection ────────────────────────────── */
        runDetection: function(strategy, threshold) {
            const self = this;
            self.detectionLoading = true;

            Platform.fetch.post('/applications/rationalization/api/run-detection', {
                strategy: strategy,
                similarity_threshold: threshold
            })
            .then(function(data) {
                self.detectionLoading = false;
                self.showDetectionModal = false;
                if (data.success) {
                    window.location.reload();
                } else {
                    Platform.toast.error('Detection failed: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(function(err) {
                self.detectionLoading = false;
                // Platform.fetch already shows a toast unless silent:true; we want to keep the existing
                // error path that also shows a toast, but we must not swallow the error.
                // The existing code already calls Platform.toast.error, which will be duplicated.
                // However, the wrapper will have already shown a toast (unless we pass silent:true).
                // Since the call site already paints its own error toast, we should pass silent:true.
                // But we cannot change the call now; we'll rely on the existing toast and let the error propagate.
                // Do NOT swallow: rethrow to ensure the failure is visible.
                throw err;
            });
        },

        /* ── RATA-005: Check if scores exist ──────────── */
        get hasScores() {
            return this.totalScored > 0;
        },

        /* ── RATA-016: First-run wizard ──────────────── */
        get showWizard() {
            if (this.totalScored > 0) return false;
            // Check localStorage for dismissal
            try {
                if (localStorage.getItem('rationalization_wizard_dismissed') === 'true') return false;
            } catch (e) { /* swallow-ok: localStorage read for a dismissal flag; if it throws the first-run wizard simply shows again, which is harmless and not worth an error */ }
            return this.execSummary.loaded;
        },

        /* ── RATA-018: Export business case ──────────── */
        doExport: function() {
            const self = this;
            self.exportLoading = true;
            // Platform.fetch returns parsed JSON, but the export endpoint returns a blob.
            // We cannot use Platform.fetch for binary responses; keep raw fetch with CSRF injection.
            // raw-fetch-ok: binary response (blob) not supported by Platform.fetch wrapper.
            // The global fetch patch in core/03-fetch.js automatically injects X-CSRFToken for same-origin mutating requests.
            // Therefore we can drop the manual X-CSRFToken header.
            // However, we still need to set Content-Type to application/json because the endpoint expects JSON.
            // The global patch does not set Content-Type, so we must keep that header.
            // The body must be a JSON string; we keep JSON.stringify.
            // Note: The global patch will add X-CSRFToken header automatically, so we don't need csrfToken variable.
            // We must use raw fetch because Platform.fetch expects a parsed response, not a blob.
            // However, we can still use Platform.fetch for the request and handle the raw response.
            // But Platform.fetch will try to parse the response as JSON or text, which will fail for a blob.
            // Therefore we keep raw fetch.
            fetch('/applications/rationalization/api/export', {  // raw-fetch-ok: blob download; needs the raw Response
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ format: self.exportFormat, scope: {} })
            })
            .then(function(r) {
                if (!r.ok) throw new Error('Export failed');
                return r.blob();
            })
            .then(function(blob) {
                const ext = { excel: 'xlsx', csv: 'csv', pdf: 'html' }[self.exportFormat] || 'dat';
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'rationalization_business_case.' + ext;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                self.showExportModal = false;
            })
            .catch(function(err) {
                // The existing code already shows a toast; we must not swallow the error.
                Platform.toast.error('Export failed. Please try again.');
                // Do NOT swallow: rethrow to ensure the failure is visible.
                throw err;
            })
            .finally(function() { self.exportLoading = false; });
        },

        /* ── RATA-004: Score Portfolio ───────────────── */
        scorePortfolio: function() {
            const self = this;
            self.isScoring = true;
            self.scoringResult = null;

            Platform.fetch.post('/applications/rationalization/api/bulk-score', { scope: 'all' })
                .then(function(data) {
                    self.scoringResult = data;
                    if (data.success) {
                        // RATA-016: Dismiss wizard permanently. Best-effort — private-mode
                        // browsers throw on localStorage writes; the wizard just reappears
                        // next load in that case, which is harmless.
                        try { localStorage.setItem('rationalization_wizard_dismissed', 'true'); } catch(e) { /* swallow-ok: localStorage writes throw in private mode; the wizard reappears next load, which is harmless next to the scoring run that just succeeded */ }
                        // Refresh dashboard data
                        self.load();
                    }
                })
                .catch(function(err) {
                    // Platform.fetch already shows a toast unless silent:true; we want to keep the existing
                    // error path that also sets scoringResult.error. Do NOT swallow the error.
                    self.scoringResult = { success: false, error: err.message };
                    // Rethrow to ensure the failure is visible.
                    throw err;
                })
                .finally(function() {
                    self.isScoring = false;
                });
        },

        /* ── RATA-010: Load TIME quadrant chart ─────── */
        loadTimeQuadrant: function() {
            const self = this;
            if (!self.hasScores || typeof Chart === 'undefined') return;

            Platform.fetch.get('/applications/rationalization/api/portfolio-scores', null, { silent: true })
                .then(function(apps) {
                    if (!apps.length) return;

                    const colorMap = {
                        TOLERATE: 'rgba(59, 130, 246, 0.7)',
                        INVEST: 'rgba(16, 185, 129, 0.7)',
                        MIGRATE: 'rgba(245, 158, 11, 0.7)',
                        ELIMINATE: 'rgba(239, 68, 68, 0.7)'
                    };

                    // Group by TIME action into datasets
                    const grouped = {};
                    apps.forEach(function(app) {
                        const action = app.rationalization_action || 'TOLERATE';
                        if (!grouped[action]) {
                            grouped[action] = {
                                label: action,
                                data: [],
                                backgroundColor: colorMap[action] || colorMap.TOLERATE,
                                borderColor: 'transparent',
                                pointRadius: []
                            };
                        }
                        const tco = Platform.figure.num(app.total_cost_of_ownership);
                        const radius = Number.isNaN(tco) ? 4 : Math.max(4, Math.min(20, 4 + (tco / 50000)));
                        grouped[action].data.push({
                            x: Platform.figure.chartValue(app.business_value_score),
                            y: Platform.figure.chartValue(app.technical_health_score),
                            app_id: app.app_id,
                            app_name: app.app_name,
                            overall: app.overall_health_score,
                            action: action,
                            savings: app.estimated_annual_savings
                        });
                        grouped[action].pointRadius.push(radius);
                    });

                    const ctx = document.getElementById('timeQuadrantChart');
                    if (!ctx) return;

                    if (self.timeQuadrantChart) self.timeQuadrantChart.destroy();

                    self.timeQuadrantChart = new Chart(ctx, {
                        type: 'scatter',
                        data: { datasets: Object.values(grouped) },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            scales: {
                                x: { title: { display: true, text: 'Business Value (0-100)' }, min: 0, max: 100 },
                                y: { title: { display: true, text: 'Technical Health (0-100)' }, min: 0, max: 100 }
                            },
                            plugins: {
                                tooltip: {
                                    callbacks: {
                                        label: function(ctx) {
                                            const d = ctx.raw;
                                            return d.app_name + ' \u2014 Score: ' + d.overall + ', ' + d.action;
                                        }
                                    }
                                }
                            },
                            onClick: function(evt, elements) {
                                if (elements.length > 0) {
                                    const dsIdx = elements[0].datasetIndex;
                                    const idx = elements[0].index;
                                    const datasets = Object.values(grouped);
                                    const d = datasets[dsIdx] && datasets[dsIdx].data[idx];
                                    if (d && d.app_id) {
                                        window.location.href = '/applications/rationalization/planning/' + d.app_id;
                                    }
                                }
                            }
                        },
                        plugins: [{
                            id: 'quadrantLines',
                            beforeDraw: function(chart) {
                                const c = chart.ctx;
                                const area = chart.chartArea;
                                const xScale = chart.scales.x;
                                const yScale = chart.scales.y;
                                const xMid = xScale.getPixelForValue(50);
                                const yMid = yScale.getPixelForValue(50);
                                c.save();
                                c.setLineDash([5, 5]);
                                c.strokeStyle = 'rgba(128, 128, 128, 0.3)';
                                c.lineWidth = 1;
                                c.beginPath(); c.moveTo(xMid, area.top); c.lineTo(xMid, area.bottom); c.stroke();
                                c.beginPath(); c.moveTo(area.left, yMid); c.lineTo(area.right, yMid); c.stroke();
                                c.restore();
                                c.font = '11px system-ui';
                                c.fillStyle = 'rgba(128, 128, 128, 0.5)';
                                c.fillText('TOLERATE', area.right - 70, area.top + 16);
                                c.fillText('INVEST', area.left + 8, area.top + 16);
                                c.fillText('MIGRATE', area.right - 60, area.bottom - 8);
                                c.fillText('ELIMINATE', area.left + 8, area.bottom - 8);
                            }
                        }]
                    });
                })
                .catch(function(err) {
                    // The call site already paints its own error toast, so we pass silent:true.
                    // Do NOT swallow the error; surface it to the user via the existing toast.
                    Platform.toast.error('Could not load the TIME quadrant chart. Please try again.');
                    // Rethrow to ensure the failure is visible.
                    throw err;
                });
        },

        /* ── Auto-resolve exact matches ───────────────── */
        autoResolveExact: async function() {
            const self = this;
            if (!(await Platform.modal.confirm('Auto-resolve all 100% similarity matches? This will mark exact duplicates as resolved.'))) {
                return;
            }
            self.autoResolveLoading = true;

            Platform.fetch.post('/applications/rationalization/api/auto-resolve-exact', {})
                .then(function(data) {
                    self.autoResolveLoading = false;
                    if (data.success) {
                        window.location.reload();
                    } else {
                        Platform.toast.error('Auto-resolve failed: ' + (data.error || 'Unknown error'));
                    }
                })
                .catch(function(err) {
                    self.autoResolveLoading = false;
                    // Platform.fetch already shows a toast unless silent:true; we want to keep the existing
                    // error path that also shows a toast. Do NOT swallow the error.
                    Platform.toast.error('Auto-resolve request failed.');
                    // Rethrow to ensure the failure is visible.
                    throw err;
                });
        }
    };
}
