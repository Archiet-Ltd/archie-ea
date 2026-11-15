/**
 * Review Queue Panel - External JavaScript
 * Extracted from app/templates/components/review_queue_panel.html
 * Uses window.__APP_CONFIG__ bridge for server-side values
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

let reviewQueueItems = [];
let selectedReviewItems = new Set();

function openReviewQueue(previewData) {
    Platform.modal.open('review-queue-panel');
    loadReviewQueue(previewData);
}

function closeReviewQueue() {
    Platform.modal.close('review-queue-panel');
    selectedReviewItems.clear();
}

function loadReviewQueue(previewData) {
    // If preview data provided, use it; otherwise fetch from API
    if (previewData && previewData.applications) {
        reviewQueueItems = buildReviewItemsFromPreview(previewData);
        renderReviewQueue(reviewQueueItems);
        updateReviewCounts();
        return Promise.resolve();
    } else {
        // Platform.fetch throws on non-ok responses, and returns parsed data directly.
        // We catch to paint an inline error state, so we pass silent:true to avoid duplicate toast.
        return Platform.fetch('/api/review-queue', { silent: true })
            .then(function(data) {
                // The API returns { success: true, items: [...] } on success.
                if (data.success) {
                    reviewQueueItems = data.items || [];
                    renderReviewQueue(reviewQueueItems);
                } else {
                    safeHTML(document.getElementById('review-queue-content'),
                        '<div class="text-destructive text-center py-8">Failed to load review queue: ' + (data.error || 'unknown error') + '</div>');
                }
                updateReviewCounts();
            })
            .catch(function(error) {
                // Paint inline error state; error.message is already user‑readable.
                safeHTML(document.getElementById('review-queue-content'),
                    '<div class="text-destructive text-center py-8">Failed to load review queue: ' + error.message + '</div>');
                // Re‑throw to preserve existing error propagation.
                throw error;
            });
    }
}

function buildReviewItemsFromPreview(previewData) {
    let items = [];
    let itemId = 1;
    let LOW_CONFIDENCE_THRESHOLD = 0.70;

    if (!previewData.applications || previewData.applications.length === 0) {
        return items;
    }

    let totalMappings = 0;
    let lowConfidenceMappings = 0;

    previewData.applications.forEach(function(app) {

        // Capability mappings - only include low confidence
        if (app.capabilities) {
            app.capabilities.forEach(function(cap) {
                totalMappings++;
                let confidence = cap.confidence_score ?? null;
                if (confidence === null || confidence < LOW_CONFIDENCE_THRESHOLD) {
                    lowConfidenceMappings++;
                    items.push({
                        id: itemId++,
                        type: 'capability',
                        application_name: app.name,
                        item_name: cap.capability_name || 'Unknown Capability',
                        confidence_score: confidence,
                        rationale: Array.isArray(cap.rationale) ? cap.rationale.join('; ') : (cap.rationale || 'AI-generated mapping'),
                        status: 'pending',
                        metadata: cap
                    });
                }
            });
        }

        // Process mappings - only include low confidence
        if (app.processes) {
            app.processes.forEach(function(proc) {
                totalMappings++;
                let confidence = proc.similarity_score ?? null;
                if (confidence === null || confidence < LOW_CONFIDENCE_THRESHOLD) {
                    lowConfidenceMappings++;
                    items.push({
                        id: itemId++,
                        type: 'process',
                        application_name: app.name,
                        item_name: proc.process_code + ' - ' + proc.process_name,
                        confidence_score: confidence,
                        rationale: proc.match_rationale || 'Semantic similarity match',
                        status: 'pending',
                        metadata: proc
                    });
                }
            });
        }

        // ArchiMate elements - only include low confidence
        if (app.archimate) {
            app.archimate.forEach(function(elem) {
                totalMappings++;
                let confidence = elem.confidence || 0.75;
                if (confidence < LOW_CONFIDENCE_THRESHOLD) {
                    lowConfidenceMappings++;
                    items.push({
                        id: itemId++,
                        type: 'archimate',
                        application_name: app.name,
                        item_name: elem.type + ': ' + elem.name,
                        confidence_score: confidence,
                        rationale: 'Generated ' + elem.type + ' in ' + elem.layer + ' layer',
                        status: 'pending',
                        metadata: elem
                    });
                }
            });
        }
    });

    return items;
}

function renderReviewQueue(items) {
    if (!items || items.length === 0) {
        safeHTML(document.getElementById('review-queue-content'),
            '<div class="text-muted-foreground text-center py-12">No items pending review</div>');
        return;
    }

    let html = items.map(function(item) {
        let confidencePercent = Platform.figure.missing(item.confidence_score) ? null : Math.round(item.confidence_score * 100);
        let confidenceColor = confidencePercent >= 80 ? 'text-emerald-600' :
                             confidencePercent >= 60 ? 'text-amber-600' : 'text-destructive';
        let confidenceBg = confidencePercent >= 80 ? 'bg-emerald-500/5' :
                          confidencePercent >= 60 ? 'bg-amber-500/5' : 'bg-destructive/5';

        let typeIcons = {
            capability: '&#127919;',
            process: '&#9881;&#65039;',
            archimate: '&#127959;&#65039;',
            vendor: '&#127970;'
        };

        let typeLabels = {
            capability: 'Capability Mapping',
            process: 'APQC Process',
            archimate: 'ArchiMate Element',
            vendor: 'Vendor Product'
        };

        let barColorClass = confidencePercent >= 80 ? 'bg-emerald-600' : (confidencePercent >= 60 ? 'bg-amber-600' : 'bg-destructive');

        return '<div class="p-4 hover:bg-muted/30 transition-colors" data-item-id="' + item.id + '" data-type="' + item.type + '" data-confidence="' + confidencePercent + '">' +
            '<div class="flex items-start gap-4">' +
                '<input type="checkbox"' +
                       ' class="mt-1 w-5 h-5 text-primary border-border rounded focus:ring-primary"' +
                       ' data-rqp-toggle="' + Number(item.id) + '"' +
                       ' aria-label="Select this mapping for bulk approval">' +
                '<div class="flex-1">' +
                    '<div class="flex items-center gap-3 mb-2">' +
                        '<span class="text-2xl">' + (typeIcons[item.type] || '') + '</span>' +
                        '<span class="px-2 py-1 bg-primary/10 text-primary/90 text-xs font-semibold rounded">' +
                            (typeLabels[item.type] || '') +
                        '</span>' +
                        '<span class="text-sm font-medium text-foreground">' + item.application_name + '</span>' +
                    '</div>' +
                    '<h4 class="text-lg font-semibold text-foreground mb-1">' + item.item_name + '</h4>' +
                    '<p class="text-sm text-muted-foreground mb-3">' + item.rationale + '</p>' +
                    '<div class="flex items-center gap-4">' +
                        '<div class="' + confidenceBg + ' px-3 py-1 rounded-lg">' +
                            '<span class="text-xs font-medium text-foreground">Confidence:</span>' +
                            '<span class="' + confidenceColor + ' font-bold ml-2">' + confidencePercent + '%</span>' +
                        '</div>' +
                        '<div class="flex-1 bg-muted rounded-full h-2 max-w-xs">' +
                            '<div class="h-2 rounded-full ' + barColorClass + '"' +
                                 ' style="width: ' + confidencePercent + '%"></div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="flex gap-2">' +
                    '<button type="button" data-rqp-action="approve" data-rqp-id="' + Number(item.id) + '"' +
                            ' class="px-4 py-2 bg-emerald-600 text-primary-foreground rounded-lg hover:bg-emerald-700 text-sm font-medium">' +
                        '&#10003; Approve' +
                    '</button>' +
                    '<button type="button" data-rqp-action="reject" data-rqp-id="' + Number(item.id) + '"' +
                            ' class="px-4 py-2 bg-destructive text-primary-foreground rounded-lg hover:bg-destructive/90 text-sm font-medium">' +
                        '&#10007; Reject' +
                    '</button>' +
                '</div>' +
            '</div>' +
        '</div>';
    }).join('');

    safeHTML(document.getElementById('review-queue-content'), html);
}

// Delegated listeners -- the CSP (no 'unsafe-inline') refuses on*= attributes
// however they reach the DOM, so the per-item checkbox and the Approve/Reject
// buttons above never fired. Bound once at document level so they survive
// renderReviewQueue() rebuilding the list.
document.addEventListener('change', function (event) {
    var box = event.target.closest('[data-rqp-toggle]');
    if (!box) return;
    toggleReviewItem(Number(box.getAttribute('data-rqp-toggle')), box.checked);
});

document.addEventListener('click', function (event) {
    var el = event.target.closest('#review-queue-content [data-rqp-action]');
    if (!el) return;
    var itemId = Number(el.getAttribute('data-rqp-id'));
    if (el.getAttribute('data-rqp-action') === 'approve') approveReviewItem(itemId);
    else if (el.getAttribute('data-rqp-action') === 'reject') rejectReviewItem(itemId);
});

function toggleReviewItem(itemId, checked) {
    if (checked) {
        selectedReviewItems.add(itemId);
    } else {
        selectedReviewItems.delete(itemId);
    }
    updateReviewCounts();
    updateSelectAllCheckbox();
}

function toggleSelectAll(checked) {
    let checkboxes = document.querySelectorAll('#review-queue-content input[type="checkbox"]');
    checkboxes.forEach(function(cb) {
        let itemDiv = cb.closest('[data-item-id]');
        if (itemDiv && itemDiv.style.display !== 'none') {
            cb.checked = checked;
            let itemId = parseInt(itemDiv.dataset.itemId);
            if (checked) {
                selectedReviewItems.add(itemId);
            } else {
                selectedReviewItems.delete(itemId);
            }
        }
    });
    updateReviewCounts();
}

function selectHighConfidence() {
    let checkboxes = document.querySelectorAll('#review-queue-content input[type="checkbox"]');
    checkboxes.forEach(function(cb) {
        let itemDiv = cb.closest('[data-item-id]');
        if (itemDiv && itemDiv.style.display !== 'none') {
            let confidence = parseInt(itemDiv.dataset.confidence);
            if (confidence >= 70) {
                cb.checked = true;
                let itemId = parseInt(itemDiv.dataset.itemId);
                selectedReviewItems.add(itemId);
            }
        }
    });
    updateReviewCounts();
    updateSelectAllCheckbox();
}

function updateSelectAllCheckbox() {
    let allCheckboxes = document.querySelectorAll('#review-queue-content input[type="checkbox"]');
    let visibleCheckboxes = Array.from(allCheckboxes).filter(function(cb) {
        let itemDiv = cb.closest('[data-item-id]');
        return itemDiv && itemDiv.style.display !== 'none';
    });
    let allChecked = visibleCheckboxes.length > 0 && visibleCheckboxes.every(function(cb) { return cb.checked; });
    let selectAllCb = document.getElementById('select-all-checkbox');
    if (selectAllCb) {
        selectAllCb.checked = allChecked;
    }
}

function updateReviewCounts() {
    let pending = reviewQueueItems.filter(function(i) { return i.status === 'pending'; });
    document.getElementById('review-pending-count').textContent = pending.length;
    document.getElementById('review-selected-count').textContent = selectedReviewItems.size;

    // Calculate breakdown by type
    let breakdown = {
        capability: pending.filter(function(i) { return i.type === 'capability'; }).length,
        process: pending.filter(function(i) { return i.type === 'process'; }).length,
        archimate: pending.filter(function(i) { return i.type === 'archimate'; }).length,
        vendor: pending.filter(function(i) { return i.type === 'vendor'; }).length
    };

    // Build breakdown text
    let parts = [];
    if (breakdown.capability > 0) parts.push(breakdown.capability + ' Capabilities');
    if (breakdown.process > 0) parts.push(breakdown.process + ' Processes');
    if (breakdown.archimate > 0) parts.push(breakdown.archimate + ' ArchiMate');
    if (breakdown.vendor > 0) parts.push(breakdown.vendor + ' Vendor');

    let breakdownEl = document.getElementById('review-breakdown');
    if (breakdownEl && parts.length > 0) {
        breakdownEl.textContent = '(' + parts.join(' \u2022 ') + ')';
    }
}

function approveReviewItem(itemId) {
    let item = reviewQueueItems.find(function(i) { return i.id === itemId; });
    if (!item) return;

    item.status = 'approved';

    let element = document.querySelector('[data-item-id="' + itemId + '"]');
    if (element) element.remove();

    updateReviewCounts();
    return Promise.resolve();
}

function rejectReviewItem(itemId) {
    let item = reviewQueueItems.find(function(i) { return i.id === itemId; });
    if (!item) return;

    item.status = 'rejected';

    let element = document.querySelector('[data-item-id="' + itemId + '"]');
    if (element) element.remove();

    updateReviewCounts();
    return Promise.resolve();
}

function bulkApproveSelected() {
    if (selectedReviewItems.size === 0) {
        Platform.toast.warning('Please select items to approve');
        return Promise.resolve();
    }

    let chain = Promise.resolve();
    selectedReviewItems.forEach(function(itemId) {
        chain = chain.then(function() {
            return approveReviewItem(itemId);
        });
    });

    return chain.then(function() {
        selectedReviewItems.clear();
        updateReviewCounts();
    });
}

function bulkRejectSelected() {
    if (selectedReviewItems.size === 0) {
        Platform.toast.warning('Please select items to reject');
        return Promise.resolve();
    }

    let chain = Promise.resolve();
    selectedReviewItems.forEach(function(itemId) {
        chain = chain.then(function() {
            return rejectReviewItem(itemId);
        });
    });

    return chain.then(function() {
        selectedReviewItems.clear();
        updateReviewCounts();
    });
}

function filterReviewQueue(type) {
    let items = document.querySelectorAll('[data-item-id]');
    items.forEach(function(item) {
        if (!type || item.dataset.type === type) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });
    updateSelectAllCheckbox();
}

function filterByConfidence(level) {
    let items = document.querySelectorAll('[data-item-id]');
    items.forEach(function(item) {
        let confidence = parseInt(item.dataset.confidence);
        let show = true;

        if (level === 'high') show = confidence >= 80;
        else if (level === 'medium') show = confidence >= 60 && confidence < 80;
        else if (level === 'low') show = confidence < 60;

        item.style.display = show ? '' : 'none';
    });
    updateSelectAllCheckbox();
}

function confirmReviewedMappings() {
    let approved = reviewQueueItems.filter(function(i) { return i.status === 'approved'; });

    if (approved.length === 0) {
        Platform.toast.warning('No mappings approved. Please review and approve items before confirming.');
        return;
    }

    if (window.onReviewConfirmed) {
        window.onReviewConfirmed(approved);
    }

    closeReviewQueue();
}
