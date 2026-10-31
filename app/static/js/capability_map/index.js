/**
 * Capability Map Application
 * Extracted from capability_map/index.html
 * Manages capability mapping, roadmap planning, and gap analysis
 */
(function() {
    'use strict';

    // DOM element cache — eliminates duplicate getElementById traversals.
    // getEl() lazily resolves and caches; invalidateEl() clears stale refs.
    const _domCache = new Map();
    function getEl(id) {
        let el = _domCache.get(id);
        if (!el) {
            el = document.getElementById(id);
            if (el) _domCache.set(id, el);
        }
        return el;
    }
    function invalidateEl(id) { _domCache.delete(id); }


    // Attribute-safe escaping. The global escapeHtml() round-trips through
    // textContent/innerHTML, which escapes < > & but NOT the double quote --
    // fine for a text node, unsafe for an attribute value. Every data-* value
    // written into a template literal below goes through this instead.
    function escapeAttr(value) {
        if (value === null || value === undefined) return '';
        return String(value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ---- Delegated action dispatcher ---------------------------------------
    //
    // The app ships script-src 'self' 'nonce-...' 'strict-dynamic' with no
    // 'unsafe-inline' and no 'unsafe-hashes', so an on*= attribute NEVER runs
    // -- innerHTML-injected ones included. (safeHTML's DOMPurify config also
    // lists onclick in FORBID_ATTR, so these were stripped before the CSP even
    // got a say.) Every control in this file therefore did nothing at all.
    //
    // Each is now `data-cm-action="<name>"` plus data-* arguments, dispatched
    // by ONE document-level listener. Delegation is required, not stylistic:
    // these grids are rebuilt wholesale from fetched data, so a listener bound
    // to a node dies with the next render.
    //
    // closest() picks the INNERMOST matching element, which is what the old
    // `event.stopPropagation()` calls were for (an icon button sitting inside
    // a clickable bar). One listener + closest() gives that for free.
    const CM_ACTIONS = {
        'retry-load':            (el) => retryLoadData(el.dataset.tab),
        'roadmap-retry':         ()   => { roadmapData.initialized = false; initRoadmapTab(); },
        'process-gap-retry':     ()   => { processGapData.loaded = false; loadProcessGapData(); },
        'expand-all-roadmap':    ()   => expandAllRoadmapRows(),
        'collapse-all-roadmap':  ()   => collapseAllRoadmapRows(),
        'toggle-row-expansion':  (el) => toggleRowExpansion(el.dataset.itemId),
        'toggle-wp-expansion':   (el) => toggleWorkPackageExpansion(el.dataset.wpId),
        'show-gap-details':      (el) => showGapDetails(el.dataset.itemId),
        'show-comments':         (el) => showCommentsPanel(el.dataset.itemId, el.dataset.itemType),
        'add-comment':           (el) => addComment(el.dataset.itemId, el.dataset.itemType),
        'delete-confirm':        (el) => openDeleteConfirmModal(
                                            el.dataset.itemId, el.dataset.itemType,
                                            el.dataset.itemName, parseInt(el.dataset.childCount, 10) || 0),
        'open-edit-wp':          (el) => openEditWPModal(el.dataset.wpId),
        'set-roadmap-view':      (el) => setRoadmapView(el.dataset.view),
        'open-convert-modal':    ()   => openConvertModal(),
        'load-persisted-roadmap':()   => loadPersistedRoadmapData(),
        'clear-filter':          (el) => clearFilter(el.dataset.field),
        'bulk-status-update':    ()   => executeBulkStatusUpdate(),
        'bulk-owner-update':     ()   => executeBulkOwnerUpdate(),
        'bulk-delete':           ()   => executeBulkDelete(),
        'save-dependency':       (el) => saveDependency(el.dataset.itemId),
        'quick-add-roadmap':     (el) => quickAddToRoadmap(el.dataset.capId, el.dataset.capName,
                                            'business', parseInt(el.dataset.capLevel, 10) || 1,
                                            el.dataset.capPriority),
        'add-to-roadmap':        (el) => addToRoadmap(el.dataset.capId, el.dataset.capName,
                                            'business', parseInt(el.dataset.capLevel, 10) || 1,
                                            el.dataset.capPriority),
        'open-mapping-modal':    (el) => openMappingModal(el.dataset.capId, el.dataset.capName),
        'toggle-row-selection':  (el) => {
            const tab = el.dataset.tab;
            const id = el.dataset.capId;
            if (!tableData[tab]) return;
            toggleRowSelection(id, tab, !tableData[tab].selected.has(id));
        },
        // openProcessMappingModal lives in index_inline.js, which loads after
        // this file; the name is resolved at click time, so order is fine.
        'open-process-mapping':  (el) => openProcessMappingModal(
                                            el.dataset.processId, el.dataset.processName,
                                            el.dataset.processCode, el.dataset.processType),
        'delete-mapping':        (el) => deleteMapping(el.dataset.mappingId, el.dataset.appId),
        'close-modal':           (el) => { const m = el.closest('.fixed'); if (m) m.remove(); },
        'dismiss-parent':        (el) => { if (el.parentElement) el.parentElement.remove(); },
    };

    function runCmAction(event) {
        const el = event.target.closest('[data-cm-action]');
        if (!el) return;
        const handler = CM_ACTIONS[el.getAttribute('data-cm-action')];
        if (!handler) return;
        handler(el, event);
    }

    document.addEventListener('click', runCmAction);

    // Delegated CHANGE handler — the Map Applications modal's checkboxes and the
    // per-application mapping settings (support level, coverage %, priority, ...)
    // used inline on*= handlers, which the strict CSP silently drops, so nothing
    // fired: the "N selected" counter stayed 0 and Save Mappings had an empty set.
    // Same delegation reason as CM_ACTIONS: the list is re-rendered wholesale.
    const CM_CHANGES = {
        // Table-row and work-package checkboxes carried inline onchange= handlers,
        // which the nonce + strict-dynamic CSP refuses to run: every render of a
        // capability row logged a CSP violation to the console and the checkbox
        // did nothing.
        'toggle-row-checkbox': (el) => toggleRowSelection(el.dataset.capId, el.dataset.tab, el.checked),
        'toggle-wp-child':    (el) => toggleWPChildSelection(parseInt(el.dataset.childId, 10), el.checked),
        'toggle-app':         (el) => toggleApplicationSelection(el.dataset.appId),
        'update-app-mapping': (el) => {
            const raw = el.value;
            const value = el.dataset.parse === 'int' ? parseInt(raw, 10) : raw;
            updateApplicationMapping(el.dataset.appId, el.dataset.field, value);
        },
    };
    document.addEventListener('change', function(event) {
        const el = event.target.closest('[data-cm-change]');
        if (!el) return;
        const handler = CM_CHANGES[el.getAttribute('data-cm-change')];
        if (handler) handler(el, event);
    });
    // Two of the controls are <div role="button"> -- absolutely positioned
    // gradient roadmap bars, not buttons -- so keyboard activation has to be
    // wired explicitly rather than inherited from the element.
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const el = event.target.closest('[data-cm-action][role="button"]');
        if (!el) return;
        event.preventDefault();
        runCmAction(event);
    });


    // Dynamic Domain Colors using string hash
    function getDomainColor(domain) {
        if (!domain) return 'bg-muted text-foreground';
    
        // Standard colors for common domains
        const standardColors = {
            'CUST': 'bg-primary/10 text-info-emphasis',
            'PROD': 'bg-sky-100 text-sky-800',
            'OPER': 'bg-amber-500/10 text-yellow-800',
            'FIN': 'bg-purple-100 text-purple-800',
            'RISK': 'bg-rose-100 text-rose-800',
            'DATA': 'bg-indigo-100 text-indigo-800',
            'PART': 'bg-pink-100 text-pink-800',
            'WORK': 'bg-orange-100 text-orange-800',
            'TECH': 'bg-muted text-foreground',
            'MFG': 'bg-orange-100 text-orange-800',
            'Manufacturing': 'bg-orange-100 text-orange-800',
            'Enterprise': 'bg-primary/10 text-info-emphasis',
            'Experience': 'bg-sky-100 text-sky-800',
            'Engineering': 'bg-amber-500/10 text-yellow-800',
            'Integration': 'bg-purple-100 text-purple-800',
            'Operations': 'bg-rose-100 text-rose-800',
            'Data': 'bg-indigo-100 text-indigo-800',
            'Governance': 'bg-pink-100 text-pink-800',
            'Analytics': 'bg-orange-100 text-orange-800',
            'Security': 'bg-rose-100 text-rose-800',
            'Quality': 'bg-purple-100 text-purple-800',
            'Mobility': 'bg-teal-100 text-teal-800',
            'Finance': 'bg-amber-500/10 text-yellow-800'
        };
    
        if (standardColors[domain]) return standardColors[domain];
    
        // Generate consistent color for unknown domains
        const colors = [
            'bg-primary/10 text-info-emphasis',
            'bg-sky-100 text-sky-800',
            'bg-amber-500/10 text-yellow-800',
            'bg-purple-100 text-purple-800',
            'bg-pink-100 text-pink-800',
            'bg-indigo-100 text-indigo-800',
            'bg-teal-100 text-teal-800',
            'bg-orange-100 text-orange-800'
        ];
    
        let hash = 0;
        for (let i = 0; i < domain.length; i++) {
            hash = domain.charCodeAt(i) + ((hash << 5) - hash);
        }
    
        return colors[Math.abs(hash) % colors.length];
    }
    
    function getTypeColor(type) {
        const colors = {
            'core': 'bg-primary/10 text-info-emphasis',
            'supporting': 'bg-muted text-foreground',
            'differentiating': 'bg-sky-100 text-sky-800'
        };
        return colors[type] || 'bg-muted text-foreground';
    }
    
    // Load data when page loads
    document.addEventListener('DOMContentLoaded', function() {
        lucide.createIcons();
        if (window.location.pathname.includes('/capability-roadmap')) {
            setTimeout(function() { initRoadmapTab(); }, 100);
        } else {
            loadDataForAllTabs();
        }
    });
    
    // Unified Table Data and Pagination System
    const tableData = {
        unified: { data: [], filtered: [], currentPage: 1, pageSize: 10, sortColumn: 'capability_name', sortDirection: 'asc', selected: new Set() },
        manufacturing: { data: [], filtered: [], currentPage: 1, pageSize: 10, sortColumn: 'capability_name', sortDirection: 'asc', selected: new Set() },
        application: { data: [], filtered: [], currentPage: 1, pageSize: 10, sortColumn: 'capability_name', sortDirection: 'asc', selected: new Set() },
        gap: { data: [], filtered: [], currentPage: 1, pageSize: 10, sortColumn: 'capability_name', sortDirection: 'asc', selected: new Set() }
    };
    
    // Phase 2-4 State Management
    const selectionState = {
        selectedCapabilities: new Set(),
        lastSelectedIndex: null
    };
    
    const undoStack = [];
    const UNDO_TIMEOUT = 10000; // 10 seconds
    
    const dragDropState = {
        draggedItem: null,
        draggedElement: null,
        dropTarget: null
    };
    
    // Track expansion state for gaps and work packages at all hierarchy levels
    // Format: { 'gap-123': true, 'wp-456': true, 'wp-789': false }
    const expandedRows = new Map();
    
    // Fetch with timeout and retry — wraps Platform.fetch with timeout protection for long API calls.
    async function fetchWithTimeout(url, options = {}, timeout = 30000) {
        // Platform.fetch already handles CSRF token injection, loading indicators, and error toasting.
        // We'll use a Promise.race to implement timeout on top of Platform.fetch.
        // Pass silent: true to prevent duplicate toasts from Platform.fetch when timeout occurs
        const fetchPromise = Platform.fetch(url, { ...options, silent: true })
            .catch(error => {
                // Re-throw the error so it can be caught by the caller
                throw error;
            });
        
        const timeoutPromise = new Promise((_, reject) => {
            setTimeout(() => {
                const timeoutMsg = 'Request timeout - server took too long to respond';
                reject(new Error(timeoutMsg));
            }, timeout);
        });

        try {
            const result = await Promise.race([fetchPromise, timeoutPromise]);
            return result;
        } catch (error) {
            // Show toast for timeout errors only
            if (error.message === 'Request timeout - server took too long to respond') {
                if (window.Platform && Platform.toast) Platform.toast.error(error.message);
            }
            throw error;
        }
    }
    
    // Display error in table
    function displayTableError(tabType, errorMessage, showRetry = true) {
        const tableBody = getEl(`${tabType}-table-body`);
        if (!tableBody) return;
    
        const retryButton = showRetry ?
            `<button type="button" data-cm-action="retry-load" data-tab="${escapeAttr(tabType)}" class="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
                <i data-lucide="refresh-cw" class="w-4 h-4 inline mr-2"></i>
                Retry
            </button>` : '';
    
        safeHTML(tableBody, `
            <tr>
                <td colspan="11" class="px-6 py-8 text-center">
                    <i data-lucide="alert-circle" class="w-8 h-8 text-destructive mx-auto mb-4"></i>
                    <p class="text-destructive font-medium mb-2">Error Loading Data</p>
                    <p class="text-sm text-muted-foreground">${escapeHtml(errorMessage)}</p>
                    ${retryButton}
                </td>
            </tr>
        `);
        lucide.createIcons();
    }
    
    // Retry loading data for specific tab
    async function retryLoadData(tabType) {
        const tableBody = getEl(`${tabType}-table-body`);
        if (tableBody) {
            safeHTML(tableBody, `
                <tr>
                    <td colspan="11" class="px-6 py-8 text-center text-muted-foreground">
                        <i data-lucide="loader-2" class="w-8 h-8 animate-spin text-primary mx-auto mb-4"></i>
                        <p>Retrying...</p>
                    </td>
                </tr>
            `);
            lucide.createIcons();
        }
        await loadDataForAllTabs();
    }
    
    // Load data for all tabs
    async function loadDataForAllTabs() {
        try {
            // Load unified data (source for all capability tabs)
            const unifiedData = await fetchWithTimeout('/capability-map/api/unified-capabilities');
            if (unifiedData.unified_capabilities || unifiedData.capabilities) {
                // Normalize unified array from possible keys
                const unifiedArr = unifiedData.unified_capabilities || unifiedData.capabilities || [];
                // Unified View: All capabilities
                tableData.unified.data = unifiedArr;
                tableData.unified.filtered = [...unifiedArr];
                populateDomainFilter('unified', unifiedArr);
    
                // Application: Only application capabilities (tolerant to different field names)
                tableData.application.data = unifiedArr.filter(item => item.type === 'Application' || item.capability_type === 'Application' || item.kind === 'Application');
                tableData.application.filtered = [...tableData.application.data];
                populateDomainFilter('application', tableData.application.data);
    
                // Manufacturing: Only manufacturing capabilities (support different shapes)
                tableData.manufacturing.data = unifiedArr.filter(item => item.type === 'Manufacturing' || item.capability_type === 'Manufacturing' || (item.domain && (item.domain.name === 'Manufacturing' || item.domain.code === 'MFG')));
                tableData.manufacturing.filtered = [...tableData.manufacturing.data];
                populateDomainFilter('manufacturing', tableData.manufacturing.data);
    
                // Gap Analysis: Only unmapped capabilities (support alternate flags)
                tableData.gap.data = unifiedArr.filter(item => (typeof item.is_mapped !== 'undefined' ? !item.is_mapped : !item.mapped));
                tableData.gap.filtered = [...tableData.gap.data];
                populateDomainFilter('gap', tableData.gap.data);
            }
    
            // Update all tables
            updateTable('unified');
            updateTable('application');
            updateTable('manufacturing');
            updateTable('gap');
    
            // Update Application tab metric cards and domain cards
            updateTabMetricCards('application', tableData.application.data);
            renderApplicationDomainCards(tableData.application.data);
            // Update Gap Analysis tab metric cards
            updateGapTabMetricCards(tableData.gap.data);
    
        } catch (error) {
            // Display error in all table bodies
            const errorMsg = error.message || 'Failed to load capability data. Please check your connection and try again.';
            displayTableError('unified', errorMsg);
            displayTableError('manufacturing', errorMsg);
            displayTableError('application', errorMsg);
            displayTableError('gap', errorMsg);
        }
    }
    
    // Populate domain filter dropdown
    function populateDomainFilter(tabType, data) {
        const domainFilter = getEl(`${tabType}-domain-filter`);
        if (domainFilter) {
            // The API returns the literal code "UNK" (and sometimes "Unknown")
            // for capabilities with no business domain set — show "Unassigned"
            // rather than the raw code, same normalisation already applied in
            // generateTableRow() below. This is a display-level fix only; the
            // option's value stays the raw code so filtering still matches
            // the underlying data.
            // Key on the code only where there IS a real one. This used to be
            // `code || name`, and the API returns the literal placeholder "UNK"
            // as a code for every capability with no business domain set -- so
            // the whole dropdown collapsed to a single "UNK" option while the
            // domain cards above it, which key on the NAME in that case, showed
            // Customer / Product / Operations / ... Clicking a card assigned
            // `select.value = "Customer"` to a <select> whose only option was
            // "UNK"; the browser silently resolves an unmatched assignment to
            // "" and the filter did nothing. Both surfaces must agree on the key.
            // filterTable() matches on `domain.code === v || domain.name === v`,
            // so a name is a valid filter value.
            const domainKey = (item) => {
                const code = item.domain?.code;
                if (code && code !== 'UNK' && code !== 'Unknown') return code;
                return item.domain?.name && item.domain.name !== 'Unknown'
                    ? item.domain.name : 'Unassigned';
            };
            const domains = [...new Set(data.map(domainKey).filter(Boolean))].sort();
            safeHTML(domainFilter, '<option value="">All Domains</option>');
            domains.forEach(domain => {
                domainFilter.innerHTML += `<option value="${escapeHtml(domain)}">${escapeHtml(domain)}</option>`; // safe: escapeHtml applied
            });
        }
    }
    
    // Delegated, because the CSP forbids inline handlers.
    //
    // These cards carried onclick="getEl('application-domain-filter')..." and
    // therefore did NOTHING for their entire life -- while wearing
    // cursor-pointer and a hover shadow, so they looked interactive. The owner
    // reported it as "what is the purpose of these if I cannot click them".
    //
    // The inline-handlers gate reported 0 the whole time: it scans templates,
    // and this handler is generated inside a JS template literal where it
    // cannot see it. Delegation is bound once, at document level, and survives
    // the grid being re-rendered.
    document.addEventListener('click', (event) => {
        const card = event.target.closest('[data-domain-filter]');
        if (!card) return;
        const select = getEl('application-domain-filter');
        if (!select) return;
        select.value = card.getAttribute('data-domain-filter');
        filterTable('application');
    });

    // Render Application Domain Cards from loaded data
    function renderApplicationDomainCards(data) {
        const grid = getEl('application-domains-grid');
        if (!grid || !data) return;
    
        // Group by domain
        const domainMap = {};
        data.forEach(item => {
            // Apps with no business domain set: show "Unassigned" rather than
            // the raw "UNK"/"Unknown" code (reads as a data error to users).
            // The API returns these as literal values, so normalise both the
            // null case and the explicit "UNK"/"Unknown" strings.
            let code = item.domain?.code || 'N/A';
            let name = item.domain?.name || 'Unassigned';
            if (code === 'UNK') code = 'N/A';
            if (name === 'Unknown') name = 'Unassigned';

            // Group by code where there IS one, otherwise by NAME.
            //
            // This used to key on `code` alone. Most capabilities carry a
            // business_domain name and no domain code, so every one of them
            // landed in the single 'N/A' bucket -- six distinct domains
            // (Customer, Product, Operations, Finance, Technology, People)
            // rendered as ONE card, labelled with whichever member happened to
            // arrive first. The screen said "Product: 30 capabilities" for an
            // estate that had six domains and five capabilities in Product.
            //
            // A constant fallback is never a grouping key: it merges everything
            // it touches and then names the merge after an arbitrary member.
            const key = (code !== 'N/A') ? code : name;
            if (!domainMap[key]) {
                domainMap[key] = { key, code, name, total: 0, mapped: 0 };
            }
            domainMap[key].total++;
            if (item.is_mapped) domainMap[key].mapped++;
        });
    
        const domains = Object.values(domainMap).sort((a, b) => b.total - a.total);
    
        if (domains.length === 0) {
            safeHTML(grid, '<div class="col-span-full text-center py-8 text-muted-foreground">No application domains found.</div>');
            return;
        }
    
        const domainColorSets = [
            { bg: 'bg-primary/5', border: 'border-primary/20', textBold: 'text-info-emphasis', textHead: 'text-info-emphasis', badge: 'bg-primary/10' },
            { bg: 'bg-layer-implementation/5', border: 'border-layer-implementation/30', textBold: 'text-layer-implementation-emphasis', textHead: 'text-layer-implementation-emphasis', badge: 'bg-layer-implementation/10' },
            { bg: 'bg-layer-motivation/5', border: 'border-layer-motivation/30', textBold: 'text-layer-motivation-emphasis', textHead: 'text-layer-motivation-emphasis', badge: 'bg-layer-motivation/10' },
            { bg: 'bg-layer-strategy/5', border: 'border-layer-strategy/30', textBold: 'text-layer-strategy-emphasis', textHead: 'text-layer-strategy-emphasis', badge: 'bg-layer-strategy/10' },
            { bg: 'bg-layer-business/5', border: 'border-layer-business/30', textBold: 'text-layer-business-emphasis', textHead: 'text-layer-business-emphasis', badge: 'bg-layer-business/10' },
            { bg: 'bg-layer-application/5', border: 'border-layer-application/30', textBold: 'text-layer-application-emphasis', textHead: 'text-layer-application-emphasis', badge: 'bg-layer-application/10' },
            { bg: 'bg-layer-risk/5', border: 'border-layer-risk/30', textBold: 'text-layer-risk-emphasis', textHead: 'text-layer-risk-emphasis', badge: 'bg-layer-risk/10' },
            { bg: 'bg-layer-technology/5', border: 'border-layer-technology/30', textBold: 'text-layer-technology-emphasis', textHead: 'text-layer-technology-emphasis', badge: 'bg-layer-technology/10' },
            { bg: 'bg-warning/5', border: 'border-warning/30', textBold: 'text-warning-emphasis', textHead: 'text-warning-emphasis', badge: 'bg-warning/10' },
        ];
    
        safeHTML(grid, domains.map((domain, index) => {
            const cs = domainColorSets[index % domainColorSets.length];
            const coverage = domain.total > 0 ? Math.round((domain.mapped / domain.total) * 100) : 0;
            const coverageColor = coverage >= 70 ? 'text-layer-implementation-emphasis' : coverage >= 40 ? 'text-warning-emphasis' : 'text-destructive-emphasis';
            const barColor = coverage >= 70 ? 'bg-layer-implementation-emphasis' : coverage >= 40 ? 'bg-warning' : 'bg-destructive';
            const statusColor = coverage >= 70 ? 'bg-layer-implementation-emphasis' : coverage >= 40 ? 'bg-warning' : 'bg-destructive';
    
            return `
                <button type="button"
                     class="${cs.bg} border-2 ${cs.border} rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer text-left w-full"
                     data-domain-filter="${escapeAttr(domain.key)}"
                     title="Filter capabilities to ${escapeHtml(domain.name)}">
                    <div class="flex items-start justify-between mb-3">
                        ${domain.code && domain.code !== 'N/A'
                            ? `<span class="text-xs font-bold ${cs.textBold} ${cs.badge} px-2 py-0.5 rounded">${escapeHtml(domain.code)}</span>`
                            : '<span></span>'}
                        <div class="w-2 h-2 rounded-full ${statusColor}"></div>
                    </div>
                    <h4 class="text-sm font-semibold ${cs.textHead} mb-1">${escapeHtml(domain.name)}</h4>
                    <div class="flex justify-between text-xs text-muted-foreground mt-2">
                        <span>${domain.total} capabilities</span>
                        <span class="font-medium ${coverageColor}">${coverage}%</span>
                    </div>
                    <div class="mt-2 w-full bg-border rounded-full h-1.5">
                        <div class="${barColor} h-1.5 rounded-full transition-all" style="width: ${coverage}%"></div>
                    </div>
                    <div class="mt-2 text-xs text-muted-foreground">
                        ${domain.mapped} mapped | ${domain.total - domain.mapped} gaps
                    </div>
                </button>
            `;
        }).join(''));
    }
    
    // Update Application tab metric cards from loaded data
    function updateTabMetricCards(tabType, data) {
        if (!data) return;
        const total = data.length;
        const mapped = data.filter(item => item.is_mapped || item.mapped).length;
        const unmapped = total - mapped;
        const coverage = total > 0 ? Math.round((mapped / total) * 100) : 0;
    
        const prefix = tabType === 'application' ? 'app' : tabType;
        const totalEl = getEl(`${prefix}-cap-count`);
        const mappedEl = getEl(`${prefix}-mapped-count`);
        const unmappedEl = getEl(`${prefix}-unmapped-count`);
        const coverageEl = getEl(`${prefix}-coverage`);
    
        if (totalEl) totalEl.textContent = total;
        if (mappedEl) mappedEl.textContent = mapped;
        if (unmappedEl) unmappedEl.textContent = unmapped;
        if (coverageEl) coverageEl.textContent = coverage + '%';
    }
    
    // Update Gap Analysis tab metric cards from loaded data
    function updateGapTabMetricCards(data) {
        if (!data) return;
        const total = data.length;
        const critical = data.filter(item => {
            const p = item.strategic_importance || '';
            return p === 'critical' || p === 'mission_critical';
        }).length;
        const high = data.filter(item => (item.strategic_importance || '') === 'high').length;
        const domains = new Set(data.map(item => item.domain?.code || item.domain?.name || 'Unassigned')).size;
    
        const totalEl = getEl('gap-total-count');
        const criticalEl = getEl('gap-critical-count');
        const highEl = getEl('gap-high-count');
        const domainEl = getEl('gap-domain-count');
    
        if (totalEl) totalEl.textContent = total;
        if (criticalEl) criticalEl.textContent = critical;
        if (highEl) highEl.textContent = high;
        if (domainEl) domainEl.textContent = domains;
    }
    
    // Unified filter function
    function filterTable(tabType) {
        const table = tableData[tabType];
        const levelFilterEl = getEl(`${tabType}-level-filter`);
        const domainFilterEl = getEl(`${tabType}-domain-filter`);
        const searchFilterEl = getEl(`${tabType}-search-filter`);
        const typeFilterEl = getEl(`${tabType}-type-filter`);
        const statusFilterEl = getEl(`${tabType}-status-filter`); // New status filter
    
        const levelFilter = levelFilterEl ? levelFilterEl.value : '';
        const domainFilter = domainFilterEl ? domainFilterEl.value : '';
        const searchFilter = searchFilterEl ? searchFilterEl.value.toLowerCase() : '';
        const typeFilter = typeFilterEl ? typeFilterEl.value : '';
        const statusFilter = statusFilterEl ? statusFilterEl.value : '';
    
        table.filtered = table.data.filter(item => {
            const levelMatch = !levelFilter || (item.level?.toString() === levelFilter || item.capability_level?.toString() === levelFilter);
            const domainMatch = !domainFilter || (item.domain?.code === domainFilter || item.domain?.name === domainFilter);
            const searchMatch = !searchFilter ||
                (item.name || item.capability_name || '').toLowerCase().includes(searchFilter) ||
                (item.application_name || '').toLowerCase().includes(searchFilter) ||
                (item.business_owner || '').toLowerCase().includes(searchFilter);
            const typeMatch = !typeFilter || item.type === typeFilter;
    
            // Status Filtering Logic
            let statusMatch = true;
            if (statusFilter === 'mapped') {
                statusMatch = item.is_mapped === true;
            } else if (statusFilter === 'unmapped') {
                statusMatch = !item.is_mapped;
            }
    
            return levelMatch && domainMatch && searchMatch && typeMatch && statusMatch;
        });
    
        table.currentPage = 1;
        updateTable(tabType);
    }
    
    // Unified clear filters function
    function clearFilters(tabType) {
        const levelFilter = getEl(`${tabType}-level-filter`);
        const domainFilter = getEl(`${tabType}-domain-filter`);
        const searchFilter = getEl(`${tabType}-search-filter`);
        const typeFilter = getEl(`${tabType}-type-filter`);
        const statusFilter = getEl(`${tabType}-status-filter`);
    
        if (levelFilter) levelFilter.value = '';
        if (domainFilter) domainFilter.value = '';
        if (searchFilter) searchFilter.value = '';
        if (typeFilter) typeFilter.value = '';
        if (statusFilter) statusFilter.value = '';
    
        filterTable(tabType);
    }
    
    // Unified sort function
    function sortTable(tabType, column) {
        const table = tableData[tabType];
        if (table.sortColumn === column) {
            table.sortDirection = table.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            table.sortColumn = column;
            table.sortDirection = 'asc';
        }
    
        table.filtered.sort((a, b) => {
            let aVal = a[column] || a[getAlternateField(column)] || '';
            let bVal = b[column] || b[getAlternateField(column)] || '';
    
            if (typeof aVal === 'string') {
                aVal = aVal.toLowerCase();
                bVal = bVal.toLowerCase();
            }
    
            if (table.sortDirection === 'asc') {
                return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
            } else {
                return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
            }
        });
    
        updateTable(tabType);
    }
    
    // Get alternate field names for compatibility
    function getAlternateField(column) {
        const fieldMap = {
            'capability_name': 'name',
            'business_owner': 'business_owner',
            'application_name': 'application_name',
            'business_impact': 'business_impact',
            'strategic_importance': 'strategic_importance',
            'coverage_percentage': 'coverage_percentage',
            'domain': 'domain',
            'level': 'level'
        };
        return fieldMap[column] || column;
    }
    
    // Unified update table function
    function updateTable(tabType) {
        const table = tableData[tabType];
        const tableBody = getEl(`${tabType}-table-body`);
        if (!tableBody) return;
    
        const startIndex = (table.currentPage - 1) * table.pageSize;
        const endIndex = startIndex + table.pageSize;
        const pageData = table.filtered.slice(startIndex, endIndex);
    
        if (pageData.length === 0) {
            safeHTML(tableBody, `
                <tr>
                    <td colspan="11" class="px-6 py-8 text-center">
                        <div class="text-center">
                            <i data-lucide="inbox" class="w-8 h-8 mx-auto mb-4 text-muted-foreground"></i>
                            <p class="text-muted-foreground">No ${tabType} capabilities found.</p>
                            <p class="text-sm text-muted-foreground">Try adjusting your filters.</p>
                        </div>
                    </td>
                </tr>
            `);
            updatePagination(tabType);
            return;
        }
    
        safeHTML(tableBody, pageData.map(item => generateTableRow(item, tabType)).join(''));
        updatePagination(tabType);
        lucide.createIcons();
    }
    
    // Generate table row HTML
    function generateTableRow(item, tabType = 'unified') {
        // Priority badge
        let priorityBadge = '';
        const priority = item.strategic_importance || '';
        if (priority === 'critical' || priority === 'mission_critical') {
            priorityBadge = '<span class="px-2 py-1 text-xs rounded-full bg-destructive text-primary-foreground font-bold">CRITICAL</span>';
        } else if (priority === 'high') {
            priorityBadge = '<span class="px-2 py-1 text-xs rounded-full bg-orange-500 text-primary-foreground font-semibold">HIGH</span>';
        } else if (priority === 'medium') {
            priorityBadge = '<span class="px-2 py-1 text-xs rounded-full bg-primary text-primary-foreground">MEDIUM</span>';
        } else {
            priorityBadge = '<span class="px-2 py-1 text-xs rounded-full bg-muted/50 text-primary-foreground">LOW</span>';
        }
    
        // Domain color
        let domainCode = item.domain?.name || item.domain?.code || 'Unassigned';
        if (domainCode === 'Unknown' || domainCode === 'UNK') domainCode = 'Unassigned';
        const domainColor = getDomainColor(domainCode);
    
        // Status badge
        const isMapped = item.is_mapped || false;
        const statusClasses = isMapped ? 'bg-sky-100 text-sky-800' : (item.type === 'Manufacturing' ? 'bg-amber-500/10 text-yellow-800' : 'bg-rose-100 text-rose-800');
        const statusText = isMapped ? 'Active' : 'Gap';
    
        const capabilityId = String(item.id || item.capability_id);
        const capabilityName = escapeHtml((item.name || item.capability_name || 'Unknown').replace(/'/g, "\\'"));
        const isSelected = tableData[tabType]?.selected?.has(capabilityId) || false;
    
        return `
            <tr class="cursor-pointer hover:bg-accent ${isSelected ? 'bg-primary/5' : ''}"
                data-cap-id="${capabilityId}"
                data-cap-name="${capabilityName}"
                data-tab-type="${tabType}"
                data-cap-level="${item.level || 1}"
                data-cap-priority="${priority}"
                draggable="true">
                <td class="px-6 py-4 whitespace-nowrap" data-no-row-click="true">
                    <input type="checkbox"
                        class="rounded border-input text-primary focus-visible:ring-ring"
                        ${isSelected ? 'checked' : ''}
                        aria-label="Select ${escapeHtml(item.name || item.capability_name || 'capability')}"
                        data-cm-change="toggle-row-checkbox" data-cap-id="${escapeAttr(capabilityId)}" data-tab="${escapeAttr(tabType)}">
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-foreground">
                    <div class="font-medium text-foreground">${escapeHtml(item.name || item.capability_name || 'Unknown')}</div>
                    ${item.source && item.source.indexOf('starter:') === 0 ? `<div class="text-xs font-normal text-muted-foreground" data-testid="capability-source">Starter capability from ${escapeHtml(item.source.slice(8))}, chosen at sign-up</div>` : ''}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <span class="px-2 py-1 text-xs rounded-full ${domainColor}">
                        ${escapeHtml(domainCode)}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <span class="px-2 py-1 text-xs rounded-full bg-muted text-foreground">
                        L${item.level || item.capability_level || 1}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <div class="text-sm">
                        <div class="font-medium">${escapeHtml(item.business_owner || 'Unassigned')}</div>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground cursor-pointer" data-open-mapping="true">
                    <div class="text-sm">
                        <div class="font-medium">${escapeHtml(item.application_name || 'No Application Mapped')}</div>
                        <div class="text-xs text-muted-foreground">${item.mapping_count > 1 ? '<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-500/10 text-primary border border-blue-500/30">+' + (item.mapping_count - 1) + ' more</span>' : escapeHtml(item.application_type || 'Application')}</div>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <div class="flex items-center">
                        <div class="w-full">
                            <div class="flex items-center justify-between mb-1">
                                <span class="text-xs font-medium">${item.business_impact || 0}%</span>
                            </div>
                            <div class="w-24 bg-border rounded-full h-2">
                                <div class="bg-gradient-to-r ${item.type === 'Manufacturing' ? 'from-orange-500 to-red-600' : 'from-blue-500 to-purple-600'} h-2 rounded-full" style="width: ${item.business_impact || 0}%"></div>
                            </div>
                        </div>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    ${priorityBadge}
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <div class="flex items-center">
                        <div class="w-16 bg-border rounded-full h-2 mr-2">
                            <div class="${isMapped ? 'bg-layer-implementation-emphasis' : 'bg-destructive'} h-2 rounded-full" style="width: ${item.coverage_percentage || (isMapped ? 100 : 0)}%"></div>
                        </div>
                        <span class="text-xs">${item.coverage_percentage || (isMapped ? 100 : 0)}%</span>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    <div class="flex items-center gap-2">
                        <span class="px-2 py-1 text-xs rounded-full ${statusClasses}">
                            ${escapeHtml(item.status || statusText)}
                        </span>
                        ${item.on_roadmap ? '<span class="px-2 py-1 text-xs rounded-full bg-purple-100 text-purple-800" title="On Roadmap"><i data-lucide="map" class="w-3 h-3 inline"></i></span>' : ''}
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground text-right">
                    <div class="relative inline-block cap-actions-wrap">
                        <button class="px-3 py-1 text-xs rounded-md bg-purple-100 text-purple-700 hover:bg-purple-200 transition-colors cap-actions-toggle" data-cap-actions-toggle="true">
                            Actions ▼
                        </button>
                        <div class="absolute right-0 mt-1 w-52 bg-card border rounded-md shadow-lg z-50 hidden cap-actions-menu">
                            <button data-action="view-detail" data-cap-id="${capabilityId}" data-cap-name="${capabilityName}" class="w-full text-left px-4 py-2 text-sm hover:bg-accent flex items-center gap-2">
                                <i data-lucide="eye" class="h-4 w-4 text-primary"></i> View Details
                            </button>
                            <button data-action="open-mapping" data-cap-id="${capabilityId}" data-cap-name="${capabilityName}" class="w-full text-left px-4 py-2 text-sm hover:bg-accent flex items-center gap-2">
                                <i data-lucide="layout-grid" class="h-4 w-4 text-primary"></i> Map to Applications
                            </button>
                            <button data-action="map-apqc" data-cap-id="${capabilityId}" data-cap-name="${capabilityName}" class="w-full text-left px-4 py-2 text-sm hover:bg-accent flex items-center gap-2">
                                <i data-lucide="git-branch" class="h-4 w-4 text-emerald-600"></i> Map to APQC Processes
                            </button>
                            <button data-action="map-archimate" data-cap-id="${capabilityId}" data-cap-name="${capabilityName}" class="w-full text-left px-4 py-2 text-sm hover:bg-accent flex items-center gap-2">
                                <i data-lucide="layers" class="h-4 w-4 text-orange-600"></i> Map to ArchiMate
                            </button>
                            <hr class="my-1 border-border">
                            <button data-action="add-roadmap" data-cap-id="${capabilityId}" data-cap-name="${capabilityName}" data-cap-type="business" data-cap-level="${item.level || 1}" data-cap-importance="${item.strategic_importance || 'medium'}" class="w-full text-left px-4 py-2 text-sm hover:bg-purple-50 flex items-center gap-2 ${item.on_roadmap ? 'opacity-50' : ''}">
                                <i data-lucide="map" class="h-4 w-4 text-primary"></i> ${item.on_roadmap ? 'Already on Roadmap' : 'Add to Roadmap'}
                            </button>
                        </div>
                    </div>
                </td>
            </tr>
        `;
    }
    
    // Unified pagination functions
    function updatePagination(tabType) {
        const table = tableData[tabType];
        const totalPages = Math.ceil(table.filtered.length / table.pageSize);
        const startRecord = (table.currentPage - 1) * table.pageSize + 1;
        const endRecord = Math.min(table.currentPage * table.pageSize, table.filtered.length);
    
        // Safely update elements
        const elements = [
            `${tabType}-start-record`, `${tabType}-end-record`, `${tabType}-total-records`,
            `${tabType}-current-page`, `${tabType}-total-pages`,
            `${tabType}-prev-btn`, `${tabType}-next-btn`
        ];
    
        elements.forEach(elId => {
            const el = document.getElementById(elId);
            if (el) {
                if (elId.includes('start-record')) el.textContent = startRecord;
                else if (elId.includes('end-record')) el.textContent = endRecord;
                else if (elId.includes('total-records')) el.textContent = table.filtered.length;
                else if (elId.includes('current-page')) el.textContent = table.currentPage;
                else if (elId.includes('total-pages')) el.textContent = totalPages;
                else if (elId.includes('prev-btn')) el.disabled = table.currentPage === 1;
                else if (elId.includes('next-btn')) el.disabled = table.currentPage === totalPages;
            }
        });
    }
    
    function previousPage(tabType) {
        const table = tableData[tabType];
        if (table.currentPage > 1) {
            table.currentPage--;
            updateTable(tabType);
        }
    }
    
    function nextPage(tabType) {
        const table = tableData[tabType];
        const totalPages = Math.ceil(table.filtered.length / table.pageSize);
        if (table.currentPage < totalPages) {
            table.currentPage++;
            updateTable(tabType);
        }
    }
    
    function changePageSize(tabType) {
        const table = tableData[tabType];
        const pageSizeSelect = getEl(`${tabType}-page-size`);
        if (pageSizeSelect) {
            table.pageSize = parseInt(pageSizeSelect.value);
            table.currentPage = 1;
            updateTable(tabType);
        }
    }
    
    // Export functions — uses raw fetch for blob download (not JSON)
    async function exportData(format) {
        try {
            // raw-fetch-ok: blob download requires Response object, not parsed body
            const response = await fetch(`/capability-map/api/export-mappings?format=${format}`, {  // raw-fetch-ok: blob download; needs the raw Response
                credentials: 'include'
            });
            if (!response.ok) {
                throw new Error('Export failed: HTTP ' + response.status);
            }
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `capability-map.${format}`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } catch (error) {
            if (window.Platform && Platform.toast) Platform.toast.error('Error exporting data: ' + (error.message || 'Unknown error'));
        }
    }
    
    // Toggle export menu
    function toggleExportMenu() {
        const menu = getEl('export-menu');
        menu.classList.toggle('hidden');
    }
    
    // Close export menu when clicking outside
    document.addEventListener('click', function(event) {
        // getEl() returns null when the element is absent, and #export-menu is
        // absent from capability_map/index.html -- so this threw an uncaught
        // TypeError on EVERY click anywhere on the page. Harmless-looking
        // (nothing depended on it) but it aborted the listener and filled the
        // console with "Cannot read properties of null (reading 'classList')",
        // which is exactly the noise that hides a real failure.
        const menu = getEl('export-menu');
        if (!menu) return;
        const button = event.target.closest('button');
        // NOTE: `button.onclick` is always null under the app's CSP -- an
        // inline handler never becomes a property -- so this can no longer
        // recognise the toggle button and the menu closes on any click. That
        // is the existing behaviour and is left alone here; it only matters on
        // a page that actually renders #export-menu.
        if (!button || !button.onclick || !button.onclick.toString().includes('toggleExportMenu')) {
            menu.classList.add('hidden');
        }
    });

    // ── Capability Actions dropdown (event delegation) ──────────────────────
    // Uses data-* attributes + CSS .hidden instead of Alpine x-data/x-show,
    // because DOMPurify strips Alpine directives from dynamically inserted HTML.
    document.addEventListener('click', function(event) {
        const toggleBtn = event.target.closest('[data-cap-actions-toggle]');

        // Close all open menus first (click-outside behavior)
        document.querySelectorAll('.cap-actions-menu:not(.hidden)').forEach(function(m) {
            if (!toggleBtn || m.parentElement !== toggleBtn.parentElement) {
                m.classList.add('hidden');
            }
        });

        // Toggle the clicked menu
        if (toggleBtn) {
            event.stopPropagation();
            let menu = toggleBtn.parentElement.querySelector('.cap-actions-menu');
            if (menu) {
                const wasHidden = menu.classList.contains('hidden');
                menu.classList.toggle('hidden');
                // DEF-078, Capgemini dry-run: the menu was position:absolute
                // inside the table's overflow-x-auto scroll container, which
                // clips any descendant that would render past its edge — so
                // every menu item's text ("View Detai…", "Map to App…", "Add
                // to Roa…") was cut off. Reposition as position:fixed using
                // the toggle button's real viewport coordinates so the menu
                // escapes the table's clipping box entirely.
                if (wasHidden) {
                    const rect = toggleBtn.getBoundingClientRect();
                    menu.style.position = 'fixed';
                    menu.style.top = (rect.bottom + 4) + 'px';
                    menu.style.right = (window.innerWidth - rect.right) + 'px';
                    menu.style.left = 'auto';
                    menu.style.marginTop = '0';
                }
            }
            return;
        }

        // Dispatch action from menu items
        let actionBtn = event.target.closest('[data-action]');
        if (!actionBtn) return;

        let action = actionBtn.getAttribute('data-action');
        let capId = actionBtn.getAttribute('data-cap-id');
        let capName = actionBtn.getAttribute('data-cap-name');

        // Close the menu
        const parentMenu = actionBtn.closest('.cap-actions-menu');
        if (parentMenu) parentMenu.classList.add('hidden');

        switch (action) {
            case 'view-detail':
                if (typeof openCapabilityDetail === 'function') openCapabilityDetail(capId, capName);
                break;
            case 'open-mapping':
                if (typeof openMappingModal === 'function') openMappingModal(capId, capName);
                break;
            case 'map-apqc':
                if (typeof openCapabilityAPQCMapping === 'function') openCapabilityAPQCMapping(capId, capName);
                break;
            case 'map-archimate':
                if (typeof openCapabilityArchimateMapping === 'function') openCapabilityArchimateMapping(capId, capName);
                break;
            case 'add-roadmap':
                if (typeof addToRoadmap === 'function') {
                    addToRoadmap(capId, capName, actionBtn.getAttribute('data-cap-type'),
                        parseInt(actionBtn.getAttribute('data-cap-level') || '1', 10),
                        actionBtn.getAttribute('data-cap-importance') || 'medium');
                }
                break;
        }
    });

    // Alpine.js component for tab management
    function capabilityMapTabs() {
        return {
            activeTab: new URLSearchParams(window.location.search).get('tab') || 'application',
            init() {
                // Initialize based on URL parameter or default
                const urlTab = new URLSearchParams(window.location.search).get('tab');
                if (urlTab) {
                    this.activeTab = urlTab;
                    this.onLensChange(urlTab);
                }
            },
            // Shared by the Lens dropdown's @change and init()'s ?tab= handling
            // — replaces the per-button @click handlers the old 11-button tab
            // row used to call directly.
            onLensChange(tab) {
                if (tab === 'roadmap') {
                    setTimeout(() => initRoadmapTab(), 100);
                } else if (tab === 'unified') {
                    setTimeout(() => loadBusinessDomainCards(), 100);
                } else if (tab === 'capability-model') {
                    setTimeout(() => loadCapabilityModelTab(), 100);
                } else if (tab === 'flow') {
                    setTimeout(() => window.dispatchEvent(new CustomEvent('load-sankey-0')), 100);
                } else if (tab === 'manufacturing') {
                    setTimeout(() => loadManufacturingDomainStats(), 100);
                } else if (tab === 'gaps') {
                    setTimeout(() => loadACMGapAnalysis(), 100);
                } else if (tab === 'process-gaps') {
                    setTimeout(() => { loadProcessGapData(); loadProcessCategoryStats(); }, 100);
                } else if (tab === 'technical') {
                    setTimeout(() => loadTechnicalTab(), 100);
                } else if (tab === 'heatmap') {
                    setTimeout(() => loadHeatMap(), 100);
                } else if (tab === 'maturity') {
                    setTimeout(() => loadMaturityRadarTab(), 100);
                }
            }
        }
    }
    
    // ============================================
    // ROADMAP TAB FUNCTIONS
    // ============================================
    
    let roadmapData = {
        items: [],
        filteredItems: [],
        timelinePeriods: [],
        displayMode: 'months',
        timelineStart: null,
        timelineEnd: null,
        timelineYears: 4,          // Configurable: 1, 2, 3, 4, 6, or 10 years
        timelineCustomStart: null,  // Custom start date (Date object or null)
        timelineCustomEnd: null,    // Custom end date (Date object or null)
        initialized: false,
        statistics: {},
        viewMode: 'auto',  // 'auto' for auto-detected gaps, 'persisted' for ArchiMate gaps
        persistedGaps: [],
        workPackages: []
    };
    
    // Current editing state
    let currentEditGapId = null;
    let currentEditWPId = null;
    
    // A design token as a #rrggbb string, for colours that are saved on a record or
    // handed to <input type="color">. Resolved when used so it follows the theme.
    function tokenHex(token) {
        return window.ArchieColorTokens.cssHex(token, 'muted-foreground');
    }

    // Gap type colors (token names, resolved by gapTypeColors())
    const GAP_TYPE_TOKENS = {
        'coverage': 'muted-foreground',
        'quality': 'warning',
        'retirement': 'destructive',
        'modernization': 'layer-motivation'
    };
    function gapTypeColors() {
        const out = {};
        Object.keys(GAP_TYPE_TOKENS).forEach(k => { out[k] = tokenHex(GAP_TYPE_TOKENS[k]); });
        return out;
    }
    
    // Initialize roadmap tab when clicked
    // MEASURED, 31 Aug 2026: this whole roadmap renderer no longer reaches the
    // screen on /capability-map/. The roadmap panel in
    // templates/capability_map/index.html is now the `roadmap_widget`
    // component with container_id='capability-roadmap', so its containers are
    // id="roadmap-timeline-capability-roadmap" etc. The functions below still
    // target the unsuffixed ids ("roadmap-labels", "roadmap-timeline") that no
    // template defines any more, so loadRoadmapData() fetches
    // /api/roadmap/gaps, hands the result to safeHTML(null, ...) and the
    // Platform logs "sanitize.html: target is not a DOM element null".
    //
    // Consequence: the controls this file renders for the roadmap (expand /
    // collapse all, gap-bar details, work-package edit and delete, convert,
    // set-view, comments, dependencies) are unreachable -- they were converted
    // off their dead onclick= attributes so they are correct if this renderer
    // is ever re-pointed at the widget's ids, but nothing renders them today.
    // Re-pointing them is a change to the widget contract, not to this file.
    function initRoadmapTab() {
        if (!roadmapData.initialized) {
            loadRoadmapData();
            roadmapData.initialized = true;
        }
    }
    
    // Set gap type filter from statistics card click
    function setGapTypeFilter(gapType) {
        const select = getEl('roadmap-gap-type-filter');
        if (select) {
            select.value = gapType;
            filterRoadmapItems();
        }
    }
    
    // Load comprehensive gap data for roadmap
    async function loadRoadmapData() {
        const container = getEl('roadmap-timeline-rows');
        safeHTML(container, `
            <div class="flex items-center justify-center h-64">
                <div class="text-center">
                    <i data-lucide="loader-2" class="w-8 h-8 animate-spin text-primary mx-auto mb-4"></i>
                    <p class="text-muted-foreground">Loading roadmap gap analysis...</p>
                </div>
            </div>
        `);
        lucide.createIcons();
    
        try {
            // Fetch comprehensive gap analysis from new API
            const data = await fetchWithTimeout('/capability-map/api/roadmap/gaps');
    
            if (data.success && data.gaps) {
                // Store statistics
                roadmapData.statistics = data.statistics || {};
    
                // Transform to roadmap items
                roadmapData.items = data.gaps.map(gap => ({
                    id: gap.id,
                    capability_id: gap.capability_id,
                    capability_type: gap.capability_type,
                    name: gap.name,
                    domain_name: gap.domain || 'Unassigned',
                    level: gap.level || 1,
                    parent_id: gap.parent_id || null,
                    parent_name: gap.parent_name || null,
                    hierarchy_path: gap.hierarchy_path || null,
                    priority: gap.priority || 'medium',
                    business_owner: gap.business_owner || 'Unassigned',
                    gap_types: gap.gap_types || [],
                    gap_details: gap.gap_details || [],
                    primary_gap: gap.primary_gap,
                    app_count: gap.app_count || 0,
                    applications: gap.applications || [],
                    start_date: gap.start_date,
                    end_date: gap.end_date,
                    strategic_importance: gap.strategic_importance || 'medium'
                }));
    
                roadmapData.filteredItems = [...roadmapData.items];
    
                // Calculate timeline range
                calculateTimelineRange();
    
                // Populate filters
                populateRoadmapDomainFilter();
                populateRoadmapParentFilter();
    
                // Update statistics
                updateRoadmapStats();
    
                // Render timeline
                renderRoadmapTimeline();
            }
        } catch (error) {
            const retryButton = `<button type="button" data-cm-action="roadmap-retry" class="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors">
                <i data-lucide="refresh-cw" class="w-4 h-4 inline mr-2"></i>
                Retry
            </button>`;
            safeHTML(container, `
                <div class="flex items-center justify-center h-64">
                    <div class="text-center">
                        <i data-lucide="alert-circle" class="w-8 h-8 text-destructive mx-auto mb-4"></i>
                        <p class="text-destructive font-medium mb-2">Error Loading Roadmap Data</p>
                        <p class="text-sm text-muted-foreground">${escapeHtml(error.message || 'Failed to load roadmap data. Please check your connection and try again.')}</p>
                        ${retryButton}
                    </div>
                </div>
            `);
            lucide.createIcons();
        }
    }
    
    // Get color based on primary gap type
    function getGapTypeColor(gapTypes) {
        const colors = gapTypeColors();
        if (!gapTypes || gapTypes.length === 0) return colors.coverage;
        // Priority: retirement > modernization > quality > coverage
        if (gapTypes.includes('retirement')) return colors.retirement;
        if (gapTypes.includes('modernization')) return colors.modernization;
        if (gapTypes.includes('quality')) return colors.quality;
        return colors.coverage;
    }
    
    // Get gap type badge HTML
    function getGapTypeBadges(gapTypes) {
        if (!gapTypes || gapTypes.length === 0) return '';
    
        const badgeConfig = {
            'coverage': { label: 'No Apps', bg: 'bg-muted', text: 'text-muted-foreground' },
            'quality': { label: 'Tactical', bg: 'bg-amber-500/10', text: 'text-amber-700' },
            'retirement': { label: 'Retiring', bg: 'bg-destructive/10', text: 'text-destructive' },
            'modernization': { label: 'Modernize', bg: 'bg-purple-100', text: 'text-purple-700' }
        };
    
        return gapTypes.map(type => {
            const config = badgeConfig[type] || { label: type, bg: 'bg-muted', text: 'text-muted-foreground' };
            return `<span class="px-1.5 py-0.5 text-xs rounded ${config.bg} ${config.text}">${config.label}</span>`;
        }).join(' ');
    }
    
    // Calculate timeline range based on configurable years or custom dates
    function calculateTimelineRange() {
        if (roadmapData.timelineCustomStart && roadmapData.timelineCustomEnd) {
            // Use custom date range if both are set
            roadmapData.timelineStart = new Date(roadmapData.timelineCustomStart);
            roadmapData.timelineEnd = new Date(roadmapData.timelineCustomEnd);
        } else {
            // Use year-based range (default: 4 years from Jan 1 of current year)
            const now = new Date();
            const years = roadmapData.timelineYears || 4;
            roadmapData.timelineStart = new Date(now.getFullYear(), 0, 1);
            roadmapData.timelineEnd = new Date(now.getFullYear() + years, 0, 1);
        }
    }

    // Set the timeline range to a preset number of years
    function setTimelineRange(years) {
        roadmapData.timelineYears = years;
        roadmapData.timelineCustomStart = null;
        roadmapData.timelineCustomEnd = null;

        // Update button states
        document.querySelectorAll('[data-timeline-range]').forEach(btn => {
            const btnYears = parseInt(btn.getAttribute('data-timeline-range'), 10);
            if (btnYears === years) {
                btn.classList.add('bg-card', 'shadow-sm');
                btn.classList.remove('hover:bg-card', 'hover:shadow-sm');
            } else {
                btn.classList.remove('bg-card', 'shadow-sm');
                btn.classList.add('hover:bg-card', 'hover:shadow-sm');
            }
        });

        // Clear custom date inputs if they exist
        const customStartInput = getEl('timeline-custom-start');
        const customEndInput = getEl('timeline-custom-end');
        if (customStartInput) customStartInput.value = '';
        if (customEndInput) customEndInput.value = '';

        calculateTimelineRange();
        updateRoadmapStats();
        renderRoadmapTimeline();

        showToast(`Timeline range: ${years} year${years > 1 ? 's' : ''}`, 'success');
    }

    // Set a custom start and end date for the timeline
    function setCustomTimelineRange() {
        const startInput = getEl('timeline-custom-start');
        const endInput = getEl('timeline-custom-end');

        if (!startInput || !endInput) return;

        const startVal = startInput.value;
        const endVal = endInput.value;

        if (!startVal || !endVal) {
            showToast('Please set both start and end dates', 'warning');
            return;
        }

        const startDate = new Date(startVal);
        const endDate = new Date(endVal);

        if (isNaN(startDate.getTime()) || isNaN(endDate.getTime())) {
            showToast('Invalid date format', 'error');
            return;
        }

        if (endDate <= startDate) {
            showToast('End date must be after start date', 'warning');
            return;
        }

        roadmapData.timelineCustomStart = startDate;
        roadmapData.timelineCustomEnd = endDate;

        // Deselect all preset buttons
        document.querySelectorAll('[data-timeline-range]').forEach(btn => {
            btn.classList.remove('bg-card', 'shadow-sm');
            btn.classList.add('hover:bg-card', 'hover:shadow-sm');
        });

        calculateTimelineRange();
        updateRoadmapStats();
        renderRoadmapTimeline();

        const startStr = startDate.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
        const endStr = endDate.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
        showToast(`Custom range: ${startStr} - ${endStr}`, 'success');
    }
    
    // Populate domain filter dropdown
    function populateRoadmapDomainFilter() {
        const select = getEl('roadmap-domain-filter');
        const domains = [...new Set(roadmapData.items.map(item => item.domain_name))].sort();
    
        safeHTML(select, '<option value="">All Domains</option>');
        domains.forEach(domain => {
            const option = document.createElement('option');
            option.value = domain;
            option.textContent = domain;
            select.appendChild(option);
        });
    }
    
    function populateRoadmapParentFilter() {
        const select = getEl('roadmap-parent-filter');
        if (!select) return;
    
        // Get unique parent capabilities from items
        // Group by type and collect unique parents
        const parentMap = new Map();
    
        roadmapData.items.forEach(item => {
            // Add items that could be parents (L1-L3 items)
            if (item.level <= 3) {
                const key = `${item.capability_type}-${item.capability_id}`;
                if (!parentMap.has(key)) {
                    parentMap.set(key, {
                        id: key,
                        capability_id: item.capability_id,
                        capability_type: item.capability_type,
                        name: item.name,
                        level: item.level,
                        domain: item.domain_name
                    });
                }
            }
    
            // Also add parent info if available
            if (item.parent_id && item.parent_name) {
                const parentKey = `${item.capability_type}-${item.parent_id}`;
                if (!parentMap.has(parentKey)) {
                    parentMap.set(parentKey, {
                        id: parentKey,
                        capability_id: item.parent_id,
                        capability_type: item.capability_type,
                        name: item.parent_name,
                        level: (item.level || 2) - 1,
                        domain: item.domain_name
                    });
                }
            }
        });
    
        // Sort parents by type, then by level, then by name
        const parents = Array.from(parentMap.values()).sort((a, b) => {
            if (a.capability_type !== b.capability_type) {
                return a.capability_type.localeCompare(b.capability_type);
            }
            if (a.level !== b.level) {
                return a.level - b.level;
            }
            return a.name.localeCompare(b.name);
        });
    
        // Build dropdown with optgroups by type
        safeHTML(select, '<option value="">All Parents</option>');
    
        const typeLabels = {
            'business': 'Business Capabilities',
            'technical': 'Technical Capabilities (ACM)',
            'process': 'Processes (APQC)'
        };
    
        const groupedParents = {};
        parents.forEach(p => {
            if (!groupedParents[p.capability_type]) {
                groupedParents[p.capability_type] = [];
            }
            groupedParents[p.capability_type].push(p);
        });
    
        Object.keys(groupedParents).forEach(type => {
            const optgroup = document.createElement('optgroup');
            optgroup.label = typeLabels[type] || type;
    
            groupedParents[type].forEach(parent => {
                const option = document.createElement('option');
                option.value = parent.id;
                const levelPrefix = 'L' + parent.level + ': ';
                option.textContent = levelPrefix + parent.name;
                optgroup.appendChild(option);
            });
    
            select.appendChild(optgroup);
        });
    }
    
    // Update roadmap statistics
    function updateRoadmapStats() {
        const items = roadmapData.filteredItems;
        const stats = roadmapData.statistics;
    
        // Total gaps (filtered) — each element is guarded, not every roadmap
        // stat badge exists in every render of this view (17 Aug 2026 QA
        // finding: "Cannot set properties of null" — these five ran
        // unguarded while the ones below already checked for null).
        const gapCountEl = getEl('roadmap-gap-count');
        if (gapCountEl) gapCountEl.textContent = items.length;

        // Update item count badges
        const visibleCountEl = getEl('roadmap-visible-count');
        if (visibleCountEl) visibleCountEl.textContent = items.length;
        const totalCountEl = getEl('roadmap-total-count');
        if (totalCountEl) totalCountEl.textContent = roadmapData.items.length;

        // Priority counts (filtered)
        const criticalCountEl = getEl('roadmap-critical-count');
        if (criticalCountEl) {
            criticalCountEl.textContent =
                items.filter(i => i.priority?.toLowerCase() === 'critical').length;
        }
        const highCountEl = getEl('roadmap-high-count');
        if (highCountEl) {
            highCountEl.textContent =
                items.filter(i => i.priority?.toLowerCase() === 'high').length;
        }
    
        // Gap type counts (from full statistics)
        const coverageEl = getEl('roadmap-coverage-count');
        const qualityEl = getEl('roadmap-quality-count');
        const retirementEl = getEl('roadmap-retirement-count');
        const modernizationEl = getEl('roadmap-modernization-count');
    
        if (coverageEl) coverageEl.textContent = stats.coverage_gaps || 0;
        if (qualityEl) qualityEl.textContent = stats.quality_gaps || 0;
        if (retirementEl) retirementEl.textContent = stats.retirement_gaps || 0;
        if (modernizationEl) modernizationEl.textContent = stats.modernization_gaps || 0;
    
        // Timeline range
        if (roadmapData.timelineStart && roadmapData.timelineEnd) {
            const startStr = roadmapData.timelineStart.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
            const endStr = roadmapData.timelineEnd.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
            getEl('roadmap-timeline-range').textContent = `${startStr} - ${endStr}`;
        }
    }
    
    // Update roadmap display mode
    function updateRoadmapDisplay() {
        roadmapData.displayMode = getEl('roadmap-display-mode').value;
        renderRoadmapTimeline();
    }
    
    // ============================================
    // SMART CASCADING FILTER FUNCTIONS
    // ============================================
    
    function onLevelFilterChange() {
        const levelFilter = getEl('roadmap-level-filter').value;
        const parentSelect = getEl('roadmap-parent-filter');
    
        // If a specific level is selected, filter parent dropdown to show only items at or above that level
        if (levelFilter && !levelFilter.includes('+')) {
            const selectedLevel = parseInt(levelFilter);
            // Update parent filter options to show only items at or above selected level
            updateParentFilterForLevel(selectedLevel);
        } else {
            // Reset parent filter to show all options
            populateRoadmapParentFilter();
        }
    
        filterRoadmapItems();
    }
    
    function onParentFilterChange() {
        const parentFilter = getEl('roadmap-parent-filter').value;
        const levelSelect = getEl('roadmap-level-filter');
        const includeChildrenCheckbox = getEl('roadmap-include-children');
    
        if (parentFilter) {
            // When a parent is selected, auto-set level to show children
            // Find the parent's level and set filter to show levels below it
            const parentItem = roadmapData.items.find(item =>
                `${item.capability_type}-${item.capability_id}` === parentFilter
            );
    
            if (parentItem && parentItem.level) {
                // Set level filter to show items at parent's level and below
                const parentLevel = parentItem.level;
                levelSelect.value = parentLevel + '+';
            }
    
            // Ensure include children is checked
            if (includeChildrenCheckbox) {
                includeChildrenCheckbox.checked = true;
            }
        }
    
        filterRoadmapItems();
    }
    
    function updateParentFilterForLevel(maxLevel) {
        const select = getEl('roadmap-parent-filter');
        if (!select) return;
    
        // Get current selection
        const currentValue = select.value;
    
        // Rebuild parent filter with only items at or above the max level
        const parentMap = new Map();
    
        roadmapData.items.forEach(item => {
            // Only include items that could be parents (at or above maxLevel - 1)
            if (item.level && item.level < maxLevel) {
                const key = `${item.capability_type}-${item.capability_id}`;
                if (!parentMap.has(key)) {
                    parentMap.set(key, {
                        id: key,
                        capability_id: item.capability_id,
                        capability_type: item.capability_type,
                        name: item.name,
                        level: item.level,
                        domain: item.domain_name
                    });
                }
            }
        });
    
        // Sort and rebuild dropdown
        const parents = Array.from(parentMap.values()).sort((a, b) => {
            if (a.capability_type !== b.capability_type) {
                return a.capability_type.localeCompare(b.capability_type);
            }
            if (a.level !== b.level) {
                return a.level - b.level;
            }
            return a.name.localeCompare(b.name);
        });
    
        safeHTML(select, '<option value="">All Parents</option>');
    
        const typeLabels = {
            'business': 'Business Capabilities',
            'technical': 'Technical Capabilities (ACM)',
            'process': 'Processes (APQC)'
        };
    
        const groupedParents = {};
        parents.forEach(p => {
            if (!groupedParents[p.capability_type]) {
                groupedParents[p.capability_type] = [];
            }
            groupedParents[p.capability_type].push(p);
        });
    
        Object.keys(groupedParents).forEach(type => {
            const optgroup = document.createElement('optgroup');
            optgroup.label = typeLabels[type] || type;
    
            groupedParents[type].forEach(parent => {
                const option = document.createElement('option');
                option.value = parent.id;
                const levelPrefix = 'L' + parent.level + ': ';
                option.textContent = levelPrefix + parent.name;
                optgroup.appendChild(option);
            });
    
            select.appendChild(optgroup);
        });
    
        // Restore selection if still valid
        if (currentValue && select.querySelector(`option[value="${currentValue}"]`)) {
            select.value = currentValue;
        }
    }
    
    // Filter roadmap items
    function filterRoadmapItems() {
        const priorityFilter = getEl('roadmap-priority-filter').value;
        const domainFilter = getEl('roadmap-domain-filter').value;
        const gapTypeFilter = getEl('roadmap-gap-type-filter')?.value || '';
        const capabilityTypeFilter = getEl('roadmap-capability-type-filter')?.value || '';
        const levelFilter = getEl('roadmap-level-filter')?.value || '';
        const parentFilter = getEl('roadmap-parent-filter')?.value || '';
        const includeChildren = getEl('roadmap-include-children')?.checked ?? true;
    
        roadmapData.filteredItems = roadmapData.items.filter(item => {
            // Priority filter
            const matchesPriority = !priorityFilter || item.priority?.toLowerCase() === priorityFilter;
    
            // Domain filter
            const matchesDomain = !domainFilter || item.domain_name === domainFilter;
    
            // Gap type filter
            const matchesGapType = !gapTypeFilter || (item.gap_types && item.gap_types.includes(gapTypeFilter));
    
            // Capability type filter
            const matchesCapType = !capabilityTypeFilter || item.capability_type === capabilityTypeFilter;
    
            // Level filter - now handles "X+" format for "X and below"
            let matchesLevel = true;
            if (levelFilter) {
                if (levelFilter.includes('+')) {
                    // "X+" means X and below (higher numbers)
                    const minLevel = parseInt(levelFilter.replace('+', ''));
                    matchesLevel = item.level >= minLevel;
                } else {
                    // Exact level match
                    matchesLevel = String(item.level) === levelFilter;
                }
            }
    
            // Parent filter - show item if it matches parent OR is a descendant of parent
            let matchesParent = true;
            if (parentFilter) {
                // Parse parent filter: "type-id" format (e.g., "business-5")
                const [parentType, parentId] = parentFilter.split('-');
                const parentIdNum = parseInt(parentId);
    
                // Check if this is the selected parent itself
                const isSelectedParent = (item.capability_id === parentIdNum && item.capability_type === parentType);
    
                // Check if this is a child/descendant of the selected parent
                const isChild = (
                    (item.parent_id === parentIdNum && item.capability_type === parentType) ||
                    (item.hierarchy_path && item.hierarchy_path.includes(parentFilter))
                );
    
                if (includeChildren) {
                    // Show parent and all children
                    matchesParent = isSelectedParent || isChild;
                } else {
                    // Show only direct children, not the parent itself
                    matchesParent = isChild && !isSelectedParent;
                }
            }
    
            return matchesPriority && matchesDomain && matchesGapType && matchesCapType && matchesLevel && matchesParent;
        });
    
        updateRoadmapStats();
        renderRoadmapTimeline();
    }
    
    // Render the roadmap timeline
    function renderRoadmapTimeline() {
        // Generate timeline periods using shared utility
        roadmapData.timelinePeriods = RoadmapUtils.generateTimelinePeriods(
            roadmapData.timelineStart,
            roadmapData.timelineEnd,
            roadmapData.displayMode
        );
    
        const columnWidth = RoadmapUtils.getColumnWidth(roadmapData.displayMode);
        const rowHeight = 64;
    
        // Render header
        const headerContainer = getEl('roadmap-timeline-header');
        safeHTML(headerContainer, RoadmapUtils.generateTimelineHeaderHTML(
            roadmapData.timelinePeriods,
            roadmapData.displayMode
        ));
    
        // Capability type icons
        const capTypeIcons = {
            'business': '📊',
            'technical': '⚙️',
            'process': '🔄'
        };
    
        // Render labels with gap type badges and expandable work packages
        const labelsContainer = getEl('roadmap-labels');
        safeHTML(labelsContainer, `
            <div class="h-12 px-4 flex items-center font-semibold text-muted-foreground border-b border-border">
                <button type="button" data-cm-action="expand-all-roadmap" class="mr-2 text-xs text-primary hover:text-primary/90 font-semibold">Expand All</button>
                <button type="button" data-cm-action="collapse-all-roadmap" class="mr-4 text-xs text-muted-foreground hover:text-foreground font-semibold">Collapse All</button>
                Capability / Gap Type
            </div>
            ${roadmapData.filteredItems.map(item => renderRoadmapLabel(item, capTypeIcons)).join('')}
        `);
    
        // Render timeline rows
        const rowsContainer = getEl('roadmap-timeline-rows');
    
        if (roadmapData.filteredItems.length === 0) {
            safeHTML(rowsContainer, `
                <div class="flex items-center justify-center h-64 text-muted-foreground">
                    <div class="text-center">
                        <i data-lucide="check-circle" class="w-8 h-8 text-emerald-500 mx-auto mb-4"></i>
                        <p>No capabilities with gaps found</p>
                        <p class="text-sm text-muted-foreground mt-1">Try adjusting your filters</p>
                    </div>
                </div>
            `);
            lucide.createIcons();
            return;
        }
    
        safeHTML(rowsContainer, roadmapData.filteredItems.map(item => {
            return renderTimelineRow(item, rowHeight, columnWidth);
        }).join(''));
    
        lucide.createIcons();
    }
    
    // ============================================
    // PHASE 4: EXPANDABLE ROWS & WORK PACKAGES
    // ============================================
    
    function renderRoadmapLabel(item, capTypeIcons) {
        const gapColor = getGapTypeColor(item.gap_types);
        const capIcon = capTypeIcons[item.capability_type] || '📋';
        const hasWorkPackages = item.work_packages && item.work_packages.length > 0;
        const gapKey = `gap-${item.id}`;
        const isExpanded = expandedRows.get(gapKey);
    
        // Calculate progress rollup
        const progress = calculateItemProgress(item);
        const progressColor = getProgressColor(progress);
    
        // Get status badge
        const status = item.status || 'not_started';
        const statusConfig = {
            'not_started': { label: 'Not Started', class: 'bg-muted text-muted-foreground', icon: 'circle' },
            'in_progress': { label: 'In Progress', class: 'bg-primary/10 text-primary', icon: 'clock' },
            'completed': { label: 'Complete', class: 'bg-emerald-500/10 text-emerald-700', icon: 'check-circle' },
            'blocked': { label: 'Blocked', class: 'bg-destructive/10 text-destructive', icon: 'alert-circle' }
        };
        const statusInfo = statusConfig[status] || statusConfig['not_started'];
    
        let html = `
            <div class="group relative min-h-[72px] px-3 py-2.5 flex items-start border-b border-border/50 hover:bg-accent transition-all" data-gap-id="${item.id}">
                <!-- Left border color indicator -->
                <div class="absolute left-0 top-0 bottom-0 w-1 transition-all" style="background-color: ${gapColor};"></div>
    
                <div class="flex items-start gap-3 w-full pl-2">
                    <!-- Expand/Collapse Icon Button -->
                    ${hasWorkPackages ? `
                        <button type="button" data-cm-action="toggle-row-expansion" data-item-id="${escapeAttr(item.id)}"
                                class="mt-0.5 flex-shrink-0 w-6 h-6 flex items-center justify-center rounded hover:bg-accent transition-colors"
                                title="${isExpanded ? 'Collapse' : 'Expand'} work packages">
                            <i data-lucide="${isExpanded ? 'chevron-down' : 'chevron-right'}" class="w-4 h-4 text-muted-foreground"></i>
                        </button>
                    ` : '<span class="w-6 flex-shrink-0"></span>'}
    
                    <!-- Gap Type Indicator with Tooltip -->
                    <div class="mt-1 w-3 h-3 rounded-full flex-shrink-0 shadow-sm"
                         style="background-color: ${gapColor};"
                         title="${item.gap_types?.join(', ') || 'Unknown'}">
                    </div>
    
                    <!-- Main Content -->
                    <div class="flex-1 min-w-0">
                        <!-- Title Row with Icon and Status -->
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-base">${capIcon}</span>
                            <span class="font-semibold text-foreground text-sm truncate" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
    
                            <!-- Status Badge -->
                            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusInfo.class}">
                                <i data-lucide="${statusInfo.icon}" class="w-3 h-3"></i>
                                ${statusInfo.label}
                            </span>
    
                            <!-- Work Package Count Badge -->
                            ${hasWorkPackages ? `
                                <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                                    <i data-lucide="package" class="w-3 h-3"></i>
                                    ${item.work_packages.length} WP
                                </span>
                            ` : ''}
    
                            <!-- Progress Percentage -->
                            ${hasWorkPackages ? `<span class="text-xs ${getProgressTextClass(progress)} font-bold">${progress}%</span>` : ''}
                        </div>
    
                        <!-- Domain and App Count -->
                        <div class="flex items-center gap-2 mt-1">
                            <span class="text-xs text-muted-foreground">${escapeHtml(item.domain_name)}</span>
                            ${item.app_count > 0 ? `
                                <span class="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                    <i data-lucide="layers" class="w-3 h-3"></i>
                                    ${item.app_count} app${item.app_count > 1 ? 's' : ''}
                                </span>
                            ` : '<span class="text-xs text-muted-foreground">No Apps</span>'}
                        </div>
    
                        <!-- Progress Bar or Gap Type Badges -->
                        ${hasWorkPackages ? `
                            <div class="w-full bg-border rounded-full h-2 mt-2 overflow-hidden">
                                <div class="${getProgressBarClass(progress)} h-2 rounded-full transition-all duration-300 shadow-sm" style="width: ${progress}%"></div>
                            </div>
                        ` : `
                            <div class="flex flex-wrap gap-1 mt-1.5">
                                ${getGapTypeBadges(item.gap_types)}
                            </div>
                        `}
                    </div>
                </div>
            </div>
        `;
    
        // Add work package child rows if expanded (hierarchical)
        if (hasWorkPackages && isExpanded) {
            html += renderWorkPackageLabels(item.work_packages, 1);
        }
    
        return html;
    }
    
    // Recursive function to render work package labels in the left column
    function renderWorkPackageLabels(workPackages, level) {
        let html = '';
    
        workPackages.forEach((wp, index) => {
            const wpKey = `wp-${wp.id}`;
            const hasChildren = wp.children && wp.children.length > 0;
            const isWpExpanded = expandedRows.get(wpKey);
            const indentPx = level * 28;
            const rowHeight = Math.max(48, 56 - (level * 4));
            const isLastChild = index === workPackages.length - 1;
    
            // Status configuration
            const status = wp.status || 'not_started';
            const statusConfig = {
                'not_started': { label: 'Not Started', class: 'bg-muted text-muted-foreground', icon: 'circle' },
                'in_progress': { label: 'In Progress', class: 'bg-primary/10 text-primary', icon: 'clock' },
                'completed': { label: 'Complete', class: 'bg-emerald-500/10 text-emerald-700', icon: 'check-circle' },
                'blocked': { label: 'Blocked', class: 'bg-destructive/10 text-destructive', icon: 'alert-circle' }
            };
            const statusInfo = statusConfig[status] || statusConfig['not_started'];
    
            // Visual hierarchy: connecting lines
            let hierarchyLines = '';
            for (let i = 1; i < level; i++) {
                hierarchyLines += `<div class="absolute bg-border" style="left: ${12 + (i * 28)}px; top: 0; bottom: 0; width: 1px;"></div>`;
            }
    
            html += `
                <div class="group relative px-3 py-2 flex items-center border-b border-gray-50 bg-indigo-50/40 hover:bg-indigo-50/60 transition-colors"
                     style="min-height: ${rowHeight}px; padding-left: ${12 + indentPx}px;"
                     data-wp-id="${wp.id}"
                     data-level="${level}">
                    ${hierarchyLines}
                    ${level > 0 ? `
                        <div class="absolute border-l border-b border-border" style="left: ${12 + ((level - 1) * 28)}px; top: 0; width: 24px; height: 50%;"></div>
                        ${!isLastChild ? `<div class="absolute bg-border" style="left: ${12 + ((level - 1) * 28)}px; top: 50%; bottom: 0; width: 1px;"></div>` : ''}
                    ` : ''}
                    <div class="flex items-center gap-2.5 w-full relative z-10">
                        ${hasChildren ? `
                            <button type="button" data-cm-action="toggle-wp-expansion" data-wp-id="${escapeAttr(wp.id)}"
                                    class="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded hover:bg-indigo-200 transition-colors"
                                    title="${isWpExpanded ? 'Collapse' : 'Expand'} sub-packages">
                                <i data-lucide="${isWpExpanded ? 'chevron-down' : 'chevron-right'}" class="w-3.5 h-3.5 text-indigo-700"></i>
                            </button>
                        ` : '<span class="w-5 flex-shrink-0"></span>'}
                        <span class="inline-flex items-center gap-1 text-xs font-bold text-indigo-700 bg-indigo-100 px-2 py-0.5 rounded-full shadow-sm">
                            L${level}
                        </span>
                        <i data-lucide="package" class="w-3.5 h-3.5 text-primary flex-shrink-0"></i>
                        <div class="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                            <span class="text-sm font-medium text-foreground truncate" title="${escapeHtml(wp.name)}">${escapeHtml(wp.name)}</span>
                            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${statusInfo.class}">
                                <i data-lucide="${statusInfo.icon}" class="w-3 h-3"></i>
                                ${statusInfo.label}
                            </span>
                            ${hasChildren ? `
                                <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                                    <i data-lucide="git-branch" class="w-3 h-3"></i>
                                    ${wp.children.length} sub
                                </span>
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
    
            // Recursively render children if expanded
            if (hasChildren && isWpExpanded) {
                html += renderWorkPackageLabels(wp.children, level + 1);
            }
        });
    
        return html;
    }
    
    function toggleRowExpansion(gapId) {
        const gapKey = `gap-${gapId}`;
        const currentState = expandedRows.get(gapKey);
        expandedRows.set(gapKey, !currentState);
        renderRoadmapTimeline();
    }
    
    
    function renderTimelineRow(item, rowHeight, columnWidth) {
        const barStyle = RoadmapUtils.getBarStyle(item, roadmapData.timelinePeriods, roadmapData.displayMode);
        const gapColor = getGapTypeColor(item.gap_types);
        const barColor = item.color || gapColor;
        const darkerBarColor = RoadmapUtils.darkenColor(barColor, 15);
    
        // Build tooltip with gap details
        const gapDetails = item.gap_details?.join('; ') || '';
        const tooltip = `${escapeHtml(item.name)}\nType: ${escapeHtml(item.capability_type)}\nGaps: ${escapeHtml(item.gap_types?.join(', '))}\n${escapeHtml(gapDetails)}\nPriority: ${escapeHtml(item.priority)}`;
    
        // Check if this is a persisted gap to show edit/delete affordance
        const isPersistedGap = roadmapData.viewMode === 'persisted' && item.is_persisted;
        const commentCount = (commentsData.get(`gap-${item.id}`) || []).length;
        const actionIcons = isPersistedGap ? `
            <button type="button" data-cm-action="show-comments" data-item-id="${escapeAttr(item.id)}" data-item-type="gap" class="mr-1 opacity-0 group-hover:opacity-70 transition-opacity" title="Comments${commentCount > 0 ? ` (${commentCount})` : ''}" aria-label="Comments on ${escapeAttr(item.name)}"><i data-lucide="message-square" class="w-4 h-4"></i></button>
            <i data-lucide="edit-2" class="w-4 h-4 mr-1 opacity-0 group-hover:opacity-70 transition-opacity"></i>
            <button type="button" data-cm-action="delete-confirm" data-item-id="${escapeAttr(item.id)}" data-item-type="gap" data-item-name="${escapeAttr(item.name)}" data-child-count="${item.work_package_count || 0}" class="mr-2 opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:text-red-200 transition-opacity" aria-label="Delete ${escapeAttr(item.name)}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
        ` : '';
    
        let html = `
            <div class="relative border-b border-border/50 hover:bg-blue-50/30" style="height: ${rowHeight}px;" data-gap-id="${item.id}">
                <!-- Grid columns -->
                <div class="absolute inset-0 flex">
                    ${roadmapData.timelinePeriods.map(() =>
                        `<div class="border-r border-border/50" style="width: ${columnWidth}px; min-width: ${columnWidth}px;"></div>`
                    ).join('')}
                </div>
                <!-- Bar -->
                <div class="absolute rounded-md flex items-center cursor-pointer hover:opacity-90 transition-opacity group"
                     style="left: ${barStyle.left}; width: ${barStyle.width}; top: 50%; transform: translateY(-50%); height: 44px; background: linear-gradient(135deg, ${barColor} 0%, ${darkerBarColor} 100%); box-shadow: 0 2px 4px rgba(0,0,0,0.15);"
                     title="${tooltip}${isPersistedGap ? '\n(Click to edit)' : ''}"
                     role="button" tabindex="0"
                     data-cm-action="show-gap-details" data-item-id="${escapeAttr(item.id)}"
                     aria-label="Open details for ${escapeAttr(item.name)}">
                    <span style="color: white; font-size: 14px; font-weight: 600; padding: 0 12px; white-space: nowrap; text-shadow: 0 1px 2px rgba(0,0,0,0.3); overflow: hidden; text-overflow: ellipsis; flex: 1;">${escapeHtml(item.name)}</span>
                    ${item.work_package_count ? `<span class="text-primary-foreground text-xs bg-background/20 px-2 py-0.5 rounded mr-2">${item.work_package_count} WP</span>` : ''}
                    ${actionIcons}
                </div>
            </div>
        `;
    
        // Add work package child rows if expanded (hierarchical)
        const hasWorkPackages = item.work_packages && item.work_packages.length > 0;
        const gapKey = `gap-${item.id}`;
        const isExpanded = expandedRows.get(gapKey);
    
        if (hasWorkPackages && isExpanded) {
            // Render work packages hierarchically with recursion
            html += renderWorkPackageHierarchy(item.work_packages, columnWidth, 1, item.id);
        }
    
        return html;
    }
    
    // Recursive function to render work package hierarchy with indentation
    function renderWorkPackageHierarchy(workPackages, columnWidth, level, parentId) {
        let html = '';
    
        workPackages.forEach(wp => {
            const wpKey = `wp-${wp.id}`;
            const wpBarStyle = RoadmapUtils.getBarStyle(wp, roadmapData.timelinePeriods, roadmapData.displayMode);
            const wpColor = wp.color || getColorByLevel(level);
            const wpDarkerColor = RoadmapUtils.darkenColor(wpColor, 15);
            const hasChildren = wp.children && wp.children.length > 0;
            const isWpExpanded = expandedRows.get(wpKey);
    
            // Calculate indentation and row styling based on level
            const indentPx = level * 24; // 24px per level
            const rowHeight = Math.max(40, 48 - (level * 4)); // Smaller rows for deeper levels
            const barHeight = Math.max(24, 32 - (level * 4));
            const bgOpacity = Math.max(10, 20 - (level * 3));
            const fontSize = Math.max(11, 12 - (level * 0.5));
    
            // Chevron for expandable work packages
            const chevron = hasChildren ? (isWpExpanded ? '▼' : '▶') : '';
            const chevronHtml = chevron ? `<button type="button" class="text-muted-foreground mr-1 cursor-pointer" data-cm-action="toggle-wp-expansion" data-wp-id="${escapeAttr(wp.id)}" aria-label="Toggle sub-packages of ${escapeAttr(wp.name)}">${chevron}</button>` : '';
    
            // Level indicator (L1, L2, L3, etc.)
            const levelBadge = `<span class="text-primary-foreground text-xs bg-white/30 px-1.5 py-0.5 rounded mr-1 font-semibold">L${level}</span>`;
    
            // Child count badge
            const childBadge = hasChildren ? `<span class="text-primary-foreground text-xs bg-background/20 px-1.5 py-0.5 rounded mr-1">${wp.children.length} sub</span>` : '';
    
            html += `
                <div class="relative border-b border-gray-50 hover:bg-blue-50/40" style="height: ${rowHeight}px; background: rgba(59, 130, 246, ${bgOpacity / 100});" data-wp-id="${wp.id}" data-parent-id="${parentId}" data-level="${level}">
                    <!-- Grid columns -->
                    <div class="absolute inset-0 flex">
                        ${roadmapData.timelinePeriods.map(() =>
                            `<div class="border-r border-border/50" style="width: ${columnWidth}px; min-width: ${columnWidth}px;"></div>`
                        ).join('')}
                    </div>
                    <!-- Work Package Bar with indentation -->
                    <div class="absolute rounded-md flex items-center cursor-pointer hover:opacity-90 transition-opacity group"
                         style="left: calc(${wpBarStyle.left} + ${indentPx}px); width: calc(${wpBarStyle.width} - ${indentPx}px); top: 50%; transform: translateY(-50%); height: ${barHeight}px; background: linear-gradient(135deg, ${wpColor} 0%, ${wpDarkerColor} 100%); box-shadow: 0 1px 3px rgba(0,0,0,0.1);"
                         title="Level ${level}: ${escapeHtml(wp.name)}\nStatus: ${escapeHtml(wp.status || 'Not Started')}\nProgress: ${wp.percent_complete || 0}%\nOwner: ${escapeHtml(wp.owner_name || 'Unassigned')}"
                         role="button" tabindex="0"
                         data-cm-action="open-edit-wp" data-wp-id="${escapeAttr(wp.id)}"
                         aria-label="Edit work package ${escapeAttr(wp.name)}">
                        ${chevronHtml}
                        ${levelBadge}
                        <span style="color: white; font-size: ${fontSize}px; font-weight: 500; padding: 0 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;">${escapeHtml(wp.name)}</span>
                        ${childBadge}
                        <span class="text-primary-foreground text-xs bg-background/20 px-1.5 py-0.5 rounded mr-1">${escapeHtml(wp.status || 'Not Started')}</span>
                        <button type="button" data-cm-action="delete-confirm" data-item-id="${escapeAttr(wp.id)}" data-item-type="work_package" data-item-name="${escapeAttr(wp.name)}" data-child-count="${hasChildren ? wp.children.length : 0}" class="mr-1.5 opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:text-red-200 transition-opacity" aria-label="Delete ${escapeAttr(wp.name)}"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>
                    </div>
                </div>
            `;
    
            // Recursively render children if expanded
            if (hasChildren && isWpExpanded) {
                html += renderWorkPackageHierarchy(wp.children, columnWidth, level + 1, wp.id);
            }
        });
    
        return html;
    }
    
    // Get color based on hierarchy level
    function getColorByLevel(level) {
        const tokens = {
            1: 'layer-application', // L1 (Major phases)
            2: 'success',           // L2 (Tasks)
            3: 'warning',           // L3 (Subtasks)
            4: 'layer-motivation',  // L4 (Activities)
            5: 'layer-risk'         // L5 (Steps)
        };
        return tokenHex(tokens[level] || 'muted-foreground');
    }
    
    // Toggle work package expansion
    function toggleWorkPackageExpansion(wpId) {
        const wpKey = `wp-${wpId}`;
        const currentState = expandedRows.get(wpKey);
        expandedRows.set(wpKey, !currentState);
        renderRoadmapTimeline();
    }
    
    // Show gap details - opens edit modal for persisted gaps, shows info popup for auto-detected
    function showGapDetails(gapId) {
        const item = roadmapData.items.find(i => i.id === gapId || i.id === parseInt(gapId));
        if (!item) return;
    
        // If viewing persisted roadmap and gap is persisted, open edit modal
        if (roadmapData.viewMode === 'persisted' && item.is_persisted) {
            openEditGapModal(item.id);
            return;
        }
    
        // For auto-detected gaps, show info popup with option to convert
        const apps = item.applications || [];
        const appList = apps.length > 0
            ? apps.slice(0, 5).map(a => `- ${a.name} (${a.lifecycle_status || 'Unknown'})`).join('\n')
            : 'No applications';
        const moreApps = apps.length > 5 ? `\n... and ${apps.length - 5} more` : '';
    
        const message = `Gap Details: ${item.name}
    
    Capability Type: ${item.capability_type}
    Domain: ${item.domain_name}
    Priority: ${item.priority}
    
    Gap Types: ${item.gap_types?.join(', ')}
    Details: ${item.gap_details?.join('; ') || 'None'}
    
    Applications (${item.app_count}):
    ${appList}${moreApps}
    
    To edit this gap, use "Convert to Roadmap" first.`;
    
        Platform.toast.info(message);
    }
    
    // Export roadmap
    async function exportRoadmap(format) {
        if (format === 'png' || format === 'jpg') {
            await RoadmapUtils.exportToImage(format, {
                items: roadmapData.filteredItems,
                timelinePeriods: roadmapData.timelinePeriods,
                displayMode: roadmapData.displayMode,
                title: 'Capability Gap Resolution Roadmap',
                subtitle: `${roadmapData.filteredItems.length} capabilities with gaps requiring attention`,
                getItemColor: (item) => RoadmapUtils.getPriorityColor(item.priority),
                filename: 'capability-gap-roadmap'
            });
        } else if (format === 'csv') {
            RoadmapUtils.exportToCSV(roadmapData.filteredItems, [
                { key: 'name', label: 'Capability Name' },
                { key: 'domain_name', label: 'Domain' },
                { key: 'level', label: 'Level' },
                { key: 'priority', label: 'Priority' },
                { key: 'business_owner', label: 'Business Owner' },
                { key: 'start_date', label: 'Target Start Date' },
                { key: 'end_date', label: 'Target End Date' },
                { key: 'gap_status', label: 'Gap Status' }
            ], 'capability-gap-roadmap');
        }
    }
    
    // ============================================
    // VIEW TOGGLE FUNCTIONS
    // ============================================
    
    function setRoadmapView(mode) {
        roadmapData.viewMode = mode;
    
        // Update button styles - use correct IDs
        const btnAuto = getEl('roadmap-view-auto');
        const btnPersisted = getEl('roadmap-view-saved');
        const hierarchyControls = getEl('hierarchy-controls');
    
        if (!btnAuto || !btnPersisted) {
            showToast('Unable to switch roadmap view: the view controls did not load. Please refresh the page.', 'error');
            return;
        }
    
        if (mode === 'auto') {
            btnAuto.className = 'px-3 py-1.5 text-sm rounded-md bg-background shadow-sm font-medium text-muted-foreground';
            btnPersisted.className = 'px-3 py-1.5 text-sm rounded-md font-medium text-muted-foreground hover:text-foreground';
    
            // Hierarchy controls now always visible for both modes
    
            loadRoadmapData();  // Load auto-detected gaps
        } else {
            btnAuto.className = 'px-3 py-1.5 text-sm rounded-md font-medium text-muted-foreground hover:text-foreground';
            btnPersisted.className = 'px-3 py-1.5 text-sm rounded-md bg-background shadow-sm font-medium text-muted-foreground';
    
            // Hierarchy controls now always visible for both modes
    
            loadPersistedRoadmapData();  // Load ArchiMate gaps
        }
    }
    
    async function loadPersistedRoadmapData() {
        const container = getEl('roadmap-timeline-rows');
        const skeletonRows = Array(5).fill(0).map(function() {
            return '' +
                '<div class="animate-pulse flex space-x-4 bg-muted/50 rounded-lg p-4">' +
                    '<div class="flex-1 space-y-3">' +
                        '<div class="h-4 bg-muted rounded w-3/4"></div>' +
                        '<div class="h-3 bg-muted rounded w-1/2"></div>' +
                    '</div>' +
                    '<div class="w-32 h-8 bg-muted rounded"></div>' +
                '</div>';
        }).join('');
    
        // Show skeleton loader instead of blank screen
        safeHTML(container, '' +
            '<div class="space-y-2 p-4">' +
                '<div class="flex items-center space-x-4 mb-6">' +
                    '<i data-lucide="loader-2" class="w-6 h-6 animate-spin text-primary"></i>' +
                    '<div>' +
                        '<p class="text-foreground font-medium">Loading saved roadmap...</p>' +
                        '<p class="text-sm text-muted-foreground">Please wait while we fetch your data</p>' +
                    '</div>' +
                '</div>' +
                skeletonRows +
            '</div>'
        );
        lucide.createIcons();
    
        try {
            const data = await fetchWithTimeout('/capability-map/api/roadmap/archimate-gaps');
    
            if (data.success) {
                roadmapData.persistedGaps = data.gaps || [];
    
                // Transform to roadmap items format
                roadmapData.items = roadmapData.persistedGaps.map(gap => ({
                    id: gap.id,
                    archimate_id: gap.archimate_id,
                    capability_id: gap.source_capability_id,
                    capability_type: gap.source_capability_type || 'unknown',
                    name: gap.name,
                    domain_name: gap.domain || 'Unassigned',
                    level: gap.level || 1,
                    parent_id: gap.parent_id || null,
                    parent_name: gap.parent_name || null,
                    hierarchy_path: gap.hierarchy_path || null,
                    priority: gap.priority || 'medium',
                    business_owner: gap.owner || 'Unassigned',
                    gap_types: gap.gap_types || [],
                    gap_details: [],
                    primary_gap: gap.gap_type,
                    app_count: 0,
                    applications: [],
                    start_date: gap.start_date,
                    end_date: gap.end_date,
                    color: gap.color,
                    resolution_status: gap.resolution_status,
                    work_package_count: gap.work_package_count || 0,
                    work_packages: gap.work_packages || [], // Include hierarchical work packages
                    is_persisted: true
                }));
    
                roadmapData.filteredItems = [...roadmapData.items];
                calculateTimelineRange();
                populateRoadmapDomainFilter();
                populateRoadmapParentFilter();
                populateOwnerFilter();
                updateRoadmapStats();
                renderRoadmapTimeline();
            }
        } catch (error) {
            // Check if it's an empty state (no data) vs actual error
            const isEmptyState = error.message && error.message.includes('No gaps found');
            if (!isEmptyState && window.Platform && Platform.toast) Platform.toast.error('Error loading persisted roadmap');
    
            if (isEmptyState || (roadmapData.persistedGaps && roadmapData.persistedGaps.length === 0)) {
                // Show empty state with call-to-action
                safeHTML(container, `
                    <div class="flex items-center justify-center h-96">
                        <div class="text-center max-w-md">
                            <div class="w-20 h-20 bg-purple-100 rounded-full flex items-center justify-center mx-auto mb-6">
                                <i data-lucide="inbox" class="w-10 h-10 text-primary"></i>
                            </div>
                            <h3 class="text-xl font-semibold text-foreground mb-2">No Saved Roadmap Yet</h3>
                            <p class="text-muted-foreground mb-6">
                                You haven't converted any capability gaps to your roadmap yet.
                                Start by analyzing auto-detected gaps and converting them to trackable roadmap items.
                            </p>
                            <div class="flex justify-center space-x-3">
                                <button type="button" data-cm-action="set-roadmap-view" data-view="auto" class="px-5 py-2.5 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 font-medium flex items-center">
                                    <i data-lucide="eye" class="w-4 h-4 mr-2"></i>
                                    View Auto-Detected Gaps
                                </button>
                                <button type="button" data-cm-action="open-convert-modal" class="px-5 py-2.5 bg-primary text-primary-foreground rounded-md hover:bg-purple-700 font-medium flex items-center">
                                    <i data-lucide="save" class="w-4 h-4 mr-2"></i>
                                    Convert Gaps
                                </button>
                            </div>
                        </div>
                    </div>
                `);
            } else {
                // Show error state with retry
                safeHTML(container, `
                    <div class="flex items-center justify-center h-96">
                        <div class="text-center max-w-md">
                            <div class="w-20 h-20 bg-destructive/10 rounded-full flex items-center justify-center mx-auto mb-6">
                                <i data-lucide="alert-circle" class="w-10 h-10 text-destructive"></i>
                            </div>
                            <h3 class="text-xl font-semibold text-foreground mb-2">Error Loading Saved Roadmap</h3>
                            <p class="text-muted-foreground mb-2">${escapeHtml(error.message || 'An unexpected error occurred')}</p>
                            <p class="text-sm text-muted-foreground mb-6">
                                This could be due to a network issue or server problem. Please try again.
                            </p>
                            <div class="flex justify-center space-x-3">
                                <button type="button" data-cm-action="load-persisted-roadmap" class="px-5 py-2.5 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 font-medium flex items-center">
                                    <i data-lucide="refresh-cw" class="w-4 h-4 mr-2"></i>
                                    Retry
                                </button>
                                <button type="button" data-cm-action="set-roadmap-view" data-view="auto" class="px-5 py-2.5 bg-secondary text-secondary-foreground rounded-md hover:bg-secondary/80 font-medium flex items-center">
                                    <i data-lucide="arrow-left" class="w-4 h-4 mr-2"></i>
                                    View Auto-Detected
                                </button>
                            </div>
                        </div>
                    </div>
                `);
            }
            lucide.createIcons();
        }
    }
    
    // ============================================
    // CONVERT MODAL FUNCTIONS
    // ============================================
    
    function openConvertModal() {
        const totalGaps = roadmapData.items.length;
        getEl('convert-total-count').textContent = totalGaps;
        getEl('convert-preview-gaps').textContent = totalGaps;
        Platform.modal.open('convert-modal');
        lucide.createIcons();
    }
    
    function closeConvertModal() {
        Platform.modal.close('convert-modal');
    }
    
    function toggleWPTemplateSection() {
        const checkbox = getEl('convert-create-wps');
        const templateSection = getEl('convert-template-section');
        const previewWPs = getEl('convert-preview-wps');
    
        if (checkbox.checked) {
            templateSection.style.display = 'block';
            previewWPs.style.display = 'flex';
        } else {
            templateSection.style.display = 'none';
            previewWPs.style.display = 'none';
        }
    }
    
    async function executeConvertGaps(event) {
        const createWPs = getEl('convert-create-wps').checked;
        const template = getEl('convert-wp-template').value;
    
        // Show loading state
        const btn = event?.target || event?.currentTarget || document.querySelector('[onclick*="executeConvertGaps"]');
        const originalText = btn.innerHTML;
        safeHTML(btn, '<i data-lucide="loader-2" class="w-4 h-4 mr-2 animate-spin"></i>Converting...');
        btn.disabled = true;
    
        try {
            const data = await Platform.fetch('/capability-map/api/roadmap/gaps/convert', {
                method: 'POST',
                body: {
                    gaps: roadmapData.items.map(item => ({
                        capability_id: item.capability_id,
                        capability_type: item.capability_type,
                        name: item.name,
                        domain: item.domain_name,
                        gap_types: item.gap_types,
                        gap_details: item.gap_details,
                        priority: item.priority,
                        strategic_importance: item.strategic_importance,
                        business_owner: item.business_owner,
                        applications: item.applications,
                        start_date: item.start_date,
                        end_date: item.end_date
                    })),
                    create_work_packages: createWPs,
                    work_package_template: template
                },
                silent: true
            });

            if (data.success) {
                closeConvertModal();
                showToast(`Converted ${data.created} gaps (${data.updated} updated)`, 'success');
                // Switch to persisted view
                setRoadmapView('persisted');
            } else {
                showToast(data.error || 'Conversion failed', 'error');
            }
        } catch (error) {
            showToast('Error converting gaps', 'error');
        } finally {
            safeHTML(btn, originalText);
            btn.disabled = false;
            lucide.createIcons();
        }
    }
    
    // ============================================
    // QUICK WIN FEATURES - EXPAND/COLLAPSE/NAVIGATION
    // ============================================
    
    // Expand all gaps and work packages in the roadmap
    function expandAllRoadmapRows() {
        // Expand all gaps
        roadmapData.items.forEach(item => {
            if (item.work_packages && item.work_packages.length > 0) {
                expandedRows.set(`gap-${item.id}`, true);
                // Recursively expand all work packages
                expandAllWorkPackages(item.work_packages);
            }
        });
        renderRoadmapTimeline();
        showToast('All items expanded', 'success');
    }
    
    // Recursively expand all work packages
    function expandAllWorkPackages(workPackages) {
        workPackages.forEach(wp => {
            if (wp.children && wp.children.length > 0) {
                expandedRows.set(`wp-${wp.id}`, true);
                expandAllWorkPackages(wp.children);
            }
        });
    }
    
    // Collapse all gaps and work packages in the roadmap
    function collapseAllRoadmapRows() {
        expandedRows.clear();
        renderRoadmapTimeline();
        showToast('All items collapsed', 'success');
    }
    
    // Jump to today's date on the timeline
    function jumpToToday() {
        const timelineContainer = getEl('roadmap-timeline-container');
        if (!timelineContainer) return;
    
        const today = new Date();
        today.setHours(0, 0, 0, 0);
    
        // Find the column that contains today
        const periods = roadmapData.timelinePeriods;
        let targetColumn = -1;
    
        for (let i = 0; i < periods.length; i++) {
            const period = periods[i];
            const periodStart = new Date(period.start);
            const periodEnd = new Date(period.end);
    
            if (today >= periodStart && today <= periodEnd) {
                targetColumn = i;
                break;
            }
        }
    
        if (targetColumn >= 0) {
            // Calculate scroll position
            const columnWidth = 120; // Match the column width used in rendering
            const scrollPosition = targetColumn * columnWidth - (timelineContainer.clientWidth / 2) + (columnWidth / 2);
    
            timelineContainer.scrollTo({
                left: Math.max(0, scrollPosition),
                behavior: 'smooth'
            });
    
            showToast('Jumped to today', 'success');
        } else {
            showToast('Today is not in the current timeline range', 'info');
        }
    }
    
    // Keyboard shortcuts for roadmap
    document.addEventListener('keydown', function(e) {
        // Ignore if user is typing in an input field
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            return;
        }
    
        // Check if we're on the roadmap view
        const roadmapSection = getEl('roadmap-view');
        if (!roadmapSection || roadmapSection.classList.contains('hidden')) {
            return;
        }
    
        // Ctrl+E: Expand All
        if (e.ctrlKey && e.key === 'e' && !e.shiftKey) {
            e.preventDefault();
            expandAllRoadmapRows();
        }
    
        // Ctrl+Shift+E: Collapse All
        if (e.ctrlKey && e.shiftKey && e.key === 'E') {
            e.preventDefault();
            collapseAllRoadmapRows();
        }
    
        // Ctrl+T: Jump to Today
        if (e.ctrlKey && e.key === 't') {
            e.preventDefault();
            jumpToToday();
        }
    
        // Ctrl+F: Focus search (if search field exists)
        if (e.ctrlKey && e.key === 'f') {
            const searchField = getEl('roadmap-search');
            if (searchField) {
                e.preventDefault();
                searchField.focus();
            }
        }
    
        // ?: Show keyboard shortcuts help
        if (e.key === '?' && !e.ctrlKey && !e.shiftKey && !e.altKey) {
            e.preventDefault();
            showKeyboardShortcutsHelp();
        }
    });
    
    // Show keyboard shortcuts help modal
    function showKeyboardShortcutsHelp() {
        const shortcuts = [
            { keys: 'Ctrl + E', description: 'Expand all items' },
            { keys: 'Ctrl + Shift + E', description: 'Collapse all items' },
            { keys: 'Ctrl + T', description: 'Jump to today' },
            { keys: 'Ctrl + F', description: 'Focus search' },
            { keys: '?', description: 'Show this help' },
            { keys: 'Esc', description: 'Close modals' }
        ];
    
        const shortcutsHtml = shortcuts.map(s =>
            `<div class="flex justify-between items-center py-2 border-b border-border/50">
                <kbd class="px-2 py-1 bg-muted border border-input rounded text-sm font-mono">${s.keys}</kbd>
                <span class="text-muted-foreground ml-4">${s.description}</span>
            </div>`
        ).join('');
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-md w-full p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-foreground flex items-center">
                        <i data-lucide="keyboard" class="w-5 h-5 mr-2 text-primary"></i>
                        Keyboard Shortcuts
                    </h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <div class="space-y-1">
                    ${shortcutsHtml}
                </div>
                <div class="mt-4 text-center">
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90">
                        Got it!
                    </button>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // ============================================
    // ADVANCED SEARCH & FILTER FUNCTIONS
    // ============================================
    
    // Toggle filter panel visibility
    function toggleFilterPanel() {
        const panel = getEl('filter-panel-content');
        const btn = getEl('filter-toggle-btn');
    
        if (panel.classList.contains('hidden')) {
            panel.classList.remove('hidden');
            safeHTML(btn, '<i data-lucide="chevron-up" class="w-4 h-4 inline mr-1"></i>Hide Filters');
        } else {
            panel.classList.add('hidden');
            safeHTML(btn, '<i data-lucide="chevron-down" class="w-4 h-4 inline mr-1"></i>Show Filters');
        }
        lucide.createIcons();
    }
    
    // Fuzzy search implementation
    function fuzzyMatch(text, search) {
        if (!search) return true;
    
        text = text.toLowerCase();
        search = search.toLowerCase();
    
        // Direct substring match (highest priority)
        if (text.includes(search)) return true;
    
        // Fuzzy match - all characters in order
        let searchIndex = 0;
        for (let i = 0; i < text.length && searchIndex < search.length; i++) {
            if (text[i] === search[searchIndex]) {
                searchIndex++;
            }
        }
        return searchIndex === search.length;
    }
    
    // Apply all filters to roadmap data
    function applyRoadmapFilters() {
        const searchTerm = getEl('roadmap-search')?.value || '';
        const statusFilter = getEl('filter-status')?.value || '';
        const priorityFilter = getEl('filter-priority')?.value || '';
        const ownerFilter = getEl('filter-owner')?.value || '';
        const levelFilter = getEl('filter-level')?.value || '';
        const dateFrom = getEl('filter-date-from')?.value || '';
        const dateTo = getEl('filter-date-to')?.value || '';
    
        // Filter items
        roadmapData.filteredItems = roadmapData.items.filter(item => {
            // Search filter (fuzzy match on name, domain, owner)
            if (searchTerm) {
                const searchableText = [
                    item.name || '',
                    item.domain_name || '',
                    item.business_owner || ''
                ].join(' ');
    
                if (!fuzzyMatch(searchableText, searchTerm)) {
                    return false;
                }
            }
    
            // Status filter
            if (statusFilter && item.status !== statusFilter) {
                return false;
            }
    
            // Priority filter
            if (priorityFilter && item.priority?.toLowerCase() !== priorityFilter) {
                return false;
            }
    
            // Owner filter
            if (ownerFilter && item.business_owner !== ownerFilter) {
                return false;
            }
    
            // Level filter
            if (levelFilter && item.level !== parseInt(levelFilter)) {
                return false;
            }
    
            // Date range filter
            if (dateFrom && item.start_date) {
                const itemDate = new Date(item.start_date);
                const filterDate = new Date(dateFrom);
                if (itemDate < filterDate) return false;
            }
    
            if (dateTo && item.end_date) {
                const itemDate = new Date(item.end_date);
                const filterDate = new Date(dateTo);
                if (itemDate > filterDate) return false;
            }
    
            return true;
        });
    
        // Update active filters display
        updateActiveFiltersDisplay();
    
        // Update stats and re-render
        updateRoadmapStats();
        renderRoadmapTimeline();
    }
    
    // Update active filters display
    function updateActiveFiltersDisplay() {
        const activeFiltersDisplay = getEl('active-filters-display');
        const activeFiltersTags = getEl('active-filters-tags');
    
        const filters = [];
    
        const searchTerm = getEl('roadmap-search')?.value;
        if (searchTerm) filters.push({ label: `Search: "${searchTerm}"`, field: 'search' });
    
        const status = getEl('filter-status')?.value;
        if (status) filters.push({ label: `Status: ${status}`, field: 'status' });
    
        const priority = getEl('filter-priority')?.value;
        if (priority) filters.push({ label: `Priority: ${priority}`, field: 'priority' });
    
        const owner = getEl('filter-owner')?.value;
        if (owner) filters.push({ label: `Owner: ${owner}`, field: 'owner' });
    
        const level = getEl('filter-level')?.value;
        if (level) filters.push({ label: `Level: L${level}`, field: 'level' });
    
        const dateFrom = getEl('filter-date-from')?.value;
        if (dateFrom) filters.push({ label: `From: ${dateFrom}`, field: 'date-from' });
    
        const dateTo = getEl('filter-date-to')?.value;
        if (dateTo) filters.push({ label: `To: ${dateTo}`, field: 'date-to' });
    
        if (filters.length > 0) {
            activeFiltersDisplay.classList.remove('hidden');
            safeHTML(activeFiltersTags, filters.map(f => `
                <span class="inline-flex items-center px-3 py-1 bg-primary/10 text-info-emphasis rounded-full text-sm">
                    ${f.label}
                    <button type="button" data-cm-action="clear-filter" data-field="${escapeAttr(f.field)}" aria-label="Clear filter" class="ml-2 hover:text-blue-900">
                        <i data-lucide="x" class="w-3 h-3"></i>
                    </button>
                </span>
            `).join(''));
            lucide.createIcons();
        } else {
            activeFiltersDisplay.classList.add('hidden');
        }
    }
    
    // Clear specific filter
    function clearFilter(field) {
        const fieldMap = {
            'search': 'roadmap-search',
            'status': 'filter-status',
            'priority': 'filter-priority',
            'owner': 'filter-owner',
            'level': 'filter-level',
            'date-from': 'filter-date-from',
            'date-to': 'filter-date-to'
        };
    
        const element = document.getElementById(fieldMap[field]);
        if (element) {
            element.value = '';
            applyRoadmapFilters();
        }
    }
    
    // Clear all filters
    function clearAllFilters() {
        const searchInput = getEl('roadmap-search');
        const gapTypeFilter = getEl('roadmap-gap-type-filter');
        const capabilityFilter = getEl('roadmap-capability-type-filter');
        const priorityFilter = getEl('roadmap-priority-filter');
        const statusFilter = getEl('filter-status');
        const domainFilter = getEl('roadmap-domain-filter');
        const levelFilter = getEl('roadmap-level-filter');
        const parentFilter = getEl('roadmap-parent-filter');
        const ownerFilter = getEl('filter-owner');
        const dateFrom = getEl('filter-date-from');
        const dateTo = getEl('filter-date-to');
    
        if (searchInput) searchInput.value = '';
        if (gapTypeFilter) gapTypeFilter.value = '';
        if (capabilityFilter) capabilityFilter.value = '';
        if (priorityFilter) priorityFilter.value = '';
        if (statusFilter) statusFilter.value = '';
        if (domainFilter) domainFilter.value = '';
        if (levelFilter) levelFilter.value = '';
        if (parentFilter) parentFilter.value = '';
        if (ownerFilter) ownerFilter.value = '';
        if (dateFrom) dateFrom.value = '';
        if (dateTo) dateTo.value = '';
    
        applyRoadmapFilters();
        showToast('All filters cleared', 'success');
    }
    
    // Apply filter presets
    function applyFilterPreset(preset) {
        clearAllFilters();
    
        const currentUser = '{{ current_user.username if current_user and current_user.is_authenticated else "" }}';
        const today = new Date().toISOString().split('T')[0];
    
        switch(preset) {
            case 'my-tasks':
                if (currentUser) {
                    const ownerFilter = getEl('filter-owner');
                    if (ownerFilter) ownerFilter.value = currentUser;
                }
                showToast('Showing your tasks', 'info');
                break;
    
            case 'overdue':
                const dateTo = getEl('filter-date-to');
                const statusOverdue = getEl('filter-status');
                if (dateTo) dateTo.value = today;
                if (statusOverdue) statusOverdue.value = 'in_progress';
                showToast('Showing overdue items', 'info');
                break;
    
            case 'critical-path':  // Filter to Critical Priority Items (priority-based, not CPM)
                const priorityFilter = getEl('roadmap-priority-filter');
                if (priorityFilter) priorityFilter.value = 'critical';
                showToast('Filtered to items with "Critical" priority level', 'info');
                break;
    
            case 'in-progress':
                const statusProgress = getEl('filter-status');
                if (statusProgress) statusProgress.value = 'in_progress';
                showToast('Showing in-progress items', 'info');
                break;
    
            case 'blocked':
                const statusBlocked = getEl('filter-status');
                if (statusBlocked) statusBlocked.value = 'blocked';
                showToast('Showing blocked items', 'info');
                break;
        }
    
        applyRoadmapFilters();
    }
    
    // Save current filters as preset
    async function saveCurrentFilters() {
        const filters = {
            search: getEl('roadmap-search')?.value || '',
            status: getEl('filter-status')?.value || '',
            priority: getEl('filter-priority')?.value || '',
            owner: getEl('filter-owner')?.value || '',
            level: getEl('filter-level')?.value || '',
            dateFrom: getEl('filter-date-from')?.value || '',
            dateTo: getEl('filter-date-to')?.value || ''
        };
    
        // Save to localStorage
        const presetName = await Platform.modal.promptText('Enter a name for this filter preset:', {
            title: 'Save filter preset'
        });
        if (presetName) {
            const savedPresets = JSON.parse(localStorage.getItem('roadmapFilterPresets') || '{}');
            savedPresets[presetName] = filters;
            localStorage.setItem('roadmapFilterPresets', JSON.stringify(savedPresets));
            showToast(`Filter preset "${presetName}" saved`, 'success');
        }
    }
    
    // Populate owner filter dropdown
    function populateOwnerFilter() {
        const ownerSelect = getEl('filter-owner');
        if (!ownerSelect) return;
    
        const owners = new Set();
        roadmapData.items.forEach(item => {
            if (item.business_owner) {
                owners.add(item.business_owner);
            }
            // Also check work packages for owners
            if (item.work_packages) {
                const extractOwners = (wps) => {
                    wps.forEach(wp => {
                        if (wp.owner_name) owners.add(wp.owner_name);
                        if (wp.children) extractOwners(wp.children);
                    });
                };
                extractOwners(item.work_packages);
            }
        });
    
        const currentOptions = ownerSelect.innerHTML;
        const newOptions = Array.from(owners).sort().map(owner =>
            `<option value="${owner}">${owner}</option>`
        ).join('');
    
        safeHTML(ownerSelect, '<option value="">All Owners</option>' + newOptions);
    }
    
    // ============================================
    // BULK OPERATIONS & SELECTION
    // ============================================
    
    // Track selected items for bulk operations
    const bulkSelectedItems = new Set();
    
    // Toggle item selection for bulk operations
    function toggleBulkSelection(itemId, itemType) {
        const key = `${itemType}-${itemId}`;
    
        if (bulkSelectedItems.has(key)) {
            bulkSelectedItems.delete(key);
        } else {
            bulkSelectedItems.add(key);
        }
    
        updateBulkSelectionUI();
    }
    
    // Select all visible items
    function selectAllVisibleItems() {
        roadmapData.filteredItems.forEach(item => {
            bulkSelectedItems.add(`gap-${item.id}`);
        });
        updateBulkSelectionUI();
        renderRoadmapTimeline();
    }
    
    // Clear bulk selection
    function clearBulkSelection() {
        bulkSelectedItems.clear();
        updateBulkSelectionUI();
        renderRoadmapTimeline();
    }
    
    // Update bulk selection UI
    function updateBulkSelectionUI() {
        const toolbar = getEl('bulk-actions-toolbar');
        const countSpan = getEl('bulk-selected-count');
    
        if (bulkSelectedItems.size > 0) {
            toolbar.classList.remove('hidden');
            toolbar.classList.add('flex');
            countSpan.textContent = bulkSelectedItems.size;
        } else {
            toolbar.classList.add('hidden');
            toolbar.classList.remove('flex');
        }
    
        lucide.createIcons();
    }
    
    // Bulk update status
    function bulkUpdateStatus() {
        if (bulkSelectedItems.size === 0) {
            showToast('No items selected', 'warning');
            return;
        }
    
        // Show modal for status selection
        const statuses = [
            { value: 'not_started', label: '📋 Not Started' },
            { value: 'planned', label: '🎯 Planned' },
            { value: 'in_progress', label: '🚀 In Progress' },
            { value: 'completed', label: '✅ Completed' },
            { value: 'blocked', label: '🚫 Blocked' },
            { value: 'cancelled', label: '❌ Cancelled' }
        ];
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-md w-full p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-foreground">Update Status</h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <p class="text-sm text-muted-foreground mb-4">
                    Update status for <strong>${bulkSelectedItems.size}</strong> selected items
                </p>
                <select id="bulk-status-select" class="w-full px-3 py-2 border border-input rounded-md mb-4">
                    ${statuses.map(s => `<option value="${s.value}">${s.label}</option>`).join('')}
                </select>
                <div class="flex justify-end space-x-3">
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="px-4 py-2 text-muted-foreground hover:bg-accent rounded-md">
                        Cancel
                    </button>
                    <button type="button" data-cm-action="bulk-status-update" class="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90">
                        Update Status
                    </button>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // Bulk status / owner / delete were never wired to an API: each one closed the
    // modal, cleared the selection and toasted "Successfully ...", so the user was
    // told a bulk edit -- including a bulk DELETE -- had been applied when nothing
    // had happened. Reporting the truth is the only correct behaviour until the
    // endpoints exist; the selection is deliberately left intact because nothing
    // was changed.
    function _bulkNotWired(action) {
        document.querySelector('.fixed')?.remove();
        showToast(`Bulk ${action} is not available yet — no changes were made.`, 'error');
    }

    // Execute bulk status update
    async function executeBulkStatusUpdate() {
        _bulkNotWired('status update');
    }
    
    // Bulk update owner
    function bulkUpdateOwner() {
        if (bulkSelectedItems.size === 0) {
            showToast('No items selected', 'warning');
            return;
        }
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-md w-full p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-foreground">Assign Owner</h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <p class="text-sm text-muted-foreground mb-4">
                    Assign owner for <strong>${bulkSelectedItems.size}</strong> selected items
                </p>
                <label for="bulk-owner-input">Field</label>
        <input type="text" id="bulk-owner-input" placeholder="Enter owner name..."
                       class="w-full px-3 py-2 border border-input rounded-md mb-4">
                <div class="flex justify-end space-x-3">
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="px-4 py-2 text-muted-foreground hover:bg-accent rounded-md">
                        Cancel
                    </button>
                    <button type="button" data-cm-action="bulk-owner-update" class="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-purple-700">
                        Assign Owner
                    </button>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // Execute bulk owner update
    async function executeBulkOwnerUpdate() {
        const ownerInput = getEl('bulk-owner-input');
        const newOwner = ownerInput.value.trim();
    
        if (!newOwner) {
            showToast('Please enter an owner name', 'warning');
            return;
        }
    
        _bulkNotWired('owner assignment');
    }
    
    // Bulk delete
    function bulkDelete() {
        if (bulkSelectedItems.size === 0) {
            showToast('No items selected', 'warning');
            return;
        }
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-md w-full p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-foreground text-destructive">
                        <i data-lucide="alert-triangle" class="w-5 h-5 inline mr-2"></i>
                        Confirm Bulk Delete
                    </h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <p class="text-sm text-muted-foreground mb-4">
                    Are you sure you want to delete <strong>${bulkSelectedItems.size}</strong> selected items?
                </p>
                <p class="text-sm text-destructive mb-4">
                    <strong>Warning:</strong> This action cannot be undone.
                </p>
                <div class="flex justify-end space-x-3">
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="px-4 py-2 text-muted-foreground hover:bg-accent rounded-md">
                        Cancel
                    </button>
                    <button type="button" data-cm-action="bulk-delete" class="px-4 py-2 bg-destructive text-primary-foreground rounded-md hover:bg-red-700">
                        Delete Items
                    </button>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // Execute bulk delete
    async function executeBulkDelete() {
        _bulkNotWired('delete');
    }
    
    // ============================================
    // PROGRESS TRACKING & ROLLUP CALCULATIONS
    // ============================================
    
    // Calculate progress for an item (gap or work package)
    function calculateItemProgress(item) {
        if (item.percent_complete !== undefined && item.percent_complete !== null) {
            return Math.round(item.percent_complete);
        }
    
        if (item.work_packages && item.work_packages.length > 0) {
            return calculateRollupProgress(item.work_packages);
        }
    
        if (item.status) {
            return estimateProgressFromStatus(item.status);
        }
    
        return 0;
    }
    
    // Calculate rollup progress from child work packages
    function calculateRollupProgress(workPackages) {
        if (!workPackages || workPackages.length === 0) return 0;
    
        let totalProgress = 0;
        let totalWeight = 0;
    
        workPackages.forEach(wp => {
            const weight = 1;
            totalWeight += weight;
    
            let wpProgress = 0;
    
            if (wp.percent_complete !== undefined && wp.percent_complete !== null) {
                wpProgress = wp.percent_complete;
            } else if (wp.children && wp.children.length > 0) {
                wpProgress = calculateRollupProgress(wp.children);
            } else if (wp.status) {
                wpProgress = estimateProgressFromStatus(wp.status);
            }
    
            totalProgress += wpProgress * weight;
        });
    
        return totalWeight > 0 ? Math.round(totalProgress / totalWeight) : 0;
    }
    
    // Estimate progress percentage from status
    function estimateProgressFromStatus(status) {
        const statusProgress = {
            'not_started': 0,
            'planned': 10,
            'in_progress': 50,
            'completed': 100,
            'blocked': 25,
            'cancelled': 0
        };
    
        return statusProgress[status] || 0;
    }
    
    // Get color for progress percentage
    function getProgressColor(progress) {
        if (progress === 0) return 'gray';
        if (progress < 25) return 'red';
        if (progress < 50) return 'orange';
        if (progress < 75) return 'yellow';
        if (progress < 100) return 'blue';
        return 'green';
    }
    
    // Returns full Tailwind class strings to avoid dynamic class construction
    // which breaks Tailwind purge (e.g. bg-${color}-600 won't be found at build time)
    function getProgressBarClass(progress) {
        if (progress === 0) return 'bg-muted-foreground/20';
        if (progress < 25) return 'bg-destructive';
        if (progress < 50) return 'bg-orange-600';
        if (progress < 75) return 'bg-yellow-600';
        if (progress < 100) return 'bg-primary';
        return 'bg-layer-implementation-emphasis';
    }

    function getProgressTextClass(progress) {
        if (progress === 0) return 'text-foreground';
        if (progress < 25) return 'text-rose-700';
        if (progress < 50) return 'text-orange-700';
        if (progress < 75) return 'text-amber-700';
        if (progress < 100) return 'text-primary';
        return 'text-sky-700';
    }
    
    // ============================================
    // COLLABORATION FEATURES - COMMENTS
    // ============================================
    
    const commentsData = new Map();
    
    // Show comments panel for an item
    function showCommentsPanel(itemId, itemType) {
        const item = itemType === 'gap'
            ? roadmapData.items.find(i => i.id === itemId)
            : findWorkPackageById(itemId);
    
        if (!item) return;
    
        const comments = commentsData.get(`${itemType}-${itemId}`) || [];
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
                <div class="flex items-center justify-between p-6 border-b border-border">
                    <div>
                        <h3 class="text-lg font-semibold text-foreground flex items-center">
                            <i data-lucide="message-square" class="w-5 h-5 mr-2 text-primary"></i>
                            Comments (${comments.length})
                        </h3>
                        <p class="text-sm text-muted-foreground mt-1">${escapeHtml(item.name)}</p>
                    </div>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
    
                <div class="flex-1 overflow-y-auto p-6 space-y-4" id="comments-list-${itemId}">
                    ${comments.length === 0 ? `
                        <div class="text-center py-12 text-muted-foreground">
                            <i data-lucide="message-circle" class="w-12 h-12 mx-auto mb-3 text-muted-foreground"></i>
                            <p>No comments yet</p>
                            <p class="text-sm mt-1">Be the first to comment!</p>
                        </div>
                    ` : comments.map(c => `
                        <div class="flex space-x-3">
                            <div class="flex-shrink-0">
                                <div class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                    <span class="text-sm font-medium text-primary">${escapeHtml(c.author.charAt(0).toUpperCase())}</span>
                                </div>
                            </div>
                            <div class="flex-1">
                                <div class="flex items-center space-x-2">
                                    <span class="font-medium text-foreground">${escapeHtml(c.author)}</span>
                                    <span class="text-xs text-muted-foreground">${escapeHtml(c.timestamp)}</span>
                                </div>
                                <p class="text-sm text-muted-foreground mt-1">${escapeHtml(c.text)}</p>
                                ${c.mentions && c.mentions.length > 0 ? `
                                    <div class="flex flex-wrap gap-1 mt-2">
                                        ${c.mentions.map(m => `<span class="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded">@${escapeHtml(m)}</span>`).join('')}
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    `).join('')}
                </div>
    
                <div class="p-6 border-t border-border">
                    <textarea id="comment-input-${itemId}"
                              placeholder="Add a comment... Use @username to mention someone"
                              class="w-full px-3 py-2 border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-none"
                              rows="3"></textarea>
                    <div class="flex justify-between items-center mt-3">
                        <span class="text-xs text-muted-foreground">Tip: Use @username to mention team members</span>
                        <button type="button" data-cm-action="add-comment" data-item-id="${escapeAttr(itemId)}" data-item-type="${escapeAttr(itemType)}"
                                class="px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 flex items-center">
                            <i data-lucide="send" class="w-4 h-4 mr-2"></i>
                            Post Comment
                        </button>
                    </div>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // Add a comment
    function addComment(itemId, itemType) {
        const input = getEl(`comment-input-${itemId}`);
        const text = input.value.trim();
    
        if (!text) {
            showToast('Please enter a comment', 'warning');
            return;
        }
    
        const key = `${itemType}-${itemId}`;
        const comments = commentsData.get(key) || [];
    
        // Extract mentions
        const mentions = [];
        const mentionRegex = /@(\w+)/g;
        let match;
        while ((match = mentionRegex.exec(text)) !== null) {
            mentions.push(match[1]);
        }
    
        const comment = {
            id: Date.now(),
            author: '{{ current_user.username if current_user and current_user.is_authenticated else "Anonymous" }}',
            text: text,
            timestamp: new Date().toLocaleString(),
            mentions: mentions
        };
    
        comments.push(comment);
        commentsData.set(key, comments);
    
        // Close and reopen modal to refresh
        document.querySelector('.fixed')?.remove();
        showCommentsPanel(itemId, itemType);
    
        // Send notifications for mentions
        if (mentions.length > 0) {
            showToast(`Comment posted with ${mentions.length} mention(s)`, 'success');
        } else {
            showToast('Comment posted', 'success');
        }
    }
    
    // ============================================
    // BREADCRUMB NAVIGATION
    // ============================================
    
    // Show breadcrumb for item hierarchy
    function showBreadcrumb(itemId, itemType) {
        const breadcrumbNav = getEl('breadcrumb-nav');
        const breadcrumbPath = getEl('breadcrumb-path');
    
        if (itemType === 'gap') {
            const gap = roadmapData.items.find(g => g.id === itemId);
            if (!gap) return;
    
            const path = [];
    
            // Add domain
            if (gap.domain_name) {
                path.push({ label: gap.domain_name, icon: 'folder', level: 0 });
            }
    
            // Add parent hierarchy if available
            if (gap.parent_name) {
                path.push({ label: gap.parent_name, icon: 'layers', level: 1 });
            }
    
            // Add current gap
            path.push({ label: gap.name, icon: 'target', level: 2, current: true });
    
            renderBreadcrumb(path);
            breadcrumbNav.classList.remove('hidden');
            breadcrumbNav.classList.add('flex');
        } else if (itemType === 'work_package') {
            // Find work package in hierarchy
            const wp = findWorkPackageById(itemId);
            if (!wp) return;
    
            const path = buildWorkPackagePath(wp);
            renderBreadcrumb(path);
            breadcrumbNav.classList.remove('hidden');
            breadcrumbNav.classList.add('flex');
        }
    }
    
    // Hide breadcrumb
    function hideBreadcrumb() {
        const breadcrumbNav = getEl('breadcrumb-nav');
        breadcrumbNav.classList.add('hidden');
        breadcrumbNav.classList.remove('flex');
    }
    
    // Render breadcrumb path
    function renderBreadcrumb(path) {
        const breadcrumbPath = getEl('breadcrumb-path');
    
        safeHTML(breadcrumbPath, path.map((item, index) => {
            const isLast = index === path.length - 1;
            const textClass = item.current ? 'font-semibold text-primary' : 'text-muted-foreground hover:text-foreground';
    
            return `
                <div class="flex items-center">
                    <i data-lucide="${item.icon}" class="w-3.5 h-3.5 mr-1"></i>
                    <span class="${textClass}">${escapeHtml(item.label)}</span>
                    ${!isLast ? '<i data-lucide="chevron-right" class="w-4 h-4 mx-2 text-muted-foreground"></i>' : ''}
                </div>
            `;
        }).join(''));
    
        lucide.createIcons();
    }
    
    // Find work package by ID in hierarchy
    function findWorkPackageById(wpId) {
        for (const gap of roadmapData.items) {
            if (gap.work_packages) {
                const found = findWPInTree(gap.work_packages, wpId);
                if (found) return found;
            }
        }
        return null;
    }
    
    // Recursive search in work package tree
    function findWPInTree(workPackages, wpId) {
        for (const wp of workPackages) {
            if (wp.id === wpId) return wp;
            if (wp.children) {
                const found = findWPInTree(wp.children, wpId);
                if (found) return found;
            }
        }
        return null;
    }
    
    // Build breadcrumb path for work package
    function buildWorkPackagePath(wp) {
        const path = [];
    
        // This is simplified - in a real implementation, you'd traverse up the hierarchy
        path.push({ label: 'Roadmap', icon: 'map', level: 0 });
        path.push({ label: wp.name, icon: 'package', level: 1, current: true });
    
        return path;
    }
    
    // ============================================
    // EXPORT FUNCTIONALITY
    // ============================================
    
    // Toggle export dropdown menu for roadmap
    function toggleRoadmapExportMenu() {
        const menu = getEl('roadmap-export-menu');
        if (!menu) {
            showToast('Unable to open the export menu: it did not load. Please refresh the page.', 'error');
            return;
        }
        menu.classList.toggle('hidden');
    
        // Close menu when clicking outside
        setTimeout(() => {
            document.addEventListener('click', function closeMenu(e) {
                if (!e.target.closest('#export-dropdown')) {
                    if (menu) menu.classList.add('hidden');
                    document.removeEventListener('click', closeMenu);
                }
            });
        }, 100);
    }
    
    // Export roadmap in various formats
    async function exportRoadmap(format) {
        const menu = getEl('roadmap-export-menu');
        if (menu) menu.classList.add('hidden');
    
        showToast(`Preparing ${format.toUpperCase()} export...`, 'info');
    
        try {
            if (format === 'csv') {
                exportAsCSV();
            } else if (format === 'excel') {
                exportAsExcel();
            } else if (format === 'pdf') {
                await exportAsPDF();
            } else if (format === 'png' || format === 'jpeg' || format === 'jpg') {
                await exportAsImage(format);
            } else if (format === 'svg') {
                await exportAsSVG();
            }
        } catch (error) {
            showToast(`Error exporting roadmap: ${error.message}`, 'error');
        }
    }
    
    // Export as CSV
    function exportAsCSV() {
        const data = [];
    
        // Headers
        data.push(['Name', 'Type', 'Status', 'Priority', 'Owner', 'Start Date', 'End Date', 'Progress', 'Domain', 'Level']);
    
        // Add gaps and work packages
        roadmapData.filteredItems.forEach(item => {
            data.push([
                item.name,
                'Gap',
                item.status || 'Not Started',
                item.priority || 'Medium',
                item.business_owner || 'Unassigned',
                item.start_date || '',
                item.end_date || '',
                calculateItemProgress(item) + '%',
                item.domain_name || '',
                '0'
            ]);
    
            if (item.work_packages) {
                addWorkPackagesToCSV(item.work_packages, data, 1);
            }
        });
    
        // Convert to CSV string
        const csvContent = data.map(row => row.map(cell => `"${cell}"`).join(',')).join('\n');
    
        // Download
        const blob = new Blob([csvContent], { type: 'text/csv' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `roadmap_${new Date().toISOString().split('T')[0]}.csv`;
        a.click();
        window.URL.revokeObjectURL(url);
    
        showToast('CSV exported successfully', 'success');
    }
    
    // Helper to add work packages to CSV
    function addWorkPackagesToCSV(workPackages, data, level) {
        workPackages.forEach(wp => {
            data.push([
                '  '.repeat(level) + wp.name,
                'Work Package',
                wp.status || 'Not Started',
                wp.priority || 'Medium',
                wp.owner_name || 'Unassigned',
                wp.start_date || '',
                wp.end_date || '',
                wp.percent_complete || '0' + '%',
                '',
                level.toString()
            ]);
    
            if (wp.children) {
                addWorkPackagesToCSV(wp.children, data, level + 1);
            }
        });
    }
    
    // Export as Excel (simplified - would use a library in production)
    function exportAsExcel() {
        // For now, export as CSV with .xlsx extension
        // In production, use a library like SheetJS
        exportAsCSV();
        showToast('Excel export (CSV format)', 'info');
    }
    
    // Export as PDF using html2canvas and jsPDF
    async function exportAsPDF() {
        try {
            // Load html2canvas dynamically if not already loaded
            if (typeof html2canvas === 'undefined') {
                await loadScript('/static/vendor/html2canvas.min.js');
            }
    
            // Load jsPDF dynamically if not already loaded
            if (typeof jspdf === 'undefined') {
                await loadScript('/static/vendor/jspdf.umd.min.js');
            }
    
            showToast('Generating high-quality PDF...', 'info');
    
            // Wait for roadmap to be fully rendered
            await waitForRoadmapRender();
    
            // Get the main roadmap view container (the entire visible area)
            const roadmapView = document.querySelector('.tab-content');
            if (!roadmapView) {
                throw new Error('Roadmap view not found');
            }
    
            // Capture with high quality settings
            const canvas = await html2canvas(roadmapView, {
                scale: 4,
                useCORS: true,
                allowTaint: true,
                logging: false,
                backgroundColor: tokenHex('background'),
                scrollY: -window.scrollY,
                scrollX: -window.scrollX,
                windowWidth: document.documentElement.scrollWidth,
                windowHeight: document.documentElement.scrollHeight
            });
    
            const imgData = canvas.toDataURL('image/png', 1.0);
            const { jsPDF } = window.jspdf;
    
            // Calculate PDF dimensions in mm
            const imgWidth = canvas.width;
            const imgHeight = canvas.height;
            const ratio = imgWidth / imgHeight;
    
            // Use A3 landscape for wide roadmaps
            let pdfWidth = 420; // A3 landscape
            let pdfHeight = pdfWidth / ratio;
    
            // Cap height at A3 portrait if too tall
            if (pdfHeight > 594) {
                pdfHeight = 594;
                pdfWidth = pdfHeight * ratio;
            }
    
            const pdf = new jsPDF({
                orientation: 'landscape',
                unit: 'mm',
                format: [pdfWidth, pdfHeight]
            });
    
            pdf.addImage(imgData, 'PNG', 0, 0, pdfWidth, pdfHeight, '', 'FAST');
            pdf.save(`roadmap-${new Date().toISOString().split('T')[0]}.pdf`);
    
            showToast('High-quality PDF exported successfully!', 'success');
        } catch (error) {
            showToast(`PDF export failed: ${error.message}`, 'error');
            throw error;
        }
    }
    
    // Export as Image (PNG, JPEG, JPG)
    async function exportAsImage(format) {
        try {
            // Load html2canvas dynamically if not already loaded
            if (typeof html2canvas === 'undefined') {
                await loadScript('/static/vendor/html2canvas.min.js');
            }
    
            showToast(`Generating high-quality ${format.toUpperCase()}...`, 'info');
    
            // Wait for roadmap to be fully rendered
            await waitForRoadmapRender();
    
            // Get the main roadmap view container (the entire visible area)
            const roadmapView = document.querySelector('.tab-content');
            if (!roadmapView) {
                throw new Error('Roadmap view not found');
            }
    
            // Capture with high quality settings
            const canvas = await html2canvas(roadmapView, {
                scale: 4,
                useCORS: true,
                allowTaint: true,
                logging: false,
                backgroundColor: tokenHex('background'),
                scrollY: -window.scrollY,
                scrollX: -window.scrollX,
                windowWidth: document.documentElement.scrollWidth,
                windowHeight: document.documentElement.scrollHeight
            });
    
            // Convert format to proper MIME type
            const mimeType = format === 'png' ? 'image/png' : 'image/jpeg';
            const quality = format === 'png' ? 1.0 : 0.98;
    
            canvas.toBlob((blob) => {
                const url = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `roadmap-${new Date().toISOString().split('T')[0]}.${format}`;
                link.click();
                URL.revokeObjectURL(url);
                showToast(`High-quality ${format.toUpperCase()} exported successfully!`, 'success');
            }, mimeType, quality);
        } catch (error) {
            showToast(`${format.toUpperCase()} export failed: ${error.message}`, 'error');
            throw error;
        }
    }
    
    // Export as SVG
    async function exportAsSVG() {
        try {
            const container = getEl('roadmap-container');
            if (!container) {
                throw new Error('Roadmap container not found');
            }
    
            showToast('Generating SVG...', 'info');
    
            // Clone the container
            const clone = container.cloneNode(true);
    
            // Get computed styles and dimensions
            const rect = container.getBoundingClientRect();
            const width = rect.width;
            const height = rect.height;
    
            // Create SVG wrapper
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            svg.setAttribute('width', width);
            svg.setAttribute('height', height);
            svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    
            // Create foreignObject to embed HTML
            const foreignObject = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
            foreignObject.setAttribute('width', '100%');
            foreignObject.setAttribute('height', '100%');
            foreignObject.appendChild(clone);
            svg.appendChild(foreignObject);
    
            // Serialize SVG
            const serializer = new XMLSerializer();
            const svgString = serializer.serializeToString(svg);
    
            // Create blob and download
            const blob = new Blob([svgString], { type: 'image/svg+xml' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `roadmap-${new Date().toISOString().split('T')[0]}.svg`;
            link.click();
            URL.revokeObjectURL(url);
    
            showToast('SVG exported successfully!', 'success');
        } catch (error) {
            throw error;
        }
    }
    
    // Helper function to wait for roadmap to be fully rendered
    async function waitForRoadmapRender() {
        return new Promise((resolve) => {
            // Check if roadmap has content
            const checkRoadmap = () => {
                const labels = getEl('roadmap-labels');
                const timeline = getEl('roadmap-timeline');
    
                if (labels && timeline) {
                    const hasLabels = labels.children.length > 1; // More than just header
                    const hasTimeline = timeline.children.length > 1; // More than just header
    
                    if (hasLabels && hasTimeline) {
                        // Wait a bit more for rendering to complete
                        setTimeout(resolve, 500);
                        return true;
                    }
                }
                return false;
            };
    
            // Check immediately
            if (checkRoadmap()) return;
    
            // Poll every 100ms for up to 10 seconds
            let attempts = 0;
            const interval = setInterval(() => {
                attempts++;
                if (checkRoadmap() || attempts > 100) {
                    clearInterval(interval);
                    resolve();
                }
            }, 100);
        });
    }
    
    // Helper function to load external scripts dynamically
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = src;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }
    
    // ============================================
    // TIMELINE ZOOM CONTROLS
    // ============================================
    
    let currentZoomLevel = 'month';
    
    // Set timeline zoom level
    function setTimelineZoom(level) {
        currentZoomLevel = level;
    
        // Convert singular to plural for RoadmapUtils compatibility
        const displayModeMap = {
            'day': 'weeks',      // Day view uses weeks display
            'week': 'weeks',
            'month': 'months',
            'quarter': 'quarters'
        };
    
        roadmapData.displayMode = displayModeMap[level] || 'months';
    
        // Update button states
        ['day', 'week', 'month', 'quarter'].forEach(z => {
            const btn = getEl(`zoom-${z}`);
            if (btn) {
                if (z === level) {
                    btn.classList.add('bg-background', 'shadow-sm');
                    btn.classList.remove('hover:bg-background', 'hover:shadow-sm');
                } else {
                    btn.classList.remove('bg-background', 'shadow-sm');
                    btn.classList.add('hover:bg-background', 'hover:shadow-sm');
                }
            }
        });
    
        // Recalculate timeline with new zoom
        calculateTimelineRange();
        renderRoadmapTimeline();
    
        showToast(`Timeline zoom: ${level}`, 'success');
    }
    
    // ============================================
    // DEPENDENCY TRACKING & CRITICAL PRIORITY HIGHLIGHT
    // Note: "Critical path" in this context means items with
    // priority='critical'. This is NOT a dependency-based CPM
    // (Critical Path Method) calculation.
    // ============================================

    // Track dependencies between items
    const dependencies = new Map();

    // Add dependency between items
    function addDependency(fromId, toId, type = 'finish-to-start') {
        const key = `${fromId}-${toId}`;
        dependencies.set(key, { from: fromId, to: toId, type: type });
        renderRoadmapTimeline();
    }

    // Remove dependency
    function removeDependency(fromId, toId) {
        const key = `${fromId}-${toId}`;
        dependencies.delete(key);
        renderRoadmapTimeline();
    }

    // Find all items whose priority is 'critical'.
    // This is a priority-based filter, NOT a dependency-based
    // Critical Path Method (CPM) calculation.
    function calculateCriticalPriorityItems() {
        const items = roadmapData.filteredItems;
        const criticalItems = [];

        items.forEach(item => {
            if (item.priority === 'critical' || item.priority === 'Critical') {
                criticalItems.push(item.id);
            }
        });

        return criticalItems;
    }

    // Keep backward-compatible alias
    function calculateCriticalPath() {
        return calculateCriticalPriorityItems();
    }

    // Highlight items that have priority='critical' with a red ring.
    // Disclaimer: this highlights by priority level, not by
    // dependency-chain analysis (CPM).
    function highlightCriticalPath() {
        const criticalItems = calculateCriticalPriorityItems();

        if (criticalItems.length === 0) {
            showToast('No items with "Critical" priority found', 'info');
            return;
        }

        // Add visual indicator to critical priority items
        criticalItems.forEach(itemId => {
            const element = document.querySelector(`[data-gap-id="${itemId}"]`);
            if (element) {
                element.classList.add('ring-2', 'ring-red-500', 'ring-offset-2');
            }
        });

        showToast(`${criticalItems.length} critical-priority items highlighted (by priority level, not dependency chain)`, 'success');
    }
    
    // Clear critical-priority item highlighting (removes red ring indicators)
    function clearCriticalPathHighlight() {
        document.querySelectorAll('[data-gap-id]').forEach(element => {
            element.classList.remove('ring-2', 'ring-red-500', 'ring-offset-2');
        });
    }
    
    // Show dependency management modal
    function showDependencyModal(itemId) {
        const item = roadmapData.items.find(i => i.id === itemId);
        if (!item) return;
    
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
        safeHTML(modal, `
            <div class="bg-card rounded-lg shadow-xl max-w-2xl w-full p-6">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-lg font-semibold text-foreground flex items-center">
                        <i data-lucide="git-branch" class="w-5 h-5 mr-2 text-primary"></i>
                        Manage Dependencies
                    </h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-5 h-5"></i>
                    </button>
                </div>
                <p class="text-sm text-muted-foreground mb-4">Item: ${escapeHtml(item.name)}</p>
    
                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-muted-foreground mb-2">Depends On</label>
                        <select id="dependency-select" class="w-full px-3 py-2 border border-input rounded-md">
                            <option value="">Select item...</option>
                            ${roadmapData.items.filter(i => i.id !== itemId).map(i =>
                                `<option value="${i.id}">${i.name}</option>`
                            ).join('')}
                        </select>
                    </div>
    
                    <div>
                        <label class="block text-sm font-medium text-muted-foreground mb-2">Dependency Type</label>
                        <select id="dependency-type" class="w-full px-3 py-2 border border-input rounded-md">
                            <option value="finish-to-start">Finish to Start</option>
                            <option value="start-to-start">Start to Start</option>
                            <option value="finish-to-finish">Finish to Finish</option>
                            <option value="start-to-finish">Start to Finish</option>
                        </select>
                    </div>
    
                    <button type="button" data-cm-action="save-dependency" data-item-id="${escapeAttr(itemId)}" class="w-full px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90">
                        Add Dependency
                    </button>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // Save dependency
    function saveDependency(toId) {
        const fromId = getEl('dependency-select').value;
        const type = getEl('dependency-type').value;
    
        if (!fromId) {
            showToast('Please select an item', 'warning');
            return;
        }
    
        addDependency(fromId, toId, type);
        document.querySelector('.fixed')?.remove();
        showToast('Dependency added', 'success');
    }
    
    // ============================================
    // EDIT GAP MODAL FUNCTIONS
    // ============================================
    
    function openEditGapModal(gapId) {
        currentEditGapId = gapId;
        const modal = getEl('edit-gap-modal');
    
        // Find the gap data
        const gap = roadmapData.items.find(g => g.id === gapId || g.archimate_id === `gap-${gapId}`);
        if (!gap) {
            showToast('Gap not found', 'error');
            return;
        }
    
        // Populate form
        getEl('edit-gap-id').value = gapId;
        getEl('edit-gap-name').value = gap.name || '';
        getEl('edit-gap-description').value = gap.description || '';
        getEl('edit-gap-type').value = gap.primary_gap || gap.gap_types?.[0] || 'coverage';
        getEl('edit-gap-priority').value = gap.priority || 'medium';
        getEl('edit-gap-status').value = gap.resolution_status || 'identified';
        getEl('edit-gap-start-date').value = gap.start_date || '';
        getEl('edit-gap-end-date').value = gap.end_date || '';
        getEl('edit-gap-color').value = gap.color || tokenHex('muted-foreground');
        getEl('edit-gap-owner').value = gap.business_owner || '';
        getEl('edit-gap-effort').value = gap.estimated_effort_days || '';
        getEl('edit-gap-cost').value = gap.estimated_cost || '';
    
        getEl('edit-gap-title').textContent = `Edit: ${gap.name}`;
    
        // Load work packages if persisted
        if (gap.is_persisted) {
            loadGapWorkPackages(gapId);
        } else {
            safeHTML(getEl('edit-gap-work-packages'), `
                <p class="text-sm text-muted-foreground italic">Save this gap first to add work packages</p>
            `);
        }
    
        Platform.modal.open('edit-gap-modal');
        lucide.createIcons();
    }
    
    function closeEditGapModal() {
        Platform.modal.close('edit-gap-modal');
        currentEditGapId = null;
    }
    
    function setGapColor(token) {
        getEl('edit-gap-color').value = tokenHex(token);
    }
    
    function setRoadmapColor(token) {
        getEl('add-roadmap-color').value = tokenHex(token);
    }

    window.addEventListener('load', () => {
        const addColor = document.getElementById('add-roadmap-color');
        if (addColor) addColor.value = tokenHex('muted-foreground');
    });

    async function loadGapWorkPackages(gapId) {
        const container = getEl('edit-gap-work-packages');
        safeHTML(container, '<p class="text-sm text-muted-foreground">Loading work packages...</p>');

        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/gaps/${gapId}`, { silent: true });
    
            if (data.success && data.gap.work_packages) {
                const wps = data.gap.work_packages;
                if (wps.length === 0) {
                    safeHTML(container, '<p class="text-sm text-muted-foreground italic">No work packages yet</p>');
                } else {
                    safeHTML(container, wps.map(wp => `
                        <div class="flex items-center justify-between p-2 bg-muted/50 rounded border border-border">
                            <div class="flex items-center space-x-2">
                                <span class="w-3 h-3 rounded" style="background-color: ${wp.color || tokenHex('layer-application')}"></span>
                                <span class="text-sm font-medium">${escapeHtml(wp.name)}</span>
                                <span class="text-xs text-muted-foreground">${escapeHtml(wp.status)}</span>
                            </div>
                            <div class="flex items-center space-x-2">
                                <span class="text-xs text-muted-foreground">${wp.start_date || 'No date'} - ${wp.end_date || 'No date'}</span>
                                <button type="button" data-cm-action="open-edit-wp" data-wp-id="${escapeAttr(wp.id)}" aria-label="Edit work package ${escapeAttr(wp.name)}" class="text-primary hover:text-primary/90">
                                    <i data-lucide="edit-2" class="w-4 h-4"></i>
                                </button>
                            </div>
                        </div>
                    `).join(''));
                    lucide.createIcons();
                }
            } else {
                // Without this the container was left reading "Loading work packages…"
                // forever whenever the payload came back without a work_packages list.
                safeHTML(container, '<p class="text-sm text-destructive">Could not read the work package list for this gap.</p>');
            }
        } catch (error) {
            safeHTML(container, '<p class="text-sm text-destructive">Error loading work packages</p>');
        }
    }
    
    async function saveGapChanges() {
        const gapId = getEl('edit-gap-id').value;
    
        const updates = {
            name: getEl('edit-gap-name').value,
            description: getEl('edit-gap-description').value,
            gap_type: getEl('edit-gap-type').value,
            priority: getEl('edit-gap-priority').value,
            resolution_status: getEl('edit-gap-status').value,
            estimated_start_date: getEl('edit-gap-start-date').value || null,
            target_resolution_date: getEl('edit-gap-end-date').value || null,
            color: getEl('edit-gap-color').value,
            owner: getEl('edit-gap-owner').value || null,
            estimated_effort_days: parseInt(getEl('edit-gap-effort').value) || null,
            estimated_cost: parseFloat(getEl('edit-gap-cost').value) || null
        };
    
        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/gaps/${gapId}`, {
                method: 'PUT',
                body: updates,
                silent: true
            });

            if (data.success) {
                showToast('Gap updated successfully', 'success');
                closeEditGapModal();
                // Reload data
                if (roadmapData.viewMode === 'persisted') {
                    loadPersistedRoadmapData();
                }
            } else {
                showToast(data.error || 'Update failed', 'error');
            }
        } catch (error) {
            showToast('Error saving changes', 'error');
        }
    }
    
    async function deleteCurrentGap() {
        const gapId = getEl('edit-gap-id').value;
        const modalId = window.modalManager.createModal({
            title: 'Delete Gap',
            content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete this gap? This action cannot be undone.</p>',
            size: 'small',
            buttons: [
                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: function() { _doDeleteGap(gapId); } }
            ]
        });
        window.modalManager.open(modalId);
    }

    async function _doDeleteGap(gapId) {

        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/gaps/${gapId}`, {
                method: 'DELETE',
                silent: true
            });

            if (data.success) {
                showToast('Gap deleted', 'success');
                closeEditGapModal();
                loadPersistedRoadmapData();
            } else {
                showToast(data.error || 'Delete failed', 'error');
            }
        } catch (error) {
            showToast('Error deleting gap', 'error');
        }
    }
    
    async function addWorkPackageToGap() {
        const gapId = currentEditGapId;
        if (!gapId) return;

        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/gaps/${gapId}/work-packages`, {
                method: 'POST',
                body: { template: 'default' },
                silent: true
            });

            if (data.success) {
                showToast('Work package created', 'success');
                loadGapWorkPackages(gapId);
            } else {
                showToast(data.error || 'Failed to create work package', 'error');
            }
        } catch (error) {
            showToast('Error creating work package', 'error');
        }
    }
    
    // ============================================
    // EDIT WORK PACKAGE MODAL FUNCTIONS
    // ============================================
    
    async function openEditWPModal(wpId) {
        currentEditWPId = wpId;
        const modal = getEl('edit-wp-modal');

        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/work-packages/${wpId}`, { silent: true });
    
            if (data.success) {
                const wp = data.work_package;
    
                // Populate form fields
                getEl('edit-wp-id').value = wpId;
                getEl('edit-wp-parent-id').value = wp.parent_id || '';
                getEl('edit-wp-name').value = wp.name || '';
                getEl('edit-wp-description').value = wp.description || '';
                getEl('edit-wp-status').value = wp.status || 'planned';
                getEl('edit-wp-priority').value = wp.priority || 'medium';
                getEl('edit-wp-progress').value = wp.percent_complete || 0;
                getEl('edit-wp-start-date').value = wp.start_date || '';
                getEl('edit-wp-end-date').value = wp.end_date || '';
                getEl('edit-wp-color').value = wp.color || tokenHex('layer-application');
                getEl('edit-wp-est-hours').value = wp.estimated_effort_hours || '';
                getEl('edit-wp-act-hours').value = wp.actual_effort_hours || '';
                getEl('edit-wp-est-cost').value = wp.estimated_cost || '';
                getEl('edit-wp-act-cost').value = wp.actual_cost || '';
    
                // Update header
                getEl('edit-wp-title').textContent = `Edit: ${wp.name}`;
                getEl('edit-wp-subtitle').textContent = `Level ${wp.level || 1} Work Package`;
    
                // Update stats bar
                const statusLabels = {
                    planned: 'Planned',
                    in_progress: 'In Progress',
                    completed: 'Completed',
                    blocked: 'Blocked',
                    cancelled: 'Cancelled'
                };
                getEl('wp-stat-status').textContent = statusLabels[wp.status] || 'Planned';
                getEl('wp-stat-progress').textContent = (wp.percent_complete || 0) + '%';
                getEl('wp-stat-owner').textContent = wp.owner_name || 'Unassigned';
                getEl('wp-stat-children').textContent = wp.child_count || 0;
    
                // Update progress display
                updateWPProgressDisplay(wp.percent_complete || 0);
    
                // Load children data
                loadWPChildren(wp.children || []);
    
                // Reset to details tab
                switchWPTab('details');

                Platform.modal.open('edit-wp-modal');
                lucide.createIcons();
            }
        } catch (error) {
            showToast('Error loading work package', 'error');
        }
    }

    function closeEditWPModal() {
        Platform.modal.close('edit-wp-modal');
        currentEditWPId = null;
    }
    
    async function saveWPChanges() {
        const wpId = getEl('edit-wp-id').value;
    
        const updates = {
            name: getEl('edit-wp-name').value,
            description: getEl('edit-wp-description').value,
            status: getEl('edit-wp-status').value,
            priority: getEl('edit-wp-priority').value,
            percent_complete: parseInt(getEl('edit-wp-progress').value) || 0,
            start_date: getEl('edit-wp-start-date').value || null,
            target_date: getEl('edit-wp-end-date').value || null,
            color: getEl('edit-wp-color').value,
            estimated_effort_hours: parseInt(getEl('edit-wp-est-hours').value) || null,
            actual_effort_hours: parseInt(getEl('edit-wp-act-hours').value) || null,
            estimated_cost: parseFloat(getEl('edit-wp-est-cost').value) || null,
            actual_cost: parseFloat(getEl('edit-wp-act-cost').value) || null
        };
    
        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/work-packages/${wpId}`, {
                method: 'PUT',
                body: updates,
                silent: true
            });

            if (data.success) {
                showToast('Work package updated', 'success');
                closeEditWPModal();
                // Reload gap work packages if edit gap modal is open
                if (currentEditGapId) {
                    loadGapWorkPackages(currentEditGapId);
                }
            } else {
                showToast(data.error || 'Update failed', 'error');
            }
        } catch (error) {
            showToast('Error saving changes', 'error');
        }
    }
    
    async function deleteCurrentWP() {
        const wpId = getEl('edit-wp-id').value;
        const modalId = window.modalManager.createModal({
            title: 'Delete Work Package',
            content: '<p class="text-sm text-muted-foreground">Delete this work package and all its children?</p>',
            size: 'small',
            buttons: [
                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: function() { _doDeleteWP(wpId); } }
            ]
        });
        window.modalManager.open(modalId);
    }

    async function _doDeleteWP(wpId) {

        try {
            const data = await Platform.fetch(`/capability-map/api/roadmap/work-packages/${wpId}?cascade=true`, {
                method: 'DELETE',
                silent: true
            });
    
            if (data.success) {
                showToast('Work package deleted', 'success');
                closeEditWPModal();
                if (currentEditGapId) {
                    loadGapWorkPackages(currentEditGapId);
                }
            } else {
                showToast(data.error || 'Delete failed', 'error');
            }
        } catch (error) {
            showToast('Error deleting work package', 'error');
        }
    }
    
    // ============================================
    // WORK PACKAGE MODAL TAB FUNCTIONS
    // ============================================
    
    function switchWPTab(tabName) {
        // Hide all tab contents
        document.querySelectorAll('.wp-tab-content').forEach(content => {
            content.classList.add('hidden');
        });
    
        // Remove active class from all tabs
        document.querySelectorAll('.wp-tab').forEach(tab => {
            tab.classList.remove('active', 'border-blue-600', 'text-primary');
            tab.classList.add('border-transparent', 'text-muted-foreground');
        });
    
        // Show selected tab content
        getEl(`wp-tab-content-${tabName}`).classList.remove('hidden');
    
        // Add active class to selected tab
        const activeTab = getEl(`wp-tab-${tabName}`);
        activeTab.classList.add('active', 'border-blue-600', 'text-primary');
        activeTab.classList.remove('border-transparent', 'text-muted-foreground');
    
        // Refresh icons
        lucide.createIcons();
    }
    
    function updateWPProgressDisplay(value) {
        getEl('edit-wp-progress-value').textContent = value + '%';
        getEl('edit-wp-progress-bar').style.width = value + '%';
        getEl('wp-stat-progress').textContent = value + '%';
    }
    
    // Store children data for filtering
    let wpChildrenData = [];
    let wpChildrenFiltered = [];
    
    function loadWPChildren(children) {
        wpChildrenData = children || [];
        wpChildrenFiltered = [...wpChildrenData];
    
        // Update stats
        getEl('wp-stat-children').textContent = wpChildrenData.length;
        getEl('wp-children-count').textContent = wpChildrenData.length;
    
        // Render children list
        renderWPChildrenList();
    }
    
    function filterWPChildren() {
        const searchTerm = getEl('wp-children-search').value.toLowerCase();
        const statusFilter = getEl('wp-children-filter-status').value;
        const priorityFilter = getEl('wp-children-filter-priority').value;
        const levelFilter = getEl('wp-children-filter-level').value;
        const sortBy = getEl('wp-children-sort').value;
    
        // Filter
        wpChildrenFiltered = wpChildrenData.filter(child => {
            const matchesSearch = !searchTerm || child.name.toLowerCase().includes(searchTerm);
            const matchesStatus = !statusFilter || child.status === statusFilter;
            const matchesPriority = !priorityFilter || child.priority === priorityFilter;
            const matchesLevel = !levelFilter || child.level === parseInt(levelFilter);
            return matchesSearch && matchesStatus && matchesPriority && matchesLevel;
        });
    
        // Sort
        wpChildrenFiltered.sort((a, b) => {
            switch(sortBy) {
                case 'name-asc': return a.name.localeCompare(b.name);
                case 'name-desc': return b.name.localeCompare(a.name);
                case 'status-asc': return (a.status || '').localeCompare(b.status || '');
                case 'priority-desc':
                    const priorityOrder = {critical: 4, high: 3, medium: 2, low: 1};
                    return (priorityOrder[b.priority] || 0) - (priorityOrder[a.priority] || 0);
                case 'date-asc': return (a.start_date || '').localeCompare(b.start_date || '');
                default: return 0;
            }
        });
    
        renderWPChildrenList();
    }
    
    function renderWPChildrenList() {
        const container = getEl('wp-children-list');
    
        if (wpChildrenFiltered.length === 0) {
            safeHTML(container, `
                <div class="text-center py-12 text-muted-foreground">
                    <i data-lucide="inbox" class="w-12 h-12 mx-auto mb-3 text-muted-foreground"></i>
                    <p class="text-sm">${wpChildrenData.length === 0 ? 'No child work packages yet' : 'No matching work packages found'}</p>
                    <button @click="addChildWorkPackage()" class="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 text-sm">
                        <i data-lucide="plus" class="w-4 h-4 inline mr-2"></i>
                        Add Child Work Package
                    </button>
                </div>
            `);
            lucide.createIcons();
            return;
        }
    
        // Render card-based list (like Process Gaps modal)
        safeHTML(container, wpChildrenFiltered.map(child => {
            const statusColors = {
                planned: 'bg-muted text-muted-foreground',
                in_progress: 'bg-primary/10 text-primary',
                completed: 'bg-emerald-500/10 text-emerald-700',
                blocked: 'bg-destructive/10 text-destructive',
                cancelled: 'bg-muted text-muted-foreground'
            };
            const statusColor = statusColors[child.status] || 'bg-muted text-muted-foreground';
    
            return `
                <div class="border border-border rounded-lg p-4 mb-3 hover:border-primary/30 hover:bg-primary/5 transition-colors">
                    <div class="flex items-start justify-between">
                        <div class="flex items-start space-x-3 flex-1">
                            <input type="checkbox"
                                   class="mt-1 rounded border-input text-primary focus-visible:ring-ring"
                                   aria-label="Select ${escapeHtml(child.name)}"
                                   data-cm-change="toggle-wp-child" data-child-id="${escapeAttr(child.id)}">
                            <div class="flex-1">
                                <div class="flex items-center space-x-2 mb-1">
                                    <span class="text-xs font-semibold text-primary bg-primary/10 px-2 py-0.5 rounded">L${child.level || 1}</span>
                                    <h4 class="font-semibold text-foreground">${escapeHtml(child.name)}</h4>
                                </div>
                                <p class="text-sm text-muted-foreground mb-2">${escapeHtml(child.description || 'No description')}</p>
                                <div class="flex items-center space-x-3 text-xs text-muted-foreground">
                                    <span>Priority: ${escapeHtml(child.priority || 'medium')}</span>
                                    <span>•</span>
                                    <span>Progress: ${child.percent_complete || 0}%</span>
                                    ${child.owner_name ? `<span>•</span><span>Owner: ${escapeHtml(child.owner_name)}</span>` : ''}
                                </div>
                            </div>
                        </div>
                        <div class="flex items-center space-x-2">
                            <span class="px-3 py-1 rounded-full text-xs font-medium ${statusColor}">
                                ${child.status || 'planned'}
                            </span>
                            <button type="button" data-cm-action="open-edit-wp" data-wp-id="${escapeAttr(child.id)}" aria-label="Edit work package ${escapeAttr(child.name)}" class="text-primary hover:text-primary/90 p-1">
                                <i data-lucide="edit-2" class="w-4 h-4"></i>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }).join(''));
    
        // Update count
        getEl('wp-children-showing-count').textContent =
            `Showing ${wpChildrenFiltered.length} of ${wpChildrenData.length} work packages`;
    
        lucide.createIcons();
    }
    
    let selectedWPChildren = new Set();
    
    function toggleWPChildSelection(childId, isSelected) {
        if (isSelected) {
            selectedWPChildren.add(childId);
        } else {
            selectedWPChildren.delete(childId);
        }
        updateWPChildrenSelectionCount();
    }
    
    function selectAllWPChildren() {
        wpChildrenFiltered.forEach(child => selectedWPChildren.add(child.id));
        renderWPChildrenList();
        updateWPChildrenSelectionCount();
    }
    
    function deselectAllWPChildren() {
        selectedWPChildren.clear();
        renderWPChildrenList();
        updateWPChildrenSelectionCount();
    }
    
    function updateWPChildrenSelectionCount() {
        getEl('wp-children-selected-count').textContent =
            `${selectedWPChildren.size} selected`;
    }
    
    function addChildWorkPackage() {
        // Add child work package not yet implemented
        showToast('Add child work package functionality coming soon', 'info');
    }
    
    // ============================================
    // ADD TO ROADMAP FUNCTIONALITY
    // ============================================
    
    function addToRoadmap(capabilityId, capabilityName, capabilityType, level, priority) {
        // Open the add-to-roadmap modal with pre-filled data
        getEl('add-roadmap-cap-id').value = capabilityId;
        getEl('add-roadmap-type').value = capabilityType;
        getEl('add-roadmap-level').value = level;
        getEl('add-roadmap-cap-name').textContent = capabilityName;
    
        // Set capability type badge
        const typeLabels = {
            'business': 'Business Capability',
            'technical': 'Technical (ACM)',
            'process': 'Process (APQC)'
        };
        getEl('add-roadmap-cap-type').textContent = typeLabels[capabilityType] || capabilityType;
    
        // Set level badge
        getEl('add-roadmap-cap-level').textContent = `Level ${level}`;
    
        // Set default dates (start: today, end: 90 days from now based on priority)
        const today = new Date();
        const daysToAdd = priority === 'critical' ? 30 : priority === 'high' ? 60 : priority === 'medium' ? 90 : 180;
        const endDate = new Date(today);
        endDate.setDate(endDate.getDate() + daysToAdd);
    
        getEl('add-roadmap-start-date').value = today.toISOString().split('T')[0];
        getEl('add-roadmap-end-date').value = endDate.toISOString().split('T')[0];
    
        // Set default priority
        getEl('add-roadmap-priority').value = priority || 'medium';
    
        // Set default gap type based on capability type
        const defaultGapType = capabilityType === 'process' ? 'coverage' : 'quality';
        getEl('add-roadmap-gap-type').value = defaultGapType;
    
        // Show modal
        Platform.modal.open('add-roadmap-modal');
    }
    
    function closeAddRoadmapModal() {
        Platform.modal.close('add-roadmap-modal');
    }
    
    async function submitAddToRoadmap() {
        const capabilityId = getEl('add-roadmap-cap-id').value;
        const capabilityType = getEl('add-roadmap-type').value;
        const capabilityName = getEl('add-roadmap-cap-name').textContent;
        const level = getEl('add-roadmap-level').value;
        const gapType = getEl('add-roadmap-gap-type').value;
        const priority = getEl('add-roadmap-priority').value;
        const startDate = getEl('add-roadmap-start-date').value;
        const endDate = getEl('add-roadmap-end-date').value;
        const color = getEl('add-roadmap-color').value;
        const createWorkPackages = getEl('add-roadmap-create-wps').checked;
    
        // Validation
        if (!startDate || !endDate) {
            showToast('Please select start and end dates', 'error');
            return;
        }
    
        if (new Date(endDate) <= new Date(startDate)) {
            showToast('End date must be after start date', 'error');
            return;
        }
    
        try {
            const data = await Platform.fetch('/capability-map/api/roadmap/gaps/add-from-capability', {
                method: 'POST',
                body: {
                    capability_id: parseInt(capabilityId),
                    capability_type: capabilityType,
                    capability_name: capabilityName,
                    level: parseInt(level),
                    gap_type: gapType,
                    priority: priority,
                    start_date: startDate,
                    end_date: endDate,
                    color: color,
                    create_work_packages: createWorkPackages
                },
                silent: true
            });

            if (data.success) {
                showToast(`Added "${capabilityName}" to roadmap`, 'success');
                closeAddRoadmapModal();

                // Reload roadmap data if on roadmap tab
                const activeTab = document.querySelector('[data-tab-target="roadmap"]');
                if (activeTab && activeTab.getAttribute('aria-selected') === 'true') {
                    loadRoadmapData();
                }

                // Refresh the capability table to show the roadmap badge
                if (typeof renderCurrentPage === 'function') {
                    renderCurrentPage();
                }
            } else {
                showToast(data.error || 'Failed to add to roadmap', 'error');
            }
        } catch (error) {
            // DEF-078, Capgemini dry-run: Platform.fetch throws on a non-2xx
            // response (silent:true suppresses its own toast, not the real
            // reason) — this discarded the server's actual message (e.g.
            // "This capability is already on the roadmap") in favour of a
            // generic "Error adding to roadmap" that told the user nothing
            // they could act on.
            showToast((error && error.message) || 'Error adding to roadmap', 'error');
        }
    }
    
    // ============================================
    // PHASE 2: MULTI-SELECT & BULK OPERATIONS
    // ============================================
    
    function toggleRowSelection(capabilityId, tabType, isChecked) {
        if (isChecked) {
            tableData[tabType].selected.add(capabilityId);
        } else {
            tableData[tabType].selected.delete(capabilityId);
        }
        updateSelectionCounter(tabType);
    }
    
    function handleRowClick(event, capabilityId, capabilityName, tabType) {
        // If clicking checkbox, let it handle itself
        if (event.target.type === 'checkbox') {
            return;
        }
    
        // Default behavior: open mapping modal
        openMappingModal(capabilityId, capabilityName);
    }
    
    function selectAllCapabilities(tabType) {
        const table = tableData[tabType];
        table.filtered.forEach(item => {
            const id = String(item.id || item.capability_id);
            table.selected.add(id);
        });
        updateTable(tabType);
        updateSelectionCounter(tabType);
    }
    
    function deselectAllCapabilities(tabType) {
        tableData[tabType].selected.clear();
        updateTable(tabType);
        updateSelectionCounter(tabType);
    }
    
    function updateSelectionCounter(tabType) {
        const counter = getEl(`${tabType}-selection-count`);
        if (counter) {
            const count = tableData[tabType].selected.size;
            counter.textContent = `${count} selected`;
            counter.classList.toggle('hidden', count === 0);
        }
    
        const bulkBtn = getEl(`${tabType}-bulk-add-btn`);
        if (bulkBtn) {
            bulkBtn.disabled = tableData[tabType].selected.size === 0;
            bulkBtn.classList.toggle('opacity-50', tableData[tabType].selected.size === 0);
        }
    }
    
    async function bulkAddToRoadmap(tabType) {
        const selectedIds = Array.from(tableData[tabType].selected);
        if (selectedIds.length === 0) {
            showToast('No capabilities selected', 'warning');
            return;
        }
    
        const selectedItems = tableData[tabType].data.filter(item =>
            selectedIds.includes(String(item.id || item.capability_id))
        );
    
        try {
            const promises = selectedItems.map(item => {
                const capabilityId = item.id || item.capability_id;
                const capabilityName = item.name || item.capability_name;
                const priority = item.strategic_importance || 'medium';
                const level = item.level || item.capability_level || 1;

                const today = new Date();
                const daysToAdd = priority === 'critical' ? 30 : priority === 'high' ? 60 : priority === 'medium' ? 90 : 180;
                const endDate = new Date(today);
                endDate.setDate(endDate.getDate() + daysToAdd);

                return Platform.fetch('/capability-map/api/roadmap/gaps/add-from-capability', {
                    method: 'POST',
                    body: {
                        capability_id: parseInt(capabilityId),
                        capability_type: 'business',
                        capability_name: capabilityName,
                        level: parseInt(level),
                        gap_type: 'coverage',
                        priority: priority,
                        start_date: today.toISOString().split('T')[0],
                        end_date: endDate.toISOString().split('T')[0],
                        color: tokenHex('muted-foreground'),
                        create_work_packages: false
                    },
                    silent: true
                }).catch(() => ({ success: false }));
            });

            const results = await Promise.all(promises);
    
            const successful = results.filter(r => r.success).length;
            const failed = results.length - successful;
    
            if (successful > 0) {
                showToastWithUndo(`Added ${successful} capabilities to roadmap`, 'success', () => {
                    // Undo logic would go here
                });
                deselectAllCapabilities(tabType);
    
                // Reload roadmap if on roadmap tab
                const activeTab = document.querySelector('[data-tab-target="roadmap"]');
                if (activeTab && activeTab.getAttribute('aria-selected') === 'true') {
                    loadRoadmapData();
                }
            }
    
            if (failed > 0) {
                showToast(`${failed} capabilities failed to add (may already be on roadmap)`, 'warning');
            }
    
        } catch (error) {
            showToast('Error adding capabilities to roadmap', 'error');
        }
    }
    
    // ============================================
    // PHASE 2: QUICK-ADD MODAL
    // ============================================
    
    function openQuickAddModal() {
        Platform.modal.open('quick-add-modal');
        loadQuickAddCapabilities();
    }
    
    function closeQuickAddModal() {
        Platform.modal.close('quick-add-modal');
    }
    
    async function loadQuickAddCapabilities() {
        const searchInput = getEl('quick-add-search').value.toLowerCase();
        const typeFilter = getEl('quick-add-type-filter').value;
        const levelFilter = getEl('quick-add-level-filter').value;
    
        // Get all capabilities from current tab data
        let capabilities = [...tableData.unified.data];
    
        // Apply filters
        if (searchInput) {
            capabilities = capabilities.filter(cap =>
                (cap.name || cap.capability_name || '').toLowerCase().includes(searchInput)
            );
        }
    
        if (typeFilter) {
            capabilities = capabilities.filter(cap => cap.type === typeFilter);
        }
    
        if (levelFilter) {
            capabilities = capabilities.filter(cap => String(cap.level || cap.capability_level) === levelFilter);
        }
    
        // Filter out capabilities already on roadmap
        capabilities = capabilities.filter(cap => !cap.on_roadmap);
    
        renderQuickAddResults(capabilities);
    }
    
    function renderQuickAddResults(capabilities) {
        const resultsContainer = getEl('quick-add-results');
    
        if (capabilities.length === 0) {
            safeHTML(resultsContainer, `
                <div class="text-center py-8 text-muted-foreground">
                    <i data-lucide="inbox" class="w-8 h-8 mx-auto mb-2"></i>
                    <p>No capabilities found</p>
                </div>
            `);
            lucide.createIcons();
            return;
        }
    
        safeHTML(resultsContainer, capabilities.map(cap => {
            const capId = cap.id || cap.capability_id;
            const capName = cap.name || cap.capability_name;
            const level = cap.level || cap.capability_level || 1;
            const priority = cap.strategic_importance || 'medium';
            const type = cap.type || 'Business';
    
            return `
                <div class="flex items-center justify-between p-3 border border-border rounded-lg hover:bg-accent">
                    <div class="flex-1">
                        <div class="font-medium text-foreground">${escapeHtml(capName)}</div>
                        <div class="text-sm text-muted-foreground">
                            <span class="px-2 py-0.5 rounded bg-muted text-muted-foreground text-xs mr-2">${escapeHtml(type)}</span>
                            <span class="px-2 py-0.5 rounded bg-primary/10 text-primary text-xs">L${level}</span>
                        </div>
                    </div>
                    <button type="button" data-cm-action="quick-add-roadmap" data-cap-id="${escapeAttr(capId)}" data-cap-name="${escapeAttr(capName)}" data-cap-level="${escapeAttr(level)}" data-cap-priority="${escapeAttr(priority)}" aria-label="Add ${escapeAttr(capName)} to roadmap"
                            class="px-3 py-1.5 bg-primary text-primary-foreground text-sm rounded-md hover:bg-purple-700">
                        <i data-lucide="plus" class="w-4 h-4 inline mr-1"></i>
                        Add
                    </button>
                </div>
            `;
        }).join(''));
    
        lucide.createIcons();
    }
    
    function quickAddToRoadmap(capabilityId, capabilityName, capabilityType, level, priority) {
        // Use the existing addToRoadmap function
        addToRoadmap(capabilityId, capabilityName, capabilityType, level, priority);
        closeQuickAddModal();
    }
    
    // ============================================
    // PHASE 2: DRAG & DROP
    // ============================================
    
    function handleDragStart(event, capabilityId, capabilityName, capabilityType, level, priority) {
        dragDropState.draggedItem = {
            capabilityId,
            capabilityName,
            capabilityType,
            level,
            priority
        };
        dragDropState.draggedElement = event.target;
        event.target.style.opacity = '0.5';
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/html', event.target.innerHTML);
    }
    
    function handleDragEnd(event) {
        event.target.style.opacity = '1';
        dragDropState.draggedItem = null;
        dragDropState.draggedElement = null;
    
        // Remove drop zone highlights
        document.querySelectorAll('.roadmap-drop-zone').forEach(zone => {
            zone.classList.remove('bg-purple-100', 'border-purple-400');
        });
    }
    
    function handleTimelineDragOver(event) {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    
        // Highlight drop zone
        const dropZone = event.target.closest('.roadmap-drop-zone');
        if (dropZone) {
            dropZone.classList.add('bg-purple-100', 'border-purple-400');
        }
    }
    
    function handleTimelineDrop(event, periodStart, periodEnd) {
        event.preventDefault();
    
        if (!dragDropState.draggedItem) return;
    
        const { capabilityId, capabilityName, capabilityType, level, priority } = dragDropState.draggedItem;
    
        // Open add-to-roadmap modal with pre-filled dates
        getEl('add-roadmap-cap-id').value = capabilityId;
        getEl('add-roadmap-type').value = capabilityType;
        getEl('add-roadmap-level').value = level;
        getEl('add-roadmap-cap-name').textContent = capabilityName;
        getEl('add-roadmap-cap-type').textContent = capabilityType === 'business' ? 'Business Capability' : 'Technical (ACM)';
        getEl('add-roadmap-cap-level').textContent = `Level ${level}`;
        getEl('add-roadmap-priority').value = priority || 'medium';
        getEl('add-roadmap-start-date').value = periodStart;
        getEl('add-roadmap-end-date').value = periodEnd;
        Platform.modal.open('add-roadmap-modal');
    
        // Remove drop zone highlight
        event.target.classList.remove('bg-purple-100', 'border-purple-400');
    }
    
    // ============================================
    // PHASE 3: KEYBOARD SHORTCUTS
    // ============================================
    
    document.addEventListener('keydown', function(event) {
        // Ignore if typing in input/textarea
        if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA') {
            return;
        }
    
        // R = Add to roadmap
        if (event.key === 'r' || event.key === 'R') {
            const activeTab = document.querySelector('[role="tab"][aria-selected="true"]');
            if (activeTab) {
                const tabType = activeTab.getAttribute('data-tab-target');
                if (tableData[tabType] && tableData[tabType].selected.size > 0) {
                    bulkAddToRoadmap(tabType);
                }
            }
        }
    
        // Ctrl+A = Select all
        if (event.ctrlKey && event.key === 'a') {
            event.preventDefault();
            const activeTab = document.querySelector('[role="tab"][aria-selected="true"]');
            if (activeTab) {
                const tabType = activeTab.getAttribute('data-tab-target');
                if (tableData[tabType]) {
                    selectAllCapabilities(tabType);
                }
            }
        }
    
        // Escape = Close modals
        if (event.key === 'Escape') {
            closeAddRoadmapModal();
            closeQuickAddModal();
            closeEditGapModal();
            closeEditWPModal();
        }
    
        // ? = Show keyboard shortcuts help
        if (event.key === '?') {
            showKeyboardShortcutsModal();
        }
    });
    
    function showKeyboardShortcutsModal() {
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 bg-black/50 overflow-y-auto h-full w-full z-50';
        safeHTML(modal, `
            <div class="relative top-20 mx-auto p-5 border w-96 shadow-lg rounded-md bg-card">
                <div class="flex justify-between items-center mb-4 pb-3 border-b">
                    <h3 class="text-lg font-bold text-foreground">Keyboard Shortcuts</h3>
                    <button type="button" data-cm-action="close-modal" aria-label="Close dialog" class="text-muted-foreground hover:text-muted-foreground">
                        <i data-lucide="x" class="w-6 h-6"></i>
                    </button>
                </div>
                <div class="space-y-3">
                    <div class="flex justify-between">
                        <span class="text-muted-foreground">Add to Roadmap</span>
                        <kbd class="px-2 py-1 bg-muted border border-input rounded text-sm">R</kbd>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-muted-foreground">Select All</span>
                        <kbd class="px-2 py-1 bg-muted border border-input rounded text-sm">Ctrl+A</kbd>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-muted-foreground">Close Modal</span>
                        <kbd class="px-2 py-1 bg-muted border border-input rounded text-sm">Esc</kbd>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-muted-foreground">Show This Help</span>
                        <kbd class="px-2 py-1 bg-muted border border-input rounded text-sm">?</kbd>
                    </div>
                </div>
            </div>
        `);
        document.body.appendChild(modal);
        lucide.createIcons();
    }
    
    // ============================================
    // PHASE 3: RIGHT-CLICK CONTEXT MENU
    // ============================================
    
    function handleRowRightClick(event, capabilityId, capabilityName, tabType) {
        event.preventDefault();
    
        // Remove existing context menus
        document.querySelectorAll('.context-menu').forEach(menu => menu.remove());
    
        const menu = document.createElement('div');
        menu.className = 'context-menu fixed bg-card border border-border rounded-lg shadow-lg z-50 py-1';
        menu.style.left = event.pageX + 'px';
        menu.style.top = event.pageY + 'px';
    
        const item = tableData[tabType].data.find(i => String(i.id || i.capability_id) === capabilityId);
        const level = item?.level || item?.capability_level || 1;
        const priority = item?.strategic_importance || 'medium';
    
        safeHTML(menu, `
            <button type="button" data-cm-action="open-mapping-modal" data-cap-id="${escapeAttr(capabilityId)}" data-cap-name="${escapeAttr(capabilityName)}"
                    class="w-full text-left px-4 py-2 hover:bg-accent flex items-center gap-2">
                <i data-lucide="layout-grid" class="w-4 h-4 text-primary"></i>
                <span>Map to Applications</span>
            </button>
            <button type="button" data-cm-action="add-to-roadmap" data-cap-id="${escapeAttr(capabilityId)}" data-cap-name="${escapeAttr(capabilityName)}" data-cap-level="${escapeAttr(level)}" data-cap-priority="${escapeAttr(priority)}"
                    class="w-full text-left px-4 py-2 hover:bg-accent flex items-center gap-2">
                <i data-lucide="map" class="w-4 h-4 text-primary"></i>
                <span>Add to Roadmap</span>
            </button>
            <hr class="my-1">
            <button type="button" data-cm-action="toggle-row-selection" data-cap-id="${escapeAttr(capabilityId)}" data-tab="${escapeAttr(tabType)}"
                    class="w-full text-left px-4 py-2 hover:bg-accent flex items-center gap-2">
                <i data-lucide="check-square" class="w-4 h-4 text-muted-foreground"></i>
                <span>${tableData[tabType].selected.has(capabilityId) ? 'Deselect' : 'Select'}</span>
            </button>
        `);
    
        document.body.appendChild(menu);
        lucide.createIcons();
    
        // Close menu on click outside
        setTimeout(() => {
            document.addEventListener('click', function closeMenu() {
                menu.remove();
                document.removeEventListener('click', closeMenu);
            });
        }, 100);
    }
    
    // ============================================
    // PHASE 3: UNDO SUPPORT
    // ============================================
    
    // Toast — delegates to canonical Platform.toast (core/04-toast.js)
    function showToastWithUndo(message, type, undoCallback) {
        // Show toast via Platform.toast, then overlay an undo button if needed
        if (window.Platform && window.Platform.toast) {
            const t = type || 'info';
            const method = window.Platform.toast[t] || window.Platform.toast.info;
            if (undoCallback) {
                // Use longer duration for undo toasts to give user time to react
                const id = method.call(window.Platform.toast, String(message), { duration: (typeof UNDO_TIMEOUT !== 'undefined' ? UNDO_TIMEOUT : 5000) });
                // Attach undo button to the toast element
                const el = document.getElementById(id);
                if (el) {
                    const undoBtn = document.createElement('button');
                    undoBtn.textContent = 'Undo';
                    undoBtn.className = 'ml-2 px-3 py-1 text-xs font-medium bg-primary/20 hover:bg-primary/30 rounded transition-colors';
                    undoBtn.onclick = () => {
                        undoCallback();
                        window.Platform.toast.dismiss(id);
                    };
                    el.querySelector('.flex-1') ? el.querySelector('.flex-1').appendChild(undoBtn) : el.appendChild(undoBtn);
                }
                return id;
            }
            return method.call(window.Platform.toast, String(message));
        }
        if (window.showToast) { return window.showToast(message, type); }
    }

    // ============================================
    // END ROADMAP TAB FUNCTIONS
    // ============================================
    
    // ============================================
    // PROCESS GAP TAB FUNCTIONS
    // ============================================
    
    // Process Gap Data Store
    const processGapData = {
        data: [],
        filtered: [],
        currentPage: 1,
        pageSize: 10,
        sortColumn: 'business_impact',
        sortDirection: 'desc',
        loaded: false
    };
    
    // Load process gap data from API
    async function loadProcessGapData() {
        if (processGapData.loaded) {
            // Already loaded, just update the display
            updateProcessGapTable();
            return;
        }
    
        const tableBody = getEl('process-gap-table-body');
        safeHTML(tableBody, `
            <tr>
                <td colspan="10" class="px-6 py-8 text-center text-muted-foreground">
                    <i data-lucide="loader-2" class="w-8 h-8 animate-spin text-primary mx-auto mb-4"></i>
                    <p>Loading process gap data...</p>
                </td>
            </tr>
        `);
        lucide.createIcons();
    
        try {
            const data = await fetchWithTimeout('/capability-map/api/process-gaps');
    
            if (data.error) {
                throw new Error(data.error);
            }
    
            processGapData.data = data.process_gaps || [];
            processGapData.filtered = [...processGapData.data];
            processGapData.loaded = true;
    
            // Update statistics
            const stats = data.statistics || {};
            getEl('process-total-count').textContent = stats.total_processes || 0;
            getEl('process-unmapped-count').textContent = stats.unmapped_processes || 0;
            getEl('process-critical-count').textContent = stats.critical_gaps || 0;
            getEl('process-coverage-pct').textContent = (stats.coverage_percentage || 0) + '%';
            getEl('process-automation-pct').textContent = (stats.automation_coverage || 0) + '%';
    
            // Update table
            updateProcessGapTable();
    
        } catch (error) {
            const retryButton = `<button type="button" data-cm-action="process-gap-retry" class="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-purple-700 transition-colors">
                <i data-lucide="refresh-cw" class="w-4 h-4 inline mr-2"></i>
                Retry
            </button>`;
            safeHTML(tableBody, `
                <tr>
                    <td colspan="10" class="px-6 py-8 text-center">
                        <i data-lucide="alert-circle" class="w-8 h-8 text-destructive mx-auto mb-4"></i>
                        <p class="text-destructive font-medium mb-2">Error Loading Process Gap Data</p>
                        <p class="text-sm text-muted-foreground">${escapeHtml(error.message || 'Unable to load process gap data. Please check your connection and try again.')}</p>
                        ${retryButton}
                    </td>
                </tr>
            `);
            lucide.createIcons();
        }
    }
    
    // Filter process gap table
    function filterProcessGapTable() {
        const typeFilter = getEl('process-type-filter').value;
        const categoryFilter = getEl('process-category-filter').value;
        const levelFilter = getEl('process-level-filter').value;
        const gapStatusFilter = getEl('process-gap-status-filter').value;
        const searchFilter = getEl('process-search-filter').value.toLowerCase();
    
        processGapData.filtered = processGapData.data.filter(item => {
            const matchesType = !typeFilter || item.process_type === typeFilter;
            const catPrefix = categoryFilter ? categoryFilter.split('.')[0] + '.' : '';
            const matchesCategory = !categoryFilter || (item.process_code && (item.process_code === categoryFilter || item.process_code.startsWith(catPrefix)));
            const matchesLevel = !levelFilter || String(item.level) === levelFilter;
            const matchesGapStatus = !gapStatusFilter || item.gap_status === gapStatusFilter;
            const matchesSearch = !searchFilter ||
                (item.name || '').toLowerCase().includes(searchFilter) ||
                (item.process_code || '').toLowerCase().includes(searchFilter) ||
                (item.process_owner || '').toLowerCase().includes(searchFilter);
    
            return matchesType && matchesCategory && matchesLevel && matchesGapStatus && matchesSearch;
        });
    
        processGapData.currentPage = 1;
        updateProcessGapTable();
    }
    
    // Clear process gap filters
    function clearProcessGapFilters() {
        getEl('process-type-filter').value = '';
        getEl('process-category-filter').value = '';
        getEl('process-level-filter').value = '';
        getEl('process-gap-status-filter').value = '';
        getEl('process-search-filter').value = '';
        filterProcessGapTable();
    }
    
    // Sort process gap table
    function sortProcessGapTable(column) {
        if (processGapData.sortColumn === column) {
            processGapData.sortDirection = processGapData.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            processGapData.sortColumn = column;
            processGapData.sortDirection = column === 'business_impact' || column === 'avg_automation_coverage' ? 'desc' : 'asc';
        }
    
        const severityOrder = { 'critical': 0, 'high': 1, 'medium': 2, 'low': 3 };
    
        processGapData.filtered.sort((a, b) => {
            let aVal = a[column];
            let bVal = b[column];
    
            // Handle gap_severity specially
            if (column === 'gap_severity') {
                aVal = severityOrder[aVal] || 4;
                bVal = severityOrder[bVal] || 4;
            }
    
            if (typeof aVal === 'string') {
                aVal = (aVal || '').toLowerCase();
                bVal = (bVal || '').toLowerCase();
            }
    
            if (processGapData.sortDirection === 'asc') {
                return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
            } else {
                return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
            }
        });
    
        updateProcessGapTable();
    }
    
    // Update process gap table
    function updateProcessGapTable() {
        const tableBody = getEl('process-gap-table-body');
        if (!tableBody) return;
    
        const startIndex = (processGapData.currentPage - 1) * processGapData.pageSize;
        const endIndex = startIndex + processGapData.pageSize;
        const pageData = processGapData.filtered.slice(startIndex, endIndex);
    
        if (pageData.length === 0) {
            safeHTML(tableBody, `
                <tr>
                    <td colspan="10" class="px-6 py-8 text-center text-muted-foreground">
                        <i data-lucide="check-circle" class="w-8 h-8 text-emerald-500 mx-auto mb-4"></i>
                        <p class="font-medium">No Process Gaps Found</p>
                        <p class="text-sm">All business processes have application support, or no processes match your filters.</p>
                    </td>
                </tr>
            `);
            lucide.createIcons();
            updateProcessGapPagination();
            return;
        }
    
        safeHTML(tableBody, pageData.map(item => generateProcessGapRow(item)).join(''));
        lucide.createIcons();
        updateProcessGapPagination();
    }
    
    // Generate process gap table row
    function generateProcessGapRow(item) {
        // Process type badge colors
        const typeColors = {
            'core': 'bg-purple-100 text-purple-800',
            'support': 'bg-primary/10 text-info-emphasis',
            'management': 'bg-muted text-foreground'
        };
        const typeColor = typeColors[item.process_type] || 'bg-muted text-foreground';
    
        // Gap severity badge colors
        const severityColors = {
            'critical': 'bg-destructive text-primary-foreground',
            'high': 'bg-orange-500 text-primary-foreground',
            'medium': 'bg-amber-500 text-primary-foreground',
            'low': 'bg-emerald-500 text-primary-foreground'
        };
        const severityColor = severityColors[item.gap_severity] || 'bg-muted/50 text-primary-foreground';
    
        // Gap status badge colors
        const statusColors = {
            'no_coverage': 'bg-destructive/10 text-red-800',
            'minimal_automation': 'bg-orange-100 text-orange-800',
            'partial_automation': 'bg-amber-500/10 text-yellow-800',
            'well_automated': 'bg-emerald-500/10 text-green-800'
        };
        const statusColor = statusColors[item.gap_status] || 'bg-muted text-foreground';
    
        // Gap status display text
        const statusText = {
            'no_coverage': 'No App Coverage',
            'minimal_automation': 'Minimal Automation',
            'partial_automation': 'Partial Automation',
            'well_automated': 'Well Automated'
        };
    
        // Applications list
        let appsHtml = '';
        if (item.mapping_count > 0 && item.applications && item.applications.length > 0) {
            const appNames = item.applications.slice(0, 2).map(a => escapeHtml(a.app_name)).join(', ');
            const moreCount = item.applications.length > 2 ? ` +${item.applications.length - 2}` : '';
            appsHtml = `<span class="text-sm">${appNames}${moreCount}</span>`;
        } else {
            appsHtml = '<span class="text-sm text-destructive font-medium">No Applications</span>';
        }
    
        // Compliance badges
        let complianceBadges = '';
        if (item.sox_relevant) complianceBadges += '<span class="px-1 py-0.5 text-[10px] rounded bg-primary/10 text-primary mr-1">SOX</span>';
        if (item.gdpr_relevant) complianceBadges += '<span class="px-1 py-0.5 text-[10px] rounded bg-emerald-500/10 text-emerald-700">GDPR</span>';
    
        return `
            <tr class="hover:bg-accent">
                <td class="px-6 py-4 whitespace-nowrap">
                    <div class="text-sm font-medium text-foreground">${escapeHtml(item.name || 'Unknown')}</div>
                    <div class="text-xs text-muted-foreground">${escapeHtml(item.process_code || '')}</div>
                    ${complianceBadges ? `<div class="mt-1">${complianceBadges}</div>` : ''}
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 text-xs rounded-full ${typeColor}">
                        ${(item.process_type || 'unknown').charAt(0).toUpperCase() + (item.process_type || 'unknown').slice(1)}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 text-xs rounded-full bg-muted text-foreground">
                        L${item.level} - ${escapeHtml(item.level_name)}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">
                    ${escapeHtml(item.process_owner || 'Unassigned')}
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    ${appsHtml}
                    <div class="text-xs text-muted-foreground">${item.mapping_count} app(s)</div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <div class="flex items-center">
                        <div class="w-16 bg-border rounded-full h-2 mr-2">
                            <div class="h-2 rounded-full ${item.avg_automation_coverage >= 70 ? 'bg-emerald-500' : item.avg_automation_coverage >= 30 ? 'bg-amber-500' : 'bg-destructive'}" style="width: ${item.avg_automation_coverage}%"></div>
                        </div>
                        <span class="text-xs">${item.avg_automation_coverage}%</span>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <div class="flex items-center">
                        <div class="w-20 bg-border rounded-full h-2 mr-2">
                            <div class="bg-gradient-to-r from-purple-500 to-red-500 h-2 rounded-full" style="width: ${item.business_impact}%"></div>
                        </div>
                        <span class="text-xs font-medium">${item.business_impact}%</span>
                    </div>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 text-xs rounded-full ${severityColor} font-medium">
                        ${(item.gap_severity || 'unknown').toUpperCase()}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap">
                    <span class="px-2 py-1 text-xs rounded-full ${statusColor}">
                        ${statusText[item.gap_status] || item.gap_status}
                    </span>
                </td>
                <td class="px-6 py-4 whitespace-nowrap text-sm">
                    <button type="button" data-cm-action="open-process-mapping" data-process-id="${escapeAttr(item.id)}" data-process-name="${escapeAttr(item.name || 'Unknown')}" data-process-code="${escapeAttr(item.process_code || '')}" data-process-type="${escapeAttr(item.process_type || '')}" aria-label="Map process ${escapeAttr(item.name || 'Unknown')}"
                            class="text-primary hover:text-primary/90 text-sm font-medium">
                        <i data-lucide="map" class="w-4 h-4 inline mr-1"></i>Map
                    </button>
                </td>
            </tr>
        `;
    }
    
    // Process gap pagination functions
    function updateProcessGapPagination() {
        const totalPages = Math.ceil(processGapData.filtered.length / processGapData.pageSize) || 1;
        const startRecord = processGapData.filtered.length === 0 ? 0 : (processGapData.currentPage - 1) * processGapData.pageSize + 1;
        const endRecord = Math.min(processGapData.currentPage * processGapData.pageSize, processGapData.filtered.length);
    
        getEl('process-gap-start-record').textContent = startRecord;
        getEl('process-gap-end-record').textContent = endRecord;
        getEl('process-gap-total-records').textContent = processGapData.filtered.length;
        getEl('process-gap-current-page').textContent = processGapData.currentPage;
        getEl('process-gap-total-pages').textContent = totalPages;
    
        getEl('process-gap-prev-btn').disabled = processGapData.currentPage === 1;
        getEl('process-gap-next-btn').disabled = processGapData.currentPage === totalPages;
    }
    
    function previousProcessGapPage() {
        if (processGapData.currentPage > 1) {
            processGapData.currentPage--;
            updateProcessGapTable();
        }
    }
    
    function nextProcessGapPage() {
        const totalPages = Math.ceil(processGapData.filtered.length / processGapData.pageSize);
        if (processGapData.currentPage < totalPages) {
            processGapData.currentPage++;
            updateProcessGapTable();
        }
    }
    
    function changeProcessGapPageSize() {
        processGapData.pageSize = parseInt(getEl('process-gap-page-size').value);
        processGapData.currentPage = 1;
        updateProcessGapTable();
    }
    
    // ============================================
    // Application Mapping Modal Functions
    // ============================================
    
    let currentCapabilityId = null;
    let currentCapabilityName = '';
    let applicationsData = [];
    let selectedApplications = new Map(); // Map of app_id -> mapping data
    let currentModalRequestId = 0; // AUDIT-CAP-002: Request ID to prevent race conditions
    
    // Make function globally accessible
    window.openMappingModal = async function(capabilityId, capabilityName) {
        // ARCH-064: the dialog is not in the page until it is first opened.
        if (!await window.CapabilityMapModals.ensure('mapping-modal')) { return; }

        // Ensure capabilityId is always a string to preserve precision for large Snowflake IDs
        currentCapabilityId = String(capabilityId);
        currentCapabilityName = capabilityName;
        selectedApplications.clear();
        applicationsData = [];
    
        // AUDIT-CAP-002: Increment request ID to invalidate any in-flight requests
        const requestId = ++currentModalRequestId;
    
        // Update modal title
        const modalNameEl = getEl('modal-capability-name');
        if (modalNameEl) {
            modalNameEl.textContent = capabilityName;
        }
    
        // Show modal
        if (getEl('mapping-modal')) {
            Platform.modal.open('mapping-modal');
            document.body.classList.add('overflow-hidden');
        }
    
        // AUDIT-CAP-003: Show loading state in dropdowns and list while data loads
        setModalLoadingState(true);
    
        // Load applications for this capability
        await loadApplicationsForCapability(capabilityId, requestId);
    }
    
    window.closeMappingModal = function() {
        if (getEl('mapping-modal')) {
            Platform.modal.close('mapping-modal');
            document.body.classList.remove('overflow-hidden');
        }
        currentCapabilityId = null;
        currentCapabilityName = '';
        selectedApplications.clear();
        applicationsData = [];
    }

    // Row action "View Details" — opens a read-only detail dialog for a
    // capability. Fetches the canonical detail record and renders it with a
    // dynamically-created Platform modal (no inline handlers; CSP-safe).
    // Missing values render as an em dash, never a fabricated 0/blank.
    window.openCapabilityDetail = async function(capabilityId, capabilityName) {
        const DASH = '—';
        const esc = (window.Platform && Platform.sanitize && Platform.sanitize.escape)
            ? Platform.sanitize.escape
            : (s => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
                {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
        const val = v => (v === null || v === undefined || v === '') ? DASH : esc(v);

        const modalId = 'capability-detail-modal';
        // Destroy any prior instance so re-opening always shows fresh data.
        if (window.Platform && Platform.modal && typeof Platform.modal.destroy === 'function') {
            Platform.modal.destroy(modalId);
        }

        let data;
        try {
            const resp = await Platform.fetch(`/enterprise/capabilities/${encodeURIComponent(capabilityId)}`, { silent: true });
            data = (resp && resp.data) ? resp.data : resp;
        } catch (e) {
            if (window.Platform && Platform.toast) {
                Platform.toast.error('Could not load capability details. Please try again.');
            }
            return;
        }
        if (!data) {
            if (window.Platform && Platform.toast) Platform.toast.error('Capability not found.');
            return;
        }

        const rows = [
            ['Domain', val(data.business_domain)],
            ['Level', val(data.level)],
            ['Category', val(data.category)],
            ['Strategic Importance', val(data.strategic_importance)],
            ['Current Maturity', val(data.current_maturity_level)],
            ['Target Maturity', val(data.target_maturity_level)],
            ['Business Owner', val(data.business_owner)],
            ['IT Owner', val(data.it_owner)],
            ['Mapped Applications', val(data.application_count)]
        ];
        let content = '<dl class="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">';
        rows.forEach(([label, value]) => {
            content += '<dt class="font-medium text-muted-foreground">' + esc(label) + '</dt>'
                     + '<dd class="text-foreground">' + value + '</dd>';
        });
        content += '</dl>';

        if (data.description) {
            content += '<div class="mt-4"><p class="font-medium text-muted-foreground text-sm mb-1">Description</p>'
                     + '<p class="text-sm text-foreground whitespace-pre-line">' + esc(data.description) + '</p></div>';
        }

        const apps = Array.isArray(data.applications) ? data.applications : [];
        if (apps.length) {
            content += '<div class="mt-4"><p class="font-medium text-muted-foreground text-sm mb-2">Applications</p><ul class="space-y-1">';
            apps.forEach(a => {
                content += '<li class="text-sm text-foreground flex items-center justify-between border-b border-border py-1">'
                         + '<span>' + esc(a.name) + '</span>'
                         + '<span class="text-xs text-muted-foreground">' + val(a.support_level) + '</span></li>';
            });
            content += '</ul></div>';
        }

        Platform.modal.create({
            id: modalId,
            title: (data.name || capabilityName || 'Capability') + ' — Details',
            size: 'lg',
            content: content,
            buttons: [{ label: 'Close', variant: 'secondary' }]
        });
        Platform.modal.open(modalId);
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            window.lucide.createIcons();
        }
    };

    // AUDIT-CAP-003: Set loading state for modal dropdowns and application list
    function setModalLoadingState(isLoading) {
        const typeFilter = getEl('filter-type');
        const domainFilter = getEl('filter-domain');
        const container = getEl('applications-list');
    
        if (isLoading) {
            if (typeFilter) {
                safeHTML(typeFilter, '<option value="">Loading...</option>');
                typeFilter.disabled = true;
            }
            if (domainFilter) {
                safeHTML(domainFilter, '<option value="">Loading...</option>');
                domainFilter.disabled = true;
            }
            if (container) {
                safeHTML(container, '<div class="flex items-center justify-center py-8 text-muted-foreground"><svg class="animate-spin h-5 w-5 mr-2" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>Loading applications...</div>');
            }
        } else {
            if (typeFilter) typeFilter.disabled = false;
            if (domainFilter) domainFilter.disabled = false;
        }
    }
    
    async function loadApplicationsForCapability(capabilityId, requestId) {
        try {
            // Ensure capabilityId is a string to preserve precision
            const id = String(capabilityId);
            const data = await Platform.fetch(`/capability-map/api/capability/${id}/applications`, { silent: true });
    
            // AUDIT-CAP-002: Check if this request is still the current one
            // If user opened a different capability modal while we were fetching, discard this response
            if (requestId !== currentModalRequestId) {
                return;
            }
    
            if (data.error) {
                setModalLoadingState(false);
                showNotification('Error loading applications: ' + data.error, 'error');
                return;
            }
    
            applicationsData = data.applications || [];
    
            // Pre-select mapped applications
            selectedApplications.clear();
            applicationsData.forEach(app => {
                if (app.is_mapped) {
                    selectedApplications.set(app.id, {
                        application_id: app.id,
                        mapping_id: app.mapping_id,
                        mapping: {
                            support_level: app.support_level,
                            coverage_percentage: app.coverage_percentage,
                            support_quality: app.support_quality,
                            relationship_type: app.relationship_type,
                            relationship_strength: app.relationship_strength,
                            dependency_level: app.dependency_level,
                            gap_status: app.gap_status,
                            gap_description: app.gap_description,
                            gap_impact: app.gap_impact,
                            priority: app.priority,
                            integration_complexity: app.integration_complexity,
                            is_active: app.is_active
                        }
                    });
                }
            });
    
            // Reset filters
            const searchInput = getEl('application-search');
            const typeFilter = getEl('filter-type');
            const domainFilter = getEl('filter-domain');
            const statusFilter = getEl('filter-status');
            const sortSelect = getEl('sort-applications');
    
            if (searchInput) searchInput.value = '';
            if (typeFilter) typeFilter.value = '';
            if (domainFilter) domainFilter.value = '';
            if (statusFilter) statusFilter.value = 'all';
            if (sortSelect) sortSelect.value = 'name-asc';
    
            // AUDIT-CAP-003: Remove loading state before populating
            setModalLoadingState(false);
    
            // Populate filter options (now guaranteed to run after applicationsData is populated)
            populateFilterOptions();
    
            // Render applications
            renderApplicationsList();
    
            // Focus search input
            setTimeout(() => {
                if (searchInput) searchInput.focus();
            }, 100);
        } catch (error) {
            // AUDIT-CAP-002: Only show error if this is still the current request
            if (requestId === currentModalRequestId) {
                setModalLoadingState(false);
                showNotification('Error loading applications', 'error');
            }
        }
    }
    
    function renderApplicationsList() {
        const container = getEl('applications-list');
        const searchTerm = getEl('application-search')?.value.toLowerCase() || '';
        const filterType = getEl('filter-type')?.value || '';
        const filterDomain = getEl('filter-domain')?.value || '';
        const filterStatus = getEl('filter-status')?.value || 'all';
        const sortBy = getEl('sort-applications')?.value || 'name-asc';
    
        if (!container) return;
    
        // Filter applications
        let filtered = applicationsData.filter(app => {
            // Search filter
            const matchesSearch = !searchTerm ||
                app.name.toLowerCase().includes(searchTerm) ||
                app.type.toLowerCase().includes(searchTerm) ||
                app.domain.toLowerCase().includes(searchTerm) ||
                (app.description && app.description.toLowerCase().includes(searchTerm));
    
            // Type filter
            const matchesType = !filterType || app.type === filterType;
    
            // Domain filter
            const matchesDomain = !filterDomain || app.domain === filterDomain;
    
            // Status filter
            let matchesStatus = true;
            if (filterStatus === 'mapped') {
                matchesStatus = app.is_mapped === true;
            } else if (filterStatus === 'unmapped') {
                matchesStatus = app.is_mapped !== true;
            }
    
            return matchesSearch && matchesType && matchesDomain && matchesStatus;
        });
    
        // Sort applications
        filtered.sort((a, b) => {
            switch(sortBy) {
                case 'name-asc':
                    return a.name.localeCompare(b.name);
                case 'name-desc':
                    return b.name.localeCompare(a.name);
                case 'type-asc':
                    return a.type.localeCompare(b.type);
                case 'mapped-first':
                    if (a.is_mapped && !b.is_mapped) return -1;
                    if (!a.is_mapped && b.is_mapped) return 1;
                    return a.name.localeCompare(b.name);
                case 'unmapped-first':
                    if (!a.is_mapped && b.is_mapped) return -1;
                    if (a.is_mapped && !b.is_mapped) return 1;
                    return a.name.localeCompare(b.name);
                default:
                    return 0;
            }
        });
    
        // Update counts
        const filteredCountEl = getEl('filtered-count');
        const totalCountEl = getEl('total-count');
        const selectedCountEl = getEl('selected-count');
        if (filteredCountEl) filteredCountEl.textContent = filtered.length;
        if (totalCountEl) totalCountEl.textContent = applicationsData.length;
        if (selectedCountEl) selectedCountEl.textContent = selectedApplications.size;
    
        if (filtered.length === 0) {
            safeHTML(container, `
                <div class="text-center py-12 text-muted-foreground">
                    <i data-lucide="search-x" class="w-12 h-12 mx-auto mb-3 text-muted-foreground"></i>
                    <p class="text-lg font-medium">No applications found</p>
                    <p class="text-sm mt-1">Try adjusting your search or filters</p>
                </div>
            `);
            lucide.createIcons();
            return;
        }
    
        safeHTML(container, filtered.map(app => {
            const isSelected = selectedApplications.has(app.id);
            const mapping = selectedApplications.get(app.id);
    
            return `
                <div class="border rounded-lg p-4 transition-all ${isSelected ? 'bg-primary/5 border-primary/30 shadow-sm' : 'bg-background border-border hover:border-input hover:shadow-sm'}">
                    <div class="flex items-start justify-between">
                        <div class="flex items-start space-x-3 flex-1">
                            <input
                                type="checkbox"
                                ${isSelected ? 'checked' : ''}
                                data-cm-change="toggle-app"
                                data-app-id="${escapeAttr(app.id)}"
                                class="mt-1 h-5 w-5 text-primary focus-visible:ring-ring border-input rounded cursor-pointer"
                                title="${isSelected ? 'Deselect' : 'Select'} ${escapeHtml(app.name)}"
                                aria-label="${isSelected ? 'Deselect' : 'Select'} ${escapeHtml(app.name)}"
                            />
                            <div class="flex-1">
                                <div class="flex items-center space-x-2">
                                    <div class="font-medium text-foreground">${escapeHtml(app.name)}</div>
                                    ${app.is_mapped ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-emerald-500/10 text-success rounded-full"><i data-lucide="check-circle" class="w-3 h-3 mr-1"></i>Mapped</span>' : ''}
                                </div>
                                <div class="flex items-center space-x-2 mt-1 text-sm text-muted-foreground">
                                    <span class="flex items-center">
                                        <i data-lucide="layers" class="w-3 h-3 mr-1"></i>
                                        ${escapeHtml(app.type)}
                                    </span>
                                    <span>•</span>
                                    <span class="flex items-center">
                                        <i data-lucide="building" class="w-3 h-3 mr-1"></i>
                                        ${escapeHtml(app.domain)}
                                    </span>
                                </div>
                                ${app.description ? `<div class="text-xs text-muted-foreground mt-2 line-clamp-2">${escapeHtml(app.description)}</div>` : ''}
                            </div>
                        </div>
                        ${app.is_mapped && app.mapping_id ? `
                            <button
                                type="button"
                                data-cm-action="delete-mapping"
                                data-mapping-id="${escapeAttr(app.mapping_id)}"
                                data-app-id="${escapeAttr(app.id)}"
                                class="ml-2 px-3 py-1.5 text-xs bg-destructive text-primary-foreground rounded hover:bg-destructive/90 transition-colors flex items-center space-x-1"
                                title="Remove mapping"
                            >
                                <i data-lucide="trash-2" class="w-3 h-3"></i>
                                <span>Remove</span>
                            </button>
                        ` : ''}
                    </div>
                    ${isSelected ? renderApplicationSettings(app.id, mapping) : ''}
                </div>
            `;
        }).join(''));
    
        lucide.createIcons();
    }
    
    function populateFilterOptions() {
        // Populate type filter
        const typeFilter = getEl('filter-type');
        if (typeFilter && applicationsData.length > 0) {
            const types = [...new Set(applicationsData.map(app => app.type))].sort();
            const currentValue = typeFilter.value;
            safeHTML(typeFilter, '<option value="">All Types</option>' +
                types.map(type => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join(''));
            if (currentValue) typeFilter.value = currentValue;
        }
    
        // Populate domain filter
        const domainFilter = getEl('filter-domain');
        if (domainFilter && applicationsData.length > 0) {
            const domains = [...new Set(applicationsData.map(app => app.domain))].sort();
            const currentValue = domainFilter.value;
            safeHTML(domainFilter, '<option value="">All Domains</option>' +
                domains.map(domain => `<option value="${escapeHtml(domain)}">${escapeHtml(domain)}</option>`).join(''));
            if (currentValue) domainFilter.value = currentValue;
        }
    }
    
    window.selectAllFiltered = function() {
        const container = getEl('applications-list');
        if (!container) return;
    
        const checkboxes = container.querySelectorAll('input[type="checkbox"][data-app-id]:not(:checked)');
        checkboxes.forEach(checkbox => {
            const appId = checkbox.getAttribute('data-app-id');
            if (appId) {
                toggleApplicationSelection(appId);
            }
        });
    }
    
    window.deselectAll = function() {
        selectedApplications.clear();
        renderApplicationsList();
    }
    
    function renderApplicationSettings(appId, mapping) {
        const mappingData = mapping?.mapping || {};
        return `
            <div class="mt-4 pt-4 border-t border-border space-y-3">
                <div class="grid grid-cols-2 gap-4">
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Support Level</label>
                        <select
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="support_level"
                        >
                            <option value="full" ${mappingData.support_level === 'full' ? 'selected' : ''}>Full</option>
                            <option value="partial" ${mappingData.support_level === 'partial' ? 'selected' : ''}>Partial</option>
                            <option value="minimal" ${mappingData.support_level === 'minimal' ? 'selected' : ''}>Minimal</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Coverage %</label>
                        <input
                            type="number"
                            min="0"
                            max="100"
                            value="${mappingData.coverage_percentage || 0}"
                            aria-label="Coverage %"
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="coverage_percentage" data-parse="int"
                        />
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Support Quality (1-5)</label>
                        <input
                            type="number"
                            min="1"
                            max="5"
                            value="${mappingData.support_quality || 3}"
                            aria-label="Support Quality (1-5)"
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="support_quality" data-parse="int"
                        />
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Relationship Type</label>
                        <select
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="relationship_type"
                        >
                            <option value="enables" ${mappingData.relationship_type === 'enables' ? 'selected' : ''}>Enables</option>
                            <option value="supports" ${mappingData.relationship_type === 'supports' ? 'selected' : ''}>Supports</option>
                            <option value="governs" ${mappingData.relationship_type === 'governs' ? 'selected' : ''}>Governs</option>
                            <option value="measures" ${mappingData.relationship_type === 'measures' ? 'selected' : ''}>Measures</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Dependency Level</label>
                        <select
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="dependency_level"
                        >
                            <option value="critical" ${mappingData.dependency_level === 'critical' ? 'selected' : ''}>Critical</option>
                            <option value="high" ${mappingData.dependency_level === 'high' ? 'selected' : ''}>High</option>
                            <option value="medium" ${mappingData.dependency_level === 'medium' ? 'selected' : ''}>Medium</option>
                            <option value="low" ${mappingData.dependency_level === 'low' ? 'selected' : ''}>Low</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-medium text-muted-foreground mb-1">Priority</label>
                        <select
                            class="w-full text-sm border border-input rounded px-2 py-1"
                            data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="priority"
                        >
                            <option value="high" ${mappingData.priority === 'high' ? 'selected' : ''}>High</option>
                            <option value="medium" ${mappingData.priority === 'medium' ? 'selected' : ''}>Medium</option>
                            <option value="low" ${mappingData.priority === 'low' ? 'selected' : ''}>Low</option>
                        </select>
                    </div>
                </div>
                <div>
                    <label class="block text-xs font-medium text-muted-foreground mb-1">Gap Description</label>
                    <textarea
                        class="w-full text-sm border border-input rounded px-2 py-1"
                        rows="2"
                        data-cm-change="update-app-mapping" data-app-id="${escapeAttr(appId)}" data-field="gap_description"
                    >${mappingData.gap_description || ''}</textarea>
                </div>
            </div>
        `;
    }
    
    window.toggleApplicationSelection = function(appId) {
        // appId arrives as a string from data-app-id; applicationsData ids and the
        // selectedApplications keys are the native JSON type (usually number), so
        // resolve the app by loose match and key everything by its real id.
        const app = applicationsData.find(a => String(a.id) === String(appId));
        if (!app) return;
        appId = app.id;
    
        if (selectedApplications.has(appId)) {
            // Remove selection
            selectedApplications.delete(appId);
        } else {
            // Add selection with default or existing values
            const existingMapping = app.is_mapped ? {
                support_level: app.support_level,
                coverage_percentage: app.coverage_percentage,
                support_quality: app.support_quality,
                relationship_type: app.relationship_type,
                relationship_strength: app.relationship_strength,
                dependency_level: app.dependency_level,
                gap_status: app.gap_status,
                gap_description: app.gap_description,
                gap_impact: app.gap_impact,
                priority: app.priority,
                integration_complexity: app.integration_complexity,
                is_active: app.is_active
            } : {
                support_level: 'partial',
                coverage_percentage: 0,
                support_quality: 3,
                relationship_type: 'enables',
                relationship_strength: 3,
                dependency_level: 'medium',
                gap_status: 'unknown',
                gap_description: '',
                gap_impact: 'medium',
                priority: 'medium',
                integration_complexity: 'medium',
                is_active: true
            };
    
            selectedApplications.set(appId, {
                application_id: appId,
                mapping_id: app.mapping_id || null,
                mapping: existingMapping
            });
        }
    
        renderApplicationsList();
    }
    
    window.updateApplicationMapping = function(appId, field, value) {
        // Normalise the string data-app-id to the native key used in the map.
        const app = applicationsData.find(a => String(a.id) === String(appId));
        if (app) appId = app.id;
        if (!selectedApplications.has(appId)) return;
    
        const appData = selectedApplications.get(appId);
        appData.mapping[field] = value;
        selectedApplications.set(appId, appData);
    }
    
    window.saveMappings = async function() {
        if (!currentCapabilityId || selectedApplications.size === 0) {
            showNotification('Please select at least one application', 'warning');
            return;
        }

        try {
            const applications = Array.from(selectedApplications.values());

            const data = await Platform.fetch.post('/capability-map/api/mappings', {
                capability_id: currentCapabilityId,
                applications: applications
            }, { silent: true });
    
            if (data.error) {
                showNotification('Error saving mappings: ' + data.error, 'error');
                return;
            }
    
            const createdMsg = data.created > 0 ? `created ${data.created} new` : '';
            const updatedMsg = data.updated > 0 ? `updated ${data.updated}` : '';
            const msg = [createdMsg, updatedMsg].filter(Boolean).join(' and ') || 'saved';
            showNotification(`Successfully ${msg} mapping(s)`, 'success');
    
            // Reload all table data (this will automatically update Gap Analysis)
            await loadDataForAllTabs();
    
            // Close modal
            closeMappingModal();
        } catch (error) {
            showNotification('Error saving mappings', 'error');
        }
    }
    
    window.deleteMapping = async function(mappingId, appId) {
        const modalId = window.modalManager.createModal({
            title: 'Remove Mapping',
            content: '<p class="text-sm text-muted-foreground">Are you sure you want to remove this application-capability mapping? This action cannot be undone.</p>',
            size: 'small',
            buttons: [
                { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
                { text: 'Remove', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'remove', handler: async function() {
                    try {
                        const data = await Platform.fetch.delete(`/capability-map/api/mappings/${mappingId}`, { silent: true });

                        if (data.error) {
                            showNotification('Error removing mapping: ' + data.error, 'error');
                            return;
                        }

                        showNotification('Mapping removed successfully', 'success');

                        // Reload all table data to reflect the removal
                        await loadDataForAllTabs();
                    } catch (error) {
                        showNotification('Error removing mapping', 'error');
                    }
                } }
            ]
        });
        window.modalManager.open(modalId);
    }
    
    // ============================================
    // DELETE CONFIRMATION MODAL FUNCTIONS
    // ============================================
    
    function openDeleteConfirmModal(itemId, itemType, itemName, childCount) {
        const modal = getEl('delete-confirm-modal');
        const cascadeWarning = getEl('delete-cascade-warning');
        const cascadeMessage = getEl('delete-cascade-message');
    
        // Set hidden fields
        getEl('delete-item-id').value = itemId;
        getEl('delete-item-type').value = itemType;
    
        // Set item details
        getEl('delete-item-name').textContent = itemName;
    
        if (itemType === 'gap') {
            getEl('delete-item-info').textContent = `Gap ID: ${itemId}`;
            getEl('delete-confirm-message').textContent = 'Are you sure you want to delete this gap?';
    
            // Show cascade warning if gap has work packages
            if (childCount > 0) {
                cascadeWarning.classList.remove('hidden');
                cascadeMessage.textContent = `This gap has ${childCount} work package${childCount > 1 ? 's' : ''}. Deleting this gap will also delete all associated work packages.`;
            } else {
                cascadeWarning.classList.add('hidden');
            }
        } else if (itemType === 'work_package') {
            getEl('delete-item-info').textContent = `Work Package ID: ${itemId}`;
            getEl('delete-confirm-message').textContent = 'Are you sure you want to delete this work package?';
            cascadeWarning.classList.add('hidden');
        }
    
        Platform.modal.open('delete-confirm-modal');
        lucide.createIcons();
    }
    
    function closeDeleteConfirmModal() {
        Platform.modal.close('delete-confirm-modal');
        getEl('delete-item-id').value = '';
        getEl('delete-item-type').value = '';
    }
    
    async function confirmDelete() {
        const itemId = getEl('delete-item-id').value;
        const itemType = getEl('delete-item-type').value;
    
        if (!itemId || !itemType) {
            showToast('Invalid delete request', 'error');
            return;
        }
    
        try {
            let endpoint;
            if (itemType === 'gap') {
                endpoint = `/capability-map/api/roadmap/gaps/${itemId}`;
            } else if (itemType === 'work_package') {
                endpoint = `/capability-map/api/roadmap/work-packages/${itemId}`;
            } else {
                showToast('Unknown item type', 'error');
                return;
            }
    
            const data = await Platform.fetch.delete(endpoint, { silent: true });
    
            if (data.success) {
                const itemTypeName = itemType === 'gap' ? 'Gap' : 'Work package';
                showToast(`${itemTypeName} deleted successfully`, 'success');
                closeDeleteConfirmModal();
    
                // Reload the roadmap data
                if (roadmapData.viewMode === 'persisted') {
                    loadPersistedRoadmapData();
                } else {
                    loadRoadmapData();
                }
            } else {
                showToast(data.error || 'Delete failed', 'error');
            }
        } catch (error) {
            showToast('Error deleting item', 'error');
        }
    }
    
    window.filterApplications = function() {
        renderApplicationsList();
    }
    
    window.showNotification = function(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        const bgColor = type === 'success' ? 'bg-success' : type === 'error' ? 'bg-destructive' : type === 'warning' ? 'bg-warning' : 'bg-info';
        notification.className = `fixed top-4 right-4 ${bgColor} text-primary-foreground px-6 py-3 rounded-lg shadow-lg z-50 flex items-center space-x-2`;
        safeHTML(notification, `
            <span>${escapeHtml(message)}</span>
            <button type="button" data-cm-action="dismiss-parent" aria-label="Dismiss notification" class="ml-4 text-primary-foreground hover:text-foreground/80">×</button>
        `);
    
        document.body.appendChild(notification);
    
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 5000);
    }

    // =================================================
    // GLOBAL EXPORTS - Functions called from HTML
    // =================================================
    
    // Alpine.js Components
    window.capabilityMapTabs = capabilityMapTabs;
    
    // Table Operations
    window.filterTable = filterTable;
    window.clearFilters = clearFilters;
    window.sortTable = sortTable;
    window.updateTable = updateTable;
    
    // Pagination
    window.previousPage = previousPage;
    window.nextPage = nextPage;
    window.changePageSize = changePageSize;
    
    // Export Functions
    window.exportData = exportData;
    window.exportRoadmap = exportRoadmap;
    
    // Retry and Load Functions
    window.retryLoadData = retryLoadData;
    window.loadDataForAllTabs = loadDataForAllTabs;
    
    // Tab-Specific Load Functions
    if (typeof loadBusinessDomainCards !== 'undefined') window.loadBusinessDomainCards = loadBusinessDomainCards;
    if (typeof loadTechnicalTab !== 'undefined') window.loadTechnicalTab = loadTechnicalTab;
    if (typeof loadManufacturingDomainStats !== 'undefined') window.loadManufacturingDomainStats = loadManufacturingDomainStats;
    if (typeof loadProcessGapData !== 'undefined') window.loadProcessGapData = loadProcessGapData;
    if (typeof loadProcessCategoryStats !== 'undefined') window.loadProcessCategoryStats = loadProcessCategoryStats;
    if (typeof loadACMGapAnalysis !== 'undefined') window.loadACMGapAnalysis = loadACMGapAnalysis;
    
    // Roadmap Functions
    window.initRoadmapTab = initRoadmapTab;
    window.setGapTypeFilter = setGapTypeFilter;
    window.loadRoadmapData = loadRoadmapData;
    window.loadPersistedRoadmapData = loadPersistedRoadmapData;
    window.setRoadmapView = setRoadmapView;
    window.toggleRowExpansion = toggleRowExpansion;
    window.toggleWorkPackageExpansion = toggleWorkPackageExpansion;
    window.showGapDetails = showGapDetails;
    if (typeof expandAllRoadmapRows !== 'undefined') window.expandAllRoadmapRows = expandAllRoadmapRows;
    if (typeof collapseAllRoadmapRows !== 'undefined') window.collapseAllRoadmapRows = collapseAllRoadmapRows;
    if (typeof jumpToToday !== 'undefined') window.jumpToToday = jumpToToday;
    
    // Roadmap Timeline Range
    if (typeof setTimelineRange !== 'undefined') window.setTimelineRange = setTimelineRange;
    if (typeof setCustomTimelineRange !== 'undefined') window.setCustomTimelineRange = setCustomTimelineRange;
    if (typeof calculateCriticalPriorityItems !== 'undefined') window.calculateCriticalPriorityItems = calculateCriticalPriorityItems;
    if (typeof highlightCriticalPath !== 'undefined') window.highlightCriticalPath = highlightCriticalPath;
    if (typeof clearCriticalPathHighlight !== 'undefined') window.clearCriticalPathHighlight = clearCriticalPathHighlight;

    // Roadmap Filtering
    if (typeof filterRoadmapItems !== 'undefined') window.filterRoadmapItems = filterRoadmapItems;
    if (typeof clearFilter !== 'undefined') window.clearFilter = clearFilter;
    if (typeof clearAllFilters !== 'undefined') window.clearAllFilters = clearAllFilters;
    if (typeof applyFilterPreset !== 'undefined') window.applyFilterPreset = applyFilterPreset;
    if (typeof saveCurrentFilters !== 'undefined') window.saveCurrentFilters = saveCurrentFilters;
    if (typeof toggleFilterPanel !== 'undefined') window.toggleFilterPanel = toggleFilterPanel;
    if (typeof applyRoadmapFilters !== 'undefined') window.applyRoadmapFilters = applyRoadmapFilters;
    if (typeof onLevelFilterChange !== 'undefined') window.onLevelFilterChange = onLevelFilterChange;
    if (typeof onParentFilterChange !== 'undefined') window.onParentFilterChange = onParentFilterChange;
    
    // Roadmap Bulk Operations
    if (typeof toggleBulkSelection !== 'undefined') window.toggleBulkSelection = toggleBulkSelection;
    if (typeof selectAllVisibleItems !== 'undefined') window.selectAllVisibleItems = selectAllVisibleItems;
    if (typeof clearBulkSelection !== 'undefined') window.clearBulkSelection = clearBulkSelection;
    if (typeof bulkUpdateStatus !== 'undefined') window.bulkUpdateStatus = bulkUpdateStatus;
    if (typeof executeBulkStatusUpdate !== 'undefined') window.executeBulkStatusUpdate = executeBulkStatusUpdate;
    if (typeof bulkUpdateOwner !== 'undefined') window.bulkUpdateOwner = bulkUpdateOwner;
    if (typeof executeBulkOwnerUpdate !== 'undefined') window.executeBulkOwnerUpdate = executeBulkOwnerUpdate;
    if (typeof bulkDelete !== 'undefined') window.bulkDelete = bulkDelete;
    if (typeof executeBulkDelete !== 'undefined') window.executeBulkDelete = executeBulkDelete;
    
    // Modal Functions
    if (typeof openQuickAddModal !== 'undefined') window.openQuickAddModal = openQuickAddModal;
    if (typeof closeQuickAddModal !== 'undefined') window.closeQuickAddModal = closeQuickAddModal;
    if (typeof openConvertModal !== 'undefined') window.openConvertModal = openConvertModal;
    if (typeof closeConvertModal !== 'undefined') window.closeConvertModal = closeConvertModal;
    if (typeof executeConvertGaps !== 'undefined') window.executeConvertGaps = executeConvertGaps;
    if (typeof openEditWPModal !== 'undefined') window.openEditWPModal = openEditWPModal;
    if (typeof closeEditWPModal !== 'undefined') window.closeEditWPModal = closeEditWPModal;
    if (typeof saveWPChanges !== 'undefined') window.saveWPChanges = saveWPChanges;
    if (typeof openEditGapModal !== 'undefined') window.openEditGapModal = openEditGapModal;
    if (typeof closeEditGapModal !== 'undefined') window.closeEditGapModal = closeEditGapModal;
    if (typeof saveGapChanges !== 'undefined') window.saveGapChanges = saveGapChanges;
    if (typeof closeDeleteConfirmModal !== 'undefined') window.closeDeleteConfirmModal = closeDeleteConfirmModal;
    if (typeof confirmDelete !== 'undefined') window.confirmDelete = confirmDelete;
    if (typeof setGapColor !== 'undefined') window.setGapColor = setGapColor;
    window.setRoadmapColor = setRoadmapColor;
    
    // Work Package Functions
    if (typeof addWorkPackageToGap !== 'undefined') window.addWorkPackageToGap = addWorkPackageToGap;
    if (typeof addChildWorkPackage !== 'undefined') window.addChildWorkPackage = addChildWorkPackage;
    if (typeof switchWPTab !== 'undefined') window.switchWPTab = switchWPTab;
    if (typeof selectAllWPChildren !== 'undefined') window.selectAllWPChildren = selectAllWPChildren;
    if (typeof deselectAllWPChildren !== 'undefined') window.deselectAllWPChildren = deselectAllWPChildren;
    if (typeof saveDependency !== 'undefined') window.saveDependency = saveDependency;
    
    // Roadmap Addition Functions
    if (typeof addToRoadmap !== 'undefined') window.addToRoadmap = addToRoadmap;
    if (typeof quickAddToRoadmap !== 'undefined') window.quickAddToRoadmap = quickAddToRoadmap;
    if (typeof bulkAddToRoadmap !== 'undefined') window.bulkAddToRoadmap = bulkAddToRoadmap;
    if (typeof openAddRoadmapModal !== 'undefined') window.openAddRoadmapModal = openAddRoadmapModal;
    if (typeof closeAddRoadmapModal !== 'undefined') window.closeAddRoadmapModal = closeAddRoadmapModal;
    if (typeof submitAddToRoadmap !== 'undefined') window.submitAddToRoadmap = submitAddToRoadmap;
    
    // Row Selection Functions
    if (typeof handleRowClick !== 'undefined') window.handleRowClick = handleRowClick;
    if (typeof handleRowRightClick !== 'undefined') window.handleRowRightClick = handleRowRightClick;
    if (typeof toggleRowSelection !== 'undefined') window.toggleRowSelection = toggleRowSelection;
    if (typeof selectAllFiltered !== 'undefined') window.selectAllFiltered = selectAllFiltered;
    if (typeof deselectAll !== 'undefined') window.deselectAll = deselectAll;
    
    // Drag and Drop Functions
    if (typeof handleDragStart !== 'undefined') window.handleDragStart = handleDragStart;
    if (typeof handleDragEnd !== 'undefined') window.handleDragEnd = handleDragEnd;
    if (typeof handleDrop !== 'undefined') window.handleDrop = handleDrop;
    if (typeof handleDragOver !== 'undefined') window.handleDragOver = handleDragOver;
    
    // Mapping Modal Functions
    if (typeof openMappingModal !== 'undefined') window.openMappingModal = openMappingModal;
    if (typeof closeMappingModal !== 'undefined') window.closeMappingModal = closeMappingModal;
    if (typeof saveMappings !== 'undefined') window.saveMappings = saveMappings;
    if (typeof deleteMapping !== 'undefined') window.deleteMapping = deleteMapping;
    if (typeof toggleApplicationSelection !== 'undefined') window.toggleApplicationSelection = toggleApplicationSelection;
    if (typeof updateApplicationMapping !== 'undefined') window.updateApplicationMapping = updateApplicationMapping;
    if (typeof filterApplications !== 'undefined') window.filterApplications = filterApplications;
    
    // APQC and ArchiMate Mapping Functions
    if (typeof openCapabilityAPQCMapping !== 'undefined') window.openCapabilityAPQCMapping = openCapabilityAPQCMapping;
    if (typeof openCapabilityArchimateMapping !== 'undefined') window.openCapabilityArchimateMapping = openCapabilityArchimateMapping;
    if (typeof openProcessMappingModal !== 'undefined') window.openProcessMappingModal = openProcessMappingModal;
    if (typeof closeProcessMappingModal !== 'undefined') window.closeProcessMappingModal = closeProcessMappingModal;
    if (typeof saveProcessMappings !== 'undefined') window.saveProcessMappings = saveProcessMappings;
    if (typeof openACMMappingModal !== 'undefined') window.openACMMappingModal = openACMMappingModal;
    if (typeof closeACMMappingModal !== 'undefined') window.closeACMMappingModal = closeACMMappingModal;
    if (typeof saveACMMappings !== 'undefined') window.saveACMMappings = saveACMMappings;
    
    // ACM Gap Analysis Functions
    if (typeof filterACMByDomain !== 'undefined') window.filterACMByDomain = filterACMByDomain;
    if (typeof clearACMGapFilters !== 'undefined') window.clearACMGapFilters = clearACMGapFilters;
    if (typeof selectAllACMFiltered !== 'undefined') window.selectAllACMFiltered = selectAllACMFiltered;
    if (typeof deselectAllACM !== 'undefined') window.deselectAllACM = deselectAllACM;
    if (typeof nextACMPage !== 'undefined') window.nextACMPage = nextACMPage;
    if (typeof previousACMPage !== 'undefined') window.previousACMPage = previousACMPage;
    if (typeof openACMCapabilityDetail !== 'undefined') window.openACMCapabilityDetail = openACMCapabilityDetail;
    if (typeof deleteACMMapping !== 'undefined') window.deleteACMMapping = deleteACMMapping;
    
    // Process Gap Functions
    if (typeof filterProcessByCategory !== 'undefined') window.filterProcessByCategory = filterProcessByCategory;
    if (typeof clearProcessGapFilters !== 'undefined') window.clearProcessGapFilters = clearProcessGapFilters;
    if (typeof selectAllProcessFiltered !== 'undefined') window.selectAllProcessFiltered = selectAllProcessFiltered;
    if (typeof deselectAllProcess !== 'undefined') window.deselectAllProcess = deselectAllProcess;
    if (typeof nextProcessGapPage !== 'undefined') window.nextProcessGapPage = nextProcessGapPage;
    if (typeof previousProcessGapPage !== 'undefined') window.previousProcessGapPage = previousProcessGapPage;
    if (typeof sortProcessGapTable !== 'undefined') window.sortProcessGapTable = sortProcessGapTable;
    
    // Comments Functions
    if (typeof showCommentsPanel !== 'undefined') window.showCommentsPanel = showCommentsPanel;
    if (typeof addComment !== 'undefined') window.addComment = addComment;
    
    // Breadcrumb Functions
    if (typeof showBreadcrumb !== 'undefined') window.showBreadcrumb = showBreadcrumb;
    if (typeof hideBreadcrumb !== 'undefined') window.hideBreadcrumb = hideBreadcrumb;
    
    // UI Helper Functions
    if (typeof showNotification !== 'undefined') window.showNotification = showNotification;
    if (typeof showKeyboardShortcutsHelp !== 'undefined') window.showKeyboardShortcutsHelp = showKeyboardShortcutsHelp;
    if (typeof toggleRoadmapExportMenu !== 'undefined') window.toggleRoadmapExportMenu = toggleRoadmapExportMenu;
    if (typeof exportAsCSV !== 'undefined') window.exportAsCSV = exportAsCSV;
    if (typeof deleteCurrentGap !== 'undefined') window.deleteCurrentGap = deleteCurrentGap;
    if (typeof deleteCurrentWP !== 'undefined') window.deleteCurrentWP = deleteCurrentWP;

    // ============================================
    // CAP-003: HEAT MAP TAB
    // ============================================
    let _heatmapLoaded = false;
    let _heatmapCaps = [];         // cached capability data
    let _heatmapView = 'tags';     // 'tags' or 'matrix'
    let _heatmapFilter = 'all';    // 'all', 'gaps', 'partial', 'covered'

    function loadHeatMap() {
        if (_heatmapLoaded) return;
        const loading = document.getElementById('heatmap-loading');
        let container = document.getElementById('heatmap-container');
        if (!loading || !container) return;

        Platform.fetch('/capability-map/api/unified-capabilities')
            .then(function(data) {
                let caps = data.unified_capabilities || data.capabilities || [];
                if (caps.length === 0) {
                    loading.innerHTML = '<p class="text-sm text-muted-foreground">No capabilities found</p>';
                    return;
                }
                _heatmapCaps = caps;
                loading.classList.add('hidden');
                renderHeatmapTagView(caps, 'all');
                _heatmapLoaded = true;
            })
            .catch(function(e) {
                loading.innerHTML = '<p class="text-sm text-destructive">Failed to load heat map: ' + escapeHtml(e.message) + '</p>';
            });
    }

    function filterHeatmapCoverage(filter) {
        _heatmapFilter = filter;
        if (_heatmapView === 'tags') {
            renderHeatmapTagView(_heatmapCaps, filter);
        } else {
            renderHeatmapMatrixView(_heatmapCaps, filter);
        }
    }

    function setHeatmapView(view) {
        _heatmapView = view;
        const tagsBtn = document.getElementById('heatmap-view-tags');
        const matrixBtn = document.getElementById('heatmap-view-matrix');
        const tagsContainer = document.getElementById('heatmap-container');
        const matrixContainer = document.getElementById('heatmap-matrix-container');

        if (view === 'tags') {
            if (tagsBtn) { tagsBtn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-primary text-primary-foreground'; }
            if (matrixBtn) { matrixBtn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80'; }
            if (tagsContainer) tagsContainer.classList.remove('hidden');
            if (matrixContainer) matrixContainer.classList.add('hidden');
            renderHeatmapTagView(_heatmapCaps, _heatmapFilter);
        } else {
            if (matrixBtn) { matrixBtn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-primary text-primary-foreground'; }
            if (tagsBtn) { tagsBtn.className = 'px-2.5 py-1 rounded text-xs font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80'; }
            if (tagsContainer) tagsContainer.classList.add('hidden');
            if (matrixContainer) matrixContainer.classList.remove('hidden');
            renderHeatmapMatrixView(_heatmapCaps, _heatmapFilter);
        }
    }

    function _filterCaps(caps, filter) {
        if (filter === 'gaps') return caps.filter(function(c) { return !c.mapping_count; });
        if (filter === 'partial') return caps.filter(function(c) { return c.mapping_count === 1; });
        if (filter === 'covered') return caps.filter(function(c) { return c.mapping_count >= 2; });
        return caps;
    }

    function renderHeatmapTagView(allCaps, filter) {
        let container = document.getElementById('heatmap-container');
        if (!container) return;
        let caps = _filterCaps(allCaps, filter);

        // Group by domain
        let domains = {};
        caps.forEach(function(c) {
            let d = (c.domain && c.domain.name) || 'Unknown';
            if (!domains[d]) domains[d] = [];
            domains[d].push(c);
        });

        let sortedDomains = Object.keys(domains).sort();

        if (sortedDomains.length === 0) {
            safeHTML(container, '<p class="text-sm text-muted-foreground py-8 text-center">No capabilities match this filter.</p>');
            container.classList.remove('hidden');
            return;
        }

        let html = '';
        sortedDomains.forEach(function(domainName) {
            const domCaps = domains[domainName].sort(function(a, b) {
                // Sort gaps first (ascending mapping_count) for quick identification
                if (a.mapping_count !== b.mapping_count) return (a.mapping_count || 0) - (b.mapping_count || 0);
                return (a.name || '').localeCompare(b.name || '');
            });
            const covered = domCaps.filter(function(c) { return c.mapping_count >= 2; }).length;
            const partial = domCaps.filter(function(c) { return c.mapping_count === 1; }).length;
            let gap = domCaps.filter(function(c) { return !c.mapping_count; }).length;

            html += '<div class="mb-6">';
            html += '<div class="flex items-center gap-3 mb-2">';
            html += '<h3 class="text-sm font-semibold text-foreground">' + escapeHtml(domainName) + '</h3>';
            html += '<span class="text-[10px] text-muted-foreground">' + domCaps.length + ' capabilities</span>';
            html += '<span class="text-[10px] text-emerald-600">' + covered + ' covered</span>';
            if (partial) html += '<span class="text-[10px] text-amber-600">' + partial + ' partial</span>';
            if (gap) html += '<span class="text-[10px] text-destructive">' + gap + ' gaps</span>';
            html += '</div>';
            html += '<div class="flex flex-wrap gap-1.5">';
            domCaps.forEach(function(c, idx) {
                let colorClass = c.mapping_count >= 2
                    ? 'bg-emerald-500/80 hover:bg-emerald-500 text-primary-foreground border-emerald-600/30'
                    : c.mapping_count === 1
                        ? 'bg-amber-500/80 hover:bg-amber-500 text-primary-foreground border-amber-600/30'
                        : 'bg-destructive/80 hover:bg-destructive text-primary-foreground border-destructive/30';
                html += '<button type="button" data-cap-id="' + escapeHtml(c.id || '') + '"';
                html += ' class="hm-cap-btn px-2 py-1 rounded text-[10px] font-medium border transition-colors cursor-pointer ' + colorClass + '"';
                html += ' title="' + escapeHtml(c.name) + ' \u2014 ' + (c.mapping_count || 0) + ' app(s)">';
                html += escapeHtml(c.name);
                html += '</button>';
            });
            html += '</div></div>';
        });

        container.classList.remove('hidden');
        safeHTML(container, html);
        // Event delegation for capability buttons (onclick stripped by DOMPurify)
        container.addEventListener('click', function(e) {
            let btn = e.target.closest('.hm-cap-btn');
            if (!btn) return;
            let capId = btn.getAttribute('data-cap-id');
            let cap = _heatmapCaps.find(function(c) { return String(c.id) === capId; });
            if (cap) showHeatmapDetail(cap);
        });
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    function renderHeatmapMatrixView(allCaps, filter) {
        let container = document.getElementById('heatmap-matrix-container');
        if (!container) return;
        let caps = _filterCaps(allCaps, filter);

        // Build domain × maturity matrix
        let domains = {};
        const maturityLevels = [1, 2, 3, 4, 5];
        const maturityLabels = {1: 'Initial', 2: 'Managed', 3: 'Defined', 4: 'Quantitative', 5: 'Optimizing'};
        const maturityColors = {1: 'bg-destructive', 2: 'bg-orange-500', 3: 'bg-amber-500', 4: 'bg-lime-500', 5: 'bg-emerald-500'};

        caps.forEach(function(c) {
            let d = (c.domain && c.domain.name) || 'Unknown';
            if (!domains[d]) {
                domains[d] = {total: 0, gaps: 0, byMaturity: {}};
                maturityLevels.forEach(function(l) { domains[d].byMaturity[l] = []; });
            }
            let m = c.current_maturity || 1;
            if (m < 1) m = 1;
            if (m > 5) m = 5;
            domains[d].byMaturity[m].push(c);
            domains[d].total++;
            if (!c.mapping_count) domains[d].gaps++;
        });

        let sortedDomains = Object.keys(domains).sort();

        if (sortedDomains.length === 0) {
            safeHTML(container, '<p class="text-sm text-muted-foreground py-8 text-center">No capabilities match this filter.</p>');
            container.classList.remove('hidden');
            return;
        }

        let html = '<div class="overflow-x-auto">';
        html += '<table class="w-full border-collapse">';
        html += '<thead><tr>';
        html += '<th class="text-left text-xs font-semibold text-muted-foreground p-2 border-b border-border min-w-[180px]">Domain</th>';
        maturityLevels.forEach(function(l) {
            html += '<th class="text-center text-xs font-semibold text-muted-foreground p-2 border-b border-border min-w-[100px]">' + maturityLabels[l] + ' (' + l + ')</th>';
        });
        html += '<th class="text-center text-xs font-semibold text-muted-foreground p-2 border-b border-border min-w-[80px]">Health</th>';
        html += '</tr></thead><tbody>';

        sortedDomains.forEach(function(domainName) {
            const dom = domains[domainName];
            const healthPct = dom.total > 0 ? Math.round(((dom.total - dom.gaps) / dom.total) * 100) : 0;
            const healthColor = healthPct >= 80 ? 'text-emerald-600' : healthPct >= 60 ? 'text-amber-600' : healthPct >= 40 ? 'text-orange-600' : 'text-destructive';

            html += '<tr class="hover:bg-muted/50 transition-colors">';
            html += '<td class="p-2 border-b border-border">';
            html += '<span class="text-sm font-medium text-foreground">' + escapeHtml(domainName) + '</span>';
            html += '<span class="ml-2 text-[10px] text-muted-foreground">' + dom.total + ' caps</span>';
            if (dom.gaps) html += '<span class="ml-1 text-[10px] text-destructive">' + dom.gaps + ' gaps</span>';
            html += '</td>';

            maturityLevels.forEach(function(l) {
                let cellCaps = dom.byMaturity[l];
                let count = cellCaps.length;
                if (count === 0) {
                    html += '<td class="p-2 border-b border-border text-center"><span class="text-xs text-muted-foreground/50">\u2014</span></td>';
                } else {
                    const gapCount = cellCaps.filter(function(c) { return !c.mapping_count; }).length;
                    const intensity = Math.min(count * 15, 100);
                    let bgOpacity = (intensity / 100).toFixed(2);
                    html += '<td class="hm-matrix-cell p-2 border-b border-border text-center cursor-pointer" data-domain="' + escapeHtml(domainName) + '" data-maturity="' + l + '" title="' + count + ' capabilities at maturity ' + l + '">';
                    html += '<div class="inline-flex items-center justify-center w-10 h-10 rounded-lg ' + maturityColors[l] + '" style="opacity:' + bgOpacity + '">';
                    html += '<span class="text-primary-foreground text-sm font-bold">' + count + '</span>';
                    html += '</div>';
                    if (gapCount) html += '<div class="text-[9px] text-destructive mt-0.5">' + gapCount + ' gap' + (gapCount > 1 ? 's' : '') + '</div>';
                    html += '</td>';
                }
            });

            html += '<td class="p-2 border-b border-border text-center">';
            html += '<span class="text-sm font-bold ' + healthColor + '">' + healthPct + '%</span>';
            html += '</td></tr>';
        });

        html += '</tbody></table></div>';

        // Summary row
        const totalCaps = caps.length;
        let totalGaps = caps.filter(function(c) { return !c.mapping_count; }).length;
        const totalPartial = caps.filter(function(c) { return c.mapping_count === 1; }).length;
        const totalCovered = caps.filter(function(c) { return c.mapping_count >= 2; }).length;
        html += '<div class="mt-4 flex items-center gap-6 text-xs text-muted-foreground">';
        html += '<span><strong class="text-foreground">' + totalCaps + '</strong> capabilities</span>';
        html += '<span><strong class="text-emerald-600">' + totalCovered + '</strong> covered</span>';
        html += '<span><strong class="text-amber-600">' + totalPartial + '</strong> partial</span>';
        html += '<span><strong class="text-destructive">' + totalGaps + '</strong> gaps</span>';
        html += '<span><strong class="text-foreground">' + sortedDomains.length + '</strong> domains</span>';
        html += '</div>';

        container.classList.remove('hidden');
        safeHTML(container, html);
        // Event delegation for matrix cells (onclick stripped by DOMPurify)
        container.addEventListener('click', function(e) {
            let cell = e.target.closest('.hm-matrix-cell');
            if (!cell) return;
            showMatrixCellDetail(cell.getAttribute('data-domain'), parseInt(cell.getAttribute('data-maturity')));
        });
    }

    function showMatrixCellDetail(domainName, maturityLevel) {
        // Filter capabilities for this cell and show first one in detail panel
        let cellCaps = _heatmapCaps.filter(function(c) {
            let d = (c.domain && c.domain.name) || 'Unknown';
            let m = c.current_maturity || 1;
            return d === domainName && m === maturityLevel;
        });
        if (cellCaps.length > 0) {
            // Show list in detail panel
            let panel = document.getElementById('heatmap-detail');
            if (!panel) return;
            panel.classList.remove('hidden');

            let nameEl = document.getElementById('heatmap-detail-name');
            let domainEl = document.getElementById('heatmap-detail-domain');
            let appsEl = document.getElementById('heatmap-detail-apps');
            let curEl = document.getElementById('heatmap-detail-maturity-current');
            let tgtEl = document.getElementById('heatmap-detail-maturity-target');
            let gapEl = document.getElementById('heatmap-detail-gap');
            let listEl = document.getElementById('heatmap-detail-app-list');

            if (nameEl) nameEl.textContent = domainName + ' \u2014 Maturity ' + maturityLevel;
            if (domainEl) domainEl.textContent = cellCaps.length + ' capabilities';
            const totalApps = cellCaps.reduce(function(s, c) { return s + (c.mapping_count || 0); }, 0);
            if (appsEl) appsEl.textContent = totalApps;
            if (curEl) curEl.textContent = maturityLevel + '/5';
            const avgTarget = Math.round(cellCaps.reduce(function(s, c) { return s + (c.target_maturity || 3); }, 0) / cellCaps.length);
            if (tgtEl) tgtEl.textContent = avgTarget + '/5';
            let gapVal = avgTarget - maturityLevel;
            if (gapEl) {
                gapEl.textContent = gapVal > 0 ? '-' + gapVal : 'Met';
                gapEl.className = 'text-xl font-bold ' + (gapVal > 0 ? 'text-destructive' : 'text-emerald-600');
            }

            if (listEl) {
                let capHtml = '<h4 class="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Capabilities in this cell</h4><div class="flex flex-wrap gap-1.5">';
                cellCaps.forEach(function(c) {
                    let colorClass = c.mapping_count >= 2 ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30'
                        : c.mapping_count === 1 ? 'bg-amber-500/10 text-amber-600 border-amber-500/30'
                        : 'bg-destructive/10 text-destructive border-destructive/30';
                    capHtml += '<button data-cap-id="' + escapeHtml(c.id || '') + '" class="hm-cap-btn inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border cursor-pointer ' + colorClass + '">';
                    capHtml += escapeHtml(c.name) + ' <span class="ml-1 opacity-60">(' + (c.mapping_count || 0) + ')</span>';
                    capHtml += '</button>';
                });
                capHtml += '</div>';
                safeHTML(listEl, capHtml);
                // Event delegation for detail panel capability buttons
                listEl.addEventListener('click', function(e) {
                    let btn = e.target.closest('.hm-cap-btn');
                    if (!btn) return;
                    let capId = btn.getAttribute('data-cap-id');
                    let cap = _heatmapCaps.find(function(c) { return String(c.id) === capId; });
                    if (cap) showHeatmapDetail(cap);
                });
            }

            panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }

    function showHeatmapDetail(cap) {
        if (typeof cap === 'string') {
            try { cap = JSON.parse(cap); } catch (e) { return; }
        }
        let panel = document.getElementById('heatmap-detail');
        if (!panel) return;
        panel.classList.remove('hidden');

        let nameEl = document.getElementById('heatmap-detail-name');
        let domainEl = document.getElementById('heatmap-detail-domain');
        let appsEl = document.getElementById('heatmap-detail-apps');
        let curEl = document.getElementById('heatmap-detail-maturity-current');
        let tgtEl = document.getElementById('heatmap-detail-maturity-target');
        let gapEl = document.getElementById('heatmap-detail-gap');
        let listEl = document.getElementById('heatmap-detail-app-list');

        if (nameEl) nameEl.textContent = cap.name || 'Unknown';
        if (domainEl) domainEl.textContent = (cap.domain && cap.domain.name || 'Unknown') + ' — Level ' + (cap.level || 1);
        if (appsEl) appsEl.textContent = cap.mapping_count || 0;
        if (curEl) curEl.textContent = (cap.current_maturity || 1) + '/5';
        if (tgtEl) tgtEl.textContent = (cap.target_maturity || 3) + '/5';

        let gapVal = (cap.target_maturity || 3) - (cap.current_maturity || 1);
        if (gapEl) {
            gapEl.textContent = gapVal > 0 ? '-' + gapVal : 'Met';
            gapEl.className = 'text-xl font-bold ' + (gapVal > 0 ? 'text-destructive' : 'text-emerald-600');
        }

        if (listEl) {
            let apps = cap.applications || [];
            if (apps.length === 0) {
                safeHTML(listEl, '<p class="text-sm text-muted-foreground italic">No applications linked</p>');
            } else {
                let appHtml = '<h4 class="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Linked Applications</h4><div class="flex flex-wrap gap-1.5">';
                apps.forEach(function(a) {
                    appHtml += '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-primary/10 text-primary border border-primary/30">' + escapeHtml(a.name) + '</span>';
                });
                appHtml += '</div>';
                safeHTML(listEl, appHtml);
            }
        }

        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    window.loadHeatMap = loadHeatMap;
    window.showHeatmapDetail = showHeatmapDetail;
    window.setHeatmapView = setHeatmapView;
    window.filterHeatmapCoverage = filterHeatmapCoverage;
    window.showMatrixCellDetail = showMatrixCellDetail;

    // ── Event delegation for table rows (DOMPurify strips inline onclick) ────
    document.addEventListener('click', function(e) {
        // Handle "open mapping" action buttons
        let actionBtn = e.target.closest('[data-action="open-mapping"]');
        if (actionBtn) {
            e.stopPropagation();
            openMappingModal(actionBtn.dataset.capId, actionBtn.dataset.capName);
            return;
        }

        // Handle table row clicks (capability detail)
        let row = e.target.closest('tr[data-cap-id]');
        if (!row) return;

        // Skip if clicking checkbox column
        const noClick = e.target.closest('[data-no-row-click]');
        if (noClick) return;
        if (e.target.type === 'checkbox') return;

        // Skip if clicking an interactive element inside the row
        if (e.target.closest('button, select, a')) return;

        let capId = row.dataset.capId;
        let capName = row.dataset.capName;

        // Open the mapping modal (shows linked applications)
        openMappingModal(capId, capName);
    });

    // Event delegation for context menu
    document.addEventListener('contextmenu', function(e) {
        let row = e.target.closest('tr[data-cap-id]');
        if (!row) return;
        let capId = row.dataset.capId;
        let capName = row.dataset.capName;
        let tabType = row.dataset.tabType;
        if (typeof handleRowRightClick === 'function') {
            handleRowRightClick(e, capId, capName, tabType);
        }
    });

    // Event delegation for drag start
    document.addEventListener('dragstart', function(e) {
        let row = e.target.closest('tr[data-cap-id]');
        if (!row) return;
        if (typeof handleDragStart === 'function') {
            handleDragStart(e, row.dataset.capId, row.dataset.capName, 'business',
                parseInt(row.dataset.capLevel) || 1, row.dataset.capPriority || '');
        }
    });

    document.addEventListener('dragend', function(e) {
        let row = e.target.closest('tr[data-cap-id]');
        if (!row) return;
        if (typeof handleDragEnd === 'function') {
            handleDragEnd(e);
        }
    });

})();
