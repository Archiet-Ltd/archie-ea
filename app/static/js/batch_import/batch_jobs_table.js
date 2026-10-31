/**
 * batch_import/batch_jobs_table.js — Batch Import Jobs data table Alpine component.
 *
 * Extends Platform.dataTable.mixin() with:
 *   - Status filter watcher (server-side via setFilter)
 *   - Bulk delete with typed confirmation
 *   - Status badge and cost display helpers
 *
 * Requires: Platform.dataTable (components/data_table.js), Platform.fetch, Platform.toast, Platform.modal
 */
(function () {
    'use strict';

    Platform.require('fetch', 'toast', 'modal', 'dataTable');

    const STATUS_CLASSES = {
        pending:    'bg-muted text-muted-foreground',
        processing: 'bg-primary/10 text-primary/90 dark:bg-blue-900/30 dark:text-blue-300',
        completed:  'bg-emerald-500/10 text-green-800 dark:bg-green-900/30 dark:text-green-300',
        failed:     'bg-destructive/10 text-red-800 dark:bg-red-900/30 dark:text-red-300',
        cancelled:  'bg-muted text-foreground dark:bg-gray-900/30 dark:text-foreground/70',
        paused:     'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
    };

    document.addEventListener('alpine:init', function () {
        Alpine.data('batchJobsTable', function () {
            // Platform.dataTable.extend, not Object.assign: the mixin's computed
            // getters (selectedCount, hasSelection, allPageSelected,
            // hasActiveFilters) are non-enumerable and Object.assign drops them.
            return Platform.dataTable.extend(
                Platform.dataTable.mixin({
                    apiUrl: '/api/batch-import/jobs',
                    perPage: 20,
                    itemsKey: 'jobs',
                    storageKey: 'batch-import-jobs'
                }),
                {
                    // ── Filter state ──────────────────────────────────
                    filterStatus: '',

                    // ── Bulk delete state ─────────────────────────────
                    deleteConfirmText: '',
                    deleteInProgress: false,

                    // ── Init ──────────────────────────────────────────
                    init: function () {
                        this._tableInit();
                        const self = this;
                        this.$watch('filterStatus', function (v) {
                            self.setFilter('status', v);
                        });
                    },

                    // ── Bulk delete ────────────────────────────────────
                    confirmBulkDelete: function () {
                        this.deleteConfirmText = '';
                        Platform.modal.open('bulk-delete-confirm-modal');
                    },

                    executeBulkDelete: async function () {
                        if (this.deleteConfirmText !== 'DELETE') return;
                        this.deleteInProgress = true;
                        try {
                            await Platform.fetch('/api/batch-import/jobs/bulk', {
                                method: 'DELETE',
                                body: { ids: this._selectedIds.slice() }
                            });
                            Platform.modal.close('bulk-delete-confirm-modal');
                            this.deleteConfirmText = '';
                            this.clearSelection();
                            this._loadItems();
                            Platform.toast.success('Jobs deleted.');
                        } catch (e) {
                            Platform.toast.error((e && e.message) || 'Delete failed.');
                        } finally {
                            this.deleteInProgress = false;
                        }
                    },

                    // ── Helpers ────────────────────────────────────────
                    selectionCount: function () {
                        return this._selectedIds.length + ' job(s)';
                    },

                    statusClass: function (status) {
                        return STATUS_CLASSES[status] || 'bg-muted text-muted-foreground';
                    },

                    costDisplay: function (cost) {
                        if (cost === null || cost === undefined) return '—';
                        return window.BatchMoney.format(parseFloat(cost));
                    }
                }
            );
        });
    });
}());
