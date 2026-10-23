/**
 * Application Import Modal - External JavaScript
 * Extracted from application_import_modal.html inline scripts
 * Depends on: window.__APP_CONFIG__ (injected by template)
 */
/* Use a file-local config object so this script can coexist with other legacy globals. */
const APPLICATION_IMPORT_CONFIG = Object.assign({}, window.__APP_CONFIG__ || {});

// Excel/CSV/JSON file handling
let selectedExcelFile = null;
let excelPreviewData = [];
let currentHeaders = [];
let fieldMappings = {};
// Store APQC links from analysis for import execution
let apqcLinksByRow = {};

// These will be loaded dynamically from the API based on the ApplicationComponent model
let TARGET_FIELDS = [{ value: '', label: '-- Skip Column --' }];
let COLUMN_ALIASES = {};
let fieldsLoaded = false;

// Load available fields from the backend API (based on actual model)
async function loadImportFields() {
  if (fieldsLoaded) return;

  try {
    const data = await Platform.fetch('/applications/import-fields');
    TARGET_FIELDS = data.fields || TARGET_FIELDS;
    COLUMN_ALIASES = data.aliases || {};
    fieldsLoaded = true;
  // fabricated-ok: falls back to form field definitions/labels, not to invented records
  } catch (error) {
    // Fall back to minimal defaults if API fails
    TARGET_FIELDS = [
      { value: '', label: '-- Skip Column --' },
      { value: 'name', label: 'Name *', required: true },
      { value: 'application_code', label: 'Application Code' },
      { value: 'description', label: 'Description' },
      { value: 'deployment_status', label: 'Status' },
      { value: 'business_criticality', label: 'Business Criticality' },
      { value: 'lifecycle_status', label: 'Lifecycle Status' },
      { value: 'vendor_name', label: 'Vendor' },
      { value: 'notes', label: 'Notes' }
    ];
    COLUMN_ALIASES = {
      'name': ['name', 'application name', 'app name'],
      'application_code': ['app id', 'application id', 'app code'],
      'description': ['description'],
      'deployment_status': ['application status', 'status'],
      'business_criticality': ['business criticality', 'criticality'],
      'lifecycle_status': ['lifecycle status', 'lifecycle'],
      'vendor_name': ['vendor', 'vendor name'],
      'notes': ['notes', 'comments']
    };
  }
}

// Load fields when the page loads
loadImportFields();

// Drag and drop handlers
function handleDragOver(event) {
  event.preventDefault();
  event.stopPropagation();
  document.getElementById('drop-zone').classList.add('border-primary', 'bg-primary/5');
}

function handleDragLeave(event) {
  event.preventDefault();
  event.stopPropagation();
  document.getElementById('drop-zone').classList.remove('border-primary', 'bg-primary/5');
}

function handleDrop(event) {
  event.preventDefault();
  event.stopPropagation();
  document.getElementById('drop-zone').classList.remove('border-primary', 'bg-primary/5');

  const files = event.dataTransfer.files;
  if (files.length > 0) {
    const file = files[0];
    const ext = file.name.toLowerCase().split('.').pop();
    if (['xlsx', 'xls', 'csv', 'json'].includes(ext)) {
      document.getElementById('excel-file-input').files = files;
      handleExcelFileSelect({ target: { files: [file] } });
    } else {
      Platform.toast.warning('Please upload an Excel (.xlsx, .xls), CSV, or JSON file.');
    }
  }
}

// Native inline ondrag* attributes are blocked by the strict CSP (Firefox
// reports each blocked handler as a page error). Bind them from this trusted
// external script so drag-and-drop behaves consistently in every browser.
const applicationImportDropZone = document.getElementById('drop-zone');
if (applicationImportDropZone) {
  applicationImportDropZone.addEventListener('dragover', handleDragOver);
  applicationImportDropZone.addEventListener('dragleave', handleDragLeave);
  applicationImportDropZone.addEventListener('drop', handleDrop);
}

// Auto-detect field mapping based on column header
// Uses exact match only to avoid false positives (e.g., "Application Status" matching "name")
function autoDetectMapping(header) {
  const normalizedHeader = header.toLowerCase().trim();

  // First pass: exact match only
  for (let _field in COLUMN_ALIASES) {
    if (COLUMN_ALIASES.hasOwnProperty(_field)) {
      const aliases = COLUMN_ALIASES[_field];
      if (aliases.some(function(alias) { return normalizedHeader === alias; })) {
        return _field;
      }
    }
  }

  // Second pass: check if header starts with an alias (for headers like "Name (optional)")
  for (let _field2 in COLUMN_ALIASES) {
    if (COLUMN_ALIASES.hasOwnProperty(_field2)) {
      const aliases2 = COLUMN_ALIASES[_field2];
      if (aliases2.some(function(alias) { return normalizedHeader.startsWith(alias + ' ') || normalizedHeader.startsWith(alias + '('); })) {
        return _field2;
      }
    }
  }

  return '';
}

async function handleExcelFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  selectedExcelFile = file;
  document.getElementById('excel-file-name').textContent = file.name;

  // Ensure fields are loaded from API before previewing
  await loadImportFields();

  // Preview the file
  previewExcelFile(file);
}

async function previewExcelFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('preview_only', 'true');

  try {
    const data = await Platform.fetch.post('/applications/preview-excel', formData, { silent: true });
    if (data.error) {
      Platform.toast.error('Error: ' + data.error);
      return;
    }

    excelPreviewData = data.rows;
    displayExcelPreview(data.headers, data.rows.slice(0, 10));
    document.getElementById('excel-preview').classList.remove('hidden');
  } catch (error) {
    Platform.toast.error('Error previewing file: ' + (error.message || 'Unknown error'));
  }
}

