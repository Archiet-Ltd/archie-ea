"""The impact REST route and the additive canonical-endpoint extension."""

from __future__ import annotations

import uuid

# Fixtures (app, db_session, make_org, tenant_ctx, client, login_as) are
# discovered via app/modules/conftest.py's import of
# tests.conftest -- pytest resolves fixtures by name without this module
# importing them itself (see test_derivation_runner.py for the same
# pattern). No import needed here.


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"t004-{uuid.uuid4().hex[:10]}@example.com",
        first_name="T004",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


# --- Acceptance criterion 1: full API-1 payload -------------------------------


def test_route_returns_full_api1_payload(app, db_session, make_org, client, login_as):
    org = make_org("route-payload")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{a.id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    data = body["data"]
    assert "rows" in data and "summary" in data
    for key in ("explicit_count", "derived_count", "stale_count", "derivation_state", "latency_ms"):
        assert key in data["summary"]
    for row in data["rows"]:
        # NEW-5: element_id must be present -- this task's whole point is
        # "what stops", so a row that cannot name what element it is about
        # fails the feature's own purpose. Only the internal join key
        # (_endpoints) is stripped, never element_id.
        assert set(("element_id", "relation", "owner", "reason")).issubset(row.keys())
        assert row["element_id"] is not None
        assert "_endpoints" not in row.keys()


# --- Acceptance criterion 2: include_derived at HTTP layer --------------------


def test_route_include_derived_true_and_false_at_http_layer(app, db_session, make_org, client, login_as):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship
    import datetime as _dt

    org = make_org("route-derived")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _relationship(db_session, org.id, a, b)
    derived = DerivedRelationship(
        organization_id=org.id,
        source_element_id=a.id,
        target_element_id=c.id,
        derived_type="Serving",
        rule_id="R1",
        chain=[1, 2],
        chain_element_ids=[a.id, c.id],
        depth=2,
        confidence=1.0,
        provenance="derivation",
        engine_version="v1",
        computed_at=_dt.datetime.utcnow(),
        stale=False,
    )
    db_session.add(derived)
    db_session.commit()

    login_as(client, user)
    resp_off = client.get(f"/api/v1/intelligence/impact/{a.id}?include_derived=false")
    assert resp_off.status_code == 200
    rows_off = resp_off.get_json()["data"]["rows"]
    assert all(r["relation"]["kind"] == "explicit" for r in rows_off)

    resp_on = client.get(f"/api/v1/intelligence/impact/{a.id}?include_derived=true")
    assert resp_on.status_code == 200
    rows_on = resp_on.get_json()["data"]["rows"]
    kinds = {r["relation"]["kind"] for r in rows_on}
    assert "derived" in kinds


# --- Acceptance criterion 3: parameter validation -----------------------------


def test_route_parameter_validation_returns_400(app, db_session, make_org, client, login_as):
    org = make_org("route-validation")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{a.id}?include_derived=maybe")
    assert resp.status_code == 400
    login_as(client, user)
    resp2 = client.get(f"/api/v1/intelligence/impact/{a.id}?max_depth=11")
    assert resp2.status_code == 400
    login_as(client, user)
    resp3 = client.get(f"/api/v1/intelligence/impact/{a.id}?direction=sideways")
    assert resp3.status_code == 400


# --- Acceptance criterion 4: 404 indistinguishability -------------------------


def test_404_indistinguishability_cross_tenant_vs_nonexistent(app, db_session, make_org, client, login_as):
    org_a = make_org("route-404-a")
    org_b = make_org("route-404-b")
    user_a = _user(db_session, org_a.id)
    foreign_element = _element(db_session, org_b.id, "Foreign")
    db_session.commit()

    login_as(client, user_a)
    resp_cross_tenant = client.get(f"/api/v1/intelligence/impact/{foreign_element.id}")
    login_as(client, user_a)
    resp_nonexistent = client.get("/api/v1/intelligence/impact/999999999")

    assert resp_cross_tenant.status_code == 404 == resp_nonexistent.status_code
    body_a = resp_cross_tenant.get_json()
    body_b = resp_nonexistent.get_json()
    body_a.pop("meta", None)
    body_b.pop("meta", None)
    assert body_a == body_b
    assert body_a["error"]["code"] == "NOT_FOUND"
    # NEW-4: the element_not_found DE-14 reason code is genuinely reachable
    # here (this 404 body, not a fabricated 200 "reasons" list) -- identical
    # on both branches, so D3 indistinguishability holds.
    assert body_a["error"]["details"] == {"reason": "element_not_found"}


# --- Acceptance criterion 5: derivation_state / stale gating -----------------


def test_derivation_state_values_and_stale_gating(app, db_session, make_org, client, login_as):
    org = make_org("route-derivation-state")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/impact/{a.id}")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["summary"]["derivation_state"] in ("current", "stale", "not_computed")


# --- Acceptance criteria 6/7/8: canonical endpoint additive extension --------


def test_canonical_endpoint_additive_extension_unchanged_request(app, db_session, make_org, client, login_as):
    org = make_org("canonical-additive")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"element_id": a.id, "scenario": "modification"},
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    for key in ("risk_level", "total_score", "breakdown", "affected_elements", "summary", "analysis_id"):
        assert key in data
    # New keys are present additively.
    assert "derived_elements" in data
    assert "derivation_state" in data


def test_projection_integrity_no_commercially_sensitive_fields(app, db_session, make_org, client, login_as):
    org = make_org("canonical-projection")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"element_id": a.id, "scenario": "modification", "include_derived": True},
    )
    assert resp.status_code == 200
    for element in resp.get_json()["data"]["affected_elements"]:
        assert set(element.keys()) == {"id", "name", "type", "level"}


