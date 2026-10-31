/**
 * Capability Map - Supplemental Functions
 * Extracted from capability_map/index.html inline scripts
 * Loaded after index.js to provide fallbacks and additional tab functions
 */

// ============================================================================
// Guarded fallbacks to ensure mapping modal opens even if main JS bundle
// fails to define handlers
// ============================================================================

if (typeof window.currentMappingCapability === 'undefined') {
    window.currentMappingCapability = null;
}

if (typeof window.openMappingModal !== 'function') {
    window.openMappingModal = async function(capabilityId, capabilityName) {
        // ARCH-064: hydrate the lazily-loaded dialog before touching its DOM.
        if (!await window.CapabilityMapModals.ensure('mapping-modal')) { return; }

        window.currentMappingCapability = { id: capabilityId, name: capabilityName };

        let nameEl = document.getElementById('modal-capability-name');
        if (nameEl) {
            nameEl.textContent = capabilityName;
        }

        let modal = document.getElementById('mapping-modal');
        if (modal) {
            Platform.modal.open('mapping-modal');
        }

        if (typeof lucide !== 'undefined') {
            lucide.createIcons();
        }
    };
}

if (typeof window.closeMappingModal !== 'function') {
    window.closeMappingModal = function() {
        let modal = document.getElementById('mapping-modal');
        if (modal) {
            Platform.modal.close('mapping-modal');
        }
        window.currentMappingCapability = null;
    };
}

if (typeof window.filterApplications !== 'function') {
    window.filterApplications = function() {
        // Placeholder for filterApplications functionality
    };
}

if (typeof window.selectAllFiltered !== 'function') {
    window.selectAllFiltered = function() {
        // Placeholder for selectAllFiltered functionality
    };
}

if (typeof window.deselectAll !== 'function') {
    window.deselectAll = function() {
        // Placeholder for deselectAll functionality
    };
}

if (typeof window.saveMappings !== 'function') {
    window.saveMappings = function() {
        toast.info('Save Mappings', {
            description: 'Batch save functionality will be implemented in Phase 2.',
            duration: 4000
        });
        // Batch save not yet implemented
        closeMappingModal();
    };
}

// ============================================================================
// Close modal when clicking outside
// ============================================================================

// ARCH-064: delegated, because #mapping-modal is injected on first open and so
// does not exist at DOMContentLoaded to attach a listener to.
document.addEventListener('click', function(e) {
    if (e.target && e.target.id === 'mapping-modal') {
        closeMappingModal();
    }
});

// ============================================================================
// Capability Mapping to APQC Processes and ArchiMate Elements
// ============================================================================

async function openCapabilityAPQCMapping(capabilityId, capabilityName) {
    // ARCH-064: hydrate the lazily-loaded dialog before reaching for its scope.
    if (!await window.CapabilityMapModals.ensure('apqc-mapping-modal')) { return; }

    // Open APQC mapping modal
    let modal = Alpine.$data(document.querySelector('#apqc-mapping-modal'));
    if (modal && modal.openMappingModal) {
        modal.openMappingModal(capabilityId, capabilityName);
    } else {
        Platform.toast.error('APQC mapping modal is not available. Please refresh the page.');
    }
}

async function openCapabilityArchimateMapping(capabilityId, capabilityName) {
    // ARCH-064: hydrate the lazily-loaded dialog before reaching for its scope.
    if (!await window.CapabilityMapModals.ensure('archimate-mapping-modal')) { return; }

    // Open ArchiMate mapping modal
    let modal = Alpine.$data(document.querySelector('#archimate-mapping-modal'));
    if (modal && modal.openMappingModal) {
        modal.openMappingModal(capabilityId, capabilityName);
    } else {
        Platform.toast.error('ArchiMate mapping modal is not available. Please refresh the page.');
    }
}

// =============================================================================
// ACM Technical Capability Tab Functions
// =============================================================================

let acmDomainsData = [];
let acmCapabilitiesData = [];
let acmFilteredData = [];
let acmCurrentPage = 1;
let acmPageSize = 25;
let acmApplicationsList = [];

async function loadTechnicalTab() {
    try {
        // Load domains
        let domainsData = await Platform.fetch('/capability-map/api/acm/domains', { silent: true });
        // Unchecked, a 500 parsed to `{}`: `domainsData.domains` was falsy, the render
        // was skipped, and the tab sat empty as if no domains existed.
        if (domainsData.domains) {
            acmDomainsData = domainsData.domains;
            renderACMDomains(domainsData);
        }

        // Load capabilities
        let capsData = await Platform.fetch('/capability-map/api/acm/capabilities', { silent: true });

        if (capsData.capabilities) {
            acmCapabilitiesData = capsData.capabilities;
            acmFilteredData = capsData.capabilities;
            acmCurrentPage = 1;
            renderACMCapabilitiesPage();
        }

        // Load applications for mapping modal
        loadApplicationsForACMMapping();

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        safeHTML(document.getElementById('acm-domains-grid'),
            '<div class="col-span-full text-center text-destructive py-8">' +
                '<i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>' +
                '<p>Error loading ACM data. Please try again.</p>' +
            '</div>');
    }
}

async function loadApplicationsForACMMapping() {
    try {
        let data = await Platform.fetch('/api/v1/applications/?per_page=500', { silent: true });
        // Unchecked, a 500 left the mapping dropdown holding only its
        // "Choose an application..." placeholder.
        if (data.success && data.data && data.data.applications) {
            acmApplicationsList = data.data.applications;
            populateACMApplicationsDropdown();
        }
    } catch (error) {
        if (window.Platform && Platform.toast) Platform.toast.error('Failed to load applications for mapping');
    }
}

function populateACMApplicationsDropdown() {
    let select = document.getElementById('acm-mapping-application');
    if (!select) return;

    safeHTML(select, '<option value="">Choose an application...</option>' +
        acmApplicationsList.map(function(app) { return '<option value="' + app.id + '">' + escapeHtml(app.name) + '</option>'; }).join(''));
}

function renderACMDomains(data) {
    let grid = document.getElementById('acm-domains-grid');
    let stats = data.statistics || {};

    // Update stats
    document.getElementById('acm-domain-count').textContent = stats.total_domains || 7;
    document.getElementById('acm-cap-count').textContent = stats.total_capabilities || 0;
    document.getElementById('acm-mapped-count').textContent = stats.mapped_capabilities || 0;
    document.getElementById('acm-coverage').textContent = (stats.overall_coverage || 0) + '%';

    // Domain colors
    let domainColors = {
        'USER-EXPERIENCE': { bg: 'bg-primary/5', border: 'border-primary/20', text: 'text-primary', icon: 'monitor' },
        'APPLICATION-SERVICES': { bg: 'bg-emerald-500/5', border: 'border-emerald-200', text: 'text-emerald-700', icon: 'server' },
        'DATA-STORAGE': { bg: 'bg-purple-50', border: 'border-purple-200', text: 'text-purple-700', icon: 'database' },
        'SECURITY-IDENTITY': { bg: 'bg-destructive/5', border: 'border-destructive/20', text: 'text-destructive', icon: 'shield' },
        'DEVOPS-PLATFORM': { bg: 'bg-orange-50', border: 'border-orange-200', text: 'text-orange-700', icon: 'settings' },
        'AI-ANALYTICS': { bg: 'bg-indigo-50', border: 'border-indigo-200', text: 'text-indigo-700', icon: 'brain' },
        'COMMUNICATION': { bg: 'bg-teal-50', border: 'border-teal-200', text: 'text-teal-700', icon: 'message-circle' },
    };

    safeHTML(grid, data.domains.map(function(domain) {
        let colors = domainColors[domain.domain] || { bg: 'bg-muted/30', border: 'border-border', text: 'text-muted-foreground', icon: 'layers' };
        let statusColor = domain.status === 'covered' ? 'bg-emerald-500' : domain.status === 'partial' ? 'bg-amber-500' : 'bg-destructive';

        return '<div class="' + colors.bg + ' ' + colors.border + ' border-2 rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"' +
                     ' role="button" tabindex="0" data-acm-action="filter-acm-domain"' +
                     ' data-domain="' + acmEscapeAttr(domain.domain) + '"' +
                     ' title="Filter capabilities to ' + acmEscapeAttr(domain.name) + '">' +
                '<div class="flex items-start justify-between mb-3">' +
                    '<div class="p-2 rounded-lg ' + colors.bg + '">' +
                        '<i data-lucide="' + colors.icon + '" class="w-6 h-6 ' + colors.text + '"></i>' +
                    '</div>' +
                    '<div class="w-3 h-3 rounded-full ' + statusColor + '" title="' + domain.coverage_percentage + '% coverage"></div>' +
                '</div>' +
                '<h3 class="font-semibold ' + colors.text + ' mb-1">' + escapeHtml(domain.name) + '</h3>' +
                '<p class="text-xs text-muted-foreground mb-3 line-clamp-2">' + escapeHtml(domain.description) + '</p>' +
                '<div class="flex items-center justify-between text-sm">' +
                    '<span class="text-muted-foreground">' + domain.total_capabilities + ' capabilities</span>' +
                    '<span class="' + colors.text + ' font-medium">' + domain.coverage_percentage + '%</span>' +
                '</div>' +
                '<div class="mt-2 w-full bg-border rounded-full h-1.5">' +
                    '<div class="' + statusColor + ' h-1.5 rounded-full transition-all" style="width: ' + domain.coverage_percentage + '%"></div>' +
                '</div>' +
                '<div class="mt-2 flex gap-2 text-xs text-muted-foreground">' +
                    '<span>L1: ' + domain.by_level.L1 + '</span>' +
                    '<span>L2: ' + domain.by_level.L2 + '</span>' +
                    '<span>L3: ' + domain.by_level.L3 + '</span>' +
                '</div>' +
            '</div>';
    }).join(''));
}

