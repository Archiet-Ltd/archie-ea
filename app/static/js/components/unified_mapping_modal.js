/**
 * Unified Mapping Modal - External JavaScript
 * Extracted from unified_mapping_modal.html inline scripts
 * Depends on: window.__APP_CONFIG__ (injected by template)
 *
 * Supports context modes: 'capability' (default), 'archimate', 'apqc', 'vendor'
 * Supports two-step workflow:
 *   Step 1: Target Selection (browse/search vendors or APQC processes)
 *   Step 2: Application Mapping (detailed field mapping)
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

// ============================================================================
// Unified Mapping Modal - JavaScript (Enhanced with Discovery Mode)
// ============================================================================

let UnifiedMappingModal = {
    // Core state
    context: 'capability',  // 'capability', 'archimate', 'apqc', 'vendor'
    currentStep: 1,         // 1 = target selection, 2 = application mapping
    discoveryMode: false,   // true if opened without a specific target

    // Target state
    targetId: null,
    targetName: '',
    targetType: null,
    targetsData: [],
    filteredTargets: [],

    // Application mapping state
    vendorProductId: null,
    applicationsData: [],
    selectedApplications: new Map(),

    // Pagination state
    targetPage: 1,
    targetPageSize: 20,
    appPage: 1,
    appPageSize: 20,

    // API endpoints
    apiEndpoint: '/capability-map/api',
    onSaveCallback: null,

    // ArchiMate 3.2 Relationship Types
    archimateRelationships: {
        structural: [
            { value: 'composition', label: 'Composition', description: 'Source contains target (whole-part)' },
            { value: 'aggregation', label: 'Aggregation', description: 'Source groups target (collection)' },
            { value: 'assignment', label: 'Assignment', description: 'Active element assigned to behavior' },
            { value: 'realization', label: 'Realization', description: 'Element realizes another' }
        ],
        dependency: [
            { value: 'serving', label: 'Serving', description: 'Provides functionality to another' },
            { value: 'access', label: 'Access', description: 'Behavior accesses data' },
            { value: 'influence', label: 'Influence', description: 'Motivation element influences another' }
        ],
        dynamic: [
            { value: 'triggering', label: 'Triggering', description: 'Temporal/causal relationship' },
            { value: 'flow', label: 'Flow', description: 'Transfer of information/material/value' }
        ],
        other: [
            { value: 'specialization', label: 'Specialization', description: 'More specific form of another' },
            { value: 'association', label: 'Association', description: 'Generic relationship' }
        ]
    }
};

// Initialize the modal
function initUnifiedMappingModal(config) {
    config = config || {};
    UnifiedMappingModal.context = config.context || 'capability';
    UnifiedMappingModal.apiEndpoint = config.apiEndpoint || '/capability-map/api';
    UnifiedMappingModal.onSaveCallback = config.onSaveCallback || null;
    UnifiedMappingModal.discoveryMode = config.discoveryMode || false;
    updateContextBadge();
}

function updateContextBadge() {
    let label = document.getElementById('unified-context-label');
    let text = document.getElementById('unified-context-text');
    let targetTypeLabel = document.getElementById('unified-target-type-label');

    // Update context badge colors and text
    if (UnifiedMappingModal.context === 'vendor') {
        label.className = 'inline-flex items-center px-3 py-1 text-xs font-medium rounded-full bg-primary/10 text-primary/90';
        text.textContent = 'Vendor Product Mapping';
        if (targetTypeLabel) targetTypeLabel.textContent = 'vendor products';
    } else if (UnifiedMappingModal.context === 'apqc') {
        label.className = 'inline-flex items-center px-3 py-1 text-xs font-medium rounded-full bg-emerald-500/10 text-green-800';
        text.textContent = 'APQC Process Mapping';
        if (targetTypeLabel) targetTypeLabel.textContent = 'processes';
    } else if (UnifiedMappingModal.context === 'archimate') {
        label.className = 'inline-flex items-center px-3 py-1 text-xs font-medium rounded-full bg-purple-100 text-purple-800';
        text.textContent = 'ArchiMate 3.2 Mapping';
        if (targetTypeLabel) targetTypeLabel.textContent = 'elements';
    } else {
        label.className = 'inline-flex items-center px-3 py-1 text-xs font-medium rounded-full bg-muted text-foreground';
        text.textContent = 'Capability Mapping';
        if (targetTypeLabel) targetTypeLabel.textContent = 'capabilities';
    }

    // Show/hide context-specific filters
    let vendorFilters = document.getElementById('vendor-filters');
    let apqcFilters = document.getElementById('apqc-filters');
    let archimateFilters = document.getElementById('archimate-filters');
    if (vendorFilters) vendorFilters.classList.toggle('hidden', UnifiedMappingModal.context !== 'vendor');
    if (apqcFilters) apqcFilters.classList.toggle('hidden', UnifiedMappingModal.context !== 'apqc');
    if (archimateFilters) archimateFilters.classList.toggle('hidden', UnifiedMappingModal.context !== 'archimate');
}

function updateStepIndicator() {
    let stepEl = document.getElementById('unified-current-step');
    let stepIndicator = document.getElementById('unified-step-indicator');
    if (stepEl) stepEl.textContent = UnifiedMappingModal.currentStep;

    // Hide step indicator if not in discovery mode (direct mapping)
    if (stepIndicator) {
        stepIndicator.classList.toggle('hidden', !UnifiedMappingModal.discoveryMode);
    }
}

// ============================================================================
// DISCOVERY MODE - Open modal to select target first
// ============================================================================

window.openUnifiedMappingModalDiscovery = async function(config) {
    config = config || {};
    UnifiedMappingModal.context = config.context || 'vendor';
    UnifiedMappingModal.discoveryMode = true;
    UnifiedMappingModal.currentStep = 1;
    UnifiedMappingModal.targetId = null;
    UnifiedMappingModal.targetName = '';
    UnifiedMappingModal.selectedApplications.clear();
    UnifiedMappingModal.onSaveCallback = config.onSaveCallback || null;
    UnifiedMappingModal.preSelectIds = Array.isArray(config.ids) ? config.ids : [];

    // Update UI
    updateContextBadge();
    updateStepIndicator();
    showStep(1);

    // Update modal title
    let titleEl = document.getElementById('unified-modal-title');
    let nameEl = document.getElementById('unified-modal-target-name');
    if (UnifiedMappingModal.context === 'vendor') {
        titleEl.textContent = 'Select Vendor Product to Map:';
    } else if (UnifiedMappingModal.context === 'apqc') {
        titleEl.textContent = 'Select APQC Process to Map:';
    } else {
        titleEl.textContent = 'Select Target:';
    }
    if (nameEl) nameEl.textContent = '';

    // Show modal
    Platform.modal.open('unified-mapping-modal');

    // Load targets
    await loadTargets();

    // Reinitialize icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
};

async function loadTargets() {
    let container = document.getElementById('unified-targets-list');
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8 text-muted-foreground">' +
        '<i data-lucide="loader" class="w-8 h-8 mx-auto mb-2 animate-spin"></i>' +
        '<p>Loading...</p>' +
        '</div>');
    if (typeof lucide !== 'undefined') lucide.createIcons();

    try {
        let url = '';
        if (UnifiedMappingModal.context === 'vendor') {
            url = '/dashboard/api/vendors/products';
        } else if (UnifiedMappingModal.context === 'application') {
            url = '/api/enterprise/applications';
        } else if (UnifiedMappingModal.context === 'apqc') {
            url = '/api/apqc/tree';
        } else if (UnifiedMappingModal.context === 'archimate') {
            url = '/solutions/api/archimate-all-elements';
        } else if (UnifiedMappingModal.context === 'technical-capability') {
            url = '/capability-map/api/unified-capabilities';
        } else if (UnifiedMappingModal.context === 'application-capability') {
            url = '/capability-map/api/unified-capabilities';
        } else if (UnifiedMappingModal.context === 'manufacturing-capability') {
            url = '/capability-map/api/manufacturing-capabilities';
        } else {
            // Business capabilities - use unified capabilities endpoint
            url = '/capability-map/api/unified-capabilities';
        }

        let data = await Platform.fetch(url, { silent: true });

        if (data.error) {
            safeHTML(container, '<div class="text-center py-8 text-destructive">' +
                '<i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>' +
                '<p>Error: ' + data.error + '</p>' +
                '</div>');
            if (typeof lucide !== 'undefined') lucide.createIcons();
            return;
        }

        // Normalize data structure based on context
        if (UnifiedMappingModal.context === 'vendor') {
            // Vendor products API returns array directly
            let products = Array.isArray(data) ? data : (data.products || data.vendors || []);
            UnifiedMappingModal.targetsData = products.map(function(item) {
                return {
                    id: item.id || item.vendor_product_id,
                    name: item.name || item.product_name || item.vendor_name,
                    description: item.description || '',
                    category: item.category || item.domain || '',
                    vendor_name: item.vendor_name || (item.vendor_organization ? item.vendor_organization.name : '') || item.vendor || '',
                    tier: item.tier || item.market_segment || '',
                    mapped_count: item.mapped_applications_count || item.application_count || 0
                };
            });
        } else if (UnifiedMappingModal.context === 'application') {
            // Enterprise applications API returns {applications: [...]}
            let apps = data.applications || (Array.isArray(data) ? data : []);
            UnifiedMappingModal.targetsData = apps.map(function(item) {
                return {
                    id: item.id,
                    name: item.name || 'Unknown Application',
                    description: item.description || '',
                    category: item.category || item.domain || '',
                    vendor_name: '',
                    tier: item.criticality || '',
                    mapped_count: 0
                };
            });
        } else if (UnifiedMappingModal.context === 'apqc') {
            // APQC tree API returns nested structure - flatten it
            let flattenTree = function(nodes, result) {
                result = result || [];
                if (!nodes) return result;
                for (let i = 0; i < nodes.length; i++) {
                    let node = nodes[i];
                    result.push({
                        id: node.id,
                        name: node.name || node.process_name,
                        description: node.description || '',
                        level: node.level || node.hierarchy_level || 1,
                        category: node.category || node.parent_name || '',
                        pcf_id: node.pcf_id || node.code || node.process_id || '',
                        mapped_count: node.mapped_count || node.application_count || 0
                    });
                    if (node.children && node.children.length > 0) {
                        flattenTree(node.children, result);
                    }
                }
                return result;
            };
            let treeData = data.tree || data.processes || data || [];
            UnifiedMappingModal.targetsData = flattenTree(Array.isArray(treeData) ? treeData : [treeData]);
        } else if (UnifiedMappingModal.context === 'archimate') {
            // ArchiMate elements - comprehensive response with all 6 layers
            let elements = data.elements || data || [];
            UnifiedMappingModal.targetsData = elements.map(function(item) {
                return {
                    id: item.id,
                    name: item.name,
                    description: item.description || '',
                    type: item.type || 'Element',
                    layer: item.layer || 'business',
                    layer_color: item.layer_color || '#CCCCCC',
                    table: item.table || '',
                    is_template: item.is_template || false,
                    category: item.layer  // Use layer as category for filtering
                };
            });
            // Store layer colors for UI
            if (data.layer_colors) {
                UnifiedMappingModal.layerColors = data.layer_colors;
            }
        } else {
            // Capabilities - business, application, or technical
            let capabilities = data.capabilities || data.unified_capabilities || data || [];
            UnifiedMappingModal.targetsData = (Array.isArray(capabilities) ? capabilities : []).map(function(item) {
                return {
                    id: item.id,
                    name: item.name,
                    description: item.description || '',
                    level: item.level || 1,
                    category: item.category || item.capability_type || '',
                    capability_type: item.capability_type || item.specialization_type || 'BUSINESS',
                    domain: item.domain || item.industry_domain || '',
                    mapped_count: item.mapped_count || 0
                };
            });
        }

        filterUnifiedTargets();
    } catch (error) {
        safeHTML(container, '<div class="text-center py-8 text-destructive">' +
            '<i data-lucide="alert-circle" class="w-8 h-8 mx-auto mb-2"></i>' +
            '<p>Failed to load data. Please try again.</p>' +
            '</div>');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }
}

window.filterUnifiedTargets = function() {
    let searchTerm = (document.getElementById('unified-target-search') ? document.getElementById('unified-target-search').value : '').toLowerCase();
    let filtered = UnifiedMappingModal.targetsData;

    // Apply search filter
    if (searchTerm) {
        filtered = filtered.filter(function(target) {
            return (target.name || '').toLowerCase().includes(searchTerm) ||
                (target.description || '').toLowerCase().includes(searchTerm) ||
                (target.category || '').toLowerCase().includes(searchTerm) ||
                (target.vendor_name || '').toLowerCase().includes(searchTerm) ||
                (target.pcf_id || '').toLowerCase().includes(searchTerm);
        });
    }

    // Apply context-specific filters
    if (UnifiedMappingModal.context === 'vendor') {
        let categoryFilter = document.getElementById('unified-vendor-category') ? document.getElementById('unified-vendor-category').value : '';
        let tierFilter = document.getElementById('unified-vendor-tier') ? document.getElementById('unified-vendor-tier').value : '';

        if (categoryFilter) {
            filtered = filtered.filter(function(t) { return (t.category || '').toLowerCase().includes(categoryFilter.toLowerCase()); });
        }
        if (tierFilter) {
            filtered = filtered.filter(function(t) { return (t.tier || '').toLowerCase().includes(tierFilter.toLowerCase()); });
        }
    } else if (UnifiedMappingModal.context === 'apqc') {
        let levelFilter = document.getElementById('unified-apqc-level') ? document.getElementById('unified-apqc-level').value : '';
        let apqcCategoryFilter = document.getElementById('unified-apqc-category') ? document.getElementById('unified-apqc-category').value : '';

        if (levelFilter) {
            filtered = filtered.filter(function(t) { return String(t.level) === levelFilter; });
        }
        if (apqcCategoryFilter) {
            filtered = filtered.filter(function(t) { return (t.pcf_id || '').startsWith(apqcCategoryFilter + '.'); });
        }
    } else if (UnifiedMappingModal.context === 'archimate') {
        let layerFilter = document.getElementById('unified-archimate-layer') ? document.getElementById('unified-archimate-layer').value : '';
        let typeFilter = document.getElementById('unified-archimate-type') ? document.getElementById('unified-archimate-type').value : '';

        if (layerFilter) {
            filtered = filtered.filter(function(t) { return t.layer === layerFilter; });
        }
        if (typeFilter) {
            filtered = filtered.filter(function(t) { return t.type === typeFilter; });
        }
    }

    UnifiedMappingModal.filteredTargets = filtered;
    UnifiedMappingModal.targetPage = 1;
    renderTargetsList();
};

function renderTargetsList() {
    let container = document.getElementById('unified-targets-list');
    let filteredCountEl = document.getElementById('unified-target-filtered-count');
    let totalCountEl = document.getElementById('unified-target-total-count');

    if (filteredCountEl) filteredCountEl.textContent = UnifiedMappingModal.filteredTargets.length;
    if (totalCountEl) totalCountEl.textContent = UnifiedMappingModal.targetsData.length;

    if (!container) return;

    if (UnifiedMappingModal.filteredTargets.length === 0) {
        safeHTML(container, '<div class="text-center py-8 text-muted-foreground">' +
            '<i data-lucide="search-x" class="w-8 h-8 mx-auto mb-2"></i>' +
            '<p>No results found</p>' +
            '<p class="text-sm mt-1">Try adjusting your search or filters</p>' +
            '</div>');
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    let isVendor = UnifiedMappingModal.context === 'vendor';
    let isApqc = UnifiedMappingModal.context === 'apqc';
    let isReverseMode = UnifiedMappingModal.reverseMode;

    // Paginate
    let allTargets = UnifiedMappingModal.filteredTargets;
    let pageSize = UnifiedMappingModal.targetPageSize;
    let totalPages = Math.max(1, Math.ceil(allTargets.length / pageSize));
    if (UnifiedMappingModal.targetPage > totalPages) UnifiedMappingModal.targetPage = totalPages;
    let page = UnifiedMappingModal.targetPage;
    let start = (page - 1) * pageSize;
    let pageTargets = allTargets.slice(start, start + pageSize);

    // Update pagination controls
    updateTargetPagination(page, totalPages, start + 1, Math.min(start + pageSize, allTargets.length), allTargets.length);

    safeHTML(container, pageTargets.map(function(target) {
        let badgeColor = isVendor ? 'blue' : (isApqc ? 'green' : 'gray');
        let iconName = isVendor ? 'building-2' : (isApqc ? 'workflow' : 'layers');
        let isSelected = isReverseMode && UnifiedMappingModal.selectedTargets && UnifiedMappingModal.selectedTargets.has(String(target.id));
        let selectionClass = isSelected ? 'border-primary bg-primary/5' : 'border-border bg-background';

        return '<div class="' + selectionClass + ' border rounded-lg p-3 hover:border-' + badgeColor + '-300 hover:bg-' + badgeColor + '-50 cursor-pointer transition-all"' +
            ' data-action="selectTarget" data-params=\'["' + target.id + '", "' + (target.name || '').replace(/"/g, '&quot;') + '"]\'>' +
            '<div class="flex items-start justify-between">' +
                '<div class="flex items-start space-x-3 flex-1">' +
                    (isReverseMode ? '<input type="checkbox" ' + (isSelected ? 'checked' : '') + ' class="mt-2 rounded border-border text-primary focus:ring-primary" data-umm-card-checkbox aria-label="Select this target">' : '') +
                    '<div class="flex-shrink-0 w-10 h-10 rounded-lg bg-' + badgeColor + '-100 flex items-center justify-center">' +
                        '<i data-lucide="' + iconName + '" class="w-5 h-5 text-' + badgeColor + '-600"></i>' +
                    '</div>' +
                    '<div class="flex-1 min-w-0">' +
                        '<div class="font-medium text-foreground truncate">' + (target.name || 'Unnamed') + '</div>' +
                        (isVendor && target.vendor_name ? '<div class="text-xs text-muted-foreground">' + target.vendor_name + '</div>' : '') +
                        (isApqc && target.pcf_id ? '<div class="text-xs text-muted-foreground font-mono">' + target.pcf_id + '</div>' : '') +
                        (target.description ? '<div class="text-xs text-muted-foreground/60 mt-1 line-clamp-2">' + target.description + '</div>' : '') +
                    '</div>' +
                '</div>' +
                '<div class="flex flex-col items-end space-y-1 ml-3">' +
                    (target.category ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-muted text-muted-foreground rounded">' + target.category + '</span>' : '') +
                    (target.mapped_count > 0 ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-emerald-500/10 text-emerald-700 rounded">' + target.mapped_count + ' mapped</span>' : '') +
                '</div>' +
            '</div>' +
        '</div>';
    }).join(''));

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function updateTargetPagination(page, totalPages, startItem, endItem, totalItems) {
    let paginationEl = document.getElementById('unified-target-pagination');
    if (!paginationEl) return;

    if (totalPages <= 1) {
        paginationEl.classList.add('hidden');
        return;
    }
    paginationEl.classList.remove('hidden');

    let el = function(id) { return document.getElementById(id); };
    if (el('unified-target-page-start')) el('unified-target-page-start').textContent = startItem;
    if (el('unified-target-page-end')) el('unified-target-page-end').textContent = endItem;
    if (el('unified-target-page-total')) el('unified-target-page-total').textContent = totalItems;
    if (el('unified-target-current-page')) el('unified-target-current-page').textContent = page;
    if (el('unified-target-total-pages')) el('unified-target-total-pages').textContent = totalPages;

    // Disable/enable buttons
    let prevBtn = paginationEl.querySelector('button:first-of-type');
    let nextBtn = paginationEl.querySelector('button:last-of-type');
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
}

window.changeTargetPage = function(delta) {
    let totalPages = Math.max(1, Math.ceil(UnifiedMappingModal.filteredTargets.length / UnifiedMappingModal.targetPageSize));
    let newPage = UnifiedMappingModal.targetPage + delta;
    if (newPage >= 1 && newPage <= totalPages) {
        UnifiedMappingModal.targetPage = newPage;
        renderTargetsList();
    }
};

function updateAppPagination(page, totalPages, startItem, endItem, totalItems) {
    let paginationEl = document.getElementById('unified-app-pagination');
    if (!paginationEl) return;

    if (totalPages <= 1) {
        paginationEl.classList.add('hidden');
        return;
    }
    paginationEl.classList.remove('hidden');

    let el = function(id) { return document.getElementById(id); };
    if (el('unified-app-page-start')) el('unified-app-page-start').textContent = startItem;
    if (el('unified-app-page-end')) el('unified-app-page-end').textContent = endItem;
    if (el('unified-app-page-total')) el('unified-app-page-total').textContent = totalItems;
    if (el('unified-app-current-page')) el('unified-app-current-page').textContent = page;
    if (el('unified-app-total-pages')) el('unified-app-total-pages').textContent = totalPages;

    let prevBtn = paginationEl.querySelector('button:first-of-type');
    let nextBtn = paginationEl.querySelector('button:last-of-type');
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
}

window.changeAppPage = function(delta) {
    let newPage = UnifiedMappingModal.appPage + delta;
    if (newPage >= 1) {
        UnifiedMappingModal.appPage = newPage;
        renderUnifiedApplicationsList();
    }
};

window.selectTarget = function(targetId, targetName) {
    // Handle reverse mode: toggle target selection
    if (UnifiedMappingModal.reverseMode) {
        toggleTargetSelection(targetId, targetName);
        return;
    }

    // Standard mode: select target and move to step 2
    UnifiedMappingModal.targetId = String(targetId);
    UnifiedMappingModal.targetName = targetName;
    UnifiedMappingModal.vendorProductId = UnifiedMappingModal.context === 'vendor' ? targetId : null;

    // Update title
    let nameEl = document.getElementById('unified-modal-target-name');
    if (nameEl) nameEl.textContent = targetName;

    // Move to step 2
    UnifiedMappingModal.currentStep = 2;
    updateStepIndicator();
    showStep(2);

    // Load applications for this target
    loadUnifiedApplications(targetId);
};

// Toggle target selection in reverse mode
function toggleTargetSelection(targetId, targetName) {
    if (!UnifiedMappingModal.selectedTargets) {
        UnifiedMappingModal.selectedTargets = new Set();
    }

    if (UnifiedMappingModal.selectedTargets.has(targetId)) {
        UnifiedMappingModal.selectedTargets.delete(targetId);
    } else {
        UnifiedMappingModal.selectedTargets.add(targetId);
    }

    // Re-render to show selection state
    renderTargetsList();

    // Update selected count display
    updateSelectedTargetsCount();
}

function updateSelectedTargetsCount() {
    let count = UnifiedMappingModal.selectedTargets ? UnifiedMappingModal.selectedTargets.size : 0;

    // Update Step 1 save button in reverse mode
    let reverseSaveBtn = document.getElementById('unified-reverse-save-btn');
    if (reverseSaveBtn && UnifiedMappingModal.reverseMode) {
        reverseSaveBtn.classList.remove('hidden');
        reverseSaveBtn.textContent = count > 0 ? 'Save Mappings (' + count + ' selected)' : 'Save Mappings';
    }

    // Also update Step 2 button text (legacy, in case mode switches)
    let saveBtn = document.querySelector('#unified-step-application-mapping .bg-primary'); // token-migration-ok
    if (saveBtn && UnifiedMappingModal.reverseMode) {
        saveBtn.textContent = 'Save Mappings (' + count + ' selected)';
    }
}

function selectAllTargets() {
    if (!UnifiedMappingModal.selectedTargets) {
        UnifiedMappingModal.selectedTargets = new Set();
    }
    UnifiedMappingModal.filteredTargets.forEach(function(target) {
        UnifiedMappingModal.selectedTargets.add(String(target.id));
    });
    renderTargetsList();
    updateSelectedTargetsCount();
}

function deselectAllTargets() {
    if (UnifiedMappingModal.selectedTargets) {
        UnifiedMappingModal.selectedTargets.clear();
    }
    renderTargetsList();
    updateSelectedTargetsCount();
}

function showStep(step) {
    let step1 = document.getElementById('unified-step-target-selection');
    let step2 = document.getElementById('unified-step-application-mapping');

    if (step === 1) {
        if (step1) step1.classList.remove('hidden');
        if (step2) step2.classList.add('hidden');
    } else {
        if (step1) step1.classList.add('hidden');
        if (step2) step2.classList.remove('hidden');
    }
}

window.goBackToTargetSelection = function() {
    UnifiedMappingModal.currentStep = 1;
    updateStepIndicator();
    showStep(1);

    // Update title back
    let titleEl = document.getElementById('unified-modal-title');
    let nameEl = document.getElementById('unified-modal-target-name');
    if (UnifiedMappingModal.context === 'vendor') {
        titleEl.textContent = 'Select Vendor Product to Map:';
    } else if (UnifiedMappingModal.context === 'apqc') {
        titleEl.textContent = 'Select APQC Process to Map:';
    }
    if (nameEl) nameEl.textContent = '';
};

// ============================================================================
// DIRECT MODE - Open modal with specific target (original behavior)
// ============================================================================

window.openUnifiedMappingModal = async function(targetId, targetName, options) {
    options = options || {};
    UnifiedMappingModal.targetId = String(targetId);
    UnifiedMappingModal.targetName = targetName;
    UnifiedMappingModal.targetType = options.targetType || null;
    UnifiedMappingModal.discoveryMode = false;
    UnifiedMappingModal.currentStep = 2; // Skip to step 2
    UnifiedMappingModal.selectedApplications.clear();
    UnifiedMappingModal.vendorProductId = options.vendorProductId || null;

    // Update UI
    updateContextBadge();
    updateStepIndicator();
    showStep(2);

    // Update modal title
    let titleEl = document.getElementById('unified-modal-title');
    let nameEl = document.getElementById('unified-modal-target-name');

    if (UnifiedMappingModal.context === 'archimate') {
        titleEl.textContent = 'Map ArchiMate Elements to:';
    } else if (UnifiedMappingModal.context === 'apqc') {
        titleEl.textContent = 'Map Applications to Process:';
    } else if (UnifiedMappingModal.context === 'vendor') {
        titleEl.textContent = 'Map Applications to Vendor Product:';
    } else {
        titleEl.textContent = 'Map Applications to:';
    }
    if (nameEl) nameEl.textContent = targetName;

    // Show modal
    Platform.modal.open('unified-mapping-modal');

    // Load applications
    await loadUnifiedApplications(targetId);
};

// ============================================================================
// REVERSE MODE: Application -> Targets (Capabilities/Vendors/etc)
// ============================================================================

window.openUnifiedMappingModalReverse = async function(config) {
    config = config || {};
    // Set up modal for reverse workflow
    UnifiedMappingModal.context = config.context || 'capability';
    UnifiedMappingModal.discoveryMode = true;
    UnifiedMappingModal.currentStep = 1; // Stay on step 1 to select targets
    UnifiedMappingModal.targetId = null;
    UnifiedMappingModal.targetName = '';
    UnifiedMappingModal.selectedApplications.clear();
    UnifiedMappingModal.onSaveCallback = config.onSaveCallback || null;

    // Store reverse mode context
    UnifiedMappingModal.reverseMode = true;
    UnifiedMappingModal.reverseAppId = config.appId;
    UnifiedMappingModal.reverseAppName = config.appName;

    // Update UI
    updateContextBadge();
    updateStepIndicator();
    showStep(1);

    // Update modal title for reverse mode
    let titleEl = document.getElementById('unified-modal-title');
    let nameEl = document.getElementById('unified-modal-target-name');
    if (titleEl) titleEl.textContent = 'Map ' + (config.context === 'capability' ? 'Capabilities' : 'Targets') + ' to:';
    if (nameEl) nameEl.textContent = config.appName || 'Application';

    // Show modal
    Platform.modal.open('unified-mapping-modal');

    // Load targets (capabilities/vendors/etc)
    await loadTargets();

    // Show save button in reverse mode
    let reverseSaveBtn = document.getElementById('unified-reverse-save-btn');
    if (reverseSaveBtn) {
        reverseSaveBtn.classList.remove('hidden');
    }

    // Mark this as reverse mode in UI
    if (titleEl) titleEl.dataset.reverseMode = 'true';

    // Reinitialize icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }
};

// Close modal
window.closeUnifiedMappingModal = function() {
    Platform.modal.close('unified-mapping-modal');
    UnifiedMappingModal.targetId = null;
    UnifiedMappingModal.targetName = '';
    UnifiedMappingModal.selectedApplications.clear();
    UnifiedMappingModal.applicationsData = [];
    UnifiedMappingModal.targetsData = [];
    UnifiedMappingModal.currentStep = 1;
    UnifiedMappingModal.discoveryMode = false;
    UnifiedMappingModal.reverseMode = false;

    // Hide reverse save button on cleanup
    let reverseSaveBtn = document.getElementById('unified-reverse-save-btn');
    if (reverseSaveBtn) {
        reverseSaveBtn.classList.add('hidden');
    }
};

// ============================================================================
// APPLICATION MAPPING (Step 2)
// ============================================================================

async function loadUnifiedApplications(targetId) {
    let container = document.getElementById('unified-applications-list');
    if (container) {
        safeHTML(container, '<div class="text-center py-8 text-muted-foreground">' +
            '<i data-lucide="loader" class="w-8 h-8 mx-auto mb-2 animate-spin"></i>' +
            '<p>Loading applications...</p>' +
            '</div>');
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    try {
        let id = String(targetId);
        let url = UnifiedMappingModal.apiEndpoint + '/capability/' + id + '/applications';

        if (UnifiedMappingModal.context === 'apqc') {
            url = '/api/apqc/process/' + id + '/applications';
        } else if (UnifiedMappingModal.context === 'archimate') {
            // Canonical applications list reader: one endpoint for the
            // organisation's applications, tenant-scoped by the middleware.
            url = '/applications/api/list';
        } else if (UnifiedMappingModal.context === 'vendor') {
            url = '/api/vendors/' + id + '/applications';
        }

        let data = await Platform.fetch(url, { silent: true });

        if (data.error) {
            if (container) {
                safeHTML(container, '<div class="text-center py-8 text-destructive">' +
                    '<p>Error: ' + data.error + '</p>' +
                    '</div>');
            }
            return;
        }

        UnifiedMappingModal.applicationsData = data.applications || [];

        // Pre-select mapped applications and any bulk-selected IDs
        UnifiedMappingModal.selectedApplications.clear();
        let preIds = UnifiedMappingModal.preSelectIds || [];
        UnifiedMappingModal.applicationsData.forEach(function(app) {
            if (app.is_mapped || preIds.indexOf(app.id) !== -1) {
                UnifiedMappingModal.selectedApplications.set(app.id, {
                    application_id: app.id,
                    mapping_id: app.mapping_id || null,
                    mapping: buildMappingData(app)
                });
            }
        });

        resetUnifiedFilters();
        populateUnifiedFilterOptions();
        renderUnifiedApplicationsList();

        setTimeout(function() {
            let searchInput = document.getElementById('unified-search');
            if (searchInput) searchInput.focus();
        }, 100);
    } catch (error) {
        if (container) {
            safeHTML(container, '<div class="text-center py-8 text-destructive">' +
                '<p>Failed to load applications</p>' +
                '</div>');
        }
    }
}

function buildMappingData(app) {
    let data = {
        support_level: app.support_level || 'partial',
        coverage_percentage: app.coverage_percentage || 0,
        support_quality: app.support_quality || 3,
        relationship_type: app.relationship_type || 'enables',
        relationship_strength: app.relationship_strength || 3,
        dependency_level: app.dependency_level || 'medium',
        gap_status: app.gap_status || 'unknown',
        gap_description: app.gap_description || '',
        gap_impact: app.gap_impact || 'medium',
        priority: app.priority || 'medium',
        integration_complexity: app.integration_complexity || 'medium',
        is_active: app.is_active !== false
    };

    if (UnifiedMappingModal.context === 'archimate') {
        data.archimate_relationship_type = app.archimate_relationship_type || 'serving';
        data.archimate_access_mode = app.archimate_access_mode || 'unspecified';
        data.derivation_enabled = app.derivation_enabled !== false;
    }

    if (UnifiedMappingModal.context === 'apqc') {
        data.automation_level = app.automation_level || 1;
        data.process_contribution = app.process_contribution || 50;
        data.process_criticality = app.process_criticality || 'medium';
        data.application_role = app.application_role || 'supporting';
        data.cycle_time_reduction = app.cycle_time_reduction || 0;
        data.quality_improvement = app.quality_improvement || 0;
        data.cost_reduction = app.cost_reduction || 0;
    }

    if (UnifiedMappingModal.context === 'vendor') {
        data.implementation_status = app.implementation_status || 'planned';
        data.license_type = app.license_type || 'subscription';
        data.deployment_model = app.deployment_model || 'cloud';
        data.contract_status = app.contract_status || 'active';
        data.integration_level = app.integration_level || 'standard';
        data.customization_level = app.customization_level || 'minimal';
        data.vendor_support_tier = app.vendor_support_tier || 'standard';
        data.annual_cost = app.annual_cost || 0;
        data.user_count = app.user_count || 0;
    }

    return data;
}

function resetUnifiedFilters() {
    let searchInput = document.getElementById('unified-search');
    let typeFilter = document.getElementById('unified-filter-type');
    let domainFilter = document.getElementById('unified-filter-domain');
    let statusFilter = document.getElementById('unified-filter-status');
    let sortSelect = document.getElementById('unified-sort');

    if (searchInput) searchInput.value = '';
    if (typeFilter) typeFilter.value = '';
    if (domainFilter) domainFilter.value = '';
    if (statusFilter) statusFilter.value = 'all';
    if (sortSelect) sortSelect.value = 'name-asc';
}

function populateUnifiedFilterOptions() {
    let apps = UnifiedMappingModal.applicationsData;

    let typeFilter = document.getElementById('unified-filter-type');
    if (typeFilter && apps.length > 0) {
        let types = [];
        let typesSeen = {};
        apps.forEach(function(app) {
            if (app.type && !typesSeen[app.type]) {
                typesSeen[app.type] = true;
                types.push(app.type);
            }
        });
        types.sort();
        safeHTML(typeFilter, '<option value="">All Types</option>' +
            types.map(function(type) { return '<option value="' + type + '">' + type + '</option>'; }).join(''));
    }

    let domainFilter = document.getElementById('unified-filter-domain');
    if (domainFilter && apps.length > 0) {
        let domains = [];
        let domainsSeen = {};
        apps.forEach(function(app) {
            if (app.domain && !domainsSeen[app.domain]) {
                domainsSeen[app.domain] = true;
                domains.push(app.domain);
            }
        });
        domains.sort();
        safeHTML(domainFilter, '<option value="">All Domains</option>' +
            domains.map(function(domain) { return '<option value="' + domain + '">' + domain + '</option>'; }).join(''));
    }
}

window.filterUnifiedApplications = function() {
    UnifiedMappingModal.appPage = 1;
    renderUnifiedApplicationsList();
};

function renderUnifiedApplicationsList() {
    let container = document.getElementById('unified-applications-list');
    let searchEl = document.getElementById('unified-search');
    let searchTerm = (searchEl ? searchEl.value : '').toLowerCase();
    let filterTypeEl = document.getElementById('unified-filter-type');
    let filterType = filterTypeEl ? filterTypeEl.value : '';
    let filterDomainEl = document.getElementById('unified-filter-domain');
    let filterDomain = filterDomainEl ? filterDomainEl.value : '';
    let filterStatusEl = document.getElementById('unified-filter-status');
    let filterStatus = filterStatusEl ? filterStatusEl.value : 'all';
    let sortEl = document.getElementById('unified-sort');
    let sortBy = sortEl ? sortEl.value : 'name-asc';

    if (!container) return;

    let filtered = UnifiedMappingModal.applicationsData.filter(function(app) {
        let matchesSearch = !searchTerm ||
            (app.name || '').toLowerCase().includes(searchTerm) ||
            (app.type || '').toLowerCase().includes(searchTerm) ||
            (app.domain || '').toLowerCase().includes(searchTerm) ||
            (app.description || '').toLowerCase().includes(searchTerm);

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
    let filteredCountEl = document.getElementById('unified-filtered-count');
    let totalCountEl = document.getElementById('unified-total-count');
    let selectedCountEl = document.getElementById('unified-selected-count');
    if (filteredCountEl) filteredCountEl.textContent = filtered.length;
    if (totalCountEl) totalCountEl.textContent = UnifiedMappingModal.applicationsData.length;
    if (selectedCountEl) selectedCountEl.textContent = UnifiedMappingModal.selectedApplications.size;

    if (filtered.length === 0) {
        safeHTML(container, '<div class="text-center py-12 text-muted-foreground">' +
            '<i data-lucide="search-x" class="w-12 h-12 mx-auto mb-3 text-muted-foreground/60"></i>' +
            '<p class="text-lg font-medium">No applications found</p>' +
            '<p class="text-sm mt-1">Try adjusting your search or filters</p>' +
            '</div>');
        updateAppPagination(1, 1, 0, 0, 0);
        if (typeof lucide !== 'undefined') lucide.createIcons();
        return;
    }

    // Paginate
    let appPageSize = UnifiedMappingModal.appPageSize;
    let appTotalPages = Math.max(1, Math.ceil(filtered.length / appPageSize));
    if (UnifiedMappingModal.appPage > appTotalPages) UnifiedMappingModal.appPage = appTotalPages;
    let appPage = UnifiedMappingModal.appPage;
    let appStart = (appPage - 1) * appPageSize;
    let pageApps = filtered.slice(appStart, appStart + appPageSize);

    updateAppPagination(appPage, appTotalPages, appStart + 1, Math.min(appStart + appPageSize, filtered.length), filtered.length);

    safeHTML(container, pageApps.map(function(app) {
        let isSelected = UnifiedMappingModal.selectedApplications.has(app.id);
        let mapping = UnifiedMappingModal.selectedApplications.get(app.id);

        return '<div class="border rounded-lg p-4 transition-all ' + (isSelected ? 'bg-primary/5 border-primary/30 shadow-sm' : 'bg-background border-border hover:border-border hover:shadow-sm') + '">' +
            '<div class="flex items-start justify-between">' +
                '<div class="flex items-start space-x-3 flex-1">' +
                    '<input type="checkbox" ' + (isSelected ? 'checked' : '') +
                        ' data-app-id="' + app.id + '"' +
                        ' class="mt-1 h-5 w-5 text-primary focus:ring-primary border-border rounded cursor-pointer" />' +
                    '<div class="flex-1">' +
                        '<div class="flex items-center space-x-2">' +
                            '<div class="font-medium text-foreground">' + (app.name || 'Unnamed') + '</div>' +
                            (app.is_mapped ? '<span class="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-emerald-500/10 text-green-800 rounded-full"><i data-lucide="check-circle" class="w-3 h-3 mr-1"></i>Mapped</span>' : '') +
                        '</div>' +
                        '<div class="flex items-center space-x-2 mt-1 text-sm text-muted-foreground">' +
                            '<span class="flex items-center">' +
                                '<i data-lucide="layers" class="w-3 h-3 mr-1"></i>' +
                                (app.type || 'Unknown') +
                            '</span>' +
                            '<span>&bull;</span>' +
                            '<span class="flex items-center">' +
                                '<i data-lucide="building" class="w-3 h-3 mr-1"></i>' +
                                (app.domain || 'Unknown') +
                            '</span>' +
                        '</div>' +
                        (app.description ? '<div class="text-xs text-muted-foreground/60 mt-2 line-clamp-2">' + app.description + '</div>' : '') +
                    '</div>' +
                '</div>' +
                (app.is_mapped && app.mapping_id ?
                    '<button data-action="deleteUnifiedMapping" data-params=\'["' + app.mapping_id + '", "' + app.id + '"]\'' +
                        ' class="ml-2 px-3 py-1.5 text-xs bg-destructive text-primary-foreground rounded hover:bg-destructive transition-colors flex items-center space-x-1">' +
                        '<i data-lucide="trash-2" class="w-3 h-3"></i>' +
                        '<span>Remove</span>' +
                    '</button>' : '') +
            '</div>' +
            (isSelected ? renderUnifiedApplicationSettings(app.id, mapping) : '') +
        '</div>';
    }).join(''));

    if (typeof lucide !== 'undefined') lucide.createIcons();

    // Wire up checkbox change handlers after safeHTML (DOMPurify strips onchange attrs)
    container.querySelectorAll('input[type="checkbox"][data-app-id]').forEach(function(cb) {
        cb.addEventListener('change', function() {
            toggleUnifiedApplicationSelection(cb.dataset.appId);
        });
    });
}

function renderUnifiedApplicationSettings(appId, mapping) {
    // Escaped for attribute interpolation, matching how data-params is built above.
    let appIdAttr = String(appId).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    let mappingData = (mapping && mapping.mapping) ? mapping.mapping : {};
    let context = UnifiedMappingModal.context;

    let html = '<div class="mt-4 pt-4 border-t border-border space-y-3">' +
        '<div class="grid grid-cols-2 md:grid-cols-3 gap-4">' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Support Level</label>' +
                '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="support_level">' +
                    '<option value="full"' + (mappingData.support_level === 'full' ? ' selected' : '') + '>Full</option>' +
                    '<option value="partial"' + (mappingData.support_level === 'partial' ? ' selected' : '') + '>Partial</option>' +
                    '<option value="minimal"' + (mappingData.support_level === 'minimal' ? ' selected' : '') + '>Minimal</option>' +
                '</select>' +
            '</div>' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Coverage %</label>' +
                '<input type="number" min="0" max="100" value="' + (mappingData.coverage_percentage || 0) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="coverage_percentage" data-umm-cast="int"/>' +
            '</div>' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Quality (1-5)</label>' +
                '<input type="number" min="1" max="5" value="' + (mappingData.support_quality || 3) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="support_quality" data-umm-cast="int"/>' +
            '</div>' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Relationship</label>' +
                '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="relationship_type">' +
                    '<option value="enables"' + (mappingData.relationship_type === 'enables' ? ' selected' : '') + '>Enables</option>' +
                    '<option value="supports"' + (mappingData.relationship_type === 'supports' ? ' selected' : '') + '>Supports</option>' +
                    '<option value="governs"' + (mappingData.relationship_type === 'governs' ? ' selected' : '') + '>Governs</option>' +
                    '<option value="measures"' + (mappingData.relationship_type === 'measures' ? ' selected' : '') + '>Measures</option>' +
                '</select>' +
            '</div>' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Dependency</label>' +
                '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="dependency_level">' +
                    '<option value="critical"' + (mappingData.dependency_level === 'critical' ? ' selected' : '') + '>Critical</option>' +
                    '<option value="high"' + (mappingData.dependency_level === 'high' ? ' selected' : '') + '>High</option>' +
                    '<option value="medium"' + (mappingData.dependency_level === 'medium' ? ' selected' : '') + '>Medium</option>' +
                    '<option value="low"' + (mappingData.dependency_level === 'low' ? ' selected' : '') + '>Low</option>' +
                '</select>' +
            '</div>' +
            '<div>' +
                '<label class="block text-xs font-medium text-foreground mb-1">Priority</label>' +
                '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="priority">' +
                    '<option value="high"' + (mappingData.priority === 'high' ? ' selected' : '') + '>High</option>' +
                    '<option value="medium"' + (mappingData.priority === 'medium' ? ' selected' : '') + '>Medium</option>' +
                    '<option value="low"' + (mappingData.priority === 'low' ? ' selected' : '') + '>Low</option>' +
                '</select>' +
            '</div>' +
        '</div>' +
        '<div>' +
            '<label class="block text-xs font-medium text-foreground mb-1">Gap Description</label>' +
            '<textarea class="w-full text-sm border border-border rounded px-2 py-1" rows="2" data-umm-app="' + appIdAttr + '" data-umm-field="gap_description">' + (mappingData.gap_description || '') + '</textarea>' +
        '</div>';

    // ArchiMate fields
    if (context === 'archimate') {
        html += '<div class="mt-3 pt-3 border-t border-purple-200 bg-purple-50 rounded p-3">' +
            '<div class="flex items-center mb-2">' +
                '<i data-lucide="box" class="w-4 h-4 text-primary mr-2"></i>' +
                '<span class="text-xs font-semibold text-purple-800">ArchiMate 3.2 Relationship</span>' +
            '</div>' +
            '<div class="grid grid-cols-2 gap-4">' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Relationship Type</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="archimate_relationship_type">' +
                        '<optgroup label="Structural">' +
                            '<option value="composition"' + (mappingData.archimate_relationship_type === 'composition' ? ' selected' : '') + '>Composition</option>' +
                            '<option value="aggregation"' + (mappingData.archimate_relationship_type === 'aggregation' ? ' selected' : '') + '>Aggregation</option>' +
                            '<option value="assignment"' + (mappingData.archimate_relationship_type === 'assignment' ? ' selected' : '') + '>Assignment</option>' +
                            '<option value="realization"' + (mappingData.archimate_relationship_type === 'realization' ? ' selected' : '') + '>Realization</option>' +
                        '</optgroup>' +
                        '<optgroup label="Dependency">' +
                            '<option value="serving"' + (mappingData.archimate_relationship_type === 'serving' ? ' selected' : '') + '>Serving</option>' +
                            '<option value="access"' + (mappingData.archimate_relationship_type === 'access' ? ' selected' : '') + '>Access</option>' +
                            '<option value="influence"' + (mappingData.archimate_relationship_type === 'influence' ? ' selected' : '') + '>Influence</option>' +
                        '</optgroup>' +
                        '<optgroup label="Dynamic">' +
                            '<option value="triggering"' + (mappingData.archimate_relationship_type === 'triggering' ? ' selected' : '') + '>Triggering</option>' +
                            '<option value="flow"' + (mappingData.archimate_relationship_type === 'flow' ? ' selected' : '') + '>Flow</option>' +
                        '</optgroup>' +
                        '<optgroup label="Other">' +
                            '<option value="specialization"' + (mappingData.archimate_relationship_type === 'specialization' ? ' selected' : '') + '>Specialization</option>' +
                            '<option value="association"' + (mappingData.archimate_relationship_type === 'association' ? ' selected' : '') + '>Association</option>' +
                        '</optgroup>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Access Mode</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="archimate_access_mode">' +
                        '<option value="unspecified"' + (mappingData.archimate_access_mode === 'unspecified' ? ' selected' : '') + '>Unspecified</option>' +
                        '<option value="read"' + (mappingData.archimate_access_mode === 'read' ? ' selected' : '') + '>Read</option>' +
                        '<option value="write"' + (mappingData.archimate_access_mode === 'write' ? ' selected' : '') + '>Write</option>' +
                        '<option value="read_write"' + (mappingData.archimate_access_mode === 'read_write' ? ' selected' : '') + '>Read/Write</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    // APQC fields
    if (context === 'apqc') {
        html += '<div class="mt-3 pt-3 border-t border-emerald-200 bg-emerald-500/5 rounded p-3">' +
            '<div class="flex items-center mb-2">' +
                '<i data-lucide="workflow" class="w-4 h-4 text-emerald-600 mr-2"></i>' +
                '<span class="text-xs font-semibold text-green-800">APQC Process Details</span>' +
            '</div>' +
            '<div class="grid grid-cols-2 md:grid-cols-4 gap-4">' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Automation (1-5)</label>' +
                    '<input type="number" min="1" max="5" value="' + (mappingData.automation_level || 1) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="automation_level" data-umm-cast="int"/>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Contribution %</label>' +
                    '<input type="number" min="0" max="100" value="' + (mappingData.process_contribution || 50) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="process_contribution" data-umm-cast="int"/>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Role</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="application_role">' +
                        '<option value="primary"' + (mappingData.application_role === 'primary' ? ' selected' : '') + '>Primary</option>' +
                        '<option value="secondary"' + (mappingData.application_role === 'secondary' ? ' selected' : '') + '>Secondary</option>' +
                        '<option value="supporting"' + (mappingData.application_role === 'supporting' ? ' selected' : '') + '>Supporting</option>' +
                        '<option value="enabling"' + (mappingData.application_role === 'enabling' ? ' selected' : '') + '>Enabling</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Criticality</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="process_criticality">' +
                        '<option value="critical"' + (mappingData.process_criticality === 'critical' ? ' selected' : '') + '>Critical</option>' +
                        '<option value="high"' + (mappingData.process_criticality === 'high' ? ' selected' : '') + '>High</option>' +
                        '<option value="medium"' + (mappingData.process_criticality === 'medium' ? ' selected' : '') + '>Medium</option>' +
                        '<option value="low"' + (mappingData.process_criticality === 'low' ? ' selected' : '') + '>Low</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    // Vendor fields
    if (context === 'vendor') {
        html += '<div class="mt-3 pt-3 border-t border-primary/20 bg-primary/5 rounded p-3">' +
            '<div class="flex items-center mb-2">' +
                '<i data-lucide="building-2" class="w-4 h-4 text-primary mr-2"></i>' +
                '<span class="text-xs font-semibold text-primary/90">Vendor Product Details</span>' +
            '</div>' +
            '<div class="grid grid-cols-2 md:grid-cols-3 gap-4">' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Implementation</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="implementation_status">' +
                        '<option value="planned"' + (mappingData.implementation_status === 'planned' ? ' selected' : '') + '>Planned</option>' +
                        '<option value="in_progress"' + (mappingData.implementation_status === 'in_progress' ? ' selected' : '') + '>In Progress</option>' +
                        '<option value="deployed"' + (mappingData.implementation_status === 'deployed' ? ' selected' : '') + '>Deployed</option>' +
                        '<option value="retired"' + (mappingData.implementation_status === 'retired' ? ' selected' : '') + '>Retired</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">License</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="license_type">' +
                        '<option value="perpetual"' + (mappingData.license_type === 'perpetual' ? ' selected' : '') + '>Perpetual</option>' +
                        '<option value="subscription"' + (mappingData.license_type === 'subscription' ? ' selected' : '') + '>Subscription</option>' +
                        '<option value="open_source"' + (mappingData.license_type === 'open_source' ? ' selected' : '') + '>Open Source</option>' +
                        '<option value="custom"' + (mappingData.license_type === 'custom' ? ' selected' : '') + '>Custom</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Deployment</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="deployment_model">' +
                        '<option value="on_premise"' + (mappingData.deployment_model === 'on_premise' ? ' selected' : '') + '>On-Premise</option>' +
                        '<option value="cloud"' + (mappingData.deployment_model === 'cloud' ? ' selected' : '') + '>Cloud</option>' +
                        '<option value="hybrid"' + (mappingData.deployment_model === 'hybrid' ? ' selected' : '') + '>Hybrid</option>' +
                        '<option value="saas"' + (mappingData.deployment_model === 'saas' ? ' selected' : '') + '>SaaS</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Contract</label>' +
                    '<select class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="contract_status">' +
                        '<option value="active"' + (mappingData.contract_status === 'active' ? ' selected' : '') + '>Active</option>' +
                        '<option value="expiring"' + (mappingData.contract_status === 'expiring' ? ' selected' : '') + '>Expiring Soon</option>' +
                        '<option value="expired"' + (mappingData.contract_status === 'expired' ? ' selected' : '') + '>Expired</option>' +
                        '<option value="negotiating"' + (mappingData.contract_status === 'negotiating' ? ' selected' : '') + '>Negotiating</option>' +
                    '</select>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">Annual Cost ($)</label>' +
                    '<input type="number" min="0" value="' + (mappingData.annual_cost || 0) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="annual_cost" data-umm-cast="int"/>' +
                '</div>' +
                '<div>' +
                    '<label class="block text-xs font-medium text-foreground mb-1">User Count</label>' +
                    '<input type="number" min="0" value="' + (mappingData.user_count || 0) + '" class="w-full text-sm border border-border rounded px-2 py-1" data-umm-app="' + appIdAttr + '" data-umm-field="user_count" data-umm-cast="int"/>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    html += '</div>';
    return html;
}

window.toggleUnifiedApplicationSelection = function(appId) {
    let app = UnifiedMappingModal.applicationsData.find(function(a) { return String(a.id) === String(appId); });
    if (!app) return;

    if (UnifiedMappingModal.selectedApplications.has(appId)) {
        UnifiedMappingModal.selectedApplications.delete(appId);
    } else {
        UnifiedMappingModal.selectedApplications.set(appId, {
            application_id: appId,
            mapping_id: app.mapping_id || null,
            mapping: buildMappingData(app)
        });
    }

    renderUnifiedApplicationsList();
};

window.updateUnifiedApplicationMapping = function(appId, field, value) {
    if (!UnifiedMappingModal.selectedApplications.has(appId)) return;
    let appData = UnifiedMappingModal.selectedApplications.get(appId);
    appData.mapping[field] = value;
    UnifiedMappingModal.selectedApplications.set(appId, appData);
};

window.selectAllUnifiedFiltered = function() {
    let container = document.getElementById('unified-applications-list');
    if (!container) return;

    let checkboxes = container.querySelectorAll('input[type="checkbox"][data-app-id]:not(:checked)');
    checkboxes.forEach(function(checkbox) {
        let appId = checkbox.dataset.appId;
        if (appId && !UnifiedMappingModal.selectedApplications.has(appId)) {
            toggleUnifiedApplicationSelection(appId);
        }
    });
};

window.deselectAllUnified = function() {
    UnifiedMappingModal.selectedApplications.clear();
    renderUnifiedApplicationsList();
};

function _setUnifiedSaveButtonsDisabled(disabled) {
    ['unified-save-btn', 'unified-reverse-save-btn'].forEach(function(id) {
        let btn = document.getElementById(id);
        if (btn) btn.disabled = disabled;
    });
}

window.saveUnifiedMappings = async function() {
    // Guard against double-submit: rapid repeat clicks on Save must not fire
    // duplicate mapping-creation requests.
    if (UnifiedMappingModal._saving) return;
    UnifiedMappingModal._saving = true;
    _setUnifiedSaveButtonsDisabled(true);
    try {
        await _saveUnifiedMappingsInner();
    } finally {
        UnifiedMappingModal._saving = false;
        _setUnifiedSaveButtonsDisabled(false);
    }
};

async function _saveUnifiedMappingsInner() {
    // Handle reverse mode: Application -> Targets
    if (UnifiedMappingModal.reverseMode) {
        await saveReverseMappings();
        return;
    }

    // Standard mode: Target -> Applications
    if (!UnifiedMappingModal.targetId || UnifiedMappingModal.selectedApplications.size === 0) {
        if (typeof showNotification === 'function') {
            showNotification('Please select at least one application', 'warning');
        } else {
            Platform.toast.warning('Please select at least one application');
        }
        return;
    }

    try {
        let applications = Array.from(UnifiedMappingModal.selectedApplications.values());

        let url = UnifiedMappingModal.apiEndpoint + '/mappings';
        let bodyKey = 'capability_id';

        if (UnifiedMappingModal.context === 'apqc') {
            url = '/api/apqc/process-mappings';
            bodyKey = 'process_id';
        } else if (UnifiedMappingModal.context === 'archimate') {
            url = '/capability-map/api/save-archimate-mappings';
            bodyKey = 'element_id';
        } else if (UnifiedMappingModal.context === 'vendor') {
            url = '/api/vendors/application-mappings';
            bodyKey = 'vendor_product_id';
        }

        let body = {
            applications: applications,
            context: UnifiedMappingModal.context
        };
        body[bodyKey] = UnifiedMappingModal.targetId;

        if (UnifiedMappingModal.context === 'vendor' && UnifiedMappingModal.vendorProductId) {
            body.vendor_product_id = UnifiedMappingModal.vendorProductId;
        }

        let data = await Platform.fetch.post(url, body);

        if (data.error) {
            if (typeof showNotification === 'function') {
                showNotification('Error saving mappings: ' + data.error, 'error');
            } else {
                Platform.toast.error('Error saving mappings: ' + data.error);
            }
            return;
        }

        let createdMsg = data.created > 0 ? 'created ' + data.created + ' new' : '';
        let updatedMsg = data.updated > 0 ? 'updated ' + data.updated : '';
        let msg = [createdMsg, updatedMsg].filter(Boolean).join(' and ') || 'saved';

        if (typeof showNotification === 'function') {
            showNotification('Successfully ' + msg + ' mapping(s)', 'success');
        }

        if (UnifiedMappingModal.onSaveCallback) {
            UnifiedMappingModal.onSaveCallback(data);
        }

        closeUnifiedMappingModal();
    } catch (error) {
        if (typeof showNotification === 'function') {
            showNotification('Error saving mappings', 'error');
        } else {
            Platform.toast.error('Error saving mappings');
        }
    }
};

// Save mappings in reverse mode: Application -> Targets
async function saveReverseMappings() {
    let selectedTargets = Array.from(UnifiedMappingModal.selectedTargets || []);

    if (selectedTargets.length === 0) {
        Platform.toast.warning('Please select at least one capability to map');
        return;
    }

    try {
        let successCount = 0;
        let errorCount = 0;

        // Create a mapping for each selected target to the application
        for (let i = 0; i < selectedTargets.length; i++) {
            let targetId = selectedTargets[i];
            let body = {
                capability_id: parseInt(targetId),
                applications: [{
                    application_id: parseInt(UnifiedMappingModal.reverseAppId),
                    support_level: 'partial',
                    coverage_percentage: 50,
                    support_quality: 3,
                    relationship_type: 'enables',
                    is_active: true
                }]
            };

            try {
                let data = await Platform.fetch.post(UnifiedMappingModal.apiEndpoint + '/mappings', body, { silent: true });
                if (data.success || data.created > 0) {
                    successCount++;
                } else {
                    errorCount++;
                }
            } catch (error) {
                errorCount++;
            }
        }

        if (successCount > 0) {
            Platform.toast.success('Successfully mapped ' + successCount + ' capability(s) to ' + UnifiedMappingModal.reverseAppName);
            // A partial failure used to be reported as an unqualified success.
            if (errorCount > 0) {
                Platform.toast.error(errorCount + ' capability(s) were NOT mapped — retry those.');
            }
            if (UnifiedMappingModal.onSaveCallback) {
                UnifiedMappingModal.onSaveCallback({ success: true, created: successCount });
            }
            closeUnifiedMappingModal();
        } else {
            Platform.toast.error('Failed to save mappings. Please try again.');
        }
    } catch (error) {
        Platform.toast.error('Error saving mappings: ' + error.message);
    }
}

window.deleteUnifiedMapping = async function(mappingId, appId) {
    if (!(await Platform.modal.confirm('Are you sure you want to remove this mapping?'))) {
        return;
    }

    try {
        let url = UnifiedMappingModal.apiEndpoint + '/mappings/' + mappingId;

        if (UnifiedMappingModal.context === 'apqc') {
            url = '/api/apqc/process-mappings/' + mappingId;
        } else if (UnifiedMappingModal.context === 'archimate') {
            url = '/capability-map/api/archimate-mappings/' + mappingId;
        } else if (UnifiedMappingModal.context === 'vendor') {
            url = '/api/vendors/application-mappings/' + mappingId;
        }

        let data = await Platform.fetch.delete(url);

        if (data.error) {
            if (typeof showNotification === 'function') {
                showNotification('Error deleting mapping: ' + data.error, 'error');
            }
            return;
        }

        UnifiedMappingModal.selectedApplications.delete(appId);
        await loadUnifiedApplications(UnifiedMappingModal.targetId);

        if (typeof showNotification === 'function') {
            showNotification('Mapping removed successfully', 'success');
        }
    } catch (error) {
        if (typeof showNotification === 'function') {
            showNotification('Error deleting mapping. Please try again.', 'error');
        } else {
            Platform.toast.error('Error deleting mapping. Please try again.');
        }
    }
};

// ── Delegated listeners (CSP) ───────────────────────────────────────────────
// The app ships script-src 'self' 'nonce-…' 'strict-dynamic' with no
// 'unsafe-inline'/'unsafe-hashes', so an on*= attribute never executes however
// it reaches the DOM. Worse, this markup is inserted via safeHTML(), and
// core/02-sanitize.js FORBID_ATTRs onclick/onchange outright -- the attributes
// were being stripped before the CSP even got a say. Every settings control in
// renderUnifiedApplicationSettings() was therefore inert for its whole life:
// changing Support Level, Coverage %, Relationship, the ArchiMate/APQC/vendor
// fields etc. never reached updateUnifiedApplicationMapping(), so the mapping
// saved whatever buildMappingData() defaulted to.
//
// Bound once at document level so it survives renderUnifiedApplicationsList()
// re-rendering the list from fetched data.
document.addEventListener('change', function(event) {
    let el = event.target.closest('[data-umm-app][data-umm-field]');
    if (!el) return;
    let value = el.value;
    if (el.getAttribute('data-umm-cast') === 'int') {
        value = parseInt(el.value, 10);
        if (isNaN(value)) return;
    }
    updateUnifiedApplicationMapping(el.getAttribute('data-umm-app'), el.getAttribute('data-umm-field'), value);
});

// Target-card selection. The cards carry data-action="selectTarget" with
// data-params, but nothing in the shipped JS dispatches that pair for this
// modal (capability_map/index.js has a [data-action] switch with no
// selectTarget case), so clicking a card -- cursor-pointer, hover highlight --
// did nothing. This is the dispatcher.
document.addEventListener('click', function(event) {
    // The reverse-mode checkbox sits inside the card and used to carry
    // onclick="event.stopPropagation()" -- itself stripped by the sanitizer and
    // refused by the CSP. It is deliberately allowed to fall through to the
    // card handler now: toggleTargetSelection() re-renders the card with the
    // correct checked state, so checkbox and card agree.
    let card = event.target.closest('#unified-targets-list [data-action="selectTarget"]');
    if (!card) return;
    let params;
    try {
        params = JSON.parse(card.getAttribute('data-params') || '[]');
    } catch (err) {
        return;
    }
    if (!params.length) return;
    selectTarget(params[0], params[1]);
});

// Close modal on backdrop click — runs unconditionally (script loaded after DOM element)
(function() {
    let modal = document.getElementById('unified-mapping-modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                closeUnifiedMappingModal();
            }
        });
    }
})();
