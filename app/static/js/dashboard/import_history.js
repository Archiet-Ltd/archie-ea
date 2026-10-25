/**
 * Import History - External JavaScript
 * Extracted from app/templates/dashboard/import_history.html
 * Uses window.__APP_CONFIG__ bridge for server-side values
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

let importHistory = [];
let currentImport = null;
let rollbackImportId = null;

// Load import history on page load
document.addEventListener('DOMContentLoaded', function() {
    loadImportHistory();
});

function loadImportHistory() {
    // API /api/import-history not yet implemented — show empty state
    document.getElementById('loading').classList.add('hidden');
    importHistory = [];
    displayImportHistory();
    updateStatistics();
}

function showImportHistoryError(message) {
    let container = document.getElementById('import-history-list');
    safeHTML(container,
        '<div class="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">' +
            '<p class="font-semibold">Unable to load import history.</p>' +
            '<p class="mt-1">' + message + '</p>' +
            '<button type="button" data-import-history-retry class="mt-3 inline-flex items-center rounded bg-destructive px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-red-700">' +
                'Retry' +
            '</button>' +
        '</div>');
    if (typeof window.showToast === 'function') {
        window.showToast(message, 'error');
    }
}

function displayImportHistory() {
    let container = document.getElementById('import-history-list');
    let emptyState = document.getElementById('empty-state');

    if (importHistory.length === 0) {
        safeHTML(container, '');
        emptyState.classList.remove('hidden');
        return;
    }

    emptyState.classList.add('hidden');

    safeHTML(container, importHistory.map(function(item) {
        let statusLabel = item.status.charAt(0).toUpperCase() + item.status.slice(1);

        let batchJobHtml = '';
        if (item.batch_job_id) {
            batchJobHtml = '<span class="bg-purple-100 text-purple-800 px-2 py-1 rounded text-xs font-medium">' +
                '<i class="fas fa-tasks mr-1"></i>Batch Job' +
                '</span>';
        }

        let progressHtml = '';
        if (item.status === 'running') {
            let progress = item.progress || 0;
            progressHtml = '<div class="mb-3">' +
                '<div class="flex justify-between text-sm mb-1">' +
                    '<span>Progress</span>' +
                    '<span>' + progress + '%</span>' +
                '</div>' +
                '<div class="progress-bar">' +
                    '<div class="progress-fill" style="width: ' + progress + '%"></div>' +
                '</div>' +
            '</div>';
        }

        let errorHtml = '';
        if (item.error_details) {
            let errors = JSON.parse(item.error_details);
            let errorItems = errors.slice(0, 3).map(function(error) {
                return '<div>&bull; ' + error + '</div>';
            }).join('');
            let moreText = errors.length > 3 ? '<div class="text-xs">... and ' + (errors.length - 3) + ' more</div>' : '';
            errorHtml = '<div class="bg-destructive/5 border border-destructive/20 rounded-lg p-3 mb-3">' +
                '<h3 class="text-sm font-semibold text-red-800 mb-1">Error Details</h3>' +
                '<div class="text-sm text-destructive">' + errorItems + moreText + '</div>' +
            '</div>';
        }

        let aiStatsHtml = '';
        if (item.ai_analysis_stats) {
            let stats = item.ai_analysis_stats;
            aiStatsHtml = '<div class="bg-primary/5 border border-primary/20 rounded-lg p-3 mb-3">' +
                '<h3 class="text-sm font-semibold text-primary/90 mb-2">AI Analysis Results</h3>' +
                '<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">' +
                    '<div>' +
                        '<span class="text-muted-foreground">Capability Mappings:</span>' +
                        '<span class="font-medium ml-1">' + (stats.capability_mappings_found || 0) + '</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-muted-foreground">Process Mappings:</span>' +
                        '<span class="font-medium ml-1">' + (stats.process_mappings_found || 0) + '</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-muted-foreground">ArchiMate Elements:</span>' +
                        '<span class="font-medium ml-1">' + (stats.archimate_elements_generated || 0) + '</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-muted-foreground">Avg Confidence:</span>' +
                        '<span class="font-medium ml-1">' + Math.round((stats.avg_confidence || 0) * 100) + '%</span>' +
                    '</div>' +
                '</div>' +
            '</div>';
        }

        let progressButton = '';
        if (item.status === 'running') {
            progressButton = '<button data-action="viewProgress" data-id="' + item.batch_job_id + '" class="bg-primary text-primary-foreground px-3 py-1 rounded text-sm hover:bg-purple-700 transition-colors">' +
                '<i class="fas fa-chart-line mr-1"></i>Progress' +
            '</button>';
        }

        let rollbackButton = '';
        if (item.can_rollback) {
            rollbackButton = '<button data-action="rollbackImport" data-id="' + item.id + '" class="bg-orange-600 text-primary-foreground px-3 py-1 rounded text-sm hover:bg-orange-700 transition-colors">' +
                '<i class="fas fa-undo mr-1"></i>Rollback' +
            '</button>';
        }

        let retryButton = '';
        if (item.status === 'partial' && item.records_failed > 0) {
            retryButton = '<button data-action="retryFailed" data-id="' + item.id + '" class="bg-yellow-600 text-primary-foreground px-3 py-1 rounded text-sm hover:bg-yellow-700 transition-colors">' +
                '<i class="fas fa-redo mr-1"></i>Retry Failed' +
            '</button>';
        }

        return '<div class="import-item">' +
            '<div class="flex justify-between items-start">' +
                '<div class="flex-1">' +
                    '<div class="flex items-center space-x-3 mb-2">' +
                        '<h2 class="text-lg font-semibold text-foreground">' + item.file_name + '</h2>' +
                        '<span class="status-' + item.status + ' px-2 py-1 rounded text-xs font-medium">' +
                            statusLabel +
                        '</span>' +
                        batchJobHtml +
                    '</div>' +
                    '<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-3">' +
                        '<div class="text-sm">' +
                            '<span class="text-muted-foreground">Imported by:</span>' +
                            '<span class="font-medium ml-1">' + item.imported_by_name + '</span>' +
                        '</div>' +
                        '<div class="text-sm">' +
                            '<span class="text-muted-foreground">Date:</span>' +
                            '<span class="font-medium ml-1">' + formatDate(item.imported_at) + '</span>' +
                        '</div>' +
                        '<div class="text-sm">' +
                            '<span class="text-muted-foreground">Source:</span>' +
                            '<span class="font-medium ml-1">' + item.import_source + '</span>' +
                        '</div>' +
                        '<div class="text-sm">' +
                            '<span class="text-muted-foreground">Duration:</span>' +
                            '<span class="font-medium ml-1">' + formatDuration(item.processing_time_seconds) + '</span>' +
                        '</div>' +
                    '</div>' +
                    progressHtml +
                    '<div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3">' +
                        '<div class="text-center bg-emerald-500/5 rounded p-2">' +
                            '<div class="text-lg font-bold text-emerald-600">' + (item.records_created || 0) + '</div>' +
                            '<div class="text-xs text-muted-foreground">Created</div>' +
                        '</div>' +
                        '<div class="text-center bg-primary/5 rounded p-2">' +
                            '<div class="text-lg font-bold text-primary">' + (item.records_updated || 0) + '</div>' +
                            '<div class="text-xs text-muted-foreground">Updated</div>' +
                        '</div>' +
                        '<div class="text-center bg-amber-500/5 rounded p-2">' +
                            '<div class="text-lg font-bold text-amber-600">' + (item.records_skipped || 0) + '</div>' +
                            '<div class="text-xs text-muted-foreground">Skipped</div>' +
                        '</div>' +
                        '<div class="text-center bg-destructive/5 rounded p-2">' +
                            '<div class="text-lg font-bold text-destructive">' + (item.records_failed || 0) + '</div>' +
                            '<div class="text-xs text-muted-foreground">Failed</div>' +
                        '</div>' +
                        '<div class="text-center bg-muted/30 rounded p-2">' +
                            '<div class="text-lg font-bold text-muted-foreground">' + (item.total_records || 0) + '</div>' +
                            '<div class="text-xs text-muted-foreground">Total</div>' +
                        '</div>' +
                    '</div>' +
                    errorHtml +
                    aiStatsHtml +
                '</div>' +
                '<div class="flex space-x-2 ml-4">' +
                    '<button data-action="viewDetails" data-id="' + item.id + '" class="bg-primary text-primary-foreground px-3 py-1 rounded text-sm hover:bg-primary/90 transition-colors">' +
                        '<i class="fas fa-eye mr-1"></i>View' +
                    '</button>' +
                    progressButton +
                    rollbackButton +
                    retryButton +
                '</div>' +
            '</div>' +
        '</div>';
    }).join(''));
}

function updateStatistics() {
    let totalImports = importHistory.length;
    let totalCreated = importHistory.reduce(function(sum, item) { return sum + (item.records_created || 0); }, 0);
    let totalUpdated = importHistory.reduce(function(sum, item) { return sum + (item.records_updated || 0); }, 0);
    let totalFailed = importHistory.reduce(function(sum, item) { return sum + (item.records_failed || 0); }, 0);

    document.getElementById('total-imports').textContent = totalImports;
    document.getElementById('total-created').textContent = totalCreated;
    document.getElementById('total-updated').textContent = totalUpdated;
    document.getElementById('total-failed').textContent = totalFailed;
}

function formatDate(dateString) {
    let date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function formatDuration(seconds) {
    if (!seconds) return 'Not timed';
    if (seconds < 60) return Math.round(seconds) + 's';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm ' + Math.round(seconds % 60) + 's';
    return Math.round(seconds / 3600) + 'h ' + Math.round((seconds % 3600) / 60) + 'm';
}

function viewDetails(importId) {
    currentImport = importHistory.find(function(item) { return item.id === importId; });
    if (!currentImport) return;

    let modal = document.getElementById('import-details-modal');
    let content = document.getElementById('modal-content');

    let settingsHtml = '';
    if (currentImport.import_settings) {
        settingsHtml = '<div>' +
            '<h3 class="font-semibold text-foreground mb-2">Import Settings</h3>' +
            '<div class="bg-muted/30 rounded-lg p-3">' +
                '<pre class="text-sm text-foreground">' + JSON.stringify(JSON.parse(currentImport.import_settings), null, 2) + '</pre>' +
            '</div>' +
        '</div>';
    }

    let linkedAppsHtml = '';
    if (currentImport.linked_applications) {
        let linked = currentImport.linked_applications;
        linkedAppsHtml = '<div>' +
            '<h3 class="font-semibold text-foreground mb-2">Linked Applications</h3>' +
            '<div class="grid grid-cols-3 gap-4 text-sm">' +
                '<div class="bg-emerald-500/5 rounded p-3">' +
                    '<div class="font-medium text-green-800">' + ((linked.created_ids && linked.created_ids.length) || 0) + '</div>' +
                    '<div class="text-muted-foreground">Created</div>' +
                '</div>' +
                '<div class="bg-primary/5 rounded p-3">' +
                    '<div class="font-medium text-primary/90">' + ((linked.updated_ids && linked.updated_ids.length) || 0) + '</div>' +
                    '<div class="text-muted-foreground">Updated</div>' +
                '</div>' +
                '<div class="bg-muted/30 rounded p-3">' +
                    '<div class="font-medium text-foreground">' + (linked.total_processed || 0) + '</div>' +
                    '<div class="text-muted-foreground">Total Processed</div>' +
                '</div>' +
            '</div>' +
        '</div>';
    }

    safeHTML(content, '<div class="space-y-6">' +
        '<div>' +
            '<h3 class="font-semibold text-foreground mb-2">Import Information</h3>' +
            '<div class="grid grid-cols-2 gap-4 text-sm">' +
                '<div>' +
                    '<span class="text-muted-foreground">File Name:</span>' +
                    '<span class="font-medium ml-2">' + currentImport.file_name + '</span>' +
                '</div>' +
                '<div>' +
                    '<span class="text-muted-foreground">Import Source:</span>' +
                    '<span class="font-medium ml-2">' + currentImport.import_source + '</span>' +
                '</div>' +
                '<div>' +
                    '<span class="text-muted-foreground">Imported By:</span>' +
                    '<span class="font-medium ml-2">' + currentImport.imported_by_name + '</span>' +
                '</div>' +
                '<div>' +
                    '<span class="text-muted-foreground">Import Date:</span>' +
                    '<span class="font-medium ml-2">' + formatDate(currentImport.imported_at) + '</span>' +
                '</div>' +
                '<div>' +
                    '<span class="text-muted-foreground">Status:</span>' +
                    '<span class="font-medium ml-2">' + currentImport.status + '</span>' +
                '</div>' +
                '<div>' +
                    '<span class="text-muted-foreground">Duration:</span>' +
                    '<span class="font-medium ml-2">' + formatDuration(currentImport.processing_time_seconds) + '</span>' +
                '</div>' +
            '</div>' +
        '</div>' +
        settingsHtml +
        linkedAppsHtml +
    '</div>');

    Platform.modal.open('import-details-modal');
}

function closeDetailsModal() {
    Platform.modal.close('import-details-modal');
    currentImport = null;
}

function viewProgress(batchJobId) {
    if (!batchJobId) return;
    window.open('/api/batch/jobs/' + batchJobId + '/progress', '_blank');
}

function rollbackImport(importId) {
    rollbackImportId = importId;
    Platform.modal.open('rollback-modal');
}

function closeRollbackModal() {
    Platform.modal.close('rollback-modal');
    rollbackImportId = null;
}

function confirmRollback() {
    if (!rollbackImportId) return;

    Platform.fetch.post('/applications/rollback-import/' + rollbackImportId, null, { silent: true })
        .then(function(data) {
            // Platform.fetch returns parsed response directly; success is indicated by the absence of an exception.
            // The existing code expects a `success` property; we assume the API returns { success: true } on success.
            if (data && data.success) {
                Platform.toast.success('Import rolled back successfully');
                loadImportHistory();
            } else {
                // If the API returns a non‑ok response, Platform.fetch would have thrown before reaching here.
                // This branch handles a successful response with `success: false`.
                Platform.toast.error('Failed to rollback import: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(function(error) {
            // Platform.fetch already shows a toast unless silent:true; we passed silent:true to avoid duplicate toasts.
            // However, we still need to surface the failure to the user via the existing inline error path.
            // The existing code used Platform.toast.error in the catch; we preserve that behaviour.
            Platform.toast.error('Error rolling back import');
        })
        .finally(function() {
            closeRollbackModal();
        });
}

function retryFailed() {
    // API /api/import-history/.../retry-failed not yet implemented
    Platform.toast.info('Import history API is not yet available.');
}

function exportHistory() {
    let status = document.getElementById('status-filter').value;
    let dateFrom = document.getElementById('date-from').value;
    let dateTo = document.getElementById('date-to').value;

    let url = '/applications/export-import-history?';
    let params = new URLSearchParams();

    if (status) params.append('status', status);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);

    url += params.toString();

    window.open(url, '_blank');
}

function refreshHistory() {
    loadImportHistory();
}

function applyFilters() {
    loadImportHistory();
}

// Delegated, because the CSP refuses inline on*= attributes: the error state's
// Retry button carried onclick="refreshHistory()" and never fired. refreshHistory()
// exists (it calls loadImportHistory) so the control is wired to a live path.
document.addEventListener('click', function (event) {
    if (!event.target.closest('[data-import-history-retry]')) return;
    event.preventDefault();
    refreshHistory();
});
