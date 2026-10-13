"""Founder-reported bug: "ArchiMate Composer" opened a blank canvas with a
template picker instead of the enterprise-wide Layered viewpoint (444
elements / 159 relationships across all layers).

Two parts fixed, verified here:

1. Nav plumbing (app/config/navigation_registry_v2.py,
   app/config/navigation_sections_v2.py) -- ``NavigationItemV2`` gained a
   ``query_params`` field threaded into ``url_for(endpoint, **query_params)``
   so the "ArchiMate Composer" link resolves to
   ``...composer?viewpoint=layered`` instead of the bare composer URL.

2. Backend scope (app/services/archimate_viewpoint_service.py) --
   ``get_viewpoint_data`` used to require a ``solution_id`` for every
   viewpoint, returning ``scope_required: True`` otherwise. ``'basic'`` and
   ``'layered'`` are now flagged ``enterprise_scope: True`` and, with no
   ``solution_id``, query ``ArchiMateElement.query`` directly (tenant-scoped
   by ``TenantMixin``'s ``do_orm_execute`` listener) instead of returning
   scope_required.

The cross-tenant test is the highest-stakes part of this bucket: the new
enterprise-wide path bypasses the solution-junction lookup entirely, so it
must be proven -- not assumed -- that it still only ever returns the calling
tenant's own rows.
"""

import datetime



def test_composer_sidebar_link_carries_viewpoint_query_param(app, db_session, make_org, login_as):
    """The REAL, live sidebar (``app/utils/role_access.py::get_sidebar_zones``,
    rendered by ``app/templates/components/admin_sidebar.html``) is what a
    user actually clicks -- not the NavigationRegistryV2/navigation_sections_v2
    module, which turned out to be dead code: nothing in app/_bootstrap or any
    template imports it (``grep -rln 'get_navigation_sections\\|NavigationRegistryV2'
    app/templates/`` returns nothing), so the "changes already made" to that
    module in this bucket's brief never reached the rendered page. This test
    hits the real route and asserts the actual anchor href.
    """
    import uuid

    from app.models.user import User

    org = make_org("composer-sidebar")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"composer-sidebar-{suffix}@example.com",
        first_name="Composer",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert 'href="/archimate/composer?viewpoint=layered"' in html, (
        "the rendered sidebar's ArchiMate Composer link must carry "
        "?viewpoint=layered -- otherwise clicking it still lands on the "
        "blank canvas the founder reported. Sidebar HTML did not contain "
        "the expected href."
    )


def test_composer_nav_link_resolves_to_layered_viewpoint(app):
    """The sidebar "ArchiMate Composer" item must resolve to a URL carrying
    ``viewpoint=layered``, not the bare composer URL a blank canvas is
    served from.

    Exercises the real NavigationRegistryV2 resolution path (_resolve_url ->
    _safe_url_for -> url_for(endpoint, **query_params)), not just the
    presence of the right key in the section config dict.
    """
    from app.config.navigation_registry_v2 import NavigationRegistryV2
    from app.config.navigation_sections_v2 import ARCHITECTURE_TOOLS_SECTION

    registry = NavigationRegistryV2()
    registry.register_section(ARCHITECTURE_TOOLS_SECTION)

    composer_item = next(
        item for item in ARCHITECTURE_TOOLS_SECTION.items
        if item.label == "ArchiMate Composer"
    )
    assert composer_item.query_params == {"viewpoint": "layered"}, (
        "sanity check: the nav item itself must declare the query param "
        f"before resolution is even attempted, got {composer_item.query_params!r}"
    )

    with app.test_request_context("/"):
        resolved_url = registry._resolve_url(composer_item)

    assert "viewpoint=layered" in resolved_url, (
        f"composer nav link resolved to {resolved_url!r}, missing "
        "viewpoint=layered -- clicking it would land on the blank canvas "
        "the founder reported, not the layered viewpoint"
    )
    assert resolved_url.startswith("/archimate/composer"), resolved_url


