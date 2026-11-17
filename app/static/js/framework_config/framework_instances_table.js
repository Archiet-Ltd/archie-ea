/**
 * framework_config/framework_instances_table.js — Framework Instances data table Alpine component.
 *
 * Extends Platform.dataTable.mixin() with:
 *   - Inline Edit modal for framework instance fields
 *   - Bulk delete with typed confirmation
 *   - Maturity level label/class helpers
 *   - Status badge and progress bar helpers
 *
 * Requires: Platform.dataTable (components/data_table.js), Platform.fetch, Platform.toast, Platform.modal
 */
(function () {
    'use strict';

    Platform.require('fetch', 'toast', 'modal', 'dataTable');

    const MATURITY_LABELS = {
        1: 'Initial',
        2: 'Developing',
        3: 'Defined',
        4: 'Managed',
        5: 'Optimizing'
    };

    const MATURITY_CLASSES = {
        1: 'bg-destructive/10 text-red-800 dark:bg-red-900/30 dark:text-red-300',
        2: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
        3: 'bg-amber-500/10 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300',
        4: 'bg-primary/10 text-primary/90 dark:bg-blue-900/30 dark:text-blue-300',
        5: 'bg-emerald-500/10 text-green-800 dark:bg-green-900/30 dark:text-green-300'
    };

    const STATUS_CLASSES = {
        operational:   'bg-emerald-500/10 text-green-800 dark:bg-green-900/30 dark:text-green-300',
        implementing:  'bg-primary/10 text-primary/90 dark:bg-blue-900/30 dark:text-blue-300',
        optimizing:    'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
        retiring:      'bg-destructive/10 text-red-800 dark:bg-red-900/30 dark:text-red-300',
        planning:      'bg-muted text-muted-foreground'
    };

    document.addEventListener('alpine:init', function () {
        Alpine.data('frameworkInstancesTable', function () {
            // Platform.dataTable.extend, not Object.assign: the mixin's computed
            // getters (selectedCount, hasSelection, allPageSelected,
            // hasActiveFilters) are non-enumerable and Object.assign drops them.
            return Platform.dataTable.extend(
                Platform.dataTable.mixin({
                    apiUrl: '/framework-config/api/instances',
                    perPage: 25,
                    itemsKey: 'instances',
                    storageKey: 'framework-instances'
                }),
                {
                    // ── Edit form state ───────────────────────────────
                    editForm: {
                        id: null,
                        instance_name: '',
                        instance_description: '',
                        organization_unit: '',
                        current_maturity_level: 1,
                        implementation_percentage: 0,
                        status: 'planning'
                    },
                    editSaving: false,
                    editError: '',

                    // ── Bulk delete state ─────────────────────────────
                    deleteConfirmText: '',
                    deleteInProgress: false,

                    // ── Init ──────────────────────────────────────────
                    init: function () {
                        this._tableInit();
                    },

                    // ── Edit modal ─────────────────────────────────────
                    openEditModal: function (row) {
                        this.editForm.id                       = row.id;
                        this.editForm.instance_name            = row.instance_name || '';
                        this.editForm.instance_description     = row.instance_description || '';
                        this.editForm.organization_unit        = row.organization_unit || '';
                        this.editForm.current_maturity_level   = row.current_maturity_level || 1;
                        this.editForm.implementation_percentage = row.implementation_percentage ?? '';
                        this.editForm.status                   = row.status || 'planning';
                        this.editError  = '';
                        this.editSaving = false;
                        Platform.modal.open('edit-instance-modal');
                    },

                    saveEdit: async function () {
                        if (this.editSaving) return;
                        this.editSaving = true;
                        this.editError  = '';
                        try {
                            const result = await Platform.fetch.patch(
                                '/framework-config/api/instances/' + this.editForm.id,
                                {
                                    instance_name:            this.editForm.instance_name,
                                    instance_description:     this.editForm.instance_description,
                                    organization_unit:        this.editForm.organization_unit,
                                    current_maturity_level:   this.editForm.current_maturity_level,
                                    implementation_percentage: this.editForm.implementation_percentage,
                                    status:                   this.editForm.status
                                }
                            );
                            if (result && result.success) {
                                Platform.modal.close('edit-instance-modal');
                                this._loadItems();
                                Platform.toast.success('Instance updated.');
                            } else {
                                this.editError = (result && result.error) || 'Save failed.';
                            }
                        } catch (e) {
                            this.editError = (e && e.message) || 'Network error.';
                        } finally {
                            this.editSaving = false;
                        }
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
                            await Platform.fetch('/framework-config/api/instances/bulk', {
                                method: 'DELETE',
                                body: { ids: this._selectedIds.slice() }
                            });
                            Platform.modal.close('bulk-delete-confirm-modal');
                            this.deleteConfirmText = '';
                            this.clearSelection();
                            this._loadItems();
                            Platform.toast.success('Instances deleted.');
                        } catch (e) {
                            Platform.toast.error((e && e.message) || 'Delete failed.');
                        } finally {
                            this.deleteInProgress = false;
                        }
                    },

                    // ── Helpers ────────────────────────────────────────
                    selectionCount: function () {
                        return this._selectedIds.length + ' instance(s)';
                    },

                    maturityLabel: function (level) {
                        return MATURITY_LABELS[level] || String(level);
                    },

                    maturityClass: function (level) {
                        return MATURITY_CLASSES[level] || 'bg-muted text-muted-foreground';
                    },

                    statusClass: function (status) {
                        return STATUS_CLASSES[status] || 'bg-muted text-muted-foreground';
                    },

                    progressWidth: function (pct) {
                        return Platform.figure.percent(Platform.figure.missing(pct) ? pct : Math.min(Math.max(pct, 0), 100));
                    }
                }
            );
        });
    });
}());
