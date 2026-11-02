"""A vendor's cost stays behind the spend permission on every surface, and one row agrees with itself.

A role without the spend permission reads no cost and no basis on the Vendors list, a vendor's page or
the register API. The Data quality banner counts the organisation the person is working in. Within one
vendor row, "Annual cost" and "Contract cost a year" add the same contracts. Two real organisations
throughout: the other one's contracts are 777,000.
"""
from __future__ import annotations

import re
import sys
import uuid
from datetime import date, timedelta

import pytest

from tests.test_vendor_figures_agree import _estate
from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user
from tests.test_vendor_pages import _user

pytestmark = pytest.mark.usefixtures("db_session")


def test_a_role_without_spend_reads_no_cost_on_the_vendor_page(client, login_as, db_session, make_org):
    mine, theirs = make_org("vp-a"), make_org("vp-b")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    _estate(db_session, theirs, "t" + tag, contract_cost=777000.0)
    reader = _user(db_session, mine, "compliance")
    login_as(client, reader)
    text = _answer(client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True))
    assert f"Vendor m{tag}" in text and "2 of your systems" in text
    assert "Not shown for your role" in text
    for figure in ("48,000", "36,000", "12,000", "777", "from active contracts"):
        assert figure not in text, figure


def test_the_administrator_still_reads_the_cost_on_the_vendor_page(app, db_session, make_org):
    mine = make_org("vp-c")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True))
    assert "48,000" in text and "Not shown for your role" not in text


def test_the_register_api_hides_the_basis_with_the_cost(client, login_as, db_session, make_org):
    mine = make_org("vp-d")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    login_as(client, _user(db_session, mine, "compliance"))
    rows = client.get("/api/vendors/register?per_page=100").get_json()["vendors"]
    row = next(r for r in rows if r["id"] == vendor.id)
    assert row["annual_cost"] is None and row["annual_cost_basis"] is None and row["cost_visible"] is False


def test_the_banner_counts_the_organisation_the_person_switched_to(app, db_session, make_org):
    from app.models.org_role import OrgRole

    home, worked_in = make_org("vp-e"), make_org("vp-f")
    tag = uuid.uuid4().hex[:6]
    _estate(db_session, worked_in, "w" + tag)
    client = _admin_client(app, db_session, home)
    from app.models.user import User
    user = User.query.filter_by(organization_id=home.id).order_by(User.id.desc()).first()
    OrgRole.set_role(worked_in.id, user.id, "architect")
    db_session.flush()
    with client.session_transaction() as sess:
        sess["current_org_id"] = worked_in.id
    _forget_cached_user()
    text = _answer(client.get("/applications/").get_data(as_text=True))
    assert re.search(r"1 of 3 have no vendor", text), text[:600]


