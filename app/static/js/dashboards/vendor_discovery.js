/**
 * Vendor Discovery Engine
 * Extracted from dashboards/vendor_discovery.html
 */
const APP_CONFIG = window.__APP_CONFIG__ || {};

let availableCapabilities = [];
let capabilityRequirements = [];
let discoveryResults = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    loadCapabilities();
    addCapabilityRequirement(); // Add one default requirement
});

function loadCapabilities() {
    Platform.fetch.get('/api/vendor-discovery/capabilities', null, { silent: true })
        .then(function(data) {
            if (data.success) {
                availableCapabilities = data.capabilities;
                populateCapabilityList();
            } else {
                showError(data.error || 'Failed to load capabilities');
            }
        })
        .catch(function(error) {
            showError('Failed to load capabilities');
        });
}

function populateCapabilityList() {
    const capabilityList = document.getElementById('capabilityList');
    safeHTML(capabilityList, '');

    availableCapabilities.forEach(function(cap) {
        const item = document.createElement('div');
        item.className = 'p-3 border rounded-lg hover:bg-muted/30 cursor-pointer';
        item.onclick = function() { selectCapability(cap); };
        safeHTML(item,
            '<div class="font-medium">' + cap.name + '</div>' +
            '<div class="text-sm text-muted-foreground">' + cap.business_domain + ' - Level ' + cap.level + '</div>' +
            '<div class="text-xs text-muted-foreground mt-1">' + (cap.description || 'No description') + '</div>');
        capabilityList.appendChild(item);
    });
}

function filterCapabilities() {
    const searchTerm = document.getElementById('capabilitySearch').value.toLowerCase();
    const filtered = availableCapabilities.filter(function(cap) {
        return cap.name.toLowerCase().includes(searchTerm) ||
            (cap.business_domain || '').toLowerCase().includes(searchTerm);
    });

    const capabilityList = document.getElementById('capabilityList');
    safeHTML(capabilityList, '');

    filtered.forEach(function(cap) {
        const item = document.createElement('div');
        item.className = 'p-3 border rounded-lg hover:bg-muted/30 cursor-pointer';
        item.onclick = function() { selectCapability(cap); };
        safeHTML(item,
            '<div class="font-medium">' + cap.name + '</div>' +
            '<div class="text-sm text-muted-foreground">' + cap.business_domain + ' - Level ' + cap.level + '</div>' +
            '<div class="text-xs text-muted-foreground mt-1">' + (cap.description || 'No description') + '</div>');
        capabilityList.appendChild(item);
    });
}

function selectCapability(capability) {
    const currentRequirement = capabilityRequirements[capabilityRequirements.length - 1];
    if (currentRequirement) {
        currentRequirement.capability = capability;
        currentRequirement.capability_id = capability.id;
        currentRequirement.capability_name = capability.name;
        updateRequirementDisplay();
    }
    closeCapabilityModal();
}

function addCapabilityRequirement() {
    const requirement = {
        id: Date.now(),
        capability: null,
        capability_id: null,
        capability_name: '',
        min_coverage: 70,
        importance: 'medium'
    };

    capabilityRequirements.push(requirement);
    updateRequirementDisplay();
}

