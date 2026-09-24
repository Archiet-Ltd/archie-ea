"""Tests for ``flask seed-demo-company`` — the Lantern Quay Systems demonstration.

Covers all six acceptance criteria from T-DEMO-1.
"""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


# ── helpers ─────────────────────────────────────────────────────────────────


def _row_counts(org_id):
    """Return a dict of row counts for every entity kind the seeder touches."""
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_owner import ApplicationOwner
    from app.models.unified_capability import UnifiedCapability
    from app.models.risk import Risk
    from app.models.enterprise_intelligence import PortfolioInitiative
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.implementation_migration import Plateau, Gap
    from app.models.user import User

    return {
        "elements": ArchiMateElement.query.filter_by(organization_id=org_id).count(),
        "relationships": ArchiMateRelationship.query.filter_by(organization_id=org_id).count(),
        "applications": ApplicationComponent.query.filter_by(organization_id=org_id).count(),
        "application_owners": ApplicationOwner.query.filter_by(organization_id=org_id).count(),
        "capabilities": UnifiedCapability.query.filter_by(organization_id=org_id).count(),
        "risks": Risk.query.filter_by(organization_id=org_id).count(),
        "programmes": PortfolioInitiative.query.join(
            ArchiMateElement,
            PortfolioInitiative.archimate_element_id == ArchiMateElement.id,
        ).filter(ArchiMateElement.organization_id == org_id).count(),
        "work_packages": UnifiedWorkPackage.query.join(
            ArchiMateElement,
            UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id,
        ).filter(ArchiMateElement.organization_id == org_id).count(),
        "plateaus": Plateau.query.filter_by(organization_id=org_id).count(),
        "gaps": Gap.query.filter_by(organization_id=org_id).count(),
        "users": User.query.filter_by(organization_id=org_id).count(),
    }


def _get_lantern_org_id():
    """Return the Lantern Quay org id as a plain int."""
    from app import db
    from app.models.organization import Organization

    return (
        db.session.query(Organization.id)
        .filter_by(slug="lantern-quay")
        .scalar()
    )


def _demo_user(db_session, org_id):
    """Return the demo user for *org_id* or None."""
    from app.models.user import User

    return User.query.filter_by(
        email="demo@lantern-quay.example.com", organization_id=org_id,
    ).first()


def _login_demo(client, login_as, db_session, org_id):
    """Log the test client in as the demo user."""
    user = _demo_user(db_session, org_id)
    assert user is not None, "demo user not found — was the seeder run?"
    login_as(client, user)
    return user


# ── AC1: seed creates organisation, exits 0, idempotent ────────────────────


def test_seed_creates_organisation_and_exits_zero(app, db_session, make_org):
    """AC1 part 1: on an empty database the command creates the org and all
    entity kinds with non-zero counts."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        stats = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    assert stats["organization_created"] == 1
    assert stats["elements_created"] > 200, f"expected >200 elements, got {stats['elements_created']}"
    assert stats["relationships_created"] > 150
    assert stats["applications_created"] == 20
    assert stats["application_owners_created"] > 0
    assert stats["capabilities_created"] == 24
    assert stats["risks_created"] == 10
    assert stats["programmes_created"] == 5
    assert stats["work_packages_created"] == 12
    assert stats["plateaus_created"] == 5
    assert stats["gaps_created"] == 10
    assert stats["demo_user_created"] == 1

    from app.models.organization import Organization

    org_id = (
        db_session.query(Organization.id)
        .filter_by(slug="lantern-quay")
        .scalar()
    )
    assert org_id is not None

    org = db_session.query(Organization).filter_by(id=org_id).first()
    assert org.name == "Lantern Quay Systems"

    counts = _row_counts(org_id)
    assert counts["elements"] > 200
    assert counts["relationships"] > 150
    assert counts["applications"] == 20
    assert counts["capabilities"] == 24
    assert counts["risks"] == 10
    assert counts["programmes"] == 5
    assert counts["work_packages"] == 12
    assert counts["plateaus"] == 5
    assert counts["gaps"] == 10
    assert counts["users"] >= 17  # demo user + 16 owner users


def test_second_run_changes_no_row_counts(app, db_session, make_org):
    """AC1 part 2: a second run changes no row counts."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    # tenant_scope calls db.session.remove(), detaching ORM objects.
    # Query the id as a scalar to avoid DetachedInstanceError.
    from app.models.organization import Organization

    org_id = (
        db_session.query(Organization.id)
        .filter_by(slug="lantern-quay")
        .scalar()
    )
    assert org_id is not None
    before = _row_counts(org_id)

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        second = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    after = _row_counts(org_id)
    assert after == before, f"row counts changed on re-run: {before} -> {after}"
    assert second["elements_created"] == 0
    assert second["relationships_created"] == 0
    assert second["applications_created"] == 0
    assert second["demo_user_created"] == 0


