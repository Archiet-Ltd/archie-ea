/**
 * Capability Governance Dashboard — Alpine.js component
 * Loads capabilities from the API and provides sorting, filtering, pagination,
 * bulk selection, and inline governance editing.
 */
(function () {
    'use strict';

    const API_URL = '/capability-governance/api/capabilities-for-governance';

    // CSRF token is now automatically injected by Platform.fetch for mutating methods.
    // No need for a manual getCSRF function.

    window.governanceDashboard = function () {
        return {
            // Data
            _allItems: [],
            items: [],
            loading: true,
            errorMsg: '',

            // Metrics
            totalCapabilities: 0,
            needsAttention: 0,
            governanceScore: '—',
            // Numeric value backing governanceScore's display string, or null when there is nothing
            // to measure. It drives the band word on the score card, so a 0% score never reads as healthy.
            governanceScoreValue: null,

            governanceScoreBand: function () {
                if (this.governanceScoreValue === null) return 'Nothing to measure';
                if (this.governanceScoreValue >= 80) return 'Healthy';
                if (this.governanceScoreValue >= 50) return 'Partly governed';
                return 'Weakly governed';
            },

            // The library dialogs open and close through their own handle; the older Platform.modal is the fallback.
            openDialog: function (id) {
                const el = document.getElementById(id);
                if (el && el.fbOpen) el.fbOpen();
                else if (window.Platform && window.Platform.modal) Platform.modal.open(id);
            },
            closeDialog: function (id) {
                const el = document.getElementById(id);
                if (el && el.fbClose) el.fbClose();
                else if (window.Platform && window.Platform.modal) Platform.modal.close(id);
            },

            // Pagination
            page: 1,
            pageSize: 25,
            totalItems: 0,

            // Sorting
            sortField: 'name',
            sortDir: 'asc',

            // Search
            searchQuery: '',

            // Selection
            _selectedIds: [],
            selectAllState: '',

            // Edit modal
            editForm: {
                id: null,
                name: '',
                business_owner: '',
                business_criticality: '',
                strategic_importance: '',
                description: '',
                current_maturity_level: '',
                target_maturity_level: '',
                maturity_assessment_notes: ''
            },
            editSaving: false,
            editError: '',

            // Bulk delete
            bulkDeleting: false,
            deleteConfirmText: '',
            deleteInProgress: false,

            init: function () {
                // The library dialogs are moved to <body> when opened, outside this component's scope, so they read it from a store.
                Alpine.store('governance', this);
                this.loadData();
            },

            loadData: function () {
                let self = this;
                self.loading = true;
                self.errorMsg = '';

                // Platform.fetch will throw on non-ok responses, which we catch to set errorMsg.
                // We pass silent:true because we paint our own inline error state.
                Platform.fetch(API_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, silent: true })
                    .then(function (data) {
                        // Platform.fetch returns parsed JSON directly; no need to call .json().
                        if (!data.success) {
                            self.errorMsg = data.error || 'Failed to load capabilities.';
                            self.loading = false;
                            return;
                        }
                        self._allItems = data.capabilities || [];
                        self.totalCapabilities = self._allItems.length;
                        self.needsAttention = self._allItems.filter(function (c) { return c.needs_attention; }).length;

                        const governed = self._allItems.length - self.needsAttention;
                        if (self._allItems.length > 0) {
                            self.governanceScoreValue = Math.round((governed / self._allItems.length) * 100);
                            self.governanceScore = self.governanceScoreValue + '%';
                        } else {
                            self.governanceScoreValue = null;
                            self.governanceScore = '—';
                        }

                        self.applyFilters();
                        self.loading = false;
                    })
                    .catch(function (err) {
                        self.errorMsg = 'Could not load capabilities. Please refresh.';
                        self.loading = false;
                        // Do NOT use console.error in shipped code; error is already surfaced via errorMsg.
                    });
            },

            applyFilters: function () {
                let self = this;
                let filtered = self._allItems;

                // Search filter
                if (self.searchQuery) {
                    const q = self.searchQuery.toLowerCase();
                    filtered = filtered.filter(function (c) {
                        return (c.name || '').toLowerCase().indexOf(q) !== -1 ||
                               (c.business_owner || '').toLowerCase().indexOf(q) !== -1;
                    });
                }

                // Sort
                const field = self.sortField;
                const dir = self.sortDir === 'asc' ? 1 : -1;
                filtered.sort(function (a, b) {
                    let va = (a[field] || '');
                    let vb = (b[field] || '');
                    if (typeof va === 'string') va = va.toLowerCase();
                    if (typeof vb === 'string') vb = vb.toLowerCase();
                    if (va < vb) return -1 * dir;
                    if (va > vb) return 1 * dir;
                    return 0;
                });

                self.totalItems = filtered.length;

                // Paginate
                const start = (self.page - 1) * self.pageSize;
                self.items = filtered.slice(start, start + self.pageSize).map(function (c, idx) {
                    c.row_number = start + idx + 1;
                    return c;
                });
            },

            // Search
            setSearch: function (val) {
                this.searchQuery = val;
                this.page = 1;
                this.applyFilters();
            },

            // Sorting
            setSort: function (field) {
                if (this.sortField === field) {
                    this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortField = field;
                    this.sortDir = 'asc';
                }
                this.applyFilters();
            },

            // Pagination
            setPerPage: function (val) {
                this.pageSize = parseInt(val) || 25;
                this.page = 1;
                this.applyFilters();
            },

            get pageStart() {
                return this.totalItems === 0 ? 0 : (this.page - 1) * this.pageSize + 1;
            },

            get pageEnd() {
                return Math.min(this.page * this.pageSize, this.totalItems);
            },

            get totalPages() {
                return Math.ceil(this.totalItems / this.pageSize) || 1;
            },

            get hasPrev() { return this.page > 1; },
            get hasNext() { return this.page < this.totalPages; },

            prevPage: function () {
                if (this.hasPrev) { this.page--; this.applyFilters(); }
            },
            nextPage: function () {
                if (this.hasNext) { this.page++; this.applyFilters(); }
            },
            goToPage: function (pg) {
                if (pg >= 1 && pg <= this.totalPages) { this.page = pg; this.applyFilters(); }
            },

            getVisiblePages: function () {
                const total = this.totalPages;
                const current = this.page;
                if (total <= 7) {
                    const arr = [];
                    for (let i = 1; i <= total; i++) arr.push(i);
                    return arr;
                }
                const pages = [1];
                if (current > 3) pages.push('...');
                for (let j = Math.max(2, current - 1); j <= Math.min(total - 1, current + 1); j++) {
                    pages.push(j);
                }
                if (current < total - 2) pages.push('...');
                pages.push(total);
                return pages;
            },

            // Selection
            isSelected: function (id) {
                return this._selectedIds.indexOf(id) !== -1;
            },
            selectionCount: function () {
                return this._selectedIds.length;
            },
            toggleSelectAll: function () {
                let self = this;
                const allOnPage = self.items.every(function (r) { return self._selectedIds.indexOf(r.id) !== -1; });
                if (allOnPage) {
                    self.items.forEach(function (r) {
                        let idx = self._selectedIds.indexOf(r.id);
                        if (idx !== -1) self._selectedIds.splice(idx, 1);
                    });
                    self.selectAllState = '';
                } else {
                    self.items.forEach(function (r) {
                        if (self._selectedIds.indexOf(r.id) === -1) self._selectedIds.push(r.id);
                    });
                    self.selectAllState = 'page';
                }
            },
            handleRowCheckboxClick: function (event, rowIdx) {
                const row = this.items[rowIdx];
                if (!row) return;
                let idx = this._selectedIds.indexOf(row.id);
                if (idx !== -1) {
                    this._selectedIds.splice(idx, 1);
                } else {
                    this._selectedIds.push(row.id);
                }
            },
            selectAllCrossPage: function () {
                let self = this;
                self._selectedIds = self._allItems.map(function (c) { return c.id; });
                self.selectAllState = 'cross-page';
            },
            clearSelection: function () {
                this._selectedIds = [];
                this.selectAllState = '';
            },

            // Status helpers
            statusClass: function (val) {
                if (!val) return '';
                const v = val.toLowerCase();
                if (v === 'mission_critical') return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
                if (v === 'business_critical') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300';
                if (v === 'important') return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300';
                if (v === 'supporting') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300';
                return 'bg-muted text-muted-foreground';
            },

            formatStatus: function (val) {
                if (!val) return '';
                return val.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
            },

            maturityDisplay: function (row) {
                if (!row.current_maturity_level) return '';
                const labels = { 1: 'Initial', 2: 'Repeatable', 3: 'Defined', 4: 'Managed', 5: 'Optimizing' };
                return row.current_maturity_level + ' · ' + (labels[row.current_maturity_level] || '');
            },

            // Edit modal
            openEditModal: function (row) {
                this.editForm = {
                    id: row.id,
                    name: row.name || '',
                    business_owner: row.business_owner || '',
                    business_criticality: row.business_criticality || '',
                    strategic_importance: row.strategic_importance || '',
                    description: row.description || '',
                    current_maturity_level: row.current_maturity_level ? String(row.current_maturity_level) : '',
                    target_maturity_level: row.target_maturity_level ? String(row.target_maturity_level) : '',
                    maturity_assessment_notes: row.maturity_assessment_notes || ''
                };
                this.editError = '';
                this.editSaving = false;
                this.openDialog('edit-governance-modal');
            },

            saveGovernance: function () {
                let self = this;
                if (!self.editForm.id) return;
                self.editSaving = true;
                self.editError = '';

                // Platform.fetch.post automatically injects CSRF token and serializes plain object to JSON.
                // We pass silent:true because we paint our own inline error state (editError).
                Platform.fetch.post('/capability-governance/api/update-governance/' + self.editForm.id, {
                    business_owner: self.editForm.business_owner,
                    business_criticality: self.editForm.business_criticality,
                    strategic_importance: self.editForm.strategic_importance,
                    description: self.editForm.description,
                    current_maturity_level: self.editForm.current_maturity_level ? parseInt(self.editForm.current_maturity_level) : null,
                    target_maturity_level: self.editForm.target_maturity_level ? parseInt(self.editForm.target_maturity_level) : null,
                    maturity_assessment_notes: self.editForm.maturity_assessment_notes
                }, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, silent: true })
                .then(function (data) {
                    self.editSaving = false;
                    if (data.success) {
                        self.closeDialog('edit-governance-modal');
                        if (window.Platform && window.Platform.toast) {
                            Platform.toast.success('Governance updated');
                        }
                        self.loadData();
                    } else {
                        self.editError = data.error || 'Failed to save changes.';
                    }
                })
                .catch(function (err) {
                    self.editSaving = false;
                    self.editError = 'Network error. Please try again.';
                    // Do NOT use console.error in shipped code; error is already surfaced via editError.
                });
            },

            // Bulk delete
            confirmBulkDelete: function () {
                this.deleteConfirmText = '';
                this.openDialog('bulk-delete-confirm-modal');
            },

            executeBulkDelete: function () {
                let self = this;
                if (self.deleteConfirmText !== 'DELETE' || self.deleteInProgress) return;
                let ids = self._selectedIds.slice();
                if (ids.length === 0) return;
                self.deleteInProgress = true;

                let failures = 0;
                let remaining = ids.length;
                let finish = function () {
                    self.deleteInProgress = false;
                    self.deleteConfirmText = '';
                    self.clearSelection();
                    self.closeDialog('bulk-delete-confirm-modal');
                    if (window.Platform && window.Platform.toast) {
                        if (failures === 0) {
                            Platform.toast.success('Capabilities deleted.');
                        } else if (failures === ids.length) {
                            Platform.toast.error('Delete failed.');
                        } else {
                            Platform.toast.warning((ids.length - failures) + ' deleted, ' + failures + ' failed.');
                        }
                    }
                    self.loadData();
                };

                ids.forEach(function (id) {
                    // Platform.fetch.delete automatically injects CSRF token.
                    // We pass silent:true because we handle errors via the failures counter and final toast.
                    Platform.fetch.delete('/enterprise/capabilities/' + id, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, silent: true })
                    .then(function (data) {
                        // Platform.fetch returns parsed JSON directly; success field indicates outcome.
                        if (!data.success) failures++;
                    })
                    .catch(function () {
                        failures++;
                    })
                    .finally(function () {
                        remaining--;
                        if (remaining === 0) finish();
                    });
                });
            }
        };
    };
})();