function updateRequirementDisplay() {
    const container = document.getElementById('capabilityRequirements');
    safeHTML(container, '');

    capabilityRequirements.forEach(function(req, index) {
        const item = document.createElement('div');
        item.className = 'requirement-item';
        safeHTML(item,
            '<div class="requirement-header">' +
                '<div class="flex items-center space-x-4">' +
                    '<div class="flex-1">' +
                        (req.capability_name ?
                            '<span class="font-medium">' + req.capability_name + '</span>' :
                            '<button data-action="showCapabilityModal" data-id="' + index + '" class="text-primary hover:text-primary/90">' +
                                '<i class="fas fa-plus mr-2"></i>Select Capability' +
                            '</button>'
                        ) +
                    '</div>' +
                    '<button data-action="removeRequirement" data-id="' + index + '" class="text-destructive hover:text-red-800">' +
                        '<i class="fas fa-trash"></i>' +
                    '</button>' +
                '</div>' +
            '</div>' +
            (req.capability_name ?
                '<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mt-3">' +
                    '<div>' +
                        '<label class="text-sm font-medium text-foreground">Min Coverage (%)</label>' +
                        '<input type="number" class="filter-input" value="' + req.min_coverage + '"' +
                               ' min="0" max="100" data-requirement-index="' + index + '" data-requirement-field="min_coverage">' +
                    '</div>' +
                    '<div>' +
                        '<label class="text-sm font-medium text-foreground">Importance</label>' +
                        '<select class="filter-input" data-requirement-index="' + index + '" data-requirement-field="importance">' +
                            '<option value="low"' + (req.importance === 'low' ? ' selected' : '') + '>Low</option>' +
                            '<option value="medium"' + (req.importance === 'medium' ? ' selected' : '') + '>Medium</option>' +
                            '<option value="high"' + (req.importance === 'high' ? ' selected' : '') + '>High</option>' +
                        '</select>' +
                    '</div>' +
                '</div>'
            : ''));
        container.appendChild(item);
    });
}

function updateRequirement(index, field, value) {
    if (field === 'min_coverage') {
        capabilityRequirements[index][field] = parseInt(value);
    } else {
        capabilityRequirements[index][field] = value;
    }
}

function removeRequirement(index) {
    capabilityRequirements.splice(index, 1);
    updateRequirementDisplay();
}

function showCapabilityModal(requirementIndex) {
    Platform.modal.open('capabilityModal');
    document.getElementById('capabilitySearch').value = '';
    filterCapabilities();
}

function closeCapabilityModal() {
    Platform.modal.close('capabilityModal');
}

function clearRequirements() {
    capabilityRequirements = [];
    updateRequirementDisplay();
    addCapabilityRequirement();
}

function runDiscovery() {
    // Validate requirements
    const validRequirements = capabilityRequirements.filter(function(req) { return req.capability_id; });

    if (validRequirements.length === 0) {
        showError('Please add at least one capability requirement');
        return;
    }

    // Show loading state
    document.getElementById('loadingState').classList.remove('hidden');
    document.getElementById('discoveryResults').classList.add('hidden');

    // Prepare request data
    const requestData = {
        capability_requirements: validRequirements.map(function(req) {
            return {
                capability_id: req.capability_id,
                capability_name: req.capability_name,
                min_coverage: req.min_coverage,
                importance: req.importance
            };
        }),
        organization_context: {
            size: document.getElementById('orgSize').value,
            industry: document.getElementById('industry').value,
            deployment_preference: document.getElementById('deployment').value,
            user_count: parseInt(document.getElementById('userCount').value),
            tco_period_years: parseInt(document.getElementById('tcoPeriod').value)
        }
    };

    // Add budget constraints if provided
    const minBudget = document.getElementById('minBudget').value;
    const maxBudget = document.getElementById('maxBudget').value;

    if (minBudget || maxBudget) {
        requestData.constraints = {
            budget_range: {
                min: minBudget ? parseFloat(minBudget) : 0, // zero-fallback-ok: an empty lower bound on a budget filter means no lower bound
                max: maxBudget ? parseFloat(maxBudget) : 999999999
            }
        };
    }

    // Run discovery
    Platform.fetch.post('/api/vendor-discovery/discover', requestData, { silent: true })
        .then(function(data) {
            document.getElementById('loadingState').classList.add('hidden');

            if (data.success) {
                discoveryResults = data.discovery_results;
                displayResults();
            } else {
                showError(data.error || 'Discovery failed');
            }
        })
        .catch(function(error) {
            document.getElementById('loadingState').classList.add('hidden');
            showError('Failed to run vendor discovery');
        });
}

