"""Vendors and renewals state one answer for what renews within 90 days."""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")

# renewal date offsets in days; each contract ends six months after it renews
OFFSETS = (14, 41, 66, 130, 215)


def _org_with_renewals(db_session, org, tag, offsets=OFFSETS):
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendors = []
    for i, days in enumerate(offsets):
        vendor = VendorOrganization(name=f"Vendor {tag} {i}")
        db_session.add(vendor)
        db_session.flush()
        system = ApplicationComponent(name=f"Sys {tag} {i}", organization_id=org.id,
                                      lifecycle_status="operational", vendor_organization_id=vendor.id)
        db_session.add(system)
        db_session.flush()
        renews = date.today() + timedelta(days=days)
        db_session.add(VendorContract(
            contract_name=f"Deal {tag} {i}", organization_id=org.id, vendor_id=vendor.id,
            application_id=system.id, annual_cost=1000.0 * (i + 1), status="active",
            start_date=date.today() - timedelta(days=90), renewal_date=renews,
            end_date=renews + timedelta(days=183)))
        vendors.append(vendor)
    db_session.flush()
    return vendors


def test_the_vendor_register_dates_a_renewal_by_its_renewal_date(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine, theirs = make_org("rf-a"), make_org("rf-b")
    tag = uuid.uuid4().hex[:6]
    vendors = _org_with_renewals(db_session, mine, "m" + tag)
    _org_with_renewals(db_session, theirs, "t" + tag, offsets=(5, 6, 7))
    with tenant_ctx(mine.id):
        rows = {r["id"]: r for r in vendor_register.register(mine.id)}
    assert rows[vendors[0].id]["next_renewal"] == (date.today() + timedelta(days=14)).isoformat()
    assert [rows[v.id]["renewal_due"] for v in vendors] == [True, True, True, False, False]


def test_the_renewals_tiles_count_what_the_vendors_page_counts(app, db_session, make_org):
    mine, theirs = make_org("rf-c"), make_org("rf-d")
    tag = uuid.uuid4().hex[:6]
    _org_with_renewals(db_session, mine, "m" + tag)
    _org_with_renewals(db_session, theirs, "t" + tag, offsets=(5, 6, 7, 8))
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    vendors_text = _answer(client.get("/applications/vendors").get_data(as_text=True))
    renewals = client.get("/procurement/renewals").get_data(as_text=True)
    start = renewals.index('data-testid="renewals-warning"')
    tile = _answer(renewals[start:renewals.index("</a>", start)])
    assert re.search(r"\b3 Renew within 90 days", tile), tile
    assert "3 renew within 90 days" in vendors_text


def _page_numbers(client):
    """What a person reads: the vendors page's due count, the 90 day tile, and the rows its link opens."""
    vendors_text = _answer(client.get("/applications/vendors").get_data(as_text=True))
    renewals = client.get("/procurement/renewals").get_data(as_text=True)
    start = renewals.index('data-testid="renewals-warning"')
    tile = _answer(renewals[start:renewals.index("</a>", start)])
    listed = client.get("/procurement/renewals?days=90").get_data(as_text=True)
    return vendors_text, tile, listed.count('data-testid="renewal-row"')


def test_a_contract_exactly_on_a_band_edge_is_counted_by_the_tile_its_list_and_the_vendors_page(
        app, db_session, make_org):
    mine, theirs = make_org("rf-e"), make_org("rf-f")
    tag = uuid.uuid4().hex[:6]
    _org_with_renewals(db_session, mine, "m" + tag, offsets=(30, 90, 91, 180))
    _org_with_renewals(db_session, theirs, "t" + tag, offsets=(3, 4))
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    vendors_text, tile, listed = _page_numbers(client)
    assert re.search(r"\b2 Renew within 90 days", tile), tile
    assert "2 renew within 90 days" in vendors_text
    assert listed == 2


def test_an_active_contract_never_shows_a_renewal_date_already_past(app, db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import VendorContract
    from app.services import vendor_register

    mine, theirs = make_org("rf-g"), make_org("rf-h")
    tag = uuid.uuid4().hex[:6]
    vendors = _org_with_renewals(db_session, mine, "m" + tag, offsets=(-20,))
    _org_with_renewals(db_session, theirs, "t" + tag, offsets=(-30,))
    contract = db_session.execute(db_session.query(VendorContract).filter_by(
        organization_id=mine.id).statement).scalars().first()
    ends = contract.end_date
    assert ends > date.today() + timedelta(days=90)
    with tenant_ctx(mine.id):
        row = {r["id"]: r for r in vendor_register.register(mine.id)}[vendors[0].id]
    assert row["next_renewal"] == ends.isoformat()
    assert row["renewal_due"] is False
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    vendors_text, tile, listed = _page_numbers(client)
    assert re.search(r"\b0 Renew within 90 days|Nothing renews in the next 90 days", tile), tile
    assert "None renew within 90 days" in vendors_text
    assert listed == 0
    page = client.get(f"/applications/vendors/{vendors[0].id}").get_data(as_text=True)
    assert (date.today() + timedelta(days=-20)).strftime("%d %b %Y") not in page
    assert ends.strftime("%d %b %Y") in page
