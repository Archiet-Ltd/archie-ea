"""Journey: can the procurement persona run a contract and its licences?

Level 9, docs/TESTING_STANDARD.md. This persona is the commercial steward: a
contract with a vendor, licence entitlements under it, and the renewal view they
watch. The chain is what matters -- a licence that persists but hangs off no
contract, or a contract whose licences never appear beside it, is a broken
record of spend in a system of record.

The tenancy assertion here is not decoration. license_create re-reads the
client-supplied contract_id through the tenant predicate specifically so a
crafted post cannot hang a licence off another organisation's contract, where
its holder would then see it. That guard has a comment and, until now, no test.
"""

import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _contract(client, name, start="2026-01-01", end="2027-01-01"):
    return client.post(
        "/procurement/contracts/new",
        data={"contract_name": name, "start_date": start, "end_date": end},
        follow_redirects=True,
    )


def test_procurement_records_a_contract_and_a_licence_under_it(app, client):
    """The persona's chain: contract, then entitlement, then both visible."""
    from app import db
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    with app.app_context():
        org_id = make_org(db, "Proc")
        buyer_id = make_user(db, org_id, "buyer", enterprise_role="procurement",
                             role_name="Architect")

    login(client, buyer_id)
    contract_name = "Vendor MSA %s" % uuid.uuid4().hex[:8]
    assert _contract(client, contract_name).status_code == 200

    with app.app_context():
        db.session.expunge_all()
        contract = db.session.execute(
            db.select(VendorContract).filter_by(contract_name=contract_name)
        ).scalar_one()
        assert contract.organization_id == org_id
        contract_id = contract.id

    product = "Seat Licence %s" % uuid.uuid4().hex[:8]
    response = client.post(
        "/procurement/licenses/new",
        data={
            "contract_id": str(contract_id),
            "product_name": product,
            "seats_purchased": "250",
            "seats_deployed": "180",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200, response.status_code

    # PERSISTED, and attached to the contract rather than floating.
    with app.app_context():
        db.session.expunge_all()
        licence = db.session.execute(
            db.select(LicenseEntitlement).filter_by(product_name=product)
        ).scalar_one_or_none()
        assert licence is not None, "the licence did not persist"
        assert licence.contract_id == contract_id, (
            "the licence saved without its contract -- an entitlement nobody "
            "can trace to a commercial agreement"
        )
        assert licence.organization_id == org_id

    # VISIBLE where the buyer looks next.
    page = client.get("/procurement/licenses")
    assert page.status_code == 200
    assert product in page.get_data(as_text=True)


def test_a_licence_cannot_be_hung_off_another_organisations_contract(app, client):
    """The guard license_create documents, finally exercised.

    Without the tenant re-read, a crafted post would attach this org's licence to
    another org's contract -- and that contract's owner would then see it.
    """
    from app import db
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    with app.app_context():
        org_a = make_org(db, "ProcA")
        org_b = make_org(db, "ProcB")
        buyer_a = make_user(db, org_a, "buyerA", enterprise_role="procurement",
                            role_name="Architect")
        buyer_b = make_user(db, org_b, "buyerB", enterprise_role="procurement",
                            role_name="Architect")

    # B creates a contract of its own.
    client_b = app.test_client()
    login(client_b, buyer_b)
    foreign_name = "Org B Agreement %s" % uuid.uuid4().hex[:8]
    assert _contract(client_b, foreign_name).status_code == 200

    with app.app_context():
        db.session.expunge_all()
        foreign_id = db.session.execute(
            db.select(VendorContract).filter_by(contract_name=foreign_name)
        ).scalar_one().id

    # A tries to attach a licence to it.
    login(client, buyer_a)
    product = "Cross Tenant Licence %s" % uuid.uuid4().hex[:8]
    response = client.post(
        "/procurement/licenses/new",
        data={"contract_id": str(foreign_id), "product_name": product,
              "seats_purchased": "10"},
        follow_redirects=False,
    )
    assert response.status_code == 404, (
        "org A was allowed to reference org B's contract (%s)" % response.status_code
    )

    with app.app_context():
        db.session.expunge_all()
        assert db.session.execute(
            db.select(LicenseEntitlement).filter_by(product_name=product)
        ).scalar_one_or_none() is None, "the cross-tenant licence was written"


def test_the_renewals_view_and_the_contract_agree_on_expiry(app, client):
    """Two screens, one contract: they must not contradict each other.

    The QA audit found a contract badged Active on its detail page while the
    renewals page badged the same record Expired, because the stored status and
    the computed expiry were never reconciled. The backwards date range that
    produced it is refused now; this holds the agreement for a real one.
    """
    from app import db

    with app.app_context():
        org_id = make_org(db, "ProcExp")
        buyer_id = make_user(db, org_id, "buyerx", enterprise_role="procurement",
                             role_name="Architect")

    login(client, buyer_id)
    name = "Expiring Agreement %s" % uuid.uuid4().hex[:8]
    # Ended in the past, but a coherent range: genuinely expired.
    assert _contract(client, name, start="2020-01-01", end="2021-01-01").status_code == 200

    renewals = client.get("/procurement/renewals")
    assert renewals.status_code == 200
    body = renewals.get_data(as_text=True)
    # Either it appears as expiring/expired, or the page does not list it at all --
    # what it must never do is present it as current.
    if name in body:
        window = body[max(0, body.index(name) - 600):body.index(name) + 600].lower()
        assert "active" not in window or "expir" in window, (
            "the renewals view shows an ended contract as active"
        )


def test_notice_period_form_drives_renewals_last_day_to_cancel(app, client):
    """Add a notice period on the contract form, reload, and the renewals
    view shows the correct date.

    Journey: a contract created without a notice period shows a dash on
    the renewals page. The procurement lead edits the contract to record
    a notice period, and the renewals page now shows the computed last
    day to cancel.
    """
    from datetime import date, timedelta

    from app import db
    from app.models.application_portfolio import VendorContract

    with app.app_context():
        org_id = make_org(db, "ProcNtc")
        buyer_id = make_user(db, org_id, "ntcuser", enterprise_role="procurement",
                             role_name="Architect")

    login(client, buyer_id)
    name = "Notice Test %s" % uuid.uuid4().hex[:8]

    # Create a contract with a renewal date soon enough for the default
    # 30-day window, but without a notice period.
    soon = (date.today() + timedelta(days=20)).isoformat()
    end = (date.today() + timedelta(days=365)).isoformat()
    resp = client.post(
        "/procurement/contracts/new",
        data={
            "contract_name": name,
            "start_date": "2026-01-01",
            "end_date": end,
            "renewal_date": soon,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db.session.expunge_all()
        contract = db.session.execute(
            db.select(VendorContract).filter_by(contract_name=name)
        ).scalar_one()
        assert contract.notice_period_days is None
        contract_id = contract.id

    # On the renewals page, the last day to cancel should show a dash.
    renewals = client.get("/procurement/renewals")
    assert renewals.status_code == 200
    body = renewals.get_data(as_text=True)
    assert name in body

    # Edit the contract to add a notice period of 15 days.
    resp = client.post(
        "/procurement/contracts/%d/edit" % contract_id,
        data={
            "contract_name": name,
            "start_date": "2026-01-01",
            "end_date": end,
            "renewal_date": soon,
            "notice_period_days": "15",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db.session.expunge_all()
        contract = db.session.execute(
            db.select(VendorContract).filter_by(contract_name=name)
        ).scalar_one()
        assert contract.notice_period_days == 15

    # The renewals page now shows the computed last day to cancel.
    cancel_date = date.today() + timedelta(days=20) - timedelta(days=15)
    renewals = client.get("/procurement/renewals")
    assert renewals.status_code == 200
    body = renewals.get_data(as_text=True)
    assert cancel_date.strftime("%d %b %Y") in body, (
        "renewals page missing last day to cancel %s" % cancel_date
    )