function displayResults() {
    if (!discoveryResults) return;

    // Display summary
    displaySummary();

    // Display top recommendations
    displayTopRecommendations();

    // Display all candidates
    displayAllCandidates();

    // Display coverage matrix
    displayCoverageMatrix();

    // Show results
    document.getElementById('discoveryResults').classList.remove('hidden');
}

function displaySummary() {
    const summary = discoveryResults.discovery_summary;
    const container = document.getElementById('discoverySummary');

    safeHTML(container,
        '<div class="summary-card">' +
            '<div class="summary-value">' + summary.total_candidates + '</div>' +
            '<div class="summary-label">Total Candidates</div>' +
        '</div>' +
        '<div class="summary-card">' +
            '<div class="summary-value">' + summary.strong_recommendations + '</div>' +
            '<div class="summary-label">Strong Recommendations</div>' +
        '</div>' +
        '<div class="summary-card">' +
            '<div class="summary-value">' + summary.average_score + '%</div>' +
            '<div class="summary-label">Average Score</div>' +
        '</div>' +
        '<div class="summary-card">' +
            '<div class="summary-value">' + summary.budget_compliance + '%</div>' +
            '<div class="summary-label">Budget Compliance</div>' +
        '</div>');
}

function displayTopRecommendations() {
    const recommendations = discoveryResults.top_recommendations;
    const container = document.getElementById('topRecommendations');

    safeHTML(container, '');

    recommendations.forEach(function(rec, index) {
        const card = document.createElement('div');
        card.className = 'vendor-card ' + (rec.recommendation_strength === 'strong_recommend' ? 'strong-recommended' : 'recommended');

        safeHTML(card,
            '<div class="flex justify-between items-start mb-4">' +
                '<div>' +
                    '<h4 class="text-lg font-semibold">' + rec.vendor.name + '</h4>' +
                    '<p class="text-muted-foreground">' + rec.product.name + '</p>' +
                '</div>' +
                '<div class="text-right">' +
                    '<div class="score-badge ' + getScoreClass(rec.overall_score) + '">' +
                        rec.overall_score + '% Overall Score' +
                    '</div>' +
                    '<div class="text-sm text-muted-foreground mt-1">Rank #' + rec.rank + '</div>' +
                '</div>' +
            '</div>' +

            '<div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">' +
                '<div class="text-center"><div class="text-lg font-semibold text-primary">' + Platform.figure.percent(rec.vendor.scores && rec.vendor.scores.capability_coverage) + '</div><div class="text-xs text-muted-foreground">Coverage</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-emerald-600">' + Platform.figure.percent(rec.vendor.scores && rec.vendor.scores.cost_effectiveness) + '</div><div class="text-xs text-muted-foreground">Cost</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-primary">' + Platform.figure.percent(rec.vendor.scores && rec.vendor.scores.strategic_fit) + '</div><div class="text-xs text-muted-foreground">Strategic Fit</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-orange-600">' + Platform.figure.percent(rec.vendor.scores && rec.vendor.scores.risk_profile) + '</div><div class="text-xs text-muted-foreground">Risk Profile</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-destructive">' + Platform.figure.percent(rec.vendor.scores && rec.vendor.scores.implementation_complexity) + '</div><div class="text-xs text-muted-foreground">Implementation</div></div>' +
            '</div>' +

            (rec.vendor.tco ?
                '<div class="tco-display mb-4">' +
                    '<div class="flex justify-between items-center">' +
                        '<div>' +
                            '<div class="tco-value">$' + formatNumber(rec.vendor.tco.total_tco) + '</div>' +
                            '<div class="tco-label">Total TCO (' + discoveryResults.discovery_metadata.tco_period_years + ' years)</div>' +
                        '</div>' +
                        '<div class="text-right">' +
                            '<div class="text-lg font-semibold">' + (rec.vendor.tco.per_user_annual > 0 ? window.currencyManager.format(rec.vendor.tco.per_user_annual) : 'N/A') + '</div>' +
                            '<div class="text-sm text-muted-foreground">Per User Annual</div>' +
                        '</div>' +
                    '</div>' +
                '</div>'
            : '') +

            '<div class="recommendation-reasoning mb-4">' +
                '<h5 class="font-semibold mb-2">Key Reasons:</h5>' +
                rec.reasoning.map(function(reason) {
                    return '<div class="reasoning-item"><i class="fas fa-check text-emerald-500"></i><span>' + reason + '</span></div>';
                }).join('') +
            '</div>' +

            '<div class="flex justify-between items-center">' +
                '<div class="flex space-x-4">' +
                    '<div>' +
                        '<h5 class="font-semibold text-emerald-700 mb-1">Strengths:</h5>' +
                        rec.key_strengths.map(function(strength) {
                            return '<div class="strength-item"><i class="fas fa-check-circle text-emerald-500"></i><span class="text-sm">' + strength + '</span></div>';
                        }).join('') +
                    '</div>' +
                    '<div>' +
                        '<h5 class="font-semibold text-destructive mb-1">Concerns:</h5>' +
                        rec.potential_concerns.map(function(concern) {
                            return '<div class="concern-item"><i class="fas fa-exclamation-triangle text-destructive"></i><span class="text-sm">' + concern + '</span></div>';
                        }).join('') +
                    '</div>' +
                '</div>' +
            '</div>' +

            '<div class="mt-4">' +
                '<h5 class="font-semibold text-primary mb-2">Next Steps:</h5>' +
                rec.next_steps.map(function(step) {
                    return '<div class="next-step-item"><i class="fas fa-arrow-right text-primary mt-1"></i><span class="text-sm">' + step + '</span></div>';
                }).join('') +
            '</div>');

        container.appendChild(card);
    });
}