# NOTE: NavigationRegistryV2.get_navigation_sections() -- the section-level
# entrypoint -- is not exercised here. It hits a third, separate pre-existing
# bug in this dead-code module: _is_visible() reads `config.disabled` but
# NavigationSectionV2 (unlike NavigationItemV2) declares no `disabled` field
# at all, so any get_navigation_sections() call raises AttributeError. Left
# undocumented as a known-issue rather than fixed here: this module is not
# imported by any template or app/_bootstrap file (confirmed by grep -rln
# 'get_navigation_sections\|NavigationRegistryV2' app/templates/ returning
# nothing), so it is not the code path the rendered sidebar actually uses --
# see test_composer_sidebar_link_carries_viewpoint_query_param above for the
# real, live mechanism. Fixing every bug in unexercised code is scope creep
# this bucket's brief did not ask for; the two bugs fixed above (regex= ->
# pattern=, and the disabled-item endpoint validator field-ordering bug) were
# fixed only because they blocked even importing the file the brief's diff
# touched.


def test_layered_viewpoint_returns_elements_without_solution_id(app, db_session, make_org, tenant_ctx):
    """'layered' declares enterprise_scope=True, so calling it with no
    solution_id (exactly what the composer nav link now does) must return
    real elements for the calling tenant, not scope_required: True.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("layered-vp")
    elements = [
        ArchiMateElement(name=f"Element {i}", type="ApplicationComponent",
                          layer="application", organization_id=org.id)
        for i in range(3)
    ]
    db_session.add_all(elements)
    db_session.flush()

    with tenant_ctx(org.id):
        result = get_viewpoint_data("layered", solution_id=None)

    assert result.get("scope_required") is not True, (
        f"'layered' with no solution_id still returned scope_required, "
        f"the composer would render 'Select a solution' again: {result}"
    )
    returned_ids = {e["id"] for e in result["elements"]}
    assert returned_ids == {e.id for e in elements}, (
        f"expected exactly this tenant's {len(elements)} elements, got {result['elements']}"
    )
    assert result["total"] == len(elements)


def test_layered_viewpoint_backend_failure_returns_explicit_error_not_fabricated_empty(
    app, db_session, make_org, tenant_ctx, monkeypatch
):
    """D4: a bare `except Exception: serialised = []` returned a 200 with
    elements: [] on ANY failure -- indistinguishable from a genuinely empty
    model, the fabrication class CLAUDE.md explicitly calls out. Force the
    enterprise-wide query to raise and assert the response carries an
    explicit error flag rather than silently looking like zero real data.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("layered-vp-failure")
    db_session.add(
        ArchiMateElement(name="Won't be reached", type="ApplicationComponent",
                          layer="application", organization_id=org.id)
    )
    db_session.commit()

    class _BoomQuery:
        def filter(self, *a, **k):
            return self

        def limit(self, *a, **k):
            raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(ArchiMateElement, "query", _BoomQuery())

    with tenant_ctx(org.id):
        result = get_viewpoint_data("layered", solution_id=None)

    assert result.get("error") is True, (
        f"a backend failure must set an explicit error flag, not silently "
        f"look like a real empty result: {result}"
    )
    assert result["elements"] == []
    assert result["relationships"] == [], (
        "relationships_out must be reset on failure too -- Invariant 4 "
        "forbids dangling relationships alongside an empty elements list"
    )


