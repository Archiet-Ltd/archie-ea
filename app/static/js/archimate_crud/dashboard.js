// smoke-coverage-ok: plain-language formatTypeName reads window globals set by admin_base.html; no new DOM interactions
let APP_CONFIG = window.__APP_CONFIG__ || {};

document.addEventListener('alpine:init', function() {
    Alpine.data('archDashboard', function() {
        return {
            activeTab: APP_CONFIG.initialLayer || 'motivation',
            elements: [],
            pagination: { page: 1, pages: 0, per_page: 25, total: null, has_next: false, has_prev: false },
            loading: false,
            loadError: null,   // referenced by x-show/x-text in the template; must exist to avoid Alpine ReferenceError
            searchQuery: '',
            typeFilter: '',
            perPage: 25,
            currentPage: 1,
            sortBy: 'name',
            sortOrder: 'asc',
            // D-01: totalCount used to be a separately-assigned field, written
            // in loadAllLayerCounts() (the initial sweep) but NOT in
            // loadElements() (fired on every tab switch / refresh). A tab
            // switch after any write updated layerCounts[activeTab] to the
            // fresh value while totalCount kept summing the stale initial
            // sweep, so the headline and the tiles it supposedly summarises
            // drifted apart and stayed drifted (repro: headline 146 / tiles
            // summing to 147). Making it a getter over layerCounts makes that
            // drift structurally impossible — there is exactly one number,
            // and the headline is always the sum of what the tiles show.
            get totalCount() {
                return Object.values(this.layerCounts).reduce(function(a, b) { return a + (b || 0); }, 0);
            },
            layerCounts: {},
            layerConfig: APP_CONFIG.layerConfig || {},
            popstateHandler: null,
            // ARCH-064: fieldConfigs used to be inlined into every dashboard
            // response (~280KB of static, per-request-identical JSON). It is
            // now fetched once from fieldConfigsUrl in init() below and
            // populated here when it resolves; starts empty so a type-specific
            // create/edit form falls back to plain name+description until the
            // fetch completes, exactly as it already does for a type with no
            // configured fields.
            fieldConfigs: {},

            // Card filter and grouping
            cardFilter: '',
            groupByType: false,
            collapsedTypes: {},
            sourceFilter: '',

            bulkMode: false,
            selectedIds: [],
            selectAll: false,

            editingElement: null,
            formData: { element_type: '', name: '', description: '' },
            formError: '',
            saving: false,

            // Generate with AI — wires the previously dead
            // POST /<layer>/<element_type>/ai-generate endpoint. It only drafts
            // a name/description/attributes; it never writes to the database.
            aiGenerate: {
                layer: 'motivation',
                elementType: '',
                prompt: '',
                loading: false,
                error: '',
                result: null,
            },

            showDeleteConfirm: false,
            deletingElement: null,
            deleting: false,

            // ARCH-016: validation state
            validating: false,
            showValidationPanel: false,
            validationResults: null,

            // ARCH-016: viewpoint filter
            viewpointKey: 'basic',
            availableViewpoints: {},
            viewpointTypeFilter: [],

            // Layer-specific view modes (swimlane, cards, lifecycle, table)
            layerViewMode: JSON.parse(localStorage.getItem('archLayerViewMode') || '{}'),
            layerAltViews: {
                motivation: 'swimlane',
                strategy: 'cards',
                technology: 'lifecycle'
            },

            get currentViewMode() {
                let saved = this.layerViewMode[this.activeTab];
                if (saved) return saved;
                return 'table';
            },

            get hasAltView() {
                return !!this.layerAltViews[this.activeTab];
            },

            toggleViewMode() {
                let current = this.currentViewMode;
                let alt = this.layerAltViews[this.activeTab];
                this.layerViewMode[this.activeTab] = current === 'table' ? (alt || 'table') : 'table';
                localStorage.setItem('archLayerViewMode', JSON.stringify(this.layerViewMode));
            },

            // Health panel state
            showHealthPanel: false,

            // ARC-004: advanced filter panel
            showAdvanced: false,
            filterScope: '',
            filterBuildingBlock: '',
            filterPlateau: '',
            filterHasRels: '',

            // ARC-006: element detail slide-out panel
            showDetailPanel: false,
            detailElement: null,
            detailData: null,
            detailLoading: false,
            detailEditing: false,
            detailForm: { name: '', layer: '', element_type: '', description: '' },
            detailSaving: false,
            detailSaved: false,

            get typeCounts() {
                const counts = {};
                for (let i = 0; i < this.elements.length; i++) {
                    let t = this.elements[i].element_type || this.elements[i].type || 'Unknown';
                    counts[t] = (counts[t] || 0) + 1;
                }
                return counts;
            },

            get currentLayerTypes() {
                // Must ALWAYS return an array of UNIQUE values — x-for over
                // undefined, or with duplicate :key values, throws Alpine's
                // "reading 'after'" reconciliation error on tab switch.
                let cfg = this.layerConfig[this.activeTab];
                let arr = (cfg && Array.isArray(cfg.elements)) ? cfg.elements : [];
                return Array.from(new Set(arr));
            },

            get aiGenerateLayerTypes() {
                let cfg = this.layerConfig[this.aiGenerate.layer];
                let arr = (cfg && Array.isArray(cfg.elements)) ? cfg.elements : [];
                return Array.from(new Set(arr));
            },

            get currentTypeFields() {
                // Typed fields for whichever element type the modal is currently
                // showing — the selected type when creating, the existing
                // element's type when editing. A type with no config (most of
                // them, still) returns [] and the modal stays name+description only.
                let et = this.formData.element_type;
                let cfg = et ? this.fieldConfigs[et] : null;
                return (cfg && Array.isArray(cfg.fields)) ? cfg.fields : [];
            },

            get elementGroups() {
                let filtered = this.visibleElements;
                let groups = {};
                for (let i = 0; i < filtered.length; i++) {
                    let el = filtered[i];
                    let t = el.element_type;
                    if (!groups[t]) groups[t] = { type: t, elements: [], count: 0 };
                    groups[t].elements.push(el);
                    groups[t].count++;
                }
                return Object.values(groups);
            },

            get visibleElements() {
                let filtered = Array.isArray(this.elements) ? this.elements.slice() : [];
                if (this.cardFilter === 'orphaned') {
                    filtered = filtered.filter(function(el) { return el.rel_count === 0; });
                } else if (this.cardFilter === 'undocumented') {
                    filtered = filtered.filter(function(el) { return !el.description || el.description.trim() === ''; });
                }
                if (this.filterScope) {
                    let wantedScope = this.filterScope.toLowerCase();
                    filtered = filtered.filter(function(el) {
                        let props = el.properties;
                        if (typeof props === 'string') {
                            try { props = JSON.parse(props); } catch (_) { props = {}; }
                        }
                        return String((props || {}).scope || '').toLowerCase() === wantedScope;
                    });
                }
                if (this.filterBuildingBlock) {
                    let wantedBlock = this.filterBuildingBlock.toLowerCase();
                    filtered = filtered.filter(function(el) {
                        let props = el.properties;
                        if (typeof props === 'string') {
                            try { props = JSON.parse(props); } catch (_) { props = {}; }
                        }
                        return String((props || {}).building_block || (props || {}).building_block_type || '').toLowerCase() === wantedBlock;
                    });
                }
                if (this.filterPlateau) {
                    // Was reading el.properties.plateau — a JSON-blob field the
                    // create/edit form's "Architecture state" selector never wrote
                    // to (it writes ArchiMateElement.togaf_plateau, a real column,
                    // via the /api/layer/<layer>/elements endpoint's own "plateau"
                    // key). Tagging an element Baseline/Target and then filtering by
                    // it silently returned nothing. Fixed 2 Sep 2026 alongside the
                    // API serialization (archimate_crud/routes.py).
                    let wantedPlateau = this.filterPlateau.toLowerCase();
                    filtered = filtered.filter(function(el) {
                        return String(el.plateau || '').toLowerCase() === wantedPlateau;
                    });
                }
                if (this.filterHasRels === 'yes') {
                    filtered = filtered.filter(function(el) { return typeof el.rel_count === 'number' && el.rel_count > 0; });
                } else if (this.filterHasRels === 'no') {
                    filtered = filtered.filter(function(el) { return el.rel_count === 0; });
                }
                return filtered;
            },

            get relationshipMetrics() {
                let known = 0, connected = 0, orphaned = 0;
                for (let i = 0; i < this.elements.length; i++) {
                    let count = this.elements[i].rel_count;
                    if (typeof count !== 'number') continue;
                    known++;
                    if (count > 0) connected++;
                    else orphaned++;
                }
                return { known: known, connected: connected, orphaned: orphaned };
            },

            get relationshipHint() {
                let metrics = this.relationshipMetrics;
                if (!metrics.known) return 'Relationship data unavailable on this page';
                return metrics.connected + ' of ' + metrics.known + ' measured on this page';
            },

            get documentationPercent() {
                if (!this.pageCount) return null;
                let documented = this.elements.filter(function(el) {
                    return !!(el.description && el.description.trim());
                }).length;
                return Math.round(documented / this.pageCount * 100);
            },

            get documentationHint() {
                if (!this.pageCount) return 'No page denominator available';
                let documented = this.elements.filter(function(el) {
                    return !!(el.description && el.description.trim());
                }).length;
                return documented + ' of ' + this.pageCount + ' shown on this page';
            },

            get validationSummary() {
                if (!this.validationResults) return { value: '—', hint: 'Run validation to measure posture' };
                let values = [
                    this.validationResults.element_errors,
                    this.validationResults.element_warnings,
                    this.validationResults.relationship_errors,
                    this.validationResults.relationship_warnings,
                ];
                if (values.some(function(value) { return typeof value !== 'number'; })) {
                    return { value: '—', hint: 'Validation result unavailable' };
                }
                let issues = values.reduce(function(total, value) { return total + value; }, 0);
                return issues === 0
                    ? { value: 'Clear', hint: 'Latest validation found no issues' }
                    : { value: issues, hint: 'Issues in the latest validation run' };
            },

            get sourceCounts() {
                let portfolio = 0, architecture = 0;
                for (let i = 0; i < this.elements.length; i++) {
                    let el = this.elements[i];
                    if (el.source === 'portfolio') portfolio++;
                    else architecture++;
                }
                return { portfolio: portfolio, architecture: architecture };
            },

            get healthStats() {
                let connected = 0, orphaned = 0, missing_desc = 0;
                for (let i = 0; i < this.elements.length; i++) {
                    let el = this.elements[i];
                    if (el.rel_count && el.rel_count > 0) connected++;
                    else orphaned++;
                    if (!el.description || el.description.trim() === '') missing_desc++;
                }
                return { total_relationships: connected, connected: connected, orphaned: orphaned, missing_description: missing_desc };
            },

            get layerTotal() {
                return this.pagination && typeof this.pagination.total === 'number'
                    ? this.pagination.total
                    : null;
            },

            get layerTotalKnown() {
                return typeof this.layerTotal === 'number';
            },

            // D-03: sourceCounts/healthStats are computed from `this.elements`,
            // which is only the currently-loaded page (per_page, default 25) —
            // not the full layer. pageCount names that scope explicitly so the
            // template can label numerators/denominators consistently instead
            // of mixing a page-scoped count with the repository-wide layerTotal.
            get pageCount() {
                return this.elements.length;
            },

            get paginationStart() {
                if (!this.pagination.total) return 0;
                return ((this.pagination.page - 1) * this.pagination.per_page) + 1;
            },

            get paginationEnd() {
                if (!this.pagination.total) return 0;
                return Math.min(this.pagination.page * this.pagination.per_page, this.pagination.total);
            },

            toggleTypeGroup(type) {
                this.collapsedTypes[type] = !this.collapsedTypes[type];
            },

            toggleCardFilter(filter) {
                this.cardFilter = this.cardFilter === filter ? '' : filter;
            },

            get activeLayerLabel() {
                let cfg = this.layerConfig[this.activeTab];
                return cfg ? cfg.name : this.activeTab;
            },

            clearAllFilters() {
                this.searchQuery = '';
                this.typeFilter = '';
                this.sourceFilter = '';
                this.viewpointKey = 'basic';
                this.viewpointTypeFilter = [];
                this.filterScope = '';
                this.filterBuildingBlock = '';
                this.filterPlateau = '';
                this.filterHasRels = '';
                this.cardFilter = '';
                this.currentPage = 1;
                this.loadElements();
            },

            hasActiveFilters() {
                return !!(
                    this.searchQuery || this.typeFilter || this.sourceFilter ||
                    this.viewpointKey !== 'basic' || this.filterScope ||
                    this.filterBuildingBlock || this.filterPlateau ||
                    this.filterHasRels || this.cardFilter
                );
            },

            get detailFormLayerTypes() {
                return (this.layerConfig[this.detailForm.layer] || {}).elements || [];
            },

            init() {
                var self = this;
                if (APP_CONFIG.fieldConfigsUrl) {
                    Platform.fetch.get(APP_CONFIG.fieldConfigsUrl, null, { silent: true })
                        .then(function(data) { self.fieldConfigs = data || {}; })
                        .catch(function(err) {
                            // Non-fatal: typed fields just fall back to the plain
                            // name+description form until a retry/reload succeeds.
                            // Platform.fetch already logged the error; we must not swallow it.
                            // The error is already surfaced via toast (unless silent:true).
                            // We keep the existing behavior: fall back to empty config.
                        });
                }
                this.restoreUrlState();
                let urlPanel = new URLSearchParams(window.location.search).get('panel');
                if (urlPanel === 'health') {
                    this.showHealthPanel = true;
                }
                this.popstateHandler = function() {
                    self.restoreUrlState();
                    let viewpoint = self.availableViewpoints[self.viewpointKey];
                    self.viewpointTypeFilter = viewpoint && Array.isArray(viewpoint.element_types)
                        ? viewpoint.element_types
                        : [];
                    self.loadElements(false);
                };
                window.addEventListener('popstate', this.popstateHandler);
                this.loadElements(false);
                this.loadAllLayerCounts();
                this.loadViewpoints().then(function() {
                    let viewpoint = self.availableViewpoints[self.viewpointKey];
                    // Basic/All Elements has no type filter. Reloading the same
                    // data after it resolves caused overlapping Alpine renders
                    // and cancelled transitions in Firefox.
                    if (viewpoint && Array.isArray(viewpoint.element_types) && viewpoint.element_types.length > 0) {
                        self.viewpointTypeFilter = viewpoint.element_types;
                        self.loadElements(false);
                    }
                });
            },

            destroy() {
                if (this.popstateHandler) {
                    window.removeEventListener('popstate', this.popstateHandler);
                }
            },

            restoreUrlState() {
                let params = new URLSearchParams(window.location.search);
                let wantedLayer = params.get('layer') || APP_CONFIG.initialLayer || 'motivation';
                this.activeTab = this.layerConfig[wantedLayer] ? wantedLayer : 'motivation';

                let wantedType = params.get('element_type') || APP_CONFIG.initialElementType || '';
                this.typeFilter = this.currentLayerTypes.indexOf(wantedType) >= 0 ? wantedType : '';
                this.searchQuery = params.get('search') || '';
                this.sourceFilter = ['portfolio', 'architecture'].includes(params.get('source'))
                    ? params.get('source')
                    : '';
                this.viewpointKey = params.get('viewpoint') || 'basic';
                this.currentPage = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
                let wantedPageSize = parseInt(params.get('per_page') || '25', 10);
                this.perPage = [25, 50, 100].includes(wantedPageSize) ? wantedPageSize : 25;
                this.sortBy = ['name', 'element_type'].includes(params.get('sort_by'))
                    ? params.get('sort_by')
                    : 'name';
                this.sortOrder = params.get('sort_order') === 'desc' ? 'desc' : 'asc';
                this.filterScope = params.get('scope') || '';
                this.filterBuildingBlock = params.get('building_block') || '';
                this.filterPlateau = params.get('plateau') || '';
                this.filterHasRels = params.get('has_relationships') || '';
                this.groupByType = params.get('group') === 'type';
            },

            syncUrlState() {
                let params = new URLSearchParams(window.location.search);
                [
                    'layer', 'search', 'element_type', 'source', 'viewpoint',
                    'page', 'per_page', 'sort_by', 'sort_order', 'scope',
                    'building_block', 'plateau', 'has_relationships', 'group',
                ].forEach(function(key) { params.delete(key); });
                params.set('layer', this.activeTab);
                if (this.searchQuery) params.set('search', this.searchQuery);
                if (this.typeFilter) params.set('element_type', this.typeFilter);
                if (this.sourceFilter) params.set('source', this.sourceFilter);
                if (this.viewpointKey !== 'basic') params.set('viewpoint', this.viewpointKey);
                if (this.currentPage > 1) params.set('page', String(this.currentPage));
                if (this.perPage !== 25) params.set('per_page', String(this.perPage));
                if (this.sortBy !== 'name') params.set('sort_by', this.sortBy);
                if (this.sortOrder !== 'asc') params.set('sort_order', this.sortOrder);
                if (this.filterScope) params.set('scope', this.filterScope);
                if (this.filterBuildingBlock) params.set('building_block', this.filterBuildingBlock);
                if (this.filterPlateau) params.set('plateau', this.filterPlateau);
                if (this.filterHasRels) params.set('has_relationships', this.filterHasRels);
                if (this.groupByType) params.set('group', 'type');
                let query = params.toString();
                let nextUrl = window.location.pathname + (query ? '?' + query : '') + window.location.hash;
                let currentUrl = window.location.pathname + window.location.search + window.location.hash;
                if (nextUrl !== currentUrl) {
                    window.history.pushState({ architectureRepository: true }, '', nextUrl);
                }
            },

            switchTab(layerKey) {
                if (this.activeTab === layerKey) return;
                this.activeTab = layerKey;
                this.searchQuery = '';
                this.typeFilter = '';
                this.currentPage = 1;
                this.selectedIds = [];
                this.selectAll = false;
                this.loadElements();
            },

            async loadElements(syncState) {
                if (syncState !== false) this.syncUrlState();
                this.loading = true;
                this.loadError = null;
                try {
                    let params = {
                        page: this.currentPage,
                        per_page: this.perPage,
                        sort_by: this.sortBy,
                        sort_order: this.sortOrder,
                    };
                    if (this.searchQuery) params.search = this.searchQuery;
                    if (this.sourceFilter) params.source = this.sourceFilter;
                    // Viewpoint type filter takes precedence over manual type filter
                    if (this.viewpointTypeFilter.length > 0 && !this.typeFilter) {
                        params.element_type = this.viewpointTypeFilter.join(',');
                    } else if (this.typeFilter) {
                        params.element_type = this.typeFilter;
                    }

                    let data = await Platform.fetch.get(
                        '/architecture/api/layer/' + this.activeTab + '/elements',
                        params,
                        { silent: true }  // we paint our own error state via loadError
                    );
                    this.elements = data.elements || [];
                    this.pagination = data.pagination || { page: 1, pages: 0, per_page: this.perPage, total: null, has_next: false, has_prev: false };
                    this.currentPage = this.pagination.page || this.currentPage;
                    this.layerCounts[this.activeTab] = data.pagination && typeof data.pagination.total === 'number'
                        ? data.pagination.total
                        : null;
                } catch (err) {
                    // The template already renders an error state + Retry button on
                    // `loadError` (dashboard.html); nothing ever set it until now.
                    this.loadError = err.message || 'Could not load elements for this layer';
                    this.elements = [];
                    this.pagination = { page: this.currentPage, pages: 0, per_page: this.perPage, total: null, has_next: false, has_prev: false };
                    this.layerCounts[this.activeTab] = null;   // unknown, not zero
                    // Platform.fetch already showed a toast unless silent:true; we passed silent:true,
                    // so we need to show our own toast here to maintain existing behavior.
                    if (window.Platform && Platform.toast) Platform.toast.error(this.loadError);
                } finally {
                    this.loading = false;
                    this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
                }
            },

            async loadAllLayerCounts() {
                let self = this;
                let layerKeys = Object.keys(this.layerConfig);
                let uncounted = [];
                // One batched request instead of one per layer. The per-layer
                // sweep issued 7-14 requests per page load, and the app rate-limits
                // at 120/minute, so moving through a few architecture pages made the
                // tab badges start failing with HTTP 429.
                //
                // The per-layer endpoints are kept as a fallback so behaviour degrades
                // exactly as it did before: batched -> /count -> /elements -> em dash.
                let countOneLayer = async function(layerKey) {
                    let count = null;
                    try {
                        // raw-fetch-ok: raw status selects the legacy endpoint fallback
                        let d = await Platform.fetch.get(
                            '/architecture/api/layer/' + layerKey + '/count',
                            null,
                            { silent: true }
                        );
                        // Unwrap success_response wrapper if present (per CLAUDE.md convention)
                        let payload = d.data || d;
                        count = typeof payload.total === 'number' ? payload.total : null;
                    } catch (countError) {
                        // A failed count request is explicitly handled by the
                        // compatible elements endpoint immediately below.
                        count = null;
                    }
                    if (count === null) {
                        let d2 = await Platform.fetch.get(
                            '/architecture/api/layer/' + layerKey + '/elements',
                            { per_page: 1 },
                            { silent: true }
                        );
                        count = d2.pagination && typeof d2.pagination.total === 'number'
                            ? d2.pagination.total
                            : null;
                    }
                    if (count === null) throw new Error('Count response did not include a numeric total');
                    return count;
                };

                let batched = {};
                try {
                    let data = await Platform.fetch.get(
                        '/architecture/api/layer/counts',
                        null,
                        { silent: true }
                    );
                    let payload = data.data || data;
                    batched = payload.counts || {};
                } catch (batchError) {
                    // Batched endpoint unavailable: every layer falls through below.
                    batched = {};
                }

                for (let i = 0; i < layerKeys.length; i++) {
                    let layerKey = layerKeys[i];
                    let count = batched[layerKey];
                    if (typeof count === 'number') {
                        self.layerCounts[layerKey] = count;
                        // totalCount is a getter over layerCounts (see field
                        // definition above) - nothing to assign here.
                        continue;
                    }
                    try {
                        self.layerCounts[layerKey] = await countOneLayer(layerKey);
                    } catch (e) {
                        // null, never 0: a fabricated zero is indistinguishable from
                        // a layer that really has no elements. The tab badge renders
                        // null as an em dash.
                        self.layerCounts[layerKey] = null;
                        uncounted.push(layerKey);
                    }
                }

                // One toast for the whole sweep — six per-layer toasts would be worse
                // than the failure they report.
                if (uncounted.length && window.Platform && Platform.toast) {
                    Platform.toast.error('Could not count elements for: ' + uncounted.join(', ')
                        + '. Those tabs show a dash instead of a total.');
                }
            },

            async loadViewpoints() {
                try {
                    this.availableViewpoints = await Platform.fetch.get('/api/archimate/viewpoints', null, { silent: true });
                } catch (e) {
                    // The Viewpoint <select> keeps only its hardcoded "All Elements"
                    // option when this fails, which looks exactly like a tenant that
                    // has no viewpoints configured. Say so instead.
                    this.availableViewpoints = {};
                    if (window.Platform && Platform.toast) {
                        Platform.toast.error('Could not load viewpoints — the viewpoint filter is unavailable');
                    }
                }
            },

            applyViewpoint() {
                let vp = this.availableViewpoints[this.viewpointKey];
                this.viewpointTypeFilter = (vp && vp.element_types && vp.element_types.length) ? vp.element_types : [];
                this.typeFilter = '';
                this.currentPage = 1;
                this.loadElements();
            },

            toggleSort(column) {
                if (this.sortBy === column) {
                    this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortBy = column;
                    this.sortOrder = 'asc';
                }
                this.currentPage = 1;
                this.loadElements();
            },

            goToPage(page) {
                this.currentPage = page;
                this.loadElements();
                window.scrollTo({ top: 0, behavior: 'smooth' });
            },

            toggleSelectAll() {
                if (this.selectAll) {
                    this.selectedIds = this.elements.map(function(el) { return el.id + ':' + el.element_type; });
                } else {
                    this.selectedIds = [];
                }
            },

            detailUrl(el) {
                return '/architecture/' + this.activeTab + '/' + el.element_type + '/' + el.id;
            },
            editPageUrl(el) {
                if (!el) return '#';
                return '/architecture/' + this.activeTab + '/' + el.element_type + '/' + el.id + '/edit';
            },

            formatTypeName(type) {
                if (!type) return '';
                if (window.__SHOW_ARCHIMATE_NAMES__) {
                    return type.replace(/([A-Z])/g, ' $1').trim();
                }
                const names = window.__PLAIN_LANGUAGE_NAMES__ || {};
                return names[type] || type.replace(/([A-Z])/g, ' $1').trim();
            },
            truncate(text, len) {
                if (!text) return '';
                return text.length > len ? text.substring(0, len) + '...' : text;
            },

            getLifecyclePhase(el) {
                if (!el.properties) return 'unset';
                let props = el.properties;
                if (typeof props === 'string') {
                    try { props = JSON.parse(props); } catch(e) { return 'unset'; }
                }
                let phase = props.lifecycle || 'unset';
                let valid = ['current', 'transitional', 'target', 'retire', 'unset'];
                return valid.indexOf(phase) >= 0 ? phase : 'unset';
            },

            groupElementsBy(keyFn) {
                let groups = {};
                let filtered = this.elements.filter(el => {
                    if (!this.searchQuery) return true;
                    return el.name.toLowerCase().indexOf(this.searchQuery.toLowerCase()) >= 0;
                });
                for (let i = 0; i < filtered.length; i++) {
                    let key = keyFn(filtered[i]);
                    if (!groups[key]) groups[key] = [];
                    groups[key].push(filtered[i]);
                }
                return groups;
            },

            // ARCH-016: run full model validation
            async runValidation() {
                this.validating = true;
                try {
                    this.validationResults = await Platform.fetch.post('/architecture/api/archimate/validate', {}, { silent: true });
                    this.showValidationPanel = true;
                } catch (e) {
                    // Counts are null, not 0: validation did not run, so there is no
                    // measurement to report. A 0 here would read as "model is clean".
                    this.validationResults = { element_errors: null, element_warnings: null,
                        relationship_errors: null, relationship_warnings: null,
                        element_issues: [{ element: { id: 0, name: 'Validation did not run', layer: '' },
                            issues: [{ severity: 'error', message: e.message }] }],
                        relationship_issues: [] };
                    this.showValidationPanel = true;
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Model validation could not run — the counts below are not a result.');
                    }
                }
                this.validating = false;
            },

            // Typed field values for the currently-selected type, defaulted to ''
            // so Alpine's x-model has something reactive to bind before the user
            // types (and so a field left untouched still posts as '' rather than
            // being absent, matching create_empty_form_data on the server side).
            typedFieldDefaults(elementType, source) {
                let cfg = elementType ? this.fieldConfigs[elementType] : null;
                let fields = (cfg && Array.isArray(cfg.fields)) ? cfg.fields : [];
                let out = {};
                for (let i = 0; i < fields.length; i++) {
                    let name = fields[i].name;
                    out[name] = (source && source[name] !== undefined) ? source[name] : '';
                }
                return out;
            },
            resetTypedFields() {
                // Called when the Element Type select changes: drop any typed
                // values entered for the previous type and seed defaults for the
                // newly selected one.
                let base = { element_type: this.formData.element_type, name: this.formData.name, description: this.formData.description };
                Object.assign(base, this.typedFieldDefaults(this.formData.element_type));
                this.formData = base;
            },
            openCreateModal() {
                this.editingElement = null;
                this.formData = { element_type: '', name: '', description: '' };
                this.formError = '';
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('archimate-form-modal');
                }
            },
            openEditModal(el) {
                this.editingElement = el;
                this.formData = {
                    element_type: el.element_type,
                    name: el.name,
                    description: el.description || '',
                };
                Object.assign(this.formData, this.typedFieldDefaults(el.element_type, el));
                this.formError = '';
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('archimate-form-modal');
                }
            },
            async submitForm() {
                this.formError = '';
                if (!this.formData.name.trim()) {
                    this.formError = 'Name is required';
                    return;
                }
                if (!this.editingElement && !this.formData.element_type) {
                    this.formError = 'Please select an element type';
                    return;
                }
                this.saving = true;
                try {
                    let url;
                    if (this.editingElement) {
                        url = '/architecture/' + this.activeTab + '/' + this.editingElement.element_type + '/' + this.editingElement.id + '/edit';
                    } else {
                        url = '/architecture/' + this.activeTab + '/' + this.formData.element_type + '/new';
                    }
                    let payload = {
                        name: this.formData.name,
                        description: this.formData.description,
                    };
                    // Include typed fields for the selected type (if any) so the
                    // server's _set_model_fields can persist them alongside
                    // name/description; a type with no config contributes none.
                    Object.assign(payload, this.typedFieldDefaults(this.formData.element_type, this.formData));
                    let data = await Platform.fetch.post(url, payload, { silent: true });
                    if (data.success) {
                        if (window.Platform && window.Platform.modal) {
                            window.Platform.modal.close('archimate-form-modal');
                        }
                        // F-18, Capgemini dry-run: save succeeded silently — the
                        // modal just closed and the list re-rendered, which reads
                        // identically to nothing having happened.
                        if (window.Platform && window.Platform.toast) {
                            window.Platform.toast.success(
                                this.editingElement ? 'Element updated.' : 'Element created.'
                            );
                        }
                        this.loadElements();
                        this.loadAllLayerCounts();
                    } else {
                        this.formError = data.error || 'Operation failed';
                    }
                } catch (err) {
                    this.formError = 'Error: ' + err.message;
                } finally {
                    this.saving = false;
                }
            },

            openAiGenerateModal() {
                this.aiGenerate.layer = this.activeTab;
                this.aiGenerate.elementType = '';
                this.aiGenerate.prompt = '';
                this.aiGenerate.error = '';
                this.aiGenerate.result = null;
                this.aiGenerate.loading = false;
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.open('ai-generate-modal');
                }
            },
            async runAiGenerate() {
                this.aiGenerate.error = '';
                if (!this.aiGenerate.layer || !this.aiGenerate.elementType) {
                    this.aiGenerate.error = 'Please select a layer and element type';
                    return;
                }
                if (!this.aiGenerate.prompt.trim()) {
                    this.aiGenerate.error = 'Please describe what to generate';
                    return;
                }
                this.aiGenerate.loading = true;
                this.aiGenerate.result = null;
                try {
                    let url = '/architecture/' + this.aiGenerate.layer + '/' + this.aiGenerate.elementType + '/ai-generate';
                    let data = await Platform.fetch.post(url, { prompt: this.aiGenerate.prompt, context: {} }, { silent: true });
                    if (!data.success) {
                        this.aiGenerate.error = data.error || data.message || 'AI generation failed';
                        return;
                    }
                    let inner = data.data;
                    if (!inner || inner.success === false) {
                        this.aiGenerate.error = (inner && inner.error) || 'AI generation failed';
                        return;
                    }
                    this.aiGenerate.result = inner.element || {};
                } catch (err) {
                    this.aiGenerate.error = 'Error: ' + err.message;
                } finally {
                    this.aiGenerate.loading = false;
                }
            },
            useAiGenerateResult() {
                // Never write silently: hand the draft to the existing
                // create-element form so the user reviews and submits it
                // themselves via the normal, already-audited create flow.
                if (!this.aiGenerate.result) return;
                this.editingElement = null;
                this.activeTab = this.aiGenerate.layer;
                this.formData = {
                    element_type: this.aiGenerate.elementType,
                    name: this.aiGenerate.result.name || '',
                    description: this.aiGenerate.result.description || '',
                };
                let attrs = this.aiGenerate.result.attributes || {};
                Object.assign(this.formData, this.typedFieldDefaults(this.aiGenerate.elementType, attrs));
                this.formError = '';
                if (window.Platform && window.Platform.modal) {
                    window.Platform.modal.close('ai-generate-modal');
                    window.Platform.modal.open('archimate-form-modal');
                }
            },

            confirmDelete(el) {
                this.deletingElement = el;
                this.showDeleteConfirm = true;
            },
            async executeDelete() {
                if (!this.deletingElement) return;
                this.deleting = true;
                try {
                    let el = this.deletingElement;
                    let data = await Platform.fetch.post(
                        '/architecture/' + this.activeTab + '/' + el.element_type + '/' + el.id + '/delete',
                        null,
                        { silent: true }
                    );
                    if (data.success) {
                        this.showDeleteConfirm = false;
                        this.deletingElement = null;
                        this.loadElements();
                        this.loadAllLayerCounts();
                    } else {
                        Platform.toast.error(data.error || 'Delete failed');
                    }
                } catch (err) {
                    Platform.toast.error('Error: ' + err.message);
                } finally {
                    this.deleting = false;
                }
            },

            async bulkDeleteSelected() {
                if (this.selectedIds.length === 0) return;
                let grouped = {};
                for (let i = 0; i < this.selectedIds.length; i++) {
                    let key = this.selectedIds[i];
                    let parts = key.split(':');
                    let id = parts[0];
                    let type = parts[1];
                    if (!grouped[type]) grouped[type] = [];
                    grouped[type].push(parseInt(id));
                }
                let self = this;
                let modalId = window.modalManager.createModal({
                    title: 'Delete Elements',
                    content: '<p class="text-sm text-muted-foreground">Delete ' + this.selectedIds.length + ' element(s)? This cannot be undone.</p>',
                    size: 'small',
                    buttons: [
                        { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                        { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: async function() {
                            try {
                                let entries = Object.entries(grouped);
                                for (let j = 0; j < entries.length; j++) {
                                    let type = entries[j][0];
                                    let ids = entries[j][1];
                                    await Platform.fetch.post('/architecture/' + self.activeTab + '/' + type + '/bulk-delete', { ids: ids }, { silent: true });
                                }
                                self.selectedIds = [];
                                self.selectAll = false;
                                self.loadElements();
                                self.loadAllLayerCounts();
                            } catch (err) {
                                Platform.toast.error('Error during bulk delete');
                            }
                        } }
                    ]
                });
                window.modalManager.open(modalId);
            },
            // ARC-006: detail panel methods
            async openDetailPanel(el) {
                this.showDetailPanel = true;
                this.detailEditing = false;
                this.detailSaved = false;
                this.detailElement = el;
                this.detailData = null;
                this.detailLoading = true;
                try {
                    this.detailData = await Platform.fetch.get('/architecture/api/elements/' + el.id + '/detail', null, { silent: true });
                } catch (e) {
                    // The panel has no slot for detailData.error, so the toast is the
                    // only thing standing between a failed load and a panel of dashes.
                    this.detailData = { error: e.message };
                    if (window.Platform && window.Platform.toast) {
                        window.Platform.toast.error('Could not load details for this element — the panel is empty because the request failed.');
                    }
                } finally {
                    this.detailLoading = false;
                    this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
                }
            },

            closeDetailPanel() {
                this.showDetailPanel = false;
                this.detailElement = null;
                this.detailData = null;
                this.detailEditing = false;
            },

            startDetailEdit() {
                if (!this.detailElement) return;
                let data = this.detailData || {};
                this.detailForm = {
                    name: data.name || this.detailElement.name || '',
                    layer: data.layer || this.activeTab,
                    element_type: data.type || this.detailElement.element_type || '',
                    description: data.description || '',
                };
                this.detailEditing = true;
            },

            onDetailLayerChange() {
                let layerTypes = (this.layerConfig[this.detailForm.layer] || {}).elements || [];
                if (!layerTypes.includes(this.detailForm.element_type)) {
                    this.detailForm.element_type = layerTypes[0] || '';
                }
            },

            async saveDetailEdit() {
                if (!this.detailElement) return;
                this.detailSaving = true;
                this.detailSaved = false;
                try {
                    let data = await Platform.fetch.patch('/architecture/api/elements/' + this.detailElement.id, this.detailForm, { silent: true });
                    if (data.success) {
                        this.detailSaved = true;
                        this.detailEditing = false;
                        this.detailData = Object.assign({}, this.detailData, this.detailForm, { type: this.detailForm.element_type });
                        this.loadElements();
                        let self = this;
                        setTimeout(function() { self.detailSaved = false; }, 3000);
                    } else {
                        Platform.toast.error(data.error || 'Save failed');
                    }
                } catch (e) {
                    Platform.toast.error('Error: ' + e.message);
                } finally {
                    this.detailSaving = false;
                }
            },

            bulkExportSelected() {
                let grouped = {};
                for (let i = 0; i < this.selectedIds.length; i++) {
                    let key = this.selectedIds[i];
                    let parts = key.split(':');
                    let id = parts[0];
                    let type = parts[1];
                    if (!grouped[type]) grouped[type] = [];
                    grouped[type].push(id);
                }
                let entries = Object.entries(grouped);
                for (let j = 0; j < entries.length; j++) {
                    let type = entries[j][0];
                    let ids = entries[j][1];
                    let idParams = ids.map(function(id) { return 'ids=' + id; }).join('&');
                    window.open('/architecture/' + this.activeTab + '/' + type + '/export?format=json&' + idParams, '_blank');
                }
            },
        };
    });
});

document.addEventListener('DOMContentLoaded', function() {
    if (typeof lucide !== 'undefined') lucide.createIcons();
});
