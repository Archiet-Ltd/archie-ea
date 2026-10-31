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
    from app.models.business_model import BusinessModelCanvas
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit
    from app.models.cost_fact import CostFact

    return {
        "application_ownership": ApplicationOwnership.query.filter_by(organization_id=org_id).count(),
        "organization_units": OrganizationUnit.query.filter_by(organization_id=org_id).count(),
        "cost_facts": CostFact.query.filter_by(organization_id=org_id).count(),
        "archimate_capability_elements": ArchiMateElement.query.filter_by(
            organization_id=org_id, type="Capability").count(),
        "business_model_canvases": BusinessModelCanvas.query.filter_by(organization_id=org_id).count(),
        "enterprise_initiatives": EnterpriseInitiative.query.filter_by(organization_id=org_id).count(),
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
    # Every seeded work package must carry the organisation so it is visible
    # under the tenant filter used by TenantMixin models.
    from app.models import ArchiMateElement
    from app.models.unified_work_package import UnifiedWorkPackage
    wp_orgs = (
        db_session.query(UnifiedWorkPackage.organization_id)
        .filter(UnifiedWorkPackage.id.in_(
            db_session.query(UnifiedWorkPackage.id)
            .join(ArchiMateElement, UnifiedWorkPackage.archimate_element_id == ArchiMateElement.id)
            .filter(ArchiMateElement.organization_id == org_id)
        ))
        .distinct()
        .all()
    )
    assert len(wp_orgs) == 1 and wp_orgs[0][0] == org_id, (
        f"all seeded work packages must carry org_id={org_id}, got {wp_orgs}"
    )
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
        # The accountability answer is the real walk answer now: the quota
        # pool's ApplicationOwner record is present, the withdrawn empty
        # answer is gone.
        assert result["owners"], "accountability on Quay Compute Pool returned no owners"
        assert "ownership_reader_not_built" not in result.get("reasons", [])
        assert all(owner.get("name") for owner in result["owners"])

    def test_accountability_on_event_relay(self, app, db_session, make_org):
        org_id = _seed_and_get_org_id()
        relay_id = _element_id(org_id, "Event Relay")

        result = self._call(app, org_id, "accountability_for_element", relay_id)
        # The seed records two owners against Event Relay itself (a primary
        # and a technical owner). The answer names the element's own owners
        # first, so both appear and no absence reason is given.
        # The two own owners come before any owner of an affected element;
        # their order between themselves is not part of the contract.
        assert {(o["name"], o["role"]) for o in result["owners"][:2]} == {
            ("Ivo Reed", "Primary"),
            ("Cameron SysAdmin", "Technical"),
        }
        assert "no_ownership_records" not in result.get("reasons", [])

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


# ── AC3: the one deliberate reader is read-only ─────────────────────────────
# Corrected (T-seed-05): this used to log in as the demo user and expect a
# write route to refuse them. That was the defect the results sweep of
# 29 Oct 2026 found: every demo architect, including the primary demo user,
# was seeded on the read-only Viewer platform role, so Score portfolio, Run
# detection and Place on the radar all refused the person the demo exists to
# act as. The demo user is now seeded on the Architect role and this
# assertion would be wrong for them. The real thing worth keeping — a
# Viewer-role person is refused a write route — is still true of the one
# person the seed deliberately keeps on Viewer (the business-analyst reader),
# so this test now asserts it of that person instead.


def test_demo_reader_cannot_access_write_route(app, db_session, make_org, client, login_as):
    """AC3: the one deliberate read-only person (the business-analyst reader)
    gets a 403 (or redirect) on a write route; the demo architect does not."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.organization import Organization
    from app.models.user import User

    org_id = _get_lantern_org_id()
    reader = User.query.filter_by(
        email="drew.dataanalyst@lantern-quay.example.com", organization_id=org_id,
    ).first()
    assert reader is not None, "demo reader not found — was the seeder run?"
    assert reader.role is not None and reader.role.name == "Viewer"
    login_as(client, reader)

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


def test_demo_user_has_architect_role(app, db_session, make_org):
    """The demo user is the demonstration's architect persona and is seeded
    on the Architect platform role (Permission.GENERAL) -- the platform role
    an architect has in a real customer organisation, the one that may score
    the portfolio, run duplicate detection and place a technology on the
    radar. Corrected (T-seed-05): this used to assert Viewer/permissions=0,
    which was the defect (every write action refusing the demo architect)."""
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
    assert user.role.name == "Architect"
    assert user.role.permissions != 0


def test_demo_has_exactly_one_business_architect_and_a_rerun_keeps_it(app, db_session, make_org):
    """The demonstration carries one business architect, on the platform role
    that may write (T-seed-05 corrected this from Viewer -- see
    test_demo_user_has_architect_role), and a second run neither adds
    another nor changes their persona."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]

    from app.models.user import User

    org_id = _get_lantern_org_id()
    people = (
        db_session.query(User)
        .filter(User.organization_id == org_id, User.enterprise_role == "business_architect")
        .all()
    )
    assert [p.email for p in people] == ["bryn.businessarch@lantern-quay.example.com"]
    assert people[0].role.name == "Architect"
    assert not people[0].is_org_admin


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

# ── proof-asset data: the screens the public site photographs ───────────────
# Marketing review v1 (S3 / P-06): the Applications screenshot showed four zero
# tiles, "0 of 21 have a vendor" and "Not mapped" on every row, and the ARB
# screenshot showed every tile as zero or a dash. These pin the seed so a
# re-shoot can never again photograph an empty-looking demonstration company.


def _seed_once():
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        return seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]