function displayExcelPreview(headers, rows) {
  const table = document.getElementById('excel-preview-table');
  const thead = table.querySelector('thead');
  const tbody = table.querySelector('tbody');

  // Store headers for later use
  currentHeaders = headers;
  fieldMappings = {};

  // Clear existing content
  safeHTML(thead, '');
  safeHTML(tbody, '');

  // Create mapping row with dropdowns
  const mappingRow = document.createElement('tr');
  mappingRow.className = 'bg-primary/5';
  headers.forEach((header, idx) => {
    const th = document.createElement('th');
    th.className = 'px-3 py-2';

    const select = document.createElement('select');
    select.className = 'mapping-select w-full border border-input rounded px-2 py-1 text-xs focus:ring-2 focus:ring-primary focus:border-primary';
    select.dataset.columnIndex = idx;
    select.dataset.originalHeader = header;

    // Auto-detect mapping
    const autoMapped = autoDetectMapping(header);
    fieldMappings[idx] = autoMapped;

    // Build options
    safeHTML(select, TARGET_FIELDS.map(f =>
      `<option value="${f.value}" ${autoMapped === f.value ? 'selected' : ''}>${f.label}</option>`
    ).join(''));

    // Handle mapping change
    select.addEventListener('change', function() {
      fieldMappings[this.dataset.columnIndex] = this.value;
      validateMappings();
    });

    th.appendChild(select);
    mappingRow.appendChild(th);
  });
  thead.appendChild(mappingRow);

  // Add header labels row
  const headerRow = document.createElement('tr');
  headerRow.className = 'bg-muted';
  headers.forEach(header => {
    const th = document.createElement('th');
    th.className = 'px-3 py-2 text-left text-xs font-medium text-muted-foreground uppercase';
    th.textContent = header;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);

  // Add data rows with validation highlighting
  rows.forEach((row, rowIdx) => {
    const tr = document.createElement('tr');
    tr.className = rowIdx % 2 === 0 ? 'bg-card' : 'bg-muted/50';
    row.forEach((cell, cellIdx) => {
      const td = document.createElement('td');
      td.className = 'px-3 py-2 text-sm text-foreground';
      td.dataset.columnIndex = cellIdx;

      // Highlight empty cells in required columns
      const mappedField = fieldMappings[cellIdx];
      if (mappedField === 'name' && (!cell || cell.toString().trim() === '')) {
        td.className += ' bg-destructive/10 text-red-800';
        td.title = 'Required field "Name" is empty';
      }

      td.textContent = cell || '';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  // Initial validation
  validateMappings();
}

function validateMappings() {
  const hasNameMapping = Object.values(fieldMappings).includes('name');
  const mappingWarning = document.getElementById('mapping-warning');
  const importBtn = document.getElementById('import-btn');

  if (!mappingWarning) {
    // Create warning element if it doesn't exist
    const preview = document.getElementById('excel-preview');
    const warning = document.createElement('div');
    warning.id = 'mapping-warning';
    warning.className = 'mb-4 p-3 bg-amber-500/5 border border-yellow-200 rounded text-sm text-yellow-800 hidden';
    preview.insertBefore(warning, preview.querySelector('h3').nextSibling);
  }

  const warning = document.getElementById('mapping-warning');
  if (!hasNameMapping) {
    warning.textContent = '\u26a0\ufe0f Warning: No column mapped to "Name" (required field). Please map at least one column to "Name".';
    warning.classList.remove('hidden');
    // Disable import button when validation fails
    if (importBtn) {
      importBtn.disabled = true;
      importBtn.title = 'Please map at least one column to "Name" before importing';
    }
  } else {
    warning.classList.add('hidden');
    // Enable import button when validation passes
    if (importBtn) {
      importBtn.disabled = false;
      importBtn.title = '';
    }
  }
  return hasNameMapping;
}

// Toggle auto-map options visibility (Excel tab)
function toggleAutoMapOptions() {
  const checkbox = document.getElementById('auto-map-after-import');
  const options = document.getElementById('auto-map-options');
  if (checkbox && options) {
    options.classList.toggle('hidden', !checkbox.checked);
  }
}

// Toggle auto-map options visibility (Manual tab)
function toggleAutoMapOptionsManual() {
  const checkbox = document.getElementById('auto-map-after-import-manual');
  const options = document.getElementById('auto-map-options-manual');
  if (checkbox && options) {
    options.classList.toggle('hidden', !checkbox.checked);
  }
}

// Toggle ArchiMate mode options visibility
function toggleArchimateMode() {
  const checkbox = document.getElementById('import-generate-archimate');
  const options = document.getElementById('archimate-mode-options');
  if (checkbox && options) {
    options.classList.toggle('hidden', !checkbox.checked);
  }
}

// Run comprehensive auto-mapping and return its outcome without hiding it by navigating.
// suffix: '' for Excel tab, '-manual' for Manual tab
async function runPostImportAutoMap(importedCount, suffix) {
  if (suffix === undefined) suffix = '';
  const autoMapCheckbox = document.getElementById('auto-map-after-import' + suffix);
  if (!autoMapCheckbox || !autoMapCheckbox.checked) {
    return ['Auto-mapping was not requested.'];
  }

  // Get options based on which tab
  const mapCapabilities = document.getElementById('import-map-capabilities' + suffix)?.checked ?? true;
  const mapProcesses = document.getElementById('import-map-processes' + suffix)?.checked ?? true;
  const generateArchimate = document.getElementById('import-generate-archimate' + suffix)?.checked ?? false;
  const cloneVendor = document.getElementById('import-clone-vendor' + suffix)?.checked ?? false;

  // Show progress
  const importBtn = document.getElementById(suffix === '-manual' ? 'manual-import-btn' : 'import-btn');
  if (importBtn) {
    importBtn.textContent = 'Auto-mapping...';
    importBtn.disabled = true;
  }

  try {
    const data = await Platform.fetch.post('/applications/api/comprehensive-auto-map', {
      max_applications: importedCount || 100,
      map_capabilities: mapCapabilities,
      map_processes: mapProcesses,
      generate_archimate: generateArchimate,
      clone_vendor_archimate: cloneVendor,
      auto_create: true
    }, { silent: true });
    if (data && (data.success === false || data.error)) {
      const details = [data.message, data.error].filter(value => typeof value === 'string' && value.trim());
      return ['Auto-mapping failed: ' + (details.join(': ') || 'The mapping request was rejected.')];
    }
    const counters = [
      ['process_mappings_created', 'APQC processes mapped'],
      ['archimate_elements_created', 'ArchiMate elements'],
      ['vendor_archimate_cloned', 'Vendor elements cloned'],
      ['vendor_matches_found', 'Vendor matches']
    ];
    const supplied = data && counters.filter(([field]) => Object.hasOwn(data, field));
    if (!data || data.success !== true || !supplied.length ||
        supplied.some(([field]) => !Number.isInteger(data[field]) || data[field] < 0) ||
        (data.creation_errors !== undefined && (!Array.isArray(data.creation_errors) ||
          data.creation_errors.some(error => typeof error !== 'string' || !error.trim())))) {
      return ['Auto-mapping outcome could not be confirmed: invalid server response.'];
    }
    const errors = data.creation_errors || [];
    return [errors.length ? 'Auto-mapping reported errors; some or all mappings may not have been saved.'
                          : 'Auto-mapping completed.',
      ...supplied.map(([field, label]) => `${label}: ${data[field]}`), ...errors];
  } catch (error) {
    return ['Auto-mapping failed: ' + error.message];
  }
}

// AUDIT-IMP-005: Guard flag to prevent concurrent import submissions
let _importInProgress = false;

async function processExcelImport() {
  if (!selectedExcelFile) {
    Platform.toast.warning('Please select a file first');
    return;
  }

  // AUDIT-IMP-005: Block concurrent submissions (race condition prevention)
  if (_importInProgress) {
    // Silently ignoring the click made the button look broken.
    Platform.toast.info('An import is already running — please wait for it to finish.');
    return;
  }
  _importInProgress = true;

  // Validate that name column is mapped
  if (!Object.values(fieldMappings).includes('name')) {
    _importInProgress = false;
    Platform.toast.warning('Please map at least one column to "Name" (required field) before importing.');
    return;
  }

  const formData = new FormData();
  formData.append('file', selectedExcelFile);
  formData.append('import_mode', document.getElementById('duplicate-mode').value);

  // AUDIT-IMP-005: Generate a unique import token for server-side idempotency.
  // The server rejects imports with a token already processed within 5 minutes.
  const importToken = crypto.randomUUID ? crypto.randomUUID() : (Date.now().toString(36) + Math.random().toString(36).slice(2));
  formData.append('import_token', importToken);

  // Send field mappings to backend
  const mappingsToSend = {};
  for (const [colIdx, targetField] of Object.entries(fieldMappings)) {
    if (targetField && currentHeaders[colIdx]) {
      mappingsToSend[currentHeaders[colIdx]] = targetField;
    }
  }
  formData.append('field_mappings', JSON.stringify(mappingsToSend));

  // Send APQC links from analysis (AI-powered semantic matches)
  // These will be used to create ApplicationProcessSupport records during import
  if (apqcLinksByRow && Object.keys(apqcLinksByRow).length > 0) {
    formData.append('apqc_links', JSON.stringify(apqcLinksByRow));
  }

  // AI OPTIONS - Integrated Import
  const enableAI = document.getElementById('auto-map-after-import')?.checked || false;
  formData.append('enable_ai', enableAI.toString());
  if (enableAI) {
      formData.append('map_capabilities', (document.getElementById('import-map-capabilities')?.checked || true).toString());
      formData.append('map_processes', (document.getElementById('import-map-processes')?.checked || true).toString());
      formData.append('generate_archimate', (document.getElementById('import-generate-archimate')?.checked || false).toString());
      formData.append('clone_vendor_archimate', (document.getElementById('import-clone-vendor')?.checked || false).toString());
      formData.append('confidence_threshold', '0.7');
  }

  // Show loading state
  const importBtn = event.target;
  const originalText = importBtn.textContent;
  importBtn.disabled = true;
  importBtn.textContent = 'Importing...';

  try {
    // Platform.fetch.post will throw on non-ok responses, and automatically inject CSRF token.
    // It returns parsed JSON or null (204). Since we need to handle redirects (non-JSON responses),
    // we cannot rely solely on Platform.fetch because it expects JSON or text.
    // However, the server may return a redirect (HTML) which would cause Platform.fetch to throw.
    // We'll use Platform.fetch with { silent: true } and catch any error to treat it as a redirect.
    // If the error is a PlatformError with status 200? Actually, redirects are 302/303 etc.
    // The current code treats any non-JSON response as a redirect and reloads.
    // We'll mimic that by catching the error and checking if it's a redirect.
    const data = await Platform.fetch.post('/applications/import', formData, { silent: true });
    // If we reach here, the response was JSON (success or error).
    if (data.error) {
      importBtn.disabled = false;
      importBtn.textContent = originalText;
      _importInProgress = false;  // AUDIT-IMP-005: Reset guard on error
      Platform.toast.error('Error: ' + data.error);
      return;
    }

    importBtn.disabled = false;
    importBtn.textContent = originalText;
    _importInProgress = false;  // AUDIT-IMP-005: Reset guard on success

    // Import succeeded, reload to see flash messages
    window.location.reload();
  } catch (error) {
    // Platform.fetch throws on non-ok responses, but also on network errors.
    // The original code treated a redirect (non-JSON) as success and reloaded.
    // We'll assume any error here is a redirect (or network error).
    // For safety, we'll reload only if the error is likely a redirect (status 3xx).
    // However, we cannot access error.status because PlatformError may not expose it.
    // The original code reloaded on any non-JSON response, even if it's a redirect.
    // We'll mimic that by reloading on any error, but keep the error toast for network errors.
    if (error.type === 'HttpError' && error.status >= 300 && error.status < 400) {
      // It's a redirect, reload without showing an error toast.
      window.location.reload();
      return;
    }
    importBtn.disabled = false;
    importBtn.textContent = originalText;
    _importInProgress = false;  // AUDIT-IMP-005: Reset guard on failure
    Platform.toast.error('Error importing: ' + error.message);
  }
}

// Manual entry functions
let manualEntryRowCount = 0;

function addManualEntryRow() {
  const tbody = document.getElementById('manual-entry-tbody');
  const row = document.createElement('tr');
  row.id = `manual-row-${manualEntryRowCount}`;
  safeHTML(row, `
    <td class="px-3 py-2">
      <input type="text" class="w-full border border-input rounded px-2 py-1 text-sm" name="app_id" placeholder="APP ID">
    </td>
    <td class="px-3 py-2">
      <input type="text" class="w-full border border-input rounded px-2 py-1 text-sm" data-field="name" required placeholder="Application Name *">
    </td>
    <td class="px-3 py-2">
      <input type="text" class="w-full border border-input rounded px-2 py-1 text-sm" name="component_type" placeholder="Type">
    </td>
    <td class="px-3 py-2">
      <select class="w-full border border-input rounded px-2 py-1 text-sm" name="deployment_status">
        <option value="">Select Status</option>
        <option value="planned">Planned</option>
        <option value="development">Development</option>
        <option value="testing">Testing</option>
        <option value="production">Production</option>
      </select>
    </td>
    <td class="px-3 py-2">
      <button data-action="removeManualEntryRow" data-id="manual-row-${manualEntryRowCount}" class="text-destructive hover:text-red-800 text-sm">
        Remove
      </button>
    </td>
  `);
  row.querySelector('[data-action="removeManualEntryRow"]').addEventListener('click', () => {
    removeManualEntryRow(row.id);
  });
  tbody.appendChild(row);
  manualEntryRowCount++;
}

function removeManualEntryRow(rowId) {
  document.getElementById(rowId).remove();
}

async function processManualImport(submitEvent) {
  // Capture the original control before a confirmation changes the active event.
  const importBtn = submitEvent.currentTarget;
  if (importBtn.disabled) return;
  const originalText = importBtn.textContent;
  importBtn.disabled = true;
  importBtn.textContent = 'Importing...';
  let importSaved = false;

  try {
    const tbody = document.getElementById('manual-entry-tbody');
    const rows = tbody.querySelectorAll('tr');

    if (rows.length === 0) {
      Platform.toast.warning('Please add at least one application');
      return;
    }

    const applications = [];
    let hasErrors = false;

    rows.forEach(row => {
      const inputs = row.querySelectorAll('input, select');
      const appData = {};

      inputs.forEach(input => {
        const field = input.dataset.field || input.name;
        if (field && input.value) {
          appData[field] = input.value.trim();
        }
      });

      if (appData.name) {
        applications.push(appData);
      } else if (inputs.length > 0) {
        hasErrors = true;
      }
    });

    if (hasErrors) {
      if (!(await Platform.modal.confirm('Some rows are missing required fields. Continue with valid rows only?'))) {
        return;
      }
    }

    if (applications.length === 0) {
      Platform.toast.error('No valid applications to import');
      return;
    }

    const duplicateMode = document.getElementById('duplicate-mode-manual')?.value || 'update';

    const data = await Platform.fetch.post('/applications/import-manual', {
      applications: applications,
      duplicate_mode: duplicateMode
    }, { silent: true });
    if (data && (data.error || data.success === false)) {
      Platform.toast.error('Error: ' + (data.error || 'Import failed'));
      return;
    }
    if (!data || data.success !== true ||
        !['created', 'updated', 'skipped', 'failed'].every(field =>
          Number.isInteger(data[field]) && data[field] >= 0)) {
      throw new Error('Import failed: invalid server response');
    }

    importSaved = true;
    const resultLines = [`Created: ${data.created}`, `Updated: ${data.updated}`,
      `Skipped: ${data.skipped}`, `Failed: ${data.failed}`];
    if (data.errors && data.errors.length > 0) {
      resultLines.push('Import errors:', ...data.errors.slice(0, 5));
    }

    // The import is saved even if the optional mapping step fails.
    const importedCount = (data.created || 0) + (data.updated || 0);
    importBtn.textContent = 'Import saved — processing result...';
    const mappingLines = await runPostImportAutoMap(importedCount, '-manual');
    const resultId = Platform.modal.create({
      title: 'Import saved',
      size: 'md',
      backdrop: false,
      keyboard: false,
      content: [...resultLines, ...mappingLines].map(line =>
        `<p class="text-sm text-foreground">${escapeHtml(String(line))}</p>`).join(''),
      buttons: [{ label: 'Done — refresh applications', variant: 'primary', resolve: true }]
    });
    await Platform.modal.prompt(resultId);
    Platform.modal.destroy(resultId);
    tbody.replaceChildren();
    window.location.reload();
  } catch (error) {
    Platform.toast.error((importSaved ? 'Import saved, but its result could not be displayed: ' : 'Error importing: ') + error.message);
  } finally {
    // Never offer to replay a saved batch, including during the pending reload.
    importBtn.disabled = importSaved;
    importBtn.textContent = importSaved ? 'Import saved' : originalText;
  }
}

// Import history
async function loadImportHistory() {
  try {
    const data = await Platform.fetch.get('/applications/import-history', null, { silent: true });
    const tbody = document.getElementById('import-history-tbody');
    safeHTML(tbody, '');

    if (data.history && data.history.length > 0) {
      data.history.forEach(record => {
        const row = document.createElement('tr');
        const date = new Date(record.imported_at);
        safeHTML(row, `
          <td class="px-4 py-3 text-sm">${escapeHtml(date.toLocaleString())}</td>
          <td class="px-4 py-3 text-sm font-medium">${escapeHtml(record.imported_by_name || 'Unknown')}</td>
          <td class="px-4 py-3 text-sm">${escapeHtml(record.import_source)}</td>
          <td class="px-4 py-3 text-sm">${escapeHtml(record.file_name || '-')}</td>
          <td class="px-4 py-3 text-sm">${escapeHtml(record.records_created)}</td>
          <td class="px-4 py-3 text-sm">${escapeHtml(record.records_updated)}</td>
          <td class="px-4 py-3 text-sm">${escapeHtml(record.records_failed)}</td>
          <td class="px-4 py-3 text-sm">
            <span class="px-2 py-1 rounded text-xs ${
              record.status === 'completed' ? 'bg-emerald-500/10 text-green-800' :
              record.status === 'failed' ? 'bg-destructive/10 text-red-800' :
              'bg-amber-500/10 text-yellow-800'
            }">
              ${escapeHtml(record.status)}
            </span>
          </td>
        `);
        tbody.appendChild(row);
      });
    } else {
      safeHTML(tbody, '<tr><td colspan="8" class="px-4 py-3 text-sm text-muted-foreground text-center">No import history</td></tr>');
    }
  } catch (error) {
    // Distinct from the "No import history" empty state above: no rows are
    // invented, but the user must not read a failed load as an empty log.
    const tbody = document.getElementById('import-history-tbody');
    if (tbody) {
      safeHTML(tbody, '<tr><td colspan="8" class="px-4 py-3 text-sm text-destructive text-center">Import history could not be loaded. This is not an empty history.</td></tr>');
    }
    Platform.toast.error('Could not load the import history: ' + (error.message || 'request failed') + '.');
  }
}

// Import Analyzer Function with Real-time Progress
function analyzeImportFile(analysisType) {
  if (!selectedExcelFile) {
    Platform.toast.warning('Please select a file first to analyze.');
    return;
  }

  const formData = new FormData();
  formData.append('file', selectedExcelFile);
  formData.append('duplicate_mode', document.getElementById('duplicate-mode')?.value || 'merge');

  // Add ArchiMate generation mode if checkbox is checked
  const archimateCheckbox = document.getElementById('import-generate-archimate');
  if (archimateCheckbox && archimateCheckbox.checked) {
    const selectedMode = document.querySelector('input[name="archimate-mode"]:checked');
    formData.append('archimate_mode', selectedMode ? selectedMode.value : 'standard');
  }

  // Handle direct import (skip analysis)
  if (analysisType === 'direct') {
    const analysisDiv = document.getElementById('import-analysis');
    const analysisContent = document.getElementById('analysis-content');
    analysisDiv.classList.remove('hidden');

    safeHTML(analysisContent, `
      <div class="bg-purple-50 border border-purple-200 rounded-lg p-4">
        <div class="flex items-center space-x-3">
          <div class="animate-spin rounded-full h-5 w-5 border-b-2 border-purple-600"></div>
          <div>
            <div class="text-primary font-medium">\u26a1 Preparing direct import...</div>
            <div class="text-sm text-primary">Validating file format for direct import</div>
          </div>
        </div>
      </div>
    `);

    // Simulate file validation and show import button
    setTimeout(() => {
      safeHTML(analysisContent, `
        <div class="bg-emerald-500/5 border border-emerald-200 rounded-lg p-4">
          <div class="flex items-center space-x-3">
            <div class="text-emerald-600">\u2705</div>
            <div>
              <div class="text-emerald-600 font-medium">Ready for Direct Import</div>
              <div class="text-sm text-emerald-500">File validated - no AI analysis performed</div>
            </div>
          </div>
        </div>
      `);

      // Show the import button
      document.getElementById('import-applications-btn').classList.remove('hidden');
    }, 2000);

    return;
  }

  // Show loading state with multiple progress indicators
  const analysisDiv = document.getElementById('import-analysis');
  const analysisContent = document.getElementById('analysis-content');
  analysisDiv.classList.remove('hidden');

  // Start elapsed time counter
  const startTime = Date.now();
  let elapsedTimer = null;

  // Different loading states for streaming vs standard analysis
  const isStreaming = analysisType === 'streaming';
  const color = isStreaming ? 'blue' : 'green';
  const icon = isStreaming ? '\ud83d\udd04' : '\ud83d\udcca';
  const title = isStreaming ? 'Streaming Analysis' : 'Standard Analysis';

  safeHTML(analysisContent, `
    <div class="bg-${color}-50 border border-${color}-200 rounded-lg p-4">
      <div class="flex items-center justify-between mb-3">
        <div class="flex items-center space-x-3">
          <div class="animate-spin rounded-full h-5 w-5 border-b-2 border-${color}-600"></div>
          <div>
            <div class="text-${color}-600 font-medium">${icon} ${title}...</div>
            <div class="text-sm text-${color}-500">
              <span id="progress-stage">Initializing</span> \u2022
              <span id="progress-count">0</span> elements \u2022
              <span id="elapsed-time">0s</span> elapsed
            </div>
          </div>
        </div>
        <div class="flex items-center space-x-2">
          <div class="flex space-x-1">
            <div class="w-2 h-2 bg-${color}-600 rounded-full animate-pulse"></div>
            <div class="w-2 h-2 bg-${color}-600 rounded-full animate-pulse" style="animation-delay: 0.2s"></div>
            <div class="w-2 h-2 bg-${color}-600 rounded-full animate-pulse" style="animation-delay: 0.4s"></div>
          </div>
        </div>
      </div>

      <div class="space-y-2">
        <div class="w-full bg-${color}-200 rounded-full h-2">
          <div id="progress-bar" class="bg-${color}-600 h-2 rounded-full transition-all duration-300 animate-pulse" style="width: 15%"></div>
        </div>

        <div class="grid grid-cols-3 gap-2 text-xs">
          <div id="stage-parse" class="text-center p-2 bg-card rounded border border-${color}-300">
            <div class="font-medium text-${color}-600">\ud83d\udcc4 Parsing</div>
            <div class="text-muted-foreground">Starting...</div>
          </div>
          <div id="stage-analyze" class="text-center p-2 bg-muted rounded border border-input">
            <div class="font-medium text-muted-foreground/60">\ud83e\udd16 AI Analysis</div>
            <div class="text-muted-foreground/60">Waiting...</div>
          </div>
          <div id="stage-generate" class="text-center p-2 bg-muted rounded border border-input">
            <div class="font-medium text-muted-foreground/60">\ud83c\udfd7\ufe0f Generation</div>
            <div class="text-muted-foreground/60">Waiting...</div>
          </div>
        </div>
      </div>
    </div>
  `);

  // Update elapsed time and estimated element count every second
  elapsedTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const elapsedEl = document.getElementById('elapsed-time');
    const countEl = document.getElementById('progress-count');

    if (elapsedEl) {
      elapsedEl.textContent = elapsed + 's';

      // Estimate element count based on elapsed time
      // Assume ~2 elements generated per second (conservative estimate)
      if (countEl && elapsed > 2) {
        const estimatedElements = Math.floor((elapsed - 2) * 2);
        countEl.textContent = estimatedElements + ' (estimated)';
      }
    }
  }, 1000);

  // Start analysis based on type
  if (analysisType === 'streaming') {
    // Try streaming analysis first (real-time progress), fallback to regular if it fails
    try {
      performStreamingAnalysis();
    } catch (error) {
      performRegularAnalysis();
    }
  } else if (analysisType === 'standard') {
    // Go directly to standard analysis
    performRegularAnalysis();
  }
}

// Callback when review is confirmed
window.onReviewConfirmed = function(approvedMappings) {
  processExcelImport();
};

function updateProgress(processed, total) {
  const progressCount = document.getElementById('progress-count');
  const progressBar = document.getElementById('progress-bar');

  if (progressCount) {
    progressCount.textContent = processed;
  }

  if (progressBar && total > 0) {
    const percentage = Math.min((processed / total) * 100, 100);
    progressBar.style.width = percentage + '%';
  }
}

function displayAnalysisResults(data) {
  const analysisContent = document.getElementById('analysis-content');

  // Store APQC links for import execution (AI-powered semantic matches)
  if (data.apqc_links_by_row) {
    apqcLinksByRow = data.apqc_links_by_row;
  } else {
    apqcLinksByRow = {};
  }

  // Display comprehensive analysis results - ORIGINAL DETAILED VIEW
  let analysisHtml = '<div class="space-y-6">';

  // Summary Section
  analysisHtml += `
    <div class="bg-primary/5 border border-primary/20 rounded-lg p-4">
      <h4 class="font-bold text-blue-900 mb-3">\ud83d\udcca Import Summary</h4>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div class="text-center">
          <div class="text-2xl font-bold text-primary">${escapeHtml(data.will_create || 0)}</div>
          <div class="text-muted-foreground">New Applications</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold text-emerald-600">${escapeHtml(data.will_update || 0)}</div>
          <div class="text-muted-foreground">Updates</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold text-orange-600">${escapeHtml(data.duplicates_in_file || 0)}</div>
          <div class="text-muted-foreground">Duplicates in File</div>
        </div>
        <div class="text-center">
          <div class="text-2xl font-bold text-muted-foreground">${escapeHtml(data.total_rows || 0)}</div>
          <div class="text-muted-foreground">Total Rows</div>
        </div>
      </div>
    </div>
  `;

  // AI Analysis Summary (if available)
  if (data.ai_analysis) {
    analysisHtml += `
      <div class="bg-purple-50 border border-purple-200 rounded-lg p-4">
        <h5 class="font-bold text-purple-900 mb-3">\ud83e\udd16 AI Analysis Summary</h5>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div class="text-center">
            <div class="text-2xl font-bold text-primary">${escapeHtml(data.ai_analysis.apqc_analyzed || 0)}</div>
            <div class="text-muted-foreground">APQC Analyzed</div>
          </div>
          <div class="text-center">
            <div class="text-2xl font-bold text-primary">${escapeHtml(data.ai_analysis.vendors_analyzed || 0)}</div>
            <div class="text-muted-foreground">Vendors Analyzed</div>
          </div>
          <div class="text-center">
            <div class="text-2xl font-bold text-primary">${escapeHtml(data.ai_analysis.ai_matches || 0)}</div>
            <div class="text-muted-foreground">AI Matches</div>
          </div>
          <div class="text-center">
            <div class="text-2xl font-bold text-primary">${escapeHtml(data.ai_analysis.confidence_avg || 0)}%</div>
            <div class="text-muted-foreground">Avg Confidence</div>
          </div>
        </div>

        // AI Service Status
        <div class="mt-3 pt-3 border-t border-purple-200">
          <div class="flex items-center justify-between">
            <div class="text-sm">
              <span class="font-medium">AI Service:</span>
              <span class="${data.ai_analysis.comprehensive_ai_enabled ? 'text-emerald-600' : 'text-orange-600'}">
                ${escapeHtml(data.ai_analysis.ai_service_used || 'Unknown')}
              </span>
            </div>
            ${data.ai_analysis.bulk_analysis_complete ?
              '<span class="text-xs bg-emerald-500/10 text-green-800 px-2 py-1 rounded">Bulk Analysis</span>' :
              '<span class="text-xs bg-orange-100 text-orange-800 px-2 py-1 rounded">Individual Analysis</span>'
            }
          </div>
          ${data.ai_analysis.ai_models_used && data.ai_analysis.ai_models_used.length > 0 ?
            `<div class="text-sm mt-1">
              <span class="font-medium">AI Models:</span>
              <span class="text-primary">${escapeHtml(data.ai_analysis.ai_models_used.join(', '))}</span>
            </div>` : ''
          }
        </div>
      </div>
    `;
  }

  // Comprehensive AI Results (NEW)
  if (data.comprehensive_ai_results) {
    const aiResults = data.comprehensive_ai_results;

    // Check if there's an error
    if (aiResults.error) {
      analysisHtml += `
        <div class="bg-destructive/5 border border-destructive/20 rounded-lg p-4">
          <h5 class="font-bold text-red-900 mb-3">\u26a0\ufe0f AI Analysis Error</h5>
          <div class="text-sm text-red-800">
            <div class="mb-2">
              <span class="font-medium">Error Type:</span> ${escapeHtml(aiResults.error)}
            </div>
            <div class="mb-2">
              <span class="font-medium">Message:</span> ${escapeHtml(aiResults.message)}
            </div>
            ${aiResults.details ? `<div class="mb-2"><span class="font-medium">Details:</span> ${escapeHtml(aiResults.details)}</div>` : ''}
            ${aiResults.partial_results ? `
              <div class="mt-3 p-2 bg-orange-100 rounded">
                <span class="font-medium">Recommendation:</span> ${escapeHtml(aiResults.partial_results.recommendation)}
              </div>
            ` : ''}
          </div>
        </div>
      `;
    } else {
      // Comprehensive AI Analysis Results - UNIFIED DISPLAY
      analysisHtml += `
        <div class="bg-gradient-to-r from-purple-50 to-blue-50 border border-purple-200 rounded-lg p-6">
          <h5 class="font-bold text-purple-900 mb-4 text-lg">\ud83e\udd16 AI-Powered Enterprise Architecture Analysis</h5>

          <!-- 4 Core AI Features -->
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <div class="bg-card rounded-lg p-4 text-center shadow-sm border-l-4 border-primary">
              <div class="text-xs text-muted-foreground mb-1">\u2713 Business Capabilities</div>
              <div class="text-3xl font-bold text-primary">${aiResults.capability_mappings_found || 0}</div>
              <div class="text-xs text-muted-foreground mt-1">AI-Mapped Capabilities</div>
            </div>
            <div class="bg-card rounded-lg p-4 text-center shadow-sm border-l-4 border-purple-500">
              <div class="text-xs text-muted-foreground mb-1">\u2713 APQC PCF Processes</div>
              <div class="text-3xl font-bold text-primary">${aiResults.process_mappings_found || 0}</div>
              <div class="text-xs text-muted-foreground mt-1">AI-Classified Processes</div>
            </div>
            <div class="bg-card rounded-lg p-4 text-center shadow-sm border-l-4 border-orange-500">
              <div class="text-xs text-muted-foreground mb-1">\u2713 ArchiMate 3.2 Elements</div>
              <div class="text-3xl font-bold text-orange-600">${aiResults.archimate_elements_generated || 0}</div>
              <div class="text-xs text-muted-foreground mt-1">AI-Generated Architecture</div>
            </div>
            <div class="bg-card rounded-lg p-4 text-center shadow-sm border-l-4 border-emerald-500">
              <div class="text-xs text-muted-foreground mb-1">\u2713 Vendor Products</div>
              <div class="text-3xl font-bold text-emerald-600">${aiResults.vendor_matches || 0}</div>
              <div class="text-xs text-muted-foreground mt-1">AI-Matched Vendors</div>
            </div>
          </div>

          <!-- Portfolio-Level Intelligence -->
          <div class="mt-4 pt-4 border-t border-purple-200">
            <h6 class="font-medium text-purple-900 mb-3">\ud83d\udcca Enterprise Portfolio Intelligence</h6>
            <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-3">
              <div class="bg-primary/5 rounded-lg p-3 text-center">
                <div class="text-2xl font-bold text-primary">${escapeHtml(data.total_rows || 0)}</div>
                <div class="text-xs text-muted-foreground">Total Applications</div>
              </div>
              <div class="bg-purple-50 rounded-lg p-3 text-center">
                <div class="text-2xl font-bold text-primary">${aiResults.total_analyzed || 0}</div>
                <div class="text-xs text-muted-foreground">AI Analyzed</div>
              </div>
              <div class="bg-emerald-500/5 rounded-lg p-3 text-center">
                <div class="text-2xl font-bold text-emerald-600">${aiResults.high_confidence_mappings || 0}</div>
                <div class="text-xs text-muted-foreground">Auto-Approved (\u226570%)</div>
              </div>
              <div class="bg-amber-500/5 rounded-lg p-3 text-center">
                <div class="text-2xl font-bold text-amber-600">${Math.max(0, (aiResults.capability_mappings_found || 0) + (aiResults.process_mappings_found || 0) + (aiResults.archimate_elements_generated || 0) - (aiResults.high_confidence_mappings || 0))}</div>
                <div class="text-xs text-muted-foreground">Needs Review (<70%)</div>
              </div>
              <div class="bg-indigo-50 rounded-lg p-3 text-center">
                <div class="text-2xl font-bold text-primary">${Math.round(((aiResults.high_confidence_mappings || 0) / Math.max((aiResults.capability_mappings_found || 0) + (aiResults.process_mappings_found || 0) + (aiResults.archimate_elements_generated || 0), 1)) * 100)}%</div>
                <div class="text-xs text-muted-foreground">AI Accuracy</div>
              </div>
            </div>
          </div>

          // Overall Confidence Summary (NEW)
          ${aiResults.applications && aiResults.applications.length > 0 ? `
            <div class="mt-3 pt-3 border-t border-purple-200">
              <h6 class="font-medium text-purple-900 mb-2">\ud83c\udfaf Overall AI Confidence Summary</h6>
              <div class="bg-muted/50 rounded-lg p-3">
                ${(() => {
                  const confidences = aiResults.applications
                    .filter(app => app.avg_capability_confidence)
                    .map(app => app.avg_capability_confidence * 100);

                  if (confidences.length === 0) return '<div class="text-sm text-muted-foreground">No confidence data available</div>';

                  const avgConfidence = (confidences.reduce((a, b) => a + b, 0) / confidences.length).toFixed(1);
                  const highConfidence = confidences.filter(c => c >= 80).length;
                  const mediumConfidence = confidences.filter(c => c >= 60 && c < 80).length;
                  const lowConfidence = confidences.filter(c => c < 60).length;

                  let overallColor, overallLevel;
                  if (avgConfidence >= 80) {
                    overallColor = 'text-emerald-600';
                    overallLevel = 'High';
                  } else if (avgConfidence >= 60) {
                    overallColor = 'text-amber-600';
                    overallLevel = 'Medium';
                  } else {
                    overallColor = 'text-destructive';
                    overallLevel = 'Low';
                  }

                  return `
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                      <div class="text-center">
                        <div class="text-2xl font-bold ${overallColor}">${avgConfidence}%</div>
                        <div class="text-xs text-muted-foreground">Average Confidence</div>
                        <div class="text-xs ${overallColor} font-medium">${overallLevel} Quality</div>
                      </div>
                      <div class="text-center">
                        <div class="text-lg font-bold text-emerald-600">${highConfidence}</div>
                        <div class="text-xs text-muted-foreground">High (\u226580%)</div>
                        <div class="w-full bg-muted rounded-full h-1 mt-1">
                          <div class="bg-emerald-500 h-1 rounded-full" style="width: ${(highConfidence / confidences.length) * 100}%"></div>
                        </div>
                      </div>
                      <div class="text-center">
                        <div class="text-lg font-bold text-amber-600">${mediumConfidence}</div>
                        <div class="text-xs text-muted-foreground">Medium (60-79%)</div>
                        <div class="w-full bg-muted rounded-full h-1 mt-1">
                          <div class="bg-amber-500 h-1 rounded-full" style="width: ${(mediumConfidence / confidences.length) * 100}%"></div>
                        </div>
                      </div>
                      <div class="text-center">
                        <div class="text-lg font-bold text-destructive">${lowConfidence}</div>
                        <div class="text-xs text-muted-foreground">Low (<60%)</div>
                        <div class="w-full bg-muted rounded-full h-1 mt-1">
                          <div class="bg-destructive h-1 rounded-full" style="width: ${(lowConfidence / confidences.length) * 100}%"></div>
                        </div>
                      </div>
                    </div>
                  `;
                })()}
              </div>
            </div>
          ` : ''}

          // Performance Metrics (NEW)
          ${aiResults.performance_metrics ? `
            <div class="mt-3 pt-3 border-t border-purple-200">
              <h6 class="font-medium text-purple-900 mb-2">\u26a1 Performance Metrics</h6>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div class="text-center">
                  <div class="text-lg font-bold text-primary">${aiResults.performance_metrics.data_preparation_time_ms || 0}ms</div>
                  <div class="text-muted-foreground">Data Preparation</div>
                </div>
                <div class="text-center">
                  <div class="text-lg font-bold text-emerald-600">${aiResults.performance_metrics.total_analysis_time_ms || 0}ms</div>
                  <div class="text-muted-foreground">Total Analysis</div>
                </div>
                <div class="text-center">
                  <div class="text-lg font-bold text-primary">${aiResults.performance_metrics.avg_time_per_app_ms || 0}ms</div>
                  <div class="text-muted-foreground">Per Application</div>
                </div>
                <div class="text-center">
                  <div class="text-lg font-bold ${aiResults.performance_metrics.timeout_protection ? 'text-orange-600' : 'text-muted-foreground'}">
                    ${aiResults.performance_metrics.timeout_protection ? 'Enabled' : 'Disabled'}
                  </div>
                  <div class="text-muted-foreground">Timeout Protection</div>
                </div>
              </div>
            </div>
          ` : ''}

          // Vendor selection Summary (NEW)
          ${aiResults.vendor_analysis_summary ? `
            <div class="mt-3 pt-3 border-t border-purple-200">
              <h6 class="font-medium text-purple-900 mb-2">\ud83c\udfe2 Vendor selection Summary</h6>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-3">
                <div class="text-center">
                  <div class="text-xl font-bold text-emerald-600">${aiResults.vendor_analysis_summary.vendors_found || 0}</div>
                  <div class="text-muted-foreground">Vendors Found</div>
                </div>
                <div class="text-center">
                  <div class="text-xl font-bold text-primary">${aiResults.vendor_analysis_summary.high_reliability_vendors || 0}</div>
                  <div class="text-muted-foreground">High Reliability</div>
                </div>
                <div class="text-center">
                  <div class="text-xl font-bold text-primary">${aiResults.vendor_analysis_summary.low_complexity_vendors || 0}</div>
                  <div class="text-muted-foreground">Low Complexity</div>
                </div>
                <div class="text-center">
                  <div class="text-xl font-bold text-orange-600">${Object.keys(aiResults.vendor_analysis_summary.vendor_types || {}).length}</div>
                  <div class="text-muted-foreground">Vendor Types</div>
                </div>
              </div>

              // Vendor Types Breakdown
              ${aiResults.vendor_analysis_summary.vendor_types && Object.keys(aiResults.vendor_analysis_summary.vendor_types).length > 0 ? `
                <div class="mt-3 p-3 bg-purple-50 rounded-lg border border-purple-200">
                  <div class="text-sm font-medium text-purple-900 mb-2">\ud83c\udfe2 Vendor Types Distribution</div>
                  <div class="space-y-2">
                    ${Object.entries(aiResults.vendor_analysis_summary.vendor_types).map(([type, count]) => {
                      const percentage = ((count / aiResults.vendor_analysis_summary.vendors_found) * 100).toFixed(1);
                      const typeColors = {
                        'enterprise': 'bg-primary/10 text-primary/90 border-primary/20',
                        'commercial': 'bg-emerald-500/10 text-green-800 border-emerald-200',
                        'open_source': 'bg-orange-100 text-orange-800 border-orange-200',
                        'custom': 'bg-purple-100 text-purple-800 border-purple-200',
                        'unknown': 'bg-muted text-foreground border-border'
                      };
                      const colorClass = typeColors[type] || typeColors.unknown;
                      return `
                        <div class="flex items-center justify-between p-2 rounded border ${colorClass}">
                          <div class="flex items-center">
                            <span class="text-sm font-medium capitalize">${escapeHtml(type.replace('_', ' '))}</span>
                            <span class="ml-2 text-xs opacity-75">(${count} vendors)</span>
                          </div>
                          <div class="flex items-center">
                            <div class="w-16 bg-muted rounded-full h-2 mr-2">
                              <div class="bg-primary h-2 rounded-full" style="width: ${percentage}%"></div>
                            </div>
                            <span class="text-xs font-bold">${percentage}%</span>
                          </div>
                        </div>
                      `;
                    }).join('')}
                  </div>
                </div>
              ` : ''}

              // Vendor Quality Metrics
              <div class="mt-3 p-3 bg-emerald-500/5 rounded-lg border border-emerald-200">
                <div class="text-sm font-medium text-green-900 mb-2">\u2705 Vendor Quality Metrics</div>
                <div class="grid grid-cols-2 gap-3">
                  <div class="text-center p-2 bg-card rounded border border-emerald-200">
                    <div class="text-lg font-bold text-emerald-600">${aiResults.vendor_analysis_summary.high_reliability_vendors || 0}</div>
                    <div class="text-xs text-muted-foreground">High Reliability</div>
                    <div class="text-xs text-emerald-600 mt-1">${aiResults.vendor_analysis_summary.vendors_found > 0 ? ((aiResults.vendor_analysis_summary.high_reliability_vendors / aiResults.vendor_analysis_summary.vendors_found) * 100).toFixed(1) : 0}% of total</div>
                  </div>
                  <div class="text-center p-2 bg-card rounded border border-emerald-200">
                    <div class="text-lg font-bold text-primary">${aiResults.vendor_analysis_summary.low_complexity_vendors || 0}</div>
                    <div class="text-xs text-muted-foreground">Low Integration Complexity</div>
                    <div class="text-xs text-primary mt-1">${aiResults.vendor_analysis_summary.vendors_found > 0 ? ((aiResults.vendor_analysis_summary.low_complexity_vendors / aiResults.vendor_analysis_summary.vendors_found) * 100).toFixed(1) : 0}% of total</div>
                  </div>
                </div>
              </div>

              // Vendor Recommendations
              ${aiResults.vendor_analysis_summary.vendor_recommendations && aiResults.vendor_analysis_summary.vendor_recommendations.length > 0 ? `
                <div class="mt-3 p-3 bg-primary/5 rounded-lg border border-primary/20">
                  <div class="text-sm font-medium text-blue-900 mb-2">\ud83d\udca1 Vendor Strategy Recommendations</div>
                  <div class="space-y-2">
                    ${aiResults.vendor_analysis_summary.vendor_recommendations.slice(0, 4).map((rec, index) => `
                      <div class="flex items-start p-2 bg-card rounded border border-blue-100">
                        <div class="flex-shrink-0 w-6 h-6 bg-primary text-primary-foreground rounded-full flex items-center justify-center text-xs font-bold mr-2">
                          ${index + 1}
                        </div>
                        <div class="text-sm text-primary/90">${escapeHtml(rec)}</div>
                      </div>
                    `).join('')}
                    ${aiResults.vendor_analysis_summary.vendor_recommendations.length > 4 ? `
                      <div class="text-xs text-primary text-center mt-2">
                        ... and ${aiResults.vendor_analysis_summary.vendor_recommendations.length - 4} more recommendations
                      </div>
                    ` : ''}
                  </div>
                </div>
              ` : ''}
            </div>
          ` : ''}

          // Application-level AI Results (show first 5)
          if (aiResults.applications && aiResults.applications.length > 0) {
            analysisHtml += '<div class="mt-4 pt-3 border-t border-purple-200">';
            analysisHtml += '<h6 class="font-medium text-purple-900 mb-2">AI Analysis Preview (First 5 Applications)</h6>';
            analysisHtml += '<div class="space-y-2 max-h-60 overflow-y-auto">';

            aiResults.applications.slice(0, 5).forEach(app => {
              const capabilityCount = app.capability_mappings ? app.capability_mappings.length : 0;
              const processCount = app.process_mappings ? app.process_mappings.length : 0;
              const archimateCount = app.archimate_elements ? app.archimate_elements.length : 0;
              const avgConfidence = app.avg_capability_confidence ? (app.avg_capability_confidence * 100).toFixed(1) : '0';

              // Determine confidence level and color
              let confidenceLevel, confidenceColor, confidenceBg;
              if (avgConfidence >= 80) {
                confidenceLevel = 'High';
                confidenceColor = 'text-emerald-700';
                confidenceBg = 'bg-emerald-500/10';
              } else if (avgConfidence >= 60) {
                confidenceLevel = 'Medium';
                confidenceColor = 'text-amber-700';
                confidenceBg = 'bg-amber-500/10';
              } else {
                confidenceLevel = 'Low';
                confidenceColor = 'text-destructive';
                confidenceBg = 'bg-destructive/10';
              }

              analysisHtml += '<div class="bg-card rounded-lg p-3 border border-border shadow-sm">';
              analysisHtml += '<div class="flex justify-between items-start mb-2">';
              analysisHtml += '<div class="font-medium text-sm text-foreground">' + escapeHtml(app.application_name) + '</div>';
              analysisHtml += '<div class="px-2 py-1 rounded-full text-xs font-medium ' + confidenceBg + ' ' + confidenceColor + '">';
              analysisHtml += confidenceLevel + ' Confidence';
              analysisHtml += '</div>';
              analysisHtml += '</div>';

              // Confidence progress bar
              analysisHtml += '<div class="mb-3">';
              analysisHtml += '<div class="flex justify-between items-center mb-1">';
              analysisHtml += '<span class="text-xs text-muted-foreground">AI Confidence Score</span>';
              analysisHtml += '<span class="text-xs font-bold ' + confidenceColor + '">' + avgConfidence + '%</span>';
              analysisHtml += '</div>';
              analysisHtml += '<div class="w-full bg-muted rounded-full h-2">';
              analysisHtml += '<div class="h-2 rounded-full transition-all duration-300" style="';
              if (avgConfidence >= 80) {
                analysisHtml += 'background-color: #10b981; width: ' + avgConfidence + '%';
              } else if (avgConfidence >= 60) {
                analysisHtml += 'background-color: #f59e0b; width: ' + avgConfidence + '%';
              } else {
                analysisHtml += 'background-color: #ef4444; width: ' + avgConfidence + '%';
              }
              analysisHtml += '"></div>';
              analysisHtml += '</div>';
              analysisHtml += '</div>';

              // AI mappings grid
              analysisHtml += '<div class="grid grid-cols-3 gap-2 text-xs">';

              // Capabilities
              analysisHtml += '<div class="text-center p-2 bg-primary/5 rounded border border-primary/20">';
              analysisHtml += '<div class="font-bold text-primary">' + capabilityCount + '</div>';
              analysisHtml += '<div class="text-muted-foreground">Capabilities</div>';
              if (capabilityCount > 0) {
                analysisHtml += '<div class="text-xs text-primary mt-1">Mapped</div>';
              }
              analysisHtml += '</div>';

              // Processes
              analysisHtml += '<div class="text-center p-2 bg-emerald-500/5 rounded border border-emerald-200">';
              analysisHtml += '<div class="font-bold text-emerald-600">' + processCount + '</div>';
              analysisHtml += '<div class="text-muted-foreground">Processes</div>';
              if (processCount > 0) {
                analysisHtml += '<div class="text-xs text-emerald-500 mt-1">Classified</div>';
              }
              analysisHtml += '</div>';

              // ArchiMate
              analysisHtml += '<div class="text-center p-2 bg-purple-50 rounded border border-purple-200">';
              analysisHtml += '<div class="font-bold text-primary">' + archimateCount + '</div>';
              analysisHtml += '<div class="text-muted-foreground">ArchiMate</div>';
              if (archimateCount > 0) {
                analysisHtml += '<div class="text-xs text-primary mt-1">Generated</div>';
              }
              analysisHtml += '</div>';

              analysisHtml += '</div>';

              // Vendor analysis if available
              if (app.vendor_analysis && app.vendor_analysis.vendor_name) {
                analysisHtml += '<div class="mt-2 pt-2 border-t border-border/50">';
                analysisHtml += '<div class="text-xs text-muted-foreground">Vendor: <span class="font-medium text-foreground">' + escapeHtml(app.vendor_analysis.vendor_name) + '</span></div>';
                if (app.vendor_analysis.vendor_type && app.vendor_analysis.vendor_type !== 'unknown') {
                  analysisHtml += '<div class="text-xs text-muted-foreground">Type: <span class="capitalize">' + escapeHtml(app.vendor_analysis.vendor_type.replace('_', ' ')) + '</span></div>';
                }
                analysisHtml += '</div>';
              }

              analysisHtml += '</div>';
            });

            if (aiResults.applications.length > 5) {
              analysisHtml += '<div class="text-xs text-muted-foreground text-center mt-2">... and ' + (aiResults.applications.length - 5) + ' more applications</div>';
            }

            analysisHtml += '</div>';
            analysisHtml += '</div>';
          }
        </div>
      `;
    }
  }

  // Column Detection Analysis
  if (data.detected_columns) {
    analysisHtml += `
      <div class="bg-muted/50 border rounded-lg p-4">
        <h5 class="font-bold text-foreground mb-3">\ud83d\uddc2\ufe0f Column Detection</h5>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <div><span class="font-medium">Name:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.name || 'Not Found')}</span></div>
          <div><span class="font-medium">App ID:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.app_id || 'Not Found')}</span></div>
          <div><span class="font-medium">ArchiMate:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.archimate || 'Not Found')}</span></div>
          <div><span class="font-medium">Vendor:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.vendor || 'Not Found')}</span></div>
          <div><span class="font-medium">Package Name:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.package_name || 'Not Found')}</span></div>
          <div><span class="font-medium">Capabilities/PCF:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.capabilities_pcf || 'Not Found')}</span></div>
          <div><span class="font-medium">Description:</span> <span class="text-emerald-600">${escapeHtml(data.detected_columns.description || 'Not Found')}</span></div>
        </div>
      </div>
    `;
  }

  // Application Details
  if (data.details && (data.details.create?.length > 0 || data.details.update?.length > 0)) {
    analysisHtml += `
      <div class="bg-card border rounded-lg p-4">
        <h5 class="font-bold text-foreground mb-3">\ud83d\udccb Application Details</h5>
    `;

    if (data.details.create?.length > 0) {
      analysisHtml += `
        <div class="mb-4">
          <h6 class="font-medium text-emerald-700 mb-2">\u2705 New Applications (${data.details.create.length})</h6>
          <div class="max-h-40 overflow-y-auto bg-emerald-500/5 rounded p-2">
            ${data.details.create.slice(0, 10).map(app => `<div class="text-sm">\u2022 ${escapeHtml(app.name)} (row ${app.row + 1})</div>`).join('')}
            ${data.details.create.length > 10 ? `<div class="text-sm text-muted-foreground">... and ${data.details.create.length - 10} more</div>` : ''}
          </div>
            <div class="text-center">
              <div class="text-xl font-bold text-destructive">${escapeHtml(data.apqc_details?.not_found?.length || 0)}</div>
              <div class="text-muted-foreground">Not Found</div>
            </div>
          </div>
      `;

      if (data.apqc_details?.link?.length > 0 || data.apqc_details?.not_found?.length > 0) {
        analysisHtml += '<div class="space-y-2">';
        if (data.apqc_details.link?.length > 0) {
          analysisHtml += `
            <div>
              <h6 class="font-medium text-emerald-700">Matched Processes (${data.apqc_details.link.length})</h6>
              <div class="max-h-30 overflow-y-auto bg-emerald-500/10 rounded p-2 text-sm">
                ${data.apqc_details.link.slice(0, 5).map(proc => `<div>\u2022 ${escapeHtml(proc.process_code)} - ${escapeHtml(proc.process_name)} (${escapeHtml(proc.source)})</div>`).join('')}
              </div>
            </div>
          `;
        }
        if (data.apqc_details.not_found?.length > 0) {
          analysisHtml += `
            <div>
              <h6 class="font-medium text-destructive">Unmatched (${data.apqc_details.not_found.length})</h6>
              <div class="max-h-30 overflow-y-auto bg-destructive/10 rounded p-2 text-sm">
                ${data.apqc_details.not_found.slice(0, 5).map(proc => `<div>\u2022 ${escapeHtml(proc.process_code)} - ${escapeHtml(proc.process_name)}</div>`).join('')}
              </div>
            </div>
          `;
        }
        analysisHtml += '</div>';
      }

      analysisHtml += '</div>';
    }

    // Vendor selection
    if (data.vendors) {
      analysisHtml += `
        <div class="bg-emerald-500/5 border border-emerald-200 rounded-lg p-4">
          <h5 class="font-bold text-green-900 mb-3">\ud83c\udfe2 Vendor selection</h5>
          <div class="grid grid-cols-3 gap-4 text-sm mb-3">
            <div class="text-center">
              <div class="text-xl font-bold text-emerald-600">${escapeHtml(data.vendors.will_link || 0)}</div>
              <div class="text-muted-foreground">Existing</div>
            </div>
            <div class="text-center">
              <div class="text-xl font-bold text-primary">${escapeHtml(data.vendors.will_create || 0)}</div>
              <div class="text-muted-foreground">New Vendors</div>
            </div>
            <div class="text-center">
              <div class="text-xl font-bold text-primary">${escapeHtml(data.vendors.inferred || 0)}</div>
              <div class="text-muted-foreground">Inferred</div>
            </div>
          </div>
      `;

      if (data.vendor_details?.create?.length > 0 || data.vendor_details?.link?.length > 0) {
        analysisHtml += '<div class="space-y-2">';
        if (data.vendor_details.create?.length > 0) {
          analysisHtml += `
            <div>
              <h6 class="font-medium text-primary">New Vendors (${data.vendor_details.create.length})</h6>
              <div class="max-h-30 overflow-y-auto bg-primary/10 rounded p-2 text-sm">
                ${data.vendor_details.create.slice(0, 5).map(v => {
                  const aiStatus = v.ai_analysis_status ? ` (${escapeHtml(v.ai_analysis_status)})` : '';
                  const sourceInfo = v.source ? ` (source: ${escapeHtml(v.source)})` : '';
                  return `<div>\u2022 ${escapeHtml(v.name)} (row ${v.row + 1}${sourceInfo})${aiStatus}</div>`;
                }).join('')}
              </div>
            </div>
          `;
        }
        if (data.vendor_details.link?.length > 0) {
          analysisHtml += `
            <div>
              <h6 class="font-medium text-emerald-700">Existing Vendors (${data.vendor_details.link.length})</h6>
              <div class="max-h-30 overflow-y-auto bg-emerald-500/10 rounded p-2 text-sm">
                ${data.vendor_details.link.slice(0, 5).map(v => {
                  const aiStatus = v.ai_analysis_status ? ` (${escapeHtml(v.ai_analysis_status)})` : '';
                  const appName = v.app_name ? ` - ${escapeHtml(v.app_name)}` : '';
                  return `<div>\u2022 ${escapeHtml(v.name)} (ID: ${escapeHtml(v.existing_id)}${appName})${aiStatus}</div>`;
                }).join('')}
              </div>
            </div>
          `;
        }
        if (data.vendor_details.inferred?.length > 0) {
          analysisHtml += `
            <div>
              <h6 class="font-medium text-purple-700">Inferred Vendors (${data.vendor_details.inferred.length})</h6>
              <div class="max-h-30 overflow-y-auto bg-purple-100 rounded p-2 text-sm">
                ${data.vendor_details.inferred.slice(0, 5).map(v => {
                  const aiStatus = v.ai_analysis_status ? ` (${escapeHtml(v.ai_analysis_status)})` : '';
                  const sourceInfo = v.source ? ` (${escapeHtml(v.source)})` : '';
                  const appName = v.app_name ? ` - ${escapeHtml(v.app_name)}` : '';
                  return `<div>\u2022 ${escapeHtml(v.name)} (ID: ${escapeHtml(v.existing_id)}${appName}${sourceInfo})${aiStatus}</div>`;
                }).join('')}
              </div>
            </div>
          `;
        }
        analysisHtml += '</div>';
      }

      analysisHtml += '</div>';
    }

    // Validation Errors
    if (data.validation_errors && data.validation_errors.length > 0) {
      analysisHtml += `
        <div class="bg-destructive/5 border border-destructive/20 rounded-lg p-4">
          <h5 class="font-bold text-red-900 mb-3">\u274c Validation Errors (${data.validation_errors.length})</h5>
          <div class="max-h-40 overflow-y-auto bg-destructive/10 rounded p-2">
            ${data.validation_errors.map(error => `<div class="text-sm text-destructive">\u2022 Row ${error.row + 1}: ${escapeHtml(error.error)}</div>`).join('')}
          </div>
        </div>
      `;
    }

    // No Name Entries
    if (data.no_name && data.no_name.length > 0) {
      analysisHtml += `
        <div class="bg-orange-50 border border-orange-200 rounded-lg p-4">
          <h5 class="font-bold text-orange-900 mb-3">\u26a0\ufe0f Missing Names (${data.no_name.length})</h5>
          <div class="text-sm text-orange-700">
            Rows with no application name: ${data.no_name.map(row => row + 1).join(', ')}
          </div>
        </div>
      `;
    }

    analysisHtml += '</div>';

    safeHTML(analysisContent, analysisHtml);
  }
}

// Modal functions
function openApplicationImportModal() {
  switchImportTab('excel');
  loadImportHistory();
  addManualEntryRow(); // Add one empty row by default
  Platform.modal.open('application-import-modal');
}

function closeApplicationImportModal() {
  resetImportModal();
  Platform.modal.close('application-import-modal');
}

function switchImportTab(tab) {
  // Hide all tabs
  document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-button').forEach(el => {
    el.classList.remove('active', 'border-blue-600', 'text-primary');
    el.classList.add('border-transparent', 'text-muted-foreground');
  });

  // Show selected tab
  document.getElementById(`tab-content-${tab}`).classList.remove('hidden');
  const tabButton = document.getElementById(`tab-${tab}`);
  tabButton.classList.add('active', 'border-blue-600', 'text-primary');
  tabButton.classList.remove('border-transparent', 'text-muted-foreground');

  if (tab === 'history') {
    loadImportHistory();
  }
}

function resetImportModal() {
  document.getElementById('excel-file-input').value = '';
  document.getElementById('excel-file-name').textContent = 'No file selected';
  document.getElementById('excel-preview').classList.add('hidden');
  safeHTML(document.getElementById('manual-entry-tbody'), '');
  selectedExcelFile = null;
  excelPreviewData = [];
  manualEntryRowCount = 0;
  apqcLinksByRow = {};  // Clear APQC links from analysis
}

// Update the existing function to open the new modal
window.openApplicationCreationModal = function() {
  openApplicationImportModal();
};