def test_app_id_branch_rejects_include_derived_and_max_depth(app, db_session, make_org, client, login_as):
    """M5: include_derived/max_depth are validated then silently ignored on
    the app_id branch before this fix -- a 200 with no derived_elements/
    derivation_state and no signal the params were dropped is
    indistinguishable from "derivation ran and found nothing". Reject
    explicitly instead.
    """
    org = make_org("canonical-app-id-rejects-derived-params")
    user = _user(db_session, org.id)
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name="Reject App", organization_id=org.id)
    db_session.add(comp)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"app_id": comp.id, "scenario": "modification", "include_derived": True},
    )
    assert resp.status_code == 400

    login_as(client, user)
    resp2 = client.post(
        "/api/v1/impact/analyze",
        json={"app_id": comp.id, "scenario": "modification", "max_depth": 4},
    )
    assert resp2.status_code == 400

    # An unchanged app_id request (no include_derived/max_depth keys at all)
    # is still untouched.
    login_as(client, user)
    resp3 = client.post(
        "/api/v1/impact/analyze",
        json={"app_id": comp.id, "scenario": "modification"},
    )
    assert resp3.status_code == 200


def test_app_id_branch_characterisation_untouched(app, db_session, make_org, client, login_as):
    """D2: the app_id branch has no projection today and is untouched by this
    task -- this pins its current response keys.
    """
    org = make_org("canonical-app-id-characterisation")
    user = _user(db_session, org.id)
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(name="Char App", organization_id=org.id)
    db_session.add(comp)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"app_id": comp.id, "scenario": "modification"},
    )
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    # Pre-existing contract shape from _to_contract_shape -- no
    # derived_elements/derivation_state on this branch (D2/D8: out of scope).
    expected_keys = {"risk_level", "total_score", "breakdown", "affected_elements", "diagram", "summary", "analysis_id"}
    assert set(data.keys()) == expected_keys


# --- B1 fix: a crashed derived-elements lookup must not fabricate "not_computed"


def test_derived_elements_lookup_failure_does_not_fabricate_not_computed(
    app, db_session, make_org, client, login_as, monkeypatch
):
    """B1: ``not_computed`` is a real, meaningful ``derivation_state`` (per
    acceptance item 7 -- "derivation not yet computed" with a one-click run
    action). A crashed lookup (DB error, ``MultipleResultsFound``, etc.) must
    never be silently converted into that same state -- it must surface as a
    genuine failure (500), not a state indistinguishable from "hasn't run
    yet".
    """
    import app.modules.intelligence.services.query_service as query_service_module

    org = make_org("canonical-derived-lookup-failure")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated derived-elements lookup failure")

    monkeypatch.setattr(
        query_service_module.IntelligenceQueryService, "cross_layer_impact", staticmethod(_boom)
    )

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"element_id": a.id, "scenario": "modification", "include_derived": True},
    )

    assert resp.status_code == 500
    body = resp.get_json()
    # Never silently absorbed into a 200 with derivation_state="not_computed".
    assert body.get("data") != "not_computed"
    payload_text = str(body)
    assert '"derivation_state": "not_computed"' not in payload_text


# --- NEW-1 fix: a nonexistent/cross-tenant element_id must 404, never a
# fabricated 200 with derivation_state="not_computed" -------------------------


def test_canonical_endpoint_nonexistent_element_id_is_404_not_fabricated(
    app, db_session, make_org, client, login_as
):
    """NEW-1: ImpactAnalysisService.analyze_change_impact never checks the
    element exists, so before this fix a nonexistent element_id returned a
    fully-formed 200 with derivation_state="not_computed" -- indistinguishable
    from a real element whose derivation genuinely hasn't run yet. The route
    must verify the element exists first and 404, not fabricate.
    """
    org = make_org("canonical-nonexistent-element")
    user = _user(db_session, org.id)
    db_session.commit()

    login_as(client, user)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"element_id": 999999999, "scenario": "retirement", "include_derived": True},
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    # Never a 200-shaped fabricated derivation_state for something that was
    # never analysed.
    assert "not_computed" not in str(body)


def test_canonical_endpoint_cross_tenant_element_id_is_404_not_fabricated(
    app, db_session, make_org, client, login_as
):
    """NEW-1 (cross-tenant variant): a real element belonging to a DIFFERENT
    tenant must also 404, not return a fabricated 200 -- ``ArchiMateElement``
    carries ``TenantMixin`` so the caller's own tenant filter already makes
    the row invisible; the route's existence check must honour that, not
    bypass it via a service that queries without the ORM tenant filter.
    """
    org_a = make_org("canonical-cross-tenant-a")
    org_b = make_org("canonical-cross-tenant-b")
    user_a = _user(db_session, org_a.id)
    foreign_element = _element(db_session, org_b.id, "Foreign")
    db_session.commit()

    login_as(client, user_a)
    resp = client.post(
        "/api/v1/impact/analyze",
        json={"element_id": foreign_element.id, "scenario": "retirement", "include_derived": True},
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert "not_computed" not in str(body)


# --- Acceptance criterion 9: one blueprint ------------------------------------


def test_single_blueprint_bound_to_intelligence_prefix(app):
    matches = [
        rule for rule in app.url_map.iter_rules() if str(rule).startswith("/api/v1/intelligence")
    ]
    endpoints = {rule.endpoint.split(".")[0] for rule in matches}
    assert endpoints == {"intelligence_api"}
    impact_rules = [r for r in matches if "/impact/" in str(r)]
    assert len(impact_rules) == 1
