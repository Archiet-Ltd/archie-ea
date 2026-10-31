/* EA workflows dashboard: the start dialog's field renderers. They read PHASE_FIELDS and FORM_DATA_SOURCES, which the page defines. */
/* ── Field renderer helpers ── */
const _FC = 'h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
const _FT = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

function _label(field) {
    if (!field.label) return '';
    const req = field.required ? ' <span class="text-destructive-emphasis">*</span>' : ' <span class="text-muted-foreground font-normal">(optional)</span>';
    return '<label for="field_' + field.name + '" class="block text-sm font-medium mb-2">' + field.label + req + '</label>';
}

function _help(field) {
    return field.help ? '<p class="text-xs text-muted-foreground mt-1">' + field.help + '</p>' : '';
}

function _renderText(field) {
    let html = _label(field);
    html += '<input type="text" id="field_' + field.name + '" name="' + field.name + '"'
        + ' class="' + _FC + '"'
        + ' placeholder="' + (field.placeholder || '') + '"'
        + (field.required ? ' required' : '')
        + ' maxlength="' + (field.maxlength || 200) + '">';
    html += _help(field);
    return html;
}

function _renderTextarea(field) {
    let html = _label(field);
    if (field.fileUpload) {
        html += '<div class="flex gap-2 mb-2">'
            + '<label class="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-input hover:border-primary/50 cursor-pointer text-sm transition-colors">'
            + '<i data-lucide="upload" class="h-4 w-4" aria-hidden="true"></i>'
            + '<span>Upload document</span>'
            + '<input type="file" id="field_' + field.name + '_file" accept=".txt,.md" class="sr-only">'
            + '</label>'
            + '<button type="button" id="field_' + field.name + '_clear" class="hidden px-3 py-2 rounded-md border border-input hover:bg-muted text-sm">Clear</button>'
            + '</div>';
    }
    html += '<textarea id="field_' + field.name + '" name="' + field.name + '"'
        + ' rows="' + (field.rows || 3) + '"'
        + ' class="' + _FT + '"'
        + ' placeholder="' + (field.placeholder || '') + '"'
        + (field.required ? ' required' : '')
        + '></textarea>';
    html += _help(field);
    return html;
}

function _renderSelect(field) {
    let html = _label(field);
    html += '<select id="field_' + field.name + '" name="' + field.name + '" class="' + _FC + '"'
        + (field.required ? ' required' : '') + '>';
    if (field.source) {
        html += '<option value="">-- select --</option>';
        const items = FORM_DATA_SOURCES[field.source] || [];
        items.forEach(function(item) {
            html += '<option value="' + item.id + '">' + (item.name || item.label) + '</option>';
        });
    } else if (field.options) {
        field.options.forEach(function(opt) {
            html += '<option value="' + opt.value + '">' + opt.label + '</option>';
        });
    }
    html += '</select>';
    html += _help(field);
    return html;
}

function _renderMultiselect(field) {
    let html = _label(field);
    html += '<div class="grid grid-cols-2 gap-2" id="field_' + field.name + '">';
    (field.options || []).forEach(function(opt) {
        html += '<label class="inline-flex items-center gap-2 p-2 rounded-md border border-input hover:border-primary/30 cursor-pointer text-sm transition-colors">'
            + '<input type="checkbox" name="' + field.name + '" value="' + opt.value + '" class="rounded border-input text-primary focus:ring-primary">'
            + '<span>' + opt.label + '</span>'
            + '</label>';
    });
    html += '</div>';
    html += _help(field);
    return html;
}

function _renderRadio(field) {
    let html = _label(field);
    html += '<div class="flex flex-wrap gap-2" id="field_' + field.name + '">';
    (field.options || []).forEach(function(opt, idx) {
        html += '<label class="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-input hover:border-primary/30 cursor-pointer text-sm transition-colors">'
            + '<input type="radio" name="' + field.name + '" value="' + opt.value + '"'
            + (idx === 0 && field.required ? ' required' : '')
            + ' class="text-primary focus:ring-primary">'
            + '<span>' + opt.label + '</span>'
            + '</label>';
    });
    html += '</div>';
    html += _help(field);
    return html;
}

function _renderNumber(field) {
    let html = _label(field);
    html += '<input type="number" id="field_' + field.name + '" name="' + field.name + '"'
        + ' class="' + _FC + '"'
        + ' placeholder="' + (field.placeholder || '') + '"'
        + (field.min != null ? ' min="' + field.min + '"' : '')
        + (field.max != null ? ' max="' + field.max + '"' : '')
        + (field.required ? ' required' : '') + '>';
    html += _help(field);
    return html;
}

function _renderInfo(field) {
    return '<p class="text-sm text-muted-foreground rounded-md border border-input p-3 bg-muted/30">'
        + field.text + '</p>';
}

const FIELD_RENDERERS = {
    text: _renderText,
    textarea: _renderTextarea,
    select: _renderSelect,
    multiselect: _renderMultiselect,
    radio: _renderRadio,
    number: _renderNumber,
    info: _renderInfo
};

function renderFormFields(workflowCode) {
    const container = document.getElementById('dynamicFields');
    const fields = PHASE_FIELDS[workflowCode];
    if (!fields || fields.length === 0) {
        container.innerHTML = '<p class="text-sm text-muted-foreground">No additional configuration needed.</p>';
        return;
    }
    let html = '';
    fields.forEach(function(field) {
        const renderer = FIELD_RENDERERS[field.type];
        if (renderer) {
            html += '<div>' + renderer(field) + '</div>';
        }
    });
    container.innerHTML = html;

    // Wire up file upload if any textarea has fileUpload
    fields.forEach(function(field) {
        if (field.fileUpload) {
            _wireFileUpload(field.name);
        }
    });

    if (window.lucide) window.lucide.createIcons();
}

function _wireFileUpload(fieldName) {
    const fileInput = document.getElementById('field_' + fieldName + '_file');
    const textarea = document.getElementById('field_' + fieldName);
    const clearBtn = document.getElementById('field_' + fieldName + '_clear');
    if (!fileInput || !textarea) return;
    fileInput.addEventListener('change', function() {
        const file = this.files && this.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function() {
            textarea.value = reader.result || '';
            if (clearBtn) clearBtn.classList.remove('hidden');
        };
        reader.readAsText(file, 'UTF-8');
    });
    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            fileInput.value = '';
            textarea.value = '';
            clearBtn.classList.add('hidden');
        });
    }
}
