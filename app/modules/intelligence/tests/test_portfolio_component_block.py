"""Tests for the ``component`` block ``IntelligenceQueryService.
portfolio_component_for_element`` (L3) now carries beside the plain
``application_component_id``: name, owner-recorded health, entered cost/TCO
figures, the latest fiscal-period cost row and licence position -- each part
with its own reason when nothing is recorded, redacted at the route for a
caller without budget authority.

Fixtures (app, db_session, make_org, client, login_as) are discovered via
app/modules/conftest.py's import of tests.conftest,
same pattern as test_query_service.py. No import needed here.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import event


def _element(db_session, org_id, name, layer="application", type_="ApplicationComponent"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _component(db_session, org_id, element, name="A App", **overrides):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(
        name=name, organization_id=org_id, archimate_element_id=element.id, **overrides
    )
    db_session.add(component)
    db_session.flush()
    return component


def _contract(db_session, org_id, name="Vendor Contract"):
    import datetime as _dt

    from app.models.application_portfolio import VendorContract

    contract = VendorContract(
        organization_id=org_id, contract_name=name, start_date=_dt.date.today()
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _cost_row(db_session, component, *, fiscal_year, fiscal_quarter=None, total_cost=1.0, **overrides):
    from app.models.enterprise_intelligence import ApplicationCost

    row = ApplicationCost(
        application_id=component.id,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        total_cost=total_cost,
        **overrides,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _licence(
    db_session,
    component,
    org_id,
    contract,
    *,
    product_name="Some Product",
    license_metric="per user",
    quantity_entitled=0,
    quantity_deployed=0,
    quantity_used=0,
    unit_cost=None,
    compliance_status="compliant",
    last_usage_sync=None,
):
    from app.models.license_entitlement import LicenseEntitlement

    row = LicenseEntitlement(
        contract_id=contract.id,
        application_id=component.id,
        organization_id=org_id,
        product_name=product_name,
        license_type="named_user",
        license_metric=license_metric,
        quantity_entitled=quantity_entitled,
        quantity_deployed=quantity_deployed,
        quantity_used=quantity_used,
        unit_cost=unit_cost,
        compliance_status=compliance_status,
        last_usage_sync=last_usage_sync,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_user(db_session, org, *, enterprise_role=None):
    from app.models.user import Role, User

    admin_role = Role.query.filter_by(name="Administrator").first()  # tenant-scoping-ok: Role is global reference data shared by every organisation
    if admin_role is None:
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").first()  # tenant-scoping-ok: Role is global reference data shared by every organisation

    user = User(
        email=f"portfolio-block-{uuid.uuid4().hex[:8]}@example.com",
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


def _resolve(app, org_id, element_id):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        g.current_org_id = org_id
        return IntelligenceQueryService.portfolio_component_for_element(element_id)


class _PortfolioBlockStatementCounter:
    """Records every SELECT reaching ``application_costs`` or
    ``license_entitlements`` -- the two tables the component block adds."""

    def __init__(self):
        self.application_costs = []
        self.license_entitlements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if "FROM application_costs" in statement:
            self.application_costs.append(statement)
        if "FROM license_entitlements" in statement:
            self.license_entitlements.append(statement)


# --- (1)-(4) two-organisation -----------------------------------------------


def test_two_organisation_component_block_carries_all_recorded_facts(app, db_session, make_org):
    org_a = make_org("portfolio-block-tenant-a")
    a = _element(db_session, org_a.id, "A")
    comp_a = _component(db_session, org_a.id, a, health_status="at_risk", total_cost_of_ownership=90000.0)
    _cost_row(db_session, comp_a, fiscal_year=2026, fiscal_quarter=1, total_cost=91000.0, total_budget=95000.0, variance=4000.0)
    contract = _contract(db_session, org_a.id)
    _licence(db_session, comp_a, org_a.id, contract, product_name="Suite One", quantity_entitled=100, quantity_used=60, unit_cost=12.0)
    _licence(db_session, comp_a, org_a.id, contract, product_name="Suite Two", quantity_entitled=10, quantity_used=10, unit_cost=5.0)
    db_session.commit()

    result = _resolve(app, org_a.id, a.id)

    component = result["component"]
    assert component["name"] == "A App"
    assert component["health"]["status"] == "at_risk"
    assert component["health"]["reason"] is None
    assert component["cost"]["total_cost_of_ownership"] == 90000.0
    assert component["cost"]["reason"] is None
    assert component["cost_by_period"]["fiscal_year"] == 2026
    assert component["cost_by_period"]["total_cost"] == 91000.0
    assert component["cost_by_period"]["variance"] == 4000.0
    names = {entry["product_name"] for entry in component["licences"]}
    assert names == {"Suite One", "Suite Two"}


def test_cross_tenant_licence_row_never_appears(app, db_session, make_org):
    org_a = make_org("portfolio-block-licence-tenant-a")
    org_b = make_org("portfolio-block-licence-tenant-b")
    a = _element(db_session, org_a.id, "A")
    comp_a = _component(db_session, org_a.id, a)
    contract_b = _contract(db_session, org_b.id)
    # The FK is not tenant-checked: nothing stops a row naming comp_a's id
    # from another organisation's contract/organization_id.
    foreign = _licence(db_session, comp_a, org_b.id, contract_b, product_name="Foreign", quantity_entitled=10, quantity_used=1)
    db_session.commit()

    result = _resolve(app, org_a.id, a.id)
    ids = {row["entitlement_id"] for row in (result["component"]["licences"] or [])}
    assert foreign.id not in ids


def test_cross_tenant_licence_row_appears_only_when_predicate_seam_is_neutered(
    app, db_session, make_org, monkeypatch
):
    """Mutation-proof for ``_licence_tenant_predicate``. Called this way --
    with no Flask ``g.current_org_id`` ever set -- the ORM's own automatic
    tenant filter (``with_loader_criteria`` on ``TenantMixin``, which reads
    ``g.current_org_id`` directly) never fires for either read below, so the
    explicit predicate isolated in ``_licence_tenant_predicate`` is the only
    thing standing between org A's read and org B's row; the read still
    needs an organisation to resolve the element/component by, supplied by
    monkeypatching ``current_org_id`` itself rather than a request context.
    """
    from sqlalchemy import true as sa_true

    from app.modules.intelligence.services import query_service

    org_a = make_org("portfolio-block-licence-mut-a")
    org_b = make_org("portfolio-block-licence-mut-b")
    a = _element(db_session, org_a.id, "A")
    comp_a = _component(db_session, org_a.id, a)
    contract_b = _contract(db_session, org_b.id)
    foreign = _licence(db_session, comp_a, org_b.id, contract_b, product_name="Foreign", quantity_entitled=10, quantity_used=1)
    db_session.commit()

    monkeypatch.setattr(query_service, "current_org_id", lambda: org_a.id)
    real_predicate = query_service._licence_tenant_predicate

    def _licence_ids():
        result = query_service.IntelligenceQueryService.portfolio_component_for_element(a.id)
        return {row["entitlement_id"] for row in (result["component"]["licences"] or [])}

    # Control: the real seam keeps the foreign row out.
    assert foreign.id not in _licence_ids()

    # Mutation: neuter the seam -- the foreign row now passes the .where()
    # clause that used to exclude it.
    monkeypatch.setattr(query_service, "_licence_tenant_predicate", lambda org_id: sa_true())
    with pytest.raises(AssertionError):
        assert foreign.id not in _licence_ids()

    # Restore: the leak closes again.
    monkeypatch.setattr(query_service, "_licence_tenant_predicate", real_predicate)
    assert foreign.id not in _licence_ids()


def test_cost_by_period_reads_only_the_resolved_components_own_row(app, db_session, make_org):
    org_a = make_org("portfolio-block-cost-tenant-a")
    org_b = make_org("portfolio-block-cost-tenant-b")
    a = _element(db_session, org_a.id, "A")
    comp_a = _component(db_session, org_a.id, a)
    b = _element(db_session, org_b.id, "B")
    comp_b = _component(db_session, org_b.id, b)
    # application_costs carries no tenant column of its own -- reading by
    # application_id alone is honest, and it is never comp_b's row, because
    # the dual lookup above resolves the element to comp_a only, a
    # different application_id than comp_b's.
    _cost_row(db_session, comp_a, fiscal_year=2026, total_cost=10000.0)
    _cost_row(db_session, comp_b, fiscal_year=2099, total_cost=999999.0)
    db_session.commit()

    result = _resolve(app, org_a.id, a.id)
    assert result["component"]["cost_by_period"]["fiscal_year"] == 2026
    assert result["component"]["cost_by_period"]["total_cost"] == 10000.0


def test_element_from_another_org_is_not_found_component_is_none(app, db_session, make_org):
    org_a = make_org("portfolio-block-404-a")
    org_b = make_org("portfolio-block-404-b")
    a = _element(db_session, org_a.id, "A")
    _component(db_session, org_a.id, a)
    db_session.commit()

    result = _resolve(app, org_b.id, a.id)
    assert result["application_component_id"] is None
    assert result["reasons"] == ["element_not_found"]
    assert result["component"] is None


# --- (5)-(8) not recorded -----------------------------------------------


def test_health_not_assessed_returns_honest_reason(app, db_session, make_org):
    org = make_org("portfolio-block-health-none")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a, health_status=None)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    assert result["component"]["health"]["status"] is None
    assert result["component"]["health"]["reason"] == "no_health_recorded"


def test_cost_all_absent_returns_honest_reason(app, db_session, make_org):
    org = make_org("portfolio-block-cost-none")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    cost = result["component"]["cost"]
    for key in (
        "total_cost_of_ownership",
        "license_cost_annual",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "development_cost_annual",
    ):
        assert cost[key] is None
    assert cost["reason"] == "no_cost_recorded"


def test_cost_partial_entry_carries_only_what_is_recorded(app, db_session, make_org):
    org = make_org("portfolio-block-cost-partial")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a, maintenance_cost=100.0)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    cost = result["component"]["cost"]
    assert cost["maintenance_cost"] == 100.0
    assert cost["reason"] is None
    for key in (
        "total_cost_of_ownership",
        "license_cost_annual",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "development_cost_annual",
    ):
        assert cost[key] is None


def test_cost_by_period_absent_when_no_application_cost_row(app, db_session, make_org):
    org = make_org("portfolio-block-period-none")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    period = result["component"]["cost_by_period"]
    assert period["reason"] == "no_cost_recorded"
    for key in ("fiscal_year", "fiscal_quarter", "total_cost", "total_budget", "variance"):
        assert period[key] is None


def test_cost_by_period_variance_suppressed_when_budget_not_recorded(app, db_session, make_org):
    org = make_org("portfolio-block-period-variance")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a)
    # variance holds a real stored figure, but total_budget was never
    # entered -- the register's rule withholds variance, it does not
    # recompute it from the missing input.
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=1000.0, total_budget=None, variance=250.0)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    period = result["component"]["cost_by_period"]
    assert period["total_cost"] == 1000.0
    assert period["total_budget"] is None
    assert period["variance"] is None
    assert period["reason"] is None


def test_cost_by_period_picks_the_latest_fiscal_year(app, db_session, make_org):
    org = make_org("portfolio-block-period-latest")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a)
    _cost_row(db_session, comp, fiscal_year=2025, total_cost=100.0, total_budget=100.0, variance=1.0)
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=200.0, total_budget=200.0, variance=2.0)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    period = result["component"]["cost_by_period"]
    assert period["fiscal_year"] == 2026
    assert period["total_cost"] == 200.0


def test_licences_absent_returns_honest_reason(app, db_session, make_org):
    org = make_org("portfolio-block-licences-none")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    assert result["component"]["licences"] is None
    assert result["component"]["licences_reason"] == "no_licence_recorded"


def test_licence_under_used_comparison(app, db_session, make_org):
    import datetime as _dt

    org = make_org("portfolio-block-licences-comparison")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a)
    contract = _contract(db_session, org.id)
    synced = _dt.datetime.utcnow()
    _licence(db_session, comp, org.id, contract, product_name="Over-entitled", quantity_entitled=100, quantity_used=60, last_usage_sync=synced)
    _licence(db_session, comp, org.id, contract, product_name="Fully used", quantity_entitled=50, quantity_used=50, last_usage_sync=synced)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    licences = {entry["product_name"]: entry for entry in result["component"]["licences"]}
    assert licences["Over-entitled"]["under_used"] is True
    assert licences["Over-entitled"]["under_used_reason"] is None
    assert licences["Fully used"]["under_used"] is False
    assert 40 not in licences["Over-entitled"].values()


def test_licence_under_used_is_none_when_usage_never_synced(app, db_session, make_org):
    org = make_org("portfolio-block-licences-never-synced")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a)
    contract = _contract(db_session, org.id)
    _licence(db_session, comp, org.id, contract, product_name="Never synced", quantity_entitled=10, quantity_used=0)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    licence = result["component"]["licences"][0]
    assert licence["quantity_used"] == 0
    assert licence["under_used"] is None
    assert licence["under_used_reason"] == "licence_usage_not_synced"


# --- (9) shape and batching --------------------------------------------


def test_component_block_key_shapes(app, db_session, make_org):
    org = make_org("portfolio-block-shape")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a, health_status="healthy", total_cost_of_ownership=1.0)
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=1.0, total_budget=1.0, variance=0.0)
    contract = _contract(db_session, org.id)
    _licence(db_session, comp, org.id, contract, quantity_entitled=1, quantity_used=1)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    component = result["component"]
    assert set(component.keys()) == {"name", "health", "cost", "cost_by_period", "licences", "licences_reason"}
    assert len(component["cost"]) == 11
    assert len(component["cost_by_period"]) == 7
    assert len(component["licences"][0]) == 11


def test_early_branches_all_return_component_none(app, db_session, make_org):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-block-early-branches")
    non_app = _element(db_session, org.id, "Non-App", layer="business", type_="BusinessActor")
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = None
        no_tenant = IntelligenceQueryService.portfolio_component_for_element(non_app.id)
        assert no_tenant["component"] is None

        g.current_org_id = org.id
        not_found = IntelligenceQueryService.portfolio_component_for_element(999999999)
        assert not_found["component"] is None

        no_component = IntelligenceQueryService.portfolio_component_for_element(non_app.id)
        assert no_component["component"] is None


def test_two_selects_for_no_licences_and_for_twenty(app, db_session, make_org):
    from app.extensions import db as _db

    org = make_org("portfolio-block-select-count")

    a_none = _element(db_session, org.id, "A-none")
    _component(db_session, org.id, a_none, name="No Licences")

    a_many = _element(db_session, org.id, "A-many")
    comp_many = _component(db_session, org.id, a_many, name="Twenty Licences")
    contract = _contract(db_session, org.id)
    for i in range(20):
        _licence(db_session, comp_many, org.id, contract, product_name=f"Product {i}", quantity_entitled=10, quantity_used=1)
    db_session.commit()

    for element_id, expected_licence_rows in ((a_none.id, 0), (a_many.id, 20)):
        counter = _PortfolioBlockStatementCounter()
        event.listen(_db.engine, "before_cursor_execute", counter)
        try:
            result = _resolve(app, org.id, element_id)
        finally:
            event.remove(_db.engine, "before_cursor_execute", counter)

        assert len(counter.application_costs) == 1
        assert len(counter.license_entitlements) == 1
        licences = result["component"]["licences"] or []
        assert len(licences) == expected_licence_rows


def test_accountability_answer_is_unchanged_by_the_component_block(app, db_session, make_org):
    from flask import g

    from app.models.enterprise_intelligence import ApplicationOwnership, OrganizationUnit
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("portfolio-block-l4-unchanged")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a, health_status="critical", total_cost_of_ownership=1.0)
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=1.0)
    unit = OrganizationUnit(name="Finance", unit_type="Department", organization_id=org.id)
    db_session.add(unit)
    db_session.flush()
    db_session.add(
ApplicationOwnership(
            application_id=comp.id, organization_unit_id=unit.id, ownership_type="Business Owner",
            organization_id=org.id,
        )
    )
    db_session.commit()

    with app.test_request_context("/"):
        g.current_org_id = org.id
        result = IntelligenceQueryService.accountability_for_element(a.id)

    # L4's own answer keeps its pre-existing shape exactly -- no `component`
    # key leaks in, and its own reuse pin is unaffected by this task.
    # accountability_for_element's ownership read stays withdrawn (see its
    # own docstring): it never queries ApplicationOwnership/OrganizationUnit,
    # so a real owner row -- built above alongside this task's cost/health
    # block on the same element -- still yields no owner and both withdrawal
    # reason codes, exactly as the existing withdrawal regression guard
    # (test_accountability_for_element.py::
    # test_seeded_ownership_is_never_returned_the_regression_guard_that_matters)
    # already pins for the same fixture shape. That is what "unchanged by the
    # component block" means here: this task's additions do not touch L4 at
    # all, on either side of the answer.
    assert set(result.keys()) == {"owners", "capacity_not_available", "reasons"}
    assert result["owners"] == []
    assert result["reasons"] == ["ownership_reader_not_built", "capacity_not_available"]


# --- (12) fabrication -----------------------------------------------------


def test_fabrication_every_reason_bearing_field_is_none_not_falsy(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("portfolio-block-fabrication")
    a = _element(db_session, org.id, "A")
    _component(db_session, org.id, a)
    db_session.commit()

    result = _resolve(app, org.id, a.id)
    component = result["component"]

    assert component["health"]["reason"] in REASON_CODES
    assert component["health"]["status"] is None

    assert component["cost"]["reason"] in REASON_CODES
    for key in (
        "total_cost_of_ownership",
        "license_cost_annual",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "development_cost_annual",
    ):
        assert component["cost"][key] is None

    assert component["cost_by_period"]["reason"] in REASON_CODES
    for key in ("fiscal_year", "fiscal_quarter", "total_cost", "total_budget", "variance"):
        assert component["cost_by_period"][key] is None

    assert component["licences_reason"] in REASON_CODES
    assert component["licences"] is None

    payload = json.dumps(result)
    assert '"total_cost_of_ownership": 0' not in payload
    assert '"saving"' not in payload
    assert '"shelfware_cost"' not in payload
    assert '"unused"' not in payload
    assert '"total":' not in payload
    assert '"status": "healthy"' not in payload


# --- (13) route and redaction ----------------------------------------------


def test_portfolio_route_carries_component_block(app, db_session, make_org, client, login_as):
    org = make_org("portfolio-block-route-basic")
    user = _make_user(db_session, org, enterprise_role="cto")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a, health_status="healthy", total_cost_of_ownership=5000.0)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/portfolio/{a.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["component"]["name"] == comp.name
    assert data["component"]["health"]["status"] == "healthy"


def test_portfolio_route_redacts_financial_figures_for_a_role_without_budget_authority(
    app, db_session, make_org, client, login_as
):
    import datetime as _dt

    org = make_org("portfolio-block-route-redact")
    user = _make_user(db_session, org, enterprise_role="solution_architect")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a, health_status="at_risk", total_cost_of_ownership=5000.0, maintenance_cost=1000.0)
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=6000.0, total_budget=6500.0, variance=500.0)
    contract = _contract(db_session, org.id)
    _licence(db_session, comp, org.id, contract, quantity_entitled=10, quantity_used=5, unit_cost=12.5, last_usage_sync=_dt.datetime.utcnow())
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/portfolio/{a.id}")
    component = resp.get_json()["data"]["component"]

    for key in (
        "total_cost_of_ownership",
        "license_cost_annual",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
        "implementation_cost",
        "development_cost_annual",
    ):
        assert component["cost"][key] is None
    assert component["cost"]["access_reason"] == "financial_data_restricted"

    for key in ("total_cost", "total_budget", "variance"):
        assert component["cost_by_period"][key] is None
    assert component["cost_by_period"]["access_reason"] == "financial_data_restricted"

    licence = component["licences"][0]
    assert licence["unit_cost"] is None
    assert licence["access_reason"] == "financial_data_restricted"

    # Non-financial facts stay intact for a role without budget authority.
    assert component["health"]["status"] == "at_risk"
    assert licence["quantity_entitled"] == 10
    assert licence["quantity_used"] == 5
    assert licence["under_used"] is True
    assert licence["compliance_status"] == "compliant"


def test_portfolio_route_does_not_redact_for_cto(app, db_session, make_org, client, login_as):
    org = make_org("portfolio-block-route-no-redact")
    user = _make_user(db_session, org, enterprise_role="cto")
    a = _element(db_session, org.id, "A")
    comp = _component(db_session, org.id, a, total_cost_of_ownership=5000.0)
    _cost_row(db_session, comp, fiscal_year=2026, total_cost=6000.0, total_budget=6500.0, variance=500.0)
    contract = _contract(db_session, org.id)
    _licence(db_session, comp, org.id, contract, quantity_entitled=10, quantity_used=5, unit_cost=12.5)
    db_session.commit()

    login_as(client, user)
    resp = client.get(f"/api/v1/intelligence/portfolio/{a.id}")
    component = resp.get_json()["data"]["component"]

    assert component["cost"]["total_cost_of_ownership"] == 5000.0
    assert component["cost"]["access_reason"] is None
    assert component["cost_by_period"]["total_cost"] == 6000.0
    assert component["licences"][0]["unit_cost"] == 12.5
