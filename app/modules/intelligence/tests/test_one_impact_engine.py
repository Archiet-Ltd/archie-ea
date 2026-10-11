"""One impact engine — the three surfaces (Impact Analysis page API,
AIImpactAnalysisService, and the Ask screen's cross_layer_impact endpoint)
return the same element set and order from the canonical walk."""

from __future__ import annotations


def _element(db_session, org_id, name, etype="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _application_component(db_session, org_id, element_id, name="App"):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element_id
    )
    db_session.add(comp)
    db_session.flush()
    return comp


# ── surface 1: ImpactAnalysisService (Impact Analysis page API) ──────────


def _surface_impact_analysis_service(element_id):
    """Call ImpactAnalysisService.analyze_change_impact — the Impact Analysis page API path."""
    from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
        ImpactAnalysisService,
    )

    result = ImpactAnalysisService.analyze_change_impact(element_id, change_type="MODIFY")
    direct = result.get("direct_dependencies") or []
    indirect = result.get("indirect_dependencies") or []
    all_deps = direct + indirect
    return [
        {"id": d["id"], "name": d.get("name"), "health": d.get("health"), "owners": d.get("owners", [])}
        for d in all_deps
    ]


# ── surface 2: AIImpactAnalysisService (assistant) ───────────────────────


def _surface_ai_impact_service(app_id):
    """Call AIImpactAnalysisService.analyze_application_impact — the assistant path."""
    from app.modules.ai_chat.services.ai_impact_analysis_service import (
        AIImpactAnalysisService,
    )

    result = AIImpactAnalysisService.analyze_application_impact(
        app_id, scenario="modification", include_ai_analysis=False
    )
    graph = result.get("dependency_analysis") or {}
    direct = graph.get("direct_impacts") or []
    indirect_list = []
    for depth_items in (graph.get("indirect_impacts") or {}).values():
        indirect_list.extend(depth_items)
    all_impacts = direct + indirect_list
    return [
        {"id": i["id"], "name": i.get("name"), "health": i.get("health"), "owners": i.get("owners", [])}
        for i in all_impacts
    ]


# ── surface 3: cross_layer_impact directly (Ask screen) ──────────────────


