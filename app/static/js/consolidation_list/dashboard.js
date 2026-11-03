/**
 * Consolidation List Dashboard — Alpine.js component using shared dataTable mixin.
 *
 * Replaces ~790 lines of global-var DOM manipulation with Alpine reactive state.
 * Uses Platform.dataTable.mixin() for table state, selection, expansion, formatting.
 *
 * Requires: Platform.dataTable (components/data_table.js), Platform.fetch, Platform.toast,
 *           Platform.modal, window.currencyManager (currency.js)
 */
(function() {
'use strict';

Platform.require('fetch', 'toast', 'modal', 'dataTable');

// ── Status constants (consolidation pipeline) ────────────────────────
let STATUSES = [
    'identified', 'under_review', 'impact_assessed',
    'migration_planned', 'approved', 'in_progress', 'completed'
];
let STATUS_LABELS = {
    'identified': 'Identified', 'under_review': 'Under Review',
    'impact_assessed': 'Impact Assessed', 'migration_planned': 'Migration Planned',
    'approved': 'Approved', 'in_progress': 'In Progress', 'completed': 'Completed',
    'cancelled': 'Cancelled'
};
let STATUS_COLORS = {
    'identified': 'bg-slate-500', 'under_review': 'bg-primary',
    'impact_assessed': 'bg-primary', 'migration_planned': 'bg-violet-500',
    'approved': 'bg-cyan-500', 'in_progress': 'bg-amber-500', 'completed': 'bg-emerald-500',
    'cancelled': 'bg-muted/70'
};

// ── Register Alpine component ────────────────────────────────────────
document.addEventListener('alpine:init', function() {
    Alpine.data('consolidationTable', function() {
        // Platform.dataTable.mixin() exposes selectedCount / hasSelection /
        // allPageSelected / hasActiveFilters as accessor properties added via
        // Object.defineProperty (see components/data_table.js), which default
        // to non-enumerable. A plain Object.assign({}, mixin, pageSpecific)
        // silently drops non-enumerable properties, so those getters never
        // made it onto this component — every x-text/x-show referencing them
        // threw "not defined" once Alpine tried to read them off `this`.
        // Object.getOwnPropertyDescriptors + defineProperties copies the
        // descriptors (including get/set) instead of just the current value.
        let base = Platform.dataTable.mixin({
            apiUrl: '/consolidation-list/api/entries',
            perPage: 25,
            itemsKey: 'entries',
            storageKey: 'consolidation',
            detailUrl: '/consolidation-list/api/entry/{id}/detail',
            onResponse: function(data) {
                this.summary = data.summary || {};
                // Store pagination info from API response
                if (data.pagination) {
                    this.totalItems = data.pagination.total || 0;
                }
            }
        });
        let pageSpecific = {
                // ── Page-specific state ──────────────────────────────
                summary: {},
                groupBy: '',
                pipelineStatusFilter: '',

                // ── Pipeline constants (exposed to template) ─────────
                STATUSES: STATUSES,
                STATUS_LABELS: STATUS_LABELS,
                STATUS_COLORS: STATUS_COLORS,

                // ── Init ─────────────────────────────────────────────
                init: function() {
                    this._tableInit();
                },

                // ── Summary card data ────────────────────────────────
                summaryCards: function() {
                    let s = this.summary;
                    let fmtC = (v) => (v === null || v === undefined ? Platform.figure.NOT_RECORDED : this.fmt.currency(v));
                    return [
                        { label: 'Portfolio Annual Cost', value: fmtC(s.portfolio_annual_cost), color: 'text-foreground' },
                        { label: 'Projected Savings', value: fmtC(s.total_estimated_savings), subtitle: s.savings_verified_total ? fmtC(s.savings_verified_total) + ' verified' : '', color: 'text-emerald-600' },
                        { label: 'Migration Investment', value: fmtC(s.total_migration_cost), subtitle: s.total_estimated_savings > 0 ? Math.round(s.total_migration_cost / s.total_estimated_savings * 12) + ' mo payback' : '', color: 'text-foreground' },
                        { label: 'In Progress', value: String(s.active_count || 0), subtitle: (s.completed_count || 0) + ' completed', color: 'text-primary' },
                        { label: 'Requires Attention', value: String(s.attention_count || 0), color: (s.attention_count || 0) > 0 ? 'text-amber-600' : 'text-foreground' },
                        { label: 'High Risk', value: String(s.high_risk_count || 0), color: (s.high_risk_count || 0) > 0 ? 'text-destructive' : 'text-foreground' }
                    ];
                },

                // ── Pipeline bar segments ────────────────────────────
                pipelineSegments: function() {
                    let byStatus = (this.summary && this.summary.by_status) || {};
                    let total = 0;
                    let self = this;
                    STATUSES.forEach(function(s) { total += (byStatus[s] || 0); });
                    if (total === 0) return [];
                    let segments = [];
                    STATUSES.forEach(function(status) {
                        let count = byStatus[status] || 0;
                        if (count === 0) return;
                        segments.push({
                            status: status,
                            label: STATUS_LABELS[status] || status,
                            count: count,
                            pct: Math.max((count / total) * 100, 3),
                            color: STATUS_COLORS[status] || 'bg-muted/70'
                        });
                    });
                    return segments;
                },

                // ── Pipeline click filter ────────────────────────────
                filterByPipelineStatus: function(status) {
                    this.pipelineStatusFilter = status;
                    this.setFilter('status', status);
                },

                clearPipelineFilter: function() {
                    this.pipelineStatusFilter = '';
                    this.setFilter('status', '');
                },

                // ── Badge helpers (consolidation-specific) ───────────
                timeBadgeClass: function(action) {
                    if (!action) return '';
                    let map = {
                        'TOLERATE': 'bg-slate-500/10 text-slate-600 border-slate-500/30',
                        'INVEST': 'bg-cyan-500/10 text-cyan-600 border-cyan-500/30',
                        'MIGRATE': 'bg-violet-500/10 text-violet-600 border-violet-500/30',
                        'ELIMINATE': 'bg-red-500/10 text-destructive border-red-500/30'
                    };
                    return map[(action || '').toUpperCase()] || 'bg-slate-500/10 text-slate-600 border-slate-500/30';
                },

                formatTimeAction: function(action, score) {
                    if (!action) return '\u2014';
                    let label = action.charAt(0).toUpperCase() + action.slice(1).toLowerCase();
                    return score ? label + ' (' + Math.round(score) + ')' : label;
                },

                // M4: application_owner / business_domain / user_count each used
                // to render their own leading " · " independently, so a row
                // missing an earlier field (e.g. no owner set) left a dangling
                // separator with nothing before it ("· domain"). Join only the
                // parts that actually have a value.
                entryMetaLine: function(entry) {
                    let parts = [];
                    if (entry.application_owner) parts.push(entry.application_owner);
                    if (entry.business_domain) parts.push(entry.business_domain);
                    if (entry.user_count) parts.push(entry.user_count + ' users');
                    return parts.join(' · ');
                },

                actionBadgeClass: function(action) {
                    let map = {
                        'consolidate': 'bg-blue-500/10 text-primary border-blue-500/30',
                        'merge': 'bg-blue-500/10 text-primary border-blue-500/30',
                        'decommission': 'bg-red-500/10 text-destructive border-red-500/30',
                        'retire': 'bg-amber-500/10 text-amber-600 border-amber-500/30',
                        'replace': 'bg-violet-500/10 text-violet-600 border-violet-500/30',
                        'modernize': 'bg-cyan-500/10 text-cyan-600 border-cyan-500/30',
                        'add_to_roadmap': 'bg-green-500/10 text-emerald-600 border-green-500/30',
                        'pending_review': 'bg-slate-500/10 text-slate-600 border-slate-500/30'
                    };
                    return map[action] || 'bg-slate-500/10 text-slate-600 border-slate-500/30';
                },

                complexityBadgeClass: function(level) {
                    let map = {
                        'low': 'bg-green-500/10 text-emerald-600 border-green-500/30',
                        'medium': 'bg-amber-500/10 text-amber-600 border-amber-500/30',
                        'high': 'bg-red-500/10 text-destructive border-red-500/30'
                    };
                    return map[(level || '').toLowerCase()] || 'bg-slate-500/10 text-slate-600 border-slate-500/30';
                },

                // ── Criticality bars ─────────────────────────────────
                criticalityLevel: function(level) {
                    let map = { 'critical': 4, 'high': 3, 'medium': 2, 'low': 1 };
                    return map[(level || '').toLowerCase()] || 0;
                },

                criticalityColor: function(level) {
                    let map = { 'critical': 'bg-destructive', 'high': 'bg-amber-500', 'medium': 'bg-yellow-400', 'low': 'bg-emerald-500' };
                    return map[(level || '').toLowerCase()] || 'bg-muted';
                },

                // ── Status dots for pipeline visualization ───────────
                statusDotFilled: function(status, dotStatus) {
                    let currentIdx = STATUSES.indexOf(status);
                    let dotIdx = STATUSES.indexOf(dotStatus);
                    return dotIdx <= currentIdx && currentIdx >= 0;
                },

                // ── Warning footer ───────────────────────────────────
                warningMessages: function() {
                    let s = this.summary;
                    let msgs = [];
                    if (s.no_roadmap_count > 0) msgs.push(s.no_roadmap_count + ' without roadmap link');
                    if (s.no_owner_count > 0) msgs.push(s.no_owner_count + ' without assigned owner');
                    if (s.no_target_date_count > 0) msgs.push(s.no_target_date_count + ' without target date');
                    return msgs;
                },

                // ── Bulk actions ─────────────────────────────────────
                bulkAction: function(action) {
                    if (!this.hasSelection) {
                        Platform.toast.warning('Please select at least one entry.');
                        return;
                    }
                    let entryIds = this.getSelectedIds();
                    let self = this;

                    let actionNames = {
                        'decommission': 'Decommission', 'retire': 'Retire',
                        'add_to_roadmap': 'Add to Roadmap', 'set_wave': 'Set Wave',
                        'assign_owner': 'Assign Owner'
                    };

                    if (action === 'set_wave') {
                        let modalId = window.modalManager.createModal({
                            title: 'Set Wave',
                            content: '<div class="space-y-3"><p class="text-sm text-muted-foreground">Set wave number for ' + this.selectedCount + ' entries:</p>' +
                                '<input type="number" id="bulk-wave-input" min="1" max="10" value="1" class="w-full px-4 py-2 border border-input rounded-lg bg-background text-sm"></div>',
                            size: 'small',
                            buttons: [
                                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                                { text: 'Apply', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'confirm', handler: function() {
                                    let waveVal = document.getElementById('bulk-wave-input').value;
                                    self._executeBulk('set_wave', entryIds, { wave: parseInt(waveVal, 10) });
                                } }
                            ]
                        });
                        window.modalManager.open(modalId);
                        return;
                    }

                    if (action === 'assign_owner') {
                        let modalId2 = window.modalManager.createModal({
                            title: 'Assign Owner',
                            content: '<div class="space-y-3"><p class="text-sm text-muted-foreground">Assign owner for ' + this.selectedCount + ' entries:</p>' +
                                '<input type="text" id="bulk-owner-input" placeholder="Name or team" class="w-full px-4 py-2 border border-input rounded-lg bg-background text-sm"></div>',
                            size: 'small',
                            buttons: [
                                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                                { text: 'Apply', class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'confirm', handler: function() {
                                    let ownerVal = document.getElementById('bulk-owner-input').value;
                                    self._executeBulk('assign_owner', entryIds, { owner: ownerVal });
                                } }
                            ]
                        });
                        window.modalManager.open(modalId2);
                        return;
                    }

                    // Standard confirm
                    let modalId3 = window.modalManager.createModal({
                        title: 'Confirm Bulk Action',
                        content: '<p class="text-sm text-muted-foreground">Are you sure you want to mark ' + this.selectedCount + ' entry/entries as "' + (actionNames[action] || action) + '"?</p>',
                        size: 'small',
                        buttons: [
                            { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                            { text: 'Confirm', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'confirm', handler: function() {
                                self._executeBulk(action, entryIds, {});
                            } }
                        ]
                    });
                    window.modalManager.open(modalId3);
                },

                _executeBulk: function(action, entryIds, extra) {
                    let self = this;
                    let payload = { entry_ids: entryIds, action: action };
                    Object.keys(extra).forEach(function(k) { payload[k] = extra[k]; });

                    Platform.fetch.post('/consolidation-list/api/bulk-action', payload)
                    .then(function(data) {
                        if (data.success) {
                            Platform.toast.success('Processed ' + data.updated_count + ' entries.');
                            self.clearSelection();
                            self.refresh();
                        } else {
                            Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                        }
                    })
                    .catch(function(error) {
                        Platform.log.error('Bulk action error:', error);
                        Platform.toast.error('Error performing bulk action.');
                    });
                },

                // ── Score All ────────────────────────────────────────
                scoringInProgress: false,
                scoreAll: function() {
                    let self = this;
                    this.scoringInProgress = true;
                    Platform.fetch.post('/consolidation-list/api/score-all', {})
                    .then(function(data) {
                        if (data.success) {
                            Platform.toast.success('Scored ' + (data.scored_count || 0) + ' entries. ' + (data.skipped_count || 0) + ' skipped.');
                            self.refresh();
                        } else {
                            Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                        }
                    })
                    .catch(function(error) {
                        Platform.log.error('Score all error:', error);
                        Platform.toast.error('Error scoring entries.');
                    })
                    .finally(function() {
                        self.scoringInProgress = false;
                    });
                },

                // ── Recalculate Savings ──────────────────────────────
                recalcInProgress: false,
                recalculateSavings: function() {
                    let self = this;
                    this.recalcInProgress = true;
                    Platform.fetch.post('/consolidation-list/api/recalculate-savings', {})
                    .then(function(data) {
                        if (data.success) {
                            let msg = 'Savings recalculated: ' + (data.currency_symbol || '£') + (data.total_savings == null ? Platform.figure.NOT_RECORDED : parseFloat(data.total_savings).toLocaleString());
                            if (data.real_data_count > 0) msg += ' (' + data.real_data_count + ' with real costs)';
                            Platform.toast.success(msg);
                            self.refresh();
                        } else {
                            Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                        }
                    })
                    .catch(function(error) {
                        Platform.log.error('Recalculate savings error:', error);
                        Platform.toast.error('Error recalculating savings.');
                    })
                    .finally(function() {
                        self.recalcInProgress = false;
                    });
                },

                // ── Remove entry ─────────────────────────────────────
                removeEntry: function(entryId) {
                    let self = this;
                    let modalId = window.modalManager.createModal({
                        title: 'Remove Entry',
                        content: '<p class="text-sm text-muted-foreground">Are you sure you want to remove this entry from the consolidation list?</p>',
                        size: 'small',
                        buttons: [
                            { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                            { text: 'Remove', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'remove', handler: function() {
                                Platform.fetch.delete('/consolidation-list/api/entry/' + entryId)
                                .then(function(data) {
                                    if (data.success) {
                                        Platform.toast.success('Entry removed successfully');
                                        self.refresh();
                                    } else {
                                        Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                                    }
                                })
                                .catch(function(error) {
                                    Platform.log.error('Error removing entry:', error);
                                    Platform.toast.error('Error removing entry.');
                                });
                            } }
                        ]
                    });
                    window.modalManager.open(modalId);
                },

                // ── Modal openers ────────────────────────────────────
                openAddApps: function() {
                    if (window.openAddApplicationsModal) window.openAddApplicationsModal();
                },

                openEditEntry: function(entryId) {
                    if (window.openEditEntryModal) window.openEditEntryModal(entryId);
                },

                openRoadmapConfig: function(entryId) {
                    if (window.openRoadmapConfigModal) window.openRoadmapConfigModal(entryId);
                },

                // ── Detail panel data helpers ────────────────────────
                getDetail: function(id) {
                    return this.expandedData[id] || null;
                },

                detailHasError: function(id) {
                    let d = this.expandedData[id];
                    return d && d._error;
                },

                // ── Quick Edit helpers ────────────────────────────
                getMissingFields: function(entry) {
                    let fields = [];
                    if (entry.estimated_savings == null || entry.estimated_savings === 0)
                        fields.push({ key: 'estimated_savings', label: 'Estimated Savings', type: 'currency' });
                    if (entry.migration_cost == null)
                        fields.push({ key: 'migration_cost', label: 'Migration Cost', type: 'currency' });
                    if (!entry.target_date)
                        fields.push({ key: 'target_date', label: 'Target Date', type: 'date' });
                    if (!entry.assigned_to)
                        fields.push({ key: 'assigned_to', label: 'Assigned To', type: 'text' });
                    if (!entry.business_rationale)
                        fields.push({ key: 'business_rationale', label: 'Business Rationale', type: 'textarea' });
                    if (!entry.migration_complexity)
                        fields.push({ key: 'migration_complexity', label: 'Migration Complexity', type: 'select',
                            options: [
                                { value: '', label: 'Select...' },
                                { value: 'low', label: 'Low' },
                                { value: 'medium', label: 'Medium' },
                                { value: 'high', label: 'High' }
                            ] });
                    if (!entry.data_disposition)
                        fields.push({ key: 'data_disposition', label: 'Data Disposition', type: 'select',
                            options: [
                                { value: '', label: 'Select...' },
                                { value: 'migrate', label: 'Migrate' },
                                { value: 'archive', label: 'Archive' },
                                { value: 'delete', label: 'Delete' },
                                { value: 'retain', label: 'Retain' }
                            ] });
                    if (!entry.risk_assessment)
                        fields.push({ key: 'risk_assessment', label: 'Risk Assessment', type: 'textarea' });
                    return fields;
                },

                quickSaving: {},

                quickSave: function(entryId) {
                    let self = this;
                    let formEl = document.querySelector('[data-quick-edit="' + entryId + '"]');
                    if (!formEl) return;

                    // Collect only fields that have values
                    let payload = {};
                    let inputs = formEl.querySelectorAll('[data-field]');
                    inputs.forEach(function(input) {
                        let key = input.getAttribute('data-field');
                        let val = input.value;
                        if (!val || val.trim() === '') return;

                        if (key === 'estimated_savings' || key === 'migration_cost') {
                            val = parseFloat(val);
                            if (isNaN(val)) return;
                        }
                        payload[key] = val;
                    });

                    if (Object.keys(payload).length === 0) {
                        Platform.toast.warning('No fields to save. Fill in at least one field.');
                        return;
                    }

                    self.quickSaving[entryId] = true;

                    Platform.fetch.put('/consolidation-list/api/entry/' + entryId, payload)
                    .then(function(data) {
                        if (data.success && data.entry) {
                            // Update the entry in items array in-place (no full reload)
                            let idx = self.items.findIndex(function(e) { return e.id === entryId; });
                            if (idx !== -1) {
                                Object.keys(data.entry).forEach(function(k) {
                                    self.items[idx][k] = data.entry[k];
                                });
                            }
                            Platform.toast.success('Updated successfully');
                        } else {
                            Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
                        }
                    })
                    .catch(function(err) {
                        Platform.log.error('Quick save error:', err);
                        Platform.toast.error('Error saving changes.');
                    })
                    .finally(function() {
                        delete self.quickSaving[entryId];
                    });
                },

                isQuickSaving: function(entryId) {
                    return !!this.quickSaving[entryId];
                },

                // ── Missing-field filter ──────────────────────────
                missingFilter: '',

                setMissingFilter: function(filterValue) {
                    this.missingFilter = filterValue;
                    this.setFilter('missing', filterValue);
                },

                clearMissingFilter: function() {
                    this.missingFilter = '';
                    this.setFilter('missing', '');
                }
            };
        let result = {};
        Object.defineProperties(result, Object.getOwnPropertyDescriptors(base));
        Object.defineProperties(result, Object.getOwnPropertyDescriptors(pageSpecific));
        return result;
    });
});

// ── Backward-compat shim for modals that call window.loadEntries() ───
window.loadEntries = function() {
    let el = document.querySelector('[x-data*="consolidationTable"]');
    if (el && el._x_dataStack) {
        el._x_dataStack[0].refresh();
    }
};

// ── Legacy shims for old event delegation patterns ───────────────────
window.removeEntry = function(id) {
    let el = document.querySelector('[x-data*="consolidationTable"]');
    if (el && el._x_dataStack) el._x_dataStack[0].removeEntry(id);
};

window.bulkAction = function(action) {
    let el = document.querySelector('[x-data*="consolidationTable"]');
    if (el && el._x_dataStack) el._x_dataStack[0].bulkAction(action);
};

window.scoreAllEntries = function() {
    let el = document.querySelector('[x-data*="consolidationTable"]');
    if (el && el._x_dataStack) el._x_dataStack[0].scoreAll();
};

window.recalculateSavings = function() {
    let el = document.querySelector('[x-data*="consolidationTable"]');
    if (el && el._x_dataStack) el._x_dataStack[0].recalculateSavings();
};

})();
