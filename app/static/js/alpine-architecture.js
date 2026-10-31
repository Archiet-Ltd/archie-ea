/**
 * =============================================================================
 * UNIFIED ALPINE.JS ARCHITECTURE  —  alpine-architecture.js
 * =============================================================================
 *
 * LOADING ORDER (enforce in every base template):
 *   1. Alpine CDN (defer)
 *   2. Platform core: js/core/00-namespace.js → 05-error.js
 *   3. Platform UI:   js/ui/modal.js → ui/table.js
 *   4. Legacy shims:  js/modal-system.js, js/shared/api-fetch.js, js/shared/debounce.js
 *   5. THIS FILE                                — Alpine.data() registrations
 *   6. Page-specific JS (if any)               — MUST NOT redefine names below
 *
 * RULES:
 *   ✅ All components registered via Alpine.data() — never global functions
 *   ✅ Stores declared in admin_base.html alpine:init (sidebar, loading, theme,
 *      announcer, user, notifications)
 *   ✅ No inline JS in HTML — x-data references a registered name only
 *   ✅ No implicit globals — every identifier scoped to its component
 *   ✅ No duplicated component names
 *   ✅ Modal logic lives in modal components only
 *   ✅ Page logic lives in page components only
 *   ✅ Async always goes through Platform.fetch — never the native API
 *   ✅ Error handling always sets this.errorMsg
 *   ✅ Loading always uses this.loading flag
 *   ✅ Notifications always use window.toast — never alert()
 *   ✅ Validation always runs in validate() — never inline in submit()
 *
 * NAMING CONVENTION:
 *   Page components:   <domain><Page>              e.g. vendorList
 *   Modal components:  <domain><Action>Modal       e.g. vendorCreateModal
 *   Table components:  <domain>Table               e.g. applicationTable
 *   Widget components: <name>Widget                e.g. dropdownWidget
 *
 * COMPONENT REGISTRY (all Alpine.data names in this file):
 *   Primitives:  accordionWidget, dropdownWidget, tabsWidget, tooltipWidget,
 *                confirmDialog
 *   Vendor:      vendorList, vendorCreateModal
 *   Application: applicationTable, applicationCreateModal, applicationEditModal
 *   Batch:       batchImportForm, batchJobDetail
 *   Roadmap:     roadmapApp
 *   Session:     sessionDetail
 *   Solution:    solutionList
 *   Policy:      policyMonitoringDashboard
 *   Connector:   connectorDashboard
 *   Implementation: implementationDashboard, workPackageCreateModal
 *   Framework:   frameworkConfigModal
 * =============================================================================
 */

'use strict';