def test_application_portfolio_has_populated_tiles_vendors_and_capabilities(app, db_session, make_org):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability

    _seed_once()
    org_id = _get_lantern_org_id()
    apps = ApplicationComponent.query.filter_by(organization_id=org_id).all()
    by_stage = {}
    for a in apps:
        by_stage[a.lifecycle_status] = by_stage.get(a.lifecycle_status, 0) + 1
    # Every tile the Applications screen shows is non-zero.
    for stage in ("2.1 strategic", "2.2 tactical", "3. sunset", "5. decommissioned"):
        assert by_stage.get(stage, 0) >= 1, f"no demo application at lifecycle {stage!r}: {by_stage}"
    assert all(a.vendor_name for a in apps), "an application has no vendor recorded"
    assert all(a.component_type for a in apps), "an application has no type"
    mapped = {
        m.application_component_id
        for m in ApplicationCapabilityMapping.query.filter_by(organization_id=org_id).all()
    }
    assert mapped == {a.id for a in apps}, "every application maps to a capability"
    # ADR 0008: the unified row is the projection of the business capability,
    # not a second hand-written copy of it.
    unified = UnifiedCapability.query.filter_by(organization_id=org_id).all()
    assert len(unified) == 24
    assert all(u.source_table == "business_capability" for u in unified)


def test_arb_queue_has_real_reviews_with_cycle_times(app, db_session, make_org):
    from app.models.architecture_review_board import ARBReviewItem

    stats = _seed_once()
    assert stats["arb_reviews_created"] == 11
    items = ARBReviewItem.query.filter(ARBReviewItem.review_number.like("LQ-ARB-%")).all()
    decided = [i for i in items if i.decision_date and i.submitted_at]
    assert len(decided) >= 4
    assert any(i.status == "rejected" for i in items)
    assert any(i.status in ("submitted", "under_review") for i in items)
    cycle = [(i.decision_date - i.submitted_at).days for i in decided]
    assert all(c > 0 for c in cycle)


def test_proof_asset_data_is_idempotent(app, db_session, make_org):
    _seed_once()
    again = _seed_once()
    assert again["arb_reviews_created"] == 0
    assert again["capability_mappings_created"] == 0
    assert again["capabilities_created"] == 0


# ── T-demo-01: the demo organisation answers the public promises ────────────


def test_portfolio_screen_has_initiatives_with_recorded_figures(app, db_session, make_org):
    """/portfolio reads EnterpriseInitiative; the demo organisation has five,
    each with phase, health, budget, spend, completion and a sponsor."""
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    stats = _seed_once()
    assert stats["initiatives_created"] == 7
    org_id = _get_lantern_org_id()
    rows = EnterpriseInitiative.query.filter_by(organization_id=org_id).all()
    assert len(rows) == 7
    for row in rows:
        assert row.current_phase and row.health_status and row.executive_sponsor
        assert row.approved_budget and row.spent_to_date is not None
        assert row.completion_percentage is not None
    assert {r.health_status for r in rows} >= {"green", "yellow"}
    again = _seed_once()
    assert again["initiatives_created"] == 0
    assert EnterpriseInitiative.query.filter_by(organization_id=org_id).count() == 7


def test_work_packages_have_owner_initiative_and_connections(app, db_session, make_org):
    """Every demo work package names an owner, belongs to an initiative and
    reaches the model through at least one connection."""
    from app.models import ArchiMateRelationship
    from app.models.unified_work_package import UnifiedWorkPackage

    stats = _seed_once()
    assert stats["work_packages_linked"] == 12
    org_id = _get_lantern_org_id()
    wps = UnifiedWorkPackage.query.filter_by(organization_id=org_id).all()
    assert len(wps) == 12
    for wp in wps:
        assert wp.owner_id is not None, wp.name
        assert wp.enterprise_initiative_id is not None, wp.name
        outgoing = ArchiMateRelationship.query.filter_by(
            organization_id=org_id, source_id=wp.archimate_element_id
        ).count()
        assert outgoing >= 1, wp.name
    assert _seed_once()["work_packages_linked"] == 0


