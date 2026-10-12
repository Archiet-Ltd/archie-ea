"""Cross-layer intelligence queries. ``cross_layer_impact`` (L1, "if this
fails, what stops and who owns it" -- every Capability row, and the whole
answer, also carries the one batched maturity read), ``risk_for_element``
(L6, "what could hurt this, and what does it touch" -- reuses the same
traversal per risk seed), ``portfolio_component_for_element`` (L3, resolves
an element to its ApplicationComponent for the one existing deep link),
``programme_for_element`` (L5, "what are we changing, is it on time and on
budget" -- reuses the same traversal per work-package seed), ``strategy_for_element``
(L2, "what are we trying to achieve, and how's it tracking" -- reuses the
same traversal per initiative seed) and ``value_streams_at_risk`` -- "which
value streams depend on a capability below threshold", the curated path
only (T-S1). Coverage over derived and explicit relationships for the
value-stream question is reserved for a later task and is not added here.
``accountability_for_element`` (L4) exists as a route and question card
but is currently WITHDRAWN -- it returns an honest
``ownership_reader_not_built`` reason on every call: the ownership data
source is decided, no shared tenant-safe reader for it exists yet, and a
real tenant-scoping fix to ``OrganizationUnit`` is needed before one is
safe to build -- see the method's own docstring. Five of six lenses in
``intelligence-lenses-v1.md`` are currently answered, plus the Strategic
value-streams-at-risk surface.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.modules.capabilities.services.capability_heatmap_service import CapabilityHeatmapService
from app.modules.intelligence.services.derived_facts import list_derived_facts
from app.modules.intelligence.services.latency_probe import record_query_latency
from app.modules.intelligence.services.plain_terms import plain_terms_sentence
from app.modules.intelligence.services.reason_codes import validate_reason_code

VALID_DIRECTIONS = {"downstream", "upstream", "both"}

VALUE_STREAM_RISK_REASONS = frozenset(
    {
        "capability_below_threshold",
        "capability_under_target",
        "capability_unassessed",
    }
)

NO_OWNERSHIP_REASON = validate_reason_code("no_ownership_recorded")
NO_TENANT_CONTEXT_REASON = validate_reason_code("no_tenant_context")
ELEMENT_NOT_FOUND_REASON = validate_reason_code("element_not_found")
DERIVATION_NOT_COMPUTED_REASON = validate_reason_code("derivation_not_computed")
NO_RISK_RECORDED_REASON = validate_reason_code("no_risk_recorded")
NO_APPLICATION_COMPONENT_REASON = validate_reason_code("no_application_component")
NO_WORK_PACKAGE_RECORDED_REASON = validate_reason_code("no_work_package_recorded")
NOT_COSTED_REASON = validate_reason_code("not_costed")
NO_INITIATIVE_LINKED_REASON = validate_reason_code("no_initiative_linked")
NO_BUDGET_RECORDED_REASON = validate_reason_code("no_budget_recorded")
# Distinct from NO_OWNERSHIP_REASON above ("no_ownership_recorded") -- that
# one describes a single element's missing owner field inside the L1 impact
# traversal; this one describes an ApplicationComponent with zero
# ApplicationOwnership rows at all, a different absence condition on a
# different table, for L4. Currently unused -- accountability_for_element's
# ownership read is withdrawn (see its own docstring), so nothing produces
# this today; kept in the closed vocabulary for whichever reader replaces
# the withdrawn one, not removed on the strength of a temporary gap.
NO_OWNERSHIP_RECORDS_REASON = validate_reason_code("no_ownership_records")
CAPACITY_NOT_AVAILABLE_REASON = validate_reason_code("capacity_not_available")
# Distinct from a "decision pending" state -- the ownership data source IS
# decided; what doesn't exist yet is a shared, tenant-safe reader for it.
OWNERSHIP_READER_NOT_BUILT_REASON = validate_reason_code("ownership_reader_not_built")
NO_DATA_RECORDED_REASON = validate_reason_code("no_data_recorded")
NO_STEWARD_RECORDED_REASON = validate_reason_code("no_steward_recorded")
NO_LINEAGE_RECORDED_REASON = validate_reason_code("no_lineage_recorded")
NO_COMPLIANCE_CONTROLS_RECORDED_REASON = validate_reason_code("no_compliance_controls_recorded")
NO_CONTROL_EVIDENCE_REASON = validate_reason_code("no_control_evidence")
NO_POLICY_SCAN_RECORDED_REASON = validate_reason_code("no_policy_scan_recorded")
# The programme lens's own plateau/gap block: a work package's stored
# plateau_id/gap_id may be unset (a nullable FK) or point at a record
# outside the caller's tenant (the FK itself carries no tenant check, so the
# select that resolves it is what enforces the boundary) -- both read as the
# same honest absence, not an error.
NO_PLATEAU_RECORDED_REASON = validate_reason_code("no_plateau_recorded")
NO_GAP_RECORDED_REASON = validate_reason_code("no_gap_recorded")
NO_CAPABILITY_IN_CHAIN_REASON = validate_reason_code("no_capability_in_chain")
NO_RACI_RECORDED_REASON = validate_reason_code("no_raci_recorded")
# The component block's three independent absence conditions -- no cost
# figures entered, no owner-recorded health status, no licence entitlement
# rows -- each distinct from NO_APPLICATION_COMPONENT_REASON above (which
# means no component was resolved at all).
NO_COST_RECORDED_REASON = validate_reason_code("no_cost_recorded")
NO_HEALTH_RECORDED_REASON = validate_reason_code("no_health_recorded")
NO_LICENCE_RECORDED_REASON = validate_reason_code("no_licence_recorded")
# A licence whose usage has never been synced from the source system carries
# quantity_used at its column default of zero -- comparing that default
# against quantity_entitled would report an invented under-use finding, not
# a measurement, so under_used is withheld with this reason instead.
LICENCE_USAGE_NOT_SYNCED_REASON = validate_reason_code("licence_usage_not_synced")

# T-005 (D1): the NFR-5 measurement point is this exact, PINNED series --
# never widened, never aggregated across label values.
NFR5_QUERY = "cross_layer_impact"
NFR5_DEPTH = "4"
NFR5_INCLUDE_DERIVED = "true"

# T-005 (ADR-009): NFR-5's stated measurement threshold. Appears only as the
# Shape-B trigger's threshold_seconds -- never as a yield target (task 02
# constraint: no fabricated target anywhere in the payload).
SHAPE_B_THRESHOLD_SECONDS = 2.0


def _include_derived_gate(include_derived: bool) -> bool:
    """The ``include_derived=False`` filter, isolated as its own seam (same
    pattern as ``_sec09_tenant_check`` and ``derived_facts.py``'s
    ``_apply_default_staleness_filter``) so the mutation-proof test
    (acceptance item 12) can monkeypatch exactly this to always return True
    and confirm criterion 2 goes red, without editing source under test.
    """
    return bool(include_derived)


def _sec09_tenant_check(component_org_id: Optional[int], org_id: int) -> bool:
    """The SEC-09 assertion, isolated as its own seam.

    Mirrors ``derived_facts.py``'s ``_apply_default_staleness_filter`` seam:
    the task 01 mutation-proof test (acceptance item 12) monkeypatches
    exactly this function to simulate "the tenant assertion was removed" and
    confirms the cross-tenant owner-leak test goes red, without editing
    source under test.
    """
    return component_org_id == org_id


def _licence_tenant_predicate(org_id: int):
    """The explicit ``LicenseEntitlement.organization_id ==`` predicate on
    the portfolio component block's licence read, isolated as its own seam
    -- the same pattern as ``_sec09_tenant_check`` above -- so a mutation
    test can replace it and watch the cross-tenant licence test go red.

    ``LicenseEntitlement`` carries ``TenantMixin`` so the ORM listener
    already fences a normal request; the FK from ``license_entitlements``
    to ``application_components`` carries no tenant check of its own,
    though, so this predicate is what keeps the read correct when called
    with no ambient request context (a job, a CLI command, a test looping
    tenants in one session), where the listener would otherwise no-op.
    """
    from app.models.license_entitlement import LicenseEntitlement

    return LicenseEntitlement.organization_id == org_id


def _resolve_owners_batch(
    element_ids: List[int], org_id: int
) -> Dict[int, Tuple[Optional[Dict[str, Any]], Optional[str]]]:
    """AA-5/SEC-09 owner attach: element -> component -> ownership -> unit,
    batched across MANY element ids in a small constant number of queries
    (M7 fix -- see the build report's B2/NEW-3 sections for why this is now
    the ONLY owner-resolution implementation on this path; a per-row
    ``_resolve_owner``/``_find_component_for_element`` pair used to exist
    alongside this and was deleted as dead code -- ``cross_layer_impact`` is
    this function's only production caller).

    ``ApplicationComponent`` carries ``TenantMixin`` so the component select
    below is already fenced by ``do_orm_execute`` (a cross-tenant row is
    simply not returned in a normal request); ``_sec09_tenant_check`` is the
    belt-and-braces assertion applied on top of that ORM fencing -- kept for
    the session-scoped-caller drift it defends against even now that
    ``ApplicationOwnership`` and ``OrganizationUnit`` carry ``TenantMixin``
    too and are fenced by the same listener.

    Only CURRENT ownership is attached: a row whose ``end_date`` has already
    passed is excluded from the select below, the same rule
    ``accountability_for_element`` applies, so the two owner-resolution
    paths agree on one element.

    ``archimate_element_id`` is indexed but NOT unique (a component created
    before the maintaining listener existed, or by a raw-SQL/import path,
    can point two components at the same element) -- ``.scalars().all()``
    with a deterministic ``order_by(id)`` plus ``setdefault`` below picks the
    same "first" component every time instead of risking
    ``MultipleResultsFound``.
    """
    from datetime import date

    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    distinct_ids = sorted(set(element_ids))
    results: Dict[int, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {
        eid: (None, NO_OWNERSHIP_REASON) for eid in distinct_ids
    }
    if not distinct_ids:
        return results

    components = (
        db.session.execute(
            db.select(ApplicationComponent)
            .where(ApplicationComponent.archimate_element_id.in_(distinct_ids))
            .order_by(ApplicationComponent.id)
        )
        .scalars()
        .all()
    )

    # Deterministic "first" component per element_id -- same ordering
    # _find_component_for_element uses for the single-row case.
    component_by_element: Dict[int, Any] = {}
    for comp in components:
        component_by_element.setdefault(comp.archimate_element_id, comp)

    # SEC-09: drop any component that fails the tenant assertion before it
    # is ever used to reach ownership/unit data.
    guarded_components = {
        eid: comp
        for eid, comp in component_by_element.items()
        if _sec09_tenant_check(comp.organization_id, org_id)
    }
    if not guarded_components:
        return results

    component_ids = [comp.id for comp in guarded_components.values()]
    ownerships = (
        db.session.execute(
            db.select(ApplicationOwnership)
            .where(ApplicationOwnership.application_id.in_(component_ids))
            .where(
                db.or_(
                    ApplicationOwnership.end_date.is_(None),
                    ApplicationOwnership.end_date >= date.today(),
                )
            )
        )
        .scalars()
        .all()
    )
    ownership_by_component: Dict[int, Any] = {}
    for ownership in ownerships:
        ownership_by_component.setdefault(ownership.application_id, ownership)

    unit_ids = [o.organization_unit_id for o in ownership_by_component.values()]
    units: Dict[int, Any] = {}
    if unit_ids:
        units = {
            unit.id: unit
            for unit in db.session.execute(
                db.select(OrganizationUnit).where(OrganizationUnit.id.in_(unit_ids))
            )
            .scalars()
            .all()
        }

    for eid, comp in guarded_components.items():
        ownership = ownership_by_component.get(comp.id)
        if ownership is None:
            continue
        unit = units.get(ownership.organization_unit_id)
        if unit is None:
            continue
        results[eid] = (
            {
                "organization_unit_id": unit.id,
                "name": unit.name,
                "ownership_type": ownership.ownership_type,
            },
            None,
        )
    return results


def _resolve_elements_batch(element_ids: Iterable[int], org_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
    """The element identity map -- ``{str(id): {id, name, type, layer}}``.

    ONE batched ``select`` over ``ArchiMateElement`` for every id in the WHOLE
    result set (same shape as ``_resolve_owners_batch``: collect first, then
    resolve in a constant number of queries -- never one per row).

    The four-key ceiling is enforced at the query, not only when the dict is
    built: only ``id``, ``name``, ``type`` and ``layer`` are selected, so no
    other column of the element (description, documentation, properties, the
    strategic / cost / scoring columns) is ever read, let alone serialised.
    SEC-02 keeps the projection on this path narrow so a later MCP twin
    cannot leak; widening it here would widen it there.

    Tenancy is two layers. ``ArchiMateElement`` carries ``TenantMixin``, so the
    ORM tenant-isolation listener fences the select inside a request. The
    explicit ``organization_id`` predicate below is defence in depth, exactly
    as in ``derived_facts.list_derived_facts``: it is what keeps this function
    correct when it is called with no ambient request context (a job, a CLI
    command, a test looping tenants in one session), where the listener would
    no-op entirely.

    An id that does not resolve inside ``org_id`` -- another tenant's element,
    a soft-deleted element, an element that no longer exists -- is simply
    ABSENT from the result. It is never present with a null name, a
    placeholder, or a name found anywhere else. ``org_id`` of ``None`` yields
    an empty map (there is no tenant whose elements could be named).
    """
    from app.models import ArchiMateElement

    distinct_ids = sorted({eid for eid in element_ids if eid is not None})
    if not distinct_ids or org_id is None:
        return {}

    stmt = db.select(
        ArchiMateElement.id,
        ArchiMateElement.name,
        ArchiMateElement.type,
        ArchiMateElement.layer,
    ).where(
        ArchiMateElement.id.in_(distinct_ids),
        ArchiMateElement.organization_id == org_id,
    )

    elements: Dict[str, Dict[str, Any]] = {}
    for element_id, name, element_type, layer in db.session.execute(stmt).all():
        if name is None:
            # Leave the id out rather than emit an entry with a null name.
            continue
        elements[str(element_id)] = {
            "id": element_id,
            "name": name,
            "type": element_type,
            # ``layer`` comes back as a case-insensitive ``str`` subclass
            # (canonical lower case); hand callers a plain ``str``.
            "layer": str(layer) if layer is not None else None,
        }
    return elements


def _element_ids_in_rows(rows: List[Dict[str, Any]]) -> List[int]:
    """Every element id a row can name: its ``element_id`` and each id in its
    ``relation.chain_elements``. These -- and only these -- are the keys of the
    identity map.
    """
    ids: List[int] = []
    for row in rows:
        ids.append(row["element_id"])
        ids.extend(row["relation"].get("chain_elements") or [])
    return ids


def _name_in(elements: Dict[str, Dict[str, Any]], element_id: Optional[int]) -> Optional[str]:
    entry = elements.get(str(element_id))
    return entry["name"] if entry is not None else None


def _attach_plain_terms(rows: List[Dict[str, Any]], elements: Dict[str, Dict[str, Any]]) -> None:
    """Fill ``relation.plain_terms`` on every DERIVED row (explicit rows keep
    ``None``). The names come from the identity map built for this same
    response; ``plain_terms_sentence`` returns ``None`` when either is absent.

    The derived fact's STORED source and target are passed through as they are,
    together with its stored type; ``plain_terms_sentence`` decides which is
    named first so the sentence never reverses the relationship. Nothing here
    depends on which end the caller started from, so one derived fact reads the
    same sentence from every surface and every query direction.
    """
    for row in rows:
        relation = row["relation"]
        if relation["kind"] != "derived":
            continue
        source_id, target_id = row["_endpoints"]
        relation["plain_terms"] = plain_terms_sentence(
            source_name=_name_in(elements, source_id),
            target_name=_name_in(elements, target_id),
            relation_type=relation["type"],
            depth=relation["depth"],
            confidence=relation["confidence"],
        )


def _explicit_row(rel, depth: int, chain: List[int], chain_elements: List[int], element_id: int) -> Dict[str, Any]:
    return {
        "element_id": element_id,
        "_endpoints": (rel.source_id, rel.target_id),
        "relation": {
            "kind": "explicit",
            "type": rel.type,
            "depth": depth,
            "rule_id": None,
            "chain": chain,
            "chain_elements": chain_elements,
            "confidence": None,
            "provenance": "explicit",
            "computed_at": None,
            "stale": False,
            # Derived-fact-only fields; an explicit row has no derived fact,
            # so all three are null.
            "derived_id": None,
            "engine_version": None,
            "plain_terms": None,
        },
    }


def _walk_explicit(root_id: int, max_depth: int, direction: str) -> List[Dict[str, Any]]:
    """BFS over ``ArchiMateRelationship`` (TenantMixin-fenced, no hand-written
    predicate needed) up to ``max_depth`` hops, in the requested direction.

    Cycle-safe: a node already visited is never re-queued, so this
    terminates even on a cyclic graph within ``max_depth`` iterations.
    """
    from app.models import ArchiMateRelationship

    frontier: Dict[int, Dict[str, List[int]]] = {root_id: {"chain": [], "chain_elements": [root_id]}}
    visited = {root_id}
    rows: List[Dict[str, Any]] = []

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        frontier_ids = list(frontier.keys())
        conditions = []
        if direction in ("downstream", "both"):
            conditions.append(ArchiMateRelationship.source_id.in_(frontier_ids))
        if direction in ("upstream", "both"):
            conditions.append(ArchiMateRelationship.target_id.in_(frontier_ids))
        if not conditions:
            break

        rels = (
            db.session.execute(db.select(ArchiMateRelationship).where(db.or_(*conditions)))
            .scalars()
            .all()
        )

        next_frontier: Dict[int, Dict[str, List[int]]] = {}
        for rel in rels:
            if direction in ("downstream", "both") and rel.source_id in frontier:
                to_node = rel.target_id
                if to_node not in visited:
                    prior = frontier[rel.source_id]
                    new_chain = prior["chain"] + [rel.id]
                    new_elements = prior["chain_elements"] + [to_node]
                    rows.append(_explicit_row(rel, depth, new_chain, new_elements, to_node))
                    next_frontier[to_node] = {"chain": new_chain, "chain_elements": new_elements}
                    visited.add(to_node)
            if direction in ("upstream", "both") and rel.target_id in frontier:
                to_node = rel.source_id
                if to_node not in visited:
                    prior = frontier[rel.target_id]
                    new_chain = prior["chain"] + [rel.id]
                    new_elements = prior["chain_elements"] + [to_node]
                    rows.append(_explicit_row(rel, depth, new_chain, new_elements, to_node))
                    next_frontier[to_node] = {"chain": new_chain, "chain_elements": new_elements}
                    visited.add(to_node)
        frontier = next_frontier

    return rows


def _filter_explicit_by_layer(rows: List[Dict[str, Any]], layer: str) -> List[Dict[str, Any]]:
    from app.models import ArchiMateElement

    element_ids = set()
    for row in rows:
        element_ids.update(row["_endpoints"])
    if not element_ids:
        return []
    layers = dict(
        db.session.execute(
            db.select(ArchiMateElement.id, ArchiMateElement.layer).where(
                ArchiMateElement.id.in_(element_ids)
            )
        ).all()
    )
    return [
        row
        for row in rows
        if any(layers.get(eid) == layer for eid in row["_endpoints"])
    ]


def _derived_row(fact: Dict[str, Any], root_id: int) -> Dict[str, Any]:
    if fact["source_element_id"] == root_id:
        element_id = fact["target_element_id"]
    else:
        element_id = fact["source_element_id"]
    return {
        "element_id": element_id,
        # Internal join key (popped before return, like the explicit rows'):
        # the fact's own endpoints, so ``_attach_plain_terms`` can name both
        # ends once the identity map exists.
        "_endpoints": (fact["source_element_id"], fact["target_element_id"]),
        "relation": {
            "kind": "derived",
            "type": fact["derived_type"],
            "depth": fact["depth"],
            "rule_id": fact["rule_id"],
            "chain": fact["chain"],
            "chain_elements": fact["chain_element_ids"],
            "confidence": fact["confidence"],
            "provenance": fact["provenance"],
            "computed_at": fact["computed_at"],
            "stale": fact["stale"],
            # ``derived_id`` and ``engine_version`` come straight from the dict
            # ``list_derived_facts`` returns.
            "derived_id": fact["id"],
            "engine_version": fact["engine_version"],
            # Filled by ``_attach_plain_terms`` once the identity map exists.
            "plain_terms": None,
        },
        "reason": fact.get("reason"),
    }


def _derived_filter_args(
    element_id: int, direction: str
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Translate US-1's (element_id, direction) into ``list_derived_facts``'s
    (source_element_id, target_element_id, direction) calling convention.

    ``direction="both"`` passes the SAME id as both filters -- the exact
    convention ``list_derived_facts`` documents for its ``source = X OR
    target = X`` branch.
    """
    if direction == "downstream":
        return element_id, None, None
    if direction == "upstream":
        return None, element_id, None
    return element_id, element_id, "both"


def _maturity_flags(
    reason: Optional[str],
    maturity_by_element: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The whole-answer ``maturity_flags`` block: four keys on every branch,
    never fewer.

    ``maturity_by_element`` is only ever passed non-empty from the one branch
    that actually resolved a Capability's block -- the two lists are the
    sorted element ids whose maturity block has ``assessed is False`` /
    ``under_target is True`` respectively (an id can be in at most one), and
    ``reason`` is ``None``: something was genuinely measured, even if both
    lists come back empty. Every other caller -- no Capability anywhere in
    the result, no tenant context, the element itself not found -- passes
    only *reason*: both lists stay ``None``, never an empty list standing in
    for "measured, found none" on a branch that measured nothing at all.
    """
    if maturity_by_element:
        return {
            "unassessed_capability_ids": sorted(
                eid for eid, block in maturity_by_element.items() if block["assessed"] is False
            ),
            "under_target_capability_ids": sorted(
                eid for eid, block in maturity_by_element.items() if block["under_target"] is True
            ),
            "reason": None,
            "maturity_source": "unified_capabilities",
        }
    return {
        "unassessed_capability_ids": None,
        "under_target_capability_ids": None,
        "reason": reason,
        "maturity_source": "unified_capabilities",
    }


def _not_computed_counts() -> Dict[str, Any]:
    """T-005 task 02 acceptance item 12: the not-computed branch's null
    counts, isolated as their own seam (same pattern as
    ``_include_derived_gate`` / ``_sec09_tenant_check`` above and
    ``derived_facts.py``'s ``_apply_default_staleness_filter``) so the
    mutation-proof test can monkeypatch exactly this function to emit ``0``
    instead of ``None`` and confirm the not-computed/measured-zero
    distinguishability test goes red, without editing source under test.
    """
    return {
        "explicit_count": None,
        "derived_count": None,
        "ratio": None,
        "computed_at": None,
        "engine_version": None,
        "stale_count": None,
        "last_recompute_duration_ms": None,
    }


class IntelligenceQueryService:
    """DE-9: read-only cross-layer intelligence queries."""

    @staticmethod
    def derivation_yield(organization_id: int) -> Dict[str, Any]:
        """"How much does derivation add", for one tenant.

        p95 is read from the impact-query histogram at a PINNED selector via
        a bucket-edge read -- never computed in application code, never
        widened, never aggregated across label values. It is process-local
        and estate-wide, not per tenant, so it is reported as its own
        nested, self-describing block on BOTH branches (it measures query
        latency, not derivation -- suppressing it on the not-computed branch
        would hide a real breach).

        ``explicit_count``/``derived_count``/``ratio``/
        ``last_recompute_duration_ms`` come from the tenant's
        ``DerivationRun`` row itself -- the SAME values the recompute
        response already returns (two surfaces, one answer; a store-
        agreement test pins this). ``computed_at``/``engine_version``/
        ``stale_count`` come from ``derived_fact_aggregates`` -- the store's
        OWN current state (never the ``ENGINE_VERSION`` module constant).
        """
        from app.modules.intelligence.services.derived_facts import (
            derived_fact_aggregates,
            latest_derivation_run,
        )
        from app.modules.intelligence.services.latency_probe import read_p95_bucket_edge
        from app.modules.intelligence.services.observability import record_shape_b_trigger

        # D2: this call is wrapped in its OWN series (query="derivation_yield")
        # -- the p95 read below is pinned to "cross_layer_impact" only, and is
        # therefore unaffected by calling this endpoint repeatedly.
        with record_query_latency("derivation_yield") as scope:
            scope.organization_id = organization_id

            p95_read = read_p95_bucket_edge(
                query=NFR5_QUERY, depth=NFR5_DEPTH, include_derived=NFR5_INCLUDE_DERIVED
            )
            p95_block: Dict[str, Any] = {
                "latency_seconds": p95_read["latency_seconds"],
                "sample_count": p95_read["sample_count"],
                "scope": "process_estate_wide",
                "query": NFR5_QUERY,
                "depth": int(NFR5_DEPTH),
                "include_derived": True,
                "reason": p95_read["reason"],
            }
            if p95_read.get("p95_exceeds_seconds") is not None:
                p95_block["p95_exceeds_seconds"] = p95_read["p95_exceeds_seconds"]

            # D3/D11: a real value OR the honest "exceeds the highest
            # declared bucket" fact both constitute a genuine breach signal
            # -- the Shape-B trigger must fire on either (task 02 acceptance
            # item 6), never only on the interpolated case.
            breach_value = p95_read["latency_seconds"]
            if breach_value is None and p95_read["reason"] == "p95_above_highest_bucket":
                breach_value = p95_read.get("p95_exceeds_seconds")

            shape_b_trigger = None
            if breach_value is not None and breach_value > SHAPE_B_THRESHOLD_SECONDS:
                record = record_shape_b_trigger(
                    measured_p95_seconds=breach_value,
                    threshold_seconds=SHAPE_B_THRESHOLD_SECONDS,
                    sample_count=p95_read["sample_count"],
                    query=NFR5_QUERY,
                    depth=NFR5_DEPTH,
                    include_derived=NFR5_INCLUDE_DERIVED,
                )
                shape_b_trigger = record.as_dict()

            run = latest_derivation_run(organization_id)
            if run is None:
                payload: Dict[str, Any] = {
                    "organization_id": organization_id,
                    "state": "not_computed",
                    "reason": DERIVATION_NOT_COMPUTED_REASON,
                    "reasons": [],
                    **_not_computed_counts(),
                    "p95": p95_block,
                    "shape_b_trigger": shape_b_trigger,
                }
            else:
                agg = derived_fact_aggregates(organization_id)
                payload = {
                    "organization_id": organization_id,
                    "state": "computed",
                    "explicit_count": run.explicit_count,
                    "derived_count": run.derived_count,
                    "ratio": float(run.ratio) if run.ratio is not None else None,
                    "computed_at": agg["computed_at"].isoformat() if agg["computed_at"] else None,
                    # D-5 (refuter): the derived-fact store's own computed_at
                    # is honestly null for a tenant whose latest run produced
                    # zero derived facts (nothing lands in
                    # archimate_derived_relationships to stamp). last_run_at
                    # is a distinct fact -- when derivation itself last
                    # genuinely ran, from intelligence_derivation_runs -- so a
                    # measured-zero tenant is not under-reporting a timestamp
                    # the system already has. Never repurposes computed_at,
                    # which still specifically describes store freshness.
                    "last_run_at": run.finished_at.isoformat() if run.finished_at else None,
                    "engine_version": agg["engine_versions"],
                    "stale_count": agg["stale_count"],
                    "last_recompute_duration_ms": run.duration_ms,
                    "p95": p95_block,
                    "shape_b_trigger": shape_b_trigger,
                    "reasons": [],
                }

        return payload

    @staticmethod
    def cross_layer_impact(
        element_id: int,
        *,
        include_derived: bool = True,
        include_stale: bool = False,
        max_depth: int = 3,
        direction: str = "downstream",
        layer: Optional[str] = None,
        with_owner: bool = True,
        cursor: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(VALID_DIRECTIONS)}")
        if not (1 <= max_depth <= 10):
            raise ValueError("max_depth must be between 1 and 10")

        org_id = current_org_id()

        with record_query_latency("cross_layer_impact") as scope:
            scope.organization_id = org_id
            scope.depth = max_depth
            scope.include_derived = include_derived

            # The identity map: empty on every branch that returns no rows,
            # otherwise filled below from one tenant-fenced batched select.
            elements: Dict[str, Dict[str, Any]] = {}

            if org_id is None:
                rows: List[Dict[str, Any]] = []
                summary = {
                    "explicit_count": 0,
                    "derived_count": 0,
                    "stale_count": 0,
                    "derivation_state": "not_computed",
                }
                reasons = [NO_TENANT_CONTEXT_REASON]
                maturity_flags = _maturity_flags(NO_TENANT_CONTEXT_REASON)
            else:
                from app.models import ArchiMateElement

                element = db.session.execute(
                    db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
                ).scalar_one_or_none()

                if element is None:
                    rows = []
                    summary = {
                        "explicit_count": 0,
                        "derived_count": 0,
                        "stale_count": 0,
                        "derivation_state": "not_computed",
                    }
                    reasons = [ELEMENT_NOT_FOUND_REASON]
                    maturity_flags = _maturity_flags(ELEMENT_NOT_FOUND_REASON)
                else:
                    explicit_rows = _walk_explicit(element_id, max_depth, direction)
                    if layer is not None:
                        explicit_rows = _filter_explicit_by_layer(explicit_rows, layer)

                    src, tgt, dir_kw = _derived_filter_args(element_id, direction)

                    # Fetched regardless of include_derived: the summary's
                    # derivation_state must always be a real measurement,
                    # never fabricated when include_derived=False.
                    all_matching_derived = list_derived_facts(
                        org_id,
                        include_stale=True,
                        source_element_id=src,
                        target_element_id=tgt,
                        max_depth=max_depth,
                        direction=dir_kw,
                        layer=layer,
                    )
                    if not all_matching_derived:
                        derivation_state = "not_computed"
                    elif all(r["stale"] for r in all_matching_derived):
                        derivation_state = "stale"
                    else:
                        derivation_state = "current"

                    derived_rows: List[Dict[str, Any]] = []
                    if _include_derived_gate(include_derived):
                        matching = list_derived_facts(
                            org_id,
                            include_stale=include_stale,
                            source_element_id=src,
                            target_element_id=tgt,
                            max_depth=max_depth,
                            direction=dir_kw,
                            layer=layer,
                        )
                        derived_rows = [_derived_row(fact, element_id) for fact in matching]

                    rows = explicit_rows + derived_rows

                    # Resolve every element id the result set can name in ONE
                    # batched select, INSIDE the latency scope so
                    # ``summary.latency_ms`` and the histogram measure it,
                    # then let the derived rows name both of their ends.
                    elements = _resolve_elements_batch(_element_ids_in_rows(rows), org_id)
                    _attach_plain_terms(rows, elements)

                    # The distinct Capability element ids the identity map
                    # above already resolved under the tenant predicate --
                    # rows' own elements and every id their chains pass
                    # through. An id absent from ``elements`` is never a
                    # capability here (the identity map's own tenant fence
                    # already excluded it), so a foreign element can never
                    # reach the helper. One batched call, inside this same
                    # latency scope, only when there is at least one
                    # Capability id to ask for.
                    capability_ids = sorted(
                        {
                            eid
                            for eid in _element_ids_in_rows(rows)
                            if elements.get(str(eid), {}).get("type") == "Capability"
                        }
                    )
                    maturity_by_element: Dict[int, Dict[str, Any]] = {}
                    if capability_ids:
                        maturity_by_element = CapabilityHeatmapService().maturity_for_elements(
                            capability_ids, organization_id=org_id
                        )

                    # M7 fix: batch owner resolution instead of N+1 --
                    # collect every distinct element id needing a lookup
                    # across the WHOLE result set first, then resolve in a
                    # small constant number of queries.
                    owners_by_element: Dict[int, Tuple[Optional[Dict[str, Any]], Optional[str]]] = {}
                    if with_owner and rows:
                        owners_by_element = _resolve_owners_batch(
                            [row["element_id"] for row in rows], org_id
                        )

                    for row in rows:
                        if with_owner:
                            owner, reason = owners_by_element.get(
                                row["element_id"], (None, NO_OWNERSHIP_REASON)
                            )
                        else:
                            owner, reason = None, None
                        row["owner"] = owner
                        # Derived rows may already carry a staleness reason
                        # (set by _derived_row, e.g. "derivation_stale"). That
                        # takes precedence; an owner-absence reason only fills
                        # in when the row has no reason of its own yet.
                        existing_reason = row.get("reason")
                        row["reason"] = existing_reason if existing_reason else reason
                        # A Capability row carries the block the helper
                        # already computed for it, untouched; any other row
                        # carries no ``maturity`` key at all --
                        # not applicable is not an absence, and the row's own
                        # ``reason`` above is unrelated to and unchanged by
                        # this block (the block carries its own reason).
                        if elements.get(str(row["element_id"]), {}).get("type") == "Capability":
                            row["maturity"] = maturity_by_element[row["element_id"]]
                            row["health"] = maturity_by_element[row["element_id"]]
                        else:
                            row["health"] = None
                        # NEW-5: keep element_id on every row -- this task's
                        # whole point is "what stops", so a row that cannot
                        # name what element it is about is not answering the
                        # question. Only the internal join key (_endpoints)
                        # is stripped.
                        row.pop("_endpoints", None)

                    explicit_count = sum(1 for r in rows if r["relation"]["kind"] == "explicit")
                    derived_count = sum(1 for r in rows if r["relation"]["kind"] == "derived")
                    stale_count = sum(1 for r in rows if r["relation"].get("stale"))

                    scope.explicit_rows = explicit_count
                    scope.derived_rows = derived_count
                    scope.stale_rows = stale_count

                    summary = {
                        "explicit_count": explicit_count,
                        "derived_count": derived_count,
                        "stale_count": stale_count,
                        "derivation_state": derivation_state,
                    }
                    reasons = []
                    # The whole-answer flags block: computed only when at
                    # least one Capability id was found (capability_ids
                    # non-empty, so maturity_by_element is non-empty too --
                    # the helper guarantees every id asked for is present);
                    # otherwise nothing was measured, so both lists stay
                    # None with the honest reason, never an empty list
                    # standing in for "measured, found none".
                    maturity_flags = _maturity_flags(NO_CAPABILITY_IN_CHAIN_REASON, maturity_by_element)

        summary["latency_ms"] = scope.latency_ms

        # Stable pagination: preserve canonical walk order (explicit rows
        # first in BFS order, then derived rows).  The cursor is a 0-based
        # index into the full unpaginated list; next_cursor is the index of
        # the first row that would appear on the next page.
        total = len(rows)
        next_cursor = None
        if page_size is not None and page_size > 0:
            slice_start = cursor if cursor is not None else 0
            page = rows[slice_start : slice_start + page_size]
            if slice_start + page_size < total:
                next_cursor = slice_start + page_size
            rows = page

        return {
            "rows": rows,
            "summary": summary,
            "reasons": reasons,
            "elements": elements,
            "maturity_flags": maturity_flags,
            "total": total,
            "next_cursor": next_cursor,
        }

    @staticmethod
    def risk_for_element(
        element_id: int,
        *,
        max_depth: int = 3,
        include_derived: bool = True,
    ) -> Dict[str, Any]:
        """L6, "what could hurt <element>, and what does it touch": every
        ``Risk`` seeded directly on this element (``Risk.archimate_element_id
        == element_id``), each with the SAME blast-radius traversal
        ``cross_layer_impact`` already runs for L1 -- no second traversal
        algorithm, per the platform convention against parallel scoring
        logic.

        Scope note, not silently dropped: ``RiskEntityLink`` (a risk
        threatening an Application/Solution/Programme with no direct
        ``archimate_element_id`` of its own on this element) is not resolved
        here -- none of those three models carry an ArchiMate mirror id
        today, so there is no element to seed a traversal from. Only
        directly-mirrored risks are answered; an indirect risk is invisible
        to this query, not wrongly reported as "none". Tracked as a
        follow-up, not implemented speculatively.

        Score is a **display** label, not a stored fact (per
        ``intelligence-lenses-v1.md`` L6): each risk's own
        ``likelihood x impact`` is shown on its row; when a risk's blast
        radius reaches other elements, the chain's aggregate score is the
        single worst (max) risk reaching it, the conservative choice
        documented in the L3/L6 brief, not a summed exposure figure.
        """
        from app.models.risk import Risk

        org_id = current_org_id()

        with record_query_latency("risk_for_element") as scope:
            scope.organization_id = org_id

            if org_id is None:
                return {
                    "risks": [],
                    "reasons": [NO_TENANT_CONTEXT_REASON],
                    "elements": {},
                }

            from app.models import ArchiMateElement

            element = db.session.execute(
                db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
            ).scalar_one_or_none()
            if element is None:
                return {
                    "risks": [],
                    "reasons": [ELEMENT_NOT_FOUND_REASON],
                    "elements": {},
                }

            seed_risks = (
                db.session.execute(
                    db.select(Risk).where(Risk.archimate_element_id == element_id)
                )
                .scalars()
                .all()
            )

            if not seed_risks:
                return {
                    "risks": [],
                    "reasons": [NO_RISK_RECORDED_REASON],
                    "elements": {},
                }

            all_elements: Dict[str, Dict[str, Any]] = {}
            risk_payloads: List[Dict[str, Any]] = []
            for risk in seed_risks:
                blast = IntelligenceQueryService.cross_layer_impact(
                    element_id,
                    include_derived=include_derived,
                    max_depth=max_depth,
                    with_owner=True,
                )
                all_elements.update(blast.get("elements") or {})
                risk_payloads.append(
                    {
                        "risk_id": risk.id,
                        "title": risk.title,
                        "status": risk.status.value if risk.status else None,
                        "likelihood": risk.likelihood,
                        "impact": risk.impact,
                        "risk_score": risk.risk_score,
                        "risk_level": risk.risk_level,
                        "owner": risk.owner,
                        "mitigation_plan": risk.mitigation_plan,
                        "affected_rows": blast.get("rows", []),
                        "affected_summary": blast.get("summary", {}),
                    }
                )

        return {"risks": risk_payloads, "reasons": [], "elements": all_elements}

    @staticmethod
    def portfolio_component_for_element(element_id: int) -> Dict[str, Any]:
        """L3, "what do we run, what does it cost, who owns it, what's
        duplicated?": resolves an element to the ``ApplicationComponent``
        row the rationalization/duplicate/TCO pages are keyed on, so the
        caller can build the ONE genuine deep link that exists today
        (``unified_applications.rationalization_planning``).

        No query of its own beyond that resolution -- reuses the exact
        dual-lookup already established in
        ``app/modules/solutions_strategic/v2/routes/strategic_routes.py``
        (the element's own ``application_component_id`` FK first, the
        reverse ``ApplicationComponent.archimate_element_id`` lookup for
        legacy rows second) rather than inventing a second answer to the
        same question.

        Duplicate-detection and TCO history were checked against this same
        brief and found to have NO per-application HTML page today (both
        are JSON-only API endpoints, `GET .../enterprise/analysis/<id>` and
        `GET /api/advanced-tco/history` keyed by `vendor_product_id` not an
        element/app id) -- so this method, deliberately, resolves only what
        the one real page needs. Linking to a JSON response would not be a
        deep link a person can read; not built.

        Beside ``application_component_id``, the answer carries a
        ``component`` block: the resolved component's name, its
        owner-recorded health, its entered cost figures, its latest
        fiscal-period cost row and its licence entitlements -- built below
        off the SAME ``component`` object resolved above (no second
        component select), in exactly two more selects of its own. Every
        early branch below returns ``component: None`` -- nothing was
        resolved, and the branch's own reason already says why.
        """
        from app.models import ArchiMateElement
        from app.models.application_portfolio import ApplicationComponent

        def _health_block(component) -> Dict[str, Any]:
            """The ``health`` key: the owner-recorded ``health_status``
            column carried exactly as recorded -- a recorded word, not a
            computed health score (the reuse register's health-score
            concept is a different, unrelated reader). ``None`` means not
            assessed, per the model's own comment on the column; no default
            status is ever invented.
            """
            status = component.health_status
            return {
                "status": status,
                "reason": None if status is not None else NO_HEALTH_RECORDED_REASON,
                "truth_class": "authoritative_fact",
            }

        def _cost_block(component) -> Dict[str, Any]:
            """The ``cost`` key: the seven entered cost figures, read off
            *component* -- the object the caller already holds, no select
            of its own. ``total_cost_of_ownership`` is the entered annual
            TCO figure exactly as recorded; nothing here sums, averages or
            derives it from the other six. ``license_cost`` is not read
            (superseded by ``license_cost_annual``, per the model's own
            comment) and neither is ``roi_score`` (a self-rated column, not
            an intelligence fact). ``implementation_cost`` is the one
            one-time figure among the six annual ones; it is carried
            through unmixed, never summed with the rest.
            """
            figures = {
                "total_cost_of_ownership": component.total_cost_of_ownership,
                "license_cost_annual": component.license_cost_annual,
                "maintenance_cost": component.maintenance_cost,
                "infrastructure_cost": component.infrastructure_cost,
                "support_cost": component.support_cost,
                "implementation_cost": component.implementation_cost,
                "development_cost_annual": component.development_cost_annual,
            }
            all_absent = all(value is None for value in figures.values())
            return {
                **figures,
                "basis": "annual_as_entered",
                "reason": NO_COST_RECORDED_REASON if all_absent else None,
                "access_reason": None,
                "truth_class": "authoritative_fact",
            }

        def _cost_by_period_block(component, org_id: int) -> Dict[str, Any]:
            """The ``cost_by_period`` key: the single latest
            ``ApplicationCost`` row for *component* -- the first of this
            method's two remaining selects, ordered newest fiscal
            year/quarter first, one row only regardless of how many
            periods exist. ``variance`` is the stored column, disclosed
            only when both ``total_cost`` and ``total_budget`` on that same
            row are themselves recorded -- nothing here recomputes it from
            the two; a stored ``variance`` is withheld, not recalculated,
            when either input is absent. Joins through ``ApplicationComponent``
            for an explicit ``organization_id ==`` predicate (same rationale
            as ``_licence_entries``'s ``_licence_tenant_predicate`` -- the FK
            from ``application_costs`` to ``application_components`` carries
            no tenant check of its own, so this is what keeps the read
            correct when called with no ambient request context).
            """
            from app.models.enterprise_intelligence import ApplicationCost

            row = (
                db.session.execute(
                    db.select(ApplicationCost)
                    .join(ApplicationComponent, ApplicationCost.application_id == ApplicationComponent.id)
                    .where(
                        ApplicationCost.application_id == component.id,
                        ApplicationComponent.organization_id == org_id,
                    )
                    .order_by(
                        ApplicationCost.fiscal_year.desc(),
                        ApplicationCost.fiscal_quarter.desc().nulls_last(),
                        ApplicationCost.id.desc(),
                    )
                )
                .scalars()
                .first()
            )

            if row is None:
                return {
                    "fiscal_year": None,
                    "fiscal_quarter": None,
                    "total_cost": None,
                    "total_budget": None,
                    "variance": None,
                    "reason": NO_COST_RECORDED_REASON,
                    "access_reason": None,
                }

            total_cost = float(row.total_cost) if row.total_cost is not None else None
            total_budget = float(row.total_budget) if row.total_budget is not None else None
            variance = (
                float(row.variance)
                if row.variance is not None and total_cost is not None and total_budget is not None
                else None
            )
            return {
                "fiscal_year": row.fiscal_year,
                "fiscal_quarter": row.fiscal_quarter,
                "total_cost": total_cost,
                "total_budget": total_budget,
                "variance": variance,
                "reason": None,
                "access_reason": None,
            }

        def _licence_entries(component, org_id: int) -> List[Dict[str, Any]]:
            """The ``licences`` key: every ``LicenseEntitlement`` row for
            *component* -- this method's second remaining select, with its
            own tenant predicate isolated in ``_licence_tenant_predicate``
            (the FK to ``application_components`` carries no tenant check
            of its own, so that predicate is load-bearing here, not
            decorative). ``under_used`` compares two recorded integers --
            never a difference, never a dollar figure for what is not
            deployed or not used -- and only once the licence's usage has
            actually been synced; see ``LICENCE_USAGE_NOT_SYNCED_REASON``
            for the honest absence reported when it has not.
            """
            from app.models.license_entitlement import LicenseEntitlement

            rows = (
                db.session.execute(
                    db.select(LicenseEntitlement)
                    .where(
                        LicenseEntitlement.application_id == component.id,
                        _licence_tenant_predicate(org_id),
                    )
                    .order_by(LicenseEntitlement.id)
                )
                .scalars()
                .all()
            )

            entries: List[Dict[str, Any]] = []
            for row in rows:
                if row.last_usage_sync is None:
                    under_used = None
                    under_used_reason = LICENCE_USAGE_NOT_SYNCED_REASON
                else:
                    under_used = row.quantity_used < row.quantity_entitled
                    under_used_reason = None
                entries.append(
                    {
                        "entitlement_id": row.id,
                        "product_name": row.product_name,
                        "license_metric": row.license_metric,
                        "quantity_entitled": row.quantity_entitled,
                        "quantity_deployed": row.quantity_deployed,
                        "quantity_used": row.quantity_used,
                        "under_used": under_used,
                        "under_used_reason": under_used_reason,
                        "unit_cost": float(row.unit_cost) if row.unit_cost is not None else None,
                        "compliance_status": row.compliance_status,
                        "access_reason": None,
                    }
                )
            return entries

        org_id = current_org_id()
        if org_id is None:
            return {
                "application_component_id": None,
                "reasons": [NO_TENANT_CONTEXT_REASON],
                "component": None,
            }

        element = db.session.execute(
            db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
        ).scalar_one_or_none()
        if element is None:
            return {
                "application_component_id": None,
                "reasons": [ELEMENT_NOT_FOUND_REASON],
                "component": None,
            }

        component = None
        if getattr(element, "application_component_id", None):
            component = db.session.get(ApplicationComponent, element.application_component_id)
        if component is None and (element.type or "") == "ApplicationComponent":
            component = ApplicationComponent.query.filter_by(archimate_element_id=element.id).first()

        if component is None:
            return {
                "application_component_id": None,
                "reasons": [NO_APPLICATION_COMPONENT_REASON],
                "component": None,
            }

        licence_entries = _licence_entries(component, org_id)
        component_block = {
            "name": component.name,
            "health": _health_block(component),
            "cost": _cost_block(component),
            "cost_by_period": _cost_by_period_block(component, org_id),
            "licences": licence_entries if licence_entries else None,
            "licences_reason": None if licence_entries else NO_LICENCE_RECORDED_REASON,
        }

        return {
            "application_component_id": component.id,
            "reasons": [],
            "component": component_block,
        }

    @staticmethod
    def _owner_user_tenant_predicate(org_id):
        """The explicit ``User.organization_id == org_id`` predicate on the
        work package owner lookup, isolated as its own seam so a mutation
        test can replace it and watch the foreign-owner test go red.

        ``User`` carries no ``TenantMixin``, so the ORM listener does not
        fence it. Without this, a work package whose ``owner_id`` names a
        user in another organisation would return that user's name or
        email. A user with no organisation is excluded (fail closed).
        """
        from app.models.user import User

        return User.organization_id == org_id

    @staticmethod
    def programme_for_element(
        element_id: int,
        *,
        max_depth: int = 3,
        include_derived: bool = True,
    ) -> Dict[str, Any]:
        """L5, "what are we changing, is it on time and on budget, what does
        each change touch, where does it land and what gap does it close?":
        every ``UnifiedWorkPackage`` seeded directly on the picked element
        (``archimate_element_id`` FK), each with the SAME blast-radius
        traversal L1/L6 already run -- no second traversal algorithm.

        Tenant-safety note, verified not assumed: ``UnifiedWorkPackage``
        carries no ``TenantMixin``/``organization_id`` of its own. This
        method never lists work packages independently of an element --
        every row it returns is filtered by ``archimate_element_id ==
        element_id``, and ``element_id`` is only ever reached here after
        the element itself was confirmed to belong to the caller's tenant
        (below). A cross-tenant work package cannot share a seed element id
        with the wrong org's element, since ``archimate_elements.id`` is a
        real primary key each row of which belongs to exactly one tenant.
        This does not make ``UnifiedWorkPackage`` itself tenant-safe for any
        OTHER read path against it -- flagged as a separate, pre-existing
        gap in the L5 brief, not fixed here.

        Cost variance is read from the model's own ``estimated_cost``/
        ``actual_cost`` fields directly, NOT via
        ``UnifiedWorkPackage.calculate_budget_variance()`` -- that helper
        returns a bare 0 both when there is no cost data and when the
        package is exactly on budget, the same not-computed-vs-measured-
        zero collision CLAUDE.md's no-fabrication rule exists to catch.
        Variance is only reported when ``estimated_cost`` is a real
        positive number; otherwise the row carries the honest
        ``not_costed`` reason.

        Plateau and gap: the work package's plateau and gap are its ArchiMate
        relationships (read through ``work_package_service.plateau_and_gap_links``),
        then resolved against the ``Plateau``/``Gap`` tables (each carrying its
        own ``TenantMixin``) in two selects, tenant-scoped explicitly, in
        addition to the ORM listener -- a foreign-tenant row a work package
        happens to point at (the FK itself is not tenant-checked) simply
        does not come back, and reads exactly like an unset FK: every field
        ``None`` beside ``no_plateau_recorded``/``no_gap_recorded``. A
        ``plateau_transition`` gap lists its own ``originating_plateau_id``/
        ``target_plateau_id`` as the recorded ids -- no name lookup for
        them. ``UnifiedWorkPackage.priority``/``risk_level`` and
        ``Gap.resolution_status`` all carry a column default
        (``"medium"``/``"medium"``/``"identified"``), so a stored value
        cannot be told from an entered one; the value is carried as
        recorded either way, and ``risk_level_default_possible``/
        ``priority_default_possible`` disclose the possibility rather than
        the read reinterpreting it. Every row of each package's own
        ``affected_rows`` also carries the element's own recorded
        classification (``plateau``: ``"Baseline"``/``"Target"``/
        ``"Transition"``/``None``, from ``ArchiMateElement.togaf_plateau``)
        in a third, separate select -- ``_resolve_elements_batch``'s
        four-key identity-map projection is not widened to carry it.
        ``is_baseline`` is read in that same select but not carried into the
        answer: it would duplicate ``plateau == "Baseline"`` with a
        ``False`` default that would misleadingly read as "confirmed not
        baseline" for an element nobody has classified yet.
        """
        from app.models import ArchiMateElement
        from app.models.unified_work_package import UnifiedWorkPackage

        org_id = current_org_id()

        with record_query_latency("programme_for_element") as scope:
            scope.organization_id = org_id

            if org_id is None:
                return {
                    "work_packages": [],
                    "reasons": [NO_TENANT_CONTEXT_REASON],
                    "elements": {},
                }

            element = db.session.execute(
                db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
            ).scalar_one_or_none()
            if element is None:
                return {
                    "work_packages": [],
                    "reasons": [ELEMENT_NOT_FOUND_REASON],
                    "elements": {},
                }

            seed_packages = (
                db.session.execute(
                    db.select(UnifiedWorkPackage).where(
                        UnifiedWorkPackage.archimate_element_id == element_id
                    )
                )
                .scalars()
                .all()
            )

            if not seed_packages:
                return {
                    "work_packages": [],
                    "reasons": [NO_WORK_PACKAGE_RECORDED_REASON],
                    "elements": {},
                }

            from app.models.user import User

            owner_ids = {wp.owner_id for wp in seed_packages if wp.owner_id}
            owners_by_id: Dict[int, str] = {}
            if owner_ids:
                for user in db.session.execute(
                    db.select(User)
                    .where(User.id.in_(owner_ids))
                    .where(IntelligenceQueryService._owner_user_tenant_predicate(org_id))
                ).scalars():

                    owners_by_id[user.id] = user.full_name() or user.email

            # cross_layer_impact is still called exactly once per seed
            # package, unchanged -- but every blast is collected here,
            # before the per-package payload loop below, so the plateau
            # select further down can name every element and chain element
            # across every package's rows in ONE batched read rather than
            # one per package.
            all_elements: Dict[str, Dict[str, Any]] = {}
            blasts: List[Dict[str, Any]] = []
            for wp in seed_packages:
                blast = IntelligenceQueryService.cross_layer_impact(
                    element_id,
                    include_derived=include_derived,
                    max_depth=max_depth,
                    with_owner=True,
                )
                all_elements.update(blast.get("elements") or {})
                blasts.append(blast)

            # Three selects beyond the base's, each a constant number
            # regardless of how many packages or rows this element has, and
            # skipped entirely when its own id set is empty. The element-id
            # set for the plateau-classification select below is read off
            # ``all_elements`` -- the SAME already tenant-filtered identity
            # map ``_resolve_elements_batch`` builds inside each blast above
            # -- rather than re-walking every row's raw ``element_id``/
            # ``chain_elements`` again: a foreign-tenant id that never
            # resolved into the tenant-filtered identity map never enters
            # this IN list either, so a foreign element's classification
            # is never even asked for, not merely filtered out of the
            # answer.
            # The plateau and gap a work package is linked to are its ArchiMate
            # relationships; the one reader gives them (first linked id of each).
            from app.services import work_package_service

            wp_links = work_package_service.plateau_and_gap_links(seed_packages, org_id)

            def _first_link(wp, key):
                ids = wp_links.get(wp.id, {}).get(key) or []
                return ids[0] if ids else None

            plateau_ids = {i for i in (_first_link(w, "plateau_ids") for w in seed_packages) if i is not None}
            gap_ids = {i for i in (_first_link(w, "gap_ids") for w in seed_packages) if i is not None}
            plateau_element_ids = {int(eid) for eid in all_elements}

            plateaus_by_id: Dict[int, Any] = {}
            if plateau_ids:
                from app.models.implementation_migration import Plateau

                for plateau in (
                    db.session.execute(
                        db.select(Plateau)
                        .where(Plateau.id.in_(plateau_ids), Plateau.organization_id == org_id)
                        .order_by(Plateau.id)
                    )
                    .scalars()
                    .all()
                ):
                    plateaus_by_id[plateau.id] = plateau

            gaps_by_id: Dict[int, Any] = {}
            if gap_ids:
                from app.models.implementation_migration import Gap

                for gap in (
                    db.session.execute(
                        db.select(Gap)
                        .where(Gap.id.in_(gap_ids), Gap.organization_id == org_id)
                        .order_by(Gap.id)
                    )
                    .scalars()
                    .all()
                ):
                    gaps_by_id[gap.id] = gap

            elements_plateau_by_id: Dict[int, Optional[str]] = {}
            if plateau_element_ids:
                for eid, togaf_plateau, _is_baseline in db.session.execute(
                    db.select(
                        ArchiMateElement.id,
                        ArchiMateElement.togaf_plateau,
                        ArchiMateElement.is_baseline,
                    ).where(
                        ArchiMateElement.id.in_(plateau_element_ids),
                        ArchiMateElement.organization_id == org_id,
                    ).order_by(ArchiMateElement.id)
                ).all():
                    elements_plateau_by_id[eid] = togaf_plateau

            wp_payloads: List[Dict[str, Any]] = []
            for wp, blast in zip(seed_packages, blasts):
                rows = blast.get("rows", [])
                for row in rows:
                    plateau_value = elements_plateau_by_id.get(row["element_id"])
                    if plateau_value is None:
                        row["plateau"] = None
                        row["plateau_reason"] = NO_PLATEAU_RECORDED_REASON
                    else:
                        row["plateau"] = plateau_value
                        row["plateau_reason"] = None

                if wp.estimated_cost and wp.estimated_cost > 0:
                    cost_variance_pct = (
                        (wp.actual_cost or 0.0) - wp.estimated_cost
                    ) / wp.estimated_cost * 100
                    cost_reason = None
                else:
                    cost_variance_pct = None
                    cost_reason = NOT_COSTED_REASON

                plateau_row = plateaus_by_id.get(_first_link(wp, "plateau_ids"))
                if plateau_row is None:
                    plateau_block: Dict[str, Any] = {
                        "plateau_id": None,
                        "name": None,
                        "target_date": None,
                        "sequence_order": None,
                        "baseline_plateau_id": None,
                        "reason": NO_PLATEAU_RECORDED_REASON,
                    }
                else:
                    plateau_block = {
                        "plateau_id": plateau_row.id,
                        "name": plateau_row.name,
                        "target_date": plateau_row.target_date.isoformat()
                        if plateau_row.target_date
                        else None,
                        "sequence_order": plateau_row.sequence_order,
                        "baseline_plateau_id": plateau_row.baseline_plateau_id,
                        "reason": None,
                    }

                gap_row = gaps_by_id.get(_first_link(wp, "gap_ids"))
                if gap_row is None:
                    gap_block: Dict[str, Any] = {
                        "gap_id": None,
                        "name": None,
                        "gap_kind": None,
                        "gap_type": None,
                        "resolution_status": None,
                        "resolution_status_default_possible": True,
                        "originating_plateau_id": None,
                        "target_plateau_id": None,
                        "owner_text": None,
                        "estimated_cost": None,
                        "access_reason": None,
                        "reason": NO_GAP_RECORDED_REASON,
                    }
                else:
                    gap_block = {
                        "gap_id": gap_row.id,
                        "name": gap_row.name,
                        "gap_kind": gap_row.gap_kind,
                        "gap_type": gap_row.gap_type,
                        "resolution_status": gap_row.resolution_status,
                        "resolution_status_default_possible": True,
                        "originating_plateau_id": gap_row.originating_plateau_id,
                        "target_plateau_id": gap_row.target_plateau_id,
                        "owner_text": gap_row.owner,
                        "estimated_cost": gap_row.estimated_cost,
                        "access_reason": None,
                        "reason": None,
                    }

                wp_payloads.append(
                    {
                        "work_package_id": wp.id,
                        "name": wp.name,
                        "status": wp.status,
                        "progress_percentage": wp.progress_percentage,
                        "start_date": wp.start_date.isoformat() if wp.start_date else None,
                        "end_date": wp.end_date.isoformat() if wp.end_date else None,
                        "is_overdue": wp.is_overdue(),
                        "owner": owners_by_id.get(wp.owner_id),
                        "cost_variance_pct": cost_variance_pct,
                        "cost_reason": cost_reason,
                        "plateau": plateau_block,
                        "gap": gap_block,
                        "risk_level": wp.risk_level,
                        "priority": wp.priority,
                        "risk_mitigation": wp.risk_mitigation,
                        "risk_level_default_possible": True,
                        "priority_default_possible": True,
                        "affected_rows": rows,
                        "affected_summary": blast.get("summary", {}),
                    }
                )

        return {"work_packages": wp_payloads, "reasons": [], "elements": all_elements}

    @staticmethod
    def strategy_for_element(
        element_id: int,
        *,
        max_depth: int = 3,
        include_derived: bool = True,
    ) -> Dict[str, Any]:
        """L2, "what are we trying to achieve, and how's it tracking?": every
        ``PortfolioInitiative`` seeded directly on the picked element
        (``archimate_element_id`` FK), each with the SAME blast-radius
        traversal L1/L5/L6 already run -- no second traversal algorithm.

        Tenant-safety note, verified not assumed: ``PortfolioInitiative``
        carries no ``TenantMixin``/``organization_id`` of its own, the same
        gap ``UnifiedWorkPackage`` has (L5 brief). This method never lists
        initiatives independently of an element -- every row it returns is
        filtered by ``archimate_element_id == element_id``, and
        ``element_id`` is only ever reached here after the element itself
        was confirmed to belong to the caller's tenant (below). A
        cross-tenant initiative cannot share a seed element id with the
        wrong org's element, since ``archimate_elements.id`` is a real
        primary key each row of which belongs to exactly one tenant. This
        does not make ``PortfolioInitiative`` itself tenant-safe for any
        OTHER read path against it -- a separate, pre-existing gap, not
        fixed here (same category already flagged once for
        ``UnifiedWorkPackage`` in the L5 brief).

        Budget variance is read from the model's own ``total_budget``/
        ``spent_to_date`` fields directly -- ``PortfolioInitiative`` has no
        wrapping helper method to avoid, unlike L5's
        ``calculate_budget_variance()``, but the same not-computed-vs-
        measured-zero discipline still applies: variance is only reported
        when ``total_budget`` is a real positive number, else the row
        carries the honest ``no_budget_recorded`` reason.
        """
        from app.models import ArchiMateElement
        from app.models.enterprise_intelligence import PortfolioInitiative

        org_id = current_org_id()

        with record_query_latency("strategy_for_element") as scope:
            scope.organization_id = org_id

            if org_id is None:
                return {
                    "initiatives": [],
                    "reasons": [NO_TENANT_CONTEXT_REASON],
                    "elements": {},
                }

            element = db.session.execute(
                db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
            ).scalar_one_or_none()
            if element is None:
                return {
                    "initiatives": [],
                    "reasons": [ELEMENT_NOT_FOUND_REASON],
                    "elements": {},
                }

            seed_initiatives = (
                db.session.execute(
                    db.select(PortfolioInitiative).where(
                        PortfolioInitiative.archimate_element_id == element_id
                    )
                )
                .scalars()
                .all()
            )

            if not seed_initiatives:
                return {
                    "initiatives": [],
                    "reasons": [NO_INITIATIVE_LINKED_REASON],
                    "elements": {},
                }

            all_elements: Dict[str, Dict[str, Any]] = {}
            initiative_payloads: List[Dict[str, Any]] = []
            for initiative in seed_initiatives:
                blast = IntelligenceQueryService.cross_layer_impact(
                    element_id,
                    include_derived=include_derived,
                    max_depth=max_depth,
                    with_owner=True,
                )
                all_elements.update(blast.get("elements") or {})

                if initiative.total_budget and initiative.total_budget > 0:
                    # total_budget/spent_to_date are Numeric (Decimal) columns,
                    # unlike UnifiedWorkPackage's Float cost fields -- cast to
                    # float before arithmetic so the response carries a plain
                    # JSON number, not a string (Flask's JSON provider
                    # serialises Decimal as str, which would silently break
                    # every numeric consumer of this field, front end
                    # included).
                    total_budget = float(initiative.total_budget)
                    spent_to_date = float(initiative.spent_to_date or 0.0)
                    budget_variance_pct = (spent_to_date - total_budget) / total_budget * 100
                    budget_reason = None
                else:
                    budget_variance_pct = None
                    budget_reason = NO_BUDGET_RECORDED_REASON

                initiative_payloads.append(
                    {
                        "initiative_id": initiative.id,
                        "name": initiative.name,
                        "status": initiative.status,
                        "priority": initiative.priority,
                        "health_status": initiative.health_status,
                        "completion_percentage": initiative.completion_percentage,
                        "start_date": initiative.start_date.isoformat()
                        if initiative.start_date
                        else None,
                        "target_end_date": initiative.target_end_date.isoformat()
                        if initiative.target_end_date
                        else None,
                        "executive_sponsor": initiative.executive_sponsor,
                        "program_manager": initiative.program_manager,
                        "budget_variance_pct": budget_variance_pct,
                        "budget_reason": budget_reason,
                        "success_metrics": [
                            {
                                "metric_name": m.metric_name,
                                "metric_type": m.metric_type,
                                "target_value": m.target_value,
                                "actual_value": m.actual_value,
                                "status": m.status,
                            }
                            for m in initiative.success_metrics
                        ],
                        "affected_rows": blast.get("rows", []),
                        "affected_summary": blast.get("summary", {}),
                    }
                )

        return {"initiatives": initiative_payloads, "reasons": [], "elements": all_elements}

    @staticmethod
    def _raci_tenant_predicate(model, organization_id: int):
        """The explicit ``organization_id ==`` predicate on both RACI selects.

        For ``UnifiedCapability`` this is the strict predicate: a shared
        reference-catalogue row (``organization_id IS NULL``) is not this
        organisation's capability and must never supply rows. Kept as its own
        seam so the cross-tenant mutation proof can replace exactly this one
        function with a no-op and confirm the named test goes red.
        """
        return model.organization_id == organization_id

    @staticmethod
    def accountability_for_element(element_id: int) -> Dict[str, Any]:
        """L4, "who's accountable for ___, and can they take on more?":
        WITHDRAWN -- the ownership data source is decided, but no shared,
        tenant-safe reader for it exists yet, and this method's own first
        version shipped one anyway rather than using the one that already
        existed. Not a "still undecided" state; a "not built safely yet"
        one, and the two must not be conflated in copy or reason naming.

        The original version re-implemented the element -> component ->
        ownership -> unit chain that ``_resolve_owners_batch``/
        ``_sec09_tenant_check`` already provide (``cross_layer_impact``'s
        own owner field), without that function's tenant assertion. It also
        had a real, unreviewed tenant-isolation gap of its own:
        ``OrganizationUnit`` carries no ``TenantMixin``/``organization_id``,
        and the original fetched it by ``organization_unit_id`` with no
        tenant predicate at all, so a cross-tenant-seeded
        ``organization_unit_id`` on an otherwise correctly-scoped
        ``ApplicationOwnership`` row would have leaked another
        organisation's unit name/type/head-of-unit -- not caught by this
        lens's own tests, which only exercised the element-level
        cross-tenant case. It also showed expired ownership (no
        ``end_date`` filter) as current, and serialised PII fields
        (``contact_email``, ``head_of_unit``, ...) nothing in the template
        ever rendered.

        Withdrawing the read entirely -- no query against either table --
        rather than patching those in place, since the underlying gap
        (``OrganizationUnit`` has no tenant scoping of its own) needs a
        real, separate fix before ANY reader of it is safe, not just this
        one. The route, question card and tests stay in place so the lens
        is easy to re-enable once a shared, tenant-safe reader exists;
        only the query itself is disabled.

        What is answered instead, for a ``Capability`` element only: who is
        recorded against the capability that mirrors it, from the
        organisation's ``EnterpriseRaciAssignment`` rows -- a different table
        with its own tenant column, not the ownership tables above, and never
        presented as an application owner. The answer then carries a ``raci``
        block; for every other element type, and when there is no tenant
        context or the element is not found, the answer is exactly the
        withdrawn one and carries no ``raci`` key (not applicable is not an
        absence).

        The ``raci`` block has exactly six keys: ``capability_id``, ``rows``,
        ``accountable_count``, ``no_accountable``, ``reason`` and ``source``.
        Each row is as recorded (assignment id, stakeholder type, id and
        label, and the RACI letter); a null letter is carried as null and does
        not count as ``A``. A capability with rows but no ``A`` is a listed
        absence (``no_accountable`` true), not a defect and not a score. The
        stakeholder label is the recorded one; nothing is resolved to a person
        or role row.
        """
        # No record_query_latency wrapper -- the withdrawn answer has no
        # query to time, and sampling a constant into the NFR-5 latency series
        # would only dilute it with meaningless near-zero readings.
        answer: Dict[str, Any] = {
            "owners": [],
            "capacity_not_available": True,
            "reasons": [OWNERSHIP_READER_NOT_BUILT_REASON, CAPACITY_NOT_AVAILABLE_REASON],
        }

        org_id = current_org_id()
        if org_id is None:
            return answer

        from app.models import ArchiMateElement

        element = db.session.execute(
            db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
        ).scalar_one_or_none()
        if element is None or (element.type or "") != "Capability":
            return answer

        from app.models.organization_model import EnterpriseRaciAssignment
        from app.models.unified_capability import UnifiedCapability

        capability_id = db.session.execute(
            db.select(UnifiedCapability.id)
            .where(
                UnifiedCapability.archimate_element_id == element_id,
                IntelligenceQueryService._raci_tenant_predicate(UnifiedCapability, org_id),
            )
            .order_by(UnifiedCapability.id)
        ).scalars().first()

        if capability_id is None:
            answer["raci"] = {
                "capability_id": None,
                "rows": None,
                "accountable_count": None,
                "no_accountable": None,
                "reason": NO_CAPABILITY_IN_CHAIN_REASON,
                "source": "enterprise_raci_assignments",
            }
            return answer

        raci_rows = db.session.execute(
            db.select(
                EnterpriseRaciAssignment.id,
                EnterpriseRaciAssignment.stakeholder_type,
                EnterpriseRaciAssignment.stakeholder_id,
                EnterpriseRaciAssignment.stakeholder_name,
                EnterpriseRaciAssignment.raci,
            )
            .where(
                EnterpriseRaciAssignment.capability_id == capability_id,
                IntelligenceQueryService._raci_tenant_predicate(EnterpriseRaciAssignment, org_id),
            )
            .order_by(
                EnterpriseRaciAssignment.raci,
                EnterpriseRaciAssignment.stakeholder_name,
                EnterpriseRaciAssignment.id,
            )
        ).all()

        if not raci_rows:
            answer["raci"] = {
                "capability_id": capability_id,
                "rows": None,
                "accountable_count": None,
                "no_accountable": None,
                "reason": NO_RACI_RECORDED_REASON,
                "source": "enterprise_raci_assignments",
            }
            return answer

        entries = [
            {
                "assignment_id": row.id,
                "stakeholder_type": row.stakeholder_type,
                "stakeholder_id": row.stakeholder_id,
                "stakeholder_name": row.stakeholder_name,
                "raci": row.raci,
            }
            for row in raci_rows
        ]
        accountable_count = sum(1 for entry in entries if entry["raci"] == "A")
        answer["raci"] = {
            "capability_id": capability_id,
            "rows": entries,
            "accountable_count": accountable_count,
            "no_accountable": accountable_count == 0,
            "reason": None,
            "source": "enterprise_raci_assignments",
        }
        return answer

    # ------------------------------------------------------------------ #
    # L7: the Data lens.
    # ------------------------------------------------------------------ #

    _DATA_OBJECT_LIMIT = 200

    @staticmethod
    def _data_tenant_predicate(model, organization_id: int):
        """The explicit ``organization_id ==`` predicate on every read of the Data lens,
        isolated as its own seam so a mutation test can replace it and watch the
        cross-tenant tests go red. ``DataObject``, ``DataLineage`` and
        ``ArchiMateElement`` all carry ``TenantMixin``, so this is defence in depth in a
        request and what keeps a caller correct with no ambient request context."""
        return model.organization_id == organization_id

    @staticmethod
    def _lineage_other_end_visible(other_ids, organization_id: int) -> Dict[int, str]:
        """id -> name for the lineage endpoints that are elements of THIS organisation.

        The ORM filter fences the lineage row, not the element ids it names: a row can
        point at another organisation's element. An endpoint missing from the result is
        dropped by the caller and never named. Isolated as its own seam for the mutation
        proof, like the ownership seams."""
        from app.models import ArchiMateElement

        ids = {i for i in other_ids if i is not None}
        if not ids:
            return {}
        rows = db.session.execute(
            db.select(ArchiMateElement.id, ArchiMateElement.name)
            .where(ArchiMateElement.id.in_(ids))
            .where(IntelligenceQueryService._data_tenant_predicate(ArchiMateElement, organization_id))
        ).all()
        return {row[0]: row[1] for row in rows}

    @staticmethod
    def data_for_element(element_id: int) -> Dict[str, Any]:
        """L7, "what data does this hold or produce, who stewards it, and where does it
        flow?": the ``DataObject`` rows linked to the element (directly, or through its
        application component) and the lineage edges in and out of it.

        Absence is stated, never filled: no data object is ``no_data_recorded``; objects
        with neither a steward nor an owner recorded add ``no_steward_recorded``; no
        lineage edge adds ``no_lineage_recorded``. A missing figure stays ``None``.

        Steward and owner are FREE TEXT on the model, not users: they are returned as
        recorded, flagged ``recorded_as_text``, and never joined to ``User``. Sensitive or
        operational detail nobody asked for (``pii_fields``, ``storage_location``, schema
        and table names, access roles) is not returned. A lineage edge whose other end is
        not an element of this organisation is dropped, not named.
        """
        from datetime import datetime, timezone

        from app.models import ArchiMateElement
        from app.models.all_missing_models import DataLineage
        from app.models.application_layer import DataObject
        from app.models.application_portfolio import ApplicationComponent

        predicate = IntelligenceQueryService._data_tenant_predicate
        as_of = datetime.now(timezone.utc).isoformat()
        empty = {"data_objects": [], "flows": [], "as_of": as_of}

        org_id = current_org_id()
        if org_id is None:
            return {**empty, "reasons": [NO_TENANT_CONTEXT_REASON]}

        element = db.session.execute(
            db.select(ArchiMateElement)
            .where(ArchiMateElement.id == element_id)
            .where(predicate(ArchiMateElement, org_id))
        ).scalar_one_or_none()
        if element is None:
            return {**empty, "reasons": [ELEMENT_NOT_FOUND_REASON]}

        component_ids = list(db.session.execute(
            db.select(ApplicationComponent.id)
            .where(ApplicationComponent.archimate_element_id == element_id)
            .where(predicate(ApplicationComponent, org_id))
        ).scalars())
        if getattr(element, "application_component_id", None):
            component_ids.append(element.application_component_id)

        object_filter = DataObject.archimate_element_id == element_id
        if component_ids:
            object_filter = db.or_(object_filter, DataObject.application_component_id.in_(component_ids))
        objects = db.session.execute(
            db.select(DataObject)
            .where(object_filter)
            .where(predicate(DataObject, org_id))
            .order_by(DataObject.name, DataObject.id)
            .limit(IntelligenceQueryService._DATA_OBJECT_LIMIT)
        ).scalars().all()

        data_objects = []
        for obj in objects:
            steward = (obj.data_steward or "").strip() or None
            owner = (obj.data_owner or "").strip() or None
            data_objects.append({
                "id": obj.id,
                "name": obj.name,
                "data_type": obj.data_type,
                "data_classification": obj.data_classification,
                "is_master_data": bool(obj.is_master_data),
                "contains_pii": bool(obj.contains_pii),
                "gdpr_scope": bool(obj.gdpr_scope),
                "retention_period_days": obj.retention_period_days,
                "steward": steward,
                "owner": owner,
                "recorded_as_text": True,
            })

        edges = db.session.execute(
            db.select(DataLineage)
            .where(db.or_(
                DataLineage.archimate_element_id == element_id,
                DataLineage.target_archimate_element_id == element_id,
            ))
            .where(predicate(DataLineage, org_id))
            .order_by(DataLineage.id)
        ).scalars().all()
        others = {
            (e.target_archimate_element_id if e.archimate_element_id == element_id else e.archimate_element_id)
            for e in edges
        }
        visible = IntelligenceQueryService._lineage_other_end_visible(others, org_id)

        # Build elements map for all referenced elements (center + flow endpoints)
        elements = {}
        # Add center element
        elements[str(element_id)] = {
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "layer": element.layer,
        }
        # Add other elements from flows
        for other_id, other_name in visible.items():
            other_element = db.session.execute(
                db.select(ArchiMateElement)
                .where(ArchiMateElement.id == other_id)
                .where(predicate(ArchiMateElement, org_id))
            ).scalar_one_or_none()
            if other_element:
                elements[str(other_id)] = {
                    "id": other_element.id,
                    "name": other_element.name,
                    "type": other_element.type,
                    "layer": other_element.layer,
                }

        flows = []
        for edge in edges:
            outgoing = edge.archimate_element_id == element_id
            other_id = edge.target_archimate_element_id if outgoing else edge.archimate_element_id
            if other_id not in visible:
                continue
            flows.append({
                "direction": "out" if outgoing else "in",
                "other_element_id": other_id,
                "other_element_name": visible[other_id],
                "lineage_type": edge.lineage_type,
                "frequency": edge.frequency,
            })

        reasons = []
        if not data_objects:
            reasons.append(NO_DATA_RECORDED_REASON)
        elif all(o["steward"] is None and o["owner"] is None for o in data_objects):
            reasons.append(NO_STEWARD_RECORDED_REASON)
        if not flows:
            reasons.append(NO_LINEAGE_RECORDED_REASON)
        return {"data_objects": data_objects, "flows": flows, "elements": elements, "as_of": as_of, "reasons": reasons}

    # ------------------------------------------------------------------ #
    # Compliance (under L6): the controls an application is mapped to.
    # ------------------------------------------------------------------ #

    _COMPLIANCE_LIMIT = 200
    # A control in one of these states is reported as recorded; it is not flagged as missing evidence.
    _EVIDENCE_NOT_REQUIRED = frozenset({"not_applicable", "waived"})

    @staticmethod
    def _compliance_tenant_predicate(model, organization_id: int):
        """The explicit ``organization_id ==`` predicate on every tenant read of the Compliance
        question, isolated as its own seam for the mutation proof. ``ApplicationComponent``,
        ``ApplicationComplianceControl``, ``PolicyViolation`` and ``ComplianceStatus`` all
        carry ``TenantMixin``; this is defence in depth in a request and what keeps a caller
        correct with no ambient request context. The global ``ComplianceControl`` and
        ``RegulatoryFramework`` rows have no tenant column and are never read with it."""
        return model.organization_id == organization_id

    @staticmethod
    def compliance_for_element(element_id: int) -> Dict[str, Any]:
        """"Which regulations and controls apply to this, and which controls have no evidence
        of being met?": the controls the element's application is mapped to
        (``ApplicationComplianceControl``, the tenant bridge), their global control and
        framework names reached ONLY by the ids on those rows, the open policy violations
        against the application, and when it was last scanned.

        Nothing is inferred and no stored aggregate is shown: ``ComplianceStatus`` percentages
        and counts default to zero on a row that was never scanned, so only its last-scan time
        is returned. No mapped control is ``no_compliance_controls_recorded``, never "0%
        compliant". A control with neither ``evidence_url`` nor ``verified_date`` (and not
        ``not_applicable``/``waived``) adds ``no_control_evidence``. The evidence URL and the
        verifier are user-authored or personal and are not returned; only whether an URL is
        recorded is.
        """
        from datetime import datetime, timezone

        from app.models import ArchiMateElement
        from app.models.application_compliance import ApplicationComplianceControl
        from app.models.application_portfolio import ApplicationComponent
        from app.models.compliance_models import ComplianceControl, RegulatoryFramework
        from app.models.policy_monitoring import ArchitecturePolicy, ComplianceStatus, PolicyViolation

        predicate = IntelligenceQueryService._compliance_tenant_predicate
        as_of = datetime.now(timezone.utc).isoformat()
        empty = {"controls": [], "open_violations": [], "last_scan_at": None, "as_of": as_of}

        org_id = current_org_id()
        if org_id is None:
            return {**empty, "reasons": [NO_TENANT_CONTEXT_REASON]}

        element = db.session.execute(
            db.select(ArchiMateElement)
            .where(ArchiMateElement.id == element_id)
            .where(predicate(ArchiMateElement, org_id))
        ).scalar_one_or_none()
        if element is None:
            return {**empty, "reasons": [ELEMENT_NOT_FOUND_REASON]}

        component_id = None
        if getattr(element, "application_component_id", None):
            component_id = db.session.execute(
                db.select(ApplicationComponent.id)
                .where(ApplicationComponent.id == element.application_component_id)
                .where(predicate(ApplicationComponent, org_id))
            ).scalar_one_or_none()
        if component_id is None:
            component_id = db.session.execute(
                db.select(ApplicationComponent.id)
                .where(ApplicationComponent.archimate_element_id == element_id)
                .where(predicate(ApplicationComponent, org_id))
            ).scalars().first()
        if component_id is None:
            return {**empty, "reasons": [NO_APPLICATION_COMPONENT_REASON]}

        rows = db.session.execute(
            db.select(ApplicationComplianceControl, ComplianceControl, RegulatoryFramework)
            .outerjoin(ComplianceControl, ComplianceControl.id == ApplicationComplianceControl.control_id)
            .outerjoin(RegulatoryFramework, RegulatoryFramework.id == ComplianceControl.framework_id)
            .where(ApplicationComplianceControl.application_id == component_id)
            .where(predicate(ApplicationComplianceControl, org_id))
            .order_by(RegulatoryFramework.code, ComplianceControl.control_code, ApplicationComplianceControl.id)
            .limit(IntelligenceQueryService._COMPLIANCE_LIMIT)
        ).all()

        controls = []
        any_missing_evidence = False
        for mapping, control, framework in rows:
            status = mapping.implementation_status
            has_url = bool((mapping.evidence_url or "").strip())
            verified = mapping.verified_date is not None
            missing = (
                not has_url and not verified
                and status not in IntelligenceQueryService._EVIDENCE_NOT_REQUIRED
            )
            any_missing_evidence = any_missing_evidence or missing
            controls.append({
                "control_id": mapping.control_id,
                "code": control.control_code if control else None,
                "name": control.title if control else "Unknown control",
                "framework_code": framework.code if framework else None,
                "framework_name": framework.name if framework else None,
                "implementation_status": status,
                "evidence_url_recorded": has_url,
                "verified": verified,
                "verified_date": mapping.verified_date.isoformat() if mapping.verified_date else None,
                "no_evidence": missing,
            })

        violations = db.session.execute(
            db.select(PolicyViolation, ArchitecturePolicy.name)
            .outerjoin(ArchitecturePolicy, ArchitecturePolicy.id == PolicyViolation.policy_id)
            .where(PolicyViolation.entity_type == "application")
            .where(PolicyViolation.entity_id == component_id)
            .where(PolicyViolation.status == "open")
            .where(predicate(PolicyViolation, org_id))
            .order_by(PolicyViolation.detected_at.desc(), PolicyViolation.id)
            .limit(IntelligenceQueryService._COMPLIANCE_LIMIT)
        ).all()
        open_violations = [
            {
                "policy_name": name,
                "severity": violation.severity,
                "status": violation.status,
                "detected_at": violation.detected_at.isoformat() if violation.detected_at else None,
            }
            for violation, name in violations
        ]

        last_scan = db.session.execute(
            db.select(db.func.max(ComplianceStatus.last_scan_at))
            .where(ComplianceStatus.entity_type == "application")
            .where(ComplianceStatus.entity_id == component_id)
            .where(predicate(ComplianceStatus, org_id))
        ).scalar_one_or_none()

        reasons = []
        if not controls:
            reasons.append(NO_COMPLIANCE_CONTROLS_RECORDED_REASON)
        elif any_missing_evidence:
            reasons.append(NO_CONTROL_EVIDENCE_REASON)
        if last_scan is None:
            reasons.append(NO_POLICY_SCAN_RECORDED_REASON)
        return {
            "controls": controls,
            "open_violations": open_violations,
            "last_scan_at": last_scan.isoformat() if last_scan else None,
            "as_of": as_of,
            "reasons": reasons,
        }

    # ------------------------------------------------------------------ #
    # T-S1: value streams at risk -- the curated path (DA-S1). Helpers are
    # staticmethods immediately above the method itself, inside the class,
    # per the implementation plan's free-region rule for this file (plan
    # § 5.2) -- not module-level functions near ``_not_computed_counts``.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _value_stream_tenant_predicate(model, organization_id: int):
        """The explicit ``organization_id ==`` predicate applied at three
        call sites on this path -- the tenant's own ``ValueStream`` select,
        the ``CapabilityValueStreamMapping`` select, and the not-found
        resolver's ``ValueStream`` select in ``routes/api.py`` -- isolated
        as its own seam -- the same pattern as
        ``derived_facts._apply_default_staleness_filter`` -- so the
        cross-tenant mutation-proof test can monkeypatch exactly this one
        function to a no-op and confirm the named test goes red, without
        editing source under test or inlining the predicate separately at
        each call site.

        ``ValueStreamStage`` deliberately carries NO predicate of its own: it
        is scoped through its parent instead, reachable only via a
        tenant-owned mapping on a tenant-owned value stream, and its join is
        checked by stream membership (``ValueStreamStage.value_stream_id ==
        CapabilityValueStreamMapping.value_stream_id``), not by calling this
        function a fourth time. See ``value_streams_at_risk``'s own
        docstring for why.

        All three models this function is actually called with already carry
        ``TenantMixin``, so this predicate is defence in depth inside a
        request and is what keeps a caller correct when called with no
        ambient request context (a job, a CLI command, a test looping
        tenants in one session), where the ORM listener would otherwise
        no-op entirely.
        """
        return model.organization_id == organization_id

    @staticmethod
    def _at_risk_for_maturity(current_maturity: Optional[int], threshold: int) -> Optional[bool]:
        """Whether a capability counts as at risk, isolated as its own seam
        so the mutation-proof test (acceptance item 11) can monkeypatch
        exactly this function to always return ``False`` for a null maturity
        and confirm the null-maturity-is-neutral test goes red, without
        editing source under test.

        ``None`` in, ``None`` out -- a capability with no maturity recorded
        is neither at risk nor safe (US-2 AC-2), never ``False``.
        """
        if current_maturity is None:
            return None
        return current_maturity < threshold

    @staticmethod
    def value_streams_at_risk(
        organization_id: int,
        *,
        threshold: int = 3,
        value_stream_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """T-S1 (DA-S1, ADR-S1, ADR-S2): "which value streams depend on a
        capability below *threshold*" -- the curated path only. A person's
        own ``capability_value_stream_mapping`` row is the whole of the
        evidence; there is no graph read here (no derived fact, no
        explicit-relationship walk) and no ``include_derived`` /
        ``include_stale`` / ``max_depth`` parameter -- those belong to T-S3.

        Five batched selects regardless of row count, in this order,
        following ``_resolve_owners_batch``'s own collect-then-resolve shape:
        value streams for the tenant (narrowed by ``value_stream_id`` when
        given); mapping rows for those value-stream ids, joined to
        ``ValueStreamStage`` for the stage id and name; capability identity
        for the distinct capability ids; maturity through the accessor;
        the helper's own owner/element map select. Never one select per row.

        Tenancy (design § 3.2, § 9): ``ValueStream`` and
        ``CapabilityValueStreamMapping`` carry the strict, explicit predicate
        through ``_value_stream_tenant_predicate``. ``ValueStreamStage`` does
        NOT carry its own ``organization_id`` predicate -- it is scoped
        through its parent: a stage is reachable only through a tenant-owned
        mapping on a tenant-owned value stream (``value_stream_id.in_(vs_ids)``,
        where every id in ``vs_ids`` already came from the fenced value-stream
        select). The join to it is an OUTER join whose ``ON`` clause checks
        both the stage id AND that the stage belongs to the mapping's own
        value stream -- a mapping pointing at a stage of a different stream
        (this tenant's or another's) must not have that stage's name
        attributed to a stream it is not part of. A mapping whose stage does
        not survive the join (null-owner, another tenant's, or a different
        own stream) is listed and counted with ``dependency.stage: null``,
        never dropped and never reported as "nothing recorded" -- a recorded
        dependency is not an absence. ``UnifiedCapability`` is read TWICE
        with two deliberately different predicates -- identity uses the
        permissive ``or_(... is_(None))``, written out in full below,
        because a tenant's own mapping row may name a shared catalogue
        capability and that mapping is honoured; maturity uses the strict
        accessor (``maturity_for_capability_ids``, ``organization_id``
        required), so a shared catalogue row contributes its mapping and
        never its maturity -- a shared number is not this tenant's
        assessment. Maturity is read only through the accessor; no module on
        this path reads ``current_maturity_level`` off a row directly.

        Counts -- every count in this payload is a count of
        DISTINCT capability ids, never of mapping rows:

        ================================ =====================================
        Field                            Carries
        ================================ =====================================
        ``rows[].capabilities[]``        one entry per mapping row on that
                                          value stream, ordered by mapping id;
                                          a capability mapped on N stages
                                          appears N times, with identical
                                          ``id``, ``current_maturity``,
                                          ``target_maturity``, ``at_risk`` and
                                          ``reason`` on every entry and a
                                          different ``dependency`` object on
                                          each
        ``rows[].at_risk_capability_count`` number of DISTINCT capability ids
                                          on that row whose ``at_risk`` is
                                          ``true``
        ``rows[].reason``                ``no_capability_linked`` when
                                          ``capabilities[]`` is empty;
                                          otherwise ``null``
        ``summary.value_streams_considered`` number of rows
        ``summary.value_streams_at_risk`` rows whose ``at_risk_capability_count``
                                          is above zero
        ``summary.capabilities_considered`` distinct capability ids reached
                                          anywhere in the answer
        ``summary.capabilities_below_threshold`` distinct capability ids whose
                                          ``at_risk`` is ``true`` anywhere in
                                          the answer; a capability at risk on
                                          two value streams counts once
        ``summary.capabilities_with_no_maturity`` distinct capability ids
                                          whose ``current_maturity`` is
                                          ``null`` anywhere in the answer
        ================================ =====================================

        Invariant: ``capabilities_below_threshold + capabilities_with_no_maturity
        <= capabilities_considered``.
        """
        from app.models.unified_capability import (
            CapabilityValueStreamMapping,
            UnifiedCapability,
            ValueStream,
            ValueStreamStage,
        )

        with record_query_latency("value_streams_at_risk") as scope:
            scope.organization_id = organization_id

            vs_stmt = db.select(ValueStream).where(
                IntelligenceQueryService._value_stream_tenant_predicate(
                    ValueStream, organization_id
                )
            )
            if value_stream_id is not None:
                vs_stmt = vs_stmt.where(ValueStream.id == value_stream_id)
            value_streams = (
                db.session.execute(vs_stmt.order_by(ValueStream.id)).scalars().all()
            )

            if not value_streams:
                rows: List[Dict[str, Any]] = []
                summary: Dict[str, Any] = {
                    "value_streams_considered": 0,
                    "value_streams_at_risk": 0,
                    "capabilities_considered": 0,
                    "capabilities_below_threshold": 0,
                    "capabilities_with_no_maturity": 0,
                    "value_streams_not_linked_to_model": 0,
                }
                reasons = [validate_reason_code("no_value_stream_recorded")]
            else:
                vs_ids = [vs.id for vs in value_streams]

                mapping_stmt = (
                    db.select(CapabilityValueStreamMapping, ValueStreamStage)
                    .outerjoin(
                        ValueStreamStage,
                        db.and_(
                            ValueStreamStage.id
                            == CapabilityValueStreamMapping.value_stream_stage_id,
                            ValueStreamStage.value_stream_id
                            == CapabilityValueStreamMapping.value_stream_id,
                        ),
                    )
                    .where(
                        CapabilityValueStreamMapping.value_stream_id.in_(vs_ids),
                        IntelligenceQueryService._value_stream_tenant_predicate(
                            CapabilityValueStreamMapping, organization_id
                        ),
                    )
                    .order_by(CapabilityValueStreamMapping.id)
                )
                mapping_rows = db.session.execute(mapping_stmt).all()

                capability_ids = sorted({m.capability_id for m, _stage in mapping_rows})

                identity_by_id: Dict[int, Dict[str, Any]] = {}
                if capability_ids:
                    identity_stmt = db.select(
                        UnifiedCapability.id,
                        UnifiedCapability.name,
                        UnifiedCapability.code,
                    ).where(
                        UnifiedCapability.id.in_(capability_ids),
                        # Permissive predicate, written out in full (§ 3.2):
                        # a tenant's own mapping row may name a shared
                        # catalogue capability, and that mapping is honoured.
                        db.or_(
                            UnifiedCapability.organization_id == organization_id,
                            UnifiedCapability.organization_id.is_(None),
                        ),
                    )
                    for cap_id, name, code in db.session.execute(identity_stmt).all():
                        identity_by_id[cap_id] = {"id": cap_id, "name": name, "code": code}

                # Maturity through the helper -- never off the columns.
                # Strict predicate, required kwarg: a shared catalogue row's
                # maturity is never read as this tenant's own.
                maturity_by_id = CapabilityHeatmapService().maturity_for_capability_ids(
                    capability_ids, organization_id=organization_id
                )

                mappings_by_vs: Dict[int, List[Tuple[Any, Any]]] = {}
                for mapping, stage in mapping_rows:
                    mappings_by_vs.setdefault(mapping.value_stream_id, []).append(
                        (mapping, stage)
                    )

                rows = []
                value_streams_at_risk_count = 0
                # Every count below is a count of DISTINCT capability
                # ids -- never a count of mapping rows. capability_rows[] (the
                # serialised list) still holds one entry per mapping row, so a
                # capability mapped on N stages appears N times there with an
                # identical id/maturity/at_risk/reason and a different
                # dependency object each time; these sets de-duplicate that
                # back down to "how many distinct capabilities", which is
                # what the summary and each row's at_risk_capability_count
                # both promise.
                capabilities_considered: set = set()
                capabilities_below_threshold_ids: set = set()
                capabilities_with_no_maturity_ids: set = set()
                value_streams_not_linked_to_model = 0

                for vs in value_streams:
                    if vs.archimate_element_id is None:
                        value_streams_not_linked_to_model += 1

                    capability_rows: List[Dict[str, Any]] = []
                    at_risk_ids_in_row: set = set()
                    for mapping, stage in mappings_by_vs.get(vs.id, []):
                        identity = identity_by_id.get(mapping.capability_id)
                        if identity is None:
                            # Named by this tenant's own mapping row but not
                            # resolvable under either predicate (deleted, or
                            # never existed) -- omitted rather than
                            # fabricated with a placeholder name.
                            continue
                        capabilities_considered.add(mapping.capability_id)

                        maturity = maturity_by_id.get(mapping.capability_id) or {
                            "current": None,
                            "target": None,
                            "under_target": None,
                            "target_gap": None,
                            "reason": validate_reason_code("no_maturity_recorded"),
                        }
                        current = maturity["current"]
                        target = maturity["target"]
                        under_target = maturity["under_target"]
                        target_gap = maturity["target_gap"]
                        at_risk = IntelligenceQueryService._at_risk_for_maturity(
                            current, threshold
                        )
                        if current is None:
                            capabilities_with_no_maturity_ids.add(mapping.capability_id)
                            cap_reason = maturity["reason"]
                        else:
                            cap_reason = None
                            if at_risk:
                                at_risk_ids_in_row.add(mapping.capability_id)
                                capabilities_below_threshold_ids.add(mapping.capability_id)

                        capability_rows.append(
                            {
                                "id": identity["id"],
                                "name": identity["name"],
                                "code": identity["code"],
                                "current_maturity": current,
                                "target_maturity": target,
                                "under_target": under_target,
                                "target_gap": target_gap,
                                "maturity_source": "unified_capabilities",
                                "at_risk": at_risk,
                                "dependency": {
                                    "link_kind": "curated",
                                    "support_type": mapping.support_type,
                                    "support_level": mapping.support_level,
                                    "impact_level": mapping.impact_level,
                                    "stage_criticality": mapping.stage_criticality,
                                    "assessed_by": mapping.assessor,
                                    "assessed_at": (
                                        mapping.last_assessed.isoformat()
                                        if mapping.last_assessed is not None
                                        else None
                                    ),
                                    "stage": (
                                        {"id": stage.id, "name": stage.name}
                                        if stage is not None
                                        else None
                                    ),
                                },
                                "reason": cap_reason,
                            }
                        )

                    row_reason = (
                        validate_reason_code("no_capability_linked")
                        if not capability_rows
                        else None
                    )

                    risk_reasons = []
                    if at_risk_ids_in_row:
                        risk_reasons.append("capability_below_threshold")
                    if any(cr.get("under_target") is True for cr in capability_rows):
                        risk_reasons.append("capability_under_target")
                    if any(cr.get("current_maturity") is None for cr in capability_rows):
                        risk_reasons.append("capability_unassessed")
                    risk_reasons.sort()

                    at_risk_count = len(at_risk_ids_in_row)
                    rows.append(
                        {
                            "value_stream": {
                                "id": vs.id,
                                "name": vs.name,
                                "code": vs.code,
                                "archimate_element_id": vs.archimate_element_id,
                            },
                            "at_risk_capability_count": at_risk_count,
                            "risk_reasons": risk_reasons,
                            "capabilities": capability_rows,
                            "reason": row_reason,
                        }
                    )
                    if at_risk_count > 0:
                        value_streams_at_risk_count += 1

                summary = {
                    "value_streams_considered": len(value_streams),
                    "value_streams_at_risk": value_streams_at_risk_count,
                    "capabilities_considered": len(capabilities_considered),
                    "capabilities_below_threshold": len(capabilities_below_threshold_ids),
                    "capabilities_with_no_maturity": len(capabilities_with_no_maturity_ids),
                    "value_streams_not_linked_to_model": value_streams_not_linked_to_model,
                }
                reasons = []

        summary["latency_ms"] = scope.latency_ms
        return {
            "threshold": threshold,
            "threshold_basis": "current_maturity_level < threshold",
            "rows": rows,
            "summary": summary,
            "reasons": reasons,
        }

    @staticmethod
    def strategy_for_element(
        element_id: int,
        *,
        max_depth: int = 3,
        include_derived: bool = True,
    ) -> Dict[str, Any]:
        """L2, "what are we trying to achieve, and how's it tracking?": every
        ``PortfolioInitiative`` seeded directly on the picked element
        (``archimate_element_id`` FK), each with the SAME blast-radius
        traversal L1/L5/L6 already run -- no second traversal algorithm.

        Tenant-safety note, verified not assumed: ``PortfolioInitiative``
        carries no ``TenantMixin``/``organization_id`` of its own, the same
        gap ``UnifiedWorkPackage`` has (L5 brief). This method never lists
        initiatives independently of an element -- every row it returns is
        filtered by ``archimate_element_id == element_id``, and
        ``element_id`` is only ever reached here after the element itself
        was confirmed to belong to the caller's tenant (below). A
        cross-tenant initiative cannot share a seed element id with the
        wrong org's element, since ``archimate_elements.id`` is a real
        primary key each row of which belongs to exactly one tenant.

        Budget variance is read from the model's own ``total_budget``/
        ``spent_to_date`` fields directly -- ``PortfolioInitiative`` has no
        wrapping helper method to avoid, but the same not-computed-vs-
        measured-zero discipline still applies: variance is only reported
        when ``total_budget`` is a real positive number, else the row
        carries the honest ``no_budget_recorded`` reason.

        When the picked element is a Capability, each initiative row carries
        the capability's maturity block from the T-MAT-1 helper.
        """
        from app.models import ArchiMateElement
        from app.models.enterprise_intelligence import PortfolioInitiative

        org_id = current_org_id()

        with record_query_latency("strategy_for_element") as scope:
            scope.organization_id = org_id

            if org_id is None:
                return {
                    "initiatives": [],
                    "reasons": [NO_TENANT_CONTEXT_REASON],
                    "elements": {},
                }

            element = db.session.execute(
                db.select(ArchiMateElement).where(ArchiMateElement.id == element_id)
            ).scalar_one_or_none()
            if element is None:
                return {
                    "initiatives": [],
                    "reasons": [ELEMENT_NOT_FOUND_REASON],
                    "elements": {},
                }

            seed_initiatives = (
                db.session.execute(
                    db.select(PortfolioInitiative).where(
                        PortfolioInitiative.archimate_element_id == element_id
                    )
                )
                .scalars()
                .all()
            )

            if not seed_initiatives:
                return {
                    "initiatives": [],
                    "reasons": [NO_INITIATIVE_LINKED_REASON],
                    "elements": {},
                }

            # Capability maturity block: one call when the element is a Capability.
            capability_maturity = None
            if (element.type or "") == "Capability":
                capability_maturity = CapabilityHeatmapService().maturity_for_elements(
                    [element_id], organization_id=org_id
                ).get(element_id)

            all_elements: Dict[str, Dict[str, Any]] = {}
            initiative_payloads: List[Dict[str, Any]] = []
            for initiative in seed_initiatives:
                blast = IntelligenceQueryService.cross_layer_impact(
                    element_id,
                    include_derived=include_derived,
                    max_depth=max_depth,
                    with_owner=True,
                )
                all_elements.update(blast.get("elements") or {})

                if initiative.total_budget and initiative.total_budget > 0:
                    total_budget = float(initiative.total_budget)
                    spent_to_date = float(initiative.spent_to_date or 0.0)
                    budget_variance_pct = (spent_to_date - total_budget) / total_budget * 100
                    budget_reason = None
                else:
                    budget_variance_pct = None
                    budget_reason = NO_BUDGET_RECORDED_REASON

                payload = {
                    "initiative_id": initiative.id,
                    "name": initiative.name,
                    "status": initiative.status,
                    "priority": initiative.priority,
                    "health_status": initiative.health_status,
                    "completion_percentage": initiative.completion_percentage,
                    "start_date": initiative.start_date.isoformat()
                    if initiative.start_date
                    else None,
                    "target_end_date": initiative.target_end_date.isoformat()
                    if initiative.target_end_date
                    else None,
                    "executive_sponsor": initiative.executive_sponsor,
                    "program_manager": initiative.program_manager,
                    "budget_variance_pct": budget_variance_pct,
                    "budget_reason": budget_reason,
                    "success_metrics": [
                        {
                            "metric_name": m.metric_name,
                            "metric_type": m.metric_type,
                            "target_value": m.target_value,
                            "actual_value": m.actual_value,
                            "status": m.status,
                        }
                        for m in initiative.success_metrics
                    ],
                    "affected_rows": blast.get("rows", []),
                    "affected_summary": blast.get("summary", {}),
                }
                if capability_maturity is not None:
                    payload["capability_maturity"] = capability_maturity
                initiative_payloads.append(payload)

        return {
            "initiatives": initiative_payloads,
            "reasons": [],
            "elements": all_elements,
        }

__all__ = ["IntelligenceQueryService"]
