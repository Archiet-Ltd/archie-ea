// smoke-coverage-ok: the changed business-case builders are pure string helpers, exercised under node by tests/test_business_case_unmeasured_scores.py
/**
 * Application Rationalization - External JavaScript
 * Extracted from rationalization.html inline scripts
 * Depends on: window.__APP_CONFIG__ (injected by template)
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

let CURRENCY_SYMBOL = APP_CONFIG.currencySymbol || '£';

// Currency formatting helper - uses CurrencyManager when available, falls back to CURRENCY_SYMBOL
function formatCurrency(amount) {
  if (window.currencyManager) return window.currencyManager.format(amount);
  return CURRENCY_SYMBOL + Number(amount || 0).toLocaleString();
}
let selectedStrategy = 'hybrid';
let thresholdSlider = null;
let thresholdValue = null;

// Attribute-value escaping. escapeHtml() (core/02-sanitize.js) escapes through
// textContent -> innerHTML, which leaves " and ' untouched: correct for a text
// node, unsafe inside an attribute. Anything interpolated into a data-* value
// must go through this instead.
function escapeAttr(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// CSRF token helper (avoids 6 inline querySelector lookups)
function getCSRFToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

// App ID validation helper (avoids 3 duplicated checks)
function validateAppId(inputId) {
  let raw = document.getElementById(inputId)?.value;
  let parsed = parseInt(raw, 10);
  if (!raw || isNaN(parsed) || parsed <= 0) return null;
  return parsed;
}

// Button loading state helper
function setButtonLoading(btn, loading, loadingText) {
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn._originalHTML = btn.innerHTML;
    safeHTML(btn, '<svg class="w-4 h-4 inline mr-1 animate-spin" fill="none" viewBox="0 0 24 24">' +
      '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
      '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
      '</svg>' + (loadingText || 'Processing...'));
  } else {
    btn.disabled = false;
    if (btn._originalHTML) {
      safeHTML(btn, btn._originalHTML);
      delete btn._originalHTML;
    }
  }
}

function setupAppAutocomplete(searchInputId, hiddenInputId, dropdownId, onSelect) {
  let searchInput = document.getElementById(searchInputId);
  let hiddenInput = document.getElementById(hiddenInputId);
  let dropdown = document.getElementById(dropdownId);
  if (!searchInput || !hiddenInput || !dropdown) return;

  let debounceTimer = null;
  let selectedName = '';
  let abortController = null;

  function renderItems(apps) {
    if (apps.length === 0) {
      safeHTML(dropdown, '<div class="px-3 py-2 text-sm text-muted-foreground">No applications found</div>');
      dropdown.classList.remove('hidden');
      searchInput.setAttribute('aria-expanded', 'true');
      return;
    }
    safeHTML(dropdown, apps.map(function(app) {
      let safeName = escapeHtml(app.name || '');
      let safeCode = escapeHtml(app.code || '');
      let safeEeid = escapeHtml(app.external_id || '');
      let safeCriticality = escapeHtml(app.criticality || '');
      return '<div class="app-autocomplete-item px-3 py-2 cursor-pointer hover:bg-accent text-sm border-b last:border-0"' +
             ' role="option" data-app-id="' + app.id + '" data-app-name="' + safeName + '">' +
          '<div class="font-medium">' + safeName + '</div>' +
          '<div class="text-xs text-muted-foreground">' +
            (safeEeid ? safeEeid : 'ID: ' + app.id) + (safeCode ? ' | ' + safeCode : '') + (safeCriticality && safeCriticality !== 'Unknown' ? ' | ' + safeCriticality : '') +
          '</div>' +
        '</div>';
    }).join(''));
    dropdown.querySelectorAll('.app-autocomplete-item').forEach(function(item) {
      item.addEventListener('click', function() {
        hiddenInput.value = this.dataset.appId;
        searchInput.value = this.dataset.appName;
        selectedName = this.dataset.appName;
        dropdown.classList.add('hidden');
        searchInput.setAttribute('aria-expanded', 'false');
        if (typeof onSelect === 'function') { onSelect(this.dataset.appId, this.dataset.appName); }
      });
    });
    dropdown.classList.remove('hidden');
    searchInput.setAttribute('aria-expanded', 'true');
  }

  async function fetchResults(query) {
    if (abortController) { abortController.abort(); }
    abortController = new AbortController();
    try {
      let url = '/api/enterprise/applications?search=' + encodeURIComponent(query) + '&limit=10';
      // silent: this paints its own inline failure state below, so the global
      // error toast would be a duplicate.
      let data = await Platform.fetch.get(url, null, {
        signal: abortController.signal,
        silent: true
      });
      renderItems(data.applications || []);
    } catch (err) {
      if (err.name === 'AbortError') return;
      /* Platform.fetch throws on both a network failure and a non-ok response, so
         this one branch now covers what used to be two. Painting "Search failed"
         here is load-bearing: a network failure used to paint nothing, leaving the
         previous query's results (or an empty dropdown) on screen as though they
         were the answer for what was just typed. */
      safeHTML(dropdown, '<div class="px-3 py-2 text-sm text-destructive">Search failed. Please try again.</div>');
      dropdown.classList.remove('hidden');
    }
  }

  searchInput.addEventListener('input', function() {
    let query = this.value.trim();
    clearTimeout(debounceTimer);
    if (query !== selectedName) { hiddenInput.value = ''; selectedName = ''; }
    debounceTimer = setTimeout(function() { fetchResults(query); }, 300);
  });

  // Show pre-existing data immediately on focus
  searchInput.addEventListener('focus', function() {
    if (hiddenInput.value) return;  // already selected — don't clobber
    if (!dropdown.classList.contains('hidden') && dropdown.innerHTML) return;  // already open
    fetchResults(this.value.trim());
  });

  // Hide dropdown on outside click
  document.addEventListener('click', function(e) {
    if (!searchInput.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.add('hidden');
      searchInput.setAttribute('aria-expanded', 'false');
    }
  });

  // Keyboard navigation
  searchInput.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      dropdown.classList.add('hidden');
      searchInput.setAttribute('aria-expanded', 'false');
      return;
    }
    let items = dropdown.querySelectorAll('.app-autocomplete-item');
    if (items.length === 0 || dropdown.classList.contains('hidden')) return;

    let activeIdx = -1;
    items.forEach(function(item, i) { if (item.hasAttribute('data-active')) activeIdx = i; });

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      let nextIdx = activeIdx < items.length - 1 ? activeIdx + 1 : 0;
      items.forEach(function(item) { item.removeAttribute('data-active'); item.classList.remove('bg-accent'); });
      items[nextIdx].setAttribute('data-active', ''); items[nextIdx].classList.add('bg-accent');
      items[nextIdx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      let prevIdx = activeIdx > 0 ? activeIdx - 1 : items.length - 1;
      items.forEach(function(item) { item.removeAttribute('data-active'); item.classList.remove('bg-accent'); });
      items[prevIdx].setAttribute('data-active', ''); items[prevIdx].classList.add('bg-accent');
      items[prevIdx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault();
      items[activeIdx].click();
    }
  });
}

// Initialize autocomplete fields — auto-load data on selection
setupAppAutocomplete('options-app-search', 'options-app-id', 'options-app-dropdown', function() {
  analyzeOptions();
});
setupAppAutocomplete('impact-app-search', 'impact-app-id', 'impact-app-dropdown', function() {
  checkRetirementBlockers();
});

// Smooth scroll to detection panel
function scrollToDetection() {
  let panel = document.getElementById('detection-panel');
  if (panel) {
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// Strategy card selection
function selectStrategy(card) {
  document.querySelectorAll('.strategy-card').forEach(function(c) {
    c.classList.remove('border-primary', 'bg-primary/5', 'shadow-md', 'ring-2', 'ring-primary/20');
    c.classList.add('border-input', 'bg-background');
    c.setAttribute('aria-checked', 'false');
  });
  card.classList.remove('border-input', 'bg-background');
  card.classList.add('border-primary', 'bg-primary/5', 'shadow-md', 'ring-2', 'ring-primary/20');
  card.setAttribute('aria-checked', 'true');
  selectedStrategy = card.dataset.strategy;
}

// DOM-dependent initialization — deferred until DOM is ready
// (this script loads in <head> via extra_head_js, before body elements exist)
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.strategy-card').forEach(function(card) {
    card.addEventListener('click', function() { selectStrategy(card); });
    // Keyboard: Enter or Space selects
    card.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectStrategy(card);
      }
    });
  });

  // Select hybrid by default
  let defaultCard = document.querySelector('[data-strategy="hybrid"]');
  if (defaultCard) selectStrategy(defaultCard);

  // Threshold slider
  thresholdSlider = document.getElementById('threshold-slider');
  thresholdValue = document.getElementById('threshold-value');
  if (thresholdSlider) {
    thresholdSlider.addEventListener('input', function() {
      thresholdValue.textContent = thresholdSlider.value;
      thresholdSlider.setAttribute('aria-valuenow', thresholdSlider.value);
      thresholdSlider.setAttribute('aria-valuetext', thresholdSlider.value + '%');
    });
  }
});

