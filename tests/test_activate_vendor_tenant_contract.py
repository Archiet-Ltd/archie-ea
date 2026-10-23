"""Activating a catalogue vendor records the calling organisation's own
contract (VendorContract), never the shared vendor row. Two organisations
activating the same vendor keep independent value, dates and status, and the
vendor detail page shows only the viewing organisation's contract.
"""

import uuid
from datetime import date

import pytest

from app.extensions import db
from tests.test_ba_tenant_and_authz import _login, _make_user_id


def _contracts(vendor_id):
    """Every organisation's contract rows for a vendor (raw SQL, so no tenant filter)."""
    db.session.expire_all()
    rows = db.session.execute(
        db.text(
            "SELECT organization_id, annual_cost, start_date, end_date, status "
            "FROM vendor_contracts WHERE vendor_id = :v ORDER BY id"
        ),
        {"v": vendor_id},
    ).all()
    return rows


def _activate(client, vendor_id, value, start, end):
    return client.post(
        f"/vendors/{vendor_id}/activate",
        data={
            "contract_start_date": start,
            "contract_end_date": end,
            "contract_value_annual": value,
        },
    )


@pytest.mark.usefixtures("db_session")
def test_two_organisations_activate_same_vendor_independently(app, db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a = make_org("act-a")
    org_b = make_org("act-b")
    vendor = VendorOrganization(name=f"ZZ-VERIFY Shared Vendor {uuid.uuid4().hex[:10]}")
    db_session.add(vendor)
    db_session.commit()
    user_a = _make_user_id(db, org_a.id, "ActA")
    user_b = _make_user_id(db, org_b.id, "ActB")

    client = app.test_client()
    _login(client, user_a)
    resp = _activate(client, vendor.id, "111000", "2026-01-01", "2027-01-01")
    assert resp.status_code in (302, 303)
    _login(client, user_b)
    resp = _activate(client, vendor.id, "222000", "2026-06-01", "2028-06-01")
    assert resp.status_code in (302, 303)

    rows = {r.organization_id: r for r in _contracts(vendor.id)}
    assert set(rows) == {org_a.id, org_b.id}
    assert rows[org_a.id].annual_cost == 111000
    assert rows[org_a.id].start_date == date(2026, 1, 1)
    assert rows[org_a.id].end_date == date(2027, 1, 1)
    assert rows[org_a.id].status == "active"
    assert rows[org_b.id].annual_cost == 222000
    assert rows[org_b.id].start_date == date(2026, 6, 1)
    assert rows[org_b.id].end_date == date(2028, 6, 1)

    # The shared vendor row carries none of it.
    db_session.expire_all()
    shared = db_session.get(VendorOrganization, vendor.id)
    assert shared.contract_value_annual is None
    assert shared.contract_start_date is None
    assert shared.contract_end_date is None
    assert shared.contract_status in (None, "catalog")

    # Activating again updates the same organisation's row rather than adding one.
    _login(client, user_a)
    _activate(client, vendor.id, "333000", "2026-01-01", "2027-01-01")
    by_org = {}
    for r in _contracts(vendor.id):
        by_org.setdefault(r.organization_id, []).append(r.annual_cost)
    assert by_org[org_a.id] == [333000]
    assert by_org[org_b.id] == [222000]


@pytest.mark.usefixtures("db_session")
def test_vendor_detail_shows_only_the_viewing_organisations_contract(app, db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a = make_org("det-a")
    org_b = make_org("det-b")
    vendor = VendorOrganization(name=f"ZZ-VERIFY Detail Vendor {uuid.uuid4().hex[:10]}")
    db_session.add(vendor)
    db_session.commit()
    user_a = _make_user_id(db, org_a.id, "DetA")
    user_b = _make_user_id(db, org_b.id, "DetB")

    client = app.test_client()
    _login(client, user_a)
    _activate(client, vendor.id, "444555", "2026-02-01", "2027-02-01")
    page_a = client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True)
    assert "444555.00" in page_a
    assert "2026-02-01" in page_a
    assert "Not contracted by your organisation" not in page_a

    _login(client, user_b)
    page_b = client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True)
    assert "444555" not in page_b
    assert "2026-02-01" not in page_b
    assert "Not contracted by your organisation" in page_b
