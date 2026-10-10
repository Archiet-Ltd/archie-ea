"""The renewals dashboard must actually list expiring contracts.

Pins the 14 Aug 2026 fix: the route passed neither `contracts` nor
`days_filter` to a template that renders both, so the page always showed
"No expiring contracts ... within the next  days." (note the blank) while the
summary tiles above showed real counts, and the ?days= dropdown submitted a
parameter nothing read.
"""

import uuid
from datetime import date, timedelta

import pytest


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def procurement_setup(app, db_session, make_org):
    from app.models.application_portfolio import VendorContract
    from app.models.user import User

    org = make_org("renewals")
    user = User(
        email=f"renewals-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Ren",
        last_name="Ewals",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="procurement",
    )
    db_session.add(user)
    contract = VendorContract(
        organization_id=org.id,
        contract_name="Expiring Soon MSA",
        contract_number=f"REN-{uuid.uuid4().hex[:6]}",
        status="active",
        contract_value=50000,
        start_date=date.today() - timedelta(days=355),
        end_date=date.today() + timedelta(days=10),
    )
    db_session.add(contract)
    db_session.flush()

    client = app.test_client()
    _login(client, user.id)
    return client, contract


def test_expiring_contract_is_listed(procurement_setup):
    client, contract = procurement_setup
    resp = client.get("/procurement/renewals")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert contract.contract_name in body


def test_days_filter_is_read_and_echoed(procurement_setup):
    client, contract = procurement_setup
    resp = client.get("/procurement/renewals?days=90")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert contract.contract_name in body

    # A contract 10 days out sits outside no valid window, but the copy must
    # never render the blank "next  days" again.
    assert "next  days" not in body

    # Unknown values fall back to the 30-day default instead of crashing.
    resp = client.get("/procurement/renewals?days=evil")
    assert resp.status_code == 200
