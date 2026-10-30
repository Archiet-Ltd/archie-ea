    // Initialize Lucide icons
    lucide.createIcons();

    const csrfToken = window.__PAGE_CONFIG__.csrfToken;

    // ===== Provider-specific field toggling =====
    const providerSelect = document.getElementById(window.__PAGE_CONFIG__.formProviderId);
    const jiraFields = document.getElementById('jira-fields');
    const huggingFaceFields = document.getElementById('huggingface-fields');
    const customApiFields = document.getElementById('custom-api-fields');
    const openrouterFields = document.getElementById('openrouter-fields');
    const watsonxFields = document.getElementById('watsonx-fields');
    const defaultModelField = document.getElementById('default-model-field');

    function toggleProviderFields() {
        const provider = providerSelect.value;
        jiraFields.style.display = 'none';
        huggingFaceFields.style.display = 'none';
        customApiFields.style.display = 'none';
        openrouterFields.style.display = 'none';
        watsonxFields.style.display = 'none';
        defaultModelField.style.display = provider === 'watsonx' ? 'none' : '';

        if (provider === 'jira') jiraFields.style.display = 'block';
        else if (provider === 'huggingface') huggingFaceFields.style.display = 'block';
        else if (provider === 'custom') customApiFields.style.display = 'block';
        else if (provider === 'watsonx') {
            watsonxFields.style.display = 'block';
            if (!watsonxLoaded && document.getElementById('watsonx_url').value.trim()) loadWatsonxModels();
        }
        else if (provider === 'openrouter') {
            openrouterFields.style.display = 'block';
            // Auto-load models when OpenRouter is selected/edited
            if (inlineModelsCache.length === 0) fetchInlineModels();
        }
    }

    // ===== IBM watsonx.ai: Granite model dropdown =====
    const watsonxModel = document.getElementById('watsonx-model');
    const watsonxStatus = document.getElementById('watsonx-status');
    const watsonxLoad = document.querySelector('[data-testid="watsonx-load-models"]');
    const defaultModelInput = document.getElementById(window.__PAGE_CONFIG__.formDefaultModelId);
    let watsonxLoaded = false;

    function loadWatsonxModels() {
        const url = document.getElementById('watsonx_url').value.trim();
        watsonxStatus.textContent = 'Loading Granite models...';
        watsonxStatus.classList.remove('text-destructive');
        const current = defaultModelInput.value;
        return Platform.fetch.get('/api/v1/llm/watsonx/models', url ? { url: url } : {}, { silent: true })
            .then(data => {
                if (!data.success) throw new Error(data.error || 'IBM watsonx.ai models could not be loaded.');
                if (!data.models.length) {
                    watsonxStatus.textContent = 'IBM watsonx.ai lists no Granite chat models at this address.';
                    return;
                }
                watsonxModel.replaceChildren();
                data.models.forEach(m => {
                    const option = document.createElement('option');
                    option.value = m.id;
                    option.textContent = m.id;
                    if (m.id === current) option.selected = true;
                    watsonxModel.appendChild(option);
                });
                defaultModelInput.value = watsonxModel.value;
                watsonxLoaded = true;
                watsonxStatus.textContent = data.models.length + ' Granite models loaded.';
            })
            .catch(err => {
                watsonxStatus.textContent = err.message;
                watsonxStatus.classList.add('text-destructive');
            });
    }

    watsonxLoad.addEventListener('click', loadWatsonxModels);
    watsonxModel.addEventListener('change', () => { defaultModelInput.value = watsonxModel.value; });

    providerSelect.addEventListener('change', toggleProviderFields);
    toggleProviderFields(); // Initial check

    // ===== Shared model rendering logic =====
    function buildModelHTML(m, selectedId) {
        const freeBadge = m.is_free
            ? '<span class="text-xs px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/30">Free</span>'
            : '<span class="text-xs px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/30">Paid</span>';
        const ctx = m.context_length ? `${Math.round(m.context_length / 1024)}K ctx` : '';
        const isSelected = m.id === selectedId;
        const selectedClass = isSelected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
        return `
            <button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ${selectedClass} transition-colors flex items-center justify-between gap-2" data-model-id="${m.id}" data-model-name="${m.name}" aria-label="Action">
                <div class="min-w-0">
                    <div class="text-sm font-medium truncate">${m.name}</div>
                    <div class="text-xs text-muted-foreground truncate">${m.id}</div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    ${isSelected ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">Current</span>' : ''}
                    ${freeBadge}
                    ${ctx ? '<span class="text-xs text-muted-foreground">' + ctx + '</span>' : ''}
                </div>
            </button>`;
    }

    function fetchModelsFromAPI(freeOnly, search) {
        const params = new URLSearchParams();
        if (freeOnly) params.set('free_only', 'true');
        if (search) params.set('search', search);
        params.set('limit', '100');
        return Platform.fetch.get('/api/v1/llm/openrouter/models', Object.fromEntries(params), { silent: true });
    }

    function escapeHtml(str) {
        if (str == null) return '';
        return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }

    // ===== Inline model browser (inside the form) =====
    const orBrowseBtn = document.getElementById('or-browse-btn');
    const orModelsList = document.getElementById('or-models-list');
    const orSearch = document.getElementById('or-search');
    const orFreeOnly = document.getElementById('or-free-only');
    let inlineModelsCache = [];

    function fetchInlineModels() {
        const freeOnly = orFreeOnly.checked;
        const search = orSearch.value.trim();

        orModelsList.innerHTML = '<div class="flex items-center justify-center py-4 text-muted-foreground text-sm"><span>Loading models...</span></div>'; /* safe-html */
        orModelsList.classList.remove('hidden');

        fetchModelsFromAPI(freeOnly, search)
            .then(data => {
                if (!data.success) {
                    orModelsList.innerHTML = '<p class="text-sm text-destructive text-center py-4">' + DOMPurify.sanitize(data.error || 'Failed to load models') + '</p>';
                    return;
                }
                if (!data.models || !data.models.length) {
                    const hint = freeOnly ? ' Try unchecking "Free models only".' : '';
                    orModelsList.innerHTML = '<p class="text-sm text-muted-foreground text-center py-4">No models matched.' + hint + '</p>'; /* safe-html */
                    return;
                }
                inlineModelsCache = data.models;
                renderInlineModels(data.models);
            })
            .catch(err => {
                orModelsList.innerHTML = '<p class="text-sm text-destructive text-center py-4">Request failed: ' + DOMPurify.sanitize(err.message) + '</p>';
            });
    }

    function getInlineSelectedModels() {
        const val = document.getElementById(window.__PAGE_CONFIG__.formDefaultModelId).value || '';
        return val.split(',').map(m => m.trim()).filter(m => m);
    }

    function renderInlineModels(models) {
        const selectedIds = getInlineSelectedModels();
        let html = '';
        models.forEach(m => {
            const selected = selectedIds.indexOf(m.id) >= 0;
            const selIdx = selectedIds.indexOf(m.id);
            const selectedClass = selected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
            const selBadge = selected
                ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">' + (selIdx === 0 ? 'Primary' : 'Backup ' + selIdx) + '</span>'
                : '';
            const freeBadge = m.is_free
                ? '<span class="text-xs px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/30">Free</span>'
                : '<span class="text-xs px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/30">Paid</span>';
            const ctx = m.context_length ? Math.round(m.context_length / 1024) + 'K ctx' : '';
            const atLimit = !selected && selectedIds.length >= MAX_MODELS;
            const opacity = atLimit ? 'opacity-40 pointer-events-none' : '';
            html += '<button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ' + selectedClass + ' ' + opacity + ' transition-colors flex items-center justify-between gap-2" data-model-id="' + escapeHtml(m.id) + '" data-model-name="' + escapeHtml(m.name) + '" aria-label="Action">' +
                '<div class="min-w-0"><div class="text-sm font-medium truncate">' + escapeHtml(m.name) + '</div><div class="text-xs text-muted-foreground truncate">' + escapeHtml(m.id) + '</div></div>' +
                '<div class="flex items-center gap-1.5 shrink-0">' + selBadge + freeBadge + (ctx ? '<span class="text-xs text-muted-foreground">' + escapeHtml(ctx) + '</span>' : '') + '</div></button>';
        });
        orModelsList.innerHTML = html; /* safe-html: all values escaped via escapeHtml() */

        orModelsList.querySelectorAll('.or-model-btn').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                const mid = this.dataset.modelId;
                let selected = getInlineSelectedModels();
                const idx = selected.indexOf(mid);
                if (idx >= 0) {
                    selected.splice(idx, 1);
                } else if (selected.length < MAX_MODELS) {
                    selected.push(mid);
                }
                document.getElementById(window.__PAGE_CONFIG__.formDefaultModelId).value = selected.join(', ');
                renderInlineModels(models);
            });
        });
    }

    orBrowseBtn.addEventListener('click', fetchInlineModels);

    let inlineSearchTimeout;
    orSearch.addEventListener('input', function() {
        clearTimeout(inlineSearchTimeout);
        inlineSearchTimeout = setTimeout(() => {
            if (inlineModelsCache.length > 0) {
                const q = orSearch.value.trim().toLowerCase();
                const filtered = q
                    ? inlineModelsCache.filter(m => m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
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
    const orModal = document.getElementById('or-modal');
    const orModalBackdrop = document.getElementById('or-modal-backdrop');
    const orModalModelsBody = document.getElementById('or-modal-models-body');
    const orModalSearch = document.getElementById('or-modal-search');
    const orModalFreeOnly = document.getElementById('or-modal-free-only');
    const orModalSelected = document.getElementById('or-modal-selected');
    const orModalSelectedChips = document.getElementById('or-modal-selected-chips');
    const orModalSelectedCount = document.getElementById('or-modal-selected-count');
    const orModalSaveBtn = document.getElementById('or-modal-save');
    let modalModelsCache = [];
    let modalSelectedModels = []; // Array of {id, name} — order = priority
    const MAX_MODELS = 5;

    function openOrModal() {
        orModal.classList.remove('hidden');
        // Pre-populate from existing default_model (comma-separated)
        modalSelectedModels = [];
        const existingModels = (document.getElementById(window.__PAGE_CONFIG__.formDefaultModelId) || {}).value || '';
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
        orModal.classList.add('hidden');
    }

    document.querySelectorAll('.or-change-model-btn').forEach(btn => {
        btn.addEventListener('click', openOrModal);
    });
    document.getElementById('or-modal-close').addEventListener('click', closeOrModal);
    document.getElementById('or-modal-cancel').addEventListener('click', closeOrModal);
    orModalBackdrop.addEventListener('click', closeOrModal);

    function isModelSelected(modelId) {
        return modalSelectedModels.some(m => m.id === modelId);
    }

    function toggleModelSelection(modelId, modelName) {
        const idx = modalSelectedModels.findIndex(m => m.id === modelId);
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
        modalSelectedModels = modalSelectedModels.filter(m => m.id !== modelId);
        updateModalChips();
        // Re-render model list to update checkmarks
        if (modalModelsCache.length > 0) {
            const q = orModalSearch.value.trim().toLowerCase();
            const filtered = q
                ? modalModelsCache.filter(m => m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
                : modalModelsCache;
            renderModalModels(filtered);
        }
    }

    function updateModalChips() {
        orModalSelectedCount.textContent = modalSelectedModels.length + '/' + MAX_MODELS;
        orModalSaveBtn.disabled = modalSelectedModels.length === 0;

        if (modalSelectedModels.length > 0) {
            orModalSelected.classList.remove('hidden');
            let html = '';
            modalSelectedModels.forEach(function(m, i) {
                const label = i === 0 ? 'Primary' : 'Backup ' + i;
                const badgeColor = i === 0 ? 'bg-primary/10 text-primary border-primary/30' : 'bg-muted text-muted-foreground border-input';
                html += '<span class="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border ' + badgeColor + '">' +
                    '<span class="font-medium">' + label + ':</span> ' +
                    '<span class="max-w-[160px] truncate">' + escapeHtml(m.id.split('/').pop()) + '</span>' +
                    '<button type="button" class="or-chip-remove ml-0.5 hover:text-destructive" data-model-id="' + escapeHtml(m.id) + '" aria-label="Action">&times;</button>' +
                '</span>';
            });
            orModalSelectedChips.innerHTML = html; /* safe-html: all values escaped via escapeHtml() */
            orModalSelectedChips.querySelectorAll('.or-chip-remove').forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    removeModelFromSelection(this.dataset.modelId);
                });
            });
        } else {
            orModalSelected.classList.add('hidden');
            orModalSelectedChips.innerHTML = '';
        }
    }

    function buildMultiModelHTML(m) {
        const freeBadge = m.is_free
            ? '<span class="text-xs px-1.5 py-0.5 rounded bg-success/10 text-success border border-success/30">Free</span>'
            : '<span class="text-xs px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/30">Paid</span>';
        const ctx = m.context_length ? Math.round(m.context_length / 1024) + 'K ctx' : '';
        const selected = isModelSelected(m.id);
        const selIdx = modalSelectedModels.findIndex(x => x.id === m.id);
        const selectedClass = selected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-accent/50';
        const selBadge = selected
            ? '<span class="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/30">' + (selIdx === 0 ? 'Primary' : 'Backup ' + selIdx) + '</span>'
            : '';
        const atLimit = !selected && modalSelectedModels.length >= MAX_MODELS;
        const opacity = atLimit ? 'opacity-40 pointer-events-none' : '';
        return '<button type="button" class="or-model-btn w-full text-left p-2.5 rounded-md ' + selectedClass + ' ' + opacity + ' transition-colors flex items-center justify-between gap-2" data-model-id="' + escapeHtml(m.id) + '" data-model-name="' + escapeHtml(m.name) + '" aria-label="Action">' +
            '<div class="min-w-0">' +
                '<div class="text-sm font-medium truncate">' + escapeHtml(m.name) + '</div>' +
                '<div class="text-xs text-muted-foreground truncate">' + escapeHtml(m.id) + '</div>' +
            '</div>' +
            '<div class="flex items-center gap-1.5 shrink-0">' +
                selBadge +
                freeBadge +
                (ctx ? '<span class="text-xs text-muted-foreground">' + ctx + '</span>' : '') +
            '</div>' +
        '</button>';
    }

    function fetchModalModels() {
        const freeOnly = orModalFreeOnly.checked;
        const search = orModalSearch.value.trim();

        orModalModelsBody.innerHTML = '<div class="flex items-center justify-center py-8 text-muted-foreground text-sm"><span>Loading models...</span></div>'; /* safe-html */

        fetchModelsFromAPI(freeOnly, search)
            .then(data => {
                if (!data.success) {
                    orModalModelsBody.innerHTML = '<p class="text-sm text-destructive text-center py-8">' + DOMPurify.sanitize(data.error || 'Failed to load models') + '</p>';
                    return;
                }
                if (!data.models || !data.models.length) {
                    const hint = freeOnly ? ' Try unchecking "Free only".' : '';
                    orModalModelsBody.innerHTML = '<p class="text-sm text-muted-foreground text-center py-8">No models matched.' + hint + '</p>'; /* safe-html */
                    return;
                }
                modalModelsCache = data.models;
                renderModalModels(data.models);
            })
            .catch(err => {
                orModalModelsBody.innerHTML = '<p class="text-sm text-destructive text-center py-8">Request failed: ' + DOMPurify.sanitize(err.message) + '</p>';
            });
    }

    function renderModalModels(models) {
        let html = '<div class="space-y-1">';
        models.forEach(m => { html += buildMultiModelHTML(m); });
        html += '</div>';
        orModalModelsBody.innerHTML = html; /* safe-html: built from buildMultiModelHTML which uses escapeHtml() */

        orModalModelsBody.querySelectorAll('.or-model-btn').forEach(btn => {
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
        modalSearchTimeout = setTimeout(() => {
            if (modalModelsCache.length > 0) {
                const q = orModalSearch.value.trim().toLowerCase();
                const filtered = q
                    ? modalModelsCache.filter(m => m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
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

        const modelStr = modalSelectedModels.map(m => m.id).join(', ');
        Platform.fetch.post('/admin/api-settings/update-model', { provider: 'openrouter', model: modelStr }, { silent: true })
            .then(data => {
                if (data.success) {
                    closeOrModal();
                    location.reload();
                } else {
                    Platform.toast.error('Failed to save: ' + (data.error || 'Unknown error'));
                    orModalSaveBtn.disabled = false;
                    orModalSaveBtn.textContent = 'Save Models';
                }
            })
            .catch(err => {
                Platform.toast.error('Request failed. Please try again.');
                orModalSaveBtn.disabled = false;
                orModalSaveBtn.textContent = 'Save Models';
                // Re-throw to preserve error propagation
                throw err;
            });
    });

    // ===== Load from .env Modal =====
    const envModal = document.getElementById('env-modal');
    const envModalBackdrop = document.getElementById('env-modal-backdrop');
    const envKeysBody = document.getElementById('env-keys-body');
    const loadEnvBtn = document.getElementById('load-env-btn');

    function openEnvModal() {
        envModal.classList.remove('hidden');
        fetchEnvKeys();
    }

    function closeEnvModal() {
        envModal.classList.add('hidden');
    }

    loadEnvBtn.addEventListener('click', openEnvModal);
    document.getElementById('env-modal-close').addEventListener('click', closeEnvModal);
    document.getElementById('env-modal-cancel').addEventListener('click', closeEnvModal);
    envModalBackdrop.addEventListener('click', closeEnvModal);

    function fetchEnvKeys() {
        envKeysBody.innerHTML = '<div class="flex items-center justify-center py-8 text-muted-foreground"><span class="mr-2">Scanning...</span></div>'; /* safe-html */
        Platform.fetch.get('/admin/api-settings/env-keys', null, { silent: true })
            .then(data => {
                if (!data.success || data.keys.length === 0) {
                    envKeysBody.innerHTML = '<p class="text-sm text-muted-foreground text-center py-8">No API keys found in environment variables.</p>'; /* safe-html */
                    return;
                }
                let html = '<div class="space-y-2">';
                data.keys.forEach(k => {
                    const statusBadge = k.in_database
                        ? '<span class="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary">In DB</span>'
                        : '<span class="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning">New</span>';
                    html += `
                        <label class="flex items-center gap-3 p-3 rounded-md border hover:bg-accent/50 cursor-pointer transition-colors">
                            <input type="checkbox" class="env-key-cb h-4 w-4 rounded border-border" value="${escapeHtml(k.env_var)}" ${k.in_database ? '' : 'checked'} aria-label="Checkbox field">
                            <div class="flex-1 min-w-0">
                                <div class="flex items-center gap-2">
                                    <span class="font-medium text-sm">${escapeHtml(k.provider)}</span>
                                    ${statusBadge}
                                </div>
                                <div class="text-xs text-muted-foreground mt-0.5">
                                    ${escapeHtml(k.env_var)}: <code class="bg-muted px-1 rounded">${escapeHtml(k.masked_key)}</code>
                                </div>
                                ${k.default_model ? '<div class="text-xs text-muted-foreground">Default model: ' + escapeHtml(k.default_model) + '</div>' : ''}
                            </div>
                        </label>`;
                });
                html += '</div>';
                envKeysBody.innerHTML = html; /* safe-html: all values escaped via escapeHtml() */
            })
            .catch(() => {
                envKeysBody.innerHTML = '<p class="text-sm text-destructive text-center py-8">Failed to scan environment variables.</p>'; /* safe-html */
                // Re-throw to preserve error propagation
                throw new Error('Failed to scan environment variables');
            });
    }

    document.getElementById('env-import-btn').addEventListener('click', function() {
        const selected = Array.from(document.querySelectorAll('.env-key-cb:checked')).map(cb => cb.value);
        if (selected.length === 0) {
            Platform.toast.warning('Please select at least one key to import.');
            return;
        }
        const updateExisting = document.getElementById('env-update-existing').checked;

        Platform.fetch.post('/admin/api-settings/load-env', { keys: selected, update_existing: updateExisting }, { silent: true })
            .then(data => {
                if (data.success) {
                    closeEnvModal();
                    location.reload();
                } else {
                    Platform.toast.error('Import failed: ' + (data.error || 'Unknown error'));
                }
            })
            .catch(() => {
                Platform.toast.error('Import request failed. Please try again.');
                // Re-throw to preserve error propagation
                throw new Error('Import request failed');
            });
    });