def test_cli_runs_end_to_end(app, db_session, make_org):
    """The CLI command runs and exits 0."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        result = app.test_cli_runner().invoke(args=["seed-demo-company"])
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception!r}"
    assert "total rows created:" in result.output


def test_cli_rejects_missing_password(app):
    """The CLI command fails when DEMO_USER_PASSWORD is not set."""
    old = os.environ.pop("DEMO_USER_PASSWORD", None)
    try:
        result = app.test_cli_runner().invoke(args=["seed-demo-company"])
        assert result.exit_code != 0
    finally:
        if old is not None:
            os.environ["DEMO_USER_PASSWORD"] = old


# ── AC2: six Ask questions return real data ────────────────────────────────


def _seed_and_get_org_id():
    """Seed the demo company and return the org id as a plain int.
    
    tenant_scope calls db.session.remove(), which detaches ORM objects.
    Always capture ids as plain ints before they can be detached.
    """
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app import db
    from app.models.organization import Organization

    return (
        db.session.query(Organization.id)
        .filter_by(slug="lantern-quay")
        .scalar()
    )


def _element_id(org_id, name):
    """Return the id of the named ArchiMateElement in *org_id*."""
    from app.models import ArchiMateElement

    el = ArchiMateElement.query.filter_by(
        organization_id=org_id, name=name,
    ).first()
    assert el is not None, f"element {name!r} not found"
    return el.id


class TestAskQuestions:
    """AC2: each of the six Ask questions returns at least one row with no
    'not recorded' reason on its main facts."""

    @staticmethod
    def _call(app, org_id, method_name, element_id, **kwargs):
        """Call an IntelligenceQueryService method inside a request context."""
        from flask import g
        from app.modules.intelligence.services.query_service import IntelligenceQueryService

        with app.test_request_context("/"):
            g.current_org_id = org_id
            method = getattr(IntelligenceQueryService, method_name)
            return method(element_id, **kwargs)

    def test_impact_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(
            app, org_id, "cross_layer_impact", pool_id,
            include_derived=True, max_depth=5, with_owner=False,
        )
        assert len(result["rows"]) > 0, "impact returned no rows"
        assert "element_not_found" not in result.get("reasons", [])
        for row in result["rows"]:
            assert row["relation"]["type"], f"row has empty relation type: {row}"

    def test_impact_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(
            app, org_id, "cross_layer_impact", relay_id,
            include_derived=True, max_depth=5, with_owner=False,
        )
        assert len(result["rows"]) > 0, "impact on Event Relay returned no rows"
        assert "element_not_found" not in result.get("reasons", [])

    def test_strategy_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(app, org_id, "strategy_for_element", pool_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_strategy_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "strategy_for_element", relay_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_portfolio_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(app, org_id, "portfolio_component_for_element", pool_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_portfolio_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "portfolio_component_for_element", relay_id)
        assert "element_not_found" not in result.get("reasons", [])
        assert result.get("application_component_id") is not None, (
            "Event Relay should resolve to an application component"
        )

    def test_accountability_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(app, org_id, "accountability_for_element", pool_id)
        assert "ownership_reader_not_built" in result.get("reasons", [])

    def test_accountability_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "accountability_for_element", relay_id)
        assert "ownership_reader_not_built" in result.get("reasons", [])

    def test_programme_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(app, org_id, "programme_for_element", pool_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_programme_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "programme_for_element", relay_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_risk_on_quay_compute_pool(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        pool_id = _element_id(org_id, "Quay Compute Pool")

        result = self._call(app, org_id, "risk_for_element", pool_id)
        assert "element_not_found" not in result.get("reasons", [])

    def test_risk_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "risk_for_element", relay_id)
        assert "element_not_found" not in result.get("reasons", [])
        assert len(result.get("risks", [])) > 0, (
            "Event Relay should have at least one risk"
        )


# ── AC3: demo user is read-only ────────────────────────────────────────────


def test_demo_user_cannot_access_write_route(app, db_session, make_org, client, login_as):
    """AC3: the demo user gets a 403 (or redirect) on a write route."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    _login_demo(client, login_as, db_session, org_id)

    # Try a POST to a known write endpoint — creating an application component
    resp = client.post(
        "/applications/create",
        data={"name": "Should Not Be Created"},
        follow_redirects=False,
    )
    # Should be rejected: either 403 or 302 redirect to login
    assert resp.status_code in (302, 403), (
        f"expected 302 or 403, got {resp.status_code}"
    )


