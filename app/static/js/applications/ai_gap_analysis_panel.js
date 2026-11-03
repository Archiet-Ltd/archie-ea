/**
 * AI Gap Analysis panel — rationalization dashboard.
 *
 * Lazy-loads the four existing, previously uncalled GET endpoints on
 * app/modules/ai_chat/routes/ai_gap_detection_routes.py
 * (blueprint url_prefix="/api/ai-gap-detection"): /summary, /critical-gaps,
 * /rationalization, /vendor-lifecycle. Each section fetches its data the
 * first time it is opened, not on page load.
 */
'use strict';

function aiGapAnalysisPanel() {
    return {
        /* ── Tabs ──────────────────────────────────────────────── */
        tabs: [
            { key: 'summary', label: 'Summary' },
            { key: 'critical-gaps', label: 'Critical Gaps' },
            { key: 'rationalization', label: 'Rationalization Opportunities' },
            { key: 'vendor-lifecycle', label: 'Vendor Lifecycle Risks' }
        ],
        activeTab: 'summary',

        /* ── Per-section load state ───────────────────────────── */
        sections: {
            summary: { loaded: false, error: null, data: {} },
            criticalGaps: { loaded: false, error: null, data: [] },
            rationalization: { loaded: false, error: null, data: [] },
            vendorLifecycle: { loaded: false, error: null, data: [] }
        },

        /* ── Endpoint URLs — read from the panel's own data attrs ── */
        urls: {},

        init: function() {
            const root = this.$el;
            this.urls = {
                summary: root.dataset.summaryUrl,
                criticalGaps: root.dataset.criticalGapsUrl,
                rationalization: root.dataset.rationalizationUrl,
                vendorLifecycle: root.dataset.vendorLifecycleUrl
            };
            this.loadSection('summary');
        },

        openTab: function(key) {
            this.activeTab = key;
            const sectionKey = key === 'critical-gaps' ? 'criticalGaps'
                : key === 'vendor-lifecycle' ? 'vendorLifecycle'
                : key;
            if (!this.sections[sectionKey].loaded && !this.sections[sectionKey].error) {
                this.loadSection(sectionKey);
            }
        },

        loadSection: function(sectionKey) {
            const self = this;
            const section = self.sections[sectionKey];
            section.error = null;

            Platform.fetch(self.urls[sectionKey], {
                headers: { 'Accept': 'application/json' },
                silent: true
            })
                .then(function(json) {
                    const payload = json.data !== undefined ? json.data : json;
                    if (sectionKey === 'summary') {
                        section.data = payload || {};
                    } else {
                        section.data = Array.isArray(payload) ? payload : [];
                    }
                    section.loaded = true;
                })
                .catch(function() {
                    section.error = 'Failed to load. Please retry.';
                    section.loaded = false;
                });
        },

        fmt: function(value) {
            return (value === null || value === undefined) ? '—' : value;
        },

        /* Currency formatting — window.currencyManager per CLAUDE.md, with the
         * same fallback precedent as app/static/js/applications/rationalization.js */
        formatCurrency: function(amount) {
            if (amount === null || amount === undefined || amount === '') return Platform.figure.NOT_RECORDED;
            if (window.currencyManager) return window.currencyManager.format(amount);
            return '£' + Number(amount).toLocaleString();
        }
    };
}