function displayAllCandidates() {
    const candidates = discoveryResults.all_candidates;
    const container = document.getElementById('allCandidates');

    safeHTML(container, '');

    candidates.forEach(function(candidate) {
        const card = document.createElement('div');
        card.className = 'vendor-card';

        safeHTML(card,
            '<div class="flex justify-between items-start mb-4">' +
                '<div>' +
                    '<h4 class="text-lg font-semibold">' + candidate.vendor.name + '</h4>' +
                    '<p class="text-muted-foreground">' + candidate.product.name + '</p>' +
                '</div>' +
                '<div class="text-right">' +
                    '<div class="score-badge ' + getScoreClass(candidate.scores.overall) + '">' +
                        candidate.scores.overall + '% Overall Score' +
                    '</div>' +
                    '<div class="text-sm text-muted-foreground mt-1">' + candidate.recommendation_strength + '</div>' +
                '</div>' +
            '</div>' +

            '<div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">' +
                '<div class="text-center"><div class="text-lg font-semibold text-primary">' + candidate.scores.capability_coverage + '%</div><div class="text-xs text-muted-foreground">Coverage</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-emerald-600">' + candidate.scores.cost_effectiveness + '%</div><div class="text-xs text-muted-foreground">Cost</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-primary">' + candidate.scores.strategic_fit + '%</div><div class="text-xs text-muted-foreground">Strategic Fit</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-orange-600">' + candidate.scores.risk_profile + '%</div><div class="text-xs text-muted-foreground">Risk Profile</div></div>' +
                '<div class="text-center"><div class="text-lg font-semibold text-destructive">' + candidate.scores.implementation_complexity + '%</div><div class="text-xs text-muted-foreground">Implementation</div></div>' +
            '</div>' +

            (candidate.tco ?
                '<div class="tco-display">' +
                    '<div class="flex justify-between items-center">' +
                        '<div>' +
                            '<div class="tco-value">$' + formatNumber(candidate.tco.total_tco) + '</div>' +
                            '<div class="tco-label">Total TCO</div>' +
                        '</div>' +
                        '<div class="text-right">' +
                            '<div class="text-lg font-semibold">$' + formatNumber(candidate.tco.per_user_annual) + '</div>' +
                            '<div class="text-sm text-muted-foreground">Per User Annual</div>' +
                        '</div>' +
                    '</div>' +
                '</div>'
            : ''));

        container.appendChild(card);
    });
}