function renderACMCapabilitiesPage() {
    let totalRecords = acmFilteredData.length;
    let totalPages = Math.ceil(totalRecords / acmPageSize) || 1;
    let startIndex = (acmCurrentPage - 1) * acmPageSize;
    let endIndex = Math.min(startIndex + acmPageSize, totalRecords);
    let pageData = acmFilteredData.slice(startIndex, endIndex);

    // Update pagination info
    document.getElementById('acm-total-records').textContent = totalRecords;
    document.getElementById('acm-start-record').textContent = totalRecords > 0 ? startIndex + 1 : 0;
    document.getElementById('acm-end-record').textContent = endIndex;
    document.getElementById('acm-current-page').textContent = acmCurrentPage;
    document.getElementById('acm-total-pages').textContent = totalPages;

    // Update pagination buttons
    document.getElementById('acm-prev-btn').disabled = acmCurrentPage <= 1;
    document.getElementById('acm-next-btn').disabled = acmCurrentPage >= totalPages;

    // Render table
    renderACMCapabilities(pageData);
}

function renderACMCapabilities(capabilities) {
    let tbody = document.getElementById('acm-capabilities-table');

    if (!capabilities || capabilities.length === 0) {
        safeHTML(tbody,
            '<tr>' +
                '<td colspan="7" class="px-6 py-12 text-center text-muted-foreground">' +
                    'No capabilities found matching filters.' +
                '</td>' +
            '</tr>');
        return;
    }

    safeHTML(tbody, capabilities.map(function(cap) {
        let domainBadge = getACMDomainBadge(cap.acm_domain);
        let levelBadge = getACMLevelBadge(cap.level);
        let statusBadge = cap.status === 'mapped'
            ? '<span class="px-2 py-1 text-xs font-medium rounded-full bg-emerald-500/10 text-green-800">Mapped</span>'
            : '<span class="px-2 py-1 text-xs font-medium rounded-full bg-destructive/10 text-red-800">Gap</span>';


        let roadmapBadge = cap.on_roadmap
            ? '<span class="px-1.5 py-0.5 text-xs rounded bg-purple-100 text-purple-800 ml-1" title="On Roadmap"><i data-lucide="map" class="w-3 h-3 inline"></i></span>'
            : '';

        return '<tr class="hover:bg-accent">' +
                '<td class="px-6 py-4 whitespace-nowrap text-sm font-mono text-muted-foreground">' + escapeHtml(cap.code || '-') + '</td>' +
                '<td class="px-6 py-4">' +
                    '<div class="text-sm font-medium text-foreground">' + escapeHtml(cap.name) + '</div>' +
                    '<div class="text-xs text-muted-foreground truncate max-w-xs">' + escapeHtml(cap.description || '') + '</div>' +
                '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' + domainBadge + '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' + levelBadge + '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">' + cap.applications_count + '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' +
                    '<div class="flex items-center gap-1">' + statusBadge + roadmapBadge + '</div>' +
                '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' +
                    '<div class="flex items-center gap-2">' +
                        '<button type="button" data-acm-action="open-acm-mapping" data-cap-id="' + acmEscapeAttr(cap.id) + '" data-cap-name="' + acmEscapeAttr(cap.name || '') + '" data-domain="' + acmEscapeAttr(cap.acm_domain) + '" data-level="' + acmEscapeAttr(cap.level) + '" class="text-cyan-600 hover:text-cyan-800 text-sm">Map</button>' +
                        '<button type="button" data-acm-action="open-acm-detail" data-cap-id="' + acmEscapeAttr(cap.id) + '" data-cap-name="' + acmEscapeAttr(cap.name || '') + '" class="text-muted-foreground hover:text-foreground text-sm">View</button>' +
                        '<button type="button" data-acm-action="add-to-roadmap" data-cap-id="' + acmEscapeAttr(cap.id) + '" data-cap-name="' + acmEscapeAttr(cap.name || '') + '" data-cap-level="' + acmEscapeAttr(cap.level_number || 1) + '" class="text-primary hover:text-purple-800 text-sm ' + (cap.on_roadmap ? 'opacity-50 cursor-not-allowed' : '') + '"' +
                            (cap.on_roadmap ? ' disabled title="Already on roadmap"' : ' title="Add to roadmap"') + '>' +
                            '<i data-lucide="map" class="w-4 h-4 inline"></i>' +
                        '</button>' +
                    '</div>' +
                '</td>' +
            '</tr>';
    }).join(''));
}

// Pagination functions
function changeACMPageSize() {
    acmPageSize = parseInt(document.getElementById('acm-page-size').value);
    acmCurrentPage = 1;
    renderACMCapabilitiesPage();
    if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
}

function previousACMPage() {
    if (acmCurrentPage > 1) {
        acmCurrentPage--;
        renderACMCapabilitiesPage();
        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    }
}

function nextACMPage() {
    let totalPages = Math.ceil(acmFilteredData.length / acmPageSize);
    if (acmCurrentPage < totalPages) {
        acmCurrentPage++;
        renderACMCapabilitiesPage();
        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    }
}

function getACMDomainBadge(domain) {
    let colors = {
        'USER-EXPERIENCE': 'bg-primary/10 text-primary/90',
        'APPLICATION-SERVICES': 'bg-emerald-500/10 text-green-800',
        'DATA-STORAGE': 'bg-purple-100 text-purple-800',
        'SECURITY-IDENTITY': 'bg-destructive/10 text-red-800',
        'DEVOPS-PLATFORM': 'bg-orange-100 text-orange-800',
        'AI-ANALYTICS': 'bg-indigo-100 text-indigo-800',
        'COMMUNICATION': 'bg-teal-100 text-teal-800',
    };
    let colorClass = colors[domain] || 'bg-muted text-foreground';
    let shortName = domain ? domain.split('-').map(function(w) { return w[0]; }).join('') : '?';
    return '<span class="px-2 py-1 text-xs font-medium rounded-full ' + colorClass + '" title="' + domain + '">' + shortName + '</span>';
}

function getACMLevelBadge(level) {
    let colors = {
        'L0': 'bg-card text-primary-foreground',
        'L1': 'bg-cyan-100 text-cyan-800',
        'L2': 'bg-primary/10 text-primary/90',
        'L3': 'bg-purple-100 text-purple-800',
        'L4': 'bg-pink-100 text-pink-800',
    };
    let colorClass = colors[level] || 'bg-muted text-foreground';
    return '<span class="px-2 py-1 text-xs font-medium rounded-full ' + colorClass + '">' + level + '</span>';
}

function filterACMByDomain(domain) {
    document.getElementById('acm-domain-filter').value = domain;
    filterACMCapabilities();
}

function filterACMCapabilities() {
    let domainFilter = document.getElementById('acm-domain-filter').value;
    let levelFilter = document.getElementById('acm-level-filter').value;
    let statusFilter = document.getElementById('acm-status-filter').value;

    let filtered = acmCapabilitiesData;

    if (domainFilter) {
        filtered = filtered.filter(function(c) { return c.acm_domain === domainFilter; });
    }
    if (levelFilter) {
        filtered = filtered.filter(function(c) { return c.level === levelFilter; });
    }
    if (statusFilter) {
        filtered = filtered.filter(function(c) { return c.status === statusFilter; });
    }

    acmFilteredData = filtered;
    acmCurrentPage = 1;
    renderACMCapabilitiesPage();
    if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
}

function openACMCapabilityDetail(capabilityId, capabilityName) {
    openCapabilityDetail(capabilityId, capabilityName);
}

