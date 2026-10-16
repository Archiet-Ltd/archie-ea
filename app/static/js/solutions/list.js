/**
 * Solutions List Page — extracted from solutions/list.html (UIUX-023)
 *
 * Requires DOM elements: searchInput, statusFilter, domainFilter, typeFilter
 */
(function() {
'use strict';

window.deleteSolution = async function(solutionId, solutionName) {
    function doDelete() {
        // Platform.fetch automatically injects CSRF token and serializes plain objects to JSON
        Platform.fetch('/solutions/' + solutionId + '/delete', {
            method: 'POST',
            // No need to set Content-Type or X-CSRFToken headers manually
            // Platform.fetch will throw a structured PlatformError on non-ok response
        })
        .then(function(data) {
            // Platform.fetch returns parsed response body directly
            // data is the parsed JSON from the server
            if (data.success) {
                if (window.Platform && Platform.toast) Platform.toast.success('Deleted "' + solutionName + '"');
                setTimeout(function() { window.location.reload(); }, 400);
            } else {
                // This branch is only reachable if the server returns a 200 with success:false,
                // because Platform.fetch throws on non-ok HTTP responses.
                if (window.Platform && Platform.toast) Platform.toast.error(data.error || 'Failed to delete');
                else Platform.toast.error(data.error || 'Failed to delete solution');
            }
        })
        .catch(function(err) {
            // Platform.fetch already shows a user-visible error toast by default,
            // but we still need to handle the error to prevent unhandled rejection.
            // The toast is already shown, so we don't need to show another one.
            // However, the original code attempted to show a toast in the catch block,
            // which would duplicate the toast. We'll keep the catch to log (if needed)
            // but not show a duplicate toast.
            // Since console output is forbidden, we'll just rethrow to surface the error.
            // However, rethrowing would break the existing flow, so we'll let the error
            // propagate silently (the global toast already shown).
            // We'll not add any fallback value per rule #2.
            // The error is already surfaced via Platform.fetch's built-in toast.
            // We'll just ensure no duplicate toast is shown.
            // The original code had duplicate toast calls; we'll remove them.
            // No further action needed.
        });
    }

    if (window.Platform && Platform.modal && Platform.modal.create) {
        let modalId = Platform.modal.create({
            title: 'Delete Solution',
            content: '<p class="text-sm text-muted-foreground">Are you sure you want to delete &ldquo;' + solutionName + '&rdquo;? This cannot be undone.</p>',
            size: 'sm',
            buttons: [
                { label: 'Cancel', variant: 'outline' },
                { label: 'Delete', variant: 'destructive', handler: doDelete }
            ]
        });
        Platform.modal.open(modalId);
    } else {
        if (await Platform.confirm('Delete "' + solutionName + '"? This cannot be undone.')) doDelete();
    }
};

// Server-side filtering — navigate to URL with filter params so all pages are filtered
function navigateWithFilters() {
    let url = new URL(window.location.href);
    let searchEl = document.getElementById('searchInput');
    let statusEl = document.getElementById('statusFilter');
    let domainEl = document.getElementById('domainFilter');
    let typeEl = document.getElementById('typeFilter');
    let createdAfterEl = document.getElementById('createdAfterFilter');
    let createdBeforeEl = document.getElementById('createdBeforeFilter');
    url.searchParams.set('search', searchEl ? searchEl.value : '');
    url.searchParams.set('status', statusEl ? statusEl.value : '');
    url.searchParams.set('domain', domainEl ? domainEl.value : '');
    url.searchParams.set('type', typeEl ? typeEl.value : '');
    url.searchParams.set('created_after', createdAfterEl ? createdAfterEl.value : '');
    url.searchParams.set('created_before', createdBeforeEl ? createdBeforeEl.value : '');
    url.searchParams.set('page', '1');
    window.location.href = url.toString();
}

let _searchDebounce = null;
document.addEventListener('DOMContentLoaded', function() {
    let searchEl = document.getElementById('searchInput');
    let statusEl = document.getElementById('statusFilter');
    let domainEl = document.getElementById('domainFilter');
    let typeEl = document.getElementById('typeFilter');
    if (searchEl) {
        searchEl.addEventListener('keyup', function(e) {
            if (e.key === 'Enter') {
                clearTimeout(_searchDebounce);
                navigateWithFilters();
            } else {
                clearTimeout(_searchDebounce);
                _searchDebounce = setTimeout(navigateWithFilters, 400);
            }
        });
    }
    if (statusEl) statusEl.addEventListener('change', navigateWithFilters);
    if (domainEl) domainEl.addEventListener('change', navigateWithFilters);
    if (typeEl) typeEl.addEventListener('change', navigateWithFilters);
    let createdAfterEl2 = document.getElementById('createdAfterFilter');
    let createdBeforeEl2 = document.getElementById('createdBeforeFilter');
    if (createdAfterEl2) createdAfterEl2.addEventListener('change', navigateWithFilters);
    if (createdBeforeEl2) createdBeforeEl2.addEventListener('change', navigateWithFilters);
});

// Reinitialize lucide icons
if (typeof lucide !== 'undefined') {
    lucide.createIcons();
}

})();

