/**
 * enterprise/work_packages_table.js — Work Packages Table Alpine component.
 *
 * Extends Platform.dataTable.mixin() with:
 *   - Inline edit modal for work package fields
 *   - Bulk delete with typed confirmation
 *   - Status / priority badge helpers
 *   - Progress bar width helper
 *
 * Requires: Platform.dataTable (components/data_table.js), Platform.fetch, Platform.toast, Platform.modal
 */
(function () {
    'use strict';

    Platform.require('fetch', 'toast', 'modal', 'dataTable');

    const STATUS_OPTIONS   = ['Planned', 'In Progress', 'Completed', 'Blocked', 'On Hold'];
    const PRIORITY_OPTIONS = ['Critical', 'High', 'Normal', 'Low'];

    document.addEventListener('alpine:init', function () {
        Alpine.data('workPackagesTable', function () {
            // Platform.dataTable.extend, not Object.assign: the mixin's computed
            // getters (selectedCount, hasSelection, allPageSelected,
            // hasActiveFilters) are non-enumerable and Object.assign drops them.
            return Platform.dataTable.extend(
                Platform.dataTable.mixin({
                    apiUrl:     '/enterprise/api/work-packages',
                    perPage:    25,
                    itemsKey:   'work_packages',
                    storageKey: 'work-packages'
                }),
                {
                    // ── Edit form state ───────────────────────────────
                    editForm: {
                        name:             '',
                        summary:          '',
                        status:           'Planned',
                        priority:         'Normal',
                        percent_complete: 0,
                        target_date:      ''
                    },
                    editingId:  null,
                    saveError:  '',
                    editSaving: false,

                    // ── Create form state ─────────────────────────────
                    // The create modal in enterprise/work_packages.html binds
                    // createForm.*, createError and createSaving, and its Create
                    // button calls submitCreateWorkPackage(). None of it existed on
                    // this component, so every click threw
                    // "submitCreateWorkPackage is not a function" from the Alpine
                    // CSP adapter and no request was ever attempted -- silently:
                    // no toast, no validation message, the modal simply did nothing.
                    // The POST endpoint it needs has been there all along
                    // (/enterprise/api/work-packages, PROD-008).
                    createForm: {
                        name:             '',
                        summary:          '',
                        status:           'Planned',
                        priority:         'Normal',
                        percent_complete: 0,
                        target_date:      ''
                    },
                    createError:  '',
                    createSaving: false,

                    // ── Bulk delete state ─────────────────────────────
                    bulkConfirmText:  '',
                    deleteInProgress: false,

                    // ── Options lists exposed to templates ────────────
                    statusOptions:   STATUS_OPTIONS,
                    priorityOptions: PRIORITY_OPTIONS,

                    // ── Init ──────────────────────────────────────────
                    init: function () {
                        this._tableInit();
                    },

                    // ── Edit modal ─────────────────────────────────────
                    openEditModal: function (row) {
                        this.editingId                    = row.id;
                        this.editForm.name                = row.name             || '';
                        this.editForm.summary             = row.summary          || '';
                        this.editForm.status              = row.status           || 'Planned';
                        this.editForm.priority            = row.priority         || 'Normal';
                        this.editForm.percent_complete    = row.percent_complete != null ? row.percent_complete : '';
                        this.editForm.target_date         = row.target_date      || '';
                        this.saveError  = '';
                        this.editSaving = false;
                        Platform.modal.open('edit-work-package-modal');
                    },

                    openCreateModal: function () {
                        this.createForm.name             = '';
                        this.createForm.summary          = '';
                        this.createForm.status           = 'Planned';
                        this.createForm.priority         = 'Normal';
                        this.createForm.percent_complete = 0;
                        this.createForm.target_date      = '';
                        this.createError  = '';
                        this.createSaving = false;
                        Platform.modal.open('create-work-package-modal');
                    },

                    submitCreateWorkPackage: function () {
                        if (this.createSaving) return;
                        // Name is required by the endpoint (MISSING_NAME). Check it
                        // here too so the user gets the message beside the field
                        // rather than a round trip.
                        if (!String(this.createForm.name || '').trim()) {
                            this.createError = 'Name is required.';
                            return;
                        }
                        this.createSaving = true;
                        this.createError  = '';
                        const self = this;
                        // silent: the modal paints its own inline createError, so a
                        // global toast would duplicate it.
                        Platform.fetch.post('/enterprise/api/work-packages', this.createForm, { silent: true })
                            .then(function (data) {
                                if (data && data.error) {
                                    self.createError = data.error;
                                    return;
                                }
                                Platform.modal.close('create-work-package-modal');
                                self.refresh();
                                Platform.toast.success('Work package created.');
                            })
                            .catch(function (err) {
                                self.createError = 'Create failed.';
                                // Never swallow: the caller sees the inline message,
                                // the console keeps the cause.
                                throw err;
                            })
                            .finally(function () {
                                self.createSaving = false;
                            });
                    },

                    saveWorkPackage: function () {
                        if (this.editSaving) return;
                        this.editSaving = true;
                        this.saveError  = '';
                        const self = this;
                        // The edit modal paints its own inline error state (saveError),
                        // so we should suppress the global toast to avoid duplicate messages.
                        Platform.fetch.patch('/enterprise/api/work-packages/' + this.editingId, this.editForm, { silent: true })
                            .then(function (data) {
                                // Platform.fetch returns parsed data directly; no need to check response.ok.
                                // The existing code expected a possible top-level `error` property in the JSON.
                                // Preserve that check.
                                if (data && data.error) {
                                    self.saveError = data.error;
                                } else {
                                    Platform.modal.close('edit-work-package-modal');
                                    self.refresh();
                                    Platform.toast.success('Work package updated.');
                                }
                            })
                            .catch(function (err) {
                                // Platform.fetch did not show a toast because we used silent:true.
                                // The existing code set a generic 'Save failed.' message.
                                // We'll keep that behaviour.
                                self.saveError = 'Save failed.';
                                // Re-throw to satisfy rule 2 (never swallow an error).
                                throw err;
                            })
                            .finally(function () {
                                self.editSaving = false;
                            });
                    },

                    // ── Bulk delete ────────────────────────────────────
                    confirmBulkDelete: function () {
                        this.bulkConfirmText = '';
                        // Must match the modal id in enterprise/work_packages.html
                        // ('bulk-delete-confirm-modal'). It named a modal that does
                        // not exist, so "Delete selected" opened nothing — the inert
                        // control the 2 Sep 2026 audit reported (F-06).
                        Platform.modal.open('bulk-delete-confirm-modal');
                    },

                    // Per-row delete (audit F-06: rows had no delete at all). Reuses
                    // the typed-DELETE confirm flow so a single row gets the same
                    // safety as a bulk selection, with no second modal to maintain.
                    deleteRow: function (row) {
                        this._selectedIds = [row.id];
                        this.confirmBulkDelete();
                    },

                    executeBulkDelete: async function () {
                        if (!this.bulkDeleteEnabled()) return;
                        this.deleteInProgress = true;
                        try {
                            // Platform.fetch.delete returns parsed data directly; throws on non-ok.
                            // The existing code expected a possible top-level `error` property.
                            // We'll preserve that check.
                            // The bulk delete operation does not have its own inline error state,
                            // but the existing catch block painted a toast. To avoid duplicate
                            // toasts (Platform.fetch shows one by default), we pass silent:true
                            // and then show our own toast in the catch to preserve existing behavior.
                            const data = await Platform.fetch.delete('/enterprise/api/work-packages/bulk', {
                                body: { ids: this._selectedIds.slice() },
                                silent: true
                            });
                            if (data && data.error) {
                                // The response was ok but contained an `error` property.
                                // This is an inline error state, so we show a toast.
                                Platform.toast.error(data.error);
                            } else {
                                // DEF-066, Capgemini dry-run: this closed
                                // 'bulk-delete-work-package-modal', a modal id
                                // that does not exist anywhere in the template
                                // (the real one is 'bulk-delete-confirm-modal',
                                // used to open it in confirmBulkDelete() above) —
                                // harmless once the real blocker (below) is fixed,
                                // but the modal would have stayed open regardless.
                                Platform.modal.close('bulk-delete-confirm-modal');
                                this.bulkConfirmText = '';
                                this.clearSelection();
                                this._loadItems();
                                Platform.toast.success('Work packages deleted.');
                            }
                        } catch (e) {
                            // Platform.fetch did not show a toast because we used silent:true.
                            // The existing code painted a toast with e.message.
                            // We'll do the same to preserve behavior.
                            Platform.toast.error((e && e.message) || 'Delete failed.');
                            // Re-throw to satisfy rule 2 (never swallow an error).
                            throw e;
                        } finally {
                            this.deleteInProgress = false;
                        }
                    },

                    bulkDeleteEnabled: function () {
                        return this.bulkConfirmText === 'DELETE' && this._selectedIds.length > 0;
                    },

                    selectionCount: function () {
                        if (this.selectAllState === 'cross-page') return this.totalItems;
                        return this._selectedIds.length;
                    },

                    // ── Badge helpers ──────────────────────────────────
                    statusClass: function (status) {
                        if (status === 'Completed')  return 'bg-success/20 text-success';
                        if (status === 'In Progress') return 'bg-primary/20 text-primary';
                        if (status === 'Blocked')    return 'bg-destructive/20 text-destructive';
                        return 'bg-muted text-muted-foreground';
                    },

                    priorityClass: function (priority) {
                        if (priority === 'Critical') return 'bg-destructive/20 text-destructive';
                        if (priority === 'High')     return 'bg-warning/20 text-warning';
                        return 'bg-muted text-muted-foreground';
                    },

                    progressWidth: function (pct) {
                        return Platform.figure.percent(Platform.figure.missing(pct) ? pct : Math.min(100, Math.max(0, pct)));
                    }
                }
            );
        });
    });
}());
