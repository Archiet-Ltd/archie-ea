/* The Value streams at risk page.
 *
 * Shows the intelligence API's own answer to "which value streams depend on a
 * capability below a maturity threshold". Nothing is counted or scored here:
 * every row, count and maturity value on the page is read from that one
 * answer, and the next answer replaces it whole.
 *
 * The threshold is the one control. Changing it asks the API again and puts
 * the value in the address bar, so a reload or a shared link opens on the
 * same answer.
 *
 * Registered as a top-level window factory and referenced as
 * x-data="valueStreamsAtRisk()", the same way the Ask page registers its own.
 */
(function (global) {
    'use strict';

    var API_URL = '/api/v1/intelligence/value-streams-at-risk';
    var EM_DASH = '—';
    var ERROR_LINE = 'We could not load the value streams just now.';

    /* The server's own message for a refused request, when it sent one. */
    function errorText(err) {
        var data = err && err.data;
        var detail = data && data.error && typeof data.error === 'object' ? data.error.message : null;
        return detail ? ERROR_LINE + ' ' + detail : ERROR_LINE;
    }

    function maturityText(value) {
        return value === null || value === undefined ? EM_DASH : String(value);
    }

    function gapText(current, target) {
        if (current === null || current === undefined || target === null || target === undefined) {
            return EM_DASH;
        }
        return String(target - current);
    }

    /* One capability entry per distinct capability on a row. The answer lists
       a capability once per stage it is mapped to, with the same maturity on
       each; here the stages are gathered onto the one entry. */
    function capabilityModels(entries) {
        var byId = {};
        var order = [];
        (entries || []).forEach(function (entry) {
            var model = byId[entry.id];
            if (!model) {
                model = {
                    id: entry.id,
                    name: entry.name,
                    code: entry.code || '',
                    current: maturityText(entry.current_maturity),
                    target: maturityText(entry.target_maturity),
                    gap: gapText(entry.current_maturity, entry.target_maturity),
                    atRisk: entry.at_risk === true,
                    unassessed: entry.at_risk === null || entry.at_risk === undefined,
                    stages: []
                };
                byId[entry.id] = model;
                order.push(model);
            }
            var stage = entry.dependency && entry.dependency.stage;
            if (stage && stage.name && model.stages.indexOf(stage.name) === -1) {
                model.stages.push(stage.name);
            }
        });
        return order.map(function (model) {
            model.stageText = model.stages.length ? model.stages.join(', ') : 'No stage recorded';
            model.statusText = model.atRisk ? 'Below threshold' : (model.unassessed ? 'Not assessed' : 'At or above threshold');
            return model;
        });
    }

    function rowModel(row) {
        var capabilities = capabilityModels(row.capabilities);
        var unassessed = capabilities.filter(function (c) { return c.unassessed; }).length;
        return {
            id: row.value_stream.id,
            name: row.value_stream.name,
            code: row.value_stream.code || '',
            atRiskCount: row.at_risk_capability_count,
            capabilityCount: capabilities.length,
            unassessedCount: unassessed,
            atRisk: row.at_risk_capability_count > 0,
            linked: capabilities.length > 0,
            capabilities: capabilities
        };
    }

    /* The page's rows, one per row of the answer, in the answer's order. */
    function buildRows(payload) {
        return ((payload && payload.rows) || []).map(rowModel);
    }

    function canonicalThreshold(value) {
        return value >= 1 && value <= 5 ? value : 3;
    }

    function parseThreshold(raw) {
        if (typeof raw !== 'string' || !/^[1-5]$/.test(raw)) return NaN;
        return parseInt(raw, 10);
    }

    function valueStreamsAtRisk() {
        return {
            state: 'loading',
            threshold: 3,
            thresholds: [],
            rows: [],
            summary: {},
            openIds: [],
            errorLine: '',
            _seq: 0,

            init() {
                var params = new URL(global.location.href).searchParams;
                var incoming = params.get('threshold');
                var fromUrl = parseThreshold(incoming);
                var initial = parseInt(this.$el.getAttribute('data-threshold'), 10);
                var next = canonicalThreshold(isNaN(fromUrl) ? initial : fromUrl);
                this.threshold = canonicalThreshold(initial);
                if (incoming === null || String(next) !== incoming) {
                    var url = new URL(global.location.href);
                    url.searchParams.set('threshold', String(next));
                    global.history.replaceState(null, '', url.pathname + url.search + url.hash);
                }
                this.load();
            },

            setThreshold(value) {
                var next = parseInt(value, 10);
                if (!(next >= 1 && next <= 5) || next === this.threshold) return;
                this.threshold = next;
                var url = new URL(global.location.href);
                url.searchParams.set('threshold', String(next));
                global.history.replaceState(null, '', url.pathname + url.search + url.hash);
                this.load();
            },

            async load() {
                this._seq += 1;
                var seq = this._seq;
                this.state = 'loading';
                this.errorLine = '';
                try {
                    var resp = await Platform.fetch.get(API_URL, { threshold: this.threshold }, { silent: true });
                    if (seq !== this._seq) return;
                    var payload = resp && resp.data;
                    if (!payload || !Array.isArray(payload.rows) || !payload.summary) {
                        throw new Error('The answer had no rows.');
                    }
                    this.rows = buildRows(payload);
                    this.summary = payload.summary;
                    this.openIds = [];
                    var anyLinked = this.rows.some(function (r) { return r.linked; });
                    if (!anyLinked) {
                        this.state = 'unmapped';
                    } else if (!payload.summary.value_streams_at_risk) {
                        this.state = 'none-at-risk';
                    } else {
                        this.state = 'ready';
                    }
                } catch (err) {
                    if (seq !== this._seq) return;
                    this.rows = [];
                    this.summary = {};
                    this.errorLine = errorText(err);
                    this.state = 'error';
                }
                this.$nextTick(function () {
                    if (global.lucide && typeof global.lucide.createIcons === 'function') {
                        global.lucide.createIcons();
                    }
                });
            },

            isOpen(row) {
                return this.openIds.indexOf(row.id) !== -1;
            },

            toggle(row) {
                var at = this.openIds.indexOf(row.id);
                if (at === -1) {
                    this.openIds.push(row.id);
                } else {
                    this.openIds.splice(at, 1);
                }
            },

            summaryText() {
                var s = this.summary || {};
                return s.value_streams_at_risk + ' of ' + s.value_streams_considered +
                    ' value streams depend on a capability below maturity ' + this.threshold + '.';
            },

            capabilitySummaryText() {
                var s = this.summary || {};
                return s.capabilities_below_threshold + ' of ' + s.capabilities_considered +
                    ' linked capabilities are below the threshold; ' +
                    s.capabilities_with_no_maturity + ' have no maturity recorded.';
            }
        };
    }

    global.valueStreamsAtRisk = valueStreamsAtRisk;
    global.ValueStreamsAtRisk = { buildRows: buildRows };
})(window);
