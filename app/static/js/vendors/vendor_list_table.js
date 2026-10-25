/**
 * vendors/vendor_list_table.js — Vendor List Table Alpine component.
 *
 * Extends Platform.dataTable.mixin() with:
 *   - Inline edit modal for vendor fields
 *   - Bulk delete with typed confirmation
 *   - Vendor products panel (async load)
 *
 * Requires: Platform.dataTable (components/data_table.js), Platform.fetch, Platform.toast, Platform.modal
 */
(function () {
    'use strict';

    Platform.require('fetch', 'toast', 'modal', 'dataTable');

    document.addEventListener('alpine:init', function () {
        Alpine.data('vendorListTable', function () {
            // Platform.dataTable.extend, not Object.assign: the mixin's computed
            // getters (selectedCount, hasSelection, allPageSelected,
            // hasActiveFilters) are non-enumerable and Object.assign drops them.
            return Platform.dataTable.extend(
                Platform.dataTable.mixin({
                    apiUrl:     '/api/vendors/register',
                    perPage:    25,
                    itemsKey:   'vendors',
                    storageKey: 'vendor-list'
                }),
                {
                    // Which tab is showing: the vendors, or the systems still to be mapped.
                    tab: 'vendors',

                    // ── Edit form state ───────────────────────────────
                    editForm: {
                        name:           '',
                        vendor_type:    '',
                        country:        '',
                        website:        '',
                        description:    '',
                        status:         'active',
                        strategic_tier: ''
                    },
                    editingId:  null,
                    editError:  '',
                    saveError:  '',
                    editSaving: false,

                    // ── Bulk delete state ─────────────────────────────
                    deleteConfirmText: '',
                    bulkConfirmText:  '',
                    deleteInProgress: false,

                    // ── Vendor products panel state ───────────────────
                    selectedVendorName:     '',
                    selectedVendorProducts: [],
                    productsLoading:        false,

                    // ── Init ──────────────────────────────────────────
                    init: function () {
                        this._tableInit();
                    },

                    // ── Clear filters ─────────────────────────────────
                    // clearFilters() resets the table state; the toolbar's search box and selects are plain
                    // controls (not x-model), so reset what the user sees as well.
                    clearAllFilters: function () {
                        this.clearFilters();
                        // Scoped to the table's own card, not this.$root: $root also contains the
                        // vendor-mapping modal (components/unified_mapping_modal.html, included inside
                        // the same x-data), whose filter selects share the "Filter by …" aria-label
                        // convention. Querying $root directly blanked the modal's selects too, without
                        // their own @change handlers firing, leaving its Alpine state stale.
                        var card = this.$root.querySelector('[data-slot="card"]');
                        if (!card) return;
                        card.querySelectorAll('input[type="search"]').forEach(function (input) { input.value = ''; });
                        card.querySelectorAll('select[aria-label^="Filter by"]').forEach(function (select) { select.value = ''; });
                    },

                    // ── Edit modal ─────────────────────────────────────
                    openEditModal: function (row) {
                        this.editingId                 = row.id;
                        this.editForm.name             = row.name           || '';
                        this.editForm.vendor_type      = row.vendor_type    || '';
                        this.editForm.country          = row.country        || '';
                        this.editForm.website          = row.website        || '';
                        this.editForm.description      = row.description    || '';
                        this.editForm.status           = row.status         || 'active';
                        this.editForm.strategic_tier   = row.strategic_tier || '';
                        this.editError  = '';
                        this.saveError  = '';
                        this.editSaving = false;
                        Platform.modal.open('edit-vendor-modal');
                    },

                    saveEdit: async function () {
                        if (this.editSaving) return;
                        this.editSaving = true;
                        this.editError  = '';
                        this.saveError  = '';
                        try {
                            const result = await Platform.fetch.patch(
                                '/api/vendors/' + this.editingId,
                                this.editForm
                            );
                            if (result && result.success) {
                                Platform.modal.close('edit-vendor-modal');
                                this._loadItems();
                                Platform.toast.success('Vendor updated.');
                            } else {
                                this.editError = (result && result.error) || 'Save failed.';
                                this.saveError = this.editError;
                            }
                        } catch (e) {
                            this.editError = (e && e.message) || 'Network error.';
                            this.saveError = this.editError;
                        } finally {
                            this.editSaving = false;
                        }
                    },

                    saveVendor: async function () {
                        return this.saveEdit();
                    },

                    // ── Bulk delete ────────────────────────────────────
                    confirmBulkDelete: function () {
                        this.deleteConfirmText = '';
                        this.bulkConfirmText = '';
                        Platform.modal.open('bulk-delete-confirm-modal');
                    },

                    executeBulkDelete: async function () {
                        if (!this.bulkDeleteEnabled()) return;
                        this.deleteInProgress = true;
                        try {
                            await Platform.fetch('/api/vendors/bulk', {
                                method: 'DELETE',
                                body:   { ids: this._selectedIds.slice() }
                            });
                            Platform.modal.close('bulk-delete-confirm-modal');
                            this.deleteConfirmText = '';
                            this.bulkConfirmText = '';
                            this.clearSelection();
                            this._loadItems();
                            Platform.toast.success('Vendors deleted.');
                        } catch (e) {
                            Platform.toast.error((e && e.message) || 'Delete failed.');
                        } finally {
                            this.deleteInProgress = false;
                        }
                    },

                    bulkDeleteEnabled: function () {
                        return this.deleteConfirmText === 'DELETE' && this._selectedIds.length > 0;
                    },

                    selectionCount: function () {
                        if (this.selectAllState === 'cross-page') return this.totalItems;
                        return this._selectedIds.length;
                    },

                    // ── Vendor products panel ──────────────────────────
                    loadVendorProducts: async function (vendorId, vendorName) {
                        this.selectedVendorName     = vendorName || '';
                        this.selectedVendorProducts = [];
                        this.productsLoading        = true;
                        try {
                            const data = await Platform.fetch.get('/vendors/' + vendorId + '/products');
                            this.selectedVendorProducts = (data && data.products) || data || [];
                        } catch (e) {
                            this.selectedVendorProducts = [];
                        } finally {
                            this.productsLoading = false;
                        }
                    },

                    // ── Helpers ────────────────────────────────────────
                    vendorDetailUrl: function (id) {
                        return '/vendors/' + id;
                    },

                    formatVendorType: function (t) {
                        return (t || 'Uncategorized').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
                    },

                    statusClass: function (status) {
                        const map = {
                            active:      'bg-emerald-500/10 text-green-800',
                            inactive:    'bg-muted text-muted-foreground',
                            pending:     'bg-amber-500/10 text-yellow-800',
                            deprecated:  'bg-destructive/10 text-destructive',
                            strategic:   'bg-primary/10 text-primary/90'
                        };
                        return map[(status || '').toLowerCase()] || 'bg-muted text-muted-foreground';
                    },

                    contractClass: function (status) {
                        const map = {
                            active:    'bg-emerald-500/10 text-green-800',
                            expired:   'bg-destructive/10 text-destructive',
                            expiring:  'bg-orange-100 text-orange-800',
                            draft:     'bg-muted text-muted-foreground',
                            catalog:   'bg-primary/10 text-primary'
                        };
                        return map[(status || '').toLowerCase()] || 'bg-muted text-muted-foreground';
                    }
                }
            );
        });
    });
}());