def test_basic_viewpoint_also_enterprise_scoped(app, db_session, make_org, tenant_ctx):
    """'basic' was flagged enterprise_scope alongside 'layered' -- confirm
    it also no longer requires solution_id, matching the brief's changes-
    already-made description of STANDARD_VIEWPOINTS.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("basic-vp")
    element = ArchiMateElement(name="Solo element", type="ApplicationComponent",
                                layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    with tenant_ctx(org.id):
        result = get_viewpoint_data("basic", solution_id=None)

    assert result.get("scope_required") is not True, result
    assert {e["id"] for e in result["elements"]} == {element.id}


def test_layered_viewpoint_cross_tenant_isolation(app, db_session, make_org, tenant_ctx):
    """The new enterprise-wide path bypasses the solution-junction lookup
    entirely and queries ArchiMateElement.query directly. Prove org A's call
    to the layered viewpoint never returns org B's elements -- the highest-
    stakes assertion in this bucket, per the brief.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org_a = make_org("layered-a")
    org_b = make_org("layered-b")

    # Tenant B: a noisy neighbour with several elements that must never
    # leak into tenant A's enterprise-wide read.
    b_elements = [
        ArchiMateElement(name=f"B element {i}", type="ApplicationComponent",
                          layer="application", organization_id=org_b.id)
        for i in range(5)
    ]
    db_session.add_all(b_elements)

    # Tenant A: exactly one element of its own.
    a_element = ArchiMateElement(name="A element", type="ApplicationComponent",
                                  layer="application", organization_id=org_a.id)
    db_session.add(a_element)
    db_session.flush()

    with tenant_ctx(org_a.id):
        result = get_viewpoint_data("layered", solution_id=None)

    returned_ids = {e["id"] for e in result["elements"]}
    b_ids = {e.id for e in b_elements}

    assert returned_ids == {a_element.id}, (
        f"tenant A's layered viewpoint returned {returned_ids}, expected "
        f"only {{{a_element.id}}}"
    )
    assert not (returned_ids & b_ids), (
        "CROSS-TENANT LEAK: org A's enterprise-wide layered viewpoint "
        f"returned org B's element ids {returned_ids & b_ids}"
    )

    # Symmetric check: tenant B must see only its own 5 elements, not A's.
    with tenant_ctx(org_b.id):
        result_b = get_viewpoint_data("layered", solution_id=None)
    returned_ids_b = {e["id"] for e in result_b["elements"]}
    assert returned_ids_b == b_ids, returned_ids_b
    assert a_element.id not in returned_ids_b


def test_solution_scoped_viewpoint_still_requires_solution_id(app, db_session, make_org, tenant_ctx):
    """Regression guard: 'stakeholder' has no enterprise_scope flag (verified
    by reading STANDARD_VIEWPOINTS -- only 'basic' and 'layered' carry it),
    so it must still return scope_required: True with no solution_id. Proves
    the opt-in flag did not leak enterprise-wide behaviour into every other
    viewpoint.
    """
    from app.services.archimate_viewpoint_service import STANDARD_VIEWPOINTS, get_viewpoint_data

    assert not STANDARD_VIEWPOINTS["stakeholder"].get("enterprise_scope"), (
        "'stakeholder' must not carry enterprise_scope -- if it does, this "
        "test needs a different solution-scoped viewpoint picked instead"
    )

    # D6: assert the COMPLETE set of enterprise_scope viewpoints is exactly
    # {'basic', 'layered'} -- not just that 'stakeholder' individually lacks
    # the flag -- so a future viewpoint accidentally gaining it is caught.
    enterprise_scoped = {
        key for key, vp in STANDARD_VIEWPOINTS.items() if vp.get("enterprise_scope")
    }
    assert enterprise_scoped == {"basic", "layered"}, (
        f"expected exactly {{'basic', 'layered'}} to carry enterprise_scope, "
        f"got {enterprise_scoped}"
    )

    org = make_org("stakeholder-vp")
    with tenant_ctx(org.id):
        result = get_viewpoint_data("stakeholder", solution_id=None)

    assert result.get("scope_required") is True, (
        f"'stakeholder' (solution-scoped) must return scope_required: True "
        f"with no solution_id -- the enterprise_scope opt-in must not have "
        f"leaked into other viewpoints, got {result}"
    )
    assert result["elements"] == []