def test_both_cost_columns_of_a_row_add_the_same_contracts(db_session, make_org, tenant_ctx, monkeypatch):
    """The rule: a vendor's active contracts that sit on one of its live systems or on no system. A
    contract on a decommissioned system or on another vendor's system counts in neither column."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services import vendor_register

    monkeypatch.setattr(sys.modules["app.decorators.requires_role"], "may_see_spend_pages", lambda user: True)
    mine = make_org("vp-g")
    tag = uuid.uuid4().hex[:6]
    vendor, apps = _estate(db_session, mine, "m" + tag)       # 36,000 + 12,000 on its two live systems
    rival = VendorOrganization(name=f"Rival {tag}")
    db_session.add(rival)
    db_session.flush()
    rival_system = ApplicationComponent(name=f"Rival sys {tag}", organization_id=mine.id,
                                        lifecycle_status="operational", vendor_organization_id=rival.id)
    db_session.add(rival_system)
    db_session.flush()
    for application_id, cost in ((None, 5000.0), (rival_system.id, 1000.0), (apps[3].id, 2000.0)):
        db_session.add(VendorContract(contract_name=f"Extra {tag} {cost}", organization_id=mine.id,
                                      vendor_id=vendor.id, application_id=application_id, annual_cost=cost,
                                      status="active", start_date=date.today() - timedelta(days=30)))
    db_session.flush()
    with tenant_ctx(mine.id):
        row = next(r for r in vendor_register.list_context()["register_rows"] if r["id"] == vendor.id)
    assert row["annual_cost"] == 53000.0
    assert row["contract_annual_cost"] == row["annual_cost"]
    assert row["contract_count"] == 4


def _own_decommissioned_system(db_session, org, vendor, tag, cost):
    from app.models.application_portfolio import ApplicationComponent, VendorContract

    system = ApplicationComponent(name=f"Retired {tag}", organization_id=org.id,
                                  lifecycle_status="5. Decommissioned", vendor_organization_id=vendor.id)
    db_session.add(system)
    db_session.flush()
    db_session.add(VendorContract(contract_name=f"Retired deal {tag}", organization_id=org.id,
                                  vendor_id=vendor.id, application_id=system.id, annual_cost=cost,
                                  status="active", start_date=date.today() - timedelta(days=30)))
    db_session.flush()
    return system


def test_a_contract_on_the_vendors_own_decommissioned_system_counts_nowhere(
        db_session, make_org, tenant_ctx, monkeypatch):
    from app.services import vendor_register

    monkeypatch.setattr(sys.modules["app.decorators.requires_role"], "may_see_spend_pages", lambda user: True)
    mine, theirs = make_org("vp-h"), make_org("vp-i")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    _estate(db_session, theirs, "t" + tag, contract_cost=777000.0)
    _own_decommissioned_system(db_session, mine, vendor, "m" + tag, 9000.0)
    with tenant_ctx(mine.id):
        row = next(r for r in vendor_register.list_context()["register_rows"] if r["id"] == vendor.id)
        coverage = vendor_register.system_coverage()
    assert row["system_count"] == 2
    assert row["annual_cost"] == 48000.0
    assert row["contract_annual_cost"] == 48000.0
    assert row["contract_count"] == 3
    assert coverage["decommissioned"] == 2   # the vendor's retired system and the unvendored one


def test_spends_largest_vendor_is_the_vendors_contract_cost_a_year(db_session, make_org, tenant_ctx, monkeypatch):
    """A contract naming vendor A that sits on vendor B's system counts for B on both screens."""
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.modules.procurement.services import get_spend_summary
    from app.services import vendor_register

    monkeypatch.setattr(sys.modules["app.decorators.requires_role"], "may_see_spend_pages", lambda user: True)
    mine, theirs = make_org("vp-j"), make_org("vp-k")
    tag = uuid.uuid4().hex[:6]
    vendor, apps = _estate(db_session, mine, "m" + tag)       # 48,000 on the vendor's two systems
    _estate(db_session, theirs, "t" + tag, contract_cost=777000.0)
    other = VendorOrganization(name=f"Other {tag}")
    db_session.add(other)
    db_session.flush()
    # names the other vendor but sits on this vendor's system: counted once, under the system's vendor
    db_session.add(VendorContract(contract_name=f"Cross {tag}", organization_id=mine.id, vendor_id=other.id,
                                  application_id=apps[0].id, annual_cost=20000.0, status="active",
                                  start_date=date.today() - timedelta(days=30)))
    db_session.flush()
    with tenant_ctx(mine.id):
        rows = {r["id"]: r for r in vendor_register.list_context()["register_rows"]}
        summary = get_spend_summary(mine.id)
    name, data = summary["top_vendors"][0]
    assert name == vendor.name
    assert data["annual_cost"] == rows[vendor.id]["contract_annual_cost"] == 68000.0
    assert dict(summary["top_vendors"]).get(other.name) is None or \
        dict(summary["top_vendors"])[other.name]["annual_cost"] == rows.get(other.id, {}).get("contract_annual_cost")


def test_the_contract_figures_cost_the_same_statements_however_many_vendors(db_session, make_org, tenant_ctx,
                                                                              monkeypatch):
    from sqlalchemy import event

    from app import db
    from app.services import vendor_register

    monkeypatch.setattr(sys.modules["app.decorators.requires_role"], "may_see_spend_pages", lambda user: True)
    small, large = make_org("vp-l"), make_org("vp-m")
    tag = uuid.uuid4().hex[:6]
    _estate(db_session, small, "s" + tag)
    for i in range(5):
        _estate(db_session, large, f"l{i}{tag}")

    def statements(org):
        with tenant_ctx(org.id):
            vendor_register.list_context()      # warm the session so only the page's own statements count
        seen = []

        def count(conn, cursor, statement, *rest):
            seen.append(statement)

        engine = db.session.get_bind()
        event.listen(engine, "before_cursor_execute", count)
        try:
            with tenant_ctx(org.id):
                rows = vendor_register.list_context()["register_rows"]
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return len(seen), len(rows)

    one, one_rows = statements(small)
    many, many_rows = statements(large)
    assert one_rows == 1 and many_rows == 5
    assert many == one, (one, many)


def test_the_vendor_page_states_an_unrecorded_annual_value_in_words_and_the_organisation_currency(
        app, db_session, make_org):
    mine = make_org("vp-n")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True))
    i = text.index("Annual Contract Value")
    assert "Not recorded" in text[i:i + 200], text[i:i + 200]


def test_a_recorded_annual_value_reads_in_the_organisation_currency(app, db_session, make_org):
    from decimal import Decimal

    mine = make_org("vp-o")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    vendor.contract_value_annual = Decimal("1234.50")
    db_session.flush()
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True))
    i = text.index("Annual Contract Value")
    assert "1,234.50" in text[i:i + 200] and "USD" not in text[i:i + 200], text[i:i + 200]