if (window.__ALPINE_ARCH_LOADED__) {
    /* Already loaded — skip re-registration. Harmless: the first load's
       components are already registered and remain in use. */
} else {
    window.__ALPINE_ARCH_LOADED__ = true;

    /* =========================================================================
     * PLATFORM DEPENDENCY REFERENCES
     * Resolved lazily so this file can load before Platform modules if needed.
     * ========================================================================= */

    function _fetch(url, opts) {
        // Merge opts with silent:true to suppress duplicate toast (error shown by component)
        const mergedOpts = Object.assign({}, opts || {}, { silent: true });
        if (window.Platform && window.Platform.fetch) {
            return window.Platform.fetch(url, mergedOpts);
        }
        // Platform.fetch must be available; if not, the loading order is broken.
        return Promise.reject(new Error('Platform.fetch not available'));
    }

    function _toast(type, msg) {
        let t = (window.Platform && window.Platform.toast) || window.toast;
        if (t && typeof t[type] === 'function') t[type](msg);
    }

    /* =========================================================================
     * INTERNAL MIXINS  (never used as x-data directly)
     * ========================================================================= */

    function _asyncMixin() {
        return {
            loading: false,
            submitting: false,
            errorMsg: '',
            successMsg: '',
            _startLoading() {
                this.loading = true; this.submitting = true; this.errorMsg = ''; this.successMsg = '';
                // Best-effort UI affordance: a missing/broken loading store must not block the operation.
                try {
                    if (typeof Alpine !== 'undefined' && Alpine.store && Alpine.store('loading')) {
                        Alpine.store('loading').start();
                    }
                } catch(e) { /* swallow-ok: a missing or broken Alpine loading store must not stop the operation the user asked for */ }
            },
            _stopLoading() {
                this.loading = false; this.submitting = false;
                // Best-effort UI affordance: a missing/broken loading store must not block the operation.
                try {
                    if (typeof Alpine !== 'undefined' && Alpine.store && Alpine.store('loading')) {
                        Alpine.store('loading').stop();
                    }
                } catch(e) { /* swallow-ok: spinner teardown only; the caller's own success or error message below is the real signal to the user */ }
            },
            _handleError(err) {
                this.loading = false; this.submitting = false;
                // Best-effort UI affordance: a missing/broken loading store must not block the operation.
                try {
                    if (typeof Alpine !== 'undefined' && Alpine.store && Alpine.store('loading')) {
                        Alpine.store('loading').stop();
                    }
                } catch(e) { /* swallow-ok: spinner teardown only; the caller's own success or error message below is the real signal to the user */ }
                this.errorMsg = (err && err.message) ? err.message : 'An unexpected error occurred.';
                _toast('error', this.errorMsg);
            },
            _handleSuccess(msg) {
                this.loading = false; this.submitting = false;
                // Best-effort UI affordance: a missing/broken loading store must not block the operation.
                try {
                    if (typeof Alpine !== 'undefined' && Alpine.store && Alpine.store('loading')) {
                        Alpine.store('loading').stop();
                    }
                } catch(e) { /* swallow-ok: spinner teardown only; the caller's own success or error message below is the real signal to the user */ }
                this.successMsg = msg || 'Operation completed successfully.';
                _toast('success', this.successMsg);
            }
        };
    }

    function _formMixin(defaultData) {
        let defaults = Object.assign({}, defaultData || {});
        return {
            formData: Object.assign({}, defaults),
            _defaultFormData: defaults,
            validationErrors: {},
            resetForm() {
                this.formData = Object.assign({}, this._defaultFormData);
                this.validationErrors = {};
                this.errorMsg = '';
                this.successMsg = '';
            },
            _setFieldError(field, msg) {
                this.validationErrors = Object.assign({}, this.validationErrors, { [field]: msg });
            },
            _clearFieldError(field) {
                let e = Object.assign({}, this.validationErrors);
                delete e[field];
                this.validationErrors = e;
            },
            _hasErrors() { return Object.keys(this.validationErrors).length > 0; }
        };
    }

    function _tableMixin(opts) {
        // Delegate to Platform.table.alpineMixin when the table module is loaded.
        // Falls back to an inline implementation so this file works standalone.
        if (window.Platform && window.Platform.table && window.Platform.table.alpineMixin) {
            return window.Platform.table.alpineMixin(opts || {});
        }
        // ── Inline fallback (identical behaviour, no Platform dependency) ──────
        return {
            items: [], totalItems: 0,
            page: 1, pageSize: (opts && opts.perPage) || 25,
            search: '', sortField: '', sortDir: 'asc',
            filters: {}, loading: false, errorMsg: '',
            get totalPages() { return Math.max(1, Math.ceil(this.totalItems / this.pageSize)); },
            get hasPrev()    { return this.page > 1; },
            get hasNext()    { return this.page < this.totalPages; },
            get pageStart()  { return this.totalItems === 0 ? 0 : (this.page - 1) * this.pageSize + 1; },
            get pageEnd()    { return Math.min(this.page * this.pageSize, this.totalItems); },
            prevPage()  { if (this.hasPrev) { this.page--; this._loadItems(); } },
            nextPage()  { if (this.hasNext) { this.page++; this._loadItems(); } },
            goToPage(n) { this.page = Math.max(1, Math.min(n, this.totalPages)); this._loadItems(); },
            setSort(field) {
                if (this.sortField === field) { this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc'; }
                else { this.sortField = field; this.sortDir = 'asc'; }
                this.page = 1; this._loadItems();
            },
            setSearch(q)       { this.search = q || ''; this.page = 1; this._loadItems(); },
            setFilter(key, value) {
                this.filters = Object.assign({}, this.filters);
                if (value === '' || value === null || value === undefined) { delete this.filters[key]; }
                else { this.filters[key] = value; }
                this.page = 1; this._loadItems();
            },
            clearFilters() { this.filters = {}; this.search = ''; this.page = 1; this._loadItems(); },
            setPerPage(n)  { this.pageSize = Math.max(1, parseInt(n, 10) || 25); this.page = 1; this._loadItems(); },
            refresh()      { this._loadItems(); },
            _buildQueryString() {
                let p = new URLSearchParams();
                p.set('page', this.page); p.set('per_page', this.pageSize);
                if (this.search) { p.set('search', this.search); p.set('q', this.search); }
                if (this.sortField) { p.set('sort', this.sortField); p.set('dir', this.sortDir); }
                let self = this;
                Object.keys(this.filters).forEach(function (k) {
                    let v = self.filters[k];
                    if (v !== '' && v !== null && v !== undefined) p.set(k, v);
                });
                return p.toString();
            },
            onSearchInput() { this.page = 1; this._loadItems(); }
        };
    }

    function _modalMixin() {
        return {
            _modalId: null,
            openModal(idOrData, payload) {
                let id   = (typeof idOrData === 'string') ? idOrData : this._modalId;
                let data = (typeof idOrData === 'string') ? payload   : idOrData;
                if (!id) {
                    if (window.Platform && window.Platform.log) {
                        window.Platform.log.warn('[_modalMixin] openModal called with no id and _modalId not set');
                    }
                    return;
                }
                let m = (window.Platform && window.Platform.modal) || window.Modal;
                if (m) m.open(id, data);
                if (typeof this.afterOpen === 'function') this.afterOpen(data);
            },
            closeModal(idOrResult, result) {
                let modalId = (typeof idOrResult === 'string') ? idOrResult : this._modalId;
                let res     = (typeof idOrResult === 'string') ? result      : idOrResult;
                if (!modalId) {
                    if (window.Platform && window.Platform.log) {
                        window.Platform.log.warn('[_modalMixin] closeModal called with no id and _modalId not set');
                    }
                    return;
                }
                let m = (window.Platform && window.Platform.modal) || window.Modal;
                if (m) m.close(modalId, res);
                if (typeof this.afterClose === 'function') this.afterClose();
            },
            afterOpen(data) { /* override */ },
            afterClose() { if (typeof this.resetForm === 'function') this.resetForm(); }
        };
    }

    /* =========================================================================
     * ALPINE REGISTRATION — runs after Alpine is ready
     * ========================================================================= */

    document.addEventListener('alpine:init', function () {

        /* -----------------------------------------------------------------------
         * PRIMITIVE WIDGETS
         * --------------------------------------------------------------------- */

        Alpine.data('accordionWidget', function (defaultOpen) {
            return { open: !!defaultOpen, toggle() { this.open = !this.open; } };
        });

        Alpine.data('dropdownWidget', function () {
            return { open: false, toggle() { this.open = !this.open; }, close() { this.open = false; } };
        });

        Alpine.data('tabsWidget', function (initialTab) {
            return {
                activeTab: initialTab || '',
                setTab(id) { this.activeTab = id; this.$dispatch('tab-changed', { tab: id }); },
                isActive(id) { return this.activeTab === id; }
            };
        });

        Alpine.data('tooltipWidget', function () {
            return { show: false, showTip() { this.show = true; }, hideTip() { this.show = false; } };
        });

        /**
         * confirmDialog
         * Trigger from any component:
         *   this.$dispatch('confirm-request', { message, confirmLabel, onConfirm })
         * Mount once in base layout:
         *   <div x-data="confirmDialog" @confirm-request.window="openModal($event.detail)">
         */
        Alpine.data('confirmDialog', function () {
            return Object.assign({}, _asyncMixin(), _modalMixin(), {
                message: 'Are you sure?',
                confirmLabel: 'Confirm',
                cancelLabel: 'Cancel',
                _onConfirm: null,

                afterOpen(detail) {
                    this.message      = (detail && detail.message)      || 'Are you sure?';
                    this.confirmLabel = (detail && detail.confirmLabel) || 'Confirm';
                    this.cancelLabel  = (detail && detail.cancelLabel)  || 'Cancel';
                    this._onConfirm   = (detail && typeof detail.onConfirm === 'function') ? detail.onConfirm : null;
                },
                confirm() {
                    if (typeof this._onConfirm === 'function') this._onConfirm();
                    this.closeModal();
                },
                cancel() { this.closeModal(); }
            });
        });

        /* -----------------------------------------------------------------------
         * VENDOR COMPONENTS
         * --------------------------------------------------------------------- */

        Alpine.data('vendorList', function (config) {
            return Object.assign({}, _asyncMixin(), _tableMixin(), {
                apiUrl: (config && config.apiUrl) || '/api/vendors',
                init() { this._loadItems(); },
                async _loadItems() {
                    this._startLoading();
                    try {
                        let data = await _fetch(this.apiUrl + '?' + this._buildQueryString());
                        this.items = data.items || data.vendors || [];
                        this.totalItems = data.total || this.items.length;
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                }
            });
        });

        // NOTE: vendorCreateModal is registered by app/static/js/vendors/create_modal.js
        // (the page-specific factory with submitCreateVendor + the vendor_management
        // .create_vendor POST target). A duplicate registration here shadowed it and
        // broke vendor creation under the CSP Alpine build — do NOT re-add it.

        /* -----------------------------------------------------------------------
         * APPLICATION COMPONENTS
         * --------------------------------------------------------------------- */

        Alpine.data('applicationTable', function (config) {
            return Object.assign({}, _asyncMixin(), _tableMixin(), {
                apiUrl: (config && config.apiUrl) || '/dashboard/api/applications/table-data',
                selected: [],
                init() {
                    this.pageSize = (config && config.pageSize) || 25;
                    this._loadItems();
                },
                async _loadItems() {
                    this._startLoading();
                    try {
                        let data = await _fetch(this.apiUrl + '?' + this._buildQueryString());
                        this.items = data.items || data.data || data.applications || [];
                        this.totalItems = data.total || this.items.length;
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                toggleSelect(id) {
                    let idx = this.selected.indexOf(id);
                    if (idx === -1) this.selected.push(id); else this.selected.splice(idx, 1);
                },
                isSelected(id) { return this.selected.indexOf(id) !== -1; },
                get allSelected() {
                    let self = this;
                    return this.items.length > 0 && this.items.every(function (i) { return self.selected.indexOf(i.id) !== -1; });
                },
                toggleSelectAll() {
                    this.selected = this.allSelected ? [] : this.items.map(function (i) { return i.id; });
                }
            });
        });

        Alpine.data('applicationCreateModal', function () {
            return Object.assign(
                {}, _asyncMixin(), _modalMixin(),
                _formMixin({ name: '', description: '', status: 'active', owner: '', department: '' }),
                {
                    apiUrl: '/dashboard/api/applications',
                    validate() {
                        this.validationErrors = {};
                        if (!this.formData.name || !this.formData.name.trim())
                            this._setFieldError('name', 'Application name is required.');
                        return !this._hasErrors();
                    },
                    async submit() {
                        if (!this.validate()) return;
                        this._startLoading();
                        try {
                            let data = await _fetch(this.apiUrl, { method: 'POST', body: this.formData });
                            this._handleSuccess('Application created.');
                            this.$dispatch('application-created', { application: data });
                            this.closeModal();
                        } catch (err) { this._handleError(err); }
                    }
                }
            );
        });

        Alpine.data('applicationEditModal', function () {
            return Object.assign(
                {}, _asyncMixin(), _modalMixin(),
                _formMixin({ id: null, name: '', description: '', status: 'active', owner: '', department: '' }),
                {
                    apiUrl: '/dashboard/api/applications',
                    afterOpen(detail) {
                        if (detail && detail.application)
                            this.formData = Object.assign({}, this._defaultFormData, detail.application);
                    },
                    validate() {
                        this.validationErrors = {};
                        if (!this.formData.name || !this.formData.name.trim())
                            this._setFieldError('name', 'Application name is required.');
                        return !this._hasErrors();
                    },
                    deleteApplication() {
                        let self = this;
                        this.$dispatch('confirm-request', {
                            message: 'Delete "' + this.formData.name + '"? This cannot be undone.',
                            confirmLabel: 'Delete',
                            onConfirm: async function () {
                                self._startLoading();
                                try {
                                    await _fetch(self.apiUrl + '/' + self.formData.id, { method: 'DELETE' });
                                    self._handleSuccess('Application deleted.');
                                    self.$dispatch('application-deleted', { id: self.formData.id });
                                    self.closeModal();
                                } catch (err) { self._handleError(err); }
                            }
                        });
                    }
                }
            );
        });

        /* -----------------------------------------------------------------------
         * BATCH IMPORT COMPONENTS
         * --------------------------------------------------------------------- */

        Alpine.data('batchImportForm', function () {
            return Object.assign({}, _asyncMixin(), _formMixin({ file: null, mapping: {}, options: {} }), {
                step: 1,
                previewData: [],
                columnHeaders: [],
                onFileChange(event) {
                    let file = event.target.files[0];
                    if (!file) return;
                    this.formData.file = file;
                    this._previewFile(file);
                },
                async _previewFile(file) {
                    this._startLoading();
                    try {
                        let fd = new FormData(); fd.append('file', file);
                        let data = await _fetch('/api/batch-import/jobs/analyze', { method: 'POST', body: fd });
                        this.columnHeaders = (data.file_stats && data.file_stats.columns) || [];
                        this.previewData = [];
                        this.step = 2; this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                async submit() {
                    this._startLoading();
                    try {
                        let fd = new FormData();
                        fd.append('file', this.formData.file);
                        fd.append('mapping', JSON.stringify(this.formData.mapping));
                        fd.append('options', JSON.stringify(this.formData.options));
                        let data = await _fetch('/api/batch-import/jobs', { method: 'POST', body: fd });
                        let fileName = (this.formData.file && this.formData.file.name) || 'your file';
                        this._handleSuccess('Importing ' + fileName + '; it appears in the list below.');
                        this.$dispatch('batch-job-created', { job: data });
                    } catch (err) { this._handleError(err); }
                }
            });
        });

        Alpine.data('batchJobDetail', function (config) {
            return Object.assign({}, _asyncMixin(), {
                jobId: (config && config.jobId) || null,
                job: null,
                _pollTimer: null,
                init() { if (this.jobId) this._loadJob(); },
                async _loadJob() {
                    this._startLoading();
                    try {
                        let resp = await _fetch('/api/batch-import/jobs/' + this.jobId);
                        let data = resp.job || resp.data || resp;
                        this.job = data; this._stopLoading();
                        if (data.status === 'processing' || data.status === 'pending') this._startPolling();
                        else this._stopPolling();
                    } catch (err) { this._handleError(err); }
                },
                _startPolling() {
                    if (this._pollTimer) return;
                    let self = this;
                    this._pollTimer = setInterval(function () { self._loadJob(); }, 3000);
                },
                _stopPolling() { if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; } },
                destroy() { this._stopPolling(); }
            });
        });

        /* -----------------------------------------------------------------------
         * ROADMAP COMPONENT
         * Replaces all roadmapApp() global functions across all roadmap templates.
         * Usage: x-data="roadmapApp({ apiBase: '/api/technology-roadmap' })"
         * --------------------------------------------------------------------- */

        Alpine.data('roadmapApp', function (config) {
            return Object.assign({}, _asyncMixin(), {
                apiBase: (config && config.apiBase) || '/api/roadmap',
                workPackages: [],
                capabilities: [],
                viewMode: 'timeline',
                selectedYear: new Date().getFullYear(),
                filters: { status: '', type: '' },
                exportOpen: false,
                init() { this._loadData(); },
                async _loadData() {
                    this._startLoading();
                    try {
                        let p = new URLSearchParams({ year: this.selectedYear, status: this.filters.status, type: this.filters.type });
                        let data = await _fetch(this.apiBase + '?' + p.toString());
                        this.workPackages = data.work_packages || [];
                        this.capabilities = data.capabilities || [];
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                setViewMode(mode) { this.viewMode = mode; },
                setYear(year) { this.selectedYear = year; this._loadData(); },
                setFilter(key, value) { this.filters[key] = value; this._loadData(); },
                async exportData(format) {
                    this.exportOpen = false;
                    window.open(this.apiBase + '/export?format=' + format + '&year=' + this.selectedYear, '_blank');
                }
            });
        });

        /* -----------------------------------------------------------------------
         * SESSION DETAIL
         * --------------------------------------------------------------------- */

        Alpine.data('sessionDetail', function (config) {
            return Object.assign({}, _asyncMixin(), {
                sessionId: (config && config.sessionId) || null,
                activeTab: 'overview',
                analysisResults: [],
                showScenario: false,
                init() { if (this.sessionId) this._loadAnalysis(); },
                async _loadAnalysis() {
                    this._startLoading();
                    try {
                        let data = await _fetch('/solution-architect/api/sessions/' + this.sessionId + '/analysis-results');
                        this.analysisResults = data.results || [];
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                setTab(tab) { this.activeTab = tab; },
                toggleScenario() { this.showScenario = !this.showScenario; }
            });
        });

        /* -----------------------------------------------------------------------
         * SOLUTION LIST
         * --------------------------------------------------------------------- */

        Alpine.data('solutionList', function () {
            return Object.assign({}, _asyncMixin(), _tableMixin(), {
                apiUrl: '/enterprise/api/solutions',
                drawerOpen: false,
                selectedSolution: null,
                init() { this._loadItems(); },
                async _loadItems() {
                    this._startLoading();
                    try {
                        let response = await _fetch(this.apiUrl + '?' + this._buildQueryString());
                        // Unwrap success_response wrapper if present (per CLAUDE.md convention)
                        let data = response.data || response;
                        this.items = data.items || data.solutions || [];
                        this.totalItems = data.total || data.pagination?.total || this.items.length;
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                openDrawer(solution) { this.selectedSolution = solution; this.drawerOpen = true; },
                closeDrawer() { this.drawerOpen = false; this.selectedSolution = null; }
            });
        });

        /* -----------------------------------------------------------------------
         * SOLUTION LIST COMPARE
         * --------------------------------------------------------------------- */

        Alpine.data('solutionListCompare', function () {
            return Object.assign({}, _asyncMixin(), _tableMixin(), {
                apiUrl: '/enterprise/api/solutions',
                drawerOpen: false,
                selectedSolution: null,
                compareIds: [],
                compareMode: false,
                startingSolution: false,
                init() { this._loadItems(); },
                // E2E-4: a fresh workspace had no working way to create its
                // first solution. The old plain create form was removed
                // (a real fix -- it opened a page mislabeled "Architecture
                // Journey"), but nothing replaced it: /solutions/new 404s,
                // "New from Template" needs an existing solution to save one
                // from, and "Start Architecture Journey" creates a separate
                // ArchitectureJourney artifact with, by that route's own
                // docstring, no Solution at all. POST /architecture-journey/start
                // already does the real thing (creates a Solution, redirects
                // into its design workspace) -- it was just never reachable
                // from this page. Calling it directly here, honestly labelled
                // "New Solution", is not the same mistake as before: the
                // label matches exactly where it goes.
                async startSolution() {
                    if (this.startingSolution) return;
                    this.startingSolution = true;
                    try {
                        const payload = await Platform.fetch.post('/architecture-journey/start', {}, { silent: true });
                        window.location.assign(payload.data.redirect);
                    } catch (error) {
                        Platform.toast.error(error.message || 'Could not create a new solution.');
                        this.startingSolution = false;
                    }
                },
                async _loadItems() {
                    this._startLoading();
                    try {
                        let response = await _fetch(this.apiUrl + '?' + this._buildQueryString());
                        // Unwrap success_response wrapper if present (per CLAUDE.md convention)
                        let data = response.data || response;
                        this.items = data.items || data.solutions || [];
                        this.totalItems = data.total || data.pagination?.total || this.items.length;
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                openDrawer(solution) { this.selectedSolution = solution; this.drawerOpen = true; },
                closeDrawer() { this.drawerOpen = false; this.selectedSolution = null; },
                toggleCompare(id) {
                    const idx = this.compareIds.indexOf(id);
                    if (idx === -1) {
                        if (this.compareIds.length < 3) this.compareIds.push(id);
                    } else {
                        this.compareIds.splice(idx, 1);
                    }
                    this.compareMode = this.compareIds.length > 0;
                },
                isInCompare(id) { return this.compareIds.includes(id); },
                async deleteSolution(id, name) {
                    if (!(await Platform.modal.confirm(`Delete solution "${name}"?`))) return;
                    // The list is served by enterprise.api_solutions (/enterprise/api/solutions),
                    // but that blueprint exposes no per-id DELETE. Deletion lives on the
                    // solution_design blueprint, which owns the cascade cleanup.
                    _fetch(`/solutions/${id}/delete-json`, { method: 'DELETE' })
                        .then(() => this._loadItems())
                        .catch(err => Platform.toast.error('Delete failed: ' + err.message));
                }
            });
        });

        /* -----------------------------------------------------------------------
         * POLICY MONITORING DASHBOARD
         * --------------------------------------------------------------------- */

        Alpine.data('policyMonitoringDashboard', function () {
            return Object.assign({}, _asyncMixin(), {
                apiUrl: '/api/policy-monitoring',
                monitoringData: { health_status: 'LOADING...', violations: [], metrics: {} },
                _pollTimer: null,
                init() { this._loadData(); this._startPolling(); },
                async _loadData() {
                    this._startLoading();
                    try {
                        let data = await _fetch(this.apiUrl + '/status');
                        this.monitoringData = data; this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                _startPolling() {
                    if (this._pollTimer) return;
                    let self = this;
                    this._pollTimer = setInterval(function () { self._loadData(); }, 30000);
                },
                destroy() { if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; } }
            });
        });

        /* -----------------------------------------------------------------------
         * CONNECTOR DASHBOARD
         * --------------------------------------------------------------------- */

        Alpine.data('connectorDashboard', function () {
            return Object.assign({}, _asyncMixin(), {
                apiUrl: '/integrations/api/connectors',
                connectors: [],
                selectedConnector: null,
                detailOpen: false,
                init() { this._loadConnectors(); },
                async _loadConnectors() {
                    this._startLoading();
                    try {
                        let data = await _fetch(this.apiUrl);
                        this.connectors = data.connectors || data.items || [];
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                openDetail(connector) { this.selectedConnector = connector; this.detailOpen = true; },
                closeDetail() { this.detailOpen = false; this.selectedConnector = null; }
            });
        });

        /* -----------------------------------------------------------------------
         * IMPLEMENTATION PLANNING COMPONENTS
         * --------------------------------------------------------------------- */

        Alpine.data('implementationDashboard', function (config) {
            return Object.assign({}, _asyncMixin(), {
                apiUrl: (config && config.apiUrl) || '/api/implementation',
                viewMode: 'gantt',
                showExportModal: false,
                exportFormat: 'csv',
                exportOptions: { workPackages: true, gaps: true, deliverables: false },
                workPackages: [],
                init() { this._loadData(); },
                async _loadData() {
                    this._startLoading();
                    try {
                        let data = await _fetch(this.apiUrl + '/dashboard');
                        this.workPackages = data.work_packages || [];
                        this._stopLoading();
                    } catch (err) { this._handleError(err); }
                },
                setViewMode(mode) { this.viewMode = mode; },
                openExport() { this.showExportModal = true; },
                closeExport() { this.showExportModal = false; },
                performExport() {
                    let params = new URLSearchParams();
                    params.append('format', this.exportFormat);
                    if (this.exportOptions.workPackages) params.append('include_work_packages', 'true');
                    if (this.exportOptions.gaps) params.append('include_gaps', 'true');
                    if (this.exportOptions.deliverables) params.append('include_deliverables', 'true');
                    window.open('/implementation/export?' + params.toString(), '_blank');
                    this.showExportModal = false;
                }
            });
        });

        Alpine.data('workPackageCreateModal', function () {
            return Object.assign(
                {}, _asyncMixin(), _modalMixin(),
                _formMixin({
                    name: '', description: '', assigned_to: '',
                    priority: 'medium', status: 'planned',
                    start_date: '', end_date: ''
                }),
                {
                    apiUrl: '/api/implementation/work-packages',
                    validate() {
                        this.validationErrors = {};
                        if (!this.formData.name || !this.formData.name.trim())
                            this._setFieldError('name', 'Work package name is required.');
                        if (this.formData.start_date && this.formData.end_date &&
                            this.formData.end_date < this.formData.start_date)
                            this._setFieldError('end_date', 'End date must be after start date.');
                        return !this._hasErrors();
                    },
                    async submit() {
                        if (!this.validate()) return;
                        this._startLoading();
                        try {
                            let data = await _fetch(this.apiUrl, { method: 'POST', body: this.formData });
                            this._handleSuccess('Work package created.');
                            this.$dispatch('work-package-created', { workPackage: data });
                            this.closeModal();
                        } catch (err) { this._handleError(err); }
                    }
                }
            );
        });

        /* -----------------------------------------------------------------------
         * FRAMEWORK CONFIG MODAL
         * --------------------------------------------------------------------- */

        Alpine.data('frameworkConfigModal', function () {
            return Object.assign(
                {}, _asyncMixin(), _modalMixin(),
                _formMixin({ configuration_name: '', configuration_code: '', description: '' }),
                {
                    apiUrl: '/api/framework-config/configurations',
                    validate() {
                        this.validationErrors = {};
                        if (!this.formData.configuration_name || !this.formData.configuration_name.trim())
                            this._setFieldError('configuration_name', 'Configuration name is required.');
                        if (!this.formData.configuration_code || !this.formData.configuration_code.trim())
                            this._setFieldError('configuration_code', 'Configuration code is required.');
                        return !this._hasErrors();
                    },
                    async submit() {
                        if (!this.validate()) return;
                        this._startLoading();
                        try {
                            let data = await _fetch(this.apiUrl, { method: 'POST', body: this.formData });
                            this._handleSuccess('Framework configuration saved.');
                            this.$dispatch('framework-config-created', { config: data });
                            this.closeModal();
                        } catch (err) { this._handleError(err); }
                    }
                }
            );
        });

    }); /* end alpine:init */

} /* end guard */
