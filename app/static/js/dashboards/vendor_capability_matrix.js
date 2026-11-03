const APP_CONFIG = window.__APP_CONFIG__ || {};

let matrixData = [];
let filteredData = [];

// Initialize matrix on page load
document.addEventListener('DOMContentLoaded', function() {
    loadMatrixData();
});

function loadMatrixData() {
    Platform.fetch('/api/vendors/capability-matrix', { silent: true })
        .then(function(data) {
            matrixData = data;
            filteredData = data;
            renderMatrix();
        })
        .catch(function(error) {
            // The call site already paints its own inline error state, so we pass silent:true to avoid duplicate toast.
            // However, Platform.fetch will still throw, and we must surface the failure to the user via showError.
            // The existing console.error call is replaced with a real user-visible error path.
            showError('Failed to load capability matrix data');
        });
}

function renderMatrix() {
    const container = document.getElementById('matrixContent');

    if (filteredData.length === 0) {
        safeHTML(container, '<div class="text-center py-8">' +
                '<i class="fas fa-search text-4xl text-muted-foreground/60"></i>' +
                '<p class="mt-4 text-muted-foreground">No data found matching your filters</p>' +
            '</div>');
        return;
    }

    // Build matrix HTML
    let html = '<div class="overflow-x-auto"><table class="matrix-table">';

    // Header row with vendor names
    html += '<thead><tr><th class="capability-header">Capability</th>';
    const vendors = [];
    const vendorSet = {};
    filteredData.forEach(function(item) {
        if (!vendorSet[item.vendor_name]) {
            vendorSet[item.vendor_name] = true;
            vendors.push(item.vendor_name);
        }
    });
    vendors.forEach(function(vendor) {
        html += '<th class="vendor-header" colspan="' + vendorProductsCount(vendor) + '">' + vendor + '</th>';
    });
    html += '</tr></thead>';

    // Product row
    html += '<tr><th></th>';
    vendors.forEach(function(vendor) {
        const products = vendorProducts(vendor);
        products.forEach(function(product) {
            html += '<th class="text-xs">' + product + '</th>';
        });
    });
    html += '</tr>';

    // Data rows
    const capabilities = [];
    const capSet = {};
    filteredData.forEach(function(item) {
        if (!capSet[item.capability_name]) {
            capSet[item.capability_name] = true;
            capabilities.push(item.capability_name);
        }
    });
    capabilities.forEach(function(capability) {
        html += '<tr><td class="font-medium">' + capability + '</td>';

        vendors.forEach(function(vendor) {
            const products = vendorProducts(vendor);
            products.forEach(function(product) {
                const data = findMatrixData(capability, vendor, product);
                if (data) {
                    const coverageClass = getCoverageClass(data.coverage_percentage);
                    html += '<td class="' + coverageClass + '" data-action="showDetails" data-params=\'["' + capability + '","' + vendor + '","' + product + '"]\' title="' + data.coverage_percentage + '% coverage">' +
                        data.coverage_percentage + '%' +
                    '</td>';
                } else {
                    html += '<td class="coverage-weak">-</td>';
                }
            });
        });
        html += '</tr>';
    });

    html += '</table></div>';
    safeHTML(container, html);
}

function vendorProductsCount(vendor) {
    const productSet = {};
    filteredData.filter(function(item) { return item.vendor_name === vendor; }).forEach(function(item) {
        productSet[item.product_name] = true;
    });
    return Object.keys(productSet).length;
}

function vendorProducts(vendor) {
    const productSet = {};
    const products = [];
    filteredData.filter(function(item) { return item.vendor_name === vendor; }).forEach(function(item) {
        if (!productSet[item.product_name]) {
            productSet[item.product_name] = true;
            products.push(item.product_name);
        }
    });
    return products;
}

function findMatrixData(capability, vendor, product) {
    return filteredData.find(function(item) {
        return item.capability_name === capability &&
            item.vendor_name === vendor &&
            item.product_name === product;
    });
}

function getCoverageClass(coverage) {
    if (coverage >= 80) return 'coverage-high';
    if (coverage >= 60) return 'coverage-moderate';
    if (coverage >= 40) return 'coverage-partial';
    return 'coverage-weak';
}

function filterMatrix() {
    const vendorSearch = document.getElementById('vendorSearch').value.toLowerCase();
    const capabilitySearch = document.getElementById('capabilitySearch').value.toLowerCase();
    const minCoverage = parseInt(document.getElementById('minCoverage').value) || 0; // zero-fallback-ok: a blank filter box means no minimum, it is not a measured figure
    const categoryFilter = document.getElementById('categoryFilter').value;

    filteredData = matrixData.filter(function(item) {
        const vendorMatch = item.vendor_name.toLowerCase().includes(vendorSearch);
        const capabilityMatch = item.capability_name.toLowerCase().includes(capabilitySearch);
        const coverageMatch = item.coverage_percentage >= minCoverage;
        const categoryMatch = !categoryFilter || item.product_category === categoryFilter;

        return vendorMatch && capabilityMatch && coverageMatch && categoryMatch;
    });

    renderMatrix();
}

