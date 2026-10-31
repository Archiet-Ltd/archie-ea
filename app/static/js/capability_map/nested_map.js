/**
 * Capability map: the levelled heat map.
 *
 * Capabilities sit inside their parents (level 1 tiles contain level 2 boxes, which
 * contain level 3 chips) in three labelled bands: Strategic, Core and Supporting.
 * Every box is shaded by the measure chosen in the toolbar, with a legend that names
 * the measure and its steps, and a figure on each box so colour is never the only cue.
 * A capability with no value for the measure is unshaded and says "Not assessed".
 *
 * Data source: GET /capability-map/api/unified-capabilities, the same endpoint the
 * Heat Map lens uses. Each entry carries `parent_id` and `band`.
 *
 * Click behaviour is unchanged: it opens the Heat Map lens' own capability detail.
 */
(function () {
    'use strict';

    var _loaded = false;
    var _delegated = false;
    var _caps = [];

    var BANDS = [
        { key: 'strategic', label: 'Strategic' },
        { key: 'core', label: 'Core' },
        { key: 'supporting', label: 'Supporting' }
    ];

    // Steps run from calm to hot. `heat` is the token step (bg-heat-N), `mark` the
    // short figure printed on a box.
    var STEP_CLASS = {
        0: 'bg-heat-5 text-heat-foreground',
        1: 'bg-heat-3 text-heat-foreground',
        2: 'bg-heat-2 text-heat-foreground',
        3: 'bg-heat-1 text-heat-foreground'
    };

    function num(v) { return (v === null || v === undefined || v === '' || isNaN(Number(v))) ? null : Number(v); }

    var MEASURES = {
        gap: {
            name: 'Maturity gap',
            note: 'Target maturity minus current maturity.',
            steps: ['On target', '1 level short', '2 levels short', '3+ levels short'],
            value: function (c) {
                var cur = num(c.current_maturity), tgt = num(c.target_maturity);
                return (cur === null || tgt === null) ? null : tgt - cur;
            },
            step: function (v) { return v <= 0 ? 0 : Math.min(v, 3); },
            mark: function (v) { return v <= 0 ? 'On target' : 'Gap ' + v; }
        },
        coverage: {
            name: 'Application coverage',
            note: 'Applications mapped to the capability.',
            steps: ['4+ applications', '2 to 3 applications', '1 application', 'No application'],
            value: function (c) { return num(c.mapping_count); },
            step: function (v) { return v >= 4 ? 0 : v >= 2 ? 1 : v === 1 ? 2 : 3; },
            mark: function (v) { return v === 0 ? 'No apps' : v + (v === 1 ? ' app' : ' apps'); }
        },
        importance: {
            name: 'Strategic importance',
            note: 'How much the business depends on the capability.',
            steps: ['Low', 'Medium', 'High', 'Critical'],
            value: function (c) {
                var order = { low: 0, medium: 1, high: 2, critical: 3 };
                return (c.strategic_importance in order) ? order[c.strategic_importance] : null;
            },
            step: function (v) { return v; },
            mark: function (v) { return ['Low', 'Medium', 'High', 'Critical'][v]; }
        },
        cost: {
            name: 'Annual cost',
            note: 'Annual cost is not recorded for capabilities yet, so no box is coloured by it.',
            unavailable: true,
            steps: ['Under 25%', '25% to 50%', '50% to 75%', '75% and over'],
            value: function (c) { return null; },
            step: function (v, max) { var r = max ? v / max : 0; return r >= 0.75 ? 3 : r >= 0.5 ? 2 : r >= 0.25 ? 1 : 0; },
            mark: function (v) { return new Intl.NumberFormat(undefined, { notation: 'compact' }).format(v); }
        }
    };

    var _measure = 'gap';

    function stat(cap, max) {
        var m = MEASURES[_measure];
        var v = m.value(cap);
        if (v === null) return { assessed: false };
        var step = m.step(v, max);
        return { assessed: true, step: step, mark: m.mark(v) };
    }

    function boxClasses(st) {
        var base = 'text-left rounded-md border transition-colors cursor-pointer hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ';
        if (!st.assessed) return base + 'border-dashed border-border bg-card text-foreground';
        return base + 'border-border ' + STEP_CLASS[st.step];
    }

    function boxHtml(cap, byParent, max, level) {
        var st = stat(cap, max);
        var kids = (byParent[cap.id] || []).slice().sort(byName);
        var pad = level === 1 ? 'px-3 py-1.5' : 'px-2 py-1';
        var size = level === 1 ? 'text-sm font-medium' : 'text-xs';
        var html = '<div class="space-y-1" data-testid="cap-box" data-level="' + level + '" data-cap-id="' + escapeHtml(cap.id) + '" data-assessed="' + st.assessed + '">';
        html += '<button type="button" class="nm-cap-box flex w-full items-start justify-between gap-2 ' + pad + ' ' + boxClasses(st) + '" data-cap="' + escapeHtml(JSON.stringify(cap)) + '">';
        html += '<span class="' + size + ' break-words">' + escapeHtml(cap.name) + '</span>';
        html += (st.assessed
            ? '<span class="shrink-0 text-xs font-medium tabular-nums">' + escapeHtml(st.mark) + '</span>'
            : '<span class="shrink-0 text-xs text-muted-foreground">Not assessed</span>') + '</button>';
        if (kids.length) {
            html += '<div class="space-y-1 border-l border-border pl-2">';
            kids.forEach(function (k) { html += boxHtml(k, byParent, max, Math.min(level + 1, 3)); });
            html += '</div>';
        }
        return html + '</div>';
    }

    function byName(a, b) { return (a.name || '').localeCompare(b.name || ''); }

    function legendHtml() {
        var m = MEASURES[_measure];
        var html = '<span class="font-medium text-foreground">Heat by: ' + escapeHtml(m.name) + '</span>';
        html += '<span class="text-muted-foreground">' + escapeHtml(m.note) + '</span>';
        (m.unavailable ? [] : m.steps).forEach(function (label, i) {
            html += '<span class="inline-flex items-center gap-1.5" data-testid="legend-step" data-step="' + i + '">';
            html += '<span class="inline-block size-3 rounded-sm border border-border ' + STEP_CLASS[i].split(' ')[0] + '" aria-hidden="true"></span>' + escapeHtml(label) + '</span>';
        });
        html += '<span class="inline-flex items-center gap-1.5" data-testid="legend-step" data-step="none"><span class="inline-block size-3 rounded-sm border border-dashed border-border bg-card" aria-hidden="true"></span>Not assessed</span>';
        return html;
    }

    function renderStats() {
        var total = _caps.length;
        var mapped = _caps.filter(function (c) { return c.is_mapped; }).length;
        var values = {
            total: String(total),
            mapped: String(mapped),
            unmapped: String(total - mapped),
            coverage: total ? Math.round(mapped / total * 100) + '%' : '\u2014'
        };
        Object.keys(values).forEach(function (k) {
            var el = document.getElementById('capmap-stat-' + k);
            if (el) el.textContent = values[k];
        });
    }

    function renderLegend() {
        var legend = document.getElementById('capmap-legend');
        if (legend) safeHTML(legend, legendHtml());
    }

    function renderMap() {
        var container = document.getElementById('capmodel-container');
        if (!container) return;
        var m = MEASURES[_measure];
        var ids = {};
        _caps.forEach(function (c) { ids[c.id] = true; });
        var byParent = {};
        var roots = [];
        _caps.forEach(function (c) {
            if (c.parent_id && ids[c.parent_id] && c.parent_id !== c.id) {
                (byParent[c.parent_id] = byParent[c.parent_id] || []).push(c);
            } else {
                roots.push(c);
            }
        });
        var max = 0;
        _caps.forEach(function (c) { var v = m.value(c); if (v !== null && v > max) max = v; });

        var bands = BANDS.slice();
        if (roots.some(function (c) { return !c.band; })) bands.push({ key: 'unassigned', label: 'Not yet placed' });

        var html = '<div data-testid="cap-scroll" class="overflow-x-auto" tabindex="0" role="region" aria-label="Capability map, scrolls sideways on narrow screens">';
        html += '<div class="min-w-[44rem] space-y-3">';
        bands.forEach(function (band) {
            var inBand = roots.filter(function (c) { return (c.band || 'unassigned') === band.key; }).sort(byName);
            html += '<section data-testid="cap-band" data-band="' + band.key + '" class="flex gap-3 rounded-lg border border-border bg-card p-3">';
            html += '<h3 class="sticky left-0 w-20 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">' + band.label + '</h3>';
            if (!inBand.length) {
                html += '<p class="flex-1 rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">No ' + band.label.toLowerCase() + ' capabilities yet.</p>';
            } else {
                html += '<div class="grid flex-1 grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] items-start gap-3">';
                inBand.forEach(function (c) { html += boxHtml(c, byParent, max, 1); });
                html += '</div>';
            }
            html += '</section>';
        });
        html += '</div></div>';
        safeHTML(container, html);
        renderLegend();
    }

    function loadCapabilityModelTab() {
        if (_loaded) return;
        var loading = document.getElementById('capmodel-loading');
        var empty = document.getElementById('capmodel-empty');
        var container = document.getElementById('capmodel-container');
        if (!loading || !container) return;

        // Platform.fetch throws on non-ok responses; this call site paints its own
        // inline error state, so it passes silent:true to avoid a duplicate toast.
        Platform.fetch('/capability-map/api/unified-capabilities', { silent: true })
            .then(function (data) {
                var caps = data.unified_capabilities || data.capabilities || [];
                loading.classList.add('hidden');
                if (!caps.length) {
                    if (empty) empty.classList.remove('hidden');
                    renderEmptyBands(container);
                    return;
                }
                _caps = caps;
                renderMap();
                renderStats();
                container.classList.remove('hidden');
                _loaded = true;
                wireControls(container);
                if (typeof lucide !== 'undefined') lucide.createIcons();
            })
            .catch(function (e) {
                loading.classList.add('hidden');
                var msg = '<p class="text-sm text-destructive">Failed to load capability model: ' + escapeHtml(e.message) + '</p>';
                safeHTML(container, msg);
                container.classList.remove('hidden');
            });
    }

    function renderEmptyBands(container) {
        var html = '<div class="space-y-3" data-testid="cap-scroll">';
        BANDS.forEach(function (band) {
            html += '<section data-testid="cap-band" data-band="' + band.key + '" class="flex gap-3 rounded-lg border border-dashed border-border p-3">';
            html += '<h3 class="w-20 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">' + band.label + '</h3>';
            html += '<p class="flex-1 text-sm text-muted-foreground">No ' + band.label.toLowerCase() + ' capabilities are recorded. <a class="font-medium text-primary underline-offset-4 hover:underline" href="' + escapeHtml(container.getAttribute('data-add-url') || '#') + '">Define a capability</a> to place it in this band.</p></section>';
        });
        safeHTML(container, html + '</div>');
        container.classList.remove('hidden');
    }

    function wireControls(container) {
        var select = document.getElementById('capmap-measure');
        // A link can open the map on a measure: /capability-map/dashboard?heat=coverage
        var preset = new URLSearchParams(window.location.search).get('heat');
        if (select && preset && MEASURES[preset] && _measure !== preset) {
            _measure = preset;
            select.value = preset;
            renderMap();
        }
        if (select && !select.dataset.wired) {
            select.dataset.wired = '1';
            select.addEventListener('change', function () {
                if (MEASURES[select.value]) { _measure = select.value; renderMap(); }
            });
        }
        if (_delegated) return;
        container.addEventListener('click', function (e) {
            var btn = e.target.closest('.nm-cap-box');
            if (!btn) return;
            var cap;
            try { cap = JSON.parse(btn.getAttribute('data-cap')); } catch (err) { return; }
            // Reuse the Heat Map lens' own drill-down: switch to that lens
            // (its @click loads and marks it active), then open the detail.
            var heatmapTabBtn = document.getElementById('tab-heatmap');
            if (heatmapTabBtn) heatmapTabBtn.click();
            if (typeof window.showHeatmapDetail === 'function') {
                window.showHeatmapDetail(cap);
            }
            if (window.Platform && Platform.toast && typeof Platform.toast.info === 'function') {
                Platform.toast.info('Opened "' + cap.name + '" in the Heat Map detail panel.');
            }
        });
        _delegated = true;
    }

    window.loadCapabilityModelTab = loadCapabilityModelTab;
    document.addEventListener('DOMContentLoaded', loadCapabilityModelTab);
})();
