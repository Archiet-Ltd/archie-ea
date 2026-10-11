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
    var RISK_URL = '/api/v1/intelligence/risk/';
    var PORTFOLIO_URL = '/api/v1/intelligence/portfolio/';
    var PROGRAMME_URL = '/api/v1/intelligence/programme/';
    var STRATEGY_URL = '/api/v1/intelligence/strategy/';
    var ACCOUNTABILITY_URL = '/api/v1/intelligence/accountability/';
    var DATA_URL = '/api/v1/intelligence/data/';
    var COMPLIANCE_URL = '/api/v1/intelligence/compliance/';
    var RECOMPUTE_URL = '/api/v1/intelligence/derivation/recompute';
    var DERIVED_URL = '/api/v1/intelligence/derived/';

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

    /* Why a worked-out connection exists: the recorded links behind it, who drew
       each and when, the rule, and the decisions recorded against those
       elements. Written by the server; the drawer shows it as it arrives. */
    function fetchExplanation(derivedId) {
        return Platform.fetch.get(DERIVED_URL + derivedId, {}, { silent: true }).then(function (resp) {
            if (!resp || !resp.data || !resp.data.explanation) {
                throw new Error('The explanation was missing from the answer.');
            }
            return resp.data.explanation;
        });
    }

    /* L6: risks seeded on an element, each with its own blast-radius rows.
       Same request shape as fetchImpact -- see app/api/v1/intelligence/routes.
       api.py:risk_for_element for what "reasons" can carry (element_not_found,
       no_tenant_context, no_risk_recorded). */
    function fetchRisk(elementId, options) {
        return Platform.fetch.get(RISK_URL + elementId, {
            include_derived: options && options.includeDerived ? 'true' : 'false',
            max_depth: (options && options.maxDepth) || 3
        }, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    /* One risk row's server payload turned into the flat shape ask.js's
       template reads -- riskId/riskScore/riskLevel names avoid clashing
       with the JS reserved-adjacent "risk_score" underscore style and match
       the camelCase the rest of this file already uses (rowModel above). */
    function riskModel(risk) {
        return {
            riskId: risk.risk_id,
            title: risk.title,
            status: risk.status,
            likelihood: risk.likelihood,
            impact: risk.impact,
            riskScore: risk.risk_score,
            riskLevel: risk.risk_level,
            owner: risk.owner || null,
            mitigationPlan: risk.mitigation_plan || null,
            affectedRows: risk.affected_rows || [],
            affectedSummary: risk.affected_summary || {}
        };
    }

    function buildRisks(payload) {
        return (payload.risks || []).map(riskModel);
    }

    /* L3: resolves an element to its ApplicationComponent id, the one fact
       ask.js needs to build the rationalization-planning deep link. See
       app/api/v1/intelligence/routes/api.py:portfolio_component_for_element
       for what "reasons" can carry (element_not_found, no_tenant_context,
       no_application_component). */
    function fetchPortfolioComponent(elementId) {
        return Platform.fetch.get(PORTFOLIO_URL + elementId, {}, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    /* L5: work packages seeded on an element, each with its own blast-radius.
       Same request shape as fetchRisk. See
       app/modules/intelligence/routes/api.py:programme_for_element for what
       "reasons" can carry (element_not_found, no_tenant_context,
       no_work_package_recorded). */
    function fetchProgramme(elementId, options) {
        return Platform.fetch.get(PROGRAMME_URL + elementId, {
            include_derived: options && options.includeDerived ? 'true' : 'false',
            max_depth: (options && options.maxDepth) || 3
        }, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    /* One work package's server payload turned into the flat camelCase shape
       ask.js's template reads -- costVariancePct is null (not 0) when the
       package was never costed, matching the server's own not_costed
       reason rather than inventing a number. */
    /* A signed percentage to one decimal place, e.g. "12.5%" or "-3.0%". The
       one place a variance is turned into text; templates only show it. */
    function percentText(value) {
        if (value == null) return null;
        return Number(value).toLocaleString('en-GB', {
            minimumFractionDigits: 1, maximumFractionDigits: 1
        }) + '%';
    }

    function workPackageModel(wp) {
        var hasCostVariance = wp.cost_variance_pct != null;
        var costRedacted = wp.cost_reason === 'financial_data_restricted';
        return {
            workPackageId: wp.work_package_id,
            name: wp.name,
            status: wp.status,
            statusLabel: statusLabel(wp.status),
            progressPercentage: wp.progress_percentage,
            startDate: wp.start_date,
            endDate: wp.end_date,
            isOverdue: wp.is_overdue,
            owner: wp.owner || null,
            costVariancePct: hasCostVariance ? wp.cost_variance_pct : null,
            costVarianceText: hasCostVariance ? percentText(wp.cost_variance_pct) : null,
            hasCostVariance: hasCostVariance,
            costRedacted: costRedacted,
            costReason: wp.cost_reason || null,
            affectedRows: wp.affected_rows || [],
            affectedSummary: wp.affected_summary || {}
        };
    }

    function buildWorkPackages(payload) {
        return (payload.work_packages || []).map(workPackageModel);
    }

    /* L2: initiatives seeded on an element, each with its own blast-radius.
       Same request shape as fetchProgramme. See
       app/modules/intelligence/routes/api.py:strategy_for_element for what
       "reasons" can carry (element_not_found, no_tenant_context,
       no_initiative_linked). */
    function fetchStrategy(elementId, options) {
        return Platform.fetch.get(STRATEGY_URL + elementId, {
            include_derived: options && options.includeDerived ? 'true' : 'false',
            max_depth: (options && options.maxDepth) || 3
        }, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    /* One initiative's server payload turned into the flat camelCase shape
       ask.js's template reads -- budgetVariancePct is null (not 0) when the
       initiative was never budgeted, matching the server's own
       no_budget_recorded reason rather than inventing a number. Success
       metrics are nested as-is (already a small, flat list server-side). */
    function initiativeModel(initiative) {
        var hasBudgetVariance = initiative.budget_variance_pct != null;
        var budgetRedacted = initiative.budget_reason === 'financial_data_restricted';
        return {
            initiativeId: initiative.initiative_id,
            name: initiative.name,
            status: initiative.status,
            statusLabel: statusLabel(initiative.status),
            priority: initiative.priority,
            healthStatus: initiative.health_status,
            completionPercentage: initiative.completion_percentage,
            startDate: initiative.start_date,
            targetEndDate: initiative.target_end_date,
            executiveSponsor: initiative.executive_sponsor || null,
            programManager: initiative.program_manager || null,
            budgetVariancePct: hasBudgetVariance ? initiative.budget_variance_pct : null,
            budgetVarianceText: hasBudgetVariance ? percentText(initiative.budget_variance_pct) : null,
            hasBudgetVariance: hasBudgetVariance,
            budgetRedacted: budgetRedacted,
            budgetReason: initiative.budget_reason || null,
            successMetrics: (initiative.success_metrics || []).map(function (m) {
                return {
                    metricName: m.metric_name,
                    metricType: m.metric_type,
                    targetValue: m.target_value,
                    actualValue: m.actual_value,
                    status: m.status
                };
            }),
            affectedRows: initiative.affected_rows || [],
            affectedSummary: initiative.affected_summary || {}
        };
    }

    function buildInitiatives(payload) {
        return (payload.initiatives || []).map(initiativeModel);
    }

    /* L4: owners of the element's ApplicationComponent. Currently WITHDRAWN
       server-side (see IntelligenceQueryService.accountability_for_element's
       docstring) -- every response carries ownership_reader_not_built and
       capacity_not_available regardless of element_id, no owners array
       ever populated. No max_depth/include_derived -- this lens is a pure
       ownership lookup, not a blast-radius traversal, unlike every other
       lens. */
    function fetchAccountability(elementId) {
        return Platform.fetch.get(ACCOUNTABILITY_URL + elementId, {}, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    /* One ownership row's server payload turned into the flat camelCase
       shape ask.js's template reads. organizationUnit passes through
       as-is (already a small, flat object or null server-side). */
    function ownerModel(owner) {
        return {
            ownerId: owner.owner_id,
            ownershipType: owner.ownership_type,
            ownershipPercentage: owner.ownership_percentage,
            primaryContact: owner.primary_contact || null,
            contactEmail: owner.contact_email || null,
            startDate: owner.start_date,
            endDate: owner.end_date,
            organizationUnit: owner.organization_unit || null
        };
    }

    function buildOwners(payload) {
        return (payload.owners || []).map(ownerModel);
    }

    /* L7: the data objects linked to the element, and the lineage flows in
       and out of it. Steward and owner arrive as free text (recordedAsText),
       never as a person; a missing value stays null and the template says
       "not recorded", never a blank or a zero. */
    function fetchData(elementId) {
        return Platform.fetch.get(DATA_URL + elementId, {}, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    function dataObjectModel(obj) {
        return {
            id: obj.id,
            name: obj.name,
            dataType: obj.data_type || null,
            classification: obj.data_classification || null,
            isMasterData: !!obj.is_master_data,
            containsPii: !!obj.contains_pii,
            gdprScope: !!obj.gdpr_scope,
            retentionDays: obj.retention_period_days === undefined ? null : obj.retention_period_days,
            steward: obj.steward || null,
            owner: obj.owner || null
        };
    }

    function flowModel(flow, elements) {
        // The server sends other_element_name directly on the flow AND (now
        // that the elements map exists) a fuller record keyed by id in
        // elements -- prefer the direct field when present, fall back to
        // the map so a caller that only has elements (the new graph
        // rendering) still resolves a name.
        var entry = elements ? elements[String(flow.other_element_id)] : null;
        return {
            direction: flow.direction,
            otherElementId: flow.other_element_id,
            otherElementName: flow.other_element_name || (entry && entry.name) || null,
            lineageType: flow.lineage_type || null,
            frequency: flow.frequency || null
        };
    }

    function buildDataObjects(payload) {
        return (payload.data_objects || []).map(dataObjectModel);
    }

    function buildFlows(payload) {
        var elements = payload.elements || {};
        return (payload.flows || []).map(function (flow) {
            return flowModel(flow, elements);
        });
    }

    /* Compliance (under L6): the controls the element's application is mapped
       to, open policy violations and the last scan time. A control with no
       evidence says so; nothing is shown as a percentage or a zero. */
    function fetchCompliance(elementId) {
        return Platform.fetch.get(COMPLIANCE_URL + elementId, {}, { silent: true }).then(function (resp) {
            return resp && resp.data ? resp.data : {};
        });
    }

    function controlModel(c) {
        return {
            code: c.code || null,
            name: c.name,
            frameworkName: c.framework_name || null,
            status: c.implementation_status,
            evidenceRecorded: !!c.evidence_url_recorded,
            verified: !!c.verified,
            verifiedDate: c.verified_date || null,
            noEvidence: !!c.no_evidence
        };
    }

    function violationModel(v) {
        return {
            policyName: v.policy_name || 'Unnamed policy',
            severity: v.severity || null,
            detectedAt: v.detected_at || null
        };
    }

    function buildControls(payload) {
        return (payload.controls || []).map(controlModel);
    }

    function buildViolations(payload) {
        return (payload.open_violations || []).map(violationModel);
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

    /* After an answer arrives, scroll its results heading into view and move
       focus to it so the answer is visible without scrolling and announced
       for screen readers (the heading already carries tabindex="-1"). */
    function showResults(heading) {
        if (!heading) return;
        heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
        heading.focus();
    }

    /* Convert a snake_case status code to a readable label: "in_progress" →
       "In Progress", "Active" → "Active". Matches the Python-side pattern
       status.replace("_", " ").title() used across this codebase. */
    function statusLabel(status) {
        if (!status) return '';
        return status.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
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
        fetchRisk: fetchRisk,
        buildRisks: buildRisks,
        fetchPortfolioComponent: fetchPortfolioComponent,
        fetchProgramme: fetchProgramme,
        buildWorkPackages: buildWorkPackages,
        fetchStrategy: fetchStrategy,
        buildInitiatives: buildInitiatives,
        fetchAccountability: fetchAccountability,
        buildOwners: buildOwners,
        fetchData: fetchData,
        buildDataObjects: buildDataObjects,
        buildFlows: buildFlows,
        fetchCompliance: fetchCompliance,
        buildControls: buildControls,
        buildViolations: buildViolations,
        recompute: recompute,
        fetchExplanation: fetchExplanation,
        timeText: timeText,
        refreshIcons: refreshIcons,
        keepPlace: keepPlace,
        showResults: showResults,
        statusLabel: statusLabel,
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
