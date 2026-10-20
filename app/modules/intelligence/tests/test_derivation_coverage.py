"""Derivation coverage report: elements per ArchiMate layer, the derived
relationship count, the latest run's yield, and the bounded list of elements
with no derived relationship -- all read from stored rows, per organisation.

Fixtures (app, db_session, make_org, client, login_as) come from tests/conftest.
"""

from __future__ import annotations

import uuid

from app.datetime_helpers import utcnow

from app.modules.intelligence.services.derivation_coverage_service import (
    derivation_coverage,
)


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"coverage-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Coverage",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, layer="application", type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _derived(db_session, org_id, source, target):
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    row = DerivedRelationship(
        organization_id=org_id,
        source_element_id=source.id,
        target_element_id=target.id,
        derived_type="Serving",
        rule_id="serving-rule",
        chain=[source.id],
        chain_element_ids=[source.id],
        depth=1,
        confidence=1.0,
        provenance="derivation",
        engine_version="1.0",
        computed_at=utcnow(),
        stale=False,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _run(db_session, org_id, derived_count=2):
    from app.modules.intelligence.models.derivation_run import DerivationRun

    row = DerivationRun(
        organization_id=org_id,
        explicit_count=4,
        derived_count=derived_count,
        ratio=0.5,
        engine_version="1.0",
        trigger="on_demand",
        finished_at=utcnow(),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _missing_count_direct(db_session, org_id):
    """Elements with no non-stale derived relationship, counted straight from
    the stored tables -- the figure the report's missing list must match."""
    from sqlalchemy import text

    return db_session.execute(
        text(
            "SELECT count(*) FROM archimate_elements e "
            "WHERE e.organization_id = :org_id "
            "AND NOT EXISTS ("
            "    SELECT 1 FROM archimate_derived_relationships d "
            "    WHERE d.organization_id = :org_id "
            "      AND d.stale = FALSE "
            "      AND (d.source_element_id = e.id OR d.target_element_id = e.id)"
            ")"
        ),
        {"org_id": org_id},
    ).scalar_one()


# --- the route -------------------------------------------------------------


def test_coverage_report_shows_elements_per_layer_and_yield_from_stored_rows(
    app, db_session, make_org, client, login_as
):
    org = make_org("coverage-org")
    user = _user(db_session, org.id)
    app_layer = _element(db_session, org.id, "App Server", layer="application")
    _element(db_session, org.id, "Billing Process", layer="business")
    tech_layer = _element(db_session, org.id, "Database Node", layer="technology")
    _derived(db_session, org.id, app_layer, tech_layer)
    _run(db_session, org.id, derived_count=3)
    db_session.commit()

    login_as(client, user)
    response = client.get("/intelligence/coverage")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for fragment in ("Application", "Business", "Technology", "Derived relationships"):
        assert fragment in html, f"missing {fragment!r} in coverage report"
    # Check the latest run yield via the service and the HTML
    coverage = derivation_coverage(org.id)
    assert coverage["latest_run"] is not None
    assert coverage["latest_run"].derived_count == 3
    assert f'<dd class="text-foreground">{coverage["latest_run"].derived_count}</dd>' in html

    coverage = derivation_coverage(org.id)
    assert coverage["latest_run"] is not None
    assert coverage["latest_run"].derived_count == 3
    assert {entry["layer"] for entry in coverage["layers"]} == {
        "application",
        "business",
        "technology",
    }


def test_organisation_b_never_sees_organisation_a_figures(
    app, db_session, make_org, client, login_as
):
    """Identical element ids in both organisations; B never counts A's rows."""
    org_a = make_org("coverage-a")
    org_b = make_org("coverage-b")
    user_a = _user(db_session, org_a.id)
    user_b = _user(db_session, org_b.id)
    a1 = _element(db_session, org_a.id, "Alpha", layer="application")
    a2 = _element(db_session, org_a.id, "Beta", layer="application")
    _derived(db_session, org_a.id, a1, a2)
    _run(db_session, org_a.id, derived_count=1)
    b1 = _element(db_session, org_b.id, "Alpha", layer="application")
    b2 = _element(db_session, org_b.id, "Beta", layer="application")
    db_session.commit()

    login_as(client, user_a)
    data_a = client.get("/intelligence/coverage").get_data(as_text=True)
    coverage_a = derivation_coverage(org_a.id)
    assert coverage_a["latest_run"] is not None
    assert coverage_a["derived_count"] == 1
    assert coverage_a["missing_total"] == 0
    assert "Application" in data_a

    login_as(client, user_b)
    data_b = client.get("/intelligence/coverage").get_data(as_text=True)
    coverage_b = derivation_coverage(org_b.id)
    # Check service's return value for org B
    assert coverage_b["latest_run"] is None
    assert coverage_b["derived_count"] == 0
    assert coverage_b["missing_total"] == 2
    assert {entry["layer"] for entry in coverage_b["layers"]} == {"application"}
    assert coverage_b["layers"][0]["missing_count"] == 2
    # Check that the missing list contains the two elements of org B
    missing_ids = {element_id for element_id, _, _ in coverage_b["missing"]}
    assert missing_ids == {b1.id, b2.id}
    # Check HTML: org B's HTML should not contain org A's element ids in the missing rows
    assert f'data-element-id="{a1.id}"' not in data_b
    assert f'data-element-id="{a2.id}"' not in data_b
    # Org B's HTML should contain its own element ids in the missing rows
    assert f'data-element-id="{b1.id}"' in data_b
    assert f'data-element-id="{b2.id}"' in data_b


def test_organisation_b_never_sees_organisation_a_figures_with_ids_from_98(
    app, db_session, make_org, client, login_as
):
    """Same isolation as org_b, but element ids start at 98 -- high enough
    that a bare id substring would match unrelated markup (a '99+'
    notification badge, say), so only data-element-id assertions are safe."""
    from app.extensions import db

    db_session.execute(
        db.text("ALTER SEQUENCE archimate_elements_id_seq RESTART WITH 98")
    )

    org_a = make_org("coverage-high-a")
    org_b = make_org("coverage-high-b")
    user_b = _user(db_session, org_b.id)
    a1 = _element(db_session, org_a.id, "Alpha", layer="application")
    a2 = _element(db_session, org_a.id, "Beta", layer="application")
    _derived(db_session, org_a.id, a1, a2)
    _run(db_session, org_a.id, derived_count=1)
    b1 = _element(db_session, org_b.id, "Alpha", layer="application")
    b2 = _element(db_session, org_b.id, "Beta", layer="application")
    db_session.commit()

    assert a1.id >= 98
    assert a2.id >= 98
    assert b1.id >= 98
    assert b2.id >= 98

    login_as(client, user_b)
    data_b = client.get("/intelligence/coverage").get_data(as_text=True)
    coverage_b = derivation_coverage(org_b.id)
    assert coverage_b["latest_run"] is None
    assert coverage_b["derived_count"] == 0
    assert coverage_b["missing_total"] == 2
    assert {entry["layer"] for entry in coverage_b["layers"]} == {"application"}
    assert coverage_b["layers"][0]["missing_count"] == 2
    missing_ids = {element_id for element_id, _, _ in coverage_b["missing"]}
    assert missing_ids == {b1.id, b2.id}
    assert f'data-element-id="{a1.id}"' not in data_b
    assert f'data-element-id="{a2.id}"' not in data_b
    assert f'data-element-id="{b1.id}"' in data_b
    assert f'data-element-id="{b2.id}"' in data_b


def test_no_run_shows_none_yield_not_zero(app, db_session, make_org, client, login_as):
    """An organisation with elements but no completed run shows a dash for
    yield: None from the service, never 0."""
    org = make_org("coverage-no-run")
    user = _user(db_session, org.id)
    _element(db_session, org.id, "App Server", layer="application")
    db_session.commit()

    coverage = derivation_coverage(org.id)
    assert coverage["latest_run"] is None
    assert coverage["derived_count"] == 0
    assert coverage["missing_total"] == 1

    login_as(client, user)
    html = client.get("/intelligence/coverage").get_data(as_text=True)
    assert "—" in html


def test_missing_list_count_matches_direct_query(app, db_session, make_org):
    org = make_org("coverage-missing")
    a = _element(db_session, org.id, "Alpha", layer="application")
    b = _element(db_session, org.id, "Beta", layer="application")
    c = _element(db_session, org.id, "Gamma", layer="business")
    _derived(db_session, org.id, a, c)  # Alpha and Gamma have a derived relationship
    db_session.commit()

    coverage = derivation_coverage(org.id)
    assert coverage["missing_total"] == _missing_count_direct(db_session, org.id)
    assert coverage["missing_total"] == 1  # only Beta has none
    assert len(coverage["missing"]) == 1
    assert coverage["missing"][0][0] == b.id


def test_missing_list_is_bounded_to_fifty(app, db_session, make_org):
    org = make_org("coverage-bounded")
    [
        _element(db_session, org.id, f"Element {index}", layer="application")
        for index in range(60)
    ]
    db_session.commit()

    from app.modules.intelligence.services.derivation_coverage_service import (
        derivation_coverage,
    )

    coverage = derivation_coverage(org.id)
    assert coverage["missing_total"] == 60
    assert len(coverage["missing"]) == 50


def test_layer_filter_shows_only_that_layers_missing_elements(
    app, db_session, make_org, client, login_as
):
    org = make_org("coverage-layer")
    user = _user(db_session, org.id)
    app1 = _element(db_session, org.id, "App One", layer="application")
    app2 = _element(db_session, org.id, "App Two", layer="application")
    biz = _element(db_session, org.id, "Biz One", layer="business")
    _derived(db_session, org.id, app1, biz)  # App One has a derived relationship
    db_session.commit()

    login_as(client, user)
    html = client.get("/intelligence/coverage?layer=application").get_data(as_text=True)
    coverage = derivation_coverage(org.id, layer="application")
    # Check service's return value
    assert coverage["missing_total"] == 1
    assert coverage["missing"][0][0] == app2.id
    # Check HTML: the missing element row for app2 should be present, and for app1 absent
    assert f'data-element-id="{app2.id}"' in html
    assert f'data-element-id="{app1.id}"' not in html

    coverage = derivation_coverage(org.id, layer="application")
    layers = coverage["layers"]
    assert len(layers) == 1
    assert layers[0]["layer"] == "application"
    assert layers[0]["element_count"] == 2
    assert layers[0]["missing_count"] == 1  # only App Two
    assert coverage["missing_total"] == 1
    assert coverage["missing"][0][0] == app2.id


def test_coverage_route_rejects_anonymous(app, client):
    response = client.get("/intelligence/coverage")
    assert response.status_code in (301, 302, 401)


def test_derivation_coverage_empty_projection_returns_empty_structure(
    app, db_session, make_org, monkeypatch
):
    """When get_projection returns None or a projection with no nodes,
    the service returns the empty structure (no layers, latest_run from readers).
    """
    org = make_org("empty-projection-org")
    db_session.commit()

    # First, test when get_projection returns None
    def mock_get_projection_none(_org_id):
        return None

    monkeypatch.setattr(
        "app.modules.intelligence.services.derivation_coverage_service.get_projection",
        mock_get_projection_none,
    )

    coverage = derivation_coverage(org.id)
    assert coverage["organization_id"] == org.id
    assert coverage["layers"] == []
    assert coverage["derived_count"] == 0
    assert coverage["latest_run"] is None
    assert coverage["missing_total"] == 0
    assert coverage["missing"] == []

    # Second, test when get_projection returns a projection with no nodes
    class MockProjection:
        def nodes(self):
            return []

    def mock_get_projection_empty_nodes(_org_id):
        return MockProjection()

    monkeypatch.setattr(
        "app.modules.intelligence.services.derivation_coverage_service.get_projection",
        mock_get_projection_empty_nodes,
    )

    coverage = derivation_coverage(org.id)
    assert coverage["organization_id"] == org.id
    assert coverage["layers"] == []
    assert coverage["derived_count"] == 0
    assert coverage["latest_run"] is None
    assert coverage["missing_total"] == 0
    assert coverage["missing"] == []


# The following line is to prevent pytest from collecting the MockProjection class
# as a test. It is not a test function.
__all__ = [
    "test_coverage_report_shows_elements_per_layer_and_yield_from_stored_rows",
    "test_organisation_b_never_sees_organisation_a_figures",
    "test_no_run_shows_none_yield_not_zero",
    "test_missing_list_count_matches_direct_query",
    "test_missing_list_is_bounded_to_fifty",
    "test_layer_filter_shows_only_that_layers_missing_elements",
    "test_coverage_route_rejects_anonymous",
    "test_derivation_coverage_empty_projection_returns_empty_structure",
    "test_organisation_b_never_sees_organisation_a_figures_with_ids_from_98",
]