async function openCapabilityDetail(capabilityId, capabilityName) {
    let panel = document.getElementById('capability-detail-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'capability-detail-panel';
        panel.className = 'fixed inset-y-0 right-0 w-[420px] bg-card border-l border-border shadow-xl z-50 transform translate-x-full transition-transform duration-200 overflow-y-auto';
        panel.innerHTML = '<div class="p-6"><div class="flex items-center justify-between mb-4"><h3 class="text-lg font-semibold" id="cap-detail-title"></h3><button type="button" data-acm-action="close-capability-detail" aria-label="Close capability detail" class="text-muted-foreground hover:text-foreground p-1"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button></div><div id="cap-detail-content"><p class="text-sm text-muted-foreground">Loading...</p></div></div>';
        document.body.appendChild(panel);
    }
    document.getElementById('cap-detail-title').textContent = capabilityName;
    document.getElementById('cap-detail-content').innerHTML = '<p class="text-sm text-muted-foreground">Loading...</p>';
    panel.classList.remove('translate-x-full');

    try {
        let data = await Platform.fetch('/capability-map/api/capability/' + capabilityId + '/applications', { silent: true });
        let html = '';
        const cap = data.capability || {};
        html += '<div class="mb-4 space-y-1">';
        if (cap.code) html += '<p class="text-xs text-muted-foreground">' + cap.code + '</p>';
        html += '<div class="flex items-center gap-2">';
        html += '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-info/10 text-info-emphasis border border-info/30">Level ' + (cap.level || '?') + '</span>';
        html += '<span class="text-sm text-muted-foreground">' + (data.mapped_count || 0) + ' of ' + (data.total_count || 0) + ' apps mapped</span>';
        html += '</div></div>';
        if (cap.current_maturity !== undefined) {
            const cur = cap.current_maturity || 0;
            const tgt = cap.target_maturity || 0;
            html += '<div class="mb-4 p-3 bg-muted/30 rounded-lg space-y-2">';
            html += '<p class="text-xs font-medium text-muted-foreground uppercase tracking-wider">Maturity</p>';
            html += '<div class="flex items-center gap-2"><span class="text-xs w-16">Current</span><div class="flex-1 h-2 bg-muted rounded-full"><div class="h-2 bg-blue-500 rounded-full" style="width:' + (cur*20) + '%"></div></div><span class="text-xs">' + cur + '/5</span></div>';
            html += '<div class="flex items-center gap-2"><span class="text-xs w-16">Target</span><div class="flex-1 h-2 bg-muted rounded-full"><div class="h-2 bg-green-500 rounded-full" style="width:' + (tgt*20) + '%"></div></div><span class="text-xs">' + tgt + '/5</span></div>';
            html += '</div>';
        }
        // "Linked Applications" lists only apps mapped to this capability; the
        // endpoint returns every application (mapped and unmapped), so filter by
        // is_mapped to keep this count consistent with the "X of Y apps mapped"
        // line above, which is derived from the same is_mapped flag server-side.
        const apps = (data.applications || []).filter(function(app) { return app.is_mapped; });
        html += '<div class="mb-2"><p class="text-sm font-medium mb-2">Linked Applications (' + apps.length + ')</p>';
        if (apps.length === 0) {
            html += '<p class="text-sm text-muted-foreground py-4 text-center">No applications mapped to this capability.</p>';
        } else {
            html += '<div class="space-y-2">';
            apps.forEach(function(app) {
                html += '<div class="p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors">';
                html += '<a href="/applications/' + app.id + '" class="text-sm font-medium text-foreground hover:text-primary">' + (app.name || 'Unknown') + '</a>';
                if (app.support_level) html += '<p class="text-xs text-muted-foreground mt-1">Support: ' + app.support_level + '</p>';
                if (app.domain) html += '<p class="text-xs text-muted-foreground">Domain: ' + app.domain + '</p>';
                html += '</div>';
            });
            html += '</div>';
        }
        html += '</div>';
        document.getElementById('cap-detail-content').innerHTML = html;
    } catch (err) {
        // silent:true above, because this panel paints its own failure state --
        // without it the panel would sit on "Loading..." forever and read as a
        // slow request rather than a failed one.
        document.getElementById('cap-detail-content').innerHTML =
            '<p class="text-sm text-destructive">Failed to load: ' + err.message + '</p>';
    }
}

function closeCapabilityDetail() {
    let panel = document.getElementById('capability-detail-panel');
    if (panel) panel.classList.add('translate-x-full');
}

// =============================================================================
// ACM Mapping Modal Functions (Consistent with other tabs)
// =============================================================================

let acmCurrentCapabilityId = null;
let acmCurrentCapabilityName = '';
let acmModalApplicationsData = [];
let acmSelectedApplications = new Map();

async function openACMMappingModal(capabilityId, capabilityName, domain, level) {
    // ARCH-064: hydrate the lazily-loaded dialog before touching its DOM.
    if (!await window.CapabilityMapModals.ensure('acm-mapping-modal')) { return; }

    acmCurrentCapabilityId = String(capabilityId);
    acmCurrentCapabilityName = capabilityName;
    acmSelectedApplications.clear();

    // Update modal title
    let modalNameEl = document.getElementById('acm-modal-capability-name');
    if (modalNameEl) {
        modalNameEl.textContent = capabilityName;
    }

    // Show modal
    if (document.getElementById('acm-mapping-modal')) {
        Platform.modal.open('acm-mapping-modal');
        document.body.classList.add('overflow-hidden');
    }

    // Load applications for this technical capability
    loadACMApplicationsForCapability(capabilityId);
}

function closeACMMappingModal() {
    if (document.getElementById('acm-mapping-modal')) {
        Platform.modal.close('acm-mapping-modal');
        document.body.classList.remove('overflow-hidden');
    }
    acmCurrentCapabilityId = null;
    acmCurrentCapabilityName = '';
    acmSelectedApplications.clear();
    acmModalApplicationsData = [];
}

async function loadACMApplicationsForCapability(capabilityId) {
    try {
        let id = String(capabilityId);
        let data = await Platform.fetch('/capability-map/api/acm/capability/' + id + '/applications', { silent: true });
        // Unchecked, a 500 opened the mapping modal with an empty application list —
        // the existing mappings looked as though they had been cleared.

        if (data.error) {
            showACMNotification('Error loading applications: ' + data.error, 'error');
            return;
        }

        acmModalApplicationsData = data.applications || [];

        // Pre-select mapped applications
        acmSelectedApplications.clear();
        acmModalApplicationsData.forEach(function(app) {
            if (app.is_mapped) {
                acmSelectedApplications.set(app.id, {
                    application_id: app.id,
                    mapping_id: app.mapping_id,
                    mapping: {
                        capability_coverage: app.capability_coverage || 'partial',
                        maturity_level: app.maturity_level || 'defined',
                        notes: app.notes || ''
                    }
                });
            }
        });

        // Reset filters
        let searchInput = document.getElementById('acm-application-search');
        let typeFilter = document.getElementById('acm-filter-type');
        let domainFilter = document.getElementById('acm-filter-domain');
        let statusFilter = document.getElementById('acm-filter-status');
        let sortSelect = document.getElementById('acm-sort-applications');

        if (searchInput) searchInput.value = '';
        if (typeFilter) typeFilter.value = '';
        if (domainFilter) domainFilter.value = '';
        if (statusFilter) statusFilter.value = 'all';
        if (sortSelect) sortSelect.value = 'name-asc';

        // Populate filter options
        populateACMFilterOptions();

        // Render applications
        renderACMApplicationsList();

        // Focus search input
        setTimeout(function() {
            if (searchInput) searchInput.focus();
        }, 100);
    } catch (error) {
        showACMNotification('Error loading applications', 'error');
    }
}

function populateACMFilterOptions() {
    let typeFilter = document.getElementById('acm-filter-type');
    if (typeFilter && acmModalApplicationsData.length > 0) {
        let types = [];
        let typeSet = {};
        acmModalApplicationsData.forEach(function(app) {
            if (app.type && !typeSet[app.type]) {
                typeSet[app.type] = true;
                types.push(app.type);
            }
        });
        types.sort();
        let currentValue = typeFilter.value;
        safeHTML(typeFilter, '<option value="">All Types</option>' +
            types.map(function(type) { return '<option value="' + escapeHtml(type) + '">' + escapeHtml(type) + '</option>'; }).join(''));
        if (currentValue) typeFilter.value = currentValue;
    }

    let domainFilter = document.getElementById('acm-filter-domain');
    if (domainFilter && acmModalApplicationsData.length > 0) {
        let domains = [];
        let domainSet = {};
        acmModalApplicationsData.forEach(function(app) {
            if (app.domain && !domainSet[app.domain]) {
                domainSet[app.domain] = true;
                domains.push(app.domain);
            }
        });
        domains.sort();
        let currentValue2 = domainFilter.value;
        safeHTML(domainFilter, '<option value="">All Domains</option>' +
            domains.map(function(domain) { return '<option value="' + escapeHtml(domain) + '">' + escapeHtml(domain) + '</option>'; }).join(''));
        if (currentValue2) domainFilter.value = currentValue2;
    }
}

function filterACMApplications() {
    renderACMApplicationsList();
}