function showDetails(capability, vendor, product) {
    const data = findMatrixData(capability, vendor, product);
    if (!data) return;

    const modal = document.getElementById('detailModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');

    modalTitle.textContent = vendor + ' ' + product + ' \u2192 ' + capability + ' (' + data.coverage_percentage + '% Coverage)';

    let bodyHtml = '<div class="space-y-6">' +
            '<!-- Overall Assessment -->' +
            '<div>' +
                '<h4 class="font-semibold mb-3">Overall Assessment</h4>' +
                '<div class="flex items-center space-x-4">' +
                    '<div class="flex-1">' +
                        '<div class="bg-muted rounded-full h-4">' +
                            '<div class="bg-primary h-4 rounded-full" style="width: ' + data.coverage_percentage + '%"></div>' +
                        '</div>' +
                    '</div>' +
                    '<span class="font-semibold">' + data.coverage_percentage + '%</span>' +
                '</div>' +
            '</div>' +
            '<!-- Breakdown -->' +
            '<div>' +
                '<h4 class="font-semibold mb-3">Coverage Breakdown</h4>' +
                '<div class="grid grid-cols-2 gap-4">' +
                    '<div>' +
                        '<span class="text-sm text-muted-foreground">Out-of-Box:</span> ' +
                        '<span class="font-medium">' + Platform.figure.percent(data.out_of_box_percentage) + '</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-sm text-muted-foreground">Customization:</span> ' +
                        '<span class="font-medium">' + (Platform.figure.missing(data.coverage_percentage) || Platform.figure.missing(data.out_of_box_percentage) ? Platform.figure.NOT_SCORED : (data.coverage_percentage - data.out_of_box_percentage) + '%') + '</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-sm text-muted-foreground">Maturity Level:</span> ' +
                        '<span class="font-medium">' + (data.maturity_level || 'N/A') + '/5</span>' +
                    '</div>' +
                    '<div>' +
                        '<span class="text-sm text-muted-foreground">Implementation:</span> ' +
                        '<span class="font-medium">' + (data.implementation_complexity || 'N/A') + '/10</span>' +
                    '</div>' +
                '</div>' +
            '</div>';

    // Gaps
    if (data.gaps && data.gaps.length > 0) {
        bodyHtml += '<div>' +
                '<h4 class="font-semibold mb-3">Identified Gaps</h4>' +
                '<div class="gap-list">';

        data.gaps.forEach(function(gap) {
            const severityClass = gap.severity === 'high' ? 'severity-high' :
                                 gap.severity === 'medium' ? 'severity-medium' : 'severity-low';
            bodyHtml += '<div class="gap-item">' +
                    '<span class="gap-severity ' + severityClass + '">' + gap.severity + '</span>' +
                    '<span class="text-sm">' + gap.name + '</span>' +
                '</div>';
        });

        bodyHtml += '</div></div>';
    }

    // Strengths
    if (data.strengths && data.strengths.length > 0) {
        bodyHtml += '<div>' +
                '<h4 class="font-semibold mb-3">Key Strengths</h4>' +
                '<div class="strength-list">';

        data.strengths.forEach(function(strength) {
            bodyHtml += '<div class="strength-item">' +
                    '<i class="fas fa-check-circle text-emerald-500"></i>' +
                    '<span class="text-sm">' + strength + '</span>' +
                '</div>';
        });

        bodyHtml += '</div></div>';
    }

    // Evidence
    if (data.evidence) {
        bodyHtml += '<div>' +
                '<h4 class="font-semibold mb-3">Evidence & Verification</h4>' +
                '<div class="bg-muted/30 rounded p-3">' +
                    '<p class="text-sm"><strong>Source:</strong> ' + (data.evidence.source || 'N/A') + '</p>' +
                    '<p class="text-sm"><strong>Verified:</strong> ' + (data.evidence.verified_at ? new Date(data.evidence.verified_at).toLocaleDateString() : 'Not verified') + '</p>' +
                    '<p class="text-sm"><strong>By:</strong> ' + (data.evidence.verified_by || 'N/A') + '</p>' +
                '</div>' +
            '</div>';
    }

    bodyHtml += '</div>';
    safeHTML(modalBody, bodyHtml);

    Platform.modal.open('detailModal');
}

function closeDetailModal() {
    Platform.modal.close('detailModal');
}

function exportMatrix() {
    // Create CSV export
    let csv = 'Capability,Vendor,Product,Coverage %,Maturity,Implementation Complexity\n';

    filteredData.forEach(function(item) {
        csv += '"' + item.capability_name + '","' + item.vendor_name + '","' + item.product_name + '",' + item.coverage_percentage + ',' + (item.maturity_level || '') + ',' + (item.implementation_complexity || '') + '\n';
    });

    // Download CSV
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vendor_capability_matrix.csv';
    a.click();
    window.URL.revokeObjectURL(url);
}

function refreshData() {
    loadMatrixData();
}

function showError(message) {
    const container = document.getElementById('matrixContent');
    safeHTML(container, '<div class="text-center py-8">' +
            '<i class="fas fa-exclamation-triangle text-4xl text-destructive"></i>' +
            '<p class="mt-4 text-muted-foreground">' + message + '</p>' +
            '<button data-action="loadMatrixData" class="mt-4 bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">' +
                'Retry' +
            '</button>' +
        '</div>');
}