/**
 * Alpine.js component factory for the "Create Solution" drawer.
 * Reads URLs from window.__SOLUTION_LIST_CONFIG__:
 *   { createSolutionUrl, listSolutionsUrl }
 */
function solutionDrawer() {
  return {
    drawerOpen: false,
    formData: {
      name: '',
      description: '',
      solution_type: 'Platform',
      business_domain: '',
      complexity_level: 'Moderate',
      business_value: '',
      technical_lead: '',
      adm_phase: 'A',
      drivers: [],
      goals: []
    },
    submitting: false,
    errorMsg: '',
    init() {
      window.addEventListener('open-solution-drawer', () => {
        this.drawerOpen = true;
        this.errorMsg = '';
      });
    },
    openSolutionDrawer() {
      this.drawerOpen = true;
      this.errorMsg = '';
    },
    addDriver() {
      this.formData.drivers.push({
        name: '',
        driver_type: 'technology',
        description: ''
      });
    },
    removeDriver(index) {
      this.formData.drivers.splice(index, 1);
    },
    addGoal() {
      this.formData.goals.push({
        name: '',
        priority: '2',
        description: ''
      });
    },
    removeGoal(index) {
      this.formData.goals.splice(index, 1);
    },
    submitCreateSolution() {
      // FAR-017: Prevent double-click duplicates
      if (this.submitting) return;
      if (!this.formData.name.trim()) { this.errorMsg = 'Solution name is required.'; return; }
      this.submitting = true;
      this.errorMsg = '';
      const createUrl = window.__SOLUTION_LIST_CONFIG__?.createSolutionUrl || '/solutions/create';
      const listUrl = window.__SOLUTION_LIST_CONFIG__?.listSolutionsUrl || '/solutions/';
      const payload = Object.assign({}, this.formData, { application_ids: [], element_ids: [], drawer_mode: true });
      // Platform.fetch automatically injects CSRF token and serializes plain objects to JSON
      // We pass { silent: true } because we handle inline error state ourselves (this.errorMsg)
      Platform.fetch.post(createUrl, payload, { silent: true })
        .then(data => {
          this.submitting = false;
          // Platform.fetch throws on non-ok HTTP responses, so data is only reached on success
          if (!data.success) { 
            // This branch is only reachable if the server returns a 200 with success:false
            this.errorMsg = data.error || 'An error occurred.'; 
            return; 
          }
          this.drawerOpen = false;
          showToast({ title: 'Solution created successfully', variant: 'default' });
          setTimeout(() => { window.location.href = listUrl; }, 800);
        })
        .catch(err => {
          this.submitting = false;
          // Platform.fetch already shows a toast unless silent:true, but we passed silent:true
          // to avoid duplicate error messages. We need to set inline error state.
          // The error message is available in err.message
          this.errorMsg = err.message || 'Network error. Please try again.';
        });
    }
  };
}

function openSolutionDrawer() {
  window.dispatchEvent(new CustomEvent('open-solution-drawer'));
}

document.addEventListener('alpine:init', () => {
  if (window.Alpine) {
    window.Alpine.data('solutionDrawer', solutionDrawer);
    window.Alpine.data('solutionCreateModal', solutionDrawer);
  }
});

window.solutionDrawer = solutionDrawer;
window.solutionCreateModal = solutionDrawer;
window.openSolutionDrawer = openSolutionDrawer;