def test_layered_viewpoint_with_no_org_context_returns_scope_required(app, db_session, make_org):
    """D5: the enterprise-wide path relies on TenantMixin's do_orm_execute
    listener to scope ArchiMateElement.query -- but that listener is a
    documented NO-OP (not a deny) when g.current_org_id is unset
    (app/middleware/tenant_isolation.py). Before the fix flipped
    'no solution_id' from scope_required=True to running an unscoped query,
    the function was safe-by-default; after, it would return every tenant's
    rows if this path is ever reached with no org resolved. Assert the
    enterprise-wide branch fails closed (scope_required, zero rows) rather
    than falling through to an unscoped query, when no current org context
    is set at all.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org_a = make_org("d5-no-context-a")
    db_session.add(
        ArchiMateElement(
            name="D5 leak probe",
            type="ApplicationComponent",
            layer="application",
            organization_id=org_a.id,
        )
    )
    db_session.commit()

    # A request context with g.current_org_id deliberately left unset --
    # mirrors any caller outside the normal @login_required request flow
    # (CLI, scheduler, a future non-authenticated caller).
    with app.test_request_context("/"):
        result = get_viewpoint_data("layered", solution_id=None)

    assert result.get("scope_required") is True, (
        f"with no current org context, the enterprise-wide 'layered' path "
        f"must fail closed (scope_required: True) rather than run an "
        f"unscoped query that would return every tenant's rows, got {result}"
    )
    assert result["elements"] == []


# ── dashboard composer layer links: layer= filter ────────────────────────────
# Founder-reported: the Technology tab's "32 elements" card promised elements
# that the "Open in composer" link never actually filtered to.


def test_dashboard_layer_tab_links_carry_correct_layer_param(app, db_session, make_org, login_as):
    """Each of the six dashboard layer tabs' 'Open in composer' anchor must be
    Alpine-bound to that tab's own layer key, not the static, unscoped href
    the founder reported. Asserts the Alpine binding expression itself
    (rendered once, evaluated per-tab client-side by activeRole), matching
    how test_composer_sidebar_link_carries_viewpoint_query_param above checks
    the sidebar's real rendered HTML rather than a source file.
    """
    import uuid

    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org = make_org("layer-tab-links")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"layer-tab-links-{suffix}@example.com",
        first_name="Layer",
        last_name="Tabs",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    # The per-layer panel only renders in dashboard_mode == 'data'
    # (dashboard_views.py:606-608: "guided" for applications_count < 5 and
    # zero capability mappings) -- seed 5 applications so this org reaches
    # 'data' mode and the layer tabs actually render.
    db_session.add_all([
        ApplicationComponent(name=f"App {i}", organization_id=org.id)
        for i in range(5)
    ])
    db_session.flush()

    client = app.test_client()
    login_as(client, user)

    resp = client.get("/dashboard/overview")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    html = resp.get_data(as_text=True)

    assert (
        ":href=\"'/archimate/composer?viewpoint=layered&layer=' + activeRole\""
        in html
    ), (
        "the per-layer 'Open in composer' link must be dynamically bound to "
        "activeRole so each of the six tabs opens its own layer -- static "
        "href found instead"
    )

    for layer_key in ("motivation", "strategy", "business", "application",
                       "technology", "implementation"):
        assert f"{layer_key}:" in html, (
            f"expected the Alpine _layers object to declare a '{layer_key}' "
            f"key (drives activeRole matching for that tab)"
        )


def test_layer_filtered_viewpoint_returns_only_that_layers_element_types(
    app, db_session, make_org, tenant_ctx
):
    """A layer= filter on the 'layered' viewpoint must return only elements
    whose type belongs to that layer (via the shared LAYER_TYPES map), and no
    relationship with an endpoint outside that set.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("layer-filter-types")
    tech_node = ArchiMateElement(name="Tech Node", type="Node", layer="technology",
                                  organization_id=org.id)
    app_component = ArchiMateElement(name="App Component", type="ApplicationComponent",
                                      layer="application", organization_id=org.id)
    db_session.add_all([tech_node, app_component])
    db_session.flush()

    cross_rel = ArchiMateRelationship(
        source_id=tech_node.id, target_id=app_component.id, type="serving",
        organization_id=org.id,
    )
    db_session.add(cross_rel)
    db_session.flush()

    with tenant_ctx(org.id):
        result = get_viewpoint_data("layered", solution_id=None, layer="technology")

    returned_ids = {e["id"] for e in result["elements"]}
    assert returned_ids == {tech_node.id}, (
        f"layer='technology' must return only the technology-typed element, "
        f"got {result['elements']}"
    )
    assert result["total"] == 1

    # The relationship has one endpoint (app_component) outside the filtered
    # set, so Invariant 4 (no dangling endpoints) must hide it entirely.
    assert result["relationships"] == [], (
        f"a relationship with an endpoint outside the layer filter must not "
        f"be returned: {result['relationships']}"
    )