function displayCoverageMatrix() {
    const matrix = discoveryResults.capability_coverage_matrix;
    const container = document.getElementById('coverageMatrix');

    if (!matrix || !matrix.capabilities.length) {
        safeHTML(container, '<p class="text-muted-foreground">No coverage data available</p>');
        return;
    }

    let html = '<div class="overflow-x-auto"><table class="comparison-table">';

    // Header
    html += '<thead><tr><th>Vendor / Product</th>';
    matrix.capabilities.forEach(function(cap) {
        html += '<th>' + cap.name + '</th>';
    });
    html += '</tr></thead>';

    // Data rows
    matrix.coverage_data.forEach(function(row) {
        html += '<tr>';
        html += '<td class="font-medium">' + row.vendor_name + '</td>';

        matrix.capabilities.forEach(function(cap) {
            const coverage = row['capability_' + cap.id] || 0;
            const coverageClass = getCoverageClass(coverage);
            html += '<td class="' + coverageClass + '">' + coverage + '%</td>';
        });

        html += '</tr>';
    });

    html += '</table></div>';
    safeHTML(container, html);
}

function getScoreClass(score) {
    if (score >= 85) return 'score-excellent';
    if (score >= 75) return 'score-good';
    if (score >= 65) return 'score-fair';
    return 'score-poor';
}

function getCoverageClass(coverage) {
    if (coverage >= 80) return 'bg-emerald-500/10 text-green-800';
    if (coverage >= 60) return 'bg-primary/10 text-primary/90';
    if (coverage >= 40) return 'bg-amber-500/10 text-yellow-800';
    return 'bg-destructive/10 text-red-800';
}

function formatNumber(num) {
    return Math.round(num).toLocaleString();
}

function exportResults() {
    if (!discoveryResults) {
        showError('No results to export');
        return;
    }

    // Create CSV export
    let csv = 'Vendor,Product,Overall Score,Coverage,Cost,Strategic Fit,Risk,Implementation,Total TCO,Per User Annual,Recommendation\n';

    discoveryResults.all_candidates.forEach(function(candidate) {
        csv += '"' + candidate.vendor.name + '","' + candidate.product.name + '",' + candidate.scores.overall + ',' + candidate.scores.capability_coverage + ',' + candidate.scores.cost_effectiveness + ',' + candidate.scores.strategic_fit + ',' + candidate.scores.risk_profile + ',' + candidate.scores.implementation_complexity + ',' + (Platform.figure.missing(candidate.tco && candidate.tco.total_tco) ? '' : candidate.tco.total_tco) + ',' + (Platform.figure.missing(candidate.tco && candidate.tco.per_user_annual) ? '' : candidate.tco.per_user_annual) + ',' + candidate.recommendation_strength + '\n';
    });

    // Download CSV
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vendor_discovery_results.csv';
    a.click();
    window.URL.revokeObjectURL(url);
}

function showError(message) {
    Platform.toast.error(message); // Simple error display - could be enhanced with proper toast notifications
}

// Delegated, because the CSP refuses inline on*= attributes: the Min Coverage
// input and the Importance select carried onchange="updateRequirement(...)" and
// therefore never wrote back, so every requirement kept its default no matter
// what the user changed. updateRequirement() exists at file scope and only
// mutates the in-memory capabilityRequirements array -- nothing destructive.
document.addEventListener('change', function(event) {
    const el = event.target.closest('[data-requirement-field]');
    if (!el) return;
    updateRequirement(Number(el.getAttribute('data-requirement-index')),
                      el.getAttribute('data-requirement-field'), el.value);
});
