/**
 * Maturity Radar — current vs. target capability maturity (Business Architect visual 2).
 *
 * Data source: GET /capability-map/api/unified-capabilities — the SAME endpoint the
 * existing "Heat Map" tab already uses (see loadHeatMap() in capability_map/index.js).
 * No new backend route is introduced.
 *
 * The endpoint does not expose per-dimension (people/process/technology/data/governance)
 * maturity — only a single current_maturity / target_maturity pair per capability — so,
 * per the documented fallback, this renders a radar of AVERAGE current vs. target
 * maturity per BusinessDomain. A domain selector lets a Business Architect drill into
 * one domain to see its individual capabilities instead of the domain-level average,
 * and a level selector filters which capabilities feed the aggregate.
 *
 * Uses Chart.js (type: 'radar') — the same CDN/version already used by
 * arb/dashboard.html and applications/rationalization/dashboard.html. No new external
 * charting library is introduced.
 */
(function () {
    'use strict';

    var _allCaps = null;
    var _chart = null;
    var _domainsPopulated = false;

    // Shared helper — see app/static/js/shared/css_color_tokens.js.
    var cssHSL = (window.ArchieColorTokens && window.ArchieColorTokens.cssHSL) || function (varName, alpha) {
        var raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
        if (!raw) return null;
        return alpha !== undefined ? 'hsl(' + raw + ' / ' + alpha + ')' : 'hsl(' + raw + ')';
    };

    function loadMaturityRadarTab(forceRerender) {
        var loading = document.getElementById('maturity-loading');
        var empty = document.getElementById('maturity-empty');
        var chartContainer = document.getElementById('maturity-chart-container');
        if (!loading) return;

        if (_allCaps !== null) {
            if (forceRerender) renderRadar();
            return;
        }

        // Platform.fetch throws on non-ok responses, so the catch block runs for HTTP errors
        // as well as network errors, preventing a failed load from falling through to the
        // same "no capabilities" panel a genuinely empty portfolio shows.
        // We pass { silent: true } because we paint our own inline error state and show a toast below.
        Platform.fetch('/capability-map/api/unified-capabilities', { silent: true })
            .then(function (data) {
                var caps = data.unified_capabilities || data.capabilities || [];
                loading.classList.add('hidden');
                if (!caps.length) {
                    if (empty) empty.classList.remove('hidden');
                    return;
                }
                _allCaps = caps;
                populateDomainSelect(caps);
                renderRadar();
                if (chartContainer) chartContainer.classList.remove('hidden');
            })
            .catch(function (e) {
                loading.classList.add('hidden');
                // The empty panel says "no capabilities yet", which would be a lie
                // here — the capabilities may well exist and simply failed to load.
                if (empty) empty.classList.remove('hidden');
                if (window.Platform && window.Platform.toast) {
                    window.Platform.toast.error('Could not load capability maturity: ' + (e.message || 'request failed') + '. The chart below is empty because the load failed, not because there is no data.');
                }
            });
    }

    function populateDomainSelect(caps) {
        if (_domainsPopulated) return;
        var select = document.getElementById('maturity-domain-select');
        if (!select) return;
        var names = Array.from(new Set(caps.map(function (c) {
            return (c.domain && c.domain.name) || 'Unknown';
        }))).sort();
        names.forEach(function (name) {
            var opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            select.appendChild(opt);
        });
        _domainsPopulated = true;
    }

    function renderRadar() {
        if (!_allCaps || typeof Chart === 'undefined') return;

        var domainSelect = document.getElementById('maturity-domain-select');
        var levelSelect = document.getElementById('maturity-level-select');
        var selectedDomain = domainSelect ? domainSelect.value : '';
        var selectedLevel = levelSelect ? levelSelect.value : '';

        var caps = _allCaps.filter(function (c) {
            if (selectedLevel && String(c.level || 1) !== selectedLevel) return false;
            return true;
        });

        var labels = [];
        var currentData = [];
        var targetData = [];
        var truncated = false;

        if (!selectedDomain) {
            // Aggregate mode: one axis per Business Domain (average maturity).
            var byDomain = {};
            caps.forEach(function (c) {
                var name = (c.domain && c.domain.name) || 'Unknown';
                if (!byDomain[name]) byDomain[name] = { current: 0, target: 0, count: 0 };
                byDomain[name].current += (c.current_maturity || 1);
                byDomain[name].target += (c.target_maturity || 3);
                byDomain[name].count += 1;
            });
            var domainNames = Object.keys(byDomain).sort();
            domainNames.forEach(function (name) {
                var d = byDomain[name];
                labels.push(name);
                currentData.push(Math.round((d.current / d.count) * 10) / 10);
                targetData.push(Math.round((d.target / d.count) * 10) / 10);
            });
        } else {
            // Drill-down mode: one axis per capability within the selected domain.
            var domainCaps = caps.filter(function (c) {
                return ((c.domain && c.domain.name) || 'Unknown') === selectedDomain;
            }).sort(function (a, b) { return (a.name || '').localeCompare(b.name || ''); });

            var MAX_AXES = 15;
            if (domainCaps.length > MAX_AXES) {
                truncated = true;
                domainCaps = domainCaps.slice(0, MAX_AXES);
            }
            domainCaps.forEach(function (c) {
                labels.push(c.name);
                currentData.push(c.current_maturity || 1);
                targetData.push(c.target_maturity || 3);
            });
        }

        var ctx = document.getElementById('maturity-radar-chart');
        var chartContainer = document.getElementById('maturity-chart-container');
        if (!ctx) return;

        if (_chart) {
            _chart.destroy();
            _chart = null;
        }

        if (!labels.length) {
            var empty = document.getElementById('maturity-empty');
            if (chartContainer) chartContainer.classList.add('hidden');
            if (empty) empty.classList.remove('hidden');
            return;
        }

        var primaryLine = cssHSL('--primary') || 'currentColor';
        var primaryFill = cssHSL('--primary', 0.22) || 'transparent';

        _chart = new Chart(ctx, {
            type: 'radar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Current Maturity',
                        data: currentData,
                        backgroundColor: primaryFill,
                        borderColor: primaryLine,
                        pointBackgroundColor: primaryLine,
                        borderWidth: 2
                    },
                    {
                        label: 'Target Maturity',
                        data: targetData,
                        backgroundColor: 'transparent',
                        borderColor: cssHSL('--success') || 'currentColor',
                        borderDash: [6, 4],
                        pointBackgroundColor: cssHSL('--success') || 'currentColor',
                        borderWidth: 2
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    r: {
                        min: 0,
                        max: 5,
                        ticks: { stepSize: 1 },
                        pointLabels: { font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: { position: 'bottom' },
                    tooltip: {
                        callbacks: {
                            label: function (context) {
                                return context.dataset.label + ': ' + context.formattedValue + '/5';
                            }
                        }
                    }
                }
            }
        });

        if (chartContainer && truncated) {
            var note = chartContainer.querySelector('.mr-truncation-note');
            if (!note) {
                note = document.createElement('p');
                note.className = 'mr-truncation-note text-[10px] text-muted-foreground mt-2';
                chartContainer.appendChild(note);
            }
            note.textContent = 'Showing the first ' + labels.length + ' capabilities in this domain (alphabetical) for chart readability.';
        } else if (chartContainer) {
            var existingNote = chartContainer.querySelector('.mr-truncation-note');
            if (existingNote) existingNote.remove();
        }
    }

    window.loadMaturityRadarTab = loadMaturityRadarTab;
})();