def test_layer_filter_count_matches_dashboard_card_count_including_physical_fold(
    app, db_session, make_org, tenant_ctx
):
    """The highest-value assertion in this bucket: the composer's
    layer='technology' element count must equal the dashboard card's own
    layer_breakdown['technology'] for the same tenant -- including an
    Equipment/Facility/Material row, pinning the ArchiMate 3.2
    physical-folds-into-technology behaviour on both sides at once, via the
    now-single shared LAYER_TYPES map.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("layer-count-agreement")
    elements = [
        ArchiMateElement(name="Node 1", type="Node", layer="technology", organization_id=org.id),
        ArchiMateElement(name="Device 1", type="Device", layer="technology", organization_id=org.id),
        # Physical sub-type -- ArchiMate 3.2 folds this into Technology, and
        # the dashboard card's own count already reflects that.
        ArchiMateElement(name="Rack 1", type="Equipment", layer="technology", organization_id=org.id),
        # A different layer, to prove it is excluded from the technology count.
        ArchiMateElement(name="App 1", type="ApplicationComponent", layer="application",
                          organization_id=org.id),
    ]
    db_session.add_all(elements)
    db_session.flush()

    with tenant_ctx(org.id):
        composer_result = get_viewpoint_data("layered", solution_id=None, layer="technology")

    # Reproduce the dashboard route's own count logic against the same map,
    # rather than hitting the full /dashboard/overview HTTP route (which
    # pulls in unrelated nav/feature-section rendering) -- both read the same
    # LAYER_TYPES/LAYER_TYPE_TO_LAYER import, so this proves the shared-map
    # property directly.
    from app import db
    from app.services.archimate_viewpoint_service import LAYER_TYPE_TO_LAYER

    with tenant_ctx(org.id):
        rows = (
            db.session.query(ArchiMateElement.type, db.func.count(ArchiMateElement.id))
            .group_by(ArchiMateElement.type)
            .all()
        )
    card_technology_count = sum(
        count for elem_type, count in rows
        if LAYER_TYPE_TO_LAYER.get((elem_type or "").lower()) == "technology"
    )

    assert composer_result["total"] == card_technology_count == 3, (
        f"composer layer='technology' total={composer_result['total']}, "
        f"dashboard card technology count={card_technology_count} -- these "
        f"must agree, including the Equipment row folded into technology"
    )


def test_layer_param_is_noop_when_absent_for_solution_scoped_and_other_viewpoints(
    app, db_session, make_org, tenant_ctx
):
    """Regression: the optional `layer` param must not change behaviour for
    solution-scoped viewpoints or non-'layered' viewpoints when omitted.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org = make_org("layer-noop-regression")
    element = ArchiMateElement(name="Solo", type="ApplicationComponent",
                                layer="application", organization_id=org.id)
    db_session.add(element)
    db_session.flush()

    with tenant_ctx(org.id):
        without_layer = get_viewpoint_data("layered", solution_id=None)
        with_none_layer = get_viewpoint_data("layered", solution_id=None, layer=None)

    assert without_layer == with_none_layer

    with tenant_ctx(org.id):
        stakeholder_result = get_viewpoint_data("stakeholder", solution_id=None, layer="technology")

    assert stakeholder_result.get("scope_required") is True, (
        "a solution-scoped viewpoint must still require solution_id "
        "regardless of an incidental layer= param"
    )


def test_layer_filter_unknown_value_is_rejected_by_api_route(app, db_session, make_org, login_as):
    """The API route must 400 an unrecognised layer value, never silently
    fall back to 'no filter applied'.
    """
    import uuid

    from app.models.user import User

    org = make_org("layer-unknown-400")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"layer-unknown-{suffix}@example.com",
        first_name="Layer",
        last_name="Unknown",
        organization_id=org.id,
        confirmed=True, onboarding_completed_at=datetime.datetime.utcnow(),
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)

    resp = client.get("/archimate/viewpoints-api/layered/data?layer=not_a_real_layer")
    assert resp.status_code == 400, resp.get_data(as_text=True)[:500]


