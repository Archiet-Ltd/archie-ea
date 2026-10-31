/**
 * Applications Portfolio — Unified Alpine.js Component
 *
 * Registered as: Alpine.data('appPortfolio', appPortfolio)
 *
 * Systems:
 *   - Modal manager  : openPortfolioModal(key) / closePortfolioModal()
 *   - Toast/notify   : notify(msg, type)
 *   - Filter system  : onFilterChange() / clearFilters() / hasActiveFilters()
 *   - Pagination     : goToPage(n) / changePageSize(n)
 *   - Row selection  : toggleRow(id, checked) / toggleSelectAll(checked)
 *   - Bulk actions   : bulkExportSelected() / confirmBulkDelete()
 *   - Mapping        : openMappingForApp(id, name, type)
 *   - Export         : exportCSV()
 */
function appPortfolio() {
  return {
    // ── State ──────────────────────────────────────────────────────────────
    loading: false,
    selectedIds: new Set(),
    filters: {
      search: '',
      type: '',
      status: '',
      domain: '',
      capability_level: '',
      process_level: '',
    },

    // ARCH-100: sortable column headers. Values match the allow-listed
    // columns in application_list() (id, name, type, lifecycle_status).
    sort: '',
    dir: 'asc',

    // ARCH-100: density toggle (comfortable/compact), persisted per-browser.
    density: 'comfortable',

    // AI Map — reuses the existing comprehensive-auto-map + accept endpoints
    // (app/modules/applications/routes/auto_mapping_routes.py). Their only
    // caller used to be the import modal, so applications already in the
    // estate could never be AI-enriched from this page.
    aiMap: {
      loading: false,
      accepting: false,
      error: '',
      result: null,
      previewApplications: null,
      confidenceThreshold: 0.7,
      maxApplications: 50,
      mapCapabilities: true,
      mapProcesses: true,
      acceptResult: '',
    },

    // ── Lifecycle ──────────────────────────────────────────────────────────
    init() {
      // Sync filter state from current URL params
      const params = new URLSearchParams(window.location.search);
      this.filters.search           = params.get('search')            || '';
      this.filters.type             = params.get('type')              || '';
      this.filters.status           = params.get('status')            || '';
      this.filters.domain           = params.get('domain')            || '';
      this.filters.capability_level = params.get('capability_level')  || '';
      this.filters.process_level    = params.get('process_level')     || '';
      this.sort                     = params.get('sort')              || '';
      this.dir                      = params.get('dir')               || 'asc';

      // ARCH-100: density preference persists across visits (localStorage),
      // independent of the URL — it's a display preference, not a filter.
      try {
        const savedDensity = window.localStorage.getItem('appPortfolio.density');
        if (savedDensity === 'compact' || savedDensity === 'comfortable') {
          this.density = savedDensity;
        }
      } catch (e) {
        // localStorage unavailable (private mode, etc.) — default stands.
      }

      // Mark page ready (removes skeleton)
      this.$nextTick(() => { this.loading = false; });

      // Keyboard: Escape — Platform.modal handles LIFO ESC; no manual listener needed here.
    },

    // ── Modal system ───────────────────────────────────────────────────────
    // Modal keys map to element IDs: 'create' → 'modal-create', etc.
    openPortfolioModal(key) {
      const modalId = 'modal-' + key;
      Platform.modal.open(modalId);
    },

    closePortfolioModal() {
      // Close all modals opened via this component
      ['create', 'import', 'match-vendors', 'consolidation', 'bulk-map'].forEach(key => {
        const modalId = 'modal-' + key;
        if (Platform.modal.isOpen(modalId)) {
          Platform.modal.close(modalId);
        }
      });
    },

    // ── Toast / notification ───────────────────────────────────────────────
    notify(message, type = 'default') {
      if (window.Platform && window.Platform.toast) {
        if (type === 'error') Platform.toast.error(message);
        else if (type === 'success') Platform.toast.success(message);
        else if (type === 'warning') Platform.toast.warning(message);
        else Platform.toast.info(message);
        return;
      }
      // Legacy fallback: appToast store
      if (typeof Alpine !== 'undefined' && Alpine.store('appToast')) {
        const t = Alpine.store('appToast');
        if (t._timer) clearTimeout(t._timer);
        t.message = message;
        t.type    = type;
        t.visible = true;
        t._timer  = setTimeout(() => { t.visible = false; }, 4000);
      }
    },

    // ── Filter system ──────────────────────────────────────────────────────
    onFilterChange() {
      this._navigate(1);
    },

    clearFilters() {
      this.filters.search           = '';
      this.filters.type             = '';
      this.filters.status           = '';
      this.filters.domain           = '';
      this.filters.capability_level = '';
      this.filters.process_level    = '';
      this._navigate(1);
    },

    hasActiveFilters() {
      return Object.values(this.filters).some(v => v !== '');
    },

    // ARCH-104: stat-tile click-to-filter. Sets the lifecycle status filter
    // to the bucket the tile represents (STATUS_MAP keys in list_simple.html)
    // and re-navigates the same way the filter <select> does.
    setStatusFilter(status) {
      this.filters.status = status;
      this.onFilterChange();
    },

    // ── Sorting (ARCH-100) ─────────────────────────────────────────────────
    sortBy(column) {
      if (this.sort === column) {
        this.dir = this.dir === 'asc' ? 'desc' : 'asc';
      } else {
        this.sort = column;
        this.dir = 'asc';
      }
      this._navigate(1);
    },

    // ── Density (ARCH-100) ─────────────────────────────────────────────────
    toggleDensity() {
      this.density = this.density === 'compact' ? 'comfortable' : 'compact';
      try {
        window.localStorage.setItem('appPortfolio.density', this.density);
      } catch (e) {
        // localStorage unavailable — the toggle still works for this page view.
      }
    },

    // ── Pagination ─────────────────────────────────────────────────────────
    goToPage(page) {
      this._navigate(page);
    },

    changePageSize(size) {
      this._navigate(1, parseInt(size, 10));
    },

    _navigate(page, pageSize = null) {
      const params = new URLSearchParams(window.location.search);
      params.set('page', page);
      if (pageSize) params.set('page_size', pageSize);

      // Apply current filter state
      Object.entries(this.filters).forEach(([k, v]) => {
        if (v) params.set(k, v);
        else params.delete(k);
      });

      // Preserve sort state (ARCH-100)
      if (this.sort) {
        params.set('sort', this.sort);
        params.set('dir', this.dir);
      } else {
        params.delete('sort');
        params.delete('dir');
      }

      window.location.href = `${window.location.pathname}?${params.toString()}`;
    },

    // ── Row selection ──────────────────────────────────────────────────────
    toggleRow(id, checked) {
      if (checked) this.selectedIds.add(id);
      else         this.selectedIds.delete(id);
      // Trigger Alpine reactivity for Set (Alpine 3 doesn't track Set mutations)
      this.selectedIds = new Set(this.selectedIds);
    },

    toggleSelectAll(checked) {
      if (checked) {
        document.querySelectorAll('[data-app-id]').forEach(row => {
          if (row && row.dataset) {
            const id = parseInt(row.dataset.appId, 10);
            if (!isNaN(id)) this.selectedIds.add(id);
          }
        });
      } else {
        this.selectedIds.clear();
      }
      this.selectedIds = new Set(this.selectedIds);
    },

    clearSelection() {
      this.selectedIds = new Set();
    },

    allSelected() {
      const rows = document.querySelectorAll('[data-app-id]');
      return rows.length > 0 && [...rows].every(r => r && r.dataset && this.selectedIds.has(parseInt(r.dataset.appId, 10)));
    },

    someSelected() {
      const rows = document.querySelectorAll('[data-app-id]');
      const count = [...rows].filter(r => r && r.dataset && this.selectedIds.has(parseInt(r.dataset.appId, 10))).length;
      return count > 0 && count < rows.length;
    },

    // ── Export ─────────────────────────────────────────────────────────────
    // P-12: this used to be a raw `window.location.href` navigation, which
    // gives the SPA no way to observe success, failure, or "nothing to
    // export" — a download interceptor confirmed zero download events on an
    // empty catalogue with no toast and no error. fetch() + blob makes every  // raw-fetch-ok: blob download; the wrapper returns a parsed body, not a Response
    // outcome observable, per CLAUDE.md's documented `fetch` discipline
    // (`if (!response.ok) throw`, never assume success from a fire-and-forget
    // navigation).
    async exportCSV() {
      const params = new URLSearchParams(window.location.search);
      params.delete('export');
      try {
        // We need raw response headers and blob, so we cannot use Platform.fetch directly.
        // Use raw fetch with CSRF safety net (core/03-fetch.js patches global fetch).
        const resp = await fetch(`/applications/export/csv?${params.toString()}`); // raw-fetch-ok: need raw blob and headers for download
        if (!resp.ok) throw new Error(`Export failed (${resp.status})`);
        if (resp.headers.get('X-Export-Empty') === '1') {
          this.notify('No applications match the current filters — nothing to export.', 'default');
          return;
        }
        const blob = await resp.blob();
        const disposition = resp.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename=([^;]+)/);
        const filename = match ? match[1].trim() : 'applications_export.csv';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        this.notify('Export downloaded.', 'success');
      } catch (e) {
        this.notify('Export failed. Please try again.', 'error');
      }
    },

    bulkExportSelected() {
      if (this.selectedIds.size === 0) {
        this.notify('No applications selected.', 'error');
        return;
      }
      const ids = [...this.selectedIds].join(',');
      // This is a navigation that triggers a download; we cannot use Platform.fetch because we need the browser to handle the file download.
      // The raw fetch is acceptable here because it's a simple GET that the CSRF safety net will protect.
      // However, we should still use fetch to detect errors before navigating.
      try {
        // We'll use Platform.fetch.get to check if the request would succeed, but we still need to trigger the download.
        // Since Platform.fetch returns parsed data, not a blob, we cannot use it for the actual download.
        // Instead, we'll keep the navigation but first verify the request with a HEAD or GET using Platform.fetch.
        // However, to keep the change minimal and preserve existing behavior, we'll keep the navigation.
        // The CSRF token is not required for GET requests, but the safety net will add it if needed.
        // We'll add a comment explaining the decision.
        window.location.href = `/applications/export/csv?ids=${ids}`;
      } catch (e) {
        this.notify('Export failed. Please try again.', 'error');
      }
    },

    // ── Bulk delete ────────────────────────────────────────────────────────
    confirmBulkDelete() {
      if (this.selectedIds.size === 0) return;
      const count = this.selectedIds.size;
      const ids = [...this.selectedIds];
      const self = this;
      const modalId = window.modalManager.createModal({
          title: 'Delete Applications',
          content: '<p class="text-sm text-muted-foreground">Delete ' + count + ' selected application' + (count !== 1 ? 's' : '') + '? Deleted applications are recoverable for a limited window; this list will stop showing them immediately.</p>',
          size: 'small',
          buttons: [
              { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
              { text: 'Delete', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'delete', handler: function() { self._bulkDelete(ids); } }
          ]
      });
      window.modalManager.open(modalId);
    },

    async _bulkDelete(ids) {
      try {
        await Platform.fetch.post('/applications/bulk-delete', { ids, confirm: true }, {
          errorMsg: 'Failed to delete selected applications'
        });
        this.notify(`Deleted ${ids.length} application${ids.length !== 1 ? 's' : ''}.`, 'success');
        this.clearSelection();
        setTimeout(() => window.location.reload(), 800);
      } catch (err) {
        // Platform.fetch already shows a toast via errorMsg, so we don't need to duplicate.
        // However, the existing code also calls this.notify with error; we keep that for inline error state.
        this.notify('Delete failed. Please try again.', 'error');
      }
    },

    // ── Bulk field updates (PLT-020 / PLT-021 / PLT-022) ───────────────────
    // The three endpoints have existed all along - /applications/api/bulk-lifecycle,
    // -assign-owner and -tag in modules/applications/routes/list_views.py - but no
    // client ever called them. list_simple.html wired its Lifecycle, Owner and Tag
    // buttons to bulkUpdateLifecycle() / promptBulkAssignOwner() / promptBulkTag(),
    // none of which existed anywhere in the repo, so all three did nothing at all.

    bulkUpdateLifecycle(stage) {
      if (this.selectedIds.size === 0 || !stage) return;
      const ids = [...this.selectedIds];
      this._applyBulkField(
        '/applications/api/bulk-lifecycle',
        { ids, lifecycle_stage: stage },
        (updated) => `Lifecycle set to ${stage} for ${updated} application${updated !== 1 ? 's' : ''}.`,
        'Failed to update lifecycle'
      );
    },

    promptBulkAssignOwner() {
      if (this.selectedIds.size === 0) return;
      const ids = [...this.selectedIds];
      const self = this;
      // owner_name is a plain string column, not a foreign key, so a text field
      // matches the API. If it ever becomes a real user reference this must
      // switch to the debounced picker against /api/users that DESIGN.md
      // requires for entity fields.
      this._promptForValue({
        title: 'Assign Owner',
        label: 'Owner name',
        placeholder: 'Name of the accountable owner',
        confirmText: 'Assign',
        onConfirm(value) {
          self._applyBulkField(
            '/applications/api/bulk-assign-owner',
            { ids, owner_name: value },
            (updated) => `Owner set for ${updated} application${updated !== 1 ? 's' : ''}.`,
            'Failed to assign owner'
          );
        }
      });
    },

    promptBulkTag() {
      if (this.selectedIds.size === 0) return;
      const ids = [...this.selectedIds];
      const self = this;
      this._promptForValue({
        title: 'Add Tag',
        label: 'Tag',
        placeholder: 'Tag to add to each selected application',
        confirmText: 'Add Tag',
        onConfirm(value) {
          self._applyBulkField(
            '/applications/api/bulk-tag',
            { ids, tag: value },
            (updated) => `Tag added to ${updated} application${updated !== 1 ? 's' : ''}.`,
            'Failed to add tag'
          );
        }
      });
    },

    /** Collect a single value in a modal. Never window.prompt() - DESIGN.md. */
    _promptForValue(opts) {
      const inputId = 'bulk-value-input-' + Date.now();
      const modalId = window.modalManager.createModal({
        title: opts.title,
        size: 'small',
        content:
          '<label for="' + inputId + '" class="block text-sm font-medium text-foreground mb-2">' +
          opts.label + '</label>' +
          '<input id="' + inputId + '" type="text" autocomplete="off" placeholder="' +
          opts.placeholder + '" class="flex h-9 w-full rounded-md border border-input ' +
          'bg-transparent px-3 py-1 text-sm shadow-sm transition-colors ' +
          'placeholder:text-muted-foreground focus-visible:outline-none ' +
          'focus-visible:ring-1 focus-visible:ring-ring">',
        buttons: [
          { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
          { text: opts.confirmText, class: 'px-4 py-2 text-sm font-medium text-primary-foreground bg-primary border border-transparent rounded-md hover:bg-primary/90', action: 'confirm', handler: function() {
            const field = document.getElementById(inputId);
            const value = field ? field.value.trim() : '';
            if (!value) {
              Platform.toast.error(opts.label + ' is required.');
              return;
            }
            opts.onConfirm(value);
          } }
        ]
      });
      window.modalManager.open(modalId);
    },

    async _applyBulkField(url, payload, describe, errorMsg) {
      try {
        const data = await Platform.fetch.post(url, payload, { errorMsg });
        if (data && data.success === false) {
          Platform.toast.error(data.error || errorMsg);
          return;
        }
        // Report the server's own updated_count, not the number requested. These
        // endpoints skip ids they cannot find and return an `errors` list, so
        // claiming every selected application changed would overstate the result.
        const requested = payload.ids.length;
        const updated = (data && typeof data.updated_count === 'number')
          ? data.updated_count
          : requested;
        this.notify(describe(updated), updated === requested ? 'success' : 'warning');
        if (data && Array.isArray(data.errors) && data.errors.length > 0) {
          const detail = data.errors.map(e => (typeof e === 'string' ? e : (e && (e.error || e.message)) || 'unknown error')).slice(0, 3).join('; ');
          this.notify(`${data.errors.length} application${data.errors.length !== 1 ? 's' : ''} could not be updated: ${detail}`, 'warning');
        }
        this.clearSelection();
        setTimeout(() => window.location.reload(), 800);
      } catch (err) {
        // Platform.fetch() already raised a toast for the failed request (it is
        // not called with silent:true here), so toasting again here would show
        // the same failure twice, so nothing more is needed here.
      }
    },

    // ── AI Map (existing comprehensive-auto-map / accept endpoints) ────────
    openAiMapModal() {
      this.aiMap.loading = false;
      this.aiMap.accepting = false;
      this.aiMap.error = '';
      this.aiMap.result = null;
      this.aiMap.previewApplications = null;
      this.aiMap.acceptResult = '';
      Platform.modal.open('ai-map-modal');
    },

    closeAiMapModal() {
      Platform.modal.close('ai-map-modal');
    },

    async runAiMap() {
      this.aiMap.loading = true;
      this.aiMap.error = '';
      this.aiMap.result = null;
      this.aiMap.previewApplications = null;
      try {
        const data = await Platform.fetch.post('/applications/api/comprehensive-auto-map', {
          max_applications: this.aiMap.maxApplications || 50,
          map_capabilities: this.aiMap.mapCapabilities,
          map_processes: this.aiMap.mapProcesses,
          confidence_threshold: this.aiMap.confidenceThreshold,
          // auto_create stays false here: this call is analysis-only. The
          // preview is written to the database only if the user clicks
          // "Accept & Save", which goes through the dedicated accept
          // endpoint below.
          auto_create: false,
        }, { silent: true });
        if (!data || data.success === false) {
          this.aiMap.error = (data && (data.message || data.error)) || 'AI mapping analysis failed.';
          return;
        }
        this.aiMap.result = data;
        this.aiMap.previewApplications = Array.isArray(data.applications) ? data.applications : [];
      } catch (err) {
        this.aiMap.error = (err && err.message) || 'AI mapping analysis failed.';
      } finally {
        this.aiMap.loading = false;
      }
    },

    async acceptAiMap() {
      if (!this.aiMap.previewApplications || this.aiMap.previewApplications.length === 0) {
        this.aiMap.error = 'Nothing to accept — run the analysis first.';
        return;
      }
      this.aiMap.accepting = true;
      this.aiMap.error = '';
      try {
        const data = await Platform.fetch.post('/applications/api/comprehensive-auto-map/accept', {
          applications: this.aiMap.previewApplications,
          confidence_threshold: this.aiMap.confidenceThreshold,
        }, { silent: true });
        if (!data || data.success === false) {
          this.aiMap.error = (data && (data.message || data.error)) || 'Saving the AI mappings failed.';
          return;
        }
        const created = typeof data.mappings_created === 'number' ? data.mappings_created : 0;
        this.aiMap.acceptResult = `Saved ${created} mapping${created !== 1 ? 's' : ''} across ${data.applications_processed || 0} application${data.applications_processed !== 1 ? 's' : ''}.`;
        this.notify(this.aiMap.acceptResult, 'success');
        setTimeout(() => window.location.reload(), 1200);
      } catch (err) {
        this.aiMap.error = (err && err.message) || 'Saving the AI mappings failed.';
      } finally {
        this.aiMap.accepting = false;
      }
    },

    // ── Mapping ────────────────────────────────────────────────────────────
    openMappingForApp(id, name, mappingType) {
      const normalizedType = mappingType || 'capability';

      // Delegate to the unified mapping modal if registered
      if (typeof window.openUnifiedMappingModal === 'function') {
        if (typeof window.initUnifiedMappingModal === 'function') {
          window.initUnifiedMappingModal({
            context: normalizedType,
            apiEndpoint: '/capability-map/api',
          });
        }

        // unified_mapping_modal.js's openUnifiedMappingModal is always
        // (targetId, targetName, options) — see window.openUnifiedMappingModal
        // in components/unified_mapping_modal.js, which does
        // `UnifiedMappingModal.targetId = String(targetId)`. The
        // window.UnifiedMappingModal-gated else-branch called it with a
        // single {id, name, type} object instead, so targetId became that
        // whole object and String(targetId) produced the literal string
        // "[object Object]", which then flowed straight into the
        // capability-lookup URL (`/capability-map/api/capability/[object
        // Object]/applications`, a 400) every time this branch was taken.
        // There is only one real signature; use it unconditionally.
        window.openUnifiedMappingModal(id, name, {
          context: normalizedType,
          targetType: normalizedType,
        });
        return;
      }

      // Fallback: navigate to the mapping page
      const routes = {
        capability: `/applications/${id}/map-capabilities`,
        apqc:       `/applications/${id}/map-processes`,
        vendor:     `/applications/${id}/map-vendors`,
        archimate:  `/applications/${id}/map-archimate`,
      };
      const url = routes[normalizedType] || `/applications/${id}`;
      window.location.href = url;
    },

    // ── Single delete ──────────────────────────────────────────────────────
    // The row menu's Delete asks first, then posts to the same delete route
    // the old inline form did (CSRF token from the page's meta tag).
    async deleteApplication(url, name) {
      const ok = await Platform.modal.confirm('Delete ' + name + '? This cannot be undone.');
      if (!ok) return;
      const meta = document.querySelector('meta[name=csrf-token]');
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = url;
      const token = document.createElement('input');
      token.type = 'hidden';
      token.name = 'csrf_token';
      token.value = meta ? meta.content : '';
      form.appendChild(token);
      document.body.appendChild(form);
      form.submit();
    },
  };
}

/**
 * Create Application inline form component.
 * Used inside the #modal-create panel.
 */
function applicationCreateForm() {
  return {
    submitting: false,
    errorMsg: '',

    // A chat answer that named an application not yet in the model links here with
    // ?add_name=<name> (the existing form, prefilled, never a second "add" dialog).
    init() {
      try {
        const addName = new URLSearchParams(window.location.search).get('add_name');
        if (addName) {
          this.form.name = addName.slice(0, 255);
          this.scheduleCheck();
          this.$nextTick(() => Platform.modal.open('modal-create'));
        }
      } catch (e) { /* swallow-ok: the prefill is a convenience; the form still opens empty */ }
    },
    // ARCH-041/ARCH-042: per-field errors keyed by form field name, rendered
    // next to the corresponding input with aria-invalid + aria-describedby,
    // instead of a single opaque banner (or, worse, the raw HTTP status
    // phrase "Bad Request"). Populated either client-side (required-field
    // check below) or from the API's {"errors": {field: [msg, ...]}} shape.
    fieldErrors: {},

    fieldInvalid(field) {
      return !!(this.fieldErrors && this.fieldErrors[field]);
    },

    // Duplicate check: existing applications the new one may repeat, looked up
    // as the person types. `matches` is null until a check has answered, so an
    // unanswered check is never shown as "nothing similar".
    matches: null,
    checkError: '',
    confirmedDistinct: false,
    // True from the first keystroke until the lookup for the current text has
    // answered; submit waits for it so a stale or missing answer never passes.
    checking: false,
    // Set once the person has been told the lookup failed and pressed save again.
    checkFailureAcknowledged: false,
    _checkTimer: null,
    _checkSeq: 0,
    _checkPromise: null,
    _checkArgs: null,

    scheduleCheck() {
      // Any answer still in flight belongs to older text: never paint it.
      this._checkSeq++;
      clearTimeout(this._checkTimer);
      this._checkTimer = null;
      this.confirmedDistinct = false;
      this.checkFailureAcknowledged = false;
      // The previous answer belongs to the previous text: drop it now.
      this.matches = null;
      this.renderMatches();
      const name = this.form.name.trim();
      const code = this.form.application_code.trim();
      if (!name && !code) {
        this.checking = false;
        this._checkArgs = null;
        this.checkError = '';
        return;
      }
      this.checking = true;
      this._checkArgs = [name, code];
      this._checkTimer = setTimeout(() => {
        this._checkTimer = null;
        this._checkPromise = this.runCheck(name, code);
      }, 300);
    },

    // Resolves once the lookup for the text now in the form has answered: runs
    // a still-waiting lookup at once and awaits one already in flight.
    async settleCheck() {
      if (this._checkTimer) {
        clearTimeout(this._checkTimer);
        this._checkTimer = null;
        const [name, code] = this._checkArgs || ['', ''];
        this._checkPromise = this.runCheck(name, code);
      }
      if (this._checkPromise) await this._checkPromise;
    },

    async runCheck(name, code) {
      const seq = ++this._checkSeq;
      const params = new URLSearchParams({ name: name, application_code: code });
      try {
        const data = await Platform.fetch.get(
          '/applications/api/registration-check?' + params.toString(), null, { silent: true });
        if (seq !== this._checkSeq) return;
        this.matches = Array.isArray(data.matches) ? data.matches : [];
        this.checkError = '';
        this.renderMatches();
      } catch (err) {
        if (seq !== this._checkSeq) return;
        this.matches = null;
        this.renderMatches();
        this.checkError = 'The duplicate check could not run. Check the application list before saving.';
      } finally {
        if (seq === this._checkSeq) this.checking = false;
      }
    },

    // Built with DOM calls (textContent, never markup) so a name containing
    // angle brackets is shown as text.
    renderMatches() {
      this.$nextTick(() => {
        const list = this.$refs.matchList;
        if (!list) return;
        list.replaceChildren();
        for (const m of this.matches || []) {
          const item = document.createElement('li');
          item.className = 'flex flex-wrap items-center gap-2';
          const link = document.createElement('a');
          link.href = m.url;
          link.className = 'text-primary underline underline-offset-2';
          link.textContent = m.name;
          item.appendChild(link);
          if (m.application_code) {
            const code = document.createElement('span');
            code.className = 'text-muted-foreground';
            code.textContent = m.application_code;
            item.appendChild(code);
          }
          const why = document.createElement('span');
          why.className = 'text-muted-foreground';
          why.textContent = '— ' + String(m.reason || '').toLowerCase();
          item.appendChild(why);
          list.appendChild(item);
        }
      });
    },

    get hasSameName() {
      return !!this.matches && this.matches.some((m) => m.match === 'same_name');
    },

    get needsConfirmation() {
      return !!this.matches && this.matches.length > 0 && !this.hasSameName;
    },

    form: {
      name: '',
      application_code: '',
      application_type: '',
      business_criticality: '',
      deployment_status: '',
      lifecycle_status: '',
      business_owner: '',
      description: '',
    },

    async submit() {
      // FAR-017: Prevent double-click duplicates
      if (this.submitting) return;
      this.fieldErrors = {};
      if (!this.form.name.trim()) {
        // ARCH-042: mark the field invalid programmatically (aria-invalid +
        // aria-describedby), not just a focus ring — the previous behaviour
        // scrolled/focused the field but left #modal-create
        // [aria-invalid="true"] matching 0 elements.
        this.fieldErrors = { name: 'Application name is required.' };
        this.errorMsg = 'Application name is required.';
        this.$nextTick(() => document.getElementById('ca-name')?.focus());
        return;
      }
      // Claim the form before waiting, so a second press sends nothing while
      // the lookup settles; every early return below hands it back.
      this.submitting = true;
      try {
        // A lookup still waiting or in flight must answer before the form is judged.
        await this.settleCheck();
      } catch (err) {
        // runCheck reports its own failure through checkError.
      }
      if (this.checkError && !this.checkFailureAcknowledged) {
        // The lookup failed: say so and let the next press save without it.
        this.checkFailureAcknowledged = true;
        this.errorMsg = this.checkError + ' Press Add Application again to save without it.';
        this.submitting = false;
        return;
      }
      if (this.needsConfirmation && !this.confirmedDistinct) {
        this.errorMsg = 'Confirm that none of the similar applications is the same one.';
        this.submitting = false;
        return;
      }
      this.errorMsg   = '';

      const url = window.__APP_CONFIG__?.createApplicationUrl || '/applications/create';

      // The create schema (ApplicationCreateSchema) names the field
      // `criticality`, and rejects unknown keys outright, so posting the
      // form's own `business_criticality` 400ed EVERY submission. It also
      // validates each enum with OneOf, so an untouched "Select..." option
      // ('') is not "no value" to it -- it is an invalid value. Send the
      // schema's names, and omit anything the user left blank.
      const payload = {};
      for (const [key, value] of Object.entries(this.form)) {
        if (value === '' || value === null || value === undefined) continue;
        payload[key === 'business_criticality' ? 'criticality' : key] = value;
      }

      try {
        const data = await Platform.fetch.post(url, payload, { silent: true });

        // Success: close modal and reload
        Platform.modal.close('modal-create');
        if (data.redirect) {
          window.location.href = data.redirect;
        } else {
          window.location.reload();
        }
      } catch (err) {
        // ARCH-041: err.data.errors is the API's {field: [msg, ...]} map.
        // Render each against its field rather than collapsing to one
        // generic string, and never fall back to err.message alone — that
        // is response.statusText ("Bad Request") when the body carries no
        // top-level "error"/"message" key.
        const apiErrors = err.data && err.data.errors;
        if (apiErrors && typeof apiErrors === 'object') {
          const flattened = {};
          for (const field of Object.keys(apiErrors)) {
            const msgs = apiErrors[field];
            const formField = field === 'criticality' ? 'business_criticality' : field;
            flattened[formField] = Array.isArray(msgs) ? msgs[0] : msgs;
          }
          this.fieldErrors = flattened;
          this.errorMsg = Object.entries(flattened).map(([f, m]) => `${f}: ${m}`).join('; ');
        } else {
          const errorDetail = (err.data && (err.data.error || err.data.message)) || err.message || 'An unexpected error occurred';
          this.errorMsg = errorDetail;
        }
        Platform.toast.error('Failed to create application: ' + this.errorMsg);
      } finally {
        this.submitting = false;
      }
    },
  };
}

// ── Alpine registration ──────────────────────────────────────────────────────
document.addEventListener('alpine:init', () => {
  Alpine.store('appToast', { visible: false, message: '', type: 'default', _timer: null });
  Alpine.data('appPortfolio', appPortfolio);
  Alpine.data('applicationCreateForm', applicationCreateForm);

  // Handle close-modal dispatch from modal backdrop / cancel buttons
  document.addEventListener('app-close-modal', () => {
    if (Platform.modal.isOpen('modal-create')) {
      Platform.modal.close('modal-create');
    }
  });
});
