"""Application cost screens read the cost fact store, not the cost columns."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def as_org(tenant_ctx):
    import contextlib

    @contextlib.contextmanager
    def _as(org_id):
        try:
            with tenant_ctx(org_id):
                yield
        finally:
            g.pop("current_org_id", None)

    return _as


def _application(db_session, org, **columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name="Ledger %s" % uuid.uuid4().hex[:6], organization_id=org.id, **columns)
    db_session.add(row)
    db_session.flush()
    return row


def test_cost_reads_come_from_the_fact_not_the_column(db_session, make_org, as_org):
    from app.services.application_cost_accessor import (
        get_annual_cost, get_cost_by_column, set_annual_cost, set_cost_column)

    org = make_org("pages-read")
    with as_org(org.id):
        application = _application(db_session, org)
        set_annual_cost(application, 5000)
        set_cost_column(application, "license_cost_annual", 1200)
        db_session.flush()
        # The column drifts without its fact: the screen keeps the fact's answer.
        application.total_cost_of_ownership = 999999
        db_session.flush()
        assert get_annual_cost(application) == Decimal("5000")
        assert get_cost_by_column(application, "license_cost_annual") == Decimal("1200")
        assert get_cost_by_column(application, "support_cost") is None


def test_column_without_a_fact_reads_as_not_recorded(db_session, make_org, as_org):
    from app.services.application_cost_accessor import get_annual_cost

    org = make_org("pages-nofact")
    with as_org(org.id):
        application = _application(db_session, org, total_cost_of_ownership=7000)
        assert get_annual_cost(application) is None


def test_mixed_currencies_without_a_rate_read_as_missing(db_session, make_org, as_org):
    from app.services.application_cost_accessor import get_annual_cost, set_annual_cost

    org = make_org("pages-rate")
    with as_org(org.id):
        application = _application(db_session, org)
        # A currency with no exchange rate to the reporting currency is never
        # added in as if it were the same money.
        set_annual_cost(application, 5000, currency="XQZ")
        db_session.flush()
        assert get_annual_cost(application) is None


def test_fact_sheet_cost_comes_from_the_fact(db_session, make_org, as_org):
    from app.services.application_cost_accessor import set_annual_cost, set_cost_column
    from app.services.application_fact_sheet import build_fact_sheet

    org = make_org("pages-sheet")
    with as_org(org.id):
        application = _application(db_session, org)
        set_annual_cost(application, 5000)
        set_cost_column(application, "license_cost_annual", 1200)
        db_session.flush()
        application.total_cost_of_ownership = 1
        application.license_cost_annual = 1
        sheet = build_fact_sheet(application)
        assert sheet["annual_cost_formatted"] == "5,000"
        assert sheet["license_cost_formatted"] == "1,200"


def test_form_cost_writes_update_column_and_fact(db_session, make_org, as_org):
    from app.services.application_cost_accessor import get_cost_by_column, set_cost_column

    org = make_org("pages-write")
    with as_org(org.id):
        application = _application(db_session, org)
        set_cost_column(application, "development_cost_annual", Decimal("300"))
        db_session.flush()
        assert float(application.development_cost_annual) == 300.0
        assert get_cost_by_column(application, "development_cost_annual") == Decimal("300")
        set_cost_column(application, "development_cost_annual", None)
        db_session.flush()
        assert application.development_cost_annual is None
        assert get_cost_by_column(application, "development_cost_annual") is None


def test_backfill_is_idempotent_reports_per_organisation_and_stays_in_its_organisation(
        db_session, make_org, as_org):
    from app.commands.backfill_cost_facts import backfill_cost_facts
    from app.services.cost_fact_store import list_facts

    org_a, org_b = make_org("pages-bf-a"), make_org("pages-bf-b")
    with as_org(org_a.id):
        app_a = _application(db_session, org_a, total_cost_of_ownership=100, license_cost_annual=40)
    with as_org(org_b.id):
        app_b = _application(db_session, org_b, total_cost_of_ownership=900)
    db_session.flush()

    first = backfill_cost_facts(organization_ids=[org_a.id, org_b.id])
    assert first[str(org_a.id)]["results"]["application_components"]["created"] == 2
    assert first[str(org_b.id)]["results"]["application_components"]["created"] == 1
    second = backfill_cost_facts(organization_ids=[org_a.id, org_b.id])
    for org in (org_a, org_b):
        counts = second[str(org.id)]["results"]["application_components"]
        assert counts["created"] == 0 and counts["updated"] == 0 and counts["unchanged"] > 0

    facts_a = list_facts(org_a.id, element_type="application")
    facts_b = list_facts(org_b.id, element_type="application")
    assert {f.element_id for f in facts_a} == {app_a.id}
    assert {f.element_id for f in facts_b} == {app_b.id}
    assert all(f.organization_id == org_a.id for f in facts_a)
    assert sum(Decimal(f.amount) for f in facts_b) == Decimal("900")