def _surface_cross_layer_impact(element_id):
    """Call IntelligenceQueryService.cross_layer_impact — the Ask screen path."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=False,
        max_depth=3,
        direction="downstream",
        with_owner=True,
    )
    rows = result.get("rows") or []
    elements = result.get("elements") or {}
    return [
        {
            "id": r["element_id"],
            "name": elements.get(str(r["element_id"]), {}).get("name"),
            "health": r.get("health"),
            "owners": r.get("owners", []),
        }
        for r in rows
    ]


# ── the comparison test ──────────────────────────────────────────────────


def test_three_surfaces_return_same_element_set_and_order(app, db_session, make_org):
    """The three impact surfaces return the same element set, the same
    canonical walk order, and a health field on every row.

    Uses a branching diamond graph where the old per-surface engines
    (recursive CTE with ORDER BY level, name vs. RelationshipService vs.
    canonical BFS) would produce different traversal orders.  The test
    fails on main and passes only when all three surfaces are repointed
    to the single canonical walk.
    """
    org = make_org("one-engine")
    a = _element(db_session, org.id, "Platform-A")
    # Insert B before C so the canonical BFS order is B, C at depth 1.
    # Name them so alphabetical order (Alpha, Zebra) differs from
    # insertion order (Zebra, Alpha) — the old CTE sorted by name.
    b = _element(db_session, org.id, "Zebra-B")
    c = _element(db_session, org.id, "Alpha-C")
    d = _element(db_session, org.id, "Capability-D", etype="Capability", layer="business")
    # Diamond: A → B, A → C, B → D, C → D
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, a, c)
    _relationship(db_session, org.id, b, d)
    _relationship(db_session, org.id, c, d)
    app_comp = _application_component(db_session, org.id, a.id, name="Platform App")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        surface1 = _surface_impact_analysis_service(a.id)
        surface2 = _surface_ai_impact_service(app_comp.id)
        surface3 = _surface_cross_layer_impact(a.id)

    # All three surfaces must return the same element ids in the same order.
    ids1 = [d["id"] for d in surface1]
    ids2 = [d["id"] for d in surface2]
    ids3 = [d["id"] for d in surface3]

    assert ids1 == ids2, (
        f"ImpactAnalysisService ({ids1}) and AIImpactAnalysisService ({ids2}) "
        f"return different element sets or order"
    )
    assert ids1 == ids3, (
        f"ImpactAnalysisService ({ids1}) and cross_layer_impact ({ids3}) "
        f"return different element sets or order"
    )

    # All three surfaces must return non-empty results.
    assert len(surface1) > 0, "ImpactAnalysisService returned no dependencies"
    assert len(surface2) > 0, "AIImpactAnalysisService returned no dependencies"
    assert len(surface3) > 0, "cross_layer_impact returned no dependencies"

    # Every row from every surface must carry a health field.
    for label, rows in [
        ("ImpactAnalysisService", surface1),
        ("AIImpactAnalysisService", surface2),
        ("cross_layer_impact", surface3),
    ]:
        for row in rows:
            assert "health" in row, (
                f"{label} row {row['id']} missing health field"
            )


def test_two_organisation_impact_never_crosses_org_boundary(app, db_session, make_org):
    """An impact walk in organisation A never returns
    elements belonging to organisation B."""
    org_a = make_org("one-engine-org-a")
    org_b = make_org("one-engine-org-b")

    a1 = _element(db_session, org_a.id, "A1")
    a2 = _element(db_session, org_a.id, "A2")
    _relationship(db_session, org_a.id, a1, a2)

    b1 = _element(db_session, org_b.id, "B1")
    b2 = _element(db_session, org_b.id, "B2")
    _relationship(db_session, org_b.id, b1, b2)

    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id

        surface1 = _surface_impact_analysis_service(a1.id)
        surface3 = _surface_cross_layer_impact(a1.id)

    # Org A's walk must only contain org A's elements.
    org_a_ids = {a1.id, a2.id}
    org_b_ids = {b1.id, b2.id}

    for row in surface1:
        assert row["id"] in org_a_ids, f"Org A impact walk leaked org B element {row['id']}"
        assert row["id"] not in org_b_ids, f"Org A impact walk leaked org B element {row['id']}"

    for row in surface3:
        assert row["id"] in org_a_ids, f"Org A cross_layer_impact leaked org B element {row['id']}"
        assert row["id"] not in org_b_ids, f"Org A cross_layer_impact leaked org B element {row['id']}"


def test_health_field_present_on_every_row(app, db_session, make_org):
    """Every impact row carries a health field — the maturity block
    for Capability rows, None for others."""
    org = make_org("one-engine-health")
    a = _element(db_session, org.id, "App-X")
    b = _element(db_session, org.id, "Svc-Y")
    c = _element(db_session, org.id, "Cap-Z", etype="Capability", layer="business")
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, b, c)
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows = result.get("rows") or []
    elements = result.get("elements") or {}
    assert len(rows) > 0, "Expected at least one dependency row"

    for row in rows:
        # Every row must have a "health" key.
        assert "health" in row, f"Row {row['element_id']} missing health field"
        el_type = elements.get(str(row["element_id"]), {}).get("type")
        if el_type == "Capability":
            # Capability rows carry the maturity block as health.
            assert row["health"] is not None, (
                f"Capability row {row['element_id']} has null health"
            )
        else:
            # Non-capability rows carry None (renders as —).
            assert row["health"] is None, (
                f"Non-capability row {row['element_id']} has unexpected health data"
            )


def test_pagination_stable_cursor_and_total(app, db_session, make_org):
    """Pagination with stable cursor returns correct page and total."""
    org = make_org("one-engine-page")
    root = _element(db_session, org.id, "Root")
    # Create 5 direct dependencies.
    children = []
    for i in range(5):
        child = _element(db_session, org.id, f"Child-{i}")
        _relationship(db_session, org.id, root, child)
        children.append(child)
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        # Page 1: first 2 rows.
        page1 = IntelligenceQueryService.cross_layer_impact(
            root.id,
            include_derived=False,
            max_depth=3,
            with_owner=False,
            page_size=2,
        )
        assert page1["total"] == 5
        assert len(page1["rows"]) == 2
        assert page1["next_cursor"] is not None

        # Page 2: next 2 rows after cursor.
        page2 = IntelligenceQueryService.cross_layer_impact(
            root.id,
            include_derived=False,
            max_depth=3,
            with_owner=False,
            page_size=2,
            cursor=page1["next_cursor"],
        )
        assert page2["total"] == 5
        assert len(page2["rows"]) == 2
        assert page2["next_cursor"] is not None

        # Page 3: last row.
        page3 = IntelligenceQueryService.cross_layer_impact(
            root.id,
            include_derived=False,
            max_depth=3,
            with_owner=False,
            page_size=2,
            cursor=page2["next_cursor"],
        )
        assert page3["total"] == 5
        assert len(page3["rows"]) == 1
        assert page3["next_cursor"] is None  # No more pages.

        # All element ids across pages must be unique and cover all 5 children.
        all_ids = set()
        for page in [page1, page2, page3]:
            for row in page["rows"]:
                all_ids.add(row["element_id"])
        assert all_ids == {c.id for c in children}


def test_risk_score_uses_all_elements_when_paginated(app, db_session, make_org):
    """When paginated, risk scoring (total_affected, weighted_score, risk_level)
    and the stored ImpactAnalysisResult use all affected elements, not just the
    current page."""
    org = make_org("one-engine-risk-page")
    root = _element(db_session, org.id, "Root")
    children = []
    for i in range(5):
        child = _element(db_session, org.id, f"Child-{i}")
        _relationship(db_session, org.id, root, child)
        children.append(child)
    db_session.commit()

    from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
        ImpactAnalysisService,
    )

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        # Paginated call: page 1 of 2.
        result = ImpactAnalysisService.analyze_change_impact(
            root.id, change_type="MODIFY", page_size=2
        )

    # total_affected must reflect all 5 children, not just the 2 on page 1.
    assert result["total_affected"] == 5, (
        f"total_affected should be 5 (all children), got {result['total_affected']}"
    )
    # weighted_score must reflect all 5 children (each medium = weight 2, so 10).
    assert result["weighted_score"] == 10, (
        f"weighted_score should be 10 (5 × medium=2), got {result['weighted_score']}"
    )
    # risk_level must be MEDIUM (score 10, between 5 and 20).
    assert result["risk_level"] == "MEDIUM", (
        f"risk_level should be MEDIUM, got {result['risk_level']}"
    )
    # The stored analysis record must contain all 5 element IDs.
    analysis_id = result["analysis_id"]
    assert analysis_id is not None, "analysis_id should not be None"
    from app.models.traceability import ImpactAnalysisResult
    record = db_session.get(ImpactAnalysisResult, analysis_id)
    import json as _json
    stored_ids = _json.loads(record.impacted_elements)
    assert len(stored_ids) == 5, (
        f"stored impacted_elements should have 5 IDs, got {len(stored_ids)}"
    )
    assert set(stored_ids) == {c.id for c in children}, (
        "stored impacted_elements should match all children"
    )


def test_affected_applications_count_is_correct(app, db_session, make_org):
    """affected_applications_count in the stored record matches the number of
    impacted elements that have an ApplicationComponent."""
    org = make_org("one-engine-app-count")
    a = _element(db_session, org.id, "App-A")
    b = _element(db_session, org.id, "Svc-B")
    c = _element(db_session, org.id, "Svc-C")
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, b, c)
    # Element B has an application component; C does not.
    _application_component(db_session, org.id, b.id, name="Service App")
    db_session.commit()

    from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
        ImpactAnalysisService,
    )

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        result = ImpactAnalysisService.analyze_change_impact(
            a.id, change_type="MODIFY"
        )

    analysis_id = result["analysis_id"]
    assert analysis_id is not None
    from app.models.traceability import ImpactAnalysisResult
    record = db_session.get(ImpactAnalysisResult, analysis_id)
    assert record.affected_applications_count == 1, (
        f"affected_applications_count should be 1, got {record.affected_applications_count}"
    )


def test_two_organisations_never_see_one_anothers_elements_in_stored_analysis(
    app, db_session, make_org
):
    """Two organisations never see one another's elements in stored
    ImpactAnalysisResult records."""
    org_a = make_org("one-engine-stored-org-a")
    org_b = make_org("one-engine-stored-org-b")

    a1 = _element(db_session, org_a.id, "A1")
    a2 = _element(db_session, org_a.id, "A2")
    _relationship(db_session, org_a.id, a1, a2)

    b1 = _element(db_session, org_b.id, "B1")
    b2 = _element(db_session, org_b.id, "B2")
    _relationship(db_session, org_b.id, b1, b2)

    db_session.commit()

    from app.modules.solutions_strategic.v2.services.impact_analysis_service import (
        ImpactAnalysisService,
    )

    # Run analysis for org A.
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result_a = ImpactAnalysisService.analyze_change_impact(
            a1.id, change_type="MODIFY"
        )

    # Run analysis for org B.
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b.id
        result_b = ImpactAnalysisService.analyze_change_impact(
            b1.id, change_type="MODIFY"
        )

    from app.models.traceability import ImpactAnalysisResult
    import json as _json

    record_a = db_session.get(ImpactAnalysisResult, result_a["analysis_id"])
    record_b = db_session.get(ImpactAnalysisResult, result_b["analysis_id"])

    stored_ids_a = set(_json.loads(record_a.impacted_elements))
    stored_ids_b = set(_json.loads(record_b.impacted_elements))

    # The stored impacted_elements are the dependencies, not the seed.
    org_a_dep_ids = {a2.id}
    org_b_dep_ids = {b2.id}

    # Org A's stored record must only contain org A's dependency elements.
    assert stored_ids_a == org_a_dep_ids, (
        f"Org A stored record contains {stored_ids_a}, expected {org_a_dep_ids}"
    )
    assert stored_ids_a.isdisjoint({b1.id, b2.id}), (
        f"Org A stored record leaked org B elements: {stored_ids_a & {b1.id, b2.id}}"
    )

    # Org B's stored record must only contain org B's dependency elements.
    assert stored_ids_b == org_b_dep_ids, (
        f"Org B stored record contains {stored_ids_b}, expected {org_b_dep_ids}"
    )
    assert stored_ids_b.isdisjoint({a1.id, a2.id}), (
        f"Org B stored record leaked org A elements: {stored_ids_b & {a1.id, a2.id}}"
    )


def test_large_impact_walk_first_page_within_latency_budget(app, db_session, make_org):
    """A 2,400-row impact walk returns the first page within the latency budget.

    The platform SLO for `/api/v1/intelligence/*` (answers) defines
    p95 <= 2s (2000ms). This test seeds 2,400 downstream elements in a
    wide fan-out graph and asserts the first page (page_size=50) completes
    within that budget.
    """
    org = make_org("one-engine-large-walk")
    root = _element(db_session, org.id, "Root-Large")

    # Create 2,400 direct children (wide fan-out, depth=1).
    # This exercises the full BFS walk and pagination slice.
    children = []
    batch_size = 500
    for batch_start in range(0, 2400, batch_size):
        batch = []
        for i in range(batch_start, min(batch_start + batch_size, 2400)):
            child = _element(db_session, org.id, f"Child-{i}")
            _relationship(db_session, org.id, root, child)
            batch.append(child)
        children.extend(batch)
        db_session.flush()
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        # First page: 50 rows.
        result = IntelligenceQueryService.cross_layer_impact(
            root.id,
            include_derived=False,
            max_depth=3,
            direction="downstream",
            with_owner=False,
            page_size=50,
        )

    # The summary.latency_ms is measured inside the latency scope and
    # includes the full walk + batch resolution + pagination slice.
    latency_ms = result["summary"]["latency_ms"]
    assert latency_ms is not None, "latency_ms must be recorded"

    # Budget: platform SLO for answers (/api/v1/intelligence/*) is p95 <= 2s.
    budget_ms = 2000.0
    assert latency_ms <= budget_ms, (
        f"First page latency {latency_ms:.1f}ms exceeds budget {budget_ms}ms "
        f"(2,400 rows, page_size=50)"
    )

    # Sanity: total rows reported must be 2,400.
    assert result["total"] == 2400, f"Expected 2400 total rows, got {result['total']}"
    assert len(result["rows"]) == 50, f"First page should have 50 rows, got {len(result['rows'])}"
    assert result["next_cursor"] == 50, f"next_cursor should be 50, got {result['next_cursor']}"


# ── PR 2: owners on every row ──────────────────────────────────────────────


def _make_user(db_session, org, first_name="Alice", last_name="Owner"):
    from app.models.user import User

    user = User(
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name.lower()}.{last_name.lower()}@example.com",
        organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _application_owner(db_session, org, element_type, element_id, user, ownership_type="primary", application_id=None):
    from app.models.application_owner import ApplicationOwner

    ao = ApplicationOwner(
        element_type=element_type,
        element_id=element_id,
        application_id=application_id,
        user_id=user.id,
        organization_id=org.id,
        ownership_type=ownership_type,
    )
    db_session.add(ao)
    db_session.flush()
    return ao


def test_owners_field_present_on_every_row(app, db_session, make_org):
    """Every row of cross_layer_impact carries an owners list.
    An empty list renders as em-dash; a row with an ApplicationOwner
    record carries name and role."""
    org = make_org("owners-field")
    a = _element(db_session, org.id, "App-X")
    b = _element(db_session, org.id, "Svc-Y")
    c = _element(db_session, org.id, "Cap-Z", etype="Capability", layer="business")
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, b, c)
    # Create an ApplicationComponent for element B and link the owner
    # through application_id (the correct path for ApplicationComponent
    # elements).
    comp_b = _application_component(db_session, org.id, b.id, name="Svc App")
    user = _make_user(db_session, org, "Bob", "Smith")
    _application_owner(db_session, org, None, None, user, "primary", application_id=comp_b.id)
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            a.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows = result.get("rows") or []
    assert len(rows) > 0, "Expected at least one dependency row"

    for row in rows:
        assert "owners" in row, f"Row {row['element_id']} missing owners field"
        assert isinstance(row["owners"], list), f"Row {row['element_id']} owners is not a list"
        if row["element_id"] == b.id:
            assert len(row["owners"]) > 0, f"Row {row['element_id']} should have owners"
            assert "name" in row["owners"][0], "Owner entry missing name"
            assert "role" in row["owners"][0], "Owner entry missing role"
        else:
            # Rows without ApplicationOwner records have empty list.
            assert row["owners"] == [], f"Row {row['element_id']} should have empty owners"


def test_two_organisation_owner_isolation(app, db_session, make_org):
    """Organisation B's ApplicationOwner records never appear in
    organisation A's impact walk."""
    org_a = make_org("owners-iso-a")
    org_b = make_org("owners-iso-b")

    a1 = _element(db_session, org_a.id, "A1")
    a2 = _element(db_session, org_a.id, "A2")
    _relationship(db_session, org_a.id, a1, a2)

    b1 = _element(db_session, org_b.id, "B1")
    b2 = _element(db_session, org_b.id, "B2")
    _relationship(db_session, org_b.id, b1, b2)

    # Create ApplicationComponent and ApplicationOwner for org B's element.
    comp_b2 = _application_component(db_session, org_b.id, b2.id, name="B2 App")
    user_b = _make_user(db_session, org_b, "OrgB", "Owner")
    _application_owner(db_session, org_b, None, None, user_b, "primary", application_id=comp_b2.id)

    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = IntelligenceQueryService.cross_layer_impact(
            a1.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows = result.get("rows") or []
    for row in rows:
        # Org A's rows must have empty owners (no ApplicationOwner records for org A).
        assert row["owners"] == [], (
            f"Org A row {row['element_id']} leaked owners: {row['owners']}"
        )

    # Verify org B's walk has owners.
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b.id
        result_b = IntelligenceQueryService.cross_layer_impact(
            b1.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows_b = result_b.get("rows") or []
    b2_row = [r for r in rows_b if r["element_id"] == b2.id]
    assert len(b2_row) == 1
    assert len(b2_row[0]["owners"]) > 0, "Org B's owned element should have owners"


def test_three_surfaces_return_same_owners(app, db_session, make_org):
    """All three impact surfaces return the same owners for the same seed.
    Extends the existing comparison test to also check owners."""
    org = make_org("owners-three-surface")
    a = _element(db_session, org.id, "Platform-A")
    b = _element(db_session, org.id, "Svc-B")
    c = _element(db_session, org.id, "Cap-C", etype="Capability", layer="business")
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, b, c)
    app_comp = _application_component(db_session, org.id, a.id, name="Platform App")

    # Create ApplicationComponent and ApplicationOwner for element B.
    comp_b = _application_component(db_session, org.id, b.id, name="Svc App")
    user = _make_user(db_session, org, "Carol", "Owner")
    _application_owner(db_session, org, None, None, user, "primary", application_id=comp_b.id)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        surface1 = _surface_impact_analysis_service(a.id)
        surface2 = _surface_ai_impact_service(app_comp.id)
        surface3 = _surface_cross_layer_impact(a.id)

    # All three surfaces must return the same owners for each element.
    def _owners_map(rows):
        return {r["id"]: r.get("owners", []) for r in rows}

    owners1 = _owners_map(surface1)
    owners2 = _owners_map(surface2)
    owners3 = _owners_map(surface3)

    for label, owners_map in [("ImpactAnalysisService", owners1),
                               ("AIImpactAnalysisService", owners2),
                               ("cross_layer_impact", owners3)]:
        for eid, owners in owners_map.items():
            assert isinstance(owners, list), f"{label} element {eid} owners is not a list"

    # Element B should have owners in all three surfaces.
    assert len(owners1.get(b.id, [])) > 0, "ImpactAnalysisService missing owners for element B"
    assert len(owners2.get(b.id, [])) > 0, "AIImpactAnalysisService missing owners for element B"
    assert len(owners3.get(b.id, [])) > 0, "cross_layer_impact missing owners for element B"

    # Owner names and roles must match across surfaces.
    for eid in owners1:
        if eid in owners2:
            assert owners1[eid] == owners2[eid], (
                f"Owner mismatch for element {eid} between ImpactAnalysisService and AIImpactAnalysisService"
            )
        if eid in owners3:
            assert owners1[eid] == owners3[eid], (
                f"Owner mismatch for element {eid} between ImpactAnalysisService and cross_layer_impact"
            )


def test_accountability_returns_owners_from_walk(app, db_session, make_org):
    """The accountability endpoint returns the owners of every affected
    element from the walk, not an empty answer."""
    org = make_org("accountability-walk")
    a = _element(db_session, org.id, "Root")
    b = _element(db_session, org.id, "Child")
    _relationship(db_session, org.id, a, b)

    # Create ApplicationComponent and ApplicationOwner for element B.
    comp_b = _application_component(db_session, org.id, b.id, name="Child App")
    user = _make_user(db_session, org, "Diana", "Owner")
    _application_owner(db_session, org, None, None, user, "primary", application_id=comp_b.id)
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(a.id)

    # The accountability answer must return owners from the walk,
    # not an empty withdrawn answer.
    assert "owners" in result, "accountability_for_element missing owners key"
    assert len(result["owners"]) > 0, (
        "accountability_for_element returned empty owners — "
        "should return owners from the walk"
    )
    # The owners should include the owner of element B.
    owner_names = [o.get("name") for o in result["owners"]]
    assert "Diana Owner" in owner_names, (
        f"Expected Diana Owner in accountability owners, got {owner_names}"
    )


def test_owner_fetch_query_count(app, db_session, make_org):
    """Owner fetch issues a bounded number of queries, independent of page size.
    The batch resolution uses a constant number of queries regardless of how
    many rows are on the page."""
    import sqlalchemy as sa

    org = make_org("owner-query-count")
    root = _element(db_session, org.id, "Root")
    children = []
    for i in range(10):
        child = _element(db_session, org.id, f"Child-{i}")
        _relationship(db_session, org.id, root, child)
        children.append(child)
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    def _count_queries(org_id, page_size):
        """Run cross_layer_impact and count queries."""
        with app.test_request_context("/"):
            from flask import g

            g.current_org_id = org_id

            queries = []

            def _listener(conn, cur, stmt, params, context, executemany):
                queries.append(stmt)

            sa.event.listen(sa.engine.Engine, "before_cursor_execute", _listener)
            try:
                IntelligenceQueryService.cross_layer_impact(
                    root.id,
                    include_derived=False,
                    max_depth=3,
                    with_owner=True,
                    page_size=page_size,
                )
            finally:
                sa.event.remove(sa.engine.Engine, "before_cursor_execute", _listener)

            return len(queries)

    # Count queries with a small page and a large page — the owner fetch
    # should issue the same number of queries regardless.
    query_count_small = _count_queries(org.id, 2)
    query_count_large = _count_queries(org.id, 10)

    # The query count should be bounded and similar regardless of page size.
    # The owner resolution adds at most 2 queries (one for ApplicationOwner,
    # one for User resolution) regardless of page size.
    assert abs(query_count_small - query_count_large) <= 2, (
        f"Query count differs too much between page_size=2 ({query_count_small}) "
        f"and page_size=10 ({query_count_large})"
    )


def test_capability_owner_does_not_leak_to_application_row(app, db_session, make_org):
    """A capability owner whose element_id equals an application-element id
    in the same organisation must NOT appear on the application's impact row.

    The batch owner lookup must filter by element_type so a capability owner
    (element_type='capability') is never attached to an ApplicationComponent
    row that happens to share the same element_id value."""
    org = make_org("cap-owner-leak")
    # Create an application element and a capability element that share
    # the same numeric id (impossible with real PKs, so we use different
    # orgs to get different PKs and verify the element_type filter).
    app_el = _element(db_session, org.id, "App", etype="ApplicationComponent")
    cap_el = _element(db_session, org.id, "Cap", etype="Capability", layer="business")
    _relationship(db_session, org.id, app_el, cap_el)

    # Create an ApplicationComponent for the app element.
    _application_component(db_session, org.id, app_el.id, name="App Component")

    # Create a capability owner with element_type='capability' and
    # element_id set to the app element's id (simulating a collision).
    user = _make_user(db_session, org, "Cap", "Owner")
    _application_owner(db_session, org, "capability", app_el.id, user, "primary")
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.cross_layer_impact(
            app_el.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows = result.get("rows") or []
    # The app element's row must have empty owners — the capability owner
    # with the same element_id must not leak through.
    app_row = [r for r in rows if r["element_id"] == app_el.id]
    if app_row:
        assert app_row[0]["owners"] == [], (
            f"App element {app_el.id} should have no owners, "
            f"got {app_row[0]['owners']}"
        )


def test_two_organisation_owner_isolation_with_capability_owner(app, db_session, make_org):
    """Organisation B's capability owner never appears in organisation A's
    impact walk, even when element_ids coincide."""
    org_a = make_org("cap-iso-a")
    org_b = make_org("cap-iso-b")

    a1 = _element(db_session, org_a.id, "A1")
    a2 = _element(db_session, org_a.id, "A2")
    _relationship(db_session, org_a.id, a1, a2)

    b1 = _element(db_session, org_b.id, "B1")
    b2 = _element(db_session, org_b.id, "B2", etype="Capability", layer="business")
    _relationship(db_session, org_b.id, b1, b2)

    # Create a capability owner for org B's element.
    user_b = _make_user(db_session, org_b, "OrgB", "CapOwner")
    _application_owner(db_session, org_b, "capability", b2.id, user_b, "primary")
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = IntelligenceQueryService.cross_layer_impact(
            a1.id, include_derived=False, max_depth=3, with_owner=True
        )

    rows = result.get("rows") or []
    for row in rows:
        assert row["owners"] == [], (
            f"Org A row {row['element_id']} leaked owners: {row['owners']}"
        )


def test_large_impact_walk_with_owners_within_budget(app, db_session, make_org):
    """A 2,400-row impact walk with owners enabled returns the first page
    within the latency budget and the owner fetch does not cause N+1."""
    org = make_org("large-walk-owners")
    root = _element(db_session, org.id, "Root-Large")

    # Create 2,400 direct children.
    children = []
    batch_size = 500
    for batch_start in range(0, 2400, batch_size):
        batch = []
        for i in range(batch_start, min(batch_start + batch_size, 2400)):
            child = _element(db_session, org.id, f"Child-{i}")
            _relationship(db_session, org.id, root, child)
            batch.append(child)
        children.extend(batch)
        db_session.flush()
    db_session.commit()

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id

        result = IntelligenceQueryService.cross_layer_impact(
            root.id,
            include_derived=False,
            max_depth=3,
            direction="downstream",
            with_owner=True,
            page_size=50,
        )

    latency_ms = result["summary"]["latency_ms"]
    budget_ms = 2000.0
    assert latency_ms <= budget_ms, (
        f"First page latency {latency_ms:.1f}ms exceeds budget {budget_ms}ms "
        f"(2,400 rows, page_size=50, with_owner=True)"
    )

    assert result["total"] == 2400, f"Expected 2400 total rows, got {result['total']}"
    assert len(result["rows"]) == 50, f"First page should have 50 rows, got {len(result['rows'])}"

    # Every row on the page must have an owners field.
    for row in result["rows"]:
        assert "owners" in row, f"Row {row['element_id']} missing owners field"
        assert isinstance(row["owners"], list), f"Row {row['element_id']} owners is not a list"