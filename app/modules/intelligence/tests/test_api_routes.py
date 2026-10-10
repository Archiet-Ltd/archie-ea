"""T-003 / task 03 acceptance criteria 3, 4, 7, 8."""

from __future__ import annotations

import uuid



def _make_user(db_session, org, *, email=None, enterprise_role=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()

    user = User(
        email=email or f"t003-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=admin_role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_element(db_session, org_id, name_hint, type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(
        name=f"E-{name_hint}", type=type_, layer="application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_relationship(db_session, org_id, source, target, type_):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _insert_derived_row(db_session, org_id, source, target, **overrides):
    import datetime as _dt

    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    params = dict(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Association",
        rule_id="fallback:X:X",
        chain=[],
        chain_element_ids=[source.id, target.id],
        depth=1,
        confidence="1.00",
        provenance="derivation",
        engine_version="1.0.0",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
        stale_since=None,
        stale_reason=None,
    )
    params.update(overrides)
    row = DerivedRelationship(**params)
    db_session.add(row)
    db_session.flush()
    return row


# --- Acceptance item 3 (brief 10): auth + CSRF on the recompute endpoint ----


def test_recompute_endpoint_requires_login(client):
    resp = client.post("/api/v1/intelligence/derivation/recompute", json={"scope": "tenant"})
    assert resp.status_code in (302, 401)


def test_recompute_endpoint_rejects_post_without_csrf_token(app, db_session, make_org, client):
    """CSRF is normally disabled under TESTING; assert it explicitly here by
    re-enabling WTF_CSRF_ENABLED for the duration of this one test."""
    org = make_org("api-csrf")
    user = _make_user(db_session, org)
    db_session.commit()

    from tests._session_test_helpers import mint_test_sid

    sid = mint_test_sid(user.id, organization_id=org.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sess["_sid"] = sid

    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/api/v1/intelligence/derivation/recompute",
            json={"scope": "tenant"},
        )
        assert resp.status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_recompute_endpoint_succeeds_and_returns_derivation_result(
    app, db_session, make_org, client, login_as
):
    org = make_org("api-recompute-ok")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    _make_relationship(db_session, org.id, a, b, "Composition")
    _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/intelligence/derivation/recompute",
        json={"scope": "tenant"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert "derived_count" in body["data"]


def test_recompute_endpoint_rejects_non_tenant_scope(app, db_session, make_org, client, login_as):
    org = make_org("api-scope-reject")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/intelligence/derivation/recompute",
        json={"scope": "estate"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_recompute_endpoint_reports_lock_held_not_500(app, db_session, make_org, client, login_as):
    from app.jobs.tenant_safe_job import job_lock
    from app.modules.intelligence.services.recompute_job import per_tenant_lock_name

    org = make_org("api-recompute-locked")
    user = _make_user(db_session, org)
    db_session.commit()
    org_id = org.id

    login_as(client, user)

    with app.app_context():
        with job_lock(per_tenant_lock_name(org_id), required=True):
            resp = client.post(
                "/api/v1/intelligence/derivation/recompute",
                json={"scope": "tenant"},
            )

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "RECOMPUTE_LOCKED"


# --- Acceptance item 8: request session survives the harness call ----------


def test_on_demand_endpoint_leaves_request_session_and_org_context_intact(
    app, db_session, make_org, client, login_as
):
    org = make_org("api-session-survives")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/intelligence/derivation/recompute",
        json={"scope": "tenant"},
    )
    assert resp.status_code == 200

    # A second request on the same client, same session, must still resolve
    # to the same user/org -- proving login state and g.current_org_id were
    # not corrupted by the harness's db.session.remove() calls inside the
    # first request.
    resp2 = client.get("/api/v1/intelligence/derived/999999")
    assert resp2.status_code == 404  # tenant-scoped 404, not a 401/redirect


# --- Acceptance item 4 (brief 3, API-2): provenance expansion --------------


def test_provenance_expansion_resolves_chain_to_source_target(
    app, db_session, make_org, client, login_as
):
    org = make_org("api-provenance")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    r1 = _make_relationship(db_session, org.id, a, b, "Composition")
    r2 = _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()

    r1_id, r2_id = r1.id, r2.id  # captured before any request detaches these

    row = _insert_derived_row(
        db_session, org.id, a, c, chain=[r1_id, r2_id], depth=2, derived_type="Serving"
    )
    db_session.commit()
    row_id = row.id

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["id"] == row_id
    expanded = body["expanded_chain"]
    assert [e["id"] for e in expanded] == [r1_id, r2_id]
    for e in expanded:
        assert e["derived_from"] == row_id
        assert "source_id" in e and "target_id" in e


def test_provenance_expansion_cross_tenant_id_is_404_not_leak(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("api-prov-a")
    org_b = make_org("api-prov-b")
    user_b = _make_user(db_session, org_b)

    a1 = _make_element(db_session, org_a.id, "a1")
    a2 = _make_element(db_session, org_a.id, "a2")
    db_session.commit()

    row_a = _insert_derived_row(db_session, org_a.id, a1, a2, chain=[], depth=1)
    db_session.commit()
    row_a_id = row_a.id

    login_as(client, user_b)
    resp = client.get(f"/api/v1/intelligence/derived/{row_a_id}")
    assert resp.status_code == 404


# --- Round-1 refuter finding D4: read path must not use tenant_scope() -----


def test_get_derived_fact_does_not_corrupt_request_globals(app, db_session, make_org):
    """``tenant_scope()`` is a background-job harness: its ``finally`` clause
    sets ``g.current_org = None`` and only restores ``g.current_org_id``,
    never ``g.current_org``. Using it inside a live request's read path
    clobbers whatever the normal request lifecycle had cached there. Proves
    the read path (used by GET /api/v1/intelligence/derived/<id>) leaves a
    pre-existing g.current_org untouched."""
    from flask import g

    from app.modules.intelligence.services.derived_facts import get_derived_fact

    org = make_org("d4-no-tenant-scope")
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    db_session.commit()
    row = _insert_derived_row(db_session, org.id, a, b, chain=[], depth=1)
    db_session.commit()
    row_id, org_id = row.id, org.id

    with app.test_request_context():
        g.current_org_id = org_id
        sentinel = object()  # stands in for the real ORM object a request caches
        g.current_org = sentinel

        result = get_derived_fact(org_id, row_id)

        assert result is not None
        assert g.current_org_id == org_id
        assert g.current_org is sentinel, (
            "the read path must not clobber g.current_org -- tenant_scope() "
            "would silently set it to None as a side effect of being used "
            "outside its intended background-job lifecycle"
        )


# --- Round-1 refuter finding D6: an unresolved chain link must not vanish ---


def test_expanded_chain_marks_an_unresolved_link_instead_of_dropping_it(
    app, db_session, make_org, client, login_as
):
    """A chain id with no matching ArchiMateRelationship (e.g. deleted after
    the derived row was computed) must appear as an explicit unresolved
    marker, not silently vanish -- a shorter-but-complete-looking array reads
    as a complete derivation chain when it is not."""
    org = make_org("d6-unresolved-chain")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    r1 = _make_relationship(db_session, org.id, a, b, "Composition")
    db_session.commit()
    r1_id = r1.id

    missing_rel_id = r1_id + 999_999  # never a real relationship id

    row = _insert_derived_row(
        db_session, org.id, a, b, chain=[r1_id, missing_rel_id], depth=2, derived_type="Serving"
    )
    db_session.commit()
    row_id = row.id

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/derived/{row_id}")
    assert resp.status_code == 200
    expanded = resp.get_json()["data"]["expanded_chain"]

    assert len(expanded) == 2, "an unresolved link must not shorten the array"
    assert expanded[0]["id"] == r1_id
    assert "source_id" in expanded[0]

    assert expanded[1]["id"] == missing_rel_id
    assert expanded[1].get("unresolved") is True
    assert "source_id" not in expanded[1]


def test_module_registers_exactly_sixteen_routes(app):
    """The impact, risk, portfolio, programme, strategy, accountability,
    data, compliance, traceability, value-streams-at-risk, yield, catalogue
    list, catalogue run and ask routes all mount on this same existing
    blueprint rather than a new one each. Still exactly one blueprint, now
    sixteen routes on it -- all six lenses of the catalogue, the L7 data
    lens, the L6 compliance lens, the traceability check over the impact
    walk, the Strategic value-streams-at-risk surface,
    recompute/derived/yield, and R1-B39's query catalogue (list + run) and
    plain-language ask endpoints.
    """
    rules = [
        rule for rule in app.url_map.iter_rules() if rule.endpoint.startswith("intelligence_api.")
    ]
    endpoints = {rule.endpoint for rule in rules}
    assert endpoints == {
        "intelligence_api.recompute_derivation",
        "intelligence_api.get_derived_fact_provenance",
        "intelligence_api.value_streams_at_risk",
        "intelligence_api.cross_layer_impact",
        "intelligence_api.risk_for_element",
        "intelligence_api.portfolio_component_for_element",
        "intelligence_api.programme_for_element",
        "intelligence_api.strategy_for_element",
        "intelligence_api.accountability_for_element",
        "intelligence_api.data_for_element",
        "intelligence_api.compliance_for_element",
        "intelligence_api.traceability_check",
        "intelligence_api.derivation_yield",
        "intelligence_api.query_catalogue_list",
        "intelligence_api.query_catalogue_run",
        "intelligence_api.ask_nl_question",
    }



# --- the derived_id on an impact row addresses the provenance endpoint --------


def test_impact_row_derived_id_addresses_the_provenance_endpoint(
    app, db_session, make_org, client, login_as
):
    """The reason ``relation.derived_id`` exists: a caller looking at a derived
    row on the impact API can open ``GET /derived/<derived_id>`` for exactly
    that fact, and both report the same ``engine_version``."""
    org = make_org("api-derived-id-roundtrip")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "a")
    b = _make_element(db_session, org.id, "b")
    c = _make_element(db_session, org.id, "c")
    r1 = _make_relationship(db_session, org.id, a, b, "Composition")
    r2 = _make_relationship(db_session, org.id, b, c, "Serving")
    db_session.commit()
    r1_id, r2_id = r1.id, r2.id

    row = _insert_derived_row(
        db_session,
        org.id,
        a,
        c,
        chain=[r1_id, r2_id],
        chain_element_ids=[a.id, b.id, c.id],
        depth=2,
        derived_type="Serving",
        engine_version="1.2.0",
    )
    db_session.commit()
    row_id = row.id

    login_as(client, user)
    impact = client.get(f"/api/v1/intelligence/impact/{a.id}?include_derived=true")
    assert impact.status_code == 200
    derived_rows = [
        r for r in impact.get_json()["data"]["rows"] if r["relation"]["kind"] == "derived"
    ]
    assert len(derived_rows) == 1
    relation = derived_rows[0]["relation"]
    assert relation["derived_id"] == row_id
    assert relation["engine_version"] == "1.2.0"

    login_as(client, user)
    provenance = client.get(f"/api/v1/intelligence/derived/{relation['derived_id']}")
    assert provenance.status_code == 200
    fact = provenance.get_json()["data"]
    assert fact["id"] == relation["derived_id"]
    assert fact["engine_version"] == relation["engine_version"]


# --- L6: GET /api/v1/intelligence/risk/<element_id> ---------------------------


def test_risk_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/risk/1")
    assert resp.status_code in (302, 401)


def test_risk_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("risk-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/risk/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_risk_endpoint_element_with_no_risk_returns_honest_empty(
    app, db_session, make_org, client, login_as
):
    org = make_org("risk-route-empty")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/risk/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["risks"] == []
    assert data["reasons"] == ["no_risk_recorded"]


def test_risk_endpoint_returns_risk_with_its_own_blast_radius(
    app, db_session, make_org, client, login_as
):
    from app.models.risk import Risk

    org = make_org("risk-route-blast")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    b = _make_element(db_session, org.id, "B")
    _make_relationship(db_session, org.id, a, b, "Serving")
    risk = Risk(
        organization_id=org.id,
        archimate_element_id=a.id,
        title="Single point of failure",
        likelihood=5,
        impact=5,
        owner="platform-team",
    )
    db_session.add(risk)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/risk/{a.id}?include_derived=true")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["reasons"] == []
    assert len(data["risks"]) == 1
    row = data["risks"][0]
    assert row["title"] == "Single point of failure"
    assert row["risk_score"] == 25
    assert row["risk_level"] == "critical"
    assert row["owner"] == "platform-team"
    assert len(row["affected_rows"]) == 1
    assert row["affected_rows"][0]["element_id"] == b.id


def test_risk_endpoint_cross_tenant_element_is_404_not_leak(
    app, db_session, make_org, client, login_as
):
    from app.models.risk import Risk

    org_a = make_org("risk-route-tenant-a")
    org_b = make_org("risk-route-tenant-b")
    user_b = _make_user(db_session, org_b)
    a = _make_element(db_session, org_a.id, "A")
    risk = Risk(organization_id=org_a.id, archimate_element_id=a.id, title="A's risk", likelihood=3, impact=3)
    db_session.add(risk)
    db_session.commit()

    login_as(client, user_b)
    resp = client.get(f"/api/v1/intelligence/risk/{a.id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


# --- L3: GET /api/v1/intelligence/portfolio/<element_id> ----------------------


def test_portfolio_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/portfolio/1")
    assert resp.status_code in (302, 401)


def test_portfolio_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("portfolio-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/portfolio/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_portfolio_endpoint_non_application_element_returns_honest_reason(
    app, db_session, make_org, client, login_as
):
    org = make_org("portfolio-route-none")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A", type_="BusinessActor")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/portfolio/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["application_component_id"] is None
    assert data["reasons"] == ["no_application_component"]


def test_portfolio_endpoint_resolves_the_linked_component(
    app, db_session, make_org, client, login_as
):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("portfolio-route-resolved")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org.id, archimate_element_id=a.id)
    db_session.add(component)
    db_session.commit()
    component_id = component.id

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/portfolio/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["application_component_id"] == component_id
    assert data["reasons"] == []


# --- L5: GET /api/v1/intelligence/programme/<element_id> --------------------


def test_programme_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/programme/1")
    assert resp.status_code in (302, 401)


def test_programme_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("programme-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/programme/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_programme_endpoint_element_with_no_work_package_returns_honest_empty(
    app, db_session, make_org, client, login_as
):
    org = make_org("programme-route-empty")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["work_packages"] == []
    assert data["reasons"] == ["no_work_package_recorded"]


def test_programme_endpoint_returns_work_package_with_its_own_blast_radius(
    app, db_session, make_org, client, login_as
):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("programme-route-blast")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    b = _make_element(db_session, org.id, "B")
    _make_relationship(db_session, org.id, a, b, "Serving")
    wp = UnifiedWorkPackage(
        name="Migrate A",
        archimate_element_id=a.id,
        organization_id=org.id,
        business_capability="Test",
        status="in_progress",
        progress_percentage=25.0,
        estimated_cost=50000.0,
        actual_cost=45000.0,
    )
    db_session.add(wp)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}?include_derived=true")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["reasons"] == []
    assert len(data["work_packages"]) == 1
    row = data["work_packages"][0]
    assert row["name"] == "Migrate A"
    assert row["cost_reason"] is None
    assert round(row["cost_variance_pct"], 2) == -10.0
    assert len(row["affected_rows"]) == 1
    assert row["affected_rows"][0]["element_id"] == b.id


def test_programme_endpoint_redacts_cost_for_a_role_without_budget_authority(
    app, db_session, make_org, client, login_as
):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("programme-route-redact")
    user = _make_user(db_session, org, enterprise_role="solution_architect")
    a = _make_element(db_session, org.id, "A")
    db_session.add(UnifiedWorkPackage(
        name="Migrate A", archimate_element_id=a.id, organization_id=org.id,
        business_capability="Test",
        estimated_cost=50000.0, actual_cost=45000.0,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    row = resp.get_json()["data"]["work_packages"][0]
    assert row["cost_variance_pct"] is None
    assert row["cost_reason"] == "financial_data_restricted"


def test_programme_endpoint_does_not_redact_cost_for_cto(
    app, db_session, make_org, client, login_as
):
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("programme-route-no-redact")
    user = _make_user(db_session, org, enterprise_role="cto")
    a = _make_element(db_session, org.id, "A")
    db_session.add(UnifiedWorkPackage(
        name="Migrate A", archimate_element_id=a.id, organization_id=org.id,
        business_capability="Test",
        estimated_cost=50000.0, actual_cost=45000.0,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    row = resp.get_json()["data"]["work_packages"][0]
    assert row["cost_variance_pct"] == -10.0
    assert row["cost_reason"] is None


def test_programme_endpoint_cross_tenant_element_is_404_not_leak(
    app, db_session, make_org, client, login_as
):
    from app.models.unified_work_package import UnifiedWorkPackage

    org_a = make_org("programme-route-tenant-a")
    org_b = make_org("programme-route-tenant-b")
    user_b = _make_user(db_session, org_b)
    a = _make_element(db_session, org_a.id, "A")
    wp = UnifiedWorkPackage(
        name="Tenant A's work", archimate_element_id=a.id, organization_id=org_a.id,
        business_capability="Test",
    )
    db_session.add(wp)
    db_session.commit()

    login_as(client, user_b)
    resp = client.get(f"/api/v1/intelligence/programme/{a.id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_strategy_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/strategy/1")
    assert resp.status_code in (302, 401)


def test_strategy_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("strategy-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/strategy/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_strategy_endpoint_element_with_no_initiative_returns_honest_empty(
    app, db_session, make_org, client, login_as
):
    org = make_org("strategy-route-empty")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/strategy/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["initiatives"] == []
    assert data["reasons"] == ["no_initiative_linked"]


def test_strategy_endpoint_returns_initiative_with_its_own_blast_radius(
    app, db_session, make_org, client, login_as
):
    from app.models.enterprise_intelligence import PortfolioInitiative

    org = make_org("strategy-route-blast")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    b = _make_element(db_session, org.id, "B")
    _make_relationship(db_session, org.id, a, b, "Serving")
    initiative = PortfolioInitiative(
        name="Transform A",
        archimate_element_id=a.id,
        status="Active",
        completion_percentage=25,
        total_budget=50000.0,
        spent_to_date=45000.0,
    )
    db_session.add(initiative)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/strategy/{a.id}?include_derived=true")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["reasons"] == []
    assert len(data["initiatives"]) == 1
    row = data["initiatives"][0]
    assert row["name"] == "Transform A"
    assert row["budget_reason"] is None
    assert round(row["budget_variance_pct"], 2) == -10.0
    assert len(row["affected_rows"]) == 1
    assert row["affected_rows"][0]["element_id"] == b.id


def test_strategy_endpoint_redacts_budget_for_a_role_without_budget_authority(
    app, db_session, make_org, client, login_as
):
    from app.models.enterprise_intelligence import PortfolioInitiative

    org = make_org("strategy-route-redact")
    user = _make_user(db_session, org, enterprise_role="business_architect")
    a = _make_element(db_session, org.id, "A")
    db_session.add(PortfolioInitiative(
        name="Transform A", archimate_element_id=a.id, status="Active",
        total_budget=50000.0, spent_to_date=45000.0,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/strategy/{a.id}")
    row = resp.get_json()["data"]["initiatives"][0]
    assert row["budget_variance_pct"] is None
    assert row["budget_reason"] == "financial_data_restricted"


def test_strategy_endpoint_does_not_redact_budget_for_portfolio_manager(
    app, db_session, make_org, client, login_as
):
    from app.models.enterprise_intelligence import PortfolioInitiative

    org = make_org("strategy-route-no-redact")
    user = _make_user(db_session, org, enterprise_role="portfolio_manager")
    a = _make_element(db_session, org.id, "A")
    db_session.add(PortfolioInitiative(
        name="Transform A", archimate_element_id=a.id, status="Active",
        total_budget=50000.0, spent_to_date=45000.0,
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/strategy/{a.id}")
    row = resp.get_json()["data"]["initiatives"][0]
    assert row["budget_variance_pct"] == -10.0
    assert row["budget_reason"] is None


def test_strategy_endpoint_cross_tenant_element_is_404_not_leak(
    app, db_session, make_org, client, login_as
):
    from app.models.enterprise_intelligence import PortfolioInitiative

    org_a = make_org("strategy-route-tenant-a")
    org_b = make_org("strategy-route-tenant-b")
    user_b = _make_user(db_session, org_b)
    a = _make_element(db_session, org_a.id, "A")
    initiative = PortfolioInitiative(
        name="Tenant A's initiative", archimate_element_id=a.id, status="Active",
    )
    db_session.add(initiative)
    db_session.commit()

    login_as(client, user_b)
    resp = client.get(f"/api/v1/intelligence/strategy/{a.id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_accountability_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/accountability/1")
    assert resp.status_code in (302, 401)


def test_accountability_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("accountability-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/accountability/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_accountability_endpoint_says_when_a_component_has_no_current_ownership(
    app, db_session, make_org, client, login_as
):
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("accountability-route-none")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org.id, archimate_element_id=a.id)
    db_session.add(component)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/accountability/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["owners"] == []
    assert data["capacity_not_available"] is True
    assert data["reasons"] == ["no_ownership_records", "capacity_not_available"]


def test_accountability_endpoint_serves_current_ownership_without_personal_data(
    app, db_session, make_org, client, login_as
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org = make_org("accountability-route-owner")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org.id, archimate_element_id=a.id)
    db_session.add(component)
    db_session.flush()
    unit = OrganizationUnit(
        organization_id=org.id, name="Finance", unit_type="Department", head_of_unit="Pat Head",
        contact_email="unit@example.com",
    )
    db_session.add(unit)
    db_session.flush()
    db_session.add(ApplicationOwnership(
        organization_id=org.id, application_id=component.id, organization_unit_id=unit.id,
        ownership_type="Business Owner", primary_contact="Jordan Owner", contact_email="jordan@example.com",
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/accountability/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert [o["ownership_type"] for o in data["owners"]] == ["Business Owner"]
    assert data["owners"][0]["organization_unit"]["name"] == "Finance"
    assert data["reasons"] == ["capacity_not_available"]
    body = resp.get_data(as_text=True)
    for hidden in ("Jordan Owner", "jordan@example.com", "unit@example.com", "Pat Head"):
        assert hidden not in body


def test_accountability_endpoint_cross_tenant_element_is_404_not_leak(
    app, db_session, make_org, client, login_as
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit

    org_a = make_org("accountability-route-tenant-a")
    org_b = make_org("accountability-route-tenant-b")
    user_b = _make_user(db_session, org_b)
    a = _make_element(db_session, org_a.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org_a.id, archimate_element_id=a.id)
    db_session.add(component)
    db_session.flush()
    unit = OrganizationUnit(organization_id=org_a.id, name="Tenant A Finance", unit_type="Department")
    db_session.add(unit)
    db_session.flush()
    db_session.add(ApplicationOwnership(
        organization_id=org_a.id, application_id=component.id, organization_unit_id=unit.id, ownership_type="Business Owner",
    ))
    db_session.commit()

    login_as(client, user_b)
    resp = client.get(f"/api/v1/intelligence/accountability/{a.id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


# --- L7: the Data lens ---------------------------------------------------------


def test_data_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/data/1")
    assert resp.status_code in (302, 401)


def test_data_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("data-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/data/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_data_endpoint_foreign_element_reads_the_same_as_a_missing_one(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("data-route-a"), make_org("data-route-b")
    user = _make_user(db_session, org_a)
    foreign = _make_element(db_session, org_b.id, "foreign")
    db_session.commit()
    user_id, foreign_id = user.id, foreign.id
    db_session.expunge_all()

    from app.models.user import User

    # tenant-scoping-ok: test fixture reloading the user this test just created, after expunge_all
    login_as(client, db_session.get(User, user_id))
    resp = client.get(f"/api/v1/intelligence/data/{foreign_id}")

    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_data_endpoint_returns_objects_and_flows_for_a_real_element(
    app, db_session, make_org, client, login_as
):
    from app.models.all_missing_models import DataLineage
    from app.models.application_layer import DataObject

    org = make_org("data-route-ok")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    b = _make_element(db_session, org.id, "B")
    db_session.add(DataObject(name="Orders", archimate_element_id=a.id, organization_id=org.id,
                              data_steward="Sam", pii_fields='["email"]'))
    db_session.add(DataLineage(name="A to B", archimate_element_id=a.id,
                               target_archimate_element_id=b.id, organization_id=org.id))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/data/{a.id}")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert [o["name"] for o in data["data_objects"]] == ["Orders"]
    assert data["data_objects"][0]["steward"] == "Sam"
    assert [f["direction"] for f in data["flows"]] == ["out"]
    assert data["reasons"] == []
    assert "pii_fields" not in resp.get_data(as_text=True) and "email" not in str(data["data_objects"])


# --- Compliance (under L6) -----------------------------------------------------


def test_compliance_endpoint_requires_login(client):
    resp = client.get("/api/v1/intelligence/compliance/1")
    assert resp.status_code in (302, 401)


def test_compliance_endpoint_unknown_element_is_404(app, db_session, make_org, client, login_as):
    org = make_org("compliance-route-404")
    user = _make_user(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = client.get("/api/v1/intelligence/compliance/999999999")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "element_not_found"


def test_compliance_endpoint_returns_controls_for_a_real_application(
    app, db_session, make_org, client, login_as
):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    org = make_org("compliance-route-ok")
    user = _make_user(db_session, org)
    a = _make_element(db_session, org.id, "A")
    component = ApplicationComponent(name="A App", organization_id=org.id, archimate_element_id=a.id)
    framework = RegulatoryFramework(code="RT-FW", name="Route framework", category="security")
    db_session.add_all([component, framework])
    db_session.flush()
    control = ComplianceControl(framework_id=framework.id, control_code="R-1", title="Route control")
    db_session.add(control)
    db_session.flush()
    db_session.add(ApplicationComplianceControl(
        organization_id=org.id, application_id=component.id, control_id=control.id,
        implementation_status="planned", evidence_url="https://secret.example/x",
    ))
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/compliance/{a.id}")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert [c["code"] for c in data["controls"]] == ["R-1"]
    assert data["controls"][0]["evidence_url_recorded"] is True
    assert "no_policy_scan_recorded" in data["reasons"]
    assert "secret.example" not in resp.get_data(as_text=True)