def test_ask_programme_card_has_owner_and_connections(app, db_session, make_org):
    org_id = _seed_and_get_org_id()
    from app.models.unified_work_package import UnifiedWorkPackage

    wp = UnifiedWorkPackage.query.filter_by(
        organization_id=org_id, name="Deploy automated calibration rigs"
    ).first()
    result = TestAskQuestions._call(
        app, org_id, "programme_for_element", wp.archimate_element_id
    )
    card = result["work_packages"][0]
    assert card["owner"] == "Ivo Reed"
    assert card["affected_summary"]["explicit_count"] + card["affected_summary"]["derived_count"] > 0


def test_business_model_canvas_is_seeded_once_and_tenant_scoped(app, db_session, make_org):
    from app.config.archimate_viewpoints import CANVAS_TEMPLATES
    from app.models.business_model import BusinessModelCanvas
    from app.modules.business_model_canvas import model_service

    stats = _seed_once()
    assert stats["business_model_canvases_created"] == 1
    org_id = _get_lantern_org_id()
    canvases = BusinessModelCanvas.query.filter_by(organization_id=org_id).all()
    bmc = [c for c in canvases if c.canvas_type is None]
    assert len(bmc) == 1
    assert bmc[0].operating_model_type
    # one filled canvas of every other framework, and none of them empty in any element box
    framework = {c.canvas_type: c for c in canvases if c.canvas_type}
    assert set(framework) == {k for k, t in CANVAS_TEMPLATES.items() if t["record"] == "business_model_canvas"} - {
        "business_model_canvas"
    }
    for key, canvas in framework.items():
        for box in model_service.element_boxes(canvas):
            assert model_service.entries(canvas, box), (key, box)
    again = _seed_once()
    assert again["business_model_canvases_created"] == 0 and again["framework_canvases_created"] == 0
    other = make_org("other-tenant-canvas")
    other_id = other.id
    db_session.commit()
    assert BusinessModelCanvas.query.filter_by(organization_id=other_id).count() == 0

