/**
 * Capability Roadmap - External JavaScript
 * Extracted from capability_roadmap.html inline scripts
 * Depends on: window.__APP_CONFIG__ (injected by template)
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

// Close modal when clicking outside
document.addEventListener('DOMContentLoaded', function() {
    let modal = document.getElementById('mapping-modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                closeMappingModal();
            }
        });
    }
});

// ============================================================================
// Capability Mapping to APQC Processes and ArchiMate Elements
// ============================================================================

function openCapabilityAPQCMapping(capabilityId, capabilityName) {
    // Open APQC mapping modal
    let modal = Alpine.$data(document.querySelector('#apqc-mapping-modal'));
    if (modal && modal.openMappingModal) {
        modal.openMappingModal(capabilityId, capabilityName);
    } else {
        // `toast` is not a global anywhere in this app, so this branch used to
        // report nothing at all: the button simply did nothing.
        if (window.Platform && Platform.toast) {
            Platform.toast.error('APQC mapping is not available — please refresh the page.');
        }
    }
}

function openCapabilityArchimateMapping(capabilityId, capabilityName) {
    // Open ArchiMate mapping modal
    let modal = Alpine.$data(document.querySelector('#archimate-mapping-modal'));
    if (modal && modal.openMappingModal) {
        modal.openMappingModal(capabilityId, capabilityName);
    } else {
        // `toast` is not a global anywhere in this app, so this branch used to
        // report nothing at all: the button simply did nothing.
        if (window.Platform && Platform.toast) {
            Platform.toast.error('ArchiMate mapping is not available — please refresh the page.');
        }
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
        safeHTML(document.getElementById('acm-domains-grid'), '\
            <div class="col-span-full text-center text-destructive py-8">\
                <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>\
                <p>Error loading ACM data. Please try again.</p>\
            </div>\
        ');
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
        if (window.Platform && Platform.toast) Platform.toast.error('Could not load applications for mapping.');
    }
}

function populateACMApplicationsDropdown() {
    let select = document.getElementById('acm-mapping-application');
    if (!select) return;

    safeHTML(select, '<option value="">Choose an application...</option>' +
        acmApplicationsList.map(function(app) { return '<option value="' + app.id + '">' + app.name + '</option>'; }).join(''));
}

function renderACMDomains(data) {
    let grid = document.getElementById('acm-domains-grid');
    let stats = data.statistics || {};

    // Update stats
    document.getElementById('acm-domain-count').textContent = stats.total_domains || 7;
    document.getElementById('acm-cap-count').textContent = stats.total_capabilities || 0;
    document.getElementById('acm-mapped-count').textContent = stats.mapped_capabilities || 0;
    document.getElementById('acm-coverage').textContent = Platform.figure.percent(stats.overall_coverage);

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
        let colors = domainColors[domain.domain] || { bg: 'bg-muted/30', border: 'border-border', text: 'text-foreground', icon: 'layers' };
        let statusColor = domain.status === 'covered' ? 'bg-emerald-500' : domain.status === 'partial' ? 'bg-amber-500' : 'bg-destructive';

        return '\
            <div class="' + colors.bg + ' ' + colors.border + ' border-2 rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"\
                 data-action="filterACMByDomain" data-id="' + domain.domain + '">\
                <div class="flex items-start justify-between mb-3">\
                    <div class="p-2 rounded-lg ' + colors.bg + '">\
                        <i data-lucide="' + colors.icon + '" class="w-6 h-6 ' + colors.text + '"></i>\
                    </div>\
                    <div class="w-3 h-3 rounded-full ' + statusColor + '" title="' + domain.coverage_percentage + '% coverage"></div>\
                </div>\
                <h3 class="font-semibold ' + colors.text + ' mb-1">' + domain.name + '</h3>\
                <p class="text-xs text-muted-foreground mb-3 line-clamp-2">' + domain.description + '</p>\
                <div class="flex items-center justify-between text-sm">\
                    <span class="text-muted-foreground">' + domain.total_capabilities + ' capabilities</span>\
                    <span class="' + colors.text + ' font-medium">' + domain.coverage_percentage + '%</span>\
                </div>\
                <div class="mt-2 w-full bg-muted rounded-full h-1.5">\
                    <div class="' + statusColor + ' h-1.5 rounded-full transition-all" style="width: ' + domain.coverage_percentage + '%"></div>\
                </div>\
                <div class="mt-2 flex gap-2 text-xs text-muted-foreground">\
                    <span>L1: ' + domain.by_level.L1 + '</span>\
                    <span>L2: ' + domain.by_level.L2 + '</span>\
                    <span>L3: ' + domain.by_level.L3 + '</span>\
                </div>\
            </div>\
        ';
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
        safeHTML(tbody, '\
            <tr>\
                <td colspan="7" class="px-6 py-12 text-center text-muted-foreground">\
                    No capabilities found matching filters.\
                </td>\
            </tr>\
        ');
        return;
    }

    safeHTML(tbody, capabilities.map(function(cap) {
        let domainBadge = getACMDomainBadge(cap.acm_domain);
        let levelBadge = getACMLevelBadge(cap.level);
        let statusBadge = cap.status === 'mapped'
            ? '<span class="px-2 py-1 text-xs font-medium rounded-full bg-emerald-500/10 text-green-800">Mapped</span>'
            : '<span class="px-2 py-1 text-xs font-medium rounded-full bg-destructive/10 text-red-800">Gap</span>';

        let escapedName = (cap.name || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');

        let roadmapBadge = cap.on_roadmap
            ? '<span class="px-1.5 py-0.5 text-xs rounded bg-purple-100 text-purple-800 ml-1" title="On Roadmap"><i data-lucide="map" class="w-3 h-3 inline"></i></span>'
            : '';

        return '\
            <tr class="hover:bg-muted/30">\
                <td class="px-6 py-4 whitespace-nowrap text-sm font-mono text-muted-foreground">' + (cap.code || '-') + '</td>\
                <td class="px-6 py-4">\
                    <div class="text-sm font-medium text-foreground">' + cap.name + '</div>\
                    <div class="text-xs text-muted-foreground truncate max-w-xs">' + (cap.description || '') + '</div>\
                </td>\
                <td class="px-6 py-4 whitespace-nowrap">' + domainBadge + '</td>\
                <td class="px-6 py-4 whitespace-nowrap">' + levelBadge + '</td>\
                <td class="px-6 py-4 whitespace-nowrap text-sm text-muted-foreground">' + cap.applications_count + '</td>\
                <td class="px-6 py-4 whitespace-nowrap">\
                    <div class="flex items-center gap-1">\
                        ' + statusBadge + roadmapBadge + '\
                    </div>\
                </td>\
                <td class="px-6 py-4 whitespace-nowrap">\
                    <div class="flex items-center gap-2">\
                        <button data-action="openACMMappingModal" data-params=\'[' + cap.id + ', "' + escapedName + '", "' + cap.acm_domain + '", "' + cap.level + '"]\'\
                                class="text-cyan-600 hover:text-cyan-800 text-sm">\
                            Map\
                        </button>\
                        <button data-action="openACMCapabilityDetail" data-params=\'[' + cap.id + ', "' + escapedName + '"]\'\
                                class="text-muted-foreground hover:text-foreground text-sm">\
                            View\
                        </button>\
                        <button data-action="addToRoadmap" data-params=\'[' + cap.id + ', "' + escapedName + '", "technical", ' + (cap.level_number || 1) + ', "medium"]\'\
                                class="text-primary hover:text-purple-800 text-sm ' + (cap.on_roadmap ? 'opacity-50 cursor-not-allowed' : '') + '"\
                                ' + (cap.on_roadmap ? 'disabled title="Already on roadmap"' : 'title="Add to roadmap"') + '>\
                            <i data-lucide="map" class="w-4 h-4 inline"></i>\
                        </button>\
                    </div>\
                </td>\
            </tr>\
        ';
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

function filterByUnifiedDomain(code) {
    document.getElementById('unified-domain-filter').value = code;
    filterTable('unified');
}

function filterByACMGapDomain(domain) {
    document.getElementById('acm-gap-domain-filter').value = domain;
    filterACMGapTable();
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
    // Open detail modal or navigate to detail page
    Platform.toast.info('View details for: ' + capabilityName + ' (ID: ' + capabilityId + ')\n\nFull detail view coming soon.');
}

// =============================================================================
// ACM Mapping Modal Functions (Consistent with other tabs)
// =============================================================================

let acmCurrentCapabilityId = null;
let acmCurrentCapabilityName = '';
let acmModalApplicationsData = [];
let acmSelectedApplications = new Map();

function openACMMappingModal(capabilityId, capabilityName, domain, level) {
    acmCurrentCapabilityId = String(capabilityId);
    acmCurrentCapabilityName = capabilityName;
    acmSelectedApplications.clear();

    // Update modal title
    let modalNameEl = document.getElementById('acm-modal-capability-name');
    if (modalNameEl) {
        modalNameEl.textContent = capabilityName;
    }

    // Show modal
    let modal = document.getElementById('acm-mapping-modal');
    if (modal) {
        Platform.modal.open('acm-mapping-modal');
        document.body.classList.add('overflow-hidden');
    }

    // Load applications for this technical capability
    loadACMApplicationsForCapability(capabilityId);
}

function closeACMMappingModal() {
    let modal = document.getElementById('acm-mapping-modal');
    if (modal) {
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
        let types = Array.from(new Set(acmModalApplicationsData.map(function(app) { return app.type; }).filter(Boolean))).sort();
        let currentValue = typeFilter.value;
        safeHTML(typeFilter, '<option value="">All Types</option>' +
            types.map(function(type) { return '<option value="' + type + '">' + type + '</option>'; }).join(''));
        if (currentValue) typeFilter.value = currentValue;
    }

    let domainFilter = document.getElementById('acm-filter-domain');
    if (domainFilter && acmModalApplicationsData.length > 0) {
        let domains = Array.from(new Set(acmModalApplicationsData.map(function(app) { return app.domain; }).filter(Boolean))).sort();
        let currentValue = domainFilter.value;
        safeHTML(domainFilter, '<option value="">All Domains</option>' +
            domains.map(function(domain) { return '<option value="' + domain + '">' + domain + '</option>'; }).join(''));
        if (currentValue) domainFilter.value = currentValue;
    }
}

function filterACMApplications() {
    renderACMApplicationsList();
}

function renderACMApplicationsList() {
    let container = document.getElementById('acm-applications-list');
    let searchTerm = (document.getElementById('acm-application-search') || {}).value;
    searchTerm = searchTerm ? searchTerm.toLowerCase() : '';
    let filterType = (document.getElementById('acm-filter-type') || {}).value || '';
    let filterDomain = (document.getElementById('acm-filter-domain') || {}).value || '';
    let filterStatus = (document.getElementById('acm-filter-status') || {}).value || 'all';
    let sortBy = (document.getElementById('acm-sort-applications') || {}).value || 'name-asc';

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
        safeHTML(container, '\
            <div class="text-center py-12 text-muted-foreground">\
                <i data-lucide="search-x" class="w-12 h-12 mx-auto mb-3 text-muted-foreground/60"></i>\
                <p class="text-lg font-medium">No applications found</p>\
                <p class="text-sm mt-1">Try adjusting your search or filters</p>\
            </div>\
        ');
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    safeHTML(container, filtered.map(function(app) {
        let isSelected = acmSelectedApplications.has(app.id);
        let mapping = acmSelectedApplications.get(app.id);

        return '\
            <div class="border rounded-lg p-4 transition-all ' + (isSelected ? 'bg-cyan-50 border-cyan-300 shadow-sm' : 'bg-background border-border hover:border-border hover:shadow-sm') + '">\
                <div class="flex items-start justify-between">\
                    <div class="flex items-start space-x-3 flex-1">\
                        <input\
                            type="checkbox"\
                            ' + (isSelected ? 'checked' : '') + '\
                            onchange="toggleACMApplicationSelection(\'' + app.id + '\')"\
                            class="mt-1 h-5 w-5 text-cyan-600 focus:ring-cyan-500 border-border rounded cursor-pointer"\
                            title="' + (isSelected ? 'Deselect' : 'Select') + ' ' + app.name + '"\
                        />\
                        <div class="flex-1">\
                            <div class="flex items-center space-x-2">\
                                <div class="font-medium text-foreground">' + (app.name || 'Unknown') + '</div>\
                                ' + (app.is_mapped ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-emerald-500/10 text-green-800 rounded-full"><i data-lucide="check-circle" class="w-3 h-3 mr-1"></i>Mapped</span>' : '') + '\
                            </div>\
                            <div class="flex items-center space-x-2 mt-1 text-sm text-muted-foreground">\
                                <span class="flex items-center">\
                                    <i data-lucide="layers" class="w-3 h-3 mr-1"></i>\
                                    ' + (app.type || 'Unknown') + '\
                                </span>\
                                <span>&bull;</span>\
                                <span class="flex items-center">\
                                    <i data-lucide="building" class="w-3 h-3 mr-1"></i>\
                                    ' + (app.domain || 'Not specified') + '\
                                </span>\
                            </div>\
                            ' + (app.description ? '<div class="text-xs text-muted-foreground/60 mt-2 line-clamp-2">' + app.description + '</div>' : '') + '\
                        </div>\
                    </div>\
                    ' + (app.is_mapped && app.mapping_id ? '\
                        <button\
                            data-action="deleteACMMapping" data-params=\'["' + app.mapping_id + '", "' + app.id + '"]\'\
                            class="ml-2 px-3 py-1.5 text-xs bg-destructive text-primary-foreground rounded hover:bg-destructive transition-colors flex items-center space-x-1"\
                            title="Remove mapping"\
                        >\
                            <i data-lucide="trash-2" class="w-3 h-3"></i>\
                            <span>Remove</span>\
                        </button>\
                    ' : '') + '\
                </div>\
                ' + (isSelected ? renderACMApplicationSettings(app.id, mapping) : '') + '\
            </div>\
        ';
    }).join(''));

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderACMApplicationSettings(appId, mapping) {
    let mappingData = (mapping && mapping.mapping) ? mapping.mapping : {};
    return '\
        <div class="mt-4 pt-4 border-t border-border space-y-3">\
            <div class="grid grid-cols-3 gap-4">\
                <div>\
                    <label class="block text-xs font-medium text-foreground mb-1">Coverage Level</label>\
                    <select\
                        class="w-full text-sm border border-border rounded px-2 py-1"\
                        onchange="updateACMApplicationMapping(\'' + appId + '\', \'capability_coverage\', this.value)"\
                    >\
                        <option value="full" ' + (mappingData.capability_coverage === 'full' ? 'selected' : '') + '>Full</option>\
                        <option value="partial" ' + (mappingData.capability_coverage === 'partial' || !mappingData.capability_coverage ? 'selected' : '') + '>Partial</option>\
                        <option value="minimal" ' + (mappingData.capability_coverage === 'minimal' ? 'selected' : '') + '>Minimal</option>\
                    </select>\
                </div>\
                <div>\
                    <label class="block text-xs font-medium text-foreground mb-1">Maturity Level</label>\
                    <select\
                        class="w-full text-sm border border-border rounded px-2 py-1"\
                        onchange="updateACMApplicationMapping(\'' + appId + '\', \'maturity_level\', this.value)"\
                    >\
                        <option value="initial" ' + (mappingData.maturity_level === 'initial' ? 'selected' : '') + '>Initial</option>\
                        <option value="developing" ' + (mappingData.maturity_level === 'developing' ? 'selected' : '') + '>Developing</option>\
                        <option value="defined" ' + (mappingData.maturity_level === 'defined' || !mappingData.maturity_level ? 'selected' : '') + '>Defined</option>\
                        <option value="managed" ' + (mappingData.maturity_level === 'managed' ? 'selected' : '') + '>Managed</option>\
                        <option value="optimized" ' + (mappingData.maturity_level === 'optimized' ? 'selected' : '') + '>Optimized</option>\
                    </select>\
                </div>\
                <div>\
                    <label class="block text-xs font-medium text-foreground mb-1">Notes</label>\
                    <input\
                        type="text"\
                        value="' + (mappingData.notes || '') + '"\
                        placeholder="Optional notes..."\
                        class="w-full text-sm border border-border rounded px-2 py-1"\
                        onchange="updateACMApplicationMapping(\'' + appId + '\', \'notes\', this.value)"\
                    />\
                </div>\
            </div>\
        </div>\
    ';
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
    if (typeof type === 'undefined') type = 'info';
    let toast = document.createElement('div');
    toast.className = 'fixed bottom-4 right-4 z-50 px-6 py-3 rounded-lg shadow-lg text-primary-foreground ' + (type === 'success' ? 'bg-emerald-600' : type === 'error' ? 'bg-destructive' : 'bg-cyan-600');
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(function() {
        toast.remove();
    }, 3000);
}

// =============================================================================
// Process Mapping Modal Functions (99.99% copy from ACM)
// =============================================================================

let processCurrentProcessId = null;
let processCurrentProcessName = '';
let processModalApplicationsData = [];
let processSelectedApplications = new Map();

function openProcessMappingModal(processId, processName, processCode, processType) {
    // Check if user is authenticated before opening modal
    Platform.fetch('/capability-map/api/check-auth', { silent: true })
        .then(function(data) {
            if (data.authenticated) {
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
                let modal = document.getElementById('process-mapping-modal');
                if (modal) {
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
    let modal = document.getElementById('process-mapping-modal');
    if (modal) {
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
        let types = Array.from(new Set(processModalApplicationsData.map(function(app) { return app.type; }).filter(Boolean))).sort();
        safeHTML(typeFilter, '<option value="">All Types</option>' +
            types.map(function(type) { return '<option value="' + type + '">' + type + '</option>'; }).join(''));
    }

    let domainFilter = document.getElementById('process-filter-domain');
    if (domainFilter && processModalApplicationsData.length > 0) {
        let domains = Array.from(new Set(processModalApplicationsData.map(function(app) { return app.domain; }).filter(Boolean))).sort();
        safeHTML(domainFilter, '<option value="">All Domains</option>' +
            domains.map(function(domain) { return '<option value="' + domain + '">' + domain + '</option>'; }).join(''));
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

    return '\
        <div class="border rounded-lg p-4 ' + (isSelected ? 'border-primary bg-primary/5' : 'border-border') + '">\
            <div class="flex items-start justify-between">\
                <div class="flex items-start space-x-3 flex-1">\
                    <input type="checkbox"\
                           id="process-app-' + app.id + '"\
                           ' + (isSelected ? 'checked' : '') + '\
                           onchange="toggleProcessApplicationSelection(' + app.id + ')"\
                           class="mt-1 rounded border-border text-primary focus:ring-primary">\
                    <div class="flex-1">\
                        <label for="process-app-' + app.id + '" class="font-medium text-foreground cursor-pointer">\
                            ' + app.name + '\
                        </label>\
                        <div class="text-sm text-muted-foreground mt-1">\
                            ' + app.type + ' ' + (app.domain ? '&bull; ' + app.domain : '') + '\
                        </div>\
                        ' + (app.description ? '<div class="text-sm text-muted-foreground mt-2">' + app.description + '</div>' : '') + '\
                    </div>\
                </div>\
                <div class="text-right">\
                    <span class="px-2 py-1 text-xs rounded-full ' + (app.status === 'active' ? 'bg-emerald-500/10 text-green-800' : 'bg-muted text-foreground') + '">\
                        ' + app.status + '\
                    </span>\
                </div>\
            </div>\
            ' + (isSelected ? '\
                <div class="mt-4 pt-4 border-t border-border">\
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">\
                        <div>\
                            <label class="block text-sm font-medium text-foreground mb-1">Support Level</label>\
                            <select id="process-support-' + app.id + '" class="w-full px-3 py-2 text-sm border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-primary">\
                                <option value="full" ' + ((mapping && mapping.mapping && mapping.mapping.support_level === 'full') ? 'selected' : '') + '>Full Support</option>\
                                <option value="partial" ' + ((mapping && mapping.mapping && mapping.mapping.support_level === 'partial') ? 'selected' : '') + '>Partial Support</option>\
                                <option value="minimal" ' + ((mapping && mapping.mapping && mapping.mapping.support_level === 'minimal') ? 'selected' : '') + '>Minimal Support</option>\
                            </select>\
                        </div>\
                        <div>\
                            <label class="block text-sm font-medium text-foreground mb-1">Automation Level</label>\
                            <select id="process-automation-' + app.id + '" class="w-full px-3 py-2 text-sm border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-primary">\
                                <option value="1" ' + ((mapping && mapping.mapping && mapping.mapping.automation_level === 1) ? 'selected' : '') + '>Manual</option>\
                                <option value="2" ' + ((mapping && mapping.mapping && mapping.mapping.automation_level === 2) ? 'selected' : '') + '>Basic Automation</option>\
                                <option value="3" ' + ((mapping && mapping.mapping && mapping.mapping.automation_level === 3) ? 'selected' : '') + '>Moderate Automation</option>\
                                <option value="4" ' + ((mapping && mapping.mapping && mapping.mapping.automation_level === 4) ? 'selected' : '') + '>High Automation</option>\
                                <option value="5" ' + ((mapping && mapping.mapping && mapping.mapping.automation_level === 5) ? 'selected' : '') + '>Full Automation</option>\
                            </select>\
                        </div>\
                        <div>\
                            <label class="block text-sm font-medium text-foreground mb-1">Notes</label>\
                            <input type="text" id="process-notes-' + app.id + '" value="' + ((mapping && mapping.mapping && mapping.mapping.notes) ? mapping.mapping.notes : '') + '"\
                                   placeholder="Add notes..."\
                                   class="w-full px-3 py-2 text-sm border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-primary">\
                        </div>\
                    </div>\
                </div>\
            ' : '') + '\
        </div>\
    ';
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
    let searchTerm = (document.getElementById('process-application-search') || {}).value;
    searchTerm = searchTerm ? searchTerm.toLowerCase() : '';
    let typeFilter = (document.getElementById('process-filter-type') || {}).value || '';
    let domainFilter = (document.getElementById('process-filter-domain') || {}).value || '';
    let statusFilter = (document.getElementById('process-filter-status') || {}).value || '';

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
    let sortValue = (document.getElementById('process-sort-applications') || {}).value || 'name-asc';

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
            let supportLevel = (document.getElementById('process-support-' + applicationId) || {}).value || 'partial';
            let automationLevel = (document.getElementById('process-automation-' + applicationId) || {}).value || 1;
            let notes = (document.getElementById('process-notes-' + applicationId) || {}).value || '';

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
    if (typeof type === 'undefined') type = 'info';
    // Simple notification - could be enhanced with a toast library
    let notification = document.createElement('div');
    notification.className = 'fixed top-4 right-4 p-4 rounded-md shadow-lg z-50 ' + (
        type === 'success' ? 'bg-emerald-500 text-primary-foreground' :
        type === 'error' ? 'bg-destructive text-primary-foreground' :
        'bg-primary text-primary-foreground'
    );
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
            document.getElementById('unified-coverage').textContent = Platform.figure.percent(data.coverage);

            // Render domain cards
            renderBusinessDomainCards(data.domains || []);
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        // The stat spans default to "0" in the template; a failed load must not
        // leave that in place looking like a real (zero) count.
        ['unified-domain-count', 'unified-cap-count', 'unified-mapped-count', 'unified-coverage'].forEach(function(id) {
            let el = document.getElementById(id);
            if (el) el.textContent = '—';
        });
        if (window.Platform && Platform.toast) Platform.toast.error('Could not load business domains.');
    }
}

function renderBusinessDomainCards(domains) {
    let grid = document.getElementById('unified-domains-grid');
    if (!domains || domains.length === 0) {
        safeHTML(grid, '<div class="col-span-full text-center py-8 text-muted-foreground/60">No business domains configured.</div>');
        return;
    }

    let colors = ['blue', 'green', 'purple', 'orange', 'teal', 'indigo', 'pink', 'cyan', 'amber'];

    safeHTML(grid, domains.map(function(domain, index) {
        let color = colors[index % colors.length];
        let coverageColor = domain.coverage >= 70 ? 'text-emerald-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-destructive';
        let barColor = domain.coverage >= 70 ? 'bg-emerald-500' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-destructive';
        let statusColor = domain.coverage >= 70 ? 'bg-green-400' : domain.coverage >= 40 ? 'bg-amber-400' : 'bg-red-400';

        return '\
            <div class="bg-' + color + '-50 border-2 border-' + color + '-200 rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"\
                 data-action="filterByUnifiedDomain" data-id="' + domain.code + '">\
                <div class="flex items-start justify-between mb-3">\
                    <span class="text-xs font-bold text-' + color + '-600 bg-' + color + '-100 px-2 py-0.5 rounded">' + domain.code + '</span>\
                    <div class="w-2 h-2 rounded-full ' + statusColor + '"></div>\
                </div>\
                <h4 class="text-sm font-semibold text-' + color + '-800 mb-1">' + domain.name + '</h4>\
                <p class="text-xs text-muted-foreground mb-3 line-clamp-2">' + (domain.description || '') + '</p>\
                <div class="flex justify-between text-xs text-muted-foreground">\
                    <span>' + (domain.capability_count || 0) + ' capabilities</span>\
                    <span class="font-medium ' + coverageColor + '">' + Platform.figure.percent(domain.coverage) + '</span>\
                </div>\
                <div class="mt-2 w-full bg-muted rounded-full h-1.5">\
                    <div class="' + barColor + ' h-1.5 rounded-full transition-all" style="width: ' + Platform.figure.barWidth(domain.coverage) + '%"></div>\
                </div>\
                <div class="mt-2 text-xs text-muted-foreground">\
                    L1: ' + (domain.l1_count || 0) + ' | L2: ' + (domain.l2_count || 0) + ' | L3: ' + (domain.l3_count || 0) + '\
                </div>\
            </div>\
        ';
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
            document.getElementById('mfg-coverage').textContent = Platform.figure.percent(data.coverage);
            document.getElementById('mfg-avg-oee').textContent = Platform.figure.percent(data.avg_oee);

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
        // The stat spans default to "0" in the template; a failed load must not
        // leave that in place looking like a real (zero) count.
        ['mfg-cap-count', 'mfg-mapped-count', 'mfg-coverage', 'mfg-avg-oee'].forEach(function(id) {
            let el = document.getElementById(id);
            if (el) el.textContent = '—';
        });
        if (window.Platform && Platform.toast) Platform.toast.error('Could not load manufacturing domain stats.');
    }
}

function updateMfgDomainCard(prefix, domainData) {
    if (!domainData) domainData = { count: 0, coverage: 0 };
    let countEl = document.getElementById('mfg-' + prefix + '-count');
    let pctEl = document.getElementById('mfg-' + prefix + '-pct');
    let barEl = document.getElementById('mfg-' + prefix + '-bar');
    let statusEl = document.getElementById('mfg-' + prefix + '-status');

    if (countEl) countEl.textContent = (domainData.count || 0) + ' capabilities';
    if (pctEl) pctEl.textContent = Platform.figure.percent(domainData.coverage);
    if (barEl) barEl.style.width = Platform.figure.barWidth(domainData.coverage) + '%';
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
                if (pctEl) pctEl.textContent = Platform.figure.percent(cat.coverage);
                if (statusEl) {
                    statusEl.className = 'w-2 h-2 rounded-full ' + (cat.coverage >= 70 ? 'bg-green-400' : cat.coverage >= 40 ? 'bg-amber-400' : 'bg-muted/70');
                }
            }
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        // The stat spans default to "0" in the template; a failed load must not
        // leave that in place looking like a real (zero) count.
        for (let i = 1; i <= 13; i++) {
            let countEl = document.getElementById('proc-cat-' + i + '-count');
            let pctEl = document.getElementById('proc-cat-' + i + '-pct');
            if (countEl) countEl.textContent = '—';
            if (pctEl) pctEl.textContent = '—';
        }
        if (window.Platform && Platform.toast) Platform.toast.error('Could not load process category stats.');
    }
}

function filterProcessByCategory(category) {
    // Set a hidden filter or search by category prefix
    document.getElementById('process-search-filter').value = category;
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
            document.getElementById('acm-gap-coverage-pct').textContent = Platform.figure.percent(stats.coverage_rate);

            // Render domain coverage grid
            renderACMGapDomainCoverage(acmGapDomainStats);

            // Render gap table
            renderACMGapTable(acmGapData);
        }

        if (typeof lucide !== 'undefined') setTimeout(function() { lucide.createIcons(); }, 100);
    } catch (error) {
        safeHTML(document.getElementById('acm-gap-table-body'), '\
            <tr>\
                <td colspan="6" class="px-6 py-8 text-center text-destructive">\
                    <i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>\
                    <p>Error loading ACM gap analysis. Please try again.</p>\
                </td>\
            </tr>\
        ');
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
        safeHTML(grid, '<div class="text-center text-muted-foreground/60 col-span-7">No domain coverage data available.</div>');
        return;
    }

    safeHTML(grid, domainStats.map(function(domain) {
        let colors = domainColors[domain.domain] || { bg: 'bg-muted', text: 'text-foreground', short: '?' };
        let coverageColor = domain.coverage >= 70 ? 'text-emerald-600' : domain.coverage >= 40 ? 'text-amber-600' : 'text-destructive';
        let barColor = domain.coverage >= 70 ? 'bg-emerald-500' : domain.coverage >= 40 ? 'bg-amber-500' : 'bg-destructive';

        return '\
            <div class="' + colors.bg + ' rounded-lg p-3 text-center cursor-pointer hover:shadow-md transition-shadow"\
                 data-action="filterByACMGapDomain" data-id="' + domain.domain + '">\
                <div class="font-semibold ' + colors.text + ' text-sm mb-1">' + colors.short + '</div>\
                <div class="' + coverageColor + ' font-bold text-lg">' + domain.coverage + '%</div>\
                <div class="w-full bg-muted rounded-full h-1 mt-2">\
                    <div class="' + barColor + ' h-1 rounded-full" style="width: ' + domain.coverage + '%"></div>\
                </div>\
                <div class="text-xs text-muted-foreground mt-1">' + domain.total + ' caps</div>\
                <div class="text-xs ' + (domain.unmapped > 0 ? 'text-destructive' : 'text-emerald-500') + '">' + domain.unmapped + ' gaps</div>\
            </div>\
        ';
    }).join(''));
}

function renderACMGapTable(capabilities) {
    let tbody = document.getElementById('acm-gap-table-body');
    let showingEl = document.getElementById('acm-gap-showing');

    if (!capabilities || capabilities.length === 0) {
        safeHTML(tbody, '\
            <tr>\
                <td colspan="6" class="px-6 py-12 text-center text-muted-foreground">\
                    <i data-lucide="check-circle" class="w-8 h-8 mx-auto mb-2 text-emerald-500"></i>\
                    <p>No gaps found matching current filters.</p>\
                </td>\
            </tr>\
        ');
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

        return '\
            <tr class="hover:bg-muted/30">\
                <td class="px-6 py-4">\
                    <div class="flex items-start">\
                        <div>\
                            <div class="text-sm font-medium text-foreground">' + cap.name + '</div>\
                            <div class="text-xs text-muted-foreground font-mono">' + (cap.code || '') + '</div>\
                        </div>\
                    </div>\
                </td>\
                <td class="px-6 py-4 whitespace-nowrap">' + domainBadge + '</td>\
                <td class="px-6 py-4 whitespace-nowrap">' + levelBadge + '</td>\
                <td class="px-6 py-4 whitespace-nowrap">\
                    <span class="text-sm ' + (cap.applications_count > 0 ? 'text-foreground' : 'text-destructive font-medium') + '">' + cap.applications_count + '</span>\
                </td>\
                <td class="px-6 py-4">\
                    <div class="flex flex-wrap gap-1">\
                        ' + (cap.vendors && cap.vendors.length > 0
                            ? cap.vendors.map(function(v) { return '<span class="px-2 py-0.5 text-[10px] rounded-full ' + (cap.is_market_data ? 'bg-primary/5 text-primary border border-blue-100' : 'bg-muted text-foreground') + '">' + v + '</span>'; }).join('')
                            : '<span class="text-xs text-muted-foreground/60 italic">No vendors found</span>'
                        ) + '\
                        ' + (cap.is_market_data ? '<div class="w-full mt-1 text-[10px] text-primary italic">Market solutions available</div>' : '') + '\
                    </div>\
                </td>\
                <td class="px-6 py-4 whitespace-nowrap">' + statusBadge + '</td>\
            </tr>\
        ';
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