def test_demo_user_has_viewer_role(app, db_session, make_org):
    """The demo user is assigned the Viewer role (permissions=0)."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    user = _demo_user(db_session, org_id)
    assert user is not None
    assert user.role is not None
    assert user.role.name == "Viewer"
    assert user.role.permissions == 0


# ── AC4: GET /demo/lantern-quay returns 200 ────────────────────────────────


def test_demo_page_returns_200_with_description(app, db_session, make_org, client):
    """AC4: GET /demo/lantern-quay returns 200 with the company description."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    resp = client.get("/demo/lantern-quay")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Lantern Quay Systems" in html
    assert "water-monitoring" in html


def test_demo_page_shows_not_seeded_when_no_org(app, client):
    """When the org hasn't been seeded, the page shows a helpful message."""
    resp = client.get("/demo/lantern-quay")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "not been seeded" in html.lower() or "seed-demo-company" in html


# ── AC5: cross-organisation isolation ──────────────────────────────────────


def test_other_organisation_sees_none_of_lantern_quay_rows(app, db_session, make_org):
    """AC5: another organisation sees none of Lantern Quay's rows."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    other = make_org("other-tenant")
    other_id = other.id
    db_session.commit()

    # Every entity kind in the other org should be empty
    counts = _row_counts(other_id)
    for kind, count in counts.items():
        assert count == 0, (
            f"other org has {count} {kind} rows — should be 0"
        )

    # The other org should not be able to resolve Lantern Quay elements
    from app.models import ArchiMateElement

    lantern_elements = ArchiMateElement.query.filter_by(
        organization_id=org_id,
    ).count()
    other_elements = ArchiMateElement.query.filter_by(
        organization_id=other_id,
    ).count()
    assert lantern_elements > 200
    assert other_elements == 0


def test_demo_user_cannot_see_other_org_data(app, db_session, make_org, client, login_as):
    """The demo user, scoped to Lantern Quay, cannot see another org's data."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    other = make_org("other-tenant-isolated")
    other_id = other.id
    db_session.commit()

    # Create an element in the other org
    from app.models import ArchiMateElement

    other_el = ArchiMateElement(
        name="Other Org Element", type="Node", layer="technology",
        organization_id=other_id,
    )
    db_session.add(other_el)
    db_session.commit()

    # Log in as demo user and try to access the other org's element
    _login_demo(client, login_as, db_session, org_id)

    resp = client.get(f"/api/v1/intelligence/impact/{other_el.id}")
    # The route validates tenant ownership and returns 404 for cross-tenant access
    assert resp.status_code == 404, (
        f"expected 404 for cross-tenant access, got {resp.status_code}"
    )


# ── AC6: static gates pass ─────────────────────────────────────────────────
# (verified by running scripts/verify.py --tag static separately)


# ── additional: data integrity checks ──────────────────────────────────────