function renderACMApplicationsList() {
    let container = document.getElementById('acm-applications-list');
    let searchEl = document.getElementById('acm-application-search');
    let searchTerm = searchEl ? searchEl.value.toLowerCase() : '';
    let filterTypeEl = document.getElementById('acm-filter-type');
    let filterType = filterTypeEl ? filterTypeEl.value : '';
    let filterDomainEl = document.getElementById('acm-filter-domain');
    let filterDomain = filterDomainEl ? filterDomainEl.value : '';
    let filterStatusEl = document.getElementById('acm-filter-status');
    let filterStatus = filterStatusEl ? filterStatusEl.value : 'all';
    let sortEl = document.getElementById('acm-sort-applications');
    let sortBy = sortEl ? sortEl.value : 'name-asc';

    if (!container) return;

    // Filter applications
    let filtered = acmModalApplicationsData.filter(function(app) {
        let matchesSearch = !searchTerm ||
            (app.name && app.name.toLowerCase().includes(searchTerm)) ||
            (app.type && app.type.toLowerCase().includes(searchTerm)) ||
            (app.domain && app.domain.toLowerCase().includes(searchTerm)) ||
            (app.description && app.description.toLowerCase().includes(searchTerm));

        let matchesType = !filterType || app.type === filterType;
        let matchesDomain = !filterDomain || app.domain === filterDomain;

        let matchesStatus = true;
        if (filterStatus === 'mapped') {
            matchesStatus = app.is_mapped === true;
        } else if (filterStatus === 'unmapped') {
            matchesStatus = app.is_mapped !== true;
        }

        return matchesSearch && matchesType && matchesDomain && matchesStatus;
    });

    // Sort applications
    filtered.sort(function(a, b) {
        switch(sortBy) {
            case 'name-asc': return (a.name || '').localeCompare(b.name || '');
            case 'name-desc': return (b.name || '').localeCompare(a.name || '');
            case 'type-asc': return (a.type || '').localeCompare(b.type || '');
            case 'mapped-first':
                if (a.is_mapped && !b.is_mapped) return -1;
                if (!a.is_mapped && b.is_mapped) return 1;
                return (a.name || '').localeCompare(b.name || '');
            case 'unmapped-first':
                if (!a.is_mapped && b.is_mapped) return -1;
                if (a.is_mapped && !b.is_mapped) return 1;
                return (a.name || '').localeCompare(b.name || '');
            default: return 0;
        }
    });

    // Update counts
    let filteredCountEl = document.getElementById('acm-filtered-count');
    let totalCountEl = document.getElementById('acm-total-count');
    let selectedCountEl = document.getElementById('acm-selected-count');
    if (filteredCountEl) filteredCountEl.textContent = filtered.length;
    if (totalCountEl) totalCountEl.textContent = acmModalApplicationsData.length;
    if (selectedCountEl) selectedCountEl.textContent = acmSelectedApplications.size;

    if (filtered.length === 0) {
        safeHTML(container,
            '<div class="text-center py-12 text-muted-foreground">' +
                '<i data-lucide="search-x" class="w-12 h-12 mx-auto mb-3 text-muted-foreground"></i>' +
                '<p class="text-lg font-medium">No applications found</p>' +
                '<p class="text-sm mt-1">Try adjusting your search or filters</p>' +
            '</div>');
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    safeHTML(container, filtered.map(function(app) {
        let isSelected = acmSelectedApplications.has(app.id);
        let mapping = acmSelectedApplications.get(app.id);

        return '<div class="border rounded-lg p-4 transition-all ' + (isSelected ? 'bg-cyan-50 border-cyan-300 shadow-sm' : 'bg-background border-border hover:border-input hover:shadow-sm') + '">' +
                '<div class="flex items-start justify-between">' +
                    '<div class="flex items-start space-x-3 flex-1">' +
                        '<input type="checkbox" ' + (isSelected ? 'checked' : '') +
                            ' onchange="toggleACMApplicationSelection(\'' + app.id + '\')"' +
                            ' class="mt-1 h-5 w-5 text-cyan-600 focus:ring-cyan-500 border-input rounded cursor-pointer"' +
                            ' title="' + (isSelected ? 'Deselect' : 'Select') + ' ' + escapeHtml(app.name) + '"' +
                            ' aria-label="' + (isSelected ? 'Deselect' : 'Select') + ' ' + escapeHtml(app.name) + '" />' +
                        '<div class="flex-1">' +
                            '<div class="flex items-center space-x-2">' +
                                '<div class="font-medium text-foreground">' + escapeHtml(app.name || 'Unknown') + '</div>' +
                                (app.is_mapped ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-emerald-500/10 text-green-800 rounded-full"><i data-lucide="check-circle" class="w-3 h-3 mr-1"></i>Mapped</span>' : '') +
                            '</div>' +
                            '<div class="flex items-center space-x-2 mt-1 text-sm text-muted-foreground">' +
                                '<span class="flex items-center"><i data-lucide="layers" class="w-3 h-3 mr-1"></i>' + escapeHtml(app.type || 'Unknown') + '</span>' +
                                '<span>&bull;</span>' +
                                '<span class="flex items-center"><i data-lucide="building" class="w-3 h-3 mr-1"></i>' + escapeHtml(app.domain || 'Not specified') + '</span>' +
                            '</div>' +
                            (app.description ? '<div class="text-xs text-muted-foreground mt-2 line-clamp-2">' + escapeHtml(app.description) + '</div>' : '') +
                        '</div>' +
                    '</div>' +
                    (app.is_mapped && app.mapping_id ?
                        '<button type="button" data-acm-action="delete-acm-mapping"' +
                            ' data-mapping-id="' + acmEscapeAttr(app.mapping_id) + '"' +
                            ' data-app-id="' + acmEscapeAttr(app.id) + '"' +
                            ' class="ml-2 px-3 py-1.5 text-xs bg-destructive text-primary-foreground rounded hover:bg-destructive transition-colors flex items-center space-x-1"' +
                            ' title="Remove mapping">' +
                            '<i data-lucide="trash-2" class="w-3 h-3"></i><span>Remove</span>' +
                        '</button>' : '') +
                '</div>' +
                (isSelected ? renderACMApplicationSettings(app.id, mapping) : '') +
            '</div>';
    }).join(''));

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderACMApplicationSettings(appId, mapping) {
    let mappingData = (mapping && mapping.mapping) ? mapping.mapping : {};
    return '<div class="mt-4 pt-4 border-t border-border space-y-3">' +
            '<div class="grid grid-cols-3 gap-4">' +
                '<div>' +
                    '<label class="block text-xs font-medium text-muted-foreground mb-1">Coverage Level</label>' +
                    '<select class="w-full text-sm border border-input rounded px-2 py-1"' +
                        ' onchange="updateACMApplicationMapping(\'' + appId + '\', \'capability_coverage\', this.value)">' +
                        '<option value="full" ' + (mappingData.capability_coverage === 'full' ? 'selected' : '') + '>Full</option>' +
                        '<option value="partial" ' + (mappingData.capability_coverage === 'partial' || !mappingData.capability_coverage ? 'selected' : '') + '>Partial</option>' +
                        '<option value="minimal" ' + (mappingData.capability_coverage === 'minimal' ? 'selected' : '') + '>Minimal</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-muted-foreground mb-1">Maturity Level</label>' +
                    '<select class="w-full text-sm border border-input rounded px-2 py-1"' +
                        ' onchange="updateACMApplicationMapping(\'' + appId + '\', \'maturity_level\', this.value)">' +
                        '<option value="initial" ' + (mappingData.maturity_level === 'initial' ? 'selected' : '') + '>Initial</option>' +
                        '<option value="developing" ' + (mappingData.maturity_level === 'developing' ? 'selected' : '') + '>Developing</option>' +
                        '<option value="defined" ' + (mappingData.maturity_level === 'defined' || !mappingData.maturity_level ? 'selected' : '') + '>Defined</option>' +
                        '<option value="managed" ' + (mappingData.maturity_level === 'managed' ? 'selected' : '') + '>Managed</option>' +
                        '<option value="optimized" ' + (mappingData.maturity_level === 'optimized' ? 'selected' : '') + '>Optimized</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-muted-foreground mb-1">Notes</label>' +
                    '<input type="text" value="' + (mappingData.notes || '') + '" placeholder="Optional notes..."' +
                        ' aria-label="Notes"' +
                        ' class="w-full text-sm border border-input rounded px-2 py-1"' +
                        ' onchange="updateACMApplicationMapping(\'' + appId + '\', \'notes\', this.value)" />' +
                '</div>' +
            '</div>' +
        '</div>';
}

function toggleACMApplicationSelection(appId) {
    if (acmSelectedApplications.has(appId)) {
        acmSelectedApplications.delete(appId);
    } else {
        acmSelectedApplications.set(appId, {
            application_id: appId,
            mapping: {
                capability_coverage: 'partial',
                maturity_level: 'defined',
                notes: ''
            }
        });
    }
    renderACMApplicationsList();
}

function updateACMApplicationMapping(appId, field, value) {
    let existing = acmSelectedApplications.get(appId);
    if (existing) {
        if (!existing.mapping) existing.mapping = {};
        existing.mapping[field] = value;
        acmSelectedApplications.set(appId, existing);
    }
}

function selectAllACMFiltered() {
    let container = document.getElementById('acm-applications-list');
    if (!container) return;

    let checkboxes = container.querySelectorAll('input[type="checkbox"]:not(:checked)');
    checkboxes.forEach(function(checkbox) {
        let onchangeAttr = checkbox.getAttribute('onchange');
        let match = onchangeAttr ? onchangeAttr.match(/toggleACMApplicationSelection\('([^']+)'\)/) : null;
        if (match) {
            let appId = match[1];
            if (!acmSelectedApplications.has(appId)) {
                toggleACMApplicationSelection(appId);
            }
        }
    });
}

function deselectAllACM() {
    acmSelectedApplications.clear();
    renderACMApplicationsList();
}

async function deleteACMMapping(mappingId, appId) {
    let modalId = window.modalManager.createModal({
        title: 'Remove Mapping',
        content: '<p class="text-sm text-muted-foreground">Remove this application mapping?</p>',
        size: 'small',
        buttons: [
            { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
            { text: 'Remove', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'remove', handler: async function() { await _doDeleteACMMapping(mappingId, appId); } }
        ]
    });
    window.modalManager.open(modalId);
}

async function _doDeleteACMMapping(mappingId, appId) {
    try {
        let data = await Platform.fetch.delete('/capability-map/api/acm/mapping/' + mappingId, { silent: true });

        if (data.success) {
            // Update local state
            acmSelectedApplications.delete(appId);
            let appIndex = acmModalApplicationsData.findIndex(function(a) { return a.id == appId; });
            if (appIndex >= 0) {
                acmModalApplicationsData[appIndex].is_mapped = false;
                acmModalApplicationsData[appIndex].mapping_id = null;
            }
            renderACMApplicationsList();
            showACMNotification('Mapping removed successfully', 'success');
        } else {
            showACMNotification('Error: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        showACMNotification('Network error', 'error');
    }
}

async function saveACMMappings() {
    if (acmSelectedApplications.size === 0) {
        showACMNotification('No applications selected', 'error');
        return;
    }

    try {
        let mappings = Array.from(acmSelectedApplications.entries()).map(function(entry) {
            let appId = entry[0];
            let data = entry[1];
            return {
                application_id: parseInt(appId),
                technical_capability_id: parseInt(acmCurrentCapabilityId),
                capability_coverage: (data.mapping && data.mapping.capability_coverage) ? data.mapping.capability_coverage : 'partial',
                maturity_level: (data.mapping && data.mapping.maturity_level) ? data.mapping.maturity_level : 'defined',
                notes: (data.mapping && data.mapping.notes) ? data.mapping.notes : ''
            };
        });

        let data = await Platform.fetch.post('/capability-map/api/acm/mappings/bulk', { mappings: mappings }, { silent: true });

        if (data.success) {
            closeACMMappingModal();
            showACMNotification('Successfully saved ' + (data.created || 0) + ' new and updated ' + (data.updated || 0) + ' mappings', 'success');
            loadTechnicalTab();
        } else {
            showACMNotification('Error: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        showACMNotification('Network error. Please try again.', 'error');
    }
}

function showACMNotification(message, type) {
    type = type || 'info';
    let toastEl = document.createElement('div');
    toastEl.className = 'fixed bottom-4 right-4 z-50 px-6 py-3 rounded-lg shadow-lg text-primary-foreground ' + (type === 'success' ? 'bg-emerald-600' : type === 'error' ? 'bg-destructive' : 'bg-cyan-600');
    toastEl.textContent = message;
    document.body.appendChild(toastEl);

    setTimeout(function() {
        toastEl.remove();
    }, 3000);
}

// =============================================================================
// Process Mapping Modal Functions
// =============================================================================

let processCurrentProcessId = null;
let processCurrentProcessName = '';
let processModalApplicationsData = [];
let processSelectedApplications = new Map();

function openProcessMappingModal(processId, processName, processCode, processType) {
    // Check if user is authenticated before opening modal
    Platform.fetch('/capability-map/api/check-auth', { silent: true })
        .then(async function(data) {
            if (data.authenticated) {
                // ARCH-064: hydrate the lazily-loaded dialog before touching its DOM.
                if (!await window.CapabilityMapModals.ensure('process-mapping-modal')) { return; }

                // User is authenticated, proceed with modal
                processCurrentProcessId = String(processId);
                processCurrentProcessName = processName;
                processSelectedApplications.clear();

                // Update modal title
                let modalNameEl = document.getElementById('process-modal-process-name');
                if (modalNameEl) {
                    modalNameEl.textContent = processName;
                }

                // Show modal
                if (document.getElementById('process-mapping-modal')) {
                    Platform.modal.open('process-mapping-modal');
                    document.body.classList.add('overflow-hidden');
                }

                // Load applications for this process
                loadProcessApplicationsForProcess(processId);
            } else {
                // FAR-001: Show login prompt without auto-redirect (was causing page to navigate away)
                showProcessNotification('Please log in to map applications to processes', 'error');
            }
        })
        .catch(function(error) {
            showProcessNotification('Error checking authentication status', 'error');
        });
}

function closeProcessMappingModal() {
    if (document.getElementById('process-mapping-modal')) {
        Platform.modal.close('process-mapping-modal');
        document.body.classList.remove('overflow-hidden');
    }
    processCurrentProcessId = null;
    processCurrentProcessName = '';
    processSelectedApplications.clear();
    processModalApplicationsData = [];
}

async function loadProcessApplicationsForProcess(processId) {
    try {
        let id = String(processId);
        let data = await Platform.fetch('/capability-map/api/process-gaps/process/' + id + '/applications', { silent: true });
        // Unchecked, a 500 opened the mapping modal with an empty application list —
        // the existing mappings looked as though they had been cleared.

        if (data.error) {
            showProcessNotification('Error loading applications: ' + data.error, 'error');
            return;
        }

        processModalApplicationsData = data.applications || [];

        // Pre-select mapped applications
        processSelectedApplications.clear();
        processModalApplicationsData.forEach(function(app) {
            if (app.is_mapped) {
                processSelectedApplications.set(app.id, {
                    application_id: app.id,
                    mapping_id: app.mapping_id,
                    mapping: {
                        support_level: app.support_level || 'partial',
                        automation_level: app.automation_level || 1,
                        notes: app.notes || ''
                    }
                });
            }
        });

        // Reset filters
        let searchInput = document.getElementById('process-application-search');
        let typeFilter = document.getElementById('process-filter-type');
        let domainFilter = document.getElementById('process-filter-domain');
        let statusFilter = document.getElementById('process-filter-status');
        let sortSelect = document.getElementById('process-sort-applications');

        if (searchInput) searchInput.value = '';
        if (typeFilter) typeFilter.value = '';
        if (domainFilter) domainFilter.value = '';
        if (statusFilter) statusFilter.value = 'all';
        if (sortSelect) sortSelect.value = 'name-asc';

        // Populate filter options
        populateProcessFilterOptions();

        // Render applications
        renderProcessApplicationsList();

        // Focus search input
        setTimeout(function() {
            if (searchInput) searchInput.focus();
        }, 100);
    } catch (error) {
        showProcessNotification('Error loading applications', 'error');
    }
}

function populateProcessFilterOptions() {
    let typeFilter = document.getElementById('process-filter-type');
    if (typeFilter && processModalApplicationsData.length > 0) {
        let types = [];
        let typeSet = {};
        processModalApplicationsData.forEach(function(app) {
            if (app.type && !typeSet[app.type]) {
                typeSet[app.type] = true;
                types.push(app.type);
            }
        });
        types.sort();
        safeHTML(typeFilter, '<option value="">All Types</option>' +
            types.map(function(type) { return '<option value="' + escapeHtml(type) + '">' + escapeHtml(type) + '</option>'; }).join(''));
    }

    let domainFilter = document.getElementById('process-filter-domain');
    if (domainFilter && processModalApplicationsData.length > 0) {
        let domains = [];
        let domainSet = {};
        processModalApplicationsData.forEach(function(app) {
            if (app.domain && !domainSet[app.domain]) {
                domainSet[app.domain] = true;
                domains.push(app.domain);
            }
        });
        domains.sort();
        safeHTML(domainFilter, '<option value="">All Domains</option>' +
            domains.map(function(domain) { return '<option value="' + escapeHtml(domain) + '">' + escapeHtml(domain) + '</option>'; }).join(''));
    }
}

function renderProcessApplicationsList() {
    let container = document.getElementById('process-applications-list');
    if (!container) return;

    let filteredData = filterProcessApplicationsData();
    let sortedData = sortProcessApplicationsData(filteredData);

    safeHTML(container, sortedData.map(function(app) { return renderProcessApplicationItem(app); }).join(''));
    updateProcessSelectionCount();

    if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
}

function renderProcessApplicationItem(app) {
    let isSelected = processSelectedApplications.has(app.id);
    let mapping = processSelectedApplications.get(app.id);

    return '<div class="border rounded-lg p-4 ' + (isSelected ? 'border-primary bg-primary/5' : 'border-border') + '">' +
            '<div class="flex items-start justify-between">' +
                '<div class="flex items-start space-x-3 flex-1">' +
                    '<input type="checkbox" id="process-app-' + app.id + '"' +
                        (isSelected ? ' checked' : '') +
                        ' onchange="toggleProcessApplicationSelection(' + app.id + ')"' +
                        ' class="mt-1 rounded border-input text-primary focus-visible:ring-ring">' +
                    '<div class="flex-1">' +
                        '<label for="process-app-' + app.id + '" class="font-medium text-foreground cursor-pointer">' +
                            escapeHtml(app.name) +
                        '</label>' +
                        '<div class="text-sm text-muted-foreground mt-1">' +
                            escapeHtml(app.type) + (app.domain ? ' &bull; ' + escapeHtml(app.domain) : '') +
                        '</div>' +
                        (app.description ? '<div class="text-sm text-muted-foreground mt-2">' + escapeHtml(app.description) + '</div>' : '') +
                    '</div>' +
                '</div>' +
                '<div class="text-right">' +
                    '<span class="px-2 py-1 text-xs rounded-full ' + (app.status === 'active' ? 'bg-emerald-500/10 text-green-800' : 'bg-muted text-foreground') + '">' +
                        escapeHtml(app.status) +
                    '</span>' +
                '</div>' +
            '</div>' +

            (isSelected ?
                '<div class="mt-4 pt-4 border-t border-border">' +
                    '<div class="grid grid-cols-1 md:grid-cols-3 gap-4">' +
                        '<div>' +
                            '<label class="block text-sm font-medium text-muted-foreground mb-1">Support Level</label>' +
                            '<select id="process-support-' + app.id + '" class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">' +
                                '<option value="full" ' + (mapping && mapping.mapping && mapping.mapping.support_level === 'full' ? 'selected' : '') + '>Full Support</option>' +
                                '<option value="partial" ' + (mapping && mapping.mapping && mapping.mapping.support_level === 'partial' ? 'selected' : '') + '>Partial Support</option>' +
                                '<option value="minimal" ' + (mapping && mapping.mapping && mapping.mapping.support_level === 'minimal' ? 'selected' : '') + '>Minimal Support</option>' +
                            '</select>' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-sm font-medium text-muted-foreground mb-1">Automation Level</label>' +
                            '<select id="process-automation-' + app.id + '" class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">' +
                                '<option value="1" ' + (mapping && mapping.mapping && mapping.mapping.automation_level === 1 ? 'selected' : '') + '>Manual</option>' +
                                '<option value="2" ' + (mapping && mapping.mapping && mapping.mapping.automation_level === 2 ? 'selected' : '') + '>Basic Automation</option>' +
                                '<option value="3" ' + (mapping && mapping.mapping && mapping.mapping.automation_level === 3 ? 'selected' : '') + '>Moderate Automation</option>' +
                                '<option value="4" ' + (mapping && mapping.mapping && mapping.mapping.automation_level === 4 ? 'selected' : '') + '>High Automation</option>' +
                                '<option value="5" ' + (mapping && mapping.mapping && mapping.mapping.automation_level === 5 ? 'selected' : '') + '>Full Automation</option>' +
                            '</select>' +
                        '</div>' +
                        '<div>' +
                            '<label class="block text-sm font-medium text-muted-foreground mb-1">Notes</label>' +
                            '<input type="text" id="process-notes-' + app.id + '" value="' + (mapping && mapping.mapping && mapping.mapping.notes ? mapping.mapping.notes : '') + '"' +
                                ' aria-label="Notes"' +
                                ' placeholder="Add notes..."' +
                                ' class="w-full px-3 py-2 text-sm border border-input rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">' +
                        '</div>' +
                    '</div>' +
                '</div>' : '') +
        '</div>';
}

function toggleProcessApplicationSelection(applicationId) {
    if (processSelectedApplications.has(applicationId)) {
        processSelectedApplications.delete(applicationId);
    } else {
        processSelectedApplications.set(applicationId, {
            application_id: applicationId,
            mapping_id: null,
            mapping: {
                support_level: 'partial',
                automation_level: 1,
                notes: ''
            }
        });
    }
    renderProcessApplicationsList();
}

function filterProcessApplicationsData() {
    let searchEl = document.getElementById('process-application-search');
    let searchTerm = searchEl ? searchEl.value.toLowerCase() : '';
    let typeFilterEl = document.getElementById('process-filter-type');
    let typeFilter = typeFilterEl ? typeFilterEl.value : '';
    let domainFilterEl = document.getElementById('process-filter-domain');
    let domainFilter = domainFilterEl ? domainFilterEl.value : '';
    let statusFilterEl = document.getElementById('process-filter-status');
    let statusFilter = statusFilterEl ? statusFilterEl.value : '';

    return processModalApplicationsData.filter(function(app) {
        let matchesSearch = !searchTerm ||
            app.name.toLowerCase().includes(searchTerm) ||
            (app.type && app.type.toLowerCase().includes(searchTerm)) ||
            (app.domain && app.domain.toLowerCase().includes(searchTerm));

        let matchesType = !typeFilter || app.type === typeFilter;
        let matchesDomain = !domainFilter || app.domain === domainFilter;

        let matchesStatus = statusFilter === 'all' ||
            (statusFilter === 'mapped' && processSelectedApplications.has(app.id)) ||
            (statusFilter === 'unmapped' && !processSelectedApplications.has(app.id));

        return matchesSearch && matchesType && matchesDomain && matchesStatus;
    });
}

function sortProcessApplicationsData(data) {
    let sortEl = document.getElementById('process-sort-applications');
    let sortValue = sortEl ? sortEl.value : 'name-asc';

    return data.slice().sort(function(a, b) {
        switch (sortValue) {
            case 'name-asc':
                return a.name.localeCompare(b.name);
            case 'name-desc':
                return b.name.localeCompare(a.name);
            case 'type-asc':
                return (a.type || '').localeCompare(b.type || '');
            case 'mapped-first':
                return (processSelectedApplications.has(b.id) ? 1 : 0) - (processSelectedApplications.has(a.id) ? 1 : 0);
            case 'unmapped-first':
                return (processSelectedApplications.has(a.id) ? 1 : 0) - (processSelectedApplications.has(b.id) ? 1 : 0);
            default:
                return 0;
        }
    });
}

function filterProcessApplications() {
    renderProcessApplicationsList();
}

function selectAllProcessFiltered() {
    let filteredData = filterProcessApplicationsData();
    filteredData.forEach(function(app) {
        if (!processSelectedApplications.has(app.id)) {
            processSelectedApplications.set(app.id, {
                application_id: app.id,
                mapping_id: null,
                mapping: {
                    support_level: 'partial',
                    automation_level: 1,
                    notes: ''
                }
            });
        }
    });
    renderProcessApplicationsList();
}

function deselectAllProcess() {
    processSelectedApplications.clear();
    renderProcessApplicationsList();
}

function updateProcessSelectionCount() {
    let selectedCountEl = document.getElementById('process-selected-count');
    let filteredCountEl = document.getElementById('process-filtered-count');
    let totalCountEl = document.getElementById('process-total-count');

    if (selectedCountEl) selectedCountEl.textContent = processSelectedApplications.size;
    if (filteredCountEl) filteredCountEl.textContent = filterProcessApplicationsData().length;
    if (totalCountEl) totalCountEl.textContent = processModalApplicationsData.length;
}

async function saveProcessMappings() {
    try {
        // Check authentication before saving
        let authData = await Platform.fetch('/capability-map/api/check-auth', { silent: true });
        // Unchecked, a 500 made `authenticated` undefined and the save aborted with
        // "Please log in" at a user who was logged in the whole time.

        if (!authData.authenticated) {
            // FAR-001: Show login prompt without auto-redirect (was causing page to navigate away)
            showProcessNotification('Please log in to save mappings', 'error');
            return;
        }

        let mappings = [];

        processSelectedApplications.forEach(function(mapping, applicationId) {
            let supportEl = document.getElementById('process-support-' + applicationId);
            let supportLevel = supportEl ? supportEl.value : 'partial';
            let automationEl = document.getElementById('process-automation-' + applicationId);
            let automationLevel = automationEl ? automationEl.value : 1;
            let notesEl = document.getElementById('process-notes-' + applicationId);
            let notes = notesEl ? notesEl.value : '';

            mappings.push({
                application_id: applicationId,
                apqc_process_id: processCurrentProcessId,
                support_level: supportLevel,
                automation_level: parseInt(automationLevel),
                notes: notes,
                mapping_id: mapping.mapping_id
            });
        });

        let result = await Platform.fetch.post('/capability-map/api/process-gaps/mappings/bulk', { mappings: mappings }, { silent: true });

        if (result.success) {
            showProcessNotification('Successfully saved ' + mappings.length + ' mappings', 'success');
            closeProcessMappingModal();
            // Refresh process gap data
            loadProcessGapData();
        } else {
            showProcessNotification('Error saving mappings: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        showProcessNotification('Mappings were NOT saved — ' + ((error && error.message) || 'request failed'), 'error');
    }
}

function showProcessNotification(message, type) {
    type = type || 'info';
    // Simple notification
    let notification = document.createElement('div');
    notification.className = 'fixed top-4 right-4 p-4 rounded-md shadow-lg z-50 ' +
        (type === 'success' ? 'bg-emerald-500 text-primary-foreground' :
        type === 'error' ? 'bg-destructive text-primary-foreground' :
        'bg-primary text-primary-foreground');
    notification.textContent = message;

    document.body.appendChild(notification);

    setTimeout(function() {
        if (notification.parentNode) {
            notification.parentNode.removeChild(notification);
        }
    }, 3000);
}

// =============================================================================
// Business Domain Cards Functions (Unified Tab)
// =============================================================================

async function loadBusinessDomainCards() {
    try {
        let data = await Platform.fetch('/capability-map/api/unified/domains', { silent: true });

        if (data.success) {
            // Update statistics
            document.getElementById('unified-domain-count').textContent = data.domain_count || 0;
            document.getElementById('unified-cap-count').textContent = data.total_capabilities || 0;
            document.getElementById('unified-mapped-count').textContent = data.mapped_count || 0;
            document.getElementById('unified-coverage').textContent = (data.coverage || 0) + '%';

            // Render domain cards
            renderBusinessDomainCards(data.domains || []);
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        if (window.Platform && Platform.toast) Platform.toast.error('Failed to load business domains');
    }
}

function renderBusinessDomainCards(domains) {
    let grid = document.getElementById('unified-domains-grid');
    if (!domains || domains.length === 0) {
        safeHTML(grid, '<div class="col-span-full text-center py-8 text-muted-foreground">No business domains configured.</div>');
        return;
    }

    // Full class strings to avoid dynamic class construction (breaks Tailwind purge)
    let domainColorSets = [
        { bg: 'bg-primary/5', border: 'border-primary/20', textBold: 'text-primary', textHead: 'text-primary/90', badge: 'bg-primary/10' },
        { bg: 'bg-emerald-500/5', border: 'border-emerald-200', textBold: 'text-emerald-600', textHead: 'text-green-800', badge: 'bg-emerald-500/10' },
        { bg: 'bg-purple-50', border: 'border-purple-200', textBold: 'text-primary', textHead: 'text-purple-800', badge: 'bg-purple-100' },
        { bg: 'bg-orange-50', border: 'border-orange-200', textBold: 'text-orange-600', textHead: 'text-orange-800', badge: 'bg-orange-100' },
        { bg: 'bg-teal-50', border: 'border-teal-200', textBold: 'text-teal-600', textHead: 'text-teal-800', badge: 'bg-teal-100' },
        { bg: 'bg-indigo-50', border: 'border-indigo-200', textBold: 'text-primary', textHead: 'text-indigo-800', badge: 'bg-indigo-100' },
        { bg: 'bg-pink-50', border: 'border-pink-200', textBold: 'text-pink-600', textHead: 'text-pink-800', badge: 'bg-pink-100' },
        { bg: 'bg-cyan-50', border: 'border-cyan-200', textBold: 'text-cyan-600', textHead: 'text-cyan-800', badge: 'bg-cyan-100' },
        { bg: 'bg-amber-50', border: 'border-amber-200', textBold: 'text-amber-600', textHead: 'text-amber-800', badge: 'bg-amber-100' },
    ];

    safeHTML(grid, domains.map(function(domain, index) {
        let cs = domainColorSets[index % domainColorSets.length];
        let coverageColor = domain.coverage >= 70 ? 'text-emerald-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-destructive';
        let barColor = domain.coverage >= 70 ? 'bg-emerald-500' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-destructive';
        let statusColor = domain.coverage >= 70 ? 'bg-green-400' : domain.coverage >= 40 ? 'bg-amber-400' : 'bg-red-400';

        return '<div class="' + cs.bg + ' border-2 ' + cs.border + ' rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"' +
                     ' role="button" tabindex="0" data-acm-action="filter-unified-domain"' +
                     ' data-domain-code="' + acmEscapeAttr(domain.code) + '"' +
                     ' title="Filter capabilities to domain ' + acmEscapeAttr(domain.code) + '">' +
                '<div class="flex items-start justify-between mb-3">' +
                    '<span class="text-xs font-bold ' + cs.textBold + ' ' + cs.badge + ' px-2 py-0.5 rounded">' + escapeHtml(domain.code) + '</span>' +
                    '<div class="w-2 h-2 rounded-full ' + statusColor + '"></div>' +
                '</div>' +
                '<h4 class="text-sm font-semibold ' + cs.textHead + ' mb-1">' + escapeHtml(domain.name) + '</h4>' +
                '<p class="text-xs text-muted-foreground mb-3 line-clamp-2">' + escapeHtml(domain.description || '') + '</p>' +
                '<div class="flex justify-between text-xs text-muted-foreground">' +
                    '<span>' + (domain.capability_count || 0) + ' capabilities</span>' +
                    '<span class="font-medium ' + coverageColor + '">' + (domain.coverage || 0) + '%</span>' +
                '</div>' +
                '<div class="mt-2 w-full bg-border rounded-full h-1.5">' +
                    '<div class="' + barColor + ' h-1.5 rounded-full transition-all" style="width: ' + (domain.coverage || 0) + '%"></div>' +
                '</div>' +
                '<div class="mt-2 text-xs text-muted-foreground">' +
                    'L1: ' + (domain.l1_count || 0) + ' | L2: ' + (domain.l2_count || 0) + ' | L3: ' + (domain.l3_count || 0) +
                '</div>' +
            '</div>';
    }).join(''));
}

// =============================================================================
// Manufacturing Domain Stats Functions
// =============================================================================

async function loadManufacturingDomainStats() {
    try {
        let data = await Platform.fetch('/capability-map/api/manufacturing/domains', { silent: true });

        if (data.success) {
            // Update statistics
            document.getElementById('mfg-cap-count').textContent = data.total_capabilities || 0;
            document.getElementById('mfg-mapped-count').textContent = data.mapped_count || 0;
            document.getElementById('mfg-coverage').textContent = (data.coverage || 0) + '%';
            document.getElementById('mfg-avg-oee').textContent = (data.avg_oee || 0) + '%';

            // Update domain cards
            let domains = data.domains || {};
            updateMfgDomainCard('prod', domains.production);
            updateMfgDomainCard('sc', domains.supply_chain);
            updateMfgDomainCard('qual', domains.quality);
            updateMfgDomainCard('maint', domains.maintenance);
            updateMfgDomainCard('eng', domains.engineering);
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        if (window.Platform && Platform.toast) Platform.toast.error('Failed to load manufacturing domains');
    }
}

function updateMfgDomainCard(prefix, domainData) {
    if (!domainData) domainData = { count: 0, coverage: 0 };
    let countEl = document.getElementById('mfg-' + prefix + '-count');
    let pctEl = document.getElementById('mfg-' + prefix + '-pct');
    let barEl = document.getElementById('mfg-' + prefix + '-bar');
    let statusEl = document.getElementById('mfg-' + prefix + '-status');

    if (countEl) countEl.textContent = (domainData.count || 0) + ' capabilities';
    if (pctEl) pctEl.textContent = (domainData.coverage || 0) + '%';
    if (barEl) barEl.style.width = (domainData.coverage || 0) + '%';
    if (statusEl) {
        statusEl.className = 'w-3 h-3 rounded-full ' + (domainData.coverage >= 70 ? 'bg-green-400' : domainData.coverage >= 40 ? 'bg-amber-400' : 'bg-muted/70');
    }
}

// =============================================================================
// Process Category Stats Functions
// =============================================================================

async function loadProcessCategoryStats() {
    try {
        let data = await Platform.fetch('/capability-map/api/process/categories', { silent: true });

        if (data.success) {
            let categories = data.categories || {};
            for (let i = 1; i <= 13; i++) {
                let cat = categories[i + '.0'] || { count: 0, coverage: 0 };
                let countEl = document.getElementById('proc-cat-' + i + '-count');
                let pctEl = document.getElementById('proc-cat-' + i + '-pct');
                let statusEl = document.getElementById('proc-cat-' + i + '-status');

                if (countEl) countEl.textContent = (cat.count || 0);
                if (pctEl) pctEl.textContent = (cat.coverage || 0) + '%';
                if (statusEl) {
                    statusEl.className = 'w-2 h-2 rounded-full ' + (cat.coverage >= 70 ? 'bg-green-400' : cat.coverage >= 40 ? 'bg-amber-400' : 'bg-muted/70');
                }
            }
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        if (window.Platform && Platform.toast) Platform.toast.error('Failed to load process categories');
    }
}

function filterProcessByCategory(category) {
    document.getElementById('process-category-filter').value = category;
    filterProcessGapTable();
}

// =============================================================================
// ACM Gap Analysis Functions (within Gap Analysis Tab)
// =============================================================================

let acmGapData = [];
let acmGapDomainStats = [];

async function loadACMGapAnalysis() {
    try {
        let data = await Platform.fetch('/capability-map/api/acm/gap-analysis', { silent: true });

        if (data.success) {
            acmGapData = data.capabilities || [];
            acmGapDomainStats = data.domain_gaps || [];

            // Update statistics
            let stats = data.statistics || {};
            document.getElementById('acm-gap-total').textContent = stats.total || 0;
            document.getElementById('acm-gap-unmapped').textContent = stats.unmapped || 0;
            document.getElementById('acm-gap-partial').textContent = stats.partial || 0;
            document.getElementById('acm-gap-coverage-pct').textContent = (stats.coverage_rate || 0).toFixed(1) + '%';

            // Render domain coverage grid
            renderACMGapDomainCoverage(acmGapDomainStats);

            // Render gap table
            renderACMGapTable(acmGapData);
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        safeHTML(document.getElementById('acm-gap-table-body'),
            '<tr>' +
                '<td colspan="6" class="px-6 py-8 text-center text-destructive">' +
                    '<i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>' +
                    '<p>Error loading ACM gap analysis. Please try again.</p>' +
                '</td>' +
            '</tr>');
    }
}

function renderACMGapDomainCoverage(domainStats) {
    let grid = document.getElementById('acm-domain-coverage-grid');

    let domainColors = {
        'USER-EXPERIENCE': { bg: 'bg-primary/10', text: 'text-primary', short: 'UX' },
        'APPLICATION-SERVICES': { bg: 'bg-emerald-500/10', text: 'text-emerald-700', short: 'APP' },
        'DATA-STORAGE': { bg: 'bg-purple-100', text: 'text-purple-700', short: 'DATA' },
        'SECURITY-IDENTITY': { bg: 'bg-destructive/10', text: 'text-destructive', short: 'SEC' },
        'DEVOPS-PLATFORM': { bg: 'bg-orange-100', text: 'text-orange-700', short: 'OPS' },
        'AI-ANALYTICS': { bg: 'bg-indigo-100', text: 'text-indigo-700', short: 'AI' },
        'COMMUNICATION': { bg: 'bg-teal-100', text: 'text-teal-700', short: 'COM' },
    };

    if (!domainStats || domainStats.length === 0) {
        safeHTML(grid, '<div class="text-center text-muted-foreground col-span-7">No domain coverage data available.</div>');
        return;
    }

    safeHTML(grid, domainStats.map(function(domain) {
        let colors = domainColors[domain.domain] || { bg: 'bg-muted', text: 'text-muted-foreground', short: '?' };
        let coverageColor = domain.coverage >= 70 ? 'text-emerald-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-destructive';
        let barColor = domain.coverage >= 70 ? 'bg-emerald-500' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-destructive';

        return '<div class="' + colors.bg + ' rounded-lg p-3 text-center cursor-pointer hover:shadow-md transition-shadow"' +
                     ' role="button" tabindex="0" data-acm-action="filter-acm-gap-domain"' +
                     ' data-domain="' + acmEscapeAttr(domain.domain) + '"' +
                     ' aria-label="Filter gaps to domain ' + acmEscapeAttr(domain.domain) + '">' +
                '<div class="font-semibold ' + colors.text + ' text-sm mb-1">' + colors.short + '</div>' +
                '<div class="' + coverageColor + ' font-bold text-lg">' + domain.coverage + '%</div>' +
                '<div class="w-full bg-border rounded-full h-1 mt-2">' +
                    '<div class="' + barColor + ' h-1 rounded-full" style="width: ' + domain.coverage + '%"></div>' +
                '</div>' +
                '<div class="text-xs text-muted-foreground mt-1">' + domain.total + ' caps</div>' +
                '<div class="text-xs ' + (domain.unmapped > 0 ? 'text-destructive' : 'text-emerald-500') + '">' + domain.unmapped + ' gaps</div>' +
            '</div>';
    }).join(''));
}

function renderACMGapTable(capabilities) {
    let tbody = document.getElementById('acm-gap-table-body');
    let showingEl = document.getElementById('acm-gap-showing');

    if (!capabilities || capabilities.length === 0) {
        safeHTML(tbody,
            '<tr>' +
                '<td colspan="6" class="px-6 py-12 text-center text-muted-foreground">' +
                    '<i data-lucide="check-circle" class="w-8 h-8 mx-auto mb-2 text-emerald-500"></i>' +
                    '<p>No gaps found matching current filters.</p>' +
                '</td>' +
            '</tr>');
        showingEl.textContent = '0';
        return;
    }

    showingEl.textContent = capabilities.length;

    safeHTML(tbody, capabilities.map(function(cap) {
        let domainBadge = getACMDomainBadge(cap.acm_domain);
        let levelBadge = getACMLevelBadge(cap.level);

        let statusBadge;
        if (cap.applications_count === 0) {
            statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-destructive/10 text-red-800">Gap</span>';
        } else if (cap.applications_count < 3) {
            statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-amber-100 text-amber-800">Partial</span>';
        } else {
            statusBadge = '<span class="px-2 py-1 text-xs font-medium rounded-full bg-emerald-500/10 text-green-800">Covered</span>';
        }

        return '<tr class="hover:bg-accent">' +
                '<td class="px-6 py-4">' +
                    '<div class="flex items-start">' +
                        '<div>' +
                            '<div class="text-sm font-medium text-foreground">' + escapeHtml(cap.name) + '</div>' +
                            '<div class="text-xs text-muted-foreground font-mono">' + escapeHtml(cap.code || '') + '</div>' +
                        '</div>' +
                    '</div>' +
                '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' + domainBadge + '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' + levelBadge + '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' +
                    '<span class="text-sm ' + (cap.applications_count > 0 ? 'text-foreground' : 'text-destructive font-medium') + '">' + cap.applications_count + '</span>' +
                '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' +
                    '<span class="text-sm ' + (cap.vendors_count > 0 ? 'text-foreground' : 'text-muted-foreground') + '">' + (cap.vendors_count || 0) + '</span>' +
                '</td>' +
                '<td class="px-6 py-4 whitespace-nowrap">' + statusBadge + '</td>' +
            '</tr>';
    }).join(''));
}

function filterACMGapTable() {
    let domainFilter = document.getElementById('acm-gap-domain-filter').value;
    let levelFilter = document.getElementById('acm-gap-level-filter').value;
    let statusFilter = document.getElementById('acm-gap-status-filter').value;
    let searchFilter = document.getElementById('acm-gap-search-filter').value.toLowerCase();

    let filtered = acmGapData;

    if (domainFilter) {
        filtered = filtered.filter(function(c) { return c.acm_domain === domainFilter; });
    }
    if (levelFilter) {
        filtered = filtered.filter(function(c) { return c.level === levelFilter; });
    }
    if (statusFilter) {
        if (statusFilter === 'gap') {
            filtered = filtered.filter(function(c) { return c.applications_count === 0; });
        } else if (statusFilter === 'partial') {
            filtered = filtered.filter(function(c) { return c.applications_count > 0 && c.applications_count < 3; });
        } else if (statusFilter === 'covered') {
            filtered = filtered.filter(function(c) { return c.applications_count >= 3; });
        }
    }
    if (searchFilter) {
        filtered = filtered.filter(function(c) {
            return (c.name && c.name.toLowerCase().includes(searchFilter)) ||
                (c.code && c.code.toLowerCase().includes(searchFilter));
        });
    }

    renderACMGapTable(filtered);
    if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
}

function clearACMGapFilters() {
    document.getElementById('acm-gap-domain-filter').value = '';
    document.getElementById('acm-gap-level-filter').value = '';
    document.getElementById('acm-gap-status-filter').value = '';
    document.getElementById('acm-gap-search-filter').value = '';
    renderACMGapTable(acmGapData);
    if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
}


// ============================================================================
// Delegated action dispatcher
// ============================================================================
//
// The app ships script-src 'self' 'nonce-...' 'strict-dynamic' with neither
// 'unsafe-inline' nor 'unsafe-hashes', so an on*= attribute is refused no
// matter how it reached the DOM -- innerHTML included. Every control rendered
// by this file therefore did nothing at all for its entire life. (safeHTML's
// DOMPurify config also lists onclick in FORBID_ATTR, so most were stripped
// before the CSP even got a say.)
//
// The controls now carry data-acm-action plus data-* arguments and are
// dispatched by a single document-level listener. Delegation, not per-node
// binding: these grids are re-rendered wholesale from fetched data, and a
// listener bound to a node dies with the next render.
//
// The attribute is deliberately NOT the data-cm-action used by index.js --
// both files load on the same page, and separate namespaces keep each
// dispatcher blind to the other's controls.

// escapeHtml() round-trips through textContent/innerHTML, which escapes < > &
// but NOT the double quote -- safe for a text node, unsafe for an attribute.
function acmEscapeAttr(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function acmSetFilterAndApply(selectId, value, apply) {
    const select = document.getElementById(selectId);
    if (!select) return;
    select.value = value;
    apply();
}

const ACM_ACTIONS = {
    'filter-acm-domain':      (el) => filterACMByDomain(el.dataset.domain),
    'open-acm-mapping':       (el) => openACMMappingModal(
                                        el.dataset.capId, el.dataset.capName,
                                        el.dataset.domain, el.dataset.level),
    'open-acm-detail':        (el) => openACMCapabilityDetail(el.dataset.capId, el.dataset.capName),
    // addToRoadmap is defined inside index.js's IIFE and exported onto window;
    // resolved at click time, so load order does not matter.
    'add-to-roadmap':         (el) => addToRoadmap(el.dataset.capId, el.dataset.capName,
                                        'technical', parseInt(el.dataset.capLevel, 10) || 1, 'medium'),
    'close-capability-detail':()   => closeCapabilityDetail(),
    'delete-acm-mapping':     (el) => deleteACMMapping(el.dataset.mappingId, el.dataset.appId),
    'filter-unified-domain':  (el) => acmSetFilterAndApply(
                                        'unified-domain-filter', el.dataset.domainCode,
                                        () => filterTable('unified')),
    'filter-acm-gap-domain':  (el) => acmSetFilterAndApply(
                                        'acm-gap-domain-filter', el.dataset.domain,
                                        filterACMGapTable),
};

function runAcmAction(event) {
    const el = event.target.closest('[data-acm-action]');
    if (!el) return;
    if (el.disabled) return;
    const handler = ACM_ACTIONS[el.getAttribute('data-acm-action')];
    if (!handler) return;
    handler(el, event);
}

document.addEventListener('click', runAcmAction);
// The domain cards are <div role="button"> (they are card layouts, not
// buttons), so keyboard activation has to be wired explicitly.
document.addEventListener('keydown', function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const el = event.target.closest('[data-acm-action][role="button"]');
    if (!el) return;
    event.preventDefault();
    runAcmAction(event);
});