def test_layer_filter_cross_tenant_isolation(app, db_session, make_org, tenant_ctx):
    """The layer filter is an additional narrowing predicate on the already
    tenant-scoped query -- confirm it cannot leak another org's elements of
    the same type/layer.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import get_viewpoint_data

    org_a = make_org("layer-tenant-a")
    org_b = make_org("layer-tenant-b")

    b_elements = [
        ArchiMateElement(name=f"B Node {i}", type="Node", layer="technology",
                          organization_id=org_b.id)
        for i in range(4)
    ]
    db_session.add_all(b_elements)
    a_element = ArchiMateElement(name="A Node", type="Node", layer="technology",
                                  organization_id=org_a.id)
    db_session.add(a_element)
    db_session.flush()

    with tenant_ctx(org_a.id):
        result_a = get_viewpoint_data("layered", solution_id=None, layer="technology")

    returned_ids = {e["id"] for e in result_a["elements"]}
    b_ids = {e.id for e in b_elements}

    assert returned_ids == {a_element.id}
    assert not (returned_ids & b_ids), (
        "CROSS-TENANT LEAK: org A's layer-filtered viewpoint returned org "
        f"B's element ids {returned_ids & b_ids}"
    )


def test_health_scorecard_agrees_with_dashboard_card_and_composer_on_technology_count(
    app, db_session, make_org, tenant_ctx
):
    """Refuter-found P1: `health_scorecard` (dashboard_views.py) used to
    define its own second, divergent type->layer map whose 'technology' list
    was missing Equipment/Facility/DistributionNetwork/Material -- so an
    Equipment element counted in the dashboard card and the layer-filtered
    composer, but fell into the health scorecard's 'other' bucket instead of
    'technology'. Three surfaces answering one question with two different
    numbers -- the exact ADR-0008 class of defect this bucket exists to
    close. This test seeds an Equipment row (one of the four previously-
    divergent types) and asserts all three surfaces agree.
    """
    from app.modules.dashboard.v2.routes.dashboard_views import (
        _assemble_health_scorecard_metrics,
    )
    from app import db
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_viewpoint_service import (
        LAYER_TYPE_TO_LAYER,
        get_viewpoint_data,
    )

    org = make_org("health-scorecard-layer-agreement")
    elements = [
        ArchiMateElement(name="Node 1", type="Node", layer="technology", organization_id=org.id),
        # Previously divergent: missing from health_scorecard's own local
        # technology list (equipment/facility/distributionnetwork/material),
        # but present in the shared LAYER_TYPES map.
        ArchiMateElement(name="Rack 1", type="Equipment", layer="technology", organization_id=org.id),
        ArchiMateElement(name="App 1", type="ApplicationComponent", layer="application",
                          organization_id=org.id),
    ]
    db_session.add_all(elements)
    db_session.flush()

    with tenant_ctx(org.id):
        composer_result = get_viewpoint_data("layered", solution_id=None, layer="technology")
        scorecard_metrics = _assemble_health_scorecard_metrics()

        rows = (
            db.session.query(ArchiMateElement.type, db.func.count(ArchiMateElement.id))
            .group_by(ArchiMateElement.type)
            .all()
        )
    card_technology_count = sum(
        count for elem_type, count in rows
        if LAYER_TYPE_TO_LAYER.get((elem_type or "").lower()) == "technology"
    )

    scorecard_technology_count = scorecard_metrics["archimate_by_layer"]["technology"]

    assert (
        composer_result["total"]
        == card_technology_count
        == scorecard_technology_count
        == 2
    ), (
        f"composer layer='technology' total={composer_result['total']}, "
        f"dashboard card technology count={card_technology_count}, "
        f"health scorecard technology count={scorecard_technology_count} -- "
        f"all three must agree, including the Equipment row"
    )
    assert scorecard_metrics["archimate_by_layer"].get("other", 0) == 0, (
        "Equipment must be counted as technology, not fall through to "
        f"'other': {scorecard_metrics['archimate_by_layer']}"
    )
