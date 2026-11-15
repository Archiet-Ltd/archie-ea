"""The vendor register: one answer to "which vendors do we have, and what do they supply".

Two real organisations in every test; each must see only the vendors its own
systems and contracts name.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.vendor.vendor_organization import VendorOrganization

pytestmark = pytest.mark.usefixtures("db_session")


def _app(db_session, org, name, vendor=None, cost=None):
    application = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id,
                                       vendor_name=vendor, total_cost_of_ownership=cost)
    db_session.add(application)
    db_session.flush()
    return application


def _contract(db_session, org, vendor, ends, annual=1000.0, application=None):
    contract = VendorContract(
        contract_name=f"Contract {uuid.uuid4().hex[:6]}", organization_id=org.id,
        vendor_id=vendor.id, start_date=date.today() - timedelta(days=300), end_date=ends,
        annual_cost=annual, status="active", application_id=application.id if application else None)
    db_session.add(contract)
    db_session.flush()
    return contract


def test_naming_a_vendor_on_a_system_links_it(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    org = make_org("link")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        first = _app(db_session, org, "CRM", vendor=f"Okta{tag}")
        second = _app(db_session, org, "SSO", vendor=f"okta{tag.upper()} ")
        assert first.vendor_organization_id is not None
        assert first.vendor_organization_id == second.vendor_organization_id
        assert VendorOrganization.query.filter(
            VendorOrganization.name.ilike(f"okta{tag}")).count() == 1
        first.vendor_name = None
        db_session.flush()
        assert first.vendor_organization_id is None
        assert [s.id for s in vendor_register.systems_without_vendor(org.id)] == [first.id]


def test_register_counts_equal_what_systems_and_contracts_name(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    org = make_org("agree")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        a = _app(db_session, org, "A", vendor=f"Salesforce{tag}", cost=1200)
        b = _app(db_session, org, "B", vendor=f"Salesforce{tag}", cost=300)
        _app(db_session, org, "C", vendor=f"Oracle{tag}")
        only_contract = VendorOrganization(name=f"Datadog{tag}", named_by_organization_id=org.id)
        db_session.add(only_contract)
        db_session.flush()
        _contract(db_session, org, only_contract, date.today() + timedelta(days=30))
        _app(db_session, org, "NoVendor")

        rows = {r["name"]: r for r in vendor_register.register(org.id)}
        assert set(rows) == {f"Salesforce{tag}", f"Oracle{tag}", f"Datadog{tag}"}
        assert rows[f"Salesforce{tag}"]["system_count"] == 2
        assert rows[f"Salesforce{tag}"]["annual_cost"] == 1500
        assert rows[f"Oracle{tag}"]["annual_cost"] is None
        assert {s["id"] for s in rows[f"Salesforce{tag}"]["systems"]} == {a.id, b.id}
        # the first row is the one renewing within 90 days
        assert vendor_register.register(org.id)[0]["name"] == f"Datadog{tag}"
        assert len(vendor_register.systems_without_vendor(org.id)) == 1


def test_register_is_private_to_each_organisation(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    org_a, org_b = make_org("a"), make_org("b")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org_a.id):
        _app(db_session, org_a, "A1", vendor=f"Okta{tag}", cost=100)
        _app(db_session, org_a, "A2", vendor=f"OnlyA{tag}")
    with tenant_ctx(org_b.id):
        _app(db_session, org_b, "B1", vendor=f"okta{tag}", cost=7)
        _app(db_session, org_b, "B2", vendor=f"OnlyB{tag}")
    with tenant_ctx(org_a.id):
        rows = {r["name"]: r for r in vendor_register.register(org_a.id)}
        assert set(rows) == {f"Okta{tag}", f"OnlyA{tag}"}
        assert rows[f"Okta{tag}"]["annual_cost"] == 100
        assert rows[f"Okta{tag}"]["system_count"] == 1
    with tenant_ctx(org_b.id):
        rows = {r["name"]: r for r in vendor_register.register(org_b.id)}
        assert set(rows) == {f"Okta{tag}", f"OnlyB{tag}"}
        assert rows[f"Okta{tag}"]["annual_cost"] == 7


def test_backfill_links_systems_that_only_carry_a_name(db_session, make_org, tenant_ctx):
    from app.services import vendor_register
    from sqlalchemy import update

    org = make_org("backfill")
    tag = uuid.uuid4().hex[:6]
    with tenant_ctx(org.id):
        application = _app(db_session, org, "Legacy")
        db_session.execute(update(ApplicationComponent).where(ApplicationComponent.id == application.id)
                           .values(vendor_name=f"Workday{tag}", vendor_organization_id=None))
        db_session.expire_all()
        assert vendor_register.register(org.id) == []
        dry = vendor_register.backfill_links(org.id, dry_run=True)
        assert dry["systems_linked"] == 1 and vendor_register.register(org.id) == []
        done = vendor_register.backfill_links(org.id)
        assert done["systems_linked"] == 1
        assert [r["name"] for r in vendor_register.register(org.id)] == [f"Workday{tag}"]
        assert vendor_register.backfill_links(org.id)["systems_linked"] == 0