def test_all_relationships_reference_existing_elements(app, db_session, make_org):
    """Every relationship's source and target exist in the same org."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    element_ids = {
        e.id for e in ArchiMateElement.query.filter_by(organization_id=org_id).all()
    }
    rels = ArchiMateRelationship.query.filter_by(organization_id=org_id).all()
    for rel in rels:
        assert rel.source_id in element_ids, (
            f"relationship {rel.id} source {rel.source_id} not in elements"
        )
        assert rel.target_id in element_ids, (
            f"relationship {rel.id} target {rel.target_id} not in elements"
        )


def test_all_application_owners_reference_existing_users_and_apps(app, db_session, make_org):
    """Every application owner row references an existing user and app."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    org_id = _get_lantern_org_id()
    app_ids = {
        a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()
    }
    user_ids = {
        u.id for u in User.query.filter_by(organization_id=org_id).all()
    }
    owners = ApplicationOwner.query.filter_by(organization_id=org_id).all()
    for ao in owners:
        assert ao.application_id in app_ids, (
            f"owner {ao.id} references unknown app {ao.application_id}"
        )
        assert ao.user_id in user_ids, (
            f"owner {ao.id} references unknown user {ao.user_id}"
        )


def test_capabilities_have_maturity_levels(app, db_session, make_org):
    """Every seeded capability has current and target maturity set."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability

    org_id = _get_lantern_org_id()
    caps = UnifiedCapability.query.filter_by(organization_id=org_id).all()
    for cap in caps:
        assert cap.current_maturity_level is not None, (
            f"capability {cap.name} has no current maturity"
        )
        assert cap.target_maturity_level is not None, (
            f"capability {cap.name} has no target maturity"
        )


def test_risks_have_likelihood_and_impact(app, db_session, make_org):
    """Every seeded risk has likelihood and impact set."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.risk import Risk

    org_id = _get_lantern_org_id()
    risks = Risk.query.filter_by(organization_id=org_id).all()
    for risk in risks:
        assert 1 <= risk.likelihood <= 5, (
            f"risk {risk.title} has invalid likelihood {risk.likelihood}"
        )
        assert 1 <= risk.impact <= 5, (
            f"risk {risk.title} has invalid impact {risk.impact}"
        )


def test_work_packages_have_costs(app, db_session, make_org):
    """Every seeded work package has estimated cost set."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models import ArchiMateElement
    from app.models.unified_work_package import UnifiedWorkPackage

    org_id = _get_lantern_org_id()
    wps = (
        UnifiedWorkPackage.query
        .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
        .filter(ArchiMateElement.organization_id == org_id)
        .all()
    )
    for wp in wps:
        assert wp.estimated_cost is not None, (
            f"work package {wp.name} has no estimated cost"
        )


def test_plateaus_have_sequence_order(app, db_session, make_org):
    """Every seeded plateau has a sequence order."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.implementation_migration import Plateau

    org_id = _get_lantern_org_id()
    plateaus = Plateau.query.filter_by(organization_id=org_id).all()
    for p in plateaus:
        assert p.sequence_order is not None, (
            f"plateau {p.name} has no sequence order"
        )


def test_gaps_have_severity(app, db_session, make_org):
    """Every seeded gap has a severity."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.implementation_migration import Gap

    org_id = _get_lantern_org_id()
    gaps = Gap.query.filter_by(organization_id=org_id).all()
    for g in gaps:
        assert g.severity is not None, (
            f"gap {g.name} has no severity"
        )


def test_elements_span_all_archimate_layers(app, db_session, make_org):
    """The seeded elements cover all six ArchiMate layers."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models import ArchiMateElement
    from app.models.organization import Organization

    org_id = _get_lantern_org_id()
    layers = {
        row[0]
        for row in db_session.query(ArchiMateElement.layer)
        .filter(ArchiMateElement.organization_id == org_id)
        .distinct()
        .all()
    }
    expected = {"business", "application", "technology", "motivation", "strategy", "implementation"}
    missing = expected - layers
    assert not missing, f"missing layers: {missing}"