def test_demo_store_agreement_check_is_clean(app, db_session, make_org, tenant_ctx):
    """After seeding, the store-agreement check reports nothing for the demo
    organisation (the check picks the organisation that holds data; here it is
    asked about the demo organisation directly, as its demo user)."""
    import importlib.util

    from app import db

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "check_store_agreement_demo",
        os.path.join(root, "scripts", "check_store_agreement.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)

    _seed_once()
    org_id = _get_lantern_org_id()
    user = _demo_user(db_session, org_id)
    with tenant_ctx(org_id):
        observations, notes = gate.observe_tenant(app, db, org_id, user, http=True)
    findings, more = gate.compare(observations)
    # The one named exception: application_costs is a retired table (no new
    # writes), so the demo leaves it empty and the check's rule that it must
    # equal the live stores is waiting on its owner to waive that surface.
    # Remove this exception when the rule changes. Everything else must be
    # clean, and the live stores must still agree with each other.
    retired = "orm:ApplicationCost(applications)=0"
    excepted = [f for f in findings
                if f.lstrip().startswith("applications with a recorded annual cost")
                and retired in f]
    for f in excepted:
        assert "orm:ApplicationComponent(annual cost recorded)=20" in f, f
        assert "orm:CostFact(applications)=20" in f, f
    remaining = [f for f in findings if f not in excepted]
    assert remaining == [], (remaining, notes + more)


def test_demo_does_not_write_the_retired_cost_table(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import ApplicationCost

    _seed_once()
    org_id = _get_lantern_org_id()
    ids = [a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id)]
    assert ids
    assert ApplicationCost.query.filter(ApplicationCost.application_id.in_(ids)).count() == 0


def test_demo_old_blanket_total_is_corrected_on_rerun(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.cost_fact import CostFact

    _seed_once()
    org_id = _get_lantern_org_id()
    for a in ApplicationComponent.query.filter_by(organization_id=org_id):
        a.total_cost_of_ownership = 85000
    db_session.flush()
    again = _seed_once()
    assert again["application_figures_agreed"] == 20
    from app.commands.seed_demo_company import _app_figures

    apps = ApplicationComponent.query.filter_by(organization_id=org_id).all()
    assert {a.name: a.total_cost_of_ownership for a in apps} == {a.name: _app_figures(a.name)[2] for a in apps}
    assert len({a.total_cost_of_ownership for a in apps}) > 5, "each application has its own cost"
    totals = {
        float(f.amount)
        for f in CostFact.query.filter_by(
            organization_id=org_id, element_type="application", category="total")
    }
    assert totals == {float(_app_figures(a.name)[2]) for a in apps}


def test_demo_seed_gives_a_second_organisation_nothing(app, db_session, make_org):
    other_id = make_org("bystander").id
    before = _row_counts(other_id)
    _seed_once()
    assert _row_counts(other_id) == before
    assert not any(before.values())


def test_demo_tco_equals_sum_of_cost_components(app, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent

    _seed_once()
    org_id = _get_lantern_org_id()
    apps = ApplicationComponent.query.filter_by(organization_id=org_id).all()
    assert len(apps) == 20
    for a in apps:
        components = (
            (a.license_cost_annual or 0)
            + (a.infrastructure_cost_monthly or 0) * 12
            + (a.maintenance_cost or 0)
            + (a.support_cost or 0)
            + (a.development_cost_annual or 0)
        )
        assert a.total_cost_of_ownership == components, a.name


def test_demo_capabilities_all_have_an_owner(app, db_session, make_org):
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    _seed_once()
    org_id = _get_lantern_org_id()
    bcs = BusinessCapability.query.filter_by(organization_id=org_id).all()
    ucs = UnifiedCapability.query.filter_by(organization_id=org_id).all()
    assert len(bcs) == 24 and len(ucs) == 24
    assert [c.code for c in bcs if not (c.business_owner or "").strip()] == []
    assert [c.code for c in ucs if not (c.business_owner or "").strip()] == []


def test_demo_plateaus_all_have_a_target_date(app, db_session, make_org):
    from app.models.implementation_migration import Plateau

    _seed_once()
    org_id = _get_lantern_org_id()
    plateaus = Plateau.query.filter_by(organization_id=org_id).all()
    assert len(plateaus) == 5
    assert [p.name for p in plateaus if p.target_date is None] == []


def test_demo_gaps_all_sit_on_a_plateau_and_have_a_work_package(app, db_session, make_org):
    from app.models.implementation_migration import Gap
    from app.services import work_package_service

    _seed_once()
    org_id = _get_lantern_org_id()
    gaps = Gap.query.filter_by(organization_id=org_id).all()
    assert len(gaps) == 10
    assert [g.name for g in gaps if not g.plateaus] == []
    assert [g.name for g in gaps if g.target_plateau_id is None] == []
    addressed = work_package_service.addressed_gap_ids(org_id)
    assert [g.name for g in gaps if g.id not in addressed] == []


def test_demo_seed_twice_changes_nothing(app, db_session, make_org):
    from app.models.implementation_migration import Gap
    from app.services import work_package_service

    _seed_once()
    org_id = _get_lantern_org_id()
    before = _row_counts(org_id)
    links_before = len(work_package_service.addressed_gap_ids(org_id))
    plateau_links_before = sum(len(g.plateaus) for g in Gap.query.filter_by(organization_id=org_id))
    again = _seed_once()
    assert _row_counts(org_id) == before
    assert len(work_package_service.addressed_gap_ids(org_id)) == links_before
    assert sum(len(g.plateaus) for g in Gap.query.filter_by(organization_id=org_id)) == plateau_links_before
    assert again["gaps_created"] == 0


def test_demo_weekly_briefing_has_content_in_every_section(app, db_session, make_org, tenant_ctx):
    """The demonstration organisation fills all four briefing sections."""
    from sqlalchemy import text

    from app.commands.apply_entity_history_trigger import FUNCTION_SQL, TABLES
    from app.modules.solutions_strategic.v2.services import weekly_briefing

    # Elements the seed adds are recorded as changes only once the history
    # trigger is installed, as it is on every deployed database.
    from app import db

    with db.engine.connect() as conn:
        conn.execute(text(FUNCTION_SQL))
        for table in TABLES:
            conn.execute(text(f'DROP TRIGGER IF EXISTS entity_history_trg ON "{table}"'))
            conn.execute(text(
                f'CREATE TRIGGER entity_history_trg BEFORE INSERT OR UPDATE OR DELETE '
                f'ON "{table}" FOR EACH ROW EXECUTE FUNCTION entity_history_record_version()'
            ))
        conn.commit()

    _seed_once()
    org_id = _get_lantern_org_id()
    with tenant_ctx(org_id):
        briefing = weekly_briefing.build(org_id)

    sections = {s["key"]: s for s in briefing["sections"]}
    assert sections["changes"]["total"] >= 1
    assert sections["decisions"]["total"] >= 2
    assert sections["risks"]["total"] >= 2
    assert sections["conformance"]["total"] >= 3
    assert "3 reviews past the time limit" in briefing["headline"]
