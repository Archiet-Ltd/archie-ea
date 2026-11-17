/**
 * Admin AI settings
 * Extracted from app/templates/admin/api_settings.html
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

// Initialize Lucide icons
lucide.createIcons();

let csrfToken = APP_CONFIG.csrfToken || '';
let providerFieldId = APP_CONFIG.providerFieldId || 'provider';
let defaultModelFieldId = APP_CONFIG.defaultModelFieldId || 'default_model';

// ===== Provider-specific field toggling =====
let providerSelect = document.getElementById(providerFieldId);
let jiraFields = document.getElementById('jira-fields');
let huggingFaceFields = document.getElementById('huggingface-fields');
let customApiFields = document.getElementById('custom-api-fields');
let openrouterFields = document.getElementById('openrouter-fields');

function toggleProviderFields() {
    let provider = providerSelect.value;
    jiraFields.style.display = 'none';
    huggingFaceFields.style.display = 'none';
    customApiFields.style.display = 'none';
    openrouterFields.style.display = 'none';

    if (provider === 'jira') jiraFields.style.display = 'block';
    else if (provider === 'huggingface') huggingFaceFields.style.display = 'block';
    else if (provider === 'custom') customApiFields.style.display = 'block';
    else if (provider === 'openrouter') {
        openrouterFields.style.display = 'block';
        // Auto-load models when OpenRouter is selected/edited
        if (inlineModelsCache.length === 0) fetchInlineModels();
    }
}

providerSelect.addEventListener('change', toggleProviderFields);
toggleProviderFields(); // Initial check

// ===== Shared model rendering logic =====
function buildModelHTML(m, selectedId) {
    let freeBadge = m.is_free
        ? '<span class="text-xs px-1.5 py-0.5 rounded bg-green-500/10 text-emerald-600 border border-green-500/30">Free</span>'
        : '<span class="text-xs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 border border-amber-500/30">Paid</span>';
    let ctx = m.context_length ? Math.round(m.context_length / 1024) + 'K ctx' : '';
    let isSelected = m.id === selectedId;
    let selectedClass = isSelected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
    return '<button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ' + selectedClass + ' transition-colors flex items-center justify-between gap-2" data-model-id="' + m.id + '" data-model-name="' + m.name + '">' +
        '<div class="min-w-0"><div class="text-sm font-medium truncate">' + m.name + '</div><div class="text-xs text-muted-foreground truncate">' + m.id + '</div></div>' +
        '<div class="flex items-center gap-1.5 shrink-0">' +
            (isSelected ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">Current</span>' : '') +
            freeBadge +
            (ctx ? '<span class="text-xs text-muted-foreground">' + ctx + '</span>' : '') +
        '</div></button>';
}

function fetchModelsFromAPI(freeOnly, search) {
    let params = new URLSearchParams();
    if (freeOnly) params.set('free_only', 'true');
    if (search) params.set('search', search);
    params.set('limit', '100');
    return Platform.fetch.get('/api/v1/llm/openrouter/models', Object.fromEntries(params), { silent: true });
}

// ===== Inline model browser (inside the form) =====
let orBrowseBtn = document.getElementById('or-browse-btn');
let orModelsList = document.getElementById('or-models-list');
let orSearch = document.getElementById('or-search');
let orFreeOnly = document.getElementById('or-free-only');
let inlineModelsCache = [];

function fetchInlineModels() {
    let freeOnly = orFreeOnly.checked;
    let search = orSearch.value.trim();

    safeHTML(orModelsList, '<div class="flex items-center justify-center py-4 text-muted-foreground text-sm"><span>Loading models...</span></div>');
    orModelsList.classList.remove('hidden');

    fetchModelsFromAPI(freeOnly, search)
        .then(function(data) {
            if (!data.success) {
                safeHTML(orModelsList, '<p class="text-sm text-destructive text-center py-4">' + DOMPurify.sanitize(data.error || 'Failed to load models') + '</p>');
                return;
            }
            if (!data.models || !data.models.length) {
                let hint = freeOnly ? ' Try unchecking "Free models only".' : '';
                safeHTML(orModelsList, '<p class="text-sm text-muted-foreground text-center py-4">No models matched.' + hint + '</p>');
                return;
            }
            inlineModelsCache = data.models;
            renderInlineModels(data.models);
        })
        .catch(function(err) {
            safeHTML(orModelsList, '<p class="text-sm text-destructive text-center py-4">Request failed: ' + DOMPurify.sanitize(err.message) + '</p>');
        });
}

function getInlineSelectedModels() {
    let val = (document.getElementById(defaultModelFieldId) || {}).value || '';
    return val.split(',').map(function(m) { return m.trim(); }).filter(function(m) { return m; });
}

function renderInlineModels(models) {
    let selectedIds = getInlineSelectedModels();
    let html = '';
    models.forEach(function(m) {
        let selected = selectedIds.indexOf(m.id) >= 0;
        let selIdx = selectedIds.indexOf(m.id);
        let selectedClass = selected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
        let selBadge = selected
            ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">' + (selIdx === 0 ? 'Primary' : 'Backup ' + selIdx) + '</span>'
            : '';
        let freeBadge = m.is_free
            ? '<span class="text-xs px-1.5 py-0.5 rounded bg-green-500/10 text-emerald-600 border border-green-500/30">Free</span>'
            : '<span class="text-xs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 border border-amber-500/30">Paid</span>';
        let ctx = m.context_length ? Math.round(m.context_length / 1024) + 'K ctx' : '';
        let atLimit = !selected && selectedIds.length >= MAX_MODELS;
        let opacity = atLimit ? 'opacity-40 pointer-events-none' : '';
        html += '<button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ' + selectedClass + ' ' + opacity + ' transition-colors flex items-center justify-between gap-2" data-model-id="' + m.id + '" data-model-name="' + m.name + '">' +
            '<div class="min-w-0"><div class="text-sm font-medium truncate">' + m.name + '</div><div class="text-xs text-muted-foreground truncate">' + m.id + '</div></div>' +
            '<div class="flex items-center gap-1.5 shrink-0">' + selBadge + freeBadge + (ctx ? '<span class="text-xs text-muted-foreground">' + ctx + '</span>' : '') + '</div></button>';
    });
    safeHTML(orModelsList, html);

    orModelsList.querySelectorAll('.or-model-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            let mid = this.dataset.modelId;
            let selected = getInlineSelectedModels();
            let idx = selected.indexOf(mid);
            if (idx >= 0) {
                selected.splice(idx, 1);
            } else if (selected.length < MAX_MODELS) {
                selected.push(mid);
            }
            document.getElementById(defaultModelFieldId).value = selected.join(', ');
            renderInlineModels(models);
        });
    });
}

orBrowseBtn.addEventListener('click', fetchInlineModels);

let inlineSearchTimeout;
orSearch.addEventListener('input', function() {
    clearTimeout(inlineSearchTimeout);
    inlineSearchTimeout = setTimeout(function() {
        if (inlineModelsCache.length > 0) {
            let q = orSearch.value.trim().toLowerCase();
            let filtered = q
                ? inlineModelsCache.filter(function(m) { return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q); })
                : inlineModelsCache;
            renderInlineModels(filtered);
            orModelsList.classList.remove('hidden');
        } else {
            fetchInlineModels();
        }
    }, 300);
});

orFreeOnly.addEventListener('change', function() {
    inlineModelsCache = [];
    fetchInlineModels();
});

// ===== Standalone Model Browser Modal (multi-select, up to 5) =====
let orModal = document.getElementById('or-modal');
let orModalBackdrop = document.getElementById('or-modal-backdrop');
let orModalModelsBody = document.getElementById('or-modal-models-body');
let orModalSearch = document.getElementById('or-modal-search');
let orModalFreeOnly = document.getElementById('or-modal-free-only');
let orSelectedSection = document.getElementById('or-modal-selected');
let orSelectedChips = document.getElementById('or-modal-selected-chips');
let orSelectedCount = document.getElementById('or-modal-selected-count');
let orModalSaveBtn = document.getElementById('or-modal-save');
let modalModelsCache = [];
let modalSelectedModels = []; // Array of {id, name} -- order = priority
let MAX_MODELS = 5;

function openOrModal() {
    Platform.modal.open('or-modal');
    // Pre-populate from existing default_model (comma-separated)
    modalSelectedModels = [];
    let existingModels = (document.getElementById(defaultModelFieldId) || {}).value || '';
    if (existingModels.trim()) {
        existingModels.split(',').forEach(function(mid) {
            mid = mid.trim();
            if (mid) modalSelectedModels.push({ id: mid, name: mid });
        });
    }
    updateModalChips();
    orModalSearch.value = '';
    fetchModalModels();
}

function closeOrModal() {
    Platform.modal.close('or-modal');
}

document.querySelectorAll('.or-change-model-btn').forEach(function(btn) {
    btn.addEventListener('click', openOrModal);
});
document.getElementById('or-modal-close').addEventListener('click', closeOrModal);
document.getElementById('or-modal-cancel').addEventListener('click', closeOrModal);
orModalBackdrop.addEventListener('click', closeOrModal);

function isModelSelected(modelId) {
    return modalSelectedModels.some(function(m) { return m.id === modelId; });
}

function toggleModelSelection(modelId, modelName) {
    let idx = modalSelectedModels.findIndex(function(m) { return m.id === modelId; });
    if (idx >= 0) {
        // Deselect
        modalSelectedModels.splice(idx, 1);
    } else if (modalSelectedModels.length < MAX_MODELS) {
        // Select
        modalSelectedModels.push({ id: modelId, name: modelName });
    }
    updateModalChips();
}

function removeModelFromSelection(modelId) {
    modalSelectedModels = modalSelectedModels.filter(function(m) { return m.id !== modelId; });
    updateModalChips();
    // Re-render model list to update checkmarks
    if (modalModelsCache.length > 0) {
        let q = orModalSearch.value.trim().toLowerCase();
        let filtered = q
            ? modalModelsCache.filter(function(m) { return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q); })
            : modalModelsCache;
        renderModalModels(filtered);
    }
}

function updateModalChips() {
    orSelectedCount.textContent = modalSelectedModels.length + '/' + MAX_MODELS;
    orModalSaveBtn.disabled = modalSelectedModels.length === 0;

    if (modalSelectedModels.length > 0) {
        orSelectedSection.classList.remove('hidden');
        let html = '';
        modalSelectedModels.forEach(function(m, i) {
            let label = i === 0 ? 'Primary' : 'Backup ' + i;
            let badgeColor = i === 0 ? 'bg-primary/10 text-primary border-primary/30' : 'bg-muted text-muted-foreground border-input';
            html += '<span class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border ' + badgeColor + '">' +
                '<span class="font-medium">' + label + ':</span> ' +
                '<span class="max-w-[160px] truncate">' + m.id.split('/').pop() + '</span>' +
                '<button type="button" class="or-chip-remove ml-0.5 hover:text-destructive" data-model-id="' + m.id + '">&times;</button>' +
            '</span>';
        });
        safeHTML(orSelectedChips, html);
        orSelectedChips.querySelectorAll('.or-chip-remove').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                removeModelFromSelection(this.dataset.modelId);
            });
        });
    } else {
        orSelectedSection.classList.add('hidden');
        safeHTML(orSelectedChips, '');
    }
}

function buildMultiModelHTML(m) {
    let freeBadge = m.is_free
        ? '<span class="text-xs px-1.5 py-0.5 rounded bg-green-500/10 text-emerald-600 border border-green-500/30">Free</span>'
        : '<span class="text-xs px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-600 border border-amber-500/30">Paid</span>';
    let ctx = m.context_length ? Math.round(m.context_length / 1024) + 'K ctx' : '';
    let selected = isModelSelected(m.id);
    let selIdx = modalSelectedModels.findIndex(function(x) { return x.id === m.id; });
    let selectedClass = selected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
    let selBadge = selected
        ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">' + (selIdx === 0 ? 'Primary' : 'Backup ' + selIdx) + '</span>'
        : '';
    let atLimit = !selected && modalSelectedModels.length >= MAX_MODELS;
    let opacity = atLimit ? 'opacity-40 pointer-events-none' : '';
    return '<button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ' + selectedClass + ' ' + opacity + ' transition-colors flex items-center justify-between gap-2" data-model-id="' + m.id + '" data-model-name="' + m.name + '">' +
        '<div class="min-w-0">' +
            '<div class="text-sm font-medium truncate">' + m.name + '</div>' +
            '<div class="text-xs text-muted-foreground truncate">' + m.id + '</div>' +
        '</div>' +
        '<div class="flex items-center gap-1.5 shrink-0">' +
            selBadge +
            freeBadge +
            (ctx ? '<span class="text-xs text-muted-foreground">' + ctx + '</span>' : '') +
        '</div>' +
    '</button>';
}

function fetchModalModels() {
    let freeOnly = orModalFreeOnly.checked;
    let search = orModalSearch.value.trim();

    safeHTML(orModalModelsBody, '<div class="flex items-center justify-center py-8 text-muted-foreground text-sm"><span>Loading models...</span></div>');

    fetchModelsFromAPI(freeOnly, search)
        .then(function(data) {
            if (!data.success) {
                safeHTML(orModalModelsBody, '<p class="text-sm text-destructive text-center py-8">' + DOMPurify.sanitize(data.error || 'Failed to load models') + '</p>');
                return;
            }
            if (!data.models || !data.models.length) {
                let hint = freeOnly ? ' Try unchecking "Free only".' : '';
                safeHTML(orModalModelsBody, '<p class="text-sm text-muted-foreground text-center py-8">No models matched.' + hint + '</p>');
                return;
            }
            modalModelsCache = data.models;
            renderModalModels(data.models);
        })
        .catch(function(err) {
            safeHTML(orModalModelsBody, '<p class="text-sm text-destructive text-center py-8">Request failed: ' + DOMPurify.sanitize(err.message) + '</p>');
        });
}

function renderModalModels(models) {
    let html = '<div class="space-y-1">';
    models.forEach(function(m) { html += buildMultiModelHTML(m); });
    html += '</div>';
    safeHTML(orModalModelsBody, html);

    orModalModelsBody.querySelectorAll('.or-model-btn').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            toggleModelSelection(this.dataset.modelId, this.dataset.modelName);
            renderModalModels(models);
        });
    });
}

let modalSearchTimeout;
orModalSearch.addEventListener('input', function() {
    clearTimeout(modalSearchTimeout);
    modalSearchTimeout = setTimeout(function() {
        if (modalModelsCache.length > 0) {
            let q = orModalSearch.value.trim().toLowerCase();
            let filtered = q
                ? modalModelsCache.filter(function(m) { return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q); })
                : modalModelsCache;
            renderModalModels(filtered);
        } else {
            fetchModalModels();
        }
    }, 300);
});

orModalFreeOnly.addEventListener('change', function() {
    modalModelsCache = [];
    fetchModalModels();
});

// Save models directly from modal (comma-separated)
orModalSaveBtn.addEventListener('click', function() {
    if (modalSelectedModels.length === 0) return;
    orModalSaveBtn.disabled = true;
    orModalSaveBtn.textContent = 'Saving...';

    let modelStr = modalSelectedModels.map(function(m) { return m.id; }).join(', ');
    Platform.fetch.post('/admin/api-settings/update-model', { provider: 'openrouter', model: modelStr })
    .then(function(data) {
        if (data.success) {
            closeOrModal();
            location.reload();
        } else {
            Platform.toast.error('Failed to save: ' + (data.error || 'Unknown error'));
            orModalSaveBtn.disabled = false;
            orModalSaveBtn.textContent = 'Save Models';
        }
    })
    .catch(function(err) {
        Platform.toast.error('Request failed.');
        orModalSaveBtn.disabled = false;
        orModalSaveBtn.textContent = 'Save Models';
    });
});

// ===== Load from .env Modal =====
let envModal = document.getElementById('env-modal');
let envModalBackdrop = document.getElementById('env-modal-backdrop');
let envKeysBody = document.getElementById('env-keys-body');
let loadEnvBtn = document.getElementById('load-env-btn');

function openEnvModal() {
    Platform.modal.open('env-modal');
    fetchEnvKeys();
}

function closeEnvModal() {
    Platform.modal.close('env-modal');
}

loadEnvBtn.addEventListener('click', openEnvModal);
document.getElementById('env-modal-close').addEventListener('click', closeEnvModal);
document.getElementById('env-modal-cancel').addEventListener('click', closeEnvModal);
envModalBackdrop.addEventListener('click', closeEnvModal);

function fetchEnvKeys() {
    safeHTML(envKeysBody, '<div class="flex items-center justify-center py-8 text-muted-foreground"><span class="mr-2">Scanning...</span></div>');
    Platform.fetch.get('/admin/api-settings/env-keys')
        .then(function(data) {
            if (!data.success || data.keys.length === 0) {
                safeHTML(envKeysBody, '<p class="text-sm text-muted-foreground text-center py-8">No API keys found in environment variables.</p>');
                return;
            }
            let html = '<div class="space-y-2">';
            data.keys.forEach(function(k) {
                let statusBadge = k.in_database
                    ? '<span class="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary/90">In DB</span>'
                    : '<span class="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-yellow-800">New</span>';
                html += '<label class="flex items-center gap-3 p-3 rounded-md border hover:bg-accent/50 cursor-pointer transition-colors">' +
                    '<input type="checkbox" class="env-key-cb h-4 w-4 rounded border-border" value="' + k.env_var + '"' + (k.in_database ? '' : ' checked') + '>' +
                    '<div class="flex-1 min-w-0">' +
                        '<div class="flex items-center gap-2">' +
                            '<span class="font-medium text-sm">' + k.provider + '</span>' +
                            statusBadge +
                        '</div>' +
                        '<div class="text-xs text-muted-foreground mt-0.5">' +
                            k.env_var + ': <code class="bg-muted px-1 rounded">' + k.masked_key + '</code>' +
                        '</div>' +
                        (k.default_model ? '<div class="text-xs text-muted-foreground">Default model: ' + k.default_model + '</div>' : '') +
                    '</div>' +
                '</label>';
            });
            html += '</div>';
            safeHTML(envKeysBody, html);
        })
        .catch(function(err) {
            safeHTML(envKeysBody, '<p class="text-sm text-destructive text-center py-8">Failed to scan environment variables.</p>');
        });
}

document.getElementById('env-import-btn').addEventListener('click', function() {
    let selected = Array.from(document.querySelectorAll('.env-key-cb:checked')).map(function(cb) { return cb.value; });
    if (selected.length === 0) {
        Platform.toast.warning('Please select at least one key to import.');
        return;
    }
    let updateExisting = document.getElementById('env-update-existing').checked;

    Platform.fetch.post('/admin/api-settings/load-env', { keys: selected, update_existing: updateExisting })
    .then(function(data) {
        if (data.success) {
            closeEnvModal();
            location.reload();
        } else {
            Platform.toast.error('Import failed: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(function(err) { Platform.toast.error('Import request failed.'); });
});
