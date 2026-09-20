/* Shared code for the Ask and Twin map pages.
 *
 * Everything that talks to the server goes through Platform.fetch, and every
 * name a person reads comes from the element map the impact answer carries.
 * Nothing here looks a name up anywhere else, and nothing here remembers a
 * name from one answer to the next: each answer is turned into row and graph
 * models from its own payload and replaced whole by the next.
 *
 * The sentence a person reads under "In plain terms" is written by the server
 * and arrives as relation.plain_terms. This file passes it through untouched.
 */
(function (global) {
    'use strict';

    var SEARCH_URL = '/archimate/api/elements/search';
    var IMPACT_URL = '/api/v1/intelligence/impact/';
    var RECOMPUTE_URL = '/api/v1/intelligence/derivation/recompute';

    var ERROR_LINE = 'We could not answer that just now.';
    var BUSY_LINE = 'A recalculation is already running. Try again shortly.';
    var STALE_NOTICE = 'The connections we worked out may be out of date.';
    var WITHHELD_NOTICE = 'Some connections we worked out are not shown because they may be out of date.';
    var WITHHELD_SUFFIX = ', worked-out connections not shown';
    var WORKED_OUT = 'Worked out';
    var WORKED_OUT_STALE = 'Worked out, may be out of date';
    var STALE_UNDATED = 'May be out of date.';

    /* The six bands of the map, top to bottom, keyed by the canonical layer
       value the impact answer carries. The plain word is what a person reads;
       the layer value itself only appears inside "Full detail". */
    var BANDS = [
        { layer: 'motivation', label: 'Goals' },
        { layer: 'strategy', label: 'Strategy' },
        { layer: 'business', label: 'Business' },
        { layer: 'application', label: 'Systems' },
        { layer: 'technology', label: 'Technology' },
        { layer: 'implementation', label: 'Delivery' }
    ];
    var UNPLACED_BAND = { layer: 'unplaced', label: 'Layer not recorded' };

    // ── requests ──────────────────────────────────────────────────────────

    function searchElements(term) {
        return Platform.fetch.get(SEARCH_URL, { q: term, limit: 10 }, { silent: true }).then(function (resp) {
            return resp && Array.isArray(resp.data) ? resp.data : [];
        });
    }

    /* Wherever worked-out connections are asked for, connections that have gone
       out of date are asked for too. Without that the answer leaves them out and
       only says so in its summary, and the person would see fewer connections
       with no way to know. */
    function fetchImpact(elementId, options) {
        return Platform.fetch.get(IMPACT_URL + elementId, {
            include_derived: options.includeDerived ? 'true' : 'false',
            include_stale: options.includeDerived ? 'true' : 'false',
            max_depth: options.maxDepth,
            with_owner: 'true'
        }, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    function recompute() {
        return Platform.fetch.post(RECOMPUTE_URL, { scope: 'tenant' }, { silent: true });
    }

    // ── small helpers ─────────────────────────────────────────────────────

    function timeText(iso) {
        if (!iso) return null;
        var text = String(iso);
        // The server sends a naive UTC timestamp; without a zone the browser
        // would read it as local time.
        if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(text)) text += 'Z';
        var when = new Date(text);
        if (isNaN(when.getTime())) return null;
        return when.toLocaleString('en-GB', {
            day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
        });
    }

    function refreshIcons() {
        if (global.lucide && typeof global.lucide.createIcons === 'function') {
            global.lucide.createIcons();
        }
    }

    /* After the notice that held the recalculation button is gone, put focus on
       the page's own heading so it does not fall back to the top of the page. Only
       when focus is still on that button (or nowhere), and only when the notice has
       gone: a person who has moved on, or a notice that is still there, is left alone. */
    function keepPlace(heading, noticeStillShown) {
        if (!heading || noticeStillShown) return;
        var active = document.activeElement;
        var onButton = active && active.hasAttribute && active.hasAttribute('data-recompute-button');
        if (onButton || !active || active === document.body) heading.focus();
    }

    function failureStatus(err) {
        return err && typeof err.status === 'number' ? err.status : null;
    }

    function bandFor(layer) {
        for (var i = 0; i < BANDS.length; i++) {
            if (BANDS[i].layer === layer) return BANDS[i];
        }
        return UNPLACED_BAND;
    }

    function pluralThings(count) {
        return count === 1 ? '1 thing' : count + ' things';
    }

    /* What the picker's status region says after a search: how many matches. */
    function resultsText(count, term) {
        return count + (count === 1 ? ' result' : ' results') + ' for ' + term;
    }

    /* What it says after an element is chosen: how many connections it has. When
       worked-out connections were left out of the answer, the message says so. */
    function connectionsText(count, name, withheld) {
        return count + (count === 1 ? ' connection' : ' connections') + ' for ' + name +
            (withheld ? WITHHELD_SUFFIX : '');
    }

    function captionText(name, nodes, edges, withheld) {
        return (name ? 'Connections for ' + name + ': ' : 'Connections: ') +
            nodes + ' things, ' + edges + ' connections' + (withheld ? WITHHELD_SUFFIX : '');
    }

    // ── payload -> models ─────────────────────────────────────────────────

    function elementEntry(elements, id) {
        var entry = elements ? elements[String(id)] : null;
        return entry || null;
    }

    function nameOf(elements, id) {
        var entry = elementEntry(elements, id);
        return entry && entry.name ? entry.name : null;
    }

    /* What "Full detail" reads before any row has been chosen. Always an object
       so the expressions that read it never meet a null. */
    function emptyDetail() {
        return {
            derived: false, type: null, layer: null, ruleId: null, confidence: null,
            chain: [], depth: null, computedAt: null, engineVersion: null, derivedId: null
        };
    }

    /* The detail a person can open under "Full detail". It is a plain copy of
       what the payload carries; nothing is worked out or filled in here. */
    function detailOf(row, elements) {
        var relation = row.relation || {};
        var entry = elementEntry(elements, row.element_id);
        var chain = (relation.chain_elements || []).map(function (id) {
            return nameOf(elements, id);
        });
        return {
            derived: relation.kind === 'derived',
            type: entry ? entry.type : null,
            layer: entry ? entry.layer : null,
            ruleId: relation.rule_id,
            confidence: relation.confidence,
            chain: chain,
            depth: relation.depth,
            computedAt: relation.computed_at,
            engineVersion: relation.engine_version,
            derivedId: relation.derived_id
        };
    }

    function rowModel(row, elements, centreId) {
        var relation = row.relation || {};
        var derived = relation.kind === 'derived';
        var chain = relation.chain_elements || [];
        // A derived fact starts at the element the question was asked about.
        // An explicit row's edge is the last step of its chain.
        var fromId = derived ? centreId : (chain.length >= 2 ? chain[chain.length - 2] : centreId);
        var owner = row.owner || null;
        var computed = timeText(relation.computed_at);
        var stale = relation.stale === true;
        return {
            key: derived
                ? 'derived-' + (relation.derived_id != null ? relation.derived_id : row.element_id + '-' + relation.depth)
                : 'explicit-' + row.element_id + '-' + relation.depth,
            elementId: row.element_id,
            fromId: fromId,
            chainIds: chain,
            fromName: nameOf(elements, fromId),
            name: nameOf(elements, row.element_id),
            derived: derived,
            kindLabel: derived ? (stale ? WORKED_OUT_STALE : WORKED_OUT) : 'Explicit',
            relationType: relation.type,
            depth: relation.depth,
            ownerName: owner && owner.name ? owner.name : null,
            stale: stale,
            staleLine: stale ? (computed ? 'Last worked out ' + computed + ' \u2014 may be out of date.' : STALE_UNDATED) : null,
            plainTerms: typeof relation.plain_terms === 'string' && relation.plain_terms ? relation.plain_terms : null,
            detail: detailOf(row, elements)
        };
    }

    /* Closest connections first; an explicit connection ahead of a worked-out
       one at the same distance; then by name so the order is steady. */
    function rankRows(a, b) {
        if (a.depth !== b.depth) return a.depth - b.depth;
        if (a.derived !== b.derived) return a.derived ? 1 : -1;
        return String(a.name || '').localeCompare(String(b.name || ''));
    }

    function buildRows(payload, centreId) {
        var elements = payload.elements || {};
        var rows = (payload.rows || []).map(function (row) {
            return rowModel(row, elements, centreId);
        });
        rows.sort(rankRows);
        return rows;
    }

    /* Whether the answer holds connections that may be out of date, and whether
       any worked-out connections were left out of it altogether.

       The answer reports its own derivation state. A stale state, or any row
       marked stale, gets a notice. Worked-out connections count as left out when
       they were asked for and the answer is stale yet lists none, or when the
       summary counts more stale rows than worked-out rows (which it cannot when
       the answer is complete, so this only fires on an answer that is not). */
    function answerState(payload, rows, includeDerived) {
        var summary = payload.summary || {};
        var derivedShown = rows.filter(function (r) { return r.derived; }).length;
        var staleShown = rows.filter(function (r) { return r.stale; }).length;
        var stale = summary.derivation_state === 'stale' || staleShown > 0;
        var withheld = includeDerived === true && (
            (summary.derivation_state === 'stale' && derivedShown === 0) ||
            (typeof summary.stale_count === 'number' && summary.stale_count > derivedShown));
        return {
            stale: stale,
            withheld: withheld,
            notice: withheld ? WITHHELD_NOTICE : STALE_NOTICE,
            notComputed: summary.derivation_state === 'not_computed' && rows.length > 0
        };
    }

    /* The map as nodes and edges, built from the same rows the table lists, so
       the picture and the text cannot disagree. */
    function buildGraph(rows, payload, centreId) {
        var elements = payload.elements || {};
        var nodeMap = {};
        function ensureNode(id) {
            if (nodeMap[id] === undefined) {
                var entry = elementEntry(elements, id);
                nodeMap[id] = {
                    id: id,
                    name: entry && entry.name ? entry.name : null,
                    layer: entry ? entry.layer : null,
                    type: entry ? entry.type : null,
                    band: bandFor(entry ? entry.layer : null).layer,
                    depth: id === centreId ? 0 : null
                };
            }
            return nodeMap[id];
        }
        ensureNode(centreId);
        var edges = rows.map(function (row) {
            var to = ensureNode(row.elementId);
            if (to.depth === null || row.depth < to.depth) to.depth = row.depth;
            ensureNode(row.fromId);
            return {
                key: row.key,
                from: row.fromId,
                to: row.elementId,
                kind: row.derived ? 'derived' : 'explicit',
                stale: row.stale,
                type: row.relationType,
                depth: row.depth
            };
        });
        var nodes = Object.keys(nodeMap).map(function (id) { return nodeMap[id]; });
        return { centreId: centreId, nodes: nodes, edges: edges };
    }

    global.Intelligence = {
        ERROR_LINE: ERROR_LINE,
        BUSY_LINE: BUSY_LINE,
        BANDS: BANDS,
        UNPLACED_BAND: UNPLACED_BAND,
        searchElements: searchElements,
        fetchImpact: fetchImpact,
        recompute: recompute,
        timeText: timeText,
        refreshIcons: refreshIcons,
        keepPlace: keepPlace,
        failureStatus: failureStatus,
        bandFor: bandFor,
        pluralThings: pluralThings,
        resultsText: resultsText,
        connectionsText: connectionsText,
        captionText: captionText,
        answerState: answerState,
        WORKED_OUT: WORKED_OUT,
        WORKED_OUT_STALE: WORKED_OUT_STALE,
        elementEntry: elementEntry,
        nameOf: nameOf,
        emptyDetail: emptyDetail,
        detailOf: detailOf,
        rowModel: rowModel,
        buildRows: buildRows,
        buildGraph: buildGraph
    };
})(window);
