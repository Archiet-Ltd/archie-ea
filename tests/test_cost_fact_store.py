"""One cost fact store: the application cost accessor writes it, rates convert it.

Every test uses two real organisations. Tests that need no browser run against
the shared transactional ``db_session`` fixture, so nothing they write survives.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")

REPO_ROOT = Path(__file__).resolve().parents[1]


def _app_component(db_session, org, name="Ledger", **cost_columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org.id, **cost_columns)
    db_session.add(row)
    db_session.flush()
    return row


def _facts(org_id, **filters):
    from app.services.cost_fact_store import list_facts

    return list_facts(org_id, **filters)


class _Stub:
    """A signed-in user: ``platform`` makes it a platform administrator; every
    stub holds the administer permission, so a False ``platform`` is an
    organisation administrator."""

    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, platform):
        self.is_platform_admin = platform

    def can(self, _permission):
        return True

    def get_id(self):
        return "0"


# ---------------------------------------------------------------- the accessor


def test_setting_annual_cost_writes_one_fact_and_keeps_the_column(db_session, make_org):
    from app.services.application_cost_accessor import get_annual_cost, set_annual_cost

    org = make_org("a")
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("1250.50"))
    db_session.flush()

    assert get_annual_cost(app_row) == Decimal("1250.5")
    facts = _facts(org.id, element_type="application")
    assert len(facts) == 1
    fact = facts[0]
    assert (fact.element_id, fact.category, fact.kind) == (app_row.id, "total", "actual")
    assert fact.amount == Decimal("1250.50")
    assert fact.currency == "GBP"  # the platform default, no organisation currency set
    assert fact.period == "annual" and fact.period_start.month == 1 and fact.period_end.month == 12
    assert fact.source == "application_cost_accessor"
    assert (fact.source_table, fact.source_id) == ("application_components", f"{app_row.id}:total_cost_of_ownership")


def test_identical_second_write_adds_no_duplicate(db_session, make_org):
    from app.services.application_cost_accessor import set_annual_cost

    org = make_org("a")
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("900"))
    db_session.flush()
    first = _facts(org.id)[0]
    set_annual_cost(app_row, Decimal("900"))
    db_session.flush()
    facts = _facts(org.id)
    assert len(facts) == 1 and facts[0].id == first.id


def test_changing_the_cost_updates_the_one_fact(db_session, make_org):
    from app.services.application_cost_accessor import set_annual_cost

    org = make_org("a")
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("900"))
    db_session.flush()
    set_annual_cost(app_row, Decimal("1100"))
    db_session.flush()
    facts = _facts(org.id)
    assert len(facts) == 1 and facts[0].amount == Decimal("1100")


def test_clearing_the_cost_leaves_no_stale_fact(db_session, make_org):
    from app.services.application_cost_accessor import get_annual_cost, set_annual_cost

    org = make_org("a")
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("900"))
    db_session.flush()
    set_annual_cost(app_row, None)
    db_session.flush()
    assert get_annual_cost(app_row) is None
    assert _facts(org.id) == []


def test_a_rejected_negative_value_leaves_no_fact(db_session, make_org):
    from app.services.application_cost_accessor import set_annual_cost

    org = make_org("a")
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("-5"))
    db_session.flush()
    assert _facts(org.id) == []


def test_a_new_application_gets_its_fact_when_it_is_saved(db_session, make_org):
    """Imports set the cost before the row has an id; the fact follows the flush."""
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import apply_cost_to_application

    org = make_org("a")
    new_app = ApplicationComponent(name="Imported", organization_id=org.id)
    apply_cost_to_application(
        new_app, {"total_cost_of_ownership": Decimal("500"), "support_cost": Decimal("40")})
    db_session.add(new_app)
    db_session.flush()

    by_category = {f.category: f for f in _facts(org.id)}
    assert set(by_category) == {"total", "support_cost"}
    assert by_category["total"].amount == Decimal("500")
    assert by_category["support_cost"].element_id == new_app.id


def test_other_cost_categories_written_by_the_accessor_are_facts(db_session, make_org):
    from app.services.application_cost_accessor import apply_cost_to_application

    org = make_org("a")
    app_row = _app_component(db_session, org)
    apply_cost_to_application(app_row, {"maintenance_cost": Decimal("70"),
                                        "infrastructure_cost_monthly": Decimal("10")})
    db_session.flush()
    facts = {f.category: f for f in _facts(org.id)}
    assert facts["maintenance_cost"].amount == Decimal("70")
    assert facts["infrastructure_cost_monthly"].period == "monthly"


def test_the_fact_is_in_the_organisation_reporting_currency(db_session, make_org):
    from app.services.application_cost_accessor import get_reporting_currency, set_annual_cost

    org = make_org("a")
    other = make_org("b")
    org.reporting_currency = "usd"
    db_session.flush()
    assert get_reporting_currency(org.id) == "USD"
    assert get_reporting_currency(other.id) == "GBP"
    app_row = _app_component(db_session, org)
    set_annual_cost(app_row, Decimal("10"))
    db_session.flush()
    assert _facts(org.id)[0].currency == "USD"


# --------------------------------------------------------------- two organisations


def test_one_organisations_facts_never_include_the_others(db_session, make_org):
    from app.services.application_cost_accessor import set_annual_cost

    org_a, org_b = make_org("a"), make_org("b")
    app_a = _app_component(db_session, org_a, "A app")
    app_b = _app_component(db_session, org_b, "B app")
    set_annual_cost(app_a, Decimal("100"))
    set_annual_cost(app_b, Decimal("200"))
    db_session.flush()

    assert [f.element_id for f in _facts(org_a.id)] == [app_a.id]
    assert [f.element_id for f in _facts(org_b.id)] == [app_b.id]
    # asking for B's element under A's organisation finds nothing
    assert _facts(org_a.id, element_ids=[app_b.id]) == []


def test_an_organisation_cannot_read_or_write_the_others_facts(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import set_annual_cost
    from app.services.cost_fact_store import list_facts, upsert_fact

    org_a, org_b = make_org("a"), make_org("b")
    app_b = _app_component(db_session, org_b, "B app")
    set_annual_cost(app_b, Decimal("200"))
    db_session.flush()

    with tenant_ctx(org_a.id):
        # the application itself is invisible to A, so the accessor has nothing to write through
        assert db_session.get(ApplicationComponent, app_b.id) is None or g.current_org_id == org_a.id
        with pytest.raises(PermissionError):
            list_facts(org_b.id)
        with pytest.raises(PermissionError):
            upsert_fact(org_b.id, "application", app_b.id, 1, "GBP", "application_cost_accessor")
        assert list_facts(org_a.id) == []

    db_session.expire_all()
    assert [f.amount for f in _facts(org_b.id)] == [Decimal("200.0000")]


def test_clearing_through_the_accessor_in_a_request_touches_only_that_organisation(
        db_session, make_org, tenant_ctx):
    from app.services.application_cost_accessor import set_annual_cost

    org_a, org_b = make_org("a"), make_org("b")
    app_a = _app_component(db_session, org_a, "same name")
    app_b = _app_component(db_session, org_b, "same name")
    set_annual_cost(app_a, Decimal("1"))
    set_annual_cost(app_b, Decimal("2"))
    db_session.flush()
    with tenant_ctx(org_a.id):
        set_annual_cost(app_a, None)
        db_session.flush()
    assert _facts(org_a.id) == []
    assert len(_facts(org_b.id)) == 1


# ------------------------------------------------------------------ the rate table


def test_an_organisation_administrator_cannot_write_the_rate_table(db_session, app):
    from app.models.cost_fact import ExchangeRate
    from app.services.currency_service import record_exchange_rate

    org_admin = _Stub(platform=False)
    with pytest.raises(PermissionError):
        record_exchange_rate(org_admin, "USD", "GBP", "0.8", date(2026, 1, 1))

    # The model refuses it too, so a route that skips the service still cannot write.
    with app.test_request_context("/"):
        g._login_user = org_admin
        db_session.add(ExchangeRate(from_currency="USD", to_currency="GBP",
                                    rate=Decimal("0.8"), effective_date=date(2026, 1, 1)))
        with pytest.raises(PermissionError):
            db_session.flush()
    db_session.rollback()
    assert db_session.execute(db_session.query(ExchangeRate).statement).first() is None


def test_a_platform_administrator_writes_a_rate_and_a_seeder_needs_no_user(db_session, app):
    from app.models.cost_fact import ExchangeRate
    from app.services.currency_service import get_exchange_rate, record_exchange_rate

    with app.test_request_context("/"):
        g._login_user = _Stub(platform=True)
        record_exchange_rate(_Stub(platform=True), "USD", "GBP", "0.80", date(2026, 1, 1))
    assert get_exchange_rate("USD", "GBP", date(2026, 6, 1)) == Decimal("0.8")

    # no request, no user (a seeder or CLI command)
    db_session.add(ExchangeRate(from_currency="EUR", to_currency="GBP",
                                rate=Decimal("0.85"), effective_date=date(2026, 1, 1)))
    db_session.flush()
    assert get_exchange_rate("EUR", "GBP", date(2026, 6, 1)) == Decimal("0.85")


# ----------------------------------------------------------------------- exchange


def _two_currency_facts(db_session, org):
    from app.services.cost_fact_store import upsert_fact

    start, end = date(2026, 1, 1), date(2026, 12, 31)
    app_row = _app_component(db_session, org)
    upsert_fact(org.id, "application", app_row.id, "1000", "USD", "test", category="license",
                period_start=start, period_end=end)
    upsert_fact(org.id, "application", app_row.id, "500", "GBP", "test", category="support",
                period_start=start, period_end=end)
    db_session.flush()


def test_a_total_across_usd_and_gbp_converts_at_the_period_rate(db_session, make_org):
    from app.models.cost_fact import ExchangeRate
    from app.services.cost_fact_store import total_in_reporting_currency

    org = make_org("a")
    _two_currency_facts(db_session, org)
    db_session.add_all([
        ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.70"),
                     effective_date=date(2025, 1, 1)),
        ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.80"),
                     effective_date=date(2026, 3, 1)),
        # dated after the period, so it must not apply
        ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.99"),
                     effective_date=date(2027, 1, 1)),
    ])
    db_session.flush()
    total = total_in_reporting_currency(org.id, "GBP")
    assert total.amount == Decimal("1300")  # 1000 * 0.80 + 500
    assert total.currency == "GBP" and not total.is_missing


def test_the_same_total_with_no_rate_is_missing_not_a_sum(db_session, make_org):
    from app.services.cost_fact_store import total_in_reporting_currency

    org = make_org("a")
    _two_currency_facts(db_session, org)
    total = total_in_reporting_currency(org.id, "GBP")
    assert total.amount is None and total.is_missing
    assert total.missing == [("USD", date(2026, 12, 31))]


def test_a_rate_dated_after_the_period_does_not_count(db_session, make_org):
    from app.models.cost_fact import ExchangeRate
    from app.services.cost_fact_store import total_in_reporting_currency

    org = make_org("a")
    _two_currency_facts(db_session, org)
    db_session.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.9"),
                                effective_date=date(2027, 1, 1)))
    db_session.flush()
    assert total_in_reporting_currency(org.id, "GBP").is_missing


def test_changing_the_reporting_currency_changes_the_result(db_session, make_org):
    from app.models.cost_fact import ExchangeRate
    from app.services.cost_fact_store import total_in_reporting_currency

    org = make_org("a")
    _two_currency_facts(db_session, org)
    db_session.add(ExchangeRate(from_currency="USD", to_currency="GBP", rate=Decimal("0.80"),
                                effective_date=date(2026, 1, 1)))
    db_session.flush()
    in_gbp = total_in_reporting_currency(org.id, "GBP")
    assert in_gbp.amount == Decimal("1300")
    # reporting in USD needs GBP->USD: the recorded USD->GBP rate is inverted
    in_usd = total_in_reporting_currency(org.id, "USD")
    assert in_usd.amount == Decimal("1000") + Decimal("500") / Decimal("0.80")
    assert in_usd.amount != in_gbp.amount
    # and the organisation's own setting is what an omitted currency reads
    org.reporting_currency = "USD"
    db_session.flush()
    assert total_in_reporting_currency(org.id).currency == "USD"


def test_one_organisations_total_never_counts_the_others_facts(db_session, make_org):
    from app.services.cost_fact_store import total_in_reporting_currency, upsert_fact

    org_a, org_b = make_org("a"), make_org("b")
    app_a = _app_component(db_session, org_a)
    app_b = _app_component(db_session, org_b)
    upsert_fact(org_a.id, "application", app_a.id, "10", "GBP", "test")
    upsert_fact(org_b.id, "application", app_b.id, "99", "GBP", "test")
    db_session.flush()
    assert total_in_reporting_currency(org_a.id, "GBP").amount == Decimal("10")


# ----------------------------------------------------------------------- backfill


def _seed_sources(db_session, org, tag):
    """Cost columns, an application_costs row and a capability allocation for one organisation."""
    from app.models.business_capabilities import BusinessCapability
    from app.models.cost_intelligence import CapabilityCostAllocation
    from app.models.enterprise_intelligence import ApplicationCost

    with_cost = _app_component(db_session, org, f"{tag} costed", total_cost_of_ownership=1000.0,
                               license_cost=200.0, maintenance_cost=50.0)
    no_cost = _app_component(db_session, org, f"{tag} uncosted")
    db_session.add(ApplicationCost(application_id=with_cost.id, fiscal_year=2025,
                                   total_cost=Decimal("900"), total_budget=Decimal("950")))
    capability = BusinessCapability(name=f"{tag} cap", organization_id=org.id, level=1)
    db_session.add(capability)
    db_session.flush()
    db_session.add(CapabilityCostAllocation(
        organization_id=org.id, capability_id=capability.id, fiscal_year=2025,
        period_start_date=date(2025, 1, 1), period_end_date=date(2025, 12, 31),
        software_licensing_cost=Decimal("300"), personnel_cost=Decimal("100"),
        currency="USD", cost_type="budget"))
    db_session.flush()
    return with_cost, no_cost, capability


def test_backfill_reports_counts_per_source_and_organisation(db_session, make_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts

    org_a, org_b = make_org("a"), make_org("b")
    app_a, _, cap_a = _seed_sources(db_session, org_a, "A")
    _seed_sources(db_session, org_b, "B")
    # B has an extra costed column so the two organisations' counts differ
    _app_component(db_session, org_b, "B second", total_cost_of_ownership=5.0)

    report = backfill_cost_facts(organization_ids=[org_a.id, org_b.id])
    a, b = report[str(org_a.id)], report[str(org_b.id)]
    assert a["before"] == {"application_components": 3, "application_costs": 1,
                           "capability_cost_allocations": 1}
    assert b["before"]["application_components"] == 4
    assert a["results"]["application_components"]["created"] == 3
    assert b["results"]["application_components"]["created"] == 4
    assert a["results"]["application_costs"]["created"] == 1
    assert a["results"]["capability_cost_allocations"]["created"] == 1

    facts_a = _facts(org_a.id)
    assert len(facts_a) == 3 + 2 + 1  # three columns, actual + budget, one allocation
    assert {f.organization_id for f in facts_a} == {org_a.id}
    assert app_a.id in {f.element_id for f in facts_a if f.element_type == "application"}
    cap_fact = next(f for f in facts_a if f.element_type == "capability")
    assert (cap_fact.element_id, cap_fact.amount, cap_fact.currency, cap_fact.kind) == (
        cap_a.id, Decimal("400"), "USD", "budget")
    assert (cap_fact.source, cap_fact.source_table) == ("backfill", "capability_cost_allocations")
    assert len(_facts(org_b.id)) == 4 + 2 + 1


def test_backfill_run_twice_changes_nothing_the_second_time(db_session, make_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts

    org = make_org("a")
    _seed_sources(db_session, org, "A")
    backfill_cost_facts(organization_ids=[org.id])
    snapshot = [(f.id, f.amount, f.source, f.source_table, f.source_id) for f in _facts(org.id)]
    again = backfill_cost_facts(organization_ids=[org.id])[str(org.id)]["results"]
    for counts in again.values():
        assert counts["created"] == 0 and counts["updated"] == 0 and counts["errors"] == 0
    assert again["application_components"]["unchanged"] == 3
    assert [(f.id, f.amount, f.source, f.source_table, f.source_id) for f in _facts(org.id)] == snapshot


def test_backfill_records_each_facts_source_and_never_touches_a_source_value(db_session, make_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts
    from app.models.enterprise_intelligence import ApplicationCost

    org = make_org("a")
    costed, _, _ = _seed_sources(db_session, org, "A")
    backfill_cost_facts(organization_ids=[org.id])
    db_session.expire_all()
    for fact in _facts(org.id):
        assert fact.source and fact.source_table and fact.source_id
    assert costed.total_cost_of_ownership == 1000.0 and costed.license_cost == 200.0
    assert db_session.query(ApplicationCost).count() >= 1  # nothing deleted
    from_columns = [f for f in _facts(org.id) if f.source_table == "application_components"]
    assert {f.source_id for f in from_columns} == {
        f"{costed.id}:total_cost_of_ownership", f"{costed.id}:license_cost",
        f"{costed.id}:maintenance_cost"}


def test_backfill_shares_facts_with_the_accessor_instead_of_duplicating(db_session, make_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts
    from app.services.application_cost_accessor import set_annual_cost

    org = make_org("a")
    costed, _, _ = _seed_sources(db_session, org, "A")
    backfill_cost_facts(organization_ids=[org.id])
    set_annual_cost(costed, Decimal("1500"))
    db_session.flush()
    totals = [f for f in _facts(org.id, category="total", element_type="application")
              if f.source_table == "application_components"]
    assert len(totals) == 1 and totals[0].amount == Decimal("1500")


def test_backfill_is_non_fatal_when_one_record_fails(db_session, make_org, monkeypatch):
    from app.commands import backfill_cost_facts as module
    from app.services import application_cost_accessor as accessor

    org = make_org("a")
    _seed_sources(db_session, org, "A")
    real = accessor.sync_cost_fact

    def flaky(app_obj, column, currency=None):
        if column == "license_cost":
            raise RuntimeError("one bad record")
        return real(app_obj, column, currency)

    monkeypatch.setattr(accessor, "sync_cost_fact", flaky)
    results = module.backfill_cost_facts(organization_ids=[org.id])[str(org.id)]["results"]
    assert results["application_components"]["errors"] == 1
    assert results["application_components"]["created"] == 2
    assert results["application_costs"]["created"] == 1


def test_a_dry_run_counts_and_writes_nothing(db_session, make_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts

    org = make_org("a")
    _seed_sources(db_session, org, "A")
    report = backfill_cost_facts(dry_run=True, organization_ids=[org.id])[str(org.id)]
    assert report["before"]["application_components"] == 3
    assert _facts(org.id) == []


# ------------------------------------------------------------------ store agreement


def test_the_fact_store_is_a_surface_of_applications_with_a_recorded_annual_cost():
    from scripts import check_store_agreement as gate

    names = [s.name for s in gate.CONCEPTS["applications with a recorded annual cost"]]
    assert "orm:CostFact(applications)" in names


def test_the_store_agreement_check_reports_agreement_on_a_seeded_organisation(
        app, db_session, make_org, tenant_ctx):
    from app import db
    from app.commands.backfill_cost_facts import backfill_cost_facts
    from scripts import check_store_agreement as gate

    org = make_org("a")
    other = make_org("b")
    _seed_sources(db_session, org, "A")
    _app_component(db_session, other, "B app", total_cost_of_ownership=7.0)
    concepts = {"applications with a recorded annual cost":
                gate.CONCEPTS["applications with a recorded annual cost"]}

    def disagreements(org_id):
        with tenant_ctx(org_id):
            observations, _ = gate.observe_tenant(app, db, org_id, concepts=concepts, http=False)
            findings, _ = gate.compare(observations)
        return observations, findings

    # before the backfill the store holds nothing the column holds: the check sees it
    _, before = disagreements(org.id)
    assert before

    backfill_cost_facts(organization_ids=[org.id, other.id])
    observations, after = disagreements(org.id)
    rows = observations["applications with a recorded annual cost"]
    assert {r[0] for r in rows} == {"orm:ApplicationComponent(annual cost recorded)",
                                    "orm:CostFact(applications)"}
    assert after == []


# ---------------------------------------------------------------------- migration


def _revision_graph():
    versions = REPO_ROOT / "migrations" / "versions"
    graph = {}
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = re.search(r'^revision\s*=\s*"([^"]+)"', text, re.M)
        down = re.search(r'^down_revision\s*=\s*(.+)$', text, re.M)
        if rev:
            graph[rev.group(1)] = set(re.findall(r'"([^"]+)"', down.group(1))) if down else set()
    return graph


def test_the_migration_chain_has_one_head_and_a_short_revision_id():
    graph = _revision_graph()
    parents = set().union(*graph.values())
    heads = [r for r in graph if r not in parents]
    assert heads == ["20261010_cost_fact_store"]
    assert graph["20261010_cost_fact_store"] == {"20261008_cr_organization_id"}
    assert all(len(r) <= 32 for r in graph)


def test_the_migration_is_idempotent_and_adds_what_it_says(db_session):
    """Run upgrade() against the live schema twice; the second run is a no-op."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect

    from app import db

    spec = importlib.util.spec_from_file_location(
        "cost_fact_store_revision", REPO_ROOT / "migrations/versions/20261010_cost_fact_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    connection = db_session.connection()
    # start from the old shape: no new tables, no reporting currency column
    connection.exec_driver_sql("DROP TABLE IF EXISTS exchange_rates")
    connection.exec_driver_sql("DROP TABLE IF EXISTS cost_facts")
    connection.exec_driver_sql("ALTER TABLE organizations DROP COLUMN IF EXISTS reporting_currency")
    inspector = inspect(connection)
    assert not inspector.has_table("cost_facts")

    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
        module.upgrade()
    inspector = inspect(connection)
    assert {"reporting_currency"} <= {c["name"] for c in inspector.get_columns("organizations")}
    wanted = {c.name for c in db.metadata.tables["cost_facts"].columns}
    assert wanted == {c["name"] for c in inspector.get_columns("cost_facts")}
    wanted = {c.name for c in db.metadata.tables["exchange_rates"].columns}
    assert wanted == {c["name"] for c in inspector.get_columns("exchange_rates")}
