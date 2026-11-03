/**
 * Add Applications to Consolidation List Modal - External JavaScript
 * Extracted from app/templates/consolidation_list/add_applications_modal.html
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

// Global state for add applications modal
let allAvailableApps = [];
let selectedAppIds = new Set();
let appSearchTimeout = null;

// Open modal
function openAddApplicationsModal() {
    Platform.modal.open('add-applications-modal');
    loadAvailableApplications();
}

// Close modal
function closeAddApplicationsModal() {
    Platform.modal.close('add-applications-modal');
    allAvailableApps = [];
    selectedAppIds.clear();
}

// Load available applications (not already in list)
function loadAvailableApplications() {
    // Get current consolidation list entries to exclude them
    let currentAppIds = allEntries.map(function(e) { return e.application_id; });

    Platform.fetch('/capability-map/api/applications')
        .then(function(data) {
            if (data.applications) {
                // Filter out applications already in consolidation list
                allAvailableApps = data.applications.filter(function(app) {
                    return !currentAppIds.includes(app.id);
                });

                // Populate department filter
                let deptSelect = document.getElementById('app-filter-department');
                let departments = [];
                let seen = {};
                allAvailableApps.forEach(function(a) {
                    if (a.department && !seen[a.department]) {
                        seen[a.department] = true;
                        departments.push(a.department);
                    }
                });
                departments.sort();
                departments.forEach(function(dept) {
                    let option = document.createElement('option');
                    option.value = dept;
                    option.textContent = dept;
                    deptSelect.appendChild(option);
                });

                displayAvailableApplications(allAvailableApps);
            } else {
                safeHTML(document.getElementById('app-list-container'),
                    '<div class="p-8 text-center text-destructive">Error loading applications. Please refresh.</div>');
            }
        })
        .catch(function(error) {
            // Platform.fetch already shows a toast unless silent:true, but we also need to paint inline error.
            // The existing inline error state is painted via safeHTML below.
            // We keep silent:false (default) because there is no duplicate toast issue (no other error display).
            safeHTML(document.getElementById('app-list-container'),
                '<div class="p-8 text-center text-destructive">Error loading applications.</div>');
        });
}

// Display applications in list
function displayAvailableApplications(apps) {
    if (apps.length === 0) {
        safeHTML(document.getElementById('app-list-container'),
            '<div class="p-8 text-center text-muted-foreground">No applications available</div>');
        return;
    }

    let html = apps.map(function(app) {
        return '<div class="border-b last:border-b-0 p-4 hover:bg-primary/5 transition cursor-pointer" data-action="toggleAppSelection" data-id="' + app.id + '">' +
            '<div class="flex items-start gap-3">' +
                '<input type="checkbox" class="app-checkbox mt-1" value="' + app.id + '" id="app-' + app.id + '" ' +
                    'onchange="updateSelectedApps()" ' + (selectedAppIds.has(app.id) ? 'checked' : '') + '>' +
                '<div class="flex-1">' +
                    '<div class="font-medium text-foreground">' + escapeHtml(app.name || 'Unknown') + '</div>' +
                    '<div class="text-sm text-muted-foreground mt-1">' +
                        'ID: ' + app.id + ' | Department: ' + escapeHtml(app.department || 'N/A') +
                    '</div>' +
                    '<div class="text-sm text-muted-foreground">' +
                        'Status: <span class="px-2 py-1 rounded-full text-xs ' + getStatusBgColor(app.status) + '">' +
                            escapeHtml(app.status || 'Unknown') +
                        '</span>' +
                    '</div>' +
                    (app.total_cost_of_ownership ?
                        '<div class="text-sm text-emerald-600 mt-1 font-medium">' +
                            'Annual Cost: \u00a3' + parseFloat(app.total_cost_of_ownership).toLocaleString() +
                        '</div>'
                    : '') +
                '</div>' +
            '</div>' +
        '</div>';
    }).join('');

    safeHTML(document.getElementById('app-list-container'), html);
}

// Toggle app selection
function toggleAppSelection(appId) {
    let checkbox = document.getElementById('app-' + appId);
    if (checkbox.checked) {
        selectedAppIds.add(appId);
    } else {
        selectedAppIds.delete(appId);
    }
    updateSelectedApps();
}

// Update selected apps count and button state
function updateSelectedApps() {
    let checkboxes = document.querySelectorAll('.app-checkbox:checked');
    selectedAppIds.clear();
    checkboxes.forEach(function(cb) { selectedAppIds.add(parseInt(cb.value)); });

    let count = selectedAppIds.size;
    document.getElementById('selected-count').textContent = count;
    document.getElementById('add-submit-btn').disabled = count === 0;

    if (count > 0) {
        document.getElementById('footer-info').textContent =
            'Ready to add ' + count + ' application' + (count !== 1 ? 's' : '');
    }
}

// Filter applications
function filterApplications() {
    let searchTerm = document.getElementById('app-search-input').value.toLowerCase();
    let department = document.getElementById('app-filter-department').value;
    let costRange = document.getElementById('app-filter-cost').value;
    let statusFilter = document.getElementById('app-filter-status').value;

    let filtered = allAvailableApps.filter(function(app) {
        // Search filter
        if (searchTerm) {
            let matchesSearch = (app.name && app.name.toLowerCase().includes(searchTerm)) ||
                                (app.id && app.id.toString().includes(searchTerm)) ||
                                (app.department && app.department.toLowerCase().includes(searchTerm));
            if (!matchesSearch) return false;
        }

        // Department filter
        if (department && app.department !== department) return false;

        // Status filter
        if (statusFilter && app.status !== statusFilter) return false;

        // Cost range filter
        if (costRange) {
            let cost = parseFloat(app.total_cost_of_ownership);
            let parts = costRange.split('-').map(Number);
            if (Number.isNaN(cost) || cost < parts[0] || cost > parts[1]) return false;
        }

        return true;
    });

    displayAvailableApplications(filtered);
}

// Clear search
function clearSearchFilter() {
    document.getElementById('app-search-input').value = '';
    document.getElementById('app-filter-department').value = '';
    document.getElementById('app-filter-cost').value = '';
    document.getElementById('app-filter-status').value = '';
    displayAvailableApplications(allAvailableApps);
}

// Submit selected applications
function submitAddApplications() {
    if (selectedAppIds.size === 0) {
        Platform.toast.warning('Please select at least one application');
        return;
    }

    let appIds = Array.from(selectedAppIds);

    Platform.fetch.post('/consolidation-list/api/add', {
        application_ids: appIds,
        source_type: 'manual',
        source_group_name: 'Manual Selection'
    })
        .then(function(data) {
            if (data.success) {
                Platform.toast.success('Added ' + data.added_count + ' application(s) to consolidation list');
                closeAddApplicationsModal();
                loadEntries(); // Reload the main list
            } else {
                // The server returned a 200 with success:false, which Platform.fetch does not treat as an error.
                // We must still show an error toast with the server's message.
                Platform.toast.error('Error: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(function(error) {
            // Platform.fetch already shows a toast for non‑ok responses (unless silent:true).
            // We keep silent:false (default) because there is no duplicate toast issue.
            // No console.error allowed; the error is already surfaced to the user via Platform.fetch's toast.
            Platform.toast.error('Error adding applications. Please try again.');
        });
}

// Helper: Get status background color
function getStatusBgColor(status) {
    let colors = {
        '2.1 STRATEGIC': 'bg-success/10 text-success',
        '2.2 TACTICAL': 'bg-primary/10 text-primary',
        'operational': 'bg-success/10 text-success',
        '3. SUNSET': 'bg-warning/10 text-warning',
        '4.1 DECOM DECIDED': 'bg-destructive/10 text-destructive',
        '4.2 DECOM PLANNED': 'bg-destructive/10 text-destructive',
        '4.3 READ-ONLY': 'bg-warning/10 text-warning',
        '5. DECOMMISSIONED': 'bg-muted text-muted-foreground',
        '1. UNDETERMINED': 'bg-muted text-muted-foreground',
        'deprecated': 'bg-destructive/10 text-destructive',
    };
    return colors[status] || 'bg-muted text-foreground';
}