// Run detection
async function runDetection() {
  let btn = document.getElementById('run-detection-btn');
  let normalState = btn.querySelector('.normal-state');
  let loadingState = btn.querySelector('.loading-state');
  let resultsDiv = document.getElementById('detection-results');

  btn.disabled = true;
  normalState.classList.add('hidden');
  loadingState.classList.remove('hidden');
  resultsDiv.classList.add('hidden');

  try {
    let data = await Platform.fetch.post(APP_CONFIG.runDetectionUrl, {
      method: selectedStrategy,
      threshold: parseInt((thresholdSlider || {}).value || '80') / 100
    });

    if (data.success) {
      // Update strategy name
      let strategyNames = {
        'fast': 'Fast Detection',
        'hybrid': 'Hybrid Detection',
        'enhanced': 'Enhanced Detection'
      };
      document.getElementById('detection-strategy-name').textContent = strategyNames[selectedStrategy] || 'Detection';

      // Update timestamp
      let now = new Date();
      document.getElementById('detection-timestamp').textContent = now.toLocaleTimeString();

      // Update result numbers
      document.getElementById('result-groups').textContent = data.groups_found;
      document.getElementById('result-exact').textContent = data.exact_matches || 0;
      document.getElementById('result-fuzzy').textContent = data.fuzzy_matches || 0;
      document.getElementById('result-savings').textContent = formatCurrency(data.estimated_savings || 0);

      // Update "View Results" link to go to this specific run
      let viewResultsLink = document.getElementById('view-results-link');
      if (data.run_id) {
        viewResultsLink.href = APP_CONFIG.simpleDashboardUrl + '?run_id=' + data.run_id;
      }

      resultsDiv.classList.remove('hidden');

      // Toggle between empty state and results panel based on groups found
      let emptyState = document.getElementById('detection-empty-state');
      let resultsPanel = document.getElementById('detection-results-panel');
      if (data.groups_found === 0) {
        emptyState.classList.remove('hidden');
        resultsPanel.classList.add('hidden');
      } else {
        emptyState.classList.add('hidden');
        resultsPanel.classList.remove('hidden');
      }

      // Scroll to results so user can see them
      resultsDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      // NO auto-reload - let user click "View Results" when ready
      // The stats will update when they navigate back or refresh manually
    } else {
      showToast('Detection failed: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (error) {
    showToast('Detection failed: ' + error.message, 'error');
  } finally {
    btn.disabled = false;
    normalState.classList.remove('hidden');
    loadingState.classList.add('hidden');
  }
}

// Auto-resolve exact matches
async function autoResolveExact() {
  let modalId = window.modalManager.createModal({
      title: 'Auto-Resolve Exact Matches',
      content: '<p class="text-sm text-muted-foreground">This will automatically resolve ALL exact match duplicate groups (100% similarity) by keeping the primary application in each group.</p><p class="text-sm text-muted-foreground mt-2">Are you sure you want to proceed?</p>',
      size: 'small',
      buttons: [
          { text: 'Cancel', class: 'px-4 py-2 text-sm font-medium text-foreground bg-background border border-border rounded-md hover:bg-muted', action: 'cancel', handler: function() {} },
          { text: 'Resolve All', class: 'px-4 py-2 text-sm font-medium text-destructive-foreground bg-destructive border border-transparent rounded-md hover:bg-destructive/90', action: 'resolve', handler: function() { _doAutoResolveExact(); } }
      ]
  });
  window.modalManager.open(modalId);
}

async function _doAutoResolveExact() {
  let btn = document.getElementById('auto-resolve-btn');
  setButtonLoading(btn, true, 'Resolving...');

  try {
    let data = await Platform.fetch.post(APP_CONFIG.autoResolveUrl);

    if (data.success) {
      showToast('Auto-Resolve Complete: ' + data.resolved_count + ' exact match groups resolved', 'success');

      // Reload page to update stats (notification visible until reload)
      setTimeout(function() { location.reload(); }, 2000);
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (error) {
    showToast('Failed to auto-resolve: ' + error.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

// Score entire portfolio (calls dashboard API)
async function scorePortfolio() {
  let btn = document.getElementById('score-portfolio-btn');
  setButtonLoading(btn, true, 'Scoring...');

  try {
    let data = await Platform.fetch.post(APP_CONFIG.scorePortfolioUrl, { force_recalculate: false });

    if (data.success) {
      let scored = (data.data && data.data.total_scored) || 0;
      showToast('Portfolio scored: ' + scored + ' applications. Opening scorecard...', 'success');
      setTimeout(function() { window.location.href = APP_CONFIG.scorecardUrl || '/dashboard/rationalization/scorecard'; }, 1500);
    } else {
      showToast('Error: ' + (data.error || 'Unknown error'), 'error');
    }
  } catch (error) {
    showToast('Failed to score portfolio: ' + error.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

// ============================================================================
// Retirement Blocker Assessment (RAT-109) — category-by-category rendering
// ============================================================================

/**
 * Renders a category-by-category retirement blocker assessment into a container.
 *
 * @param {Object} data - Response payload from /rationalization/api/retirement-blockers/<id>
 *   Expected shape: { categories, total_blockers, is_retirement_safe, blocker_summary }
 * @param {string} containerId - ID of the DOM element to render into
 */
function renderRetirementBlockers(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;

  let categories = data.categories || [];
  let isRetirementSafe = data.is_retirement_safe;
  let totalBlockers = data.total_blockers || 0;
  let summary = data.blocker_summary || '';

  // Verdict banner
  let verdictClass, verdictIcon, verdictLabel;
  if (totalBlockers > 0) {
    verdictClass = 'border-destructive/30 bg-destructive/5 text-destructive';
    verdictIcon = 'x-circle';
    verdictLabel = totalBlockers + ' Retirement Blocker' + (totalBlockers !== 1 ? 's' : '') + ' Found';
  } else {
    let hasWarning = categories.some(function(c) { return c.status === 'warning'; });
    if (hasWarning) {
      verdictClass = 'border-amber-400/30 bg-amber-50 text-amber-700';
      verdictIcon = 'alert-triangle';
      verdictLabel = 'No Hard Blockers — Review Warnings';
    } else {
      verdictClass = 'border-emerald-400/30 bg-emerald-50 text-emerald-700';
      verdictIcon = 'check-circle';
      verdictLabel = 'Retirement Safe — All Categories Clear';
    }
  }

  // Status config per category status value
  function statusConfig(status) {
    if (status === 'blocked') {
      return {
        badge: 'bg-destructive/10 text-destructive border border-destructive/20',
        icon: 'x-circle',
        iconClass: 'text-destructive',
        row: 'border-destructive/20 bg-destructive/5',
        label: 'BLOCKED'
      };
    }
    if (status === 'warning') {
      return {
        badge: 'bg-amber-100 text-amber-700 border border-amber-200',
        icon: 'alert-triangle',
        iconClass: 'text-amber-500',
        row: 'border-amber-200/40 bg-amber-50/50',
        label: 'WARNING'
      };
    }
    return {
      badge: 'bg-emerald-500/10 text-emerald-700 border border-emerald-500/20',
      icon: 'check-circle',
      iconClass: 'text-emerald-500',
      row: 'border-input bg-background',
      label: 'CLEAR'
    };
  }

  // Category icons
  let categoryIcons = {
    'Integrations': 'git-branch',
    'Users': 'users',
    'Contracts': 'file-text',
    'Compliance': 'shield',
    'Data Migration': 'database'
  };

  let categoryHtml = categories.map(function(cat) {
    let cfg = statusConfig(cat.status);
    let catIcon = categoryIcons[cat.name] || 'info';
    return (
      '<div class="flex items-start gap-3 p-3 rounded-lg border ' + cfg.row + '">' +
        '<div class="h-8 w-8 rounded-lg bg-background border border-border flex items-center justify-center flex-shrink-0 mt-0.5">' +
          '<i data-lucide="' + catIcon + '" class="w-4 h-4 text-muted-foreground"></i>' +
        '</div>' +
        '<div class="flex-1 min-w-0">' +
          '<div class="flex items-center gap-2 mb-0.5">' +
            '<p class="font-semibold text-sm">' + escapeHtml(cat.name) + '</p>' +
            '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-bold ' + cfg.badge + '">' +
              '<i data-lucide="' + cfg.icon + '" class="w-3 h-3 mr-1 ' + cfg.iconClass + '"></i>' +
              cfg.label +
            '</span>' +
          '</div>' +
          '<p class="text-xs text-muted-foreground leading-snug">' + escapeHtml(cat.details) + '</p>' +
        '</div>' +
      '</div>'
    );
  }).join('');

  let html = (
    '<div class="space-y-3">' +
      '<div class="flex items-center gap-3 p-3 rounded-lg border ' + verdictClass + '">' +
        '<i data-lucide="' + verdictIcon + '" class="w-5 h-5 flex-shrink-0"></i>' +
        '<div class="flex-1 min-w-0">' +
          '<p class="font-semibold text-sm">' + escapeHtml(verdictLabel) + '</p>' +
          (summary ? '<p class="text-xs mt-0.5 opacity-80">' + escapeHtml(summary) + '</p>' : '') +
        '</div>' +
      '</div>' +
      '<div class="space-y-2">' +
        categoryHtml +
      '</div>' +
    '</div>'
  );

  safeHTML(container, html);

  // Re-initialise Lucide icons inside the container
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }
}

// Check retirement blockers for an application
async function checkRetirementBlockers() {
  let appId = validateAppId('impact-app-id');
  if (!appId) {
    showToast('Please search and select a valid application', 'warning');
    return;
  }

  let btn = document.getElementById('check-blockers-btn');
  setButtonLoading(btn, true, 'Checking...');

  try {
    // Fire both the dependency-level check (existing) and the new authoritative
    // 5-category assessment (RAT-109) in parallel for a complete picture.
    let [result, assessResult] = await Promise.all([
      Platform.fetch.get('/dashboard/api/rationalization/retirement-blockers/' + appId, null, { silent: true }),
      Platform.fetch.get('/applications/rationalization/api/retirement-blockers/' + appId, null, { silent: true })
    ]);

    if (!result.success) {
      showToast('Error: ' + (result.error || 'Unknown error'), 'error');
      return;
    }

    // Render the RAT-109 category assessment if the container is present
    if (assessResult.success && assessResult.data) {
      renderRetirementBlockers(assessResult.data, 'retirement-blocker-assessment');
      // Reveal the assessment container
      let assessContainer = document.getElementById('retirement-blocker-assessment-wrapper');
      if (assessContainer) assessContainer.classList.remove('hidden');
    }

    let data = result.data;
    showImpactResults();
    hideBlastResults();

    // Update target app info (from search input since blocker API doesn't return it)
    let searchName = document.getElementById('impact-app-search')?.value || ('App #' + appId);
    document.getElementById('target-app-name').textContent = searchName;
    document.getElementById('target-app-lifecycle').textContent = '-';
    document.getElementById('target-app-criticality').textContent = data.total_count + ' dependencies';

    // IA-011: display canonical risk level when available
    let canonicalImpact = result.canonical_impact;
    let canonicalBanner = document.getElementById('canonical-risk-banner');
    if (canonicalImpact && canonicalImpact.risk_level) {
      let riskLevel = canonicalImpact.risk_level.toUpperCase();
      let riskClass = riskLevel === 'CRITICAL' || riskLevel === 'HIGH'
        ? 'border-destructive/30 bg-destructive/5 text-destructive'
        : riskLevel === 'MEDIUM'
          ? 'border-amber-400/30 bg-amber-50 text-amber-700'
          : 'border-emerald-400/30 bg-emerald-50 text-emerald-700';
      if (!canonicalBanner) {
        canonicalBanner = document.createElement('div');
        canonicalBanner.id = 'canonical-risk-banner';
        let impactResults = document.getElementById('impact-results');
        if (impactResults) impactResults.prepend(canonicalBanner);
      }
      safeHTML(canonicalBanner,
        '<div class="flex items-center gap-3 p-3 mb-4 rounded-lg border ' + riskClass + '">' +
          '<strong class="text-sm font-semibold">Canonical Impact Risk:</strong> ' +
          '<span class="text-sm font-bold">' + escapeHtml(riskLevel) + '</span>' +
          (canonicalImpact.summary ? ' — <span class="text-sm">' + escapeHtml(canonicalImpact.summary) + '</span>' : '') +
        '</div>'
      );
    } else if (canonicalBanner) {
      canonicalBanner.innerHTML = '';
    }

    // Update blockers display
    document.getElementById('total-blockers').textContent = data.total_count;
    document.getElementById('critical-blockers').textContent = data.critical_count;
    document.getElementById('blockers-count').textContent = data.total_count;

    // Update retire verdict — rebuild innerHTML entirely to avoid self-reference corruption
    let indicatorEl = document.getElementById('can-retire-indicator');
    if (data.has_critical_blockers) {
      safeHTML(indicatorEl, '<i data-lucide="x-circle" class="w-6 h-6 text-destructive"></i>' +
        '<span id="retire-verdict" class="text-lg font-semibold text-destructive">BLOCKED - Critical Dependencies</span>');
    } else if (data.has_blockers) {
      safeHTML(indicatorEl, '<i data-lucide="alert-triangle" class="w-6 h-6 text-amber-500"></i>' +
        '<span id="retire-verdict" class="text-lg font-semibold text-amber-600">CAUTION - Has Dependencies</span>');
    } else {
      safeHTML(indicatorEl, '<i data-lucide="check-circle" class="w-6 h-6 text-emerald-500"></i>' +
        '<span id="retire-verdict" class="text-lg font-semibold text-emerald-600">SAFE - No Blockers</span>');
    }

    // Populate blockers list (build once, set once to avoid DOM re-parsing in loop)
    let blockersList = document.getElementById('blockers-list');

    if (data.blockers && data.blockers.length > 0) {
      document.getElementById('blockers-results').classList.remove('hidden');
      document.getElementById('no-blockers-message').classList.add('hidden');

      let blockersHtml = data.blockers.map(function(blocker) {
        let isCritical = blocker.is_critical;
        return '<div class="flex items-center gap-3 p-3 rounded-lg border ' + (isCritical ? 'border-destructive/20 bg-destructive/5' : 'border-input bg-background') + '">' +
            '<div class="h-8 w-8 rounded-lg ' + (isCritical ? 'bg-destructive/10' : 'bg-slate-100') + ' flex items-center justify-center flex-shrink-0">' +
              '<i data-lucide="' + (isCritical ? 'alert-octagon' : 'link') + '" class="w-4 h-4 ' + (isCritical ? 'text-destructive' : 'text-slate-600') + '"></i>' +
            '</div>' +
            '<div class="flex-1 min-w-0">' +
              '<p class="font-medium text-sm truncate">' + escapeHtml(blocker.source_app_name) + '</p>' +
              '<p class="text-xs text-muted-foreground">' +
                escapeHtml(blocker.dependency_type) + ' | Strength: ' + escapeHtml(blocker.dependency_strength || 'unknown') +
                (blocker.blocks_retirement ? ' | <span class="text-destructive font-medium">Blocks Retirement</span>' : '') +
              '</p>' +
            '</div>' +
            (isCritical ? '<span class="px-2 py-0.5 text-xs font-bold rounded bg-destructive/10 text-destructive">CRITICAL</span>' : '') +
          '</div>';
      }).join('');
      safeHTML(blockersList, blockersHtml);
    } else {
      safeHTML(blockersList, '');
      document.getElementById('blockers-results').classList.add('hidden');
      document.getElementById('no-blockers-title').textContent = 'Safe to Retire';
      document.getElementById('no-blockers-detail').textContent = 'No blocking dependencies found. This application can be safely retired.';
      document.getElementById('no-blockers-message').classList.remove('hidden');
    }

    // Re-initialize Lucide icons
    if (typeof lucide !== 'undefined') {
      lucide.createIcons();
    }

  } catch (error) {
    showToast('Failed to check retirement blockers: ' + error.message, 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

// Check blast radius for an application
async function checkBlastRadius() {
  let appId = validateAppId('impact-app-id');
  if (!appId) {
    showToast('Please search and select a valid application', 'warning');
    return;
  }

  let blastBtn = document.getElementById('blast-radius-btn');
  setButtonLoading(blastBtn, true, 'Analyzing...');

  try {
    let result = await Platform.fetch.get('/dashboard/api/rationalization/blast-radius/' + appId + '?depth=3', null, { silent: true });

    if (!result.success) {
      showToast('Error: ' + (result.error || 'Unknown error'), 'error');
      return;
    }

    let data = result.data;
    showImpactResults();
    hideBlockersResults();

    // Update target app info
    if (data.target_app) {
      document.getElementById('target-app-name').textContent = data.target_app.name;
      document.getElementById('target-app-lifecycle').textContent = data.target_app.lifecycle_status || '-';
      document.getElementById('target-app-criticality').textContent = data.target_app.business_criticality || '-';
    }

    // Update blast summary
    document.getElementById('direct-dependents').textContent = data.direct_dependent_count;
    document.getElementById('total-affected').textContent = data.total_affected_count;
    document.getElementById('critical-paths').textContent = data.critical_path_count;
    document.getElementById('decoupling-cost').textContent =
      data.estimated_total_decoupling_cost > 0
        ? formatCurrency(data.estimated_total_decoupling_cost)
        : '-';

    // Update risk level badge
    let riskBadge = document.getElementById('blast-risk-level');
    let riskColors = {
      'low': 'bg-emerald-500/10 text-emerald-700',
      'medium': 'bg-amber-100 text-amber-700',
      'high': 'bg-orange-100 text-orange-700',
      'critical': 'bg-destructive/10 text-destructive'
    };
    riskBadge.textContent = (data.risk_level || 'unknown').toUpperCase();
    riskBadge.className = 'px-2 py-0.5 text-xs font-bold rounded-full ' + (riskColors[data.risk_level] || 'bg-slate-100 text-slate-700');

    // Populate dependency tree
    let treeEl = document.getElementById('dependency-tree');
    safeHTML(treeEl, '');

    if (data.total_affected_count === 0) {
      document.getElementById('blast-results').classList.add('hidden');
      document.getElementById('no-blockers-title').textContent = 'No Impact Detected';
      document.getElementById('no-blockers-detail').textContent = 'No downstream applications would be affected by retiring this application.';
      document.getElementById('no-blockers-message').classList.remove('hidden');
    } else {
      document.getElementById('blast-results').classList.remove('hidden');
      document.getElementById('no-blockers-message').classList.add('hidden');

      // Build all HTML at once to avoid repeated DOM re-parsing
      let treeHtmlParts = [];

      // Direct dependents
      if (data.direct_dependents && data.direct_dependents.length > 0) {
        treeHtmlParts.push('<p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Level 1 - Direct Dependents</p>');
        data.direct_dependents.forEach(function(dep) {
          treeHtmlParts.push(createDependentCard(dep));
        });
      }

      // Indirect dependents by level
      if (data.indirect_dependents) {
        Object.keys(data.indirect_dependents).sort().forEach(function(levelKey) {
          let levelNum = levelKey.replace('level_', '');
          let deps = data.indirect_dependents[levelKey];
          if (deps && deps.length > 0) {
            treeHtmlParts.push('<p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2 mt-4">Level ' + levelNum + ' - Indirect Impact</p>');
            deps.forEach(function(dep) {
              treeHtmlParts.push(createDependentCard(dep));
            });
          }
        });
      }

      safeHTML(treeEl, treeHtmlParts.join(''));
    }

    // Re-initialize Lucide icons
    if (typeof lucide !== 'undefined') {
      lucide.createIcons();
    }

  } catch (error) {
    showToast('Failed to check blast radius: ' + error.message, 'error');
  } finally {
    setButtonLoading(blastBtn, false);
  }
}

function createDependentCard(dep) {
  let isCritical = dep.is_critical;
  return '<div class="flex items-center gap-3 p-3 rounded-lg border ' + (isCritical ? 'border-destructive/20 bg-destructive/5' : 'border-input bg-background') + '">' +
      '<div class="h-8 w-8 rounded-lg ' + (isCritical ? 'bg-destructive/10' : 'bg-slate-100') + ' flex items-center justify-center flex-shrink-0">' +
        '<i data-lucide="box" class="w-4 h-4 ' + (isCritical ? 'text-destructive' : 'text-slate-600') + '"></i>' +
      '</div>' +
      '<div class="flex-1 min-w-0">' +
        '<p class="font-medium text-sm truncate">' + escapeHtml(dep.app_name) + '</p>' +
        '<p class="text-xs text-muted-foreground">' +
          escapeHtml(dep.dependency_type) + ' | ' + escapeHtml(dep.lifecycle_status || '-') + ' | ' + escapeHtml(dep.business_criticality || '-') +
        '</p>' +
      '</div>' +
      (isCritical ? '<span class="px-2 py-0.5 text-xs font-bold rounded bg-destructive/10 text-destructive">CRITICAL</span>' : '') +
    '</div>';
}

// ============================================================================
// Readiness Badge & Missing-Data Summary Helpers (RAT-101)
// ============================================================================

/**
 * Return an HTML badge string for the decision-readiness state of an app.
 *
 * @param {boolean}  isDecisionReady  - True when all high-severity dimensions are satisfied.
 * @param {number}   readinessScore   - Fraction 0.0–1.0 of satisfied dimensions.
 * @returns {string} HTML string for inline injection via safeHTML.
 */
function renderReadinessBadge(isDecisionReady, readinessScore) {
  let pct = typeof readinessScore === 'number' ? Math.round(readinessScore * 100) : null;
  let pctLabel = pct !== null ? ' (' + pct + '%)' : '';
  if (isDecisionReady) {
    return '<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-semibold ' +
           'bg-emerald-500/10 text-emerald-700 border border-emerald-500/30">' +
           '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
           '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>' +
           '</svg>' +
           'Decision Ready' + pctLabel +
           '</span>';
  }
  let color = pct !== null && pct >= 50
    ? 'bg-warning/10 text-warning border-warning/30'
    : 'bg-destructive/10 text-destructive border-destructive/30';
  return '<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-semibold ' + color + ' border">' +
         '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
         '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
         'd="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>' +
         '</svg>' +
         'Incomplete' + pctLabel +
         '</span>';
}

/**
 * Render an expandable missing-data summary for an app that is not decision-ready.
 *
 * @param {string[]} missingCritical - List of high-severity dimension names that are missing.
 * @param {string}   containerId     - ID of the element to inject into (will use safeHTML).
 */
function renderMissingReasonsList(missingCritical, containerId) {
  let el = document.getElementById(containerId);
  if (!el) return;
  if (!missingCritical || missingCritical.length === 0) {
    safeHTML(el, '');
    return;
  }
  let DIMENSION_LABELS = {
    owner: 'Application Owner',
    lifecycle: 'Lifecycle Status',
    cost: 'Cost / TCO Data',
    business_criticality: 'Business Criticality',
    tech_stack: 'Technology Stack',
    vendor: 'Vendor Link',
    processes: 'Supported Processes',
    risk: 'Risk Assessment',
  };
  let items = missingCritical.map(function(dim) {
    let label = DIMENSION_LABELS[dim] || dim.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
    return '<li class="flex items-center gap-1.5">' +
           '<svg class="w-3 h-3 text-destructive flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
           '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>' +
           '</svg>' +
           '<span>' + escapeHtml(label) + '</span>' +
           '</li>';
  }).join('');
  safeHTML(el,
    '<div class="mt-2 p-3 rounded-md bg-destructive/5 border border-destructive/20">' +
    '<p class="text-xs font-semibold text-destructive mb-1.5">Missing evidence (blocks decision):</p>' +
    '<ul class="text-xs text-muted-foreground space-y-1">' + items + '</ul>' +
    '</div>'
  );
}

/**
 * Build an inline readiness detail block for use in the Options Analysis panel
 * and other per-app detail surfaces.
 *
 * @param {Object} readiness  - { is_decision_ready, readiness_score, missing_critical }
 * @returns {string} HTML string.
 */
function buildReadinessDetailBlock(readiness) {
  if (!readiness) return '';
  let badge = renderReadinessBadge(readiness.is_decision_ready, readiness.readiness_score);
  let missing = readiness.missing_critical || readiness.missing || [];
  let DIMENSION_LABELS = {
    owner: 'Application Owner',
    lifecycle: 'Lifecycle Status',
    cost: 'Cost / TCO Data',
    business_criticality: 'Business Criticality',
    tech_stack: 'Technology Stack',
    vendor: 'Vendor Link',
    processes: 'Supported Processes',
    risk: 'Risk Assessment',
  };
  let missingHtml = '';
  if (!readiness.is_decision_ready && missing.length > 0) {
    let items = missing.map(function(dim) {
      let label = DIMENSION_LABELS[dim] || dim.replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
      return '<li class="flex items-center gap-1.5">' +
             '<svg class="w-3 h-3 text-destructive flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
             '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
             '<span>' + escapeHtml(label) + '</span>' +
             '</li>';
    }).join('');
    missingHtml =
      '<ul class="mt-1.5 text-xs text-muted-foreground space-y-1">' + items + '</ul>';
  }
  return '<div class="flex items-start gap-2 flex-wrap">' +
         badge +
         (missingHtml
           ? '<details class="w-full">' +
             '<summary class="text-xs text-muted-foreground cursor-pointer hover:text-foreground transition-colors">Show missing data</summary>' +
             missingHtml +
             '</details>'
           : '') +
         '</div>';
}

function showImpactResults() {
  document.getElementById('impact-results').classList.remove('hidden');
  document.getElementById('target-app-info').classList.remove('hidden');
}

function hideBlockersResults() {
  document.getElementById('blockers-results').classList.add('hidden');
}

function hideBlastResults() {
  document.getElementById('blast-results').classList.add('hidden');
}

// ============================================================================
// Options Analysis Functions
// ============================================================================

let optionsRadarChart = null;

// ── Null-safe score handling ────────────────────────────────────────────────
// A score of null means "not calculated". It must never be coerced to 0: a 0 is
// indistinguishable from a measured zero, ranks the option last as though it had
// been evaluated, and plots as a real point on the radar chart.
const SCORE_UNAVAILABLE = '—';  // em dash: the null display per CLAUDE.md

function hasScore(value) {
  return typeof value === 'number' && isFinite(value);
}

// Descending by score. Unscored options sink below every scored one and keep
// their catalogue order relative to each other (Array#sort is stable).
function compareByOverallScoreDesc(a, b) {
  let aScored = hasScore(a.overall_score);
  let bScored = hasScore(b.overall_score);
  if (aScored && bScored) return b.overall_score - a.overall_score;
  if (aScored) return -1;
  if (bScored) return 1;
  return 0;
}

async function analyzeOptions() {
  let appId = validateAppId('options-app-id');
  if (!appId) {
    showToast('Please search and select a valid application', 'warning');
    return;
  }

  let optionsBtn = document.getElementById('analyze-options-btn');
  setButtonLoading(optionsBtn, true, 'Analyzing...');

  try {
    // First, get the application's rationalization score to understand its current state
    let scoreData = await Platform.fetch.post('/dashboard/api/rationalization/calculate/' + appId);
    if (!scoreData.success) {
      throw new Error(scoreData.error || 'Failed to get application score');
    }

    let appScore = scoreData.data;
    let timeAction = appScore.time_action || 'TOLERATE';

    // Generate options based on TIME action
    let options = generateOptionsForTimeAction(timeAction, appScore);

    // Call options analysis API (may return 503 if engine not available)
    let useLocalFallback = false;
    try {
      let data = await Platform.fetch.post('/dashboard/api/rationalization/options-analysis/' + appId, {
        requirements: {
          business_criticality: appScore.business_score > 70 ? 'HIGH' : appScore.business_score > 40 ? 'MEDIUM' : 'LOW',
          current_technical_score: appScore.technical_score,
          current_cost_score: appScore.cost_score,
          time_action: timeAction
        },
        options: options
      });

      if (data.success && data.data && data.data.results) {
        displayOptionsResults(appId, data.data, appScore);
      } else if (data.success && data.data) {
        displayOptionsResults(appId, { results: [data.data] }, appScore);
      } else {
        useLocalFallback = true;
      }
    } catch (fetchError) {
      // Platform.fetch throws on network or HTTP error — use local analysis
      useLocalFallback = true;
    }

    if (useLocalFallback) {
      displayLocalOptionsResults(appId, options, appScore, timeAction);
    }

  } catch (error) {
    // Only show error fallback if score calculation itself failed
    displayFallbackOptions(appId, error.message);
  } finally {
    setButtonLoading(optionsBtn, false);
  }
}

function generateOptionsForTimeAction(timeAction, appScore) {
  // The strategy catalogue for a TIME action: names, descriptions and the
  // qualitative shape of each option. These are static templates, not claims
  // about this portfolio.
  //
  // `cost_estimates` is deliberately empty. It previously carried invented
  // figures (annual_cost 120000, decommission_cost 30000, annual_savings
  // 100000 …) which had no source in the user's data, yet were posted to the
  // scoring engine and surfaced as cost analysis. An empty object is the honest
  // input — the engine already treats "no cost data" as lower confidence —
  // whereas a fabricated one silently anchors a decommissioning business case.
  let options = [];

  if (timeAction === 'MIGRATE' || timeAction === 'INVEST') {
    options.push({
      id: 'opt-saas',
      name: 'Cloud SaaS Migration',
      description: 'Migrate to modern cloud-native SaaS platform',
      technical_specs: { deployment: 'cloud', scalability: 'high', maintenance: 'vendor-managed' },
      cost_estimates: {},
      metadata: { reduces_tech_debt: true, improves_scalability: true }
    });
    options.push({
      id: 'opt-modernize',
      name: 'Modernize In-Place',
      description: 'Upgrade current system with modern technologies',
      technical_specs: { deployment: 'hybrid', scalability: 'medium', maintenance: 'internal' },
      cost_estimates: {},
      metadata: { preserves_customization: true, gradual_transition: true }
    });
  }

  if (timeAction === 'INVEST') {
    options.push({
      id: 'opt-enhance',
      name: 'Strategic Enhancement',
      description: 'Invest in new features and capabilities',
      technical_specs: { deployment: 'current', scalability: 'improved', maintenance: 'internal' },
      cost_estimates: {},
      metadata: { business_growth: true, competitive_advantage: true }
    });
  }

  if (timeAction === 'TOLERATE') {
    options.push({
      id: 'opt-maintain',
      name: 'Continue As-Is',
      description: 'Maintain current system with minimal changes',
      technical_specs: { deployment: 'current', scalability: 'current', maintenance: 'minimal' },
      cost_estimates: {},
      metadata: { low_risk: true, stable: true }
    });
    options.push({
      id: 'opt-optimize',
      name: 'Cost Optimization',
      description: 'Optimize licenses and reduce operational costs',
      technical_specs: { deployment: 'current', scalability: 'current', maintenance: 'optimized' },
      cost_estimates: {},
      metadata: { cost_reduction: true }
    });
  }

  if (timeAction === 'ELIMINATE') {
    options.push({
      id: 'opt-retire',
      name: 'Planned Retirement',
      description: 'Phase out and decommission the application',
      technical_specs: { deployment: 'none', timeline: '6-12 months' },
      cost_estimates: {},
      metadata: { data_migration_required: true }
    });
    options.push({
      id: 'opt-consolidate',
      name: 'Consolidate with Existing',
      description: 'Merge functionality into existing applications',
      technical_specs: { deployment: 'existing', migration_complexity: 'medium' },
      cost_estimates: {},
      metadata: { reduces_portfolio: true }
    });
  }

  return options;
}

function displayLocalOptionsResults(appId, options, appScore, timeAction) {
  // The options-analysis service is unavailable, so nothing has been scored.
  //
  // The candidate strategies are still worth showing — they are a static
  // catalogue, not a claim about this portfolio — but every number stays null.
  // These fields used to be manufactured: overall_score from the option's
  // position in the list, confidence hard-coded at 0.60, each criterion an
  // arithmetic nudge of an unrelated health score. On screen those are
  // indistinguishable from measurements, and an architect can carry them into a
  // decommissioning business case. Null renders as an em dash (CLAUDE.md).
  let results = options.map(function(opt) {
    return {
      option_id: opt.id,
      option_name: opt.name,
      overall_score: null,
      confidence_score: null,
      criteria_scores: {
        cost_efficiency: null,
        technical_fit: null,
        risk_level: null,
        strategic_alignment: null,
        implementation_ease: null
      },
      cost_estimates: {},
      recommendations: [opt.description]
    };
  });

  displayOptionsResults(appId, { results: results, _local: true }, appScore);
}

function displayFallbackOptions(appId, errorMsg) {
  // This path renders the error itself and invents no scores or costs. It must
  // also clear anything left over from an earlier, successful analysis —
  // a previous app's banner or evidence trail sitting above this error card
  // reads as though it described the application that just failed.
  currentAnalysisData = null;
  currentAppScore = null;

  let staleBanner = document.getElementById('local-estimate-warning');
  if (staleBanner) staleBanner.remove();

  let evidenceContainer = document.getElementById('options-evidence-trail');
  if (evidenceContainer) safeHTML(evidenceContainer, '');

  let readinessEl = document.getElementById('options-readiness-indicator');
  if (readinessEl) safeHTML(readinessEl, '');

  document.getElementById('options-results').classList.remove('hidden');
  let appName = document.getElementById('options-app-search')?.value || ('Application #' + appId);
  document.getElementById('options-app-name').textContent = appName;
  document.getElementById('options-time-action').textContent = 'ANALYZE';
  document.getElementById('options-app-criticality').textContent = 'Pending Analysis';

  let tableEl = document.getElementById('options-comparison-table');
  safeHTML(tableEl, '<div class="p-4 bg-amber-50 border border-amber-200 rounded-lg">' +
      '<p class="text-amber-800 font-medium">Options analysis service temporarily unavailable</p>' +
      '<p class="text-sm text-amber-700 mt-1">Error: ' + escapeHtml(String(errorMsg)) + '</p>' +
      '<p class="text-sm text-muted-foreground mt-2">Please try again or contact support if the issue persists.</p>' +
    '</div>');

  // Clear stale radar chart from previous analysis
  if (optionsRadarChart) {
    optionsRadarChart.destroy();
    optionsRadarChart = null;
  }
}

// Store current analysis data for business case generation
let currentAnalysisData = null;
let currentAppScore = null;

// String-based HTML escaping for generated document content (unlike DOM-based escapeHtml)
function escapeForDoc(str) {
  if (str === null || str === undefined) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function displayOptionsResults(appId, analysisData, appScore) {
  // Store for business case generation
  currentAnalysisData = analysisData;
  currentAppScore = appScore;

  // Show results section
  document.getElementById('options-results').classList.remove('hidden');

  // Display actual app name from autocomplete (fallback to ID)
  let appName = document.getElementById('options-app-search')?.value || ('Application #' + appId);
  document.getElementById('options-app-name').textContent = appName;

  if (appScore) {
    let timeAction = appScore.time_action || 'ANALYZE';
    document.getElementById('options-time-action').textContent = timeAction;

    let criticality = appScore.business_score > 70 ? 'Business Critical' :
                        appScore.business_score > 40 ? 'Important' : 'Standard';
    document.getElementById('options-app-criticality').textContent = criticality;
  } else {
    document.getElementById('options-time-action').textContent = 'ANALYZE';
    document.getElementById('options-app-criticality').textContent = 'Pending';
  }

  // Render decision-readiness indicator (RAT-101)
  let readinessEl = document.getElementById('options-readiness-indicator');
  if (readinessEl) {
    let readiness = appScore && appScore.readiness;
    if (readiness) {
      safeHTML(readinessEl, buildReadinessDetailBlock(readiness));
    } else {
      safeHTML(readinessEl, '');
    }
  }

  // Render evidence trail (RAT-104) — show score drivers so architects can
  // challenge or approve the recommendation.  The dedicated evidence-trail API
  // is called only when a real app ID is present; skipped for local fallback
  // results which don't have a persisted score.
  let evidenceContainer = document.getElementById('options-evidence-trail');
  if (evidenceContainer) {
    let appIdForEvidence = validateAppId('options-app-id');
    if (appIdForEvidence && !analysisData._local && appScore && appScore.evidence_trail) {
      // Evidence already returned inline from the calculate API — render directly
      renderEvidenceTrail(appScore.evidence_trail, 'options-evidence-trail', {
        overallScore: appScore.overall_score,
        timeAction: appScore.time_action,
        dispositionAction: appScore.disposition_action,
        dispositionConfidence: appScore.disposition_confidence,
        confidenceReasons: appScore.confidence_reasons || [],
      });
    } else if (appIdForEvidence && !analysisData._local) {
      // Fetch evidence trail from the dedicated endpoint
      loadAndRenderEvidenceTrail(appIdForEvidence, 'options-evidence-trail');
    } else {
      // Local fallback — note that evidence is based on the inline score data only
      if (appScore && appScore.evidence_trail) {
        renderEvidenceTrail(appScore.evidence_trail, 'options-evidence-trail', {
          overallScore: appScore.overall_score,
          timeAction: appScore.time_action,
        });
      } else {
        safeHTML(evidenceContainer,
          '<p class="text-xs text-muted-foreground py-2">No evidence trail: the options-analysis service was unavailable, so no scores were calculated. Run a live analysis to see score drivers.</p>'
        );
      }
    }
  }

  // Manage warning banner for locally-derived estimates
  // Always remove previous banner first to prevent duplicates
  let existingBanner = document.getElementById('local-estimate-warning');
  if (existingBanner) existingBanner.remove();

  if (analysisData._local) {
    let bannerContainer = document.getElementById('options-comparison-table');
    let warningBanner = document.createElement('div');
    warningBanner.className = 'mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg flex items-start gap-2';
    warningBanner.id = 'local-estimate-warning';
    safeHTML(warningBanner, '<svg class="w-5 h-5 text-amber-600 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
        '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"/>' +
      '</svg>' +
      '<div>' +
        '<p class="text-sm font-medium text-amber-800">Scores and costs could not be calculated</p>' +
        '<p class="text-xs text-amber-700 mt-0.5">The options-analysis service was unavailable. The candidate strategies below are listed for reference only — no score, confidence or cost figure was produced for any of them, so those columns show —. Re-run the analysis once the service is available.</p>' +
      '</div>');
    bannerContainer.parentNode.insertBefore(warningBanner, bannerContainer);
  }

  // Build comparison table
  if (analysisData.results && analysisData.results.length > 0) {
    buildOptionsComparisonTable(analysisData.results);
    buildRadarChart(analysisData.results);
  } else {
    // No options at all — say so, and clear the previous application's table
    // and chart rather than leaving them under this application's name.
    safeHTML(document.getElementById('options-comparison-table'),
      '<p class="text-sm text-muted-foreground p-2">No options were returned for this application.</p>');
    buildRadarChart([]);
  }
}

function buildOptionsComparisonTable(results) {
  let tableEl = document.getElementById('options-comparison-table');

  // Sort by overall score descending; unscored options keep catalogue order at the end
  let sorted = [].concat(results).sort(compareByOverallScoreDesc);

  let html = '<div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr class="border-b">';
  html += '<th class="text-left p-2 font-semibold">Rank</th>';
  html += '<th class="text-left p-2 font-semibold">Option</th>';
  html += '<th class="text-center p-2 font-semibold">Overall Score</th>';
  html += '<th class="text-center p-2 font-semibold">Confidence</th>';
  html += '<th class="text-left p-2 font-semibold">Recommendation</th>';
  html += '</tr></thead><tbody>';

  sorted.forEach(function(result, idx) {
    // An unscored option has no rank — labelling it "Best" would assert an
    // ordering that was never computed.
    let rankBadge;
    if (!hasScore(result.overall_score)) {
      rankBadge = '<span class="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground" title="Not ranked — no score calculated">' + SCORE_UNAVAILABLE + '</span>';
    } else if (idx === 0) {
      rankBadge = '<span class="px-2 py-0.5 text-xs font-bold rounded bg-emerald-500/10 text-emerald-700">Best</span>';
    } else if (idx === 1) {
      rankBadge = '<span class="px-2 py-0.5 text-xs font-bold rounded bg-primary/10 text-primary">2nd</span>';
    } else {
      rankBadge = '<span class="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground">' + (idx + 1) + '</span>';
    }

    let scoreCell = hasScore(result.overall_score)
      ? '<span class="font-semibold">' + result.overall_score.toFixed(1) + '</span>/100'
      : '<span class="text-muted-foreground" title="Not calculated">' + SCORE_UNAVAILABLE + '</span>';
    let confidenceCell = hasScore(result.confidence_score)
      ? (result.confidence_score * 100).toFixed(0) + '%'
      : '<span class="text-muted-foreground" title="Not calculated">' + SCORE_UNAVAILABLE + '</span>';

    html += '<tr class="border-b hover:bg-muted/30">';
    html += '<td class="p-2">' + rankBadge + '</td>';
    html += '<td class="p-2 font-medium">' + escapeHtml(result.option_name || '') + '</td>';
    html += '<td class="p-2 text-center">' + scoreCell + '</td>';
    html += '<td class="p-2 text-center">' + confidenceCell + '</td>';
    html += '<td class="p-2 text-xs text-muted-foreground">' + escapeHtml(result.recommendations?.[0] || SCORE_UNAVAILABLE) + '</td>';
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  safeHTML(tableEl, html);
}

function buildRadarChart(results) {
  // Only options that actually carry at least one calculated criterion can be
  // plotted. A null criterion is not a zero, so it is never given a point.
  let plottable = (results || []).filter(function(result) {
    let scores = result.criteria_scores;
    return scores && Object.keys(scores).some(function(key) { return hasScore(scores[key]); });
  });

  // Destroy any chart left over from a previous analysis before deciding what
  // to draw — a stale chart alongside fresh results would misattribute figures.
  if (optionsRadarChart) {
    optionsRadarChart.destroy();
    optionsRadarChart = null;
  }

  if (plottable.length === 0) {
    // Nothing was calculated. An empty panel is the correct outcome; plotting
    // zeros would invent five measurements per option.
    return;
  }

  let ctx = document.getElementById('options-radar-chart').getContext('2d');

  // Extract criteria labels from the first plottable result
  let criteriaLabels = Object.keys(plottable[0].criteria_scores);

  // Build datasets for each option
  let datasets = plottable.slice(0, 3).map(function(result, idx) {
    let colors = [
      { bg: 'rgba(139, 92, 246, 0.2)', border: 'rgba(139, 92, 246, 1)' },  // purple
      { bg: 'rgba(59, 130, 246, 0.2)', border: 'rgba(59, 130, 246, 1)' },  // blue
      { bg: 'rgba(16, 185, 129, 0.2)', border: 'rgba(16, 185, 129, 1)' }   // green
    ];

    return {
      label: result.option_name,
      // null leaves a gap in Chart.js rather than drawing a point at the origin
      data: criteriaLabels.map(function(c) {
        return hasScore(result.criteria_scores[c]) ? result.criteria_scores[c] : null;
      }),
      backgroundColor: colors[idx].bg,
      borderColor: colors[idx].border,
      borderWidth: 2,
      pointBackgroundColor: colors[idx].border,
      pointBorderColor: '#fff',
      pointHoverBackgroundColor: '#fff',
      pointHoverBorderColor: colors[idx].border
    };
  });

  optionsRadarChart = new Chart(ctx, {
    type: 'radar',
    data: {
      labels: criteriaLabels.map(function(c) { return c.replace(/_/g, ' ').toUpperCase(); }),
      datasets: datasets
    },
    options: {
      scales: {
        r: {
          beginAtZero: true,
          max: 100,
          ticks: { stepSize: 20 }
        }
      },
      plugins: {
        legend: { position: 'bottom' },
        title: {
          display: true,
          text: 'Multi-Criteria Decision Analysis'
        }
      }
    }
  });
}

// A dimension score of null means "not measured" and must never be coerced to 0:
// a 0 reads as a measured worst case and would raise a risk the data never showed.
const SCORE_NOT_MEASURED = 'Not measured';

function buildScoreTableHtml(score) {
  if (!score) {
    return '<p>Score data unavailable. Run Options Analysis to populate financial metrics.</p>';
  }
  function row(label, value, bands) {
    if (!hasScore(value)) {
      return '<tr><td>' + label + '</td><td>' + SCORE_NOT_MEASURED + '</td><td>' + SCORE_NOT_MEASURED + '</td></tr>';
    }
    return '<tr><td>' + label + '</td><td>' + value.toFixed(1) + '/100</td><td>' +
      (value >= 70 ? bands[0] : value >= 40 ? bands[1] : bands[2]) + '</td></tr>';
  }
  let html = '<table>' +
    '<thead><tr><th>Dimension</th><th>Score</th><th>Assessment</th></tr></thead>' +
    '<tbody>' +
      row('Technical Health', score.technical_score, ['Healthy', 'Needs Attention', 'Critical']) +
      row('Business Value', score.business_score, ['High Value', 'Moderate Value', 'Low Value']) +
      row('Cost Efficiency', score.cost_score, ['Efficient', 'Average', 'Inefficient']) +
      row('Vendor Risk', score.vendor_score, ['Low Risk', 'Moderate Risk', 'High Risk']) +
    '</tbody></table>';
  if (score.rationale) {
    html += '<p><strong>TIME Rationale:</strong> ' + escapeForDoc(score.rationale) + '</p>';
  }
  return html;
}

function buildRiskItemsHtml(score) {
  let items = '';
  let flagged = false;
  if (score) {
    if (hasScore(score.technical_score) && score.technical_score < 40) {
      flagged = true;
      items += '<li><strong>Technical Debt Risk:</strong> Technical health score is below 40, indicating significant technical debt that may increase migration or maintenance costs.</li>';
    }
    if (hasScore(score.cost_score) && score.cost_score < 40) {
      flagged = true;
      items += '<li><strong>Cost Inefficiency Risk:</strong> Cost efficiency score is below 40, suggesting the application may be consuming disproportionate resources.</li>';
    }
    if (hasScore(score.vendor_score) && score.vendor_score < 40) {
      flagged = true;
      items += '<li><strong>Vendor Concentration Risk:</strong> Vendor risk score is below 40, indicating potential single-vendor dependency or contract concerns.</li>';
    }
    if (hasScore(score.business_score) && score.business_score > 70) {
      flagged = true;
      items += '<li><strong>Business Continuity Risk:</strong> High business value (' + score.business_score.toFixed(0) + ') means any disruption during transition will have significant impact.</li>';
    }
  }
  if (!flagged) {
    items += '<li>No critical risk indicators detected based on the dimensions that were measured.</li>';
  }
  items += '<li>Implementation complexity and timeline should be assessed during detailed planning.</li>';
  items += '<li>Data migration and integration challenges should be evaluated per target architecture.</li>';
  return items;
}

function generateBusinessCase() {
  let appId = validateAppId('options-app-id');
  if (!appId) {
    showToast('Please search and select an application, then run analysis first', 'warning');
    return;
  }

  if (!currentAnalysisData || !currentAnalysisData.results) {
    showToast('Please run Options Analysis first before generating a business case', 'warning');
    return;
  }

  // Generate business case document
  let results = [].concat(currentAnalysisData.results); // copy to avoid mutating original
  if (results.length === 0) {
    showToast('No analysis results available to generate business case', 'warning');
    return;
  }
  let sortedResults = [].concat(results).sort(compareByOverallScoreDesc);
  let topOption = sortedResults[0];
  // When the options service was unavailable nothing carries a score, so the
  // document must not present a ranking or a recommended option.
  let isRanked = hasScore(topOption.overall_score);
  let timeAction = currentAppScore?.time_action || 'ANALYZE';
  let overallScore = currentAppScore?.overall_score || 'N/A';
  let appName = document.getElementById('options-app-search')?.value || ('Application #' + appId);

  // Create business case content
  let today = new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' });

  // Build criteria benefits list
  let criteriaHtml = '';
  if (topOption.criteria_scores) {
    let entries = Object.entries(topOption.criteria_scores).filter(function(entry) { return hasScore(entry[1]) && entry[1] > 70; });
    criteriaHtml = entries.map(function(entry) {
      let label = entry[0].replace(/_/g, ' ').replace(/\b\w/g, function(c) { return c.toUpperCase(); });
      return '<li>' + label + ': ' + entry[1].toFixed(0) + '%</li>';
    }).join('');
  }
  if (!criteriaHtml) {
    criteriaHtml = '<li>Highest overall score among evaluated options</li>';
  }

  // Executive summary and recommendation depend on whether anything was scored.
  let summaryLine = isRanked
    ? 'Following analysis of ' + results.length + ' options, the highest-scoring approach is: <strong>' + escapeForDoc(topOption.option_name) + '</strong> with an overall score of ' + topOption.overall_score.toFixed(1) + '/100.'
    : 'The ' + results.length + ' candidate options listed below were not scored &mdash; the options-analysis service was unavailable &mdash; so this document contains no ranking and no recommended option. Re-generate it once scoring succeeds.';

  let recommendationBlock = isRanked
    ? '<h3>' + escapeForDoc(topOption.option_name) + '</h3>\n' +
      '    <p>' + escapeForDoc(topOption.recommendations ? topOption.recommendations[0] : 'Recommended based on multi-criteria analysis') + '</p>\n' +
      '    <p><strong>Key Benefits:</strong></p>\n' +
      '    <ul>\n      ' + criteriaHtml + '\n    </ul>'
    : '<h3>No recommendation available</h3>\n' +
      '    <p>None of the candidate options was scored, so no option can be recommended over another. The strategies above are the standard candidates for this TIME action and are listed for reference only.</p>';

  // Build financial table rows
  let financialRows = buildScoreTableHtml(currentAppScore);

  // Build options table rows — an unscored option gets no rank and no figures
  let optionsTableRows = sortedResults.map(function(r, i) {
    let ranked = hasScore(r.overall_score);
    return '<tr' + (i === 0 && ranked ? ' style="background: #ecfdf5;"' : '') + '>' +
      '<td>' + (ranked ? (i + 1) : '&mdash;') + '</td>' +
      '<td>' + escapeForDoc(r.option_name) + '</td>' +
      '<td>' + (ranked ? r.overall_score.toFixed(1) + '/100' : '&mdash;') + '</td>' +
      '<td>' + (hasScore(r.confidence_score) ? (r.confidence_score * 100).toFixed(0) + '%' : '&mdash;') + '</td>' +
    '</tr>';
  }).join('');

  // Build risk items
  let riskItems = buildRiskItemsHtml(currentAppScore);

  let businessCaseHtml = '<!DOCTYPE html>\n' +
'<html>\n' +
'<head>\n' +
'  <title>Business Case - ' + escapeForDoc(appName) + '</title>\n' +
'  <style>\n' +
'    body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; line-height: 1.6; }\n' +
'    h1 { color: #1e40af; border-bottom: 3px solid #3b82f6; padding-bottom: 10px; }\n' +
'    h2 { color: #1e3a8a; margin-top: 30px; }\n' +
'    h3 { color: #374151; }\n' +
'    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; }\n' +
'    .badge { display: inline-block; padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 14px; }\n' +
'    .badge-action { background: #dbeafe; color: #1e40af; }\n' +
'    .badge-score { background: #dcfce7; color: #166534; }\n' +
'    .section { margin: 20px 0; padding: 15px; background: #f9fafb; border-radius: 8px; border-left: 4px solid #3b82f6; }\n' +
'    table { width: 100%; border-collapse: collapse; margin: 15px 0; }\n' +
'    th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e5e7eb; }\n' +
'    th { background: #f3f4f6; font-weight: 600; }\n' +
'    .recommendation { background: #ecfdf5; border-left-color: #10b981; }\n' +
'    .risk { background: #fef2f2; border-left-color: #ef4444; }\n' +
'    .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #6b7280; }\n' +
'  </style>\n' +
'</head>\n' +
'<body>\n' +
'  <div class="header">\n' +
'    <h1>Business Case</h1>\n' +
'    <div>\n' +
'      <span class="badge badge-action">' + timeAction + '</span>\n' +
'      <span class="badge badge-score">Score: ' + (typeof overallScore === 'number' ? overallScore.toFixed(1) : overallScore) + '</span>\n' +
'    </div>\n' +
'  </div>\n' +
'\n' +
'  <p><strong>Application:</strong> ' + escapeForDoc(appName) + ' (ID: ' + appId + ')</p>\n' +
'  <p><strong>Date:</strong> ' + today + '</p>\n' +
'  <p><strong>Prepared by:</strong> Enterprise Architecture Team</p>\n' +
'\n' +
'  <h2>Executive Summary</h2>\n' +
'  <div class="section">\n' +
'    <p>This business case evaluates strategic options for ' + escapeForDoc(appName) + ' based on the TIME framework rationalization assessment. The recommended action is <strong>' + timeAction + '</strong>.</p>\n' +
'    <p>' + summaryLine + '</p>\n' +
'  </div>\n' +
'\n' +
'  <h2>Options Analysis</h2>\n' +
'  <table>\n' +
'    <thead>\n' +
'      <tr>\n' +
'        <th>Rank</th>\n' +
'        <th>Option</th>\n' +
'        <th>Score</th>\n' +
'        <th>Confidence</th>\n' +
'      </tr>\n' +
'    </thead>\n' +
'    <tbody>\n' +
'      ' + optionsTableRows + '\n' +
'    </tbody>\n' +
'  </table>\n' +
'\n' +
'  <h2>Recommendation</h2>\n' +
'  <div class="section recommendation">\n' +
'    ' + recommendationBlock + '\n' +
'  </div>\n' +
'\n' +
'  <h2>Financial Analysis</h2>\n' +
'  <div class="section">\n' +
'    ' + financialRows + '\n' +
'  </div>\n' +
'\n' +
'  <h2>Risk Assessment</h2>\n' +
'  <div class="section risk">\n' +
'    <p><strong>Data-Driven Risk Indicators:</strong></p>\n' +
'    <ul>\n' +
'      ' + riskItems + '\n' +
'    </ul>\n' +
'  </div>\n' +
'\n' +
'  <h2>Next Steps</h2>\n' +
'  <div class="section">\n' +
'    <ol>\n' +
'      <li>Review and approve this business case with stakeholders</li>\n' +
'      <li>Conduct detailed vendor/solution evaluation</li>\n' +
'      <li>Develop implementation roadmap and timeline</li>\n' +
'      <li>Secure budget and resource allocation</li>\n' +
'      <li>Initiate pilot/proof of concept if applicable</li>\n' +
'    </ol>\n' +
'  </div>\n' +
'\n' +
'  <div class="footer">\n' +
'    <p>Generated by Enterprise Architecture Rationalization System</p>\n' +
'    <p>This document is for internal use only. Data classification: Internal</p>\n' +
'  </div>\n' +
'</body>\n' +
'</html>';

  // Create downloadable file
  let blob = new Blob([businessCaseHtml], { type: 'text/html' });
  let url = URL.createObjectURL(blob);
  let link = document.createElement('a');
  link.href = url;
  link.download = 'business_case_app_' + appId + '_' + new Date().toISOString().split('T')[0] + '.html';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);

  showToast('Business case document downloaded successfully', 'success');
}

// ============================================================================
// Evidence Trail — Score Drivers / Transparency Panel (RAT-104)
// ============================================================================

/**
 * Fetch and render the full evidence trail for a single application.
 *
 * Calls GET /applications/rationalization/api/evidence-trail/<appId> and
 * injects the result into the element identified by containerId using
 * renderEvidenceTrail().
 *
 * @param {number} appId       - ApplicationComponent PK.
 * @param {string} containerId - ID of the target DOM element.
 */
async function loadAndRenderEvidenceTrail(appId, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;

  safeHTML(container,
    '<div class="flex items-center gap-2 py-4 text-sm text-muted-foreground">' +
      '<svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">' +
        '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
        '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
      '</svg>' +
      'Loading evidence trail…' +
    '</div>'
  );

  try {
    let data = await Platform.fetch.get('/applications/rationalization/api/evidence-trail/' + appId, null, { silent: true });
    if (!data.success) {
      throw new Error(data.error || 'Unknown error');
    }
    renderEvidenceTrail(data.evidence_trail, containerId, {
      overallScore: data.overall_score,
      timeAction: data.time_action,
      dispositionAction: data.disposition_action,
      dispositionConfidence: data.disposition_confidence,
      confidenceReasons: data.confidence_reasons || [],
      scoringConfigName: data.scoring_config_name,
    });
  } catch (err) {
    safeHTML(container,
      '<div class="p-3 rounded-md bg-destructive/5 border border-destructive/20 text-sm text-destructive">' +
        'Could not load evidence trail: ' + escapeHtml(err.message) +
      '</div>'
    );
  }
}

/**
 * Render a structured evidence trail as a compact, architect-friendly panel.
 *
 * Each entry in `evidence` represents a scoring dimension (Technical Health,
 * Business Value, etc.) and may contain a `sub_factors` array with per-field
 * evidence entries.  The panel uses an expandable <details> pattern so the
 * full breakdown is available without overwhelming the default view.
 *
 * All values come from the server — no percentages or scores are fabricated.
 *
 * @param {Array}  evidence      - Array of dimension evidence objects from the API.
 * @param {string} containerId   - ID of the DOM element to inject into.
 * @param {Object} [meta]        - Optional metadata for the header row:
 *   @param {number} [meta.overallScore]
 *   @param {string} [meta.timeAction]
 *   @param {string} [meta.dispositionAction]
 *   @param {string} [meta.dispositionConfidence]
 *   @param {string[]} [meta.confidenceReasons]  - Uncertainty reasons when confidence < high
 *   @param {string} [meta.scoringConfigName]
 */
function renderEvidenceTrail(evidence, containerId, meta) {
  let container = document.getElementById(containerId);
  if (!container) return;
  if (!evidence || evidence.length === 0) {
    safeHTML(container,
      '<p class="text-sm text-muted-foreground py-2">No evidence data available.</p>'
    );
    return;
  }

  meta = meta || {};
  let parts = [];

  // ── Header summary row ────────────────────────────────────────────────
  let timeColor = {
    'TOLERATE': 'bg-primary/10 text-primary border-primary/30',
    'INVEST': 'bg-emerald-500/10 text-emerald-700 border-emerald-500/30',
    'MIGRATE': 'bg-amber-500/10 text-amber-700 border-amber-500/30',
    'ELIMINATE': 'bg-destructive/10 text-destructive border-destructive/30',
  };
  let actionClass = timeColor[meta.timeAction] || 'bg-muted text-muted-foreground border-border';

  if (meta.overallScore !== undefined || meta.timeAction) {
    // ── Confidence indicator (RAT-105) ───────────────────────────────────
    // Build a styled confidence badge + expandable uncertainty reasons list
    // so low-confidence recommendations are visibly treated as tentative.
    let confidenceHtml = '';
    if (meta.dispositionAction) {
      let conf = meta.dispositionConfidence || '';
      let confBadgeClass, confLabel, confIcon;
      if (conf === 'high') {
        confBadgeClass = 'bg-success/10 text-success border-success/30';
        confLabel = 'High Confidence';
        confIcon = '&#10003;';
      } else if (conf === 'medium') {
        confBadgeClass = 'bg-amber-500/10 text-amber-700 border-amber-500/30';
        confLabel = 'Medium Confidence';
        confIcon = '&#9432;';
      } else if (conf === 'low') {
        confBadgeClass = 'bg-destructive/10 text-destructive border-destructive/30';
        confLabel = 'Low Confidence \u2014 Tentative';
        confIcon = '&#9888;';
      } else if (conf === 'none') {
        confBadgeClass = 'bg-muted text-muted-foreground border-border';
        confLabel = 'Insufficient Evidence';
        confIcon = '&#9866;';
      } else {
        confBadgeClass = 'bg-muted text-muted-foreground border-border';
        confLabel = conf ? escapeHtml(conf) : 'Unknown';
        confIcon = '';
      }

      let reasons = Array.isArray(meta.confidenceReasons) ? meta.confidenceReasons : [];

      let dispositionLabel = meta.dispositionAction
        .replace(/_/g, ' ')
        .replace(/\b\w/g, function(c) { return c.toUpperCase(); });

      // Disposition action badge
      confidenceHtml +=
        '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-muted text-muted-foreground border border-border">' +
          escapeHtml(dispositionLabel) +
        '</span>';

      // Confidence badge
      confidenceHtml +=
        '<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-semibold border ' + confBadgeClass + '">' +
          (confIcon ? '<span aria-hidden="true">' + confIcon + '</span>' : '') +
          escapeHtml(confLabel) +
        '</span>';

      // Expandable uncertainty reasons for medium / low / none
      if (reasons.length > 0 && conf !== 'high') {
        let reasonItems = reasons.map(function(r) {
          return '<li class="text-xs">' + escapeHtml(r) + '</li>';
        }).join('');

        let panelBorderClass = conf === 'low'
          ? 'border-destructive/30 bg-destructive/5'
          : 'border-amber-500/30 bg-amber-500/5';

        confidenceHtml +=
          '<details class="w-full mt-1">' +
            '<summary class="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground transition-colors select-none list-none flex items-center gap-1">' +
              '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">' +
                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>' +
              '</svg>' +
              'Why not high confidence? (' + reasons.length + ' reason' + (reasons.length !== 1 ? 's' : '') + ')' +
            '</summary>' +
            '<ul class="mt-1.5 ml-4 space-y-0.5 list-disc list-inside rounded-md border p-2 ' + panelBorderClass + '">' +
              reasonItems +
            '</ul>' +
          '</details>';
      }
    }

    parts.push(
      '<div class="flex flex-wrap items-center gap-2 mb-3">' +
        (meta.overallScore !== undefined
          ? '<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md text-xs font-semibold bg-muted text-foreground border border-border">' +
              'Overall: <strong>' + Number(meta.overallScore).toFixed(1) + '/100</strong>' +
            '</span>'
          : '') +
        (meta.timeAction
          ? '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-semibold border ' + actionClass + '">' +
              escapeHtml(meta.timeAction) +
            '</span>'
          : '') +
        confidenceHtml +
        (meta.scoringConfigName
          ? '<span class="text-xs text-muted-foreground ml-auto">Config: ' + escapeHtml(meta.scoringConfigName) + '</span>'
          : '') +
      '</div>'
    );
  }

  // ── Per-dimension cards ───────────────────────────────────────────────
  evidence.forEach(function(dim) {
    let contribClass = dim.contribution > 0
      ? 'text-emerald-700'
      : dim.contribution < 0
        ? 'text-destructive'
        : 'text-muted-foreground';
    let contribSign = dim.contribution > 0 ? '+' : '';

    // Sub-factors table
    let subHtml = '';
    if (dim.sub_factors && dim.sub_factors.length > 0) {
      let rows = dim.sub_factors.map(function(sf) {
        let sfContribClass = sf.contribution > 0
          ? 'text-emerald-700'
          : sf.contribution < 0
            ? 'text-destructive'
            : 'text-muted-foreground';
        let sfSign = sf.contribution > 0 ? '+' : '';
        return '<tr class="border-b border-border last:border-0">' +
          '<td class="py-1.5 pr-3 text-xs font-medium text-foreground align-top">' + escapeHtml(sf.factor) + '</td>' +
          '<td class="py-1.5 pr-3 text-xs text-muted-foreground align-top max-w-[120px] truncate" title="' + escapeHtml(String(sf.raw_value ?? '—')) + '">' + escapeHtml(String(sf.raw_value ?? '—')) + '</td>' +
          '<td class="py-1.5 pr-3 text-xs font-semibold ' + sfContribClass + ' align-top whitespace-nowrap">' + sfSign + sf.contribution + ' pts</td>' +
          '<td class="py-1.5 text-xs text-muted-foreground align-top">' + escapeHtml(sf.rationale || '—') + '</td>' +
        '</tr>';
      }).join('');

      subHtml =
        '<details class="mt-2">' +
          '<summary class="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground transition-colors select-none">' +
            dim.sub_factors.length + ' factor' + (dim.sub_factors.length === 1 ? '' : 's') + ' — click to expand' +
          '</summary>' +
          '<div class="mt-2 overflow-x-auto">' +
            '<table class="w-full text-xs">' +
              '<thead>' +
                '<tr class="border-b border-border">' +
                  '<th class="text-left pb-1 pr-3 text-xs font-semibold text-muted-foreground">Factor</th>' +
                  '<th class="text-left pb-1 pr-3 text-xs font-semibold text-muted-foreground">Value</th>' +
                  '<th class="text-left pb-1 pr-3 text-xs font-semibold text-muted-foreground">Points</th>' +
                  '<th class="text-left pb-1 text-xs font-semibold text-muted-foreground">Rationale</th>' +
                '</tr>' +
              '</thead>' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</details>';
    }

    parts.push(
      '<div class="rounded-lg border border-border bg-background p-3 mb-2">' +
        '<div class="flex items-start justify-between gap-2">' +
          '<div class="flex-1 min-w-0">' +
            '<div class="flex items-center gap-2 mb-0.5">' +
              '<span class="text-sm font-semibold text-foreground">' + escapeHtml(dim.factor) + '</span>' +
              (dim.weight !== null && dim.weight !== undefined
                ? '<span class="text-xs text-muted-foreground">(' + dim.weight + '% weight)</span>'
                : '') +
            '</div>' +
            '<p class="text-xs text-muted-foreground">' + escapeHtml(dim.rationale || '') + '</p>' +
            '<p class="text-xs text-muted-foreground mt-0.5 truncate" title="' + escapeHtml(dim.source || '') + '">' +
              '<span class="font-medium">Source:</span> ' + escapeHtml(dim.source || '—') +
            '</p>' +
          '</div>' +
          '<div class="flex flex-col items-end gap-1 flex-shrink-0">' +
            '<span class="text-sm font-bold text-foreground">' + Number(dim.raw_value || 0).toFixed(1) + '</span>' +
            '<span class="text-xs font-semibold ' + contribClass + '">' + contribSign + Number(dim.contribution || 0).toFixed(1) + ' pts</span>' +
          '</div>' +
        '</div>' +
        subHtml +
      '</div>'
    );
  });

  safeHTML(container,
    '<div class="evidence-trail-panel">' +
      parts.join('') +
    '</div>'
  );
}

// ============================================================================
// Portfolio Dependency Risk Functions
// ============================================================================

let _depRiskCurrentPage = 1;
let _depRiskCurrentFilter = '';

/**
 * Load the portfolio-wide dependency risk summary table.
 * Called by the "Load" button and the risk filter <select>.
 *
 * @param {number} [page=1] - Page to load (1-based).
 */
async function loadPortfolioDependencies(page) {
  APP_CONFIG = window.__APP_CONFIG__ || {};
  let baseUrl = APP_CONFIG.portfolioDependenciesUrl || '/applications/rationalization/api/portfolio-dependencies';

  _depRiskCurrentPage = page || 1;
  _depRiskCurrentFilter = (document.getElementById('dep-risk-filter')?.value || '').trim().toLowerCase();

  let url = baseUrl + '?page=' + _depRiskCurrentPage + '&per_page=25';
  if (_depRiskCurrentFilter) {
    url += '&risk_level=' + encodeURIComponent(_depRiskCurrentFilter);
  }

  // Show loading, hide others
  let elEmpty = document.getElementById('dep-risk-empty');
  let elLoading = document.getElementById('dep-risk-loading');
  let elResults = document.getElementById('dep-risk-results');
  let elNoResults = document.getElementById('dep-risk-no-results');
  if (elEmpty) elEmpty.classList.add('hidden');
  if (elNoResults) elNoResults.classList.add('hidden');
  if (elResults) elResults.classList.add('hidden');
  if (elLoading) elLoading.classList.remove('hidden');

  let loadBtn = document.getElementById('dep-risk-load-btn');
  if (loadBtn) { loadBtn.disabled = true; }

  try {
    let result = await Platform.fetch.get(url, null, { silent: true });
    if (!result.success) {
      throw new Error(result.error || 'Unknown error');
    }

    if (elLoading) elLoading.classList.add('hidden');

    if (!result.apps || result.apps.length === 0) {
      if (elNoResults) elNoResults.classList.remove('hidden');
      return;
    }

    // Build summary bar
    let summary = document.getElementById('dep-risk-summary');
    if (summary) {
      let criticalCount = result.apps.filter(function(a) { return a.risk_level === 'critical'; }).length;
      let highCount = result.apps.filter(function(a) { return a.risk_level === 'high'; }).length;
      safeHTML(summary,
        '<span class="font-medium text-muted-foreground">' + result.total + ' apps with dependencies</span>' +
        (criticalCount > 0 ? ' <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-destructive/10 text-destructive border border-destructive/30">' + criticalCount + ' Critical</span>' : '') +
        (highCount > 0 ? ' <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-orange-500/10 text-orange-600 border border-orange-500/30">' + highCount + ' High</span>' : '')
      );
    }

    // Fetch readiness data in parallel for the current page's apps (RAT-101)
    let readinessByAppId = {};
    try {
      let appIds = result.apps.map(function(a) { return a.app_id; });
      let readinessBaseUrl = APP_CONFIG.portfolioReadinessUrl || '/applications/rationalization/api/portfolio-readiness';
      let readinessUrl = readinessBaseUrl + '?per_page=100';
      let rData = await Platform.fetch.get(readinessUrl, null, { silent: true });
      if (rData.success && rData.apps) {
        rData.apps.forEach(function(ra) {
          if (appIds.indexOf(ra.app_id) !== -1) {
            readinessByAppId[ra.app_id] = ra;
          }
        });
      }
    } catch (_e) {
      // Non-fatal: table renders without readiness column if fetch fails
      // Platform.fetch already logged the error; we just proceed without readiness data
    }

    // Build table rows
    let tbody = document.getElementById('dep-risk-table-body');
    if (tbody) {
      let rowsHtml = result.apps.map(function(app) {
        let riskClass = _depRiskBadgeClass(app.risk_level);
        let retireSafe = !app.critical_blocker_count && app.risk_level !== 'critical' && app.risk_level !== 'high';
        let readinessInfo = readinessByAppId[app.app_id];
        let readinessBadgeHtml;
        if (readinessInfo) {
          readinessBadgeHtml = renderReadinessBadge(readinessInfo.is_decision_ready, readinessInfo.readiness_score);
          if (!readinessInfo.is_decision_ready && readinessInfo.missing_critical && readinessInfo.missing_critical.length > 0) {
            let DIMENSION_LABELS = {
              owner: 'Owner', lifecycle: 'Lifecycle', cost: 'Cost', business_criticality: 'Criticality',
              tech_stack: 'Tech Stack', vendor: 'Vendor', processes: 'Processes', risk: 'Risk',
            };
            let missingLabels = readinessInfo.missing_critical.map(function(d) {
              return DIMENSION_LABELS[d] || d;
            }).join(', ');
            readinessBadgeHtml += '<p class="text-[10px] text-muted-foreground mt-0.5">Missing: ' + escapeHtml(missingLabels) + '</p>';
          }
        } else {
          readinessBadgeHtml = '<span class="text-xs text-muted-foreground">Not scored</span>';
        }
        return '<tr class="border-b hover:bg-muted/30 transition-colors">' +
          '<td class="py-3 px-4">' +
            '<p class="font-medium text-sm truncate max-w-xs">' + escapeHtml(app.app_name) + '</p>' +
            '<p class="text-xs text-muted-foreground">' + escapeHtml(app.lifecycle_status || '-') + ' · ' + escapeHtml(app.business_criticality || '-') + '</p>' +
          '</td>' +
          '<td class="py-3 px-4 text-center">' +
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-semibold ' + riskClass + '">' + escapeHtml((app.risk_level || 'low').toUpperCase()) + '</span>' +
          '</td>' +
          '<td class="py-3 px-4 text-center tabular-nums font-semibold ' + (app.blocker_count > 0 ? 'text-warning' : 'text-muted-foreground') + '">' + app.blocker_count + '</td>' +
          '<td class="py-3 px-4 text-center tabular-nums font-semibold ' + (app.critical_blocker_count > 0 ? 'text-destructive' : 'text-muted-foreground') + '">' + app.critical_blocker_count + '</td>' +
          '<td class="py-3 px-4 text-center tabular-nums font-semibold text-muted-foreground">' + app.downstream_count + '</td>' +
          '<td class="py-3 px-4 text-center">' +
            (retireSafe
              ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">Safe</span>'
              : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-destructive/10 text-destructive border border-destructive/30">Blocked</span>'
            ) +
          '</td>' +
          '<td class="py-3 px-4">' + readinessBadgeHtml + '</td>' +
          '<td class="py-3 px-4 text-right">' +
            '<button type="button" data-dep-action="impact" data-dep-app-id="' + Number(app.app_id) + '" data-dep-app-name="' + escapeAttr(app.app_name) + '" ' +
              'class="h-7 px-3 rounded-md text-xs font-semibold border border-input bg-background hover:bg-accent transition-colors" ' +
              'aria-label="View dependency detail for ' + escapeHtml(app.app_name) + '">' +
              'Detail' +
            '</button>' +
          '</td>' +
        '</tr>';
      }).join('');
      safeHTML(tbody, rowsHtml);
    }

    // Pagination
    let pagination = document.getElementById('dep-risk-pagination');
    if (pagination) {
      let paginHtml = '<span>Showing ' + (((result.page - 1) * result.per_page) + 1) + '–' + Math.min(result.page * result.per_page, result.total) + ' of ' + result.total + '</span>';
      if (result.total_pages > 1) {
        paginHtml += '<div class="flex gap-1">' +
          (result.page > 1 ? '<button type="button" data-dep-action="page" data-dep-page="' + (result.page - 1) + '" class="h-7 px-3 rounded-md border border-input bg-background text-xs hover:bg-accent transition-colors" aria-label="Previous page">Prev</button>' : '') +
          '<span class="h-7 px-3 flex items-center text-xs font-semibold">Page ' + result.page + ' / ' + result.total_pages + '</span>' +
          (result.page < result.total_pages ? '<button type="button" data-dep-action="page" data-dep-page="' + (result.page + 1) + '" class="h-7 px-3 rounded-md border border-input bg-background text-xs hover:bg-accent transition-colors" aria-label="Next page">Next</button>' : '') +
        '</div>';
      }
      safeHTML(pagination, paginHtml);
    }

    if (elResults) elResults.classList.remove('hidden');

    // Re-initialize Lucide icons if present
    if (typeof lucide !== 'undefined') { lucide.createIcons(); }

  } catch (error) {
    if (elLoading) elLoading.classList.add('hidden');
    if (elEmpty) elEmpty.classList.remove('hidden');
    showToast('Failed to load dependency risk data: ' + error.message, 'error');
  } finally {
    if (loadBtn) { loadBtn.disabled = false; }
  }
}

/**
 * Load full dependency impact detail for one application and display it in the inline panel.
 *
 * @param {number} appId - Application component ID.
 * @param {string} appName - Application name for display.
 */
async function loadDependencyImpact(appId, appName) {
  APP_CONFIG = window.__APP_CONFIG__ || {};
  let baseUrl = APP_CONFIG.dependencyImpactBaseUrl || '/applications/rationalization/api/dependency-impact/';
  let url = baseUrl + appId + '?depth=3';

  let panel = document.getElementById('dep-impact-detail-panel');
  let titleEl = document.getElementById('dep-impact-panel-title');
  let bodyEl = document.getElementById('dep-impact-detail-body');

  if (!panel || !bodyEl) return;

  if (titleEl) titleEl.textContent = 'Dependency Impact: ' + appName;
  safeHTML(bodyEl,
    '<div class="flex items-center justify-center py-10 text-muted-foreground">' +
      '<svg class="w-6 h-6 animate-spin mr-2 text-primary" fill="none" viewBox="0 0 24 24">' +
        '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
        '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
      '</svg> Loading impact data…' +
    '</div>'
  );
  panel.classList.remove('hidden');
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  try {
    let result = await Platform.fetch.get(url, null, { silent: true });
    if (!result.success) {
      throw new Error(result.error || 'Unknown error');
    }

    let blockers = result.blockers || {};
    let blast = result.blast_radius || {};
    let retireSafe = result.retirement_safe;

    // --- Retirement verdict banner ---
    let verdictClass = retireSafe
      ? 'border-emerald-400/30 bg-emerald-50 text-emerald-700'
      : 'border-destructive/30 bg-destructive/5 text-destructive';
    let verdictIcon = retireSafe
      ? '<svg class="w-5 h-5 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>'
      : '<svg class="w-5 h-5 inline mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
    let verdictText = retireSafe ? 'Safe to retire — no critical blockers' : 'Retirement is blocked or carries significant risk';

    // --- Blocker summary ---
    let blockerBadge = blockers.has_critical_blockers
      ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-destructive/10 text-destructive border border-destructive/30">' + blockers.critical_count + ' Critical</span>'
      : (blockers.has_blockers
          ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-500/10 text-amber-600 border border-amber-500/30">' + blockers.total_count + ' Non-critical</span>'
          : '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">None</span>'
      );

    // --- Blast risk badge ---
    let blastRisk = blast.risk_level || 'low';
    let blastBadgeClass = _depRiskBadgeClass(blastRisk);
    let blastBadge = '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold ' + blastBadgeClass + '">' + blastRisk.toUpperCase() + '</span>';

    // --- Blocker rows ---
    let blockerRows = '';
    let blockersList = blockers.blockers || [];
    if (blockersList.length > 0) {
      blockerRows = blockersList.map(function(b) {
        return '<div class="flex items-center gap-3 p-3 rounded-lg border ' + (b.is_critical ? 'border-destructive/20 bg-destructive/5' : 'border-input bg-background') + '">' +
          '<div class="flex-1 min-w-0">' +
            '<p class="font-medium text-sm truncate">' + escapeHtml(b.source_app_name) + '</p>' +
            '<p class="text-xs text-muted-foreground">' + escapeHtml(b.dependency_type) + ' | strength: ' + escapeHtml(b.dependency_strength || '—') + '</p>' +
          '</div>' +
          (b.is_critical ? '<span class="px-2 py-0.5 text-xs font-bold rounded bg-destructive/10 text-destructive">CRITICAL</span>' : '') +
        '</div>';
      }).join('');
    } else {
      blockerRows = '<p class="text-sm text-muted-foreground">No upstream blockers recorded.</p>';
    }

    // --- Downstream dependents ---
    let directDeps = blast.direct_dependents || [];
    let depRows = '';
    if (directDeps.length > 0) {
      depRows = directDeps.map(function(d) { return createDependentCard(d); }).join('');
    } else {
      depRows = '<p class="text-sm text-muted-foreground">No direct downstream dependents recorded.</p>';
    }

    let decouplingCostDisplay = blast.estimated_total_decoupling_cost > 0
      ? formatCurrency(blast.estimated_total_decoupling_cost)
      : '—';

    safeHTML(bodyEl,
      // Verdict banner
      '<div class="flex items-center gap-2 p-3 rounded-lg border ' + verdictClass + ' text-sm font-semibold mb-1">' +
        verdictIcon + verdictText +
      '</div>' +

      // Metrics grid
      '<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">' +
        '<div class="rounded-lg border p-3 text-center">' +
          '<p class="text-xl font-bold tabular-nums">' + (blockers.total_count || 0) + '</p>' +
          '<p class="text-xs text-muted-foreground mt-1">Upstream Blockers</p>' +
        '</div>' +
        '<div class="rounded-lg border p-3 text-center">' +
          '<p class="text-xl font-bold tabular-nums text-destructive">' + (blockers.critical_count || 0) + '</p>' +
          '<p class="text-xs text-muted-foreground mt-1">Critical Blockers</p>' +
        '</div>' +
        '<div class="rounded-lg border p-3 text-center">' +
          '<p class="text-xl font-bold tabular-nums">' + (blast.total_affected_count || 0) + '</p>' +
          '<p class="text-xs text-muted-foreground mt-1">Total Affected</p>' +
        '</div>' +
        '<div class="rounded-lg border p-3 text-center">' +
          '<p class="text-xl font-bold tabular-nums text-success">' + decouplingCostDisplay + '</p>' +
          '<p class="text-xs text-muted-foreground mt-1">Decoupling Cost</p>' +
        '</div>' +
      '</div>' +

      // Blast risk
      '<div class="flex items-center gap-2 text-sm">' +
        '<span class="font-semibold text-muted-foreground">Blast Radius Risk:</span>' + blastBadge +
        '<span class="text-muted-foreground">· ' + (blast.direct_dependent_count || 0) + ' direct, ' + (blast.total_affected_count || 0) + ' total affected across ' + (blast.max_depth_analyzed || 3) + ' levels</span>' +
      '</div>' +

      // Upstream blockers
      '<div>' +
        '<h4 class="font-semibold text-sm mb-2 flex items-center gap-2">Upstream Blockers ' + blockerBadge + '</h4>' +
        '<div class="space-y-2 max-h-48 overflow-y-auto">' + blockerRows + '</div>' +
      '</div>' +

      // Downstream dependents
      '<div>' +
        '<h4 class="font-semibold text-sm mb-2">Direct Downstream Dependents</h4>' +
        '<div class="space-y-2 max-h-48 overflow-y-auto">' + depRows + '</div>' +
      '</div>'
    );

    // Re-initialize icons
    if (typeof lucide !== 'undefined') { lucide.createIcons(); }

  } catch (error) {
    safeHTML(bodyEl,
      '<div class="p-6 text-center text-destructive">' +
        '<p class="font-semibold">Failed to load dependency impact</p>' +
        '<p class="text-sm mt-1">' + escapeHtml(error.message) + '</p>' +
      '</div>'
    );
    showToast('Failed to load dependency impact: ' + error.message, 'error');
  }
}

/**
 * Return the Tailwind badge class set for a given risk level string.
 * Mirrors the pattern_registry.json badge pattern: bg-{color}-500/10 text-{color}-600 border border-{color}-500/30
 *
 * @param {string} riskLevel - low | medium | high | critical
 * @returns {string} Tailwind classes.
 */
function _depRiskBadgeClass(riskLevel) {
  switch ((riskLevel || '').toLowerCase()) {
    case 'critical': return 'bg-destructive/10 text-destructive border border-destructive/30';
    case 'high':     return 'bg-orange-500/10 text-orange-600 border border-orange-500/30';
    case 'medium':   return 'bg-amber-500/10 text-amber-600 border border-amber-500/30';
    default:         return 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30';
  }
}

// Wire up risk-level filter select to reload
(function() {
  let filterEl = document.getElementById('dep-risk-filter');
  if (filterEl) {
    filterEl.addEventListener('change', function() {
      loadPortfolioDependencies(1);
    });
  }
})();

// ============================================================================
// Decision Readiness Stats Card (RAT-101)
// ============================================================================

/**
 * Fetch the portfolio-readiness summary and populate the Decision Ready stat card.
 * Non-fatal: the card shows dashes if the endpoint is unavailable.
 */
async function loadReadinessSummary() {
  try {
    APP_CONFIG = window.__APP_CONFIG__ || {};
    let baseUrl = APP_CONFIG.portfolioReadinessUrl || '/applications/rationalization/api/portfolio-readiness';
    let data = await Platform.fetch.get(baseUrl + '?per_page=1', null, { silent: true });
    if (!data.success || !data.summary) return;
    let summary = data.summary;
    let readyEl = document.getElementById('readiness-ready-count');
    let incompleteEl = document.getElementById('readiness-incomplete-count');
    let totalEl = document.getElementById('readiness-total-scored');
    if (readyEl) readyEl.textContent = summary.decision_ready_count;
    if (incompleteEl) incompleteEl.textContent = summary.incomplete_count;
    if (totalEl) totalEl.textContent = summary.total_scored;
  } catch (_e) {
    // Non-fatal — stat card stays at dashes
  }
}

// ============================================================================
// Retirement Sequence (RAT-110)
// ============================================================================

/**
 * Disposition badge HTML for a TIME/disposition label.
 * @param {string} disposition
 * @returns {string} HTML span
 */
function _dispositionBadge(disposition) {
  let upper = (disposition || 'unknown').toUpperCase();
  let cls, label;
  switch (upper) {
    case 'ELIMINATE':
      cls = 'bg-destructive/10 text-destructive border border-destructive/30';
      label = 'Eliminate';
      break;
    case 'MIGRATE':
      cls = 'bg-amber-500/10 text-amber-600 border border-amber-500/30';
      label = 'Migrate';
      break;
    case 'TOLERATE':
      cls = 'bg-muted/50 text-muted-foreground border border-border';
      label = 'Tolerate';
      break;
    case 'INVEST':
      cls = 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30';
      label = 'Invest';
      break;
    default:
      cls = 'bg-muted/50 text-muted-foreground border border-border';
      label = escapeHtml(disposition || 'Unknown');
  }
  return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ' + cls + '">' + label + '</span>';
}

/**
 * Render a wave-based retirement sequence timeline into a container element.
 *
 * Each wave is displayed as a card showing the apps retiring in parallel in
 * that wave, their disposition, and which apps they unblock.  An arrow
 * connector separates consecutive waves.
 *
 * @param {Object} data       - Response from /rationalization/api/retirement-sequence
 * @param {string} containerId - ID of the DOM element to render into
 */
function renderRetirementSequence(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;

  if (!data || !data.success) {
    safeHTML(container,
      '<div class="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">' +
        '<p class="text-sm font-semibold text-destructive">Failed to load retirement sequence</p>' +
        '<p class="text-xs text-muted-foreground mt-1">' + escapeHtml((data && data.error) || 'Unknown error') + '</p>' +
      '</div>'
    );
    return;
  }

  if (!data.waves || data.waves.length === 0) {
    safeHTML(container,
      '<div class="rounded-lg border border-border bg-muted/20 p-8 text-center">' +
        '<div class="h-12 w-12 rounded-full bg-muted flex items-center justify-center mx-auto mb-3">' +
          '<i data-lucide="layers" class="w-6 h-6 text-muted-foreground" aria-hidden="true"></i>' +
        '</div>' +
        '<p class="font-semibold text-muted-foreground">No apps to sequence</p>' +
        '<p class="text-sm text-muted-foreground mt-1">No ELIMINATE or MIGRATE dispositions found in the scored portfolio.</p>' +
      '</div>'
    );
    if (typeof lucide !== 'undefined') { lucide.createIcons(); }
    return;
  }

  let waveBlocks = [];

  for (let i = 0; i < data.waves.length; i++) {
    let wave = data.waves[i];
    let waveLabel = 'Wave ' + wave.wave_number;

    // Header badge colour: wave 1 is most actionable
    let waveHeaderCls = wave.wave_number === 1
      ? 'bg-primary/10 text-primary border border-primary/30'
      : 'bg-muted/50 text-muted-foreground border border-border';

    let appRows = (wave.apps || []).map(function(app) {
      let unblockNames = (app.unblocks || []).map(function(uid) {
        // We only have IDs here; show as "App #ID" unless we have the name
        return 'App #' + uid;
      });

      let unblockBadges = unblockNames.length > 0
        ? '<span class="text-xs text-muted-foreground ml-2">→ unblocks ' +
            escapeHtml(unblockNames.slice(0, 3).join(', ')) +
            (unblockNames.length > 3 ? ' +' + (unblockNames.length - 3) + ' more' : '') +
          '</span>'
        : '';

      let blockedByBadges = (app.blocked_by || []).length > 0
        ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs bg-destructive/5 text-destructive border border-destructive/20 ml-1">' +
            'after ' + (app.blocked_by || []).length + ' predecessor(s)' +
          '</span>'
        : '';

      let lifecyclePill = app.lifecycle_status
        ? '<span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs bg-muted text-muted-foreground border border-border ml-1">' +
            escapeHtml(app.lifecycle_status) +
          '</span>'
        : '';

      return '<div class="flex flex-wrap items-center gap-x-2 gap-y-1 py-2 border-b last:border-0">' +
        '<i data-lucide="box" class="w-4 h-4 text-muted-foreground flex-shrink-0" aria-hidden="true"></i>' +
        '<span class="font-medium text-sm">' + escapeHtml(app.app_name || ('App #' + app.app_id)) + '</span>' +
        _dispositionBadge(app.disposition) +
        lifecyclePill +
        blockedByBadges +
        unblockBadges +
      '</div>';
    }).join('');

    let waveCard =
      '<div class="rounded-xl border bg-card shadow-sm" aria-label="' + escapeHtml(waveLabel) + '">' +
        '<div class="px-4 pt-4 pb-2 flex items-center justify-between">' +
          '<div class="flex items-center gap-2">' +
            '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-semibold ' + waveHeaderCls + '">' +
              escapeHtml(waveLabel) +
            '</span>' +
            '<span class="text-sm text-muted-foreground">' + wave.app_count + ' app' + (wave.app_count !== 1 ? 's' : '') + ' can retire in parallel</span>' +
          '</div>' +
        '</div>' +
        '<div class="px-4 pb-4 max-h-64 overflow-y-auto">' +
          (appRows || '<p class="text-sm text-muted-foreground py-2">No apps in this wave.</p>') +
        '</div>' +
      '</div>';

    waveBlocks.push(waveCard);

    // Arrow connector between waves (not after last wave)
    if (i < data.waves.length - 1) {
      waveBlocks.push(
        '<div class="flex justify-center my-2" aria-hidden="true">' +
          '<i data-lucide="arrow-down" class="w-5 h-5 text-muted-foreground"></i>' +
        '</div>'
      );
    }
  }

  // Unsequenced apps warning (cycles detected)
  let unsequencedBlock = '';
  if (data.unsequenced && data.unsequenced.length > 0) {
    unsequencedBlock =
      '<div class="mt-4 rounded-lg border border-warning/30 bg-warning/5 p-4">' +
        '<div class="flex items-start gap-2">' +
          '<i data-lucide="alert-triangle" class="w-4 h-4 text-warning flex-shrink-0 mt-0.5" aria-hidden="true"></i>' +
          '<div>' +
            '<p class="text-sm font-semibold text-warning">Dependency cycles detected</p>' +
            '<p class="text-xs text-muted-foreground mt-1">' +
              data.unsequenced.length + ' app(s) could not be placed in a wave due to circular dependencies: ' +
              'App IDs ' + escapeHtml(data.unsequenced.join(', ')) + '. Manual review required.' +
            '</p>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  // Summary header
  let summaryHeader =
    '<div class="flex items-center justify-between mb-4">' +
      '<div>' +
        '<p class="text-sm text-muted-foreground">' +
          data.total_apps + ' app' + (data.total_apps !== 1 ? 's' : '') + ' across ' +
          data.total_waves + ' wave' + (data.total_waves !== 1 ? 's' : '') +
        '</p>' +
      '</div>' +
    '</div>';

  safeHTML(container, summaryHeader + waveBlocks.join('') + unsequencedBlock);

  if (typeof lucide !== 'undefined') { lucide.createIcons(); }
}

/**
 * Fetch the portfolio retirement sequence and render it into the container.
 * Non-fatal: shows an error state if the endpoint is unavailable.
 */
async function loadRetirementSequence() {
  let container = document.getElementById('retirement-sequence-container');
  if (!container) return;

  // Show loading state
  safeHTML(container,
    '<div class="flex items-center justify-center gap-2 py-8 text-muted-foreground">' +
      '<svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">' +
        '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
        '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
      '</svg>' +
      '<span class="text-sm">Computing retirement sequence…</span>' +
    '</div>'
  );

  try {
    APP_CONFIG = window.__APP_CONFIG__ || {};
    let url = APP_CONFIG.retirementSequenceUrl || '/applications/rationalization/api/retirement-sequence';
    let data = await Platform.fetch.get(url, null, { silent: true });
    renderRetirementSequence(data, 'retirement-sequence-container');
  } catch (err) {
    safeHTML(container,
      '<div class="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">' +
        '<p class="text-sm font-semibold text-destructive">Could not load retirement sequence</p>' +
        '<p class="text-xs text-muted-foreground mt-1">' + escapeHtml(err.message) + '</p>' +
      '</div>'
    );
  }
}

// Load readiness summary when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
  loadReadinessSummary();
  loadRetirementSequence();
});

// ============================================================================
// Portfolio Workbench helpers (RAT-119)
// ============================================================================

/**
 * Return a Tailwind class string for a TIME rationalization action badge.
 * @param {string} action  — ELIMINATE | MIGRATE | INVEST | TOLERATE
 * @returns {string}
 */
function _dispositionBadgeHtml(disposition) {
  let d = (disposition || '').toLowerCase();
  let cls;
  switch (d) {
    case 'retire':      cls = 'bg-red-500/10 text-red-600 border border-red-500/30'; break;      // token-migration-ok
    case 'consolidate': cls = 'bg-purple-500/10 text-purple-600 border border-purple-500/30'; break; // token-migration-ok
    case 'replace':     cls = 'bg-orange-500/10 text-orange-600 border border-orange-500/30'; break; // token-migration-ok
    case 'replatform':  cls = 'bg-violet-500/10 text-violet-600 border border-violet-500/30'; break; // token-migration-ok
    case 'rehost':      cls = 'bg-sky-500/10 text-sky-600 border border-sky-500/30'; break;      // token-migration-ok
    case 'refactor':    cls = 'bg-amber-500/10 text-amber-600 border border-amber-500/30'; break; // token-migration-ok
    case 'retain':      cls = 'bg-blue-500/10 text-blue-600 border border-blue-500/30'; break;   // token-migration-ok
    default:            cls = 'bg-muted/50 text-muted-foreground border border-border';
  }
  return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ' + cls + '">' +
    escapeHtml(disposition || 'unknown') + '</span>';
}

/**
 * Return badge HTML for a disposition confidence level.
 * @param {string} confidence  — high | medium | low
 * @returns {string}
 */
function _confidenceBadgeHtml(confidence) {
  let c = (confidence || '').toLowerCase();
  let cls;
  switch (c) {
    case 'high':   cls = 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30'; break; // token-migration-ok
    case 'medium': cls = 'bg-amber-500/10 text-amber-600 border border-amber-500/30'; break;      // token-migration-ok
    case 'low':    cls = 'bg-red-500/10 text-red-600 border border-red-500/30'; break;            // token-migration-ok
    default:       cls = 'bg-muted/50 text-muted-foreground border border-border';
  }
  return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ' + cls + '">' +
    escapeHtml(confidence || '—') + '</span>';
}

/**
 * Render the portfolio workbench results table into a container element.
 * Used by non-Alpine callers; the primary render path is the Alpine component.
 *
 * @param {Object} data        — Response from /rationalization/api/portfolio-workbench
 * @param {string} containerId — ID of the DOM element to render into
 */
function renderWorkbenchTable(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;

  if (!data || !data.success) {
    safeHTML(container,
      '<div class="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">' +
        '<p class="text-sm font-semibold text-destructive">Failed to load workbench data</p>' +
        '<p class="text-xs text-muted-foreground mt-1">' + escapeHtml((data && data.error) || 'Unknown error') + '</p>' +
      '</div>'
    );
    return;
  }

  if (!data.results || data.results.length === 0) {
    safeHTML(container,
      '<div class="rounded-lg border border-border bg-muted/20 p-8 text-center">' +
        '<p class="font-semibold text-muted-foreground">No applications match the current filters</p>' +
      '</div>'
    );
    return;
  }

  let rows = data.results.map(function(row) {
    let healthCls = row.overall_health_score <= 33
      ? 'text-destructive'
      : row.overall_health_score <= 66
        ? 'text-amber-600'   // token-migration-ok
        : 'text-emerald-600'; // token-migration-ok
    let readinessBadge = row.is_decision_ready
      ? '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">Ready</span>' // token-migration-ok
      : '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-amber-500/10 text-amber-600 border border-amber-500/30">Incomplete</span>'; // token-migration-ok

    return '<tr class="border-b hover:bg-accent/30 transition-colors">' +
      '<td class="px-4 py-3 font-medium text-sm">' + escapeHtml(row.app_name || '') + '</td>' +
      '<td class="px-4 py-3 text-xs text-muted-foreground">' + escapeHtml(row.business_unit || '—') + '</td>' +
      '<td class="px-4 py-3">' +
        '<div class="flex flex-wrap gap-1.5">' +
          _dispositionBadge(row.rationalization_action) +
          (row.disposition_action ? _dispositionBadgeHtml(row.disposition_action) : '') +
        '</div>' +
      '</td>' +
      '<td class="px-4 py-3">' + _confidenceBadgeHtml(row.disposition_confidence) + '</td>' +
      '<td class="px-4 py-3 text-center">' +
        '<span class="tabular-nums font-semibold text-sm ' + healthCls + '">' +
          (row.overall_health_score != null ? row.overall_health_score : '—') +
        '</span>' +
      '</td>' +
      '<td class="px-4 py-3">' + readinessBadge + '</td>' +
    '</tr>';
  }).join('');

  let html =
    '<div class="overflow-x-auto">' +
      '<table class="w-full text-sm" aria-label="Portfolio triage workbench">' +
        '<thead>' +
          '<tr class="border-b bg-muted/30">' +
            '<th class="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Application</th>' +
            '<th class="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Business Unit</th>' +
            '<th class="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">TIME / Disposition</th>' +
            '<th class="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Confidence</th>' +
            '<th class="px-4 py-3 text-center text-xs font-semibold text-muted-foreground uppercase tracking-wide">Health</th>' +
            '<th class="px-4 py-3 text-left text-xs font-semibold text-muted-foreground uppercase tracking-wide">Readiness</th>' +
          '</tr>' +
        '</thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table>' +
    '</div>';

  safeHTML(container, html);
}

/**
 * Fetch the portfolio workbench data with the given filters and render it
 * into the container. Non-fatal: shows an error state if the endpoint fails.
 *
 * @param {Object} filters — key/value pairs matching the API query params
 * @param {string} [containerId] — DOM element ID to render into (optional)
 */
/**
 * RAT-121: Bulk review action — sends app_ids + action to the bulk-review endpoint.
 *
 * @param {number[]} appIds  — array of application_component_id values (max 50)
 * @param {string}   action  — 'approve' | 'defer' | 'request_data'
 * @param {string}   notes   — optional audit notes
 * @returns {Promise<Object>} — parsed JSON response
 */
function executeBulkReview(appIds, action, notes) {
  return Platform.fetch.post('/applications/rationalization/api/bulk-review', 
    { app_ids: appIds, action: action, notes: notes || '' });
}

async function loadPortfolioWorkbench(filters, containerId) {
  let container = containerId ? document.getElementById(containerId) : null;
  if (container) {
    safeHTML(container,
      '<div class="flex items-center justify-center gap-2 py-8 text-muted-foreground">' +
        '<svg class="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">' +
          '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
          '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
        '</svg>' +
        '<span class="text-sm">Loading workbench…</span>' +
      '</div>'
    );
  }

  try {
    let params = new URLSearchParams();
    let f = filters || {};
    if (f.search) params.set('search', f.search);
    if (f.readiness) params.set('readiness', f.readiness);
    if (f.confidence) params.set('confidence', f.confidence);
    if (f.disposition) params.set('disposition', f.disposition);
    if (f.time_action) params.set('time_action', f.time_action);
    if (f.review_status) params.set('review_status', f.review_status);
    if (f.business_unit) params.set('business_unit', f.business_unit);
    if (f.sort_by) params.set('sort_by', f.sort_by);
    if (f.sort_dir) params.set('sort_dir', f.sort_dir);
    if (f.page) params.set('page', f.page);
    if (f.per_page) params.set('per_page', f.per_page);

    let baseUrl = (window.__APP_CONFIG__ || {}).portfolioWorkbenchUrl
      || '/applications/rationalization/api/portfolio-workbench';
    let data = await Platform.fetch.get(baseUrl + '?' + params.toString(), null, { silent: true });
    if (container) {
      renderWorkbenchTable(data, containerId);
    }
    return data;
  } catch (err) {
    if (container) {
      safeHTML(container,
        '<div class="rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">' +
          '<p class="text-sm font-semibold text-destructive">Could not load portfolio workbench</p>' +
          '<p class="text-xs text-muted-foreground mt-1">' + escapeHtml(err.message) + '</p>' +
        '</div>'
      );
    }
    return null;
  }
}

// RAT-115: Fetch and display roadmap status for an application.
// containerId is optional — if provided, renders a loading spinner then result HTML.
async function loadRoadmapStatus(appId, containerId) {
  let container = containerId ? document.getElementById(containerId) : null;
  if (container) {
    safeHTML(container,
      '<div class="flex items-center gap-2 py-4 text-muted-foreground">' +
        '<svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">' +
          '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
          '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
        '</svg>' +
        '<span class="text-sm">Loading roadmap status...</span>' +
      '</div>'
    );
  }
  try {
    let data = await Platform.fetch.get('/applications/rationalization/api/roadmap-status/' + encodeURIComponent(appId), null, { silent: true });
    if (container) {
      renderRoadmapStatus(data, containerId);
    }
    return data;
  } catch (err) {
    if (container) {
      safeHTML(container,
        '<div class="rounded-md border border-destructive/30 bg-destructive/5 p-3">' +
          '<p class="text-sm text-destructive">Could not load roadmap status: ' + escapeHtml(err.message) + '</p>' +
        '</div>'
      );
    }
    return null;
  }
}

// RAT-115: Render roadmap status or creation form into containerId.
function renderRoadmapStatus(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container || !data) return;

  if (!data.success) {
    safeHTML(container,
      '<p class="text-sm text-destructive">' + escapeHtml(data.error || 'Error loading roadmap status.') + '</p>'
    );
    return;
  }

  if (data.has_roadmap_item) {
    safeHTML(container,
      '<div class="flex items-center gap-2">' +
        '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-emerald-500/10 text-emerald-600 border border-emerald-500/30">Roadmap item created</span>' + /* token-migration-ok */
        '<a href="/consolidation-list" class="text-xs text-emerald-700 hover:underline">View in Consolidation List</a>' + /* token-migration-ok */
      '</div>'
    );
    return;
  }

  if (!data.can_create) {
    let status = escapeHtml(data.review_status || 'draft');
    safeHTML(container,
      '<p class="text-sm text-amber-700">' + /* token-migration-ok */
        'Roadmap items require an approved recommendation. Current status: <strong>' + status + '</strong>.' +
      '</p>'
    );
    return;
  }

  // Approved and no roadmap item — render inline creation form
  safeHTML(container,
    '<p class="text-sm text-muted-foreground mb-3">Recommendation approved. Add to roadmap:</p>' +
    '<div class="space-y-3">' +
      '<input id="roadmap-inline-owner" type="text" placeholder="Owner (required)" ' + // entity-picker-ok
             'class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm" />' +
      '<input id="roadmap-inline-date" type="date" ' +
             'class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm" />' +
      '<textarea id="roadmap-inline-notes" rows="2" placeholder="Notes (optional)" ' +
                'class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"></textarea>' +
      '<button id="roadmap-inline-submit" ' +
              'class="inline-flex items-center px-3 py-1.5 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90">' +
        'Create Roadmap Item' +
      '</button>' +
      '<p id="roadmap-inline-error" class="text-sm text-destructive hidden"></p>' +
    '</div>'
  );

  let submitBtn = document.getElementById('roadmap-inline-submit');
  if (submitBtn) {
    submitBtn.addEventListener('click', async function() {
      let owner = (document.getElementById('roadmap-inline-owner') || {}).value || '';
      let targetDate = (document.getElementById('roadmap-inline-date') || {}).value || '';
      let notes = (document.getElementById('roadmap-inline-notes') || {}).value || '';
      let errorEl = document.getElementById('roadmap-inline-error');

      if (!owner.trim()) {
        if (errorEl) { errorEl.textContent = 'Owner is required.'; errorEl.classList.remove('hidden'); }
        return;
      }
      if (errorEl) { errorEl.classList.add('hidden'); }

      setButtonLoading(submitBtn, true, 'Creating...');
      try {
        let result = await Platform.fetch.post('/applications/rationalization/api/create-roadmap-item/' + encodeURIComponent(data._appId || ''), 
          { owner: owner.trim(), target_date: targetDate || null, notes: notes.trim() || null });
        if (result.success) {
          renderRoadmapStatus({ success: true, has_roadmap_item: true }, containerId);
        } else {
          if (errorEl) { errorEl.textContent = result.error || 'Creation failed.'; errorEl.classList.remove('hidden'); }
          setButtonLoading(submitBtn, false);
        }
      } catch (err) {
        if (errorEl) { errorEl.textContent = 'Network error — please try again.'; errorEl.classList.remove('hidden'); }
        setButtonLoading(submitBtn, false);
      }
    });
  }
}

// RAT-120: Decision dossier standalone helpers (used by external tooling / future integrations)
function loadDecisionDossier(appId, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;
  safeHTML(container, '<p class="text-sm text-muted-foreground">Loading dossier\u2026</p>');

  Platform.fetch.get('/applications/rationalization/api/decision-dossier/' + appId, null, { silent: true })
    .then(function(data) {
      if (!data.success) {
        safeHTML(container, '<p class="text-sm text-destructive">Failed to load dossier.</p>');
        return;
      }
      renderDecisionDossier(data, containerId);
    })
    .catch(function() {
      safeHTML(container, '<p class="text-sm text-destructive">Network error loading dossier.</p>');
    });
}

function renderDecisionDossier(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container || !data.has_score) {
    if (container) {
      safeHTML(container, '<p class="text-sm text-muted-foreground">No rationalization score available.</p>');
    }
    return;
  }

  let rec = data.recommendation || {};
  let scores = data.scores || {};
  let deps = data.dependencies || {};
  let financial = data.financial || {};

  let dispMap = {
    retire: 'bg-red-500/10 text-red-600 border border-red-500/30', // token-migration-ok
    consolidate: 'bg-purple-500/10 text-purple-600 border border-purple-500/30', // token-migration-ok
    replace: 'bg-orange-500/10 text-orange-600 border border-orange-500/30', // token-migration-ok
    replatform: 'bg-violet-500/10 text-violet-600 border border-violet-500/30', // token-migration-ok
    rehost: 'bg-sky-500/10 text-sky-600 border border-sky-500/30', // token-migration-ok
    refactor: 'bg-amber-500/10 text-amber-600 border border-amber-500/30', // token-migration-ok
    retain: 'bg-blue-500/10 text-blue-600 border border-blue-500/30', // token-migration-ok
  };
  let confMap = {
    high: 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30', // token-migration-ok
    medium: 'bg-amber-500/10 text-amber-600 border border-amber-500/30', // token-migration-ok
    low: 'bg-red-500/10 text-red-600 border border-red-500/30', // token-migration-ok
  };

  let dispClass = dispMap[(rec.disposition_action || '').toLowerCase()] || 'bg-muted/50 text-muted-foreground border border-border';
  let confClass = confMap[(rec.disposition_confidence || '').toLowerCase()] || 'bg-muted/50 text-muted-foreground border border-border';

  let html = '<div class="space-y-4">';

  // Recommendation
  html += '<div class="border border-border rounded-md p-3">' +
    '<h3 class="text-sm font-semibold mb-2">Recommendation</h3>' +
    '<div class="flex flex-wrap gap-2">' +
    '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ' + escapeHtml(dispClass) + '">' + escapeHtml(rec.disposition_action || '\u2014') + '</span>';
  if (rec.disposition_confidence) {
    html += '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium ' + escapeHtml(confClass) + '">' + escapeHtml((rec.disposition_confidence || '').toUpperCase()) + ' confidence</span>';
  }
  html += '<span class="inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-medium bg-muted/50 text-muted-foreground border border-border">Score: ' + escapeHtml(String(rec.overall_health_score ?? '\u2014')) + '</span>' +
    '</div>';
  if (rec.action_rationale) {
    html += '<p class="text-sm mt-2 text-muted-foreground">' + escapeHtml(rec.action_rationale) + '</p>';
  }
  html += '</div>';

  // Score dimensions
  html += '<div class="border border-border rounded-md p-3">' +
    '<h3 class="text-sm font-semibold mb-2">Score Dimensions</h3>' +
    '<div class="grid grid-cols-2 gap-2 text-sm">' +
    '<div>Technical Health: <span class="font-medium">' + escapeHtml(String(scores.technical_health ?? '\u2014')) + '</span></div>' +
    '<div>Business Value: <span class="font-medium">' + escapeHtml(String(scores.business_value ?? '\u2014')) + '</span></div>' +
    '<div>Cost Efficiency: <span class="font-medium">' + escapeHtml(String(scores.cost_efficiency ?? '\u2014')) + '</span></div>' +
    '<div>Vendor Risk: <span class="font-medium">' + escapeHtml(String(scores.vendor_risk ?? '\u2014')) + '</span></div>' +
    '</div></div>';

  // Dependencies
  html += '<div class="border border-border rounded-md p-3">' +
    '<h3 class="text-sm font-semibold mb-2">Dependencies</h3>' +
    '<div class="text-sm">' +
    '<span>Outbound: ' + (deps.outbound_count || 0) + '</span> \u00b7 <span>Inbound: ' + (deps.inbound_count || 0) + '</span>';
  if (deps.critical_blockers && deps.critical_blockers.length) {
    html += '<p class="text-destructive mt-1">' + deps.critical_blockers.length + ' retirement blocker(s)</p>';
  }
  html += '</div></div>';

  html += '</div>';
  safeHTML(container, html);
}

// ── RAT-126: Workflow status helpers ────────────────────────────────────────

function loadWorkflowStatus(appId, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;
  safeHTML(container, '<p class="text-sm text-muted-foreground">Loading workflow status\u2026</p>');
  Platform.fetch.get('/applications/rationalization/api/workflow-status/' + appId, null, { silent: true })
    .then(function(data) {
      if (!data.success) {
        safeHTML(container, '<p class="text-sm text-destructive">Failed to load.</p>');
        return;
      }
      renderWorkflowStatus(data, containerId);
    })
    .catch(function() {
      safeHTML(container, '<p class="text-sm text-destructive">Network error.</p>');
    });
}

function renderWorkflowStatus(data, containerId) {
  let container = document.getElementById(containerId);
  if (!container) return;
  const labels = {
    assessed: 'Assessed',
    scored: 'Scored',
    readiness_checked: 'Readiness',
    evidence_available: 'Evidence',
    reviewed: 'Reviewed',
    approved: 'Approved',
    arb_governed: 'ARB Required',
    arb_decided: 'ARB Decided',
    roadmap_created: 'Roadmap'
  };
  let html = '<div class="flex flex-wrap gap-1">';
  const steps = data.steps || {};
  Object.keys(steps).forEach(function(step) {
    const done = steps[step];
    let cls = done
      ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/30' /* token-migration-ok */
      : 'bg-muted text-muted-foreground border-border';
    html += '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ' + cls + '">' + (labels[step] || step) + '</span>';
  });
  html += '</div>';
  html += '<p class="text-xs text-muted-foreground mt-1">' + (data.completion_pct || 0) + '% complete \u00b7 Phase: ' + (data.current_phase || 'initial') + '</p>';
  safeHTML(container, html);
}

// ── RAT-122: Executive Summary Alpine component ──────────────────────────

function executiveSummary() {
  return {
    loaded: false,
    loadError: false,
    data: {},

    load: function() {
      const self = this;
      self.loaded = false;
      self.loadError = false;
      Platform.fetch.get('/applications/rationalization/api/executive-summary', null, { silent: true })
        .then(function(json) {
          if (json.success) {
            self.data = json;
            self.loaded = true;
          } else {
            self.loadError = true;
            if (window.Platform && Platform.toast) Platform.toast.error('Could not load the executive summary.');
          }
        })
        .catch(function(err) {
          self.loadError = true;
          if (window.Platform && Platform.toast) Platform.toast.error('Could not load the executive summary.');
        });
    },

    pct: function(part, total) {
      if (!total || total <= 0) return 0;
      return Math.round((part / total) * 100);
    },

    fmtCurrency: function(amount) {
      return formatCurrency(amount || 0);
    },

    netBenefit: function() {
      if (!this.data.financial) return 0;
      return (this.data.financial.total_projected_savings || 0) - (this.data.financial.total_investment_needed || 0);
    },

    totalReadiness: function() {
      if (!this.data.readiness) return 0;
      return (this.data.readiness.ready || 0) + (this.data.readiness.not_ready || 0);
    },

    healthBuckets: function() {
      const b = this.data.score_buckets || {};
      return [
        { key: 'critical_0_25', label: 'Critical (0–25)', count: b.critical_0_25 || 0, color: 'bg-red-500' }, // token-migration-ok
        { key: 'poor_26_50',    label: 'Poor (26–50)',    count: b.poor_26_50    || 0, color: 'bg-orange-500' }, // token-migration-ok
        { key: 'fair_51_75',    label: 'Fair (51–75)',    count: b.fair_51_75    || 0, color: 'bg-yellow-500' }, // token-migration-ok
        { key: 'good_76_100',   label: 'Good (76–100)',   count: b.good_76_100   || 0, color: 'bg-emerald-500' }
      ];
    },

    dispositionLabel: function(action) {
      const labels = {
        'retain':      'Retain',
        'retire':      'Retire',
        'replace':     'Replace',
        'consolidate': 'Consolidate',
        'migrate':     'Migrate',
        'invest':      'Invest',
        'tolerate':    'Tolerate'
      };
      return labels[action] || action;
    },

    reviewLabel: function(status) {
      const labels = {
        'pending':            'Pending',
        'under_review':       'Under Review',
        'approved':           'Approved',
        'rejected':           'Rejected',
        'needs_more_info':    'Needs Info',
        'escalated':          'Escalated'
      };
      return labels[status] || status;
    },

    reviewBadgeClass: function(status) {
      const classes = {
        'approved':        'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30',
        'rejected':        'bg-red-500/10 text-red-600 border border-red-500/30', // token-migration-ok
        'under_review':    'bg-blue-500/10 text-blue-600 border border-blue-500/30', // token-migration-ok
        'pending':         'bg-muted text-muted-foreground border border-border',
        'needs_more_info': 'bg-amber-500/10 text-amber-600 border border-amber-500/30', // token-migration-ok
        'escalated':       'bg-purple-500/10 text-purple-600 border border-purple-500/30' // token-migration-ok
      };
      return classes[status] || 'bg-muted text-muted-foreground border border-border';
    }
  };
}

function workflowProgress() {
  return {
    loaded: false,
    steps: {},
    completionPct: 0,
    currentPhase: '',
    currentAppId: null,

    stepLabel: function(step) {
      const labels = {
        assessed: 'Assessed',
        scored: 'Scored',
        readiness_checked: 'Readiness',
        evidence_available: 'Evidence',
        reviewed: 'Reviewed',
        approved: 'Approved',
        arb_governed: 'ARB Required',
        arb_decided: 'ARB Decided',
        roadmap_created: 'Roadmap'
      };
      return labels[step] || step;
    },

    loadStatus: function(appId) {
      if (!appId) { this.loaded = false; return; }
      const self = this;
      self.currentAppId = appId;
      Platform.fetch.get('/applications/rationalization/api/workflow-status/' + appId, null, { silent: true })
        .then(function(data) {
          if (!data.success) throw new Error(data.error || 'workflow status unavailable');
          self.steps = data.steps;
          self.completionPct = data.completion_pct;
          self.currentPhase = data.current_phase;
          self.loaded = true;
        })
        .catch(function(e) {
          /* `loaded = false` is the panel's "Select an application to view workflow
             progress" state — so a failed load told the user they had not picked an
             application, for an application they had just picked. */
          self.steps = [];
          self.completionPct = null;
          self.currentPhase = null;
          self.loaded = false;
          if (window.Platform && window.Platform.toast) {
            window.Platform.toast.error('Could not load workflow progress for this application.');
          }
        });
    },

    init: function() {
      const self = this;
      let el = document.getElementById('options-app-id');
      if (el) {
        const observer = new MutationObserver(function() {
          let appId = el.value || el.textContent;
          if (appId && appId !== self.currentAppId) self.loadStatus(appId);
        });
        observer.observe(el, { attributes: true, childList: true, characterData: true });
        setInterval(function() {
          let appId = el.value || el.textContent;
          if (appId && appId !== self.currentAppId) self.loadStatus(appId);
        }, 1500);
      }
    }
  };
}


// Delegated, because the CSP refuses inline on*= attributes: the dependency-risk
// table's per-row "Detail" button and the Prev/Next pagination buttons carried
// onclick="loadDependencyImpact(...)" / onclick="loadPortfolioDependencies(...)"
// and therefore never fired. Both target functions exist at file scope in this
// module and hit live read-only endpoints; neither is destructive. Bound once at
// document level so it survives the table being re-rendered from fetched data.
document.addEventListener('click', function (event) {
  var el = event.target.closest('[data-dep-action]');
  if (!el) return;
  event.preventDefault();
  if (el.getAttribute('data-dep-action') === 'impact') {
    loadDependencyImpact(Number(el.getAttribute('data-dep-app-id')), el.getAttribute('data-dep-app-name'));
  } else if (el.getAttribute('data-dep-action') === 'page') {
    loadPortfolioDependencies(Number(el.getAttribute('data-dep-page')));
  }
});
