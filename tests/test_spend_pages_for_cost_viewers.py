"""The Spend group's pages open for everyone who may see costs, and only they change them.

A CTO (a cost viewer) reads the contracts, renewals, licences and spend pages and the detail
page of a contract; the actions that change a contract stay with the procurement roles. A role
with no cost visibility is refused, and a contract of another organisation is not found.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _forget_cached_user():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _signed_in(app, db_session, org, role):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"spend-{role}-{uuid.uuid4().hex[:8]}@example.com", first_name="Spend", last_name="Viewer",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    return client


def _contract(db_session, org, name):
    from app.models.application_portfolio import VendorContract

    contract = VendorContract(organization_id=org.id, contract_name=name, contract_number="C-" + uuid.uuid4().hex[:5],
                            start_date=date.today() - timedelta(days=30), end_date=date.today() + timedelta(days=300))
    db_session.add(contract)
    db_session.flush()
    return contract


PAGES = ["/procurement/contracts", "/procurement/renewals", "/procurement/licenses", "/procurement/spend"]


@pytest.mark.parametrize("path", PAGES)
def test_a_cto_reads_the_cost_pages(app, db_session, make_org, path):
    org = make_org("spend-cto")
    client = _signed_in(app, db_session, org, "cto")
    _forget_cached_user()
    resp = client.get(path)
    assert resp.status_code == 200, resp.status_code


@pytest.mark.parametrize("path", PAGES)
def test_a_role_with_no_cost_visibility_is_refused(app, db_session, make_org, path):
    org = make_org("spend-ea")
    client = _signed_in(app, db_session, org, "enterprise_architect")
    _forget_cached_user()
    assert client.get(path).status_code == 403


def test_the_contract_actions_stay_with_the_procurement_roles(app, db_session, make_org):
    org = make_org("spend-actions")
    contract = _contract(db_session, org, "Acme support contract")
    cto = _signed_in(app, db_session, org, "cto")
    procurement = _signed_in(app, db_session, org, "procurement")

    _forget_cached_user()
    cto_list = cto.get("/procurement/contracts").get_data(as_text=True)
    assert "Acme support contract" in cto_list
    assert "New contract" not in cto_list
    assert 'href="/procurement/contracts/new"' not in cto_list
    _forget_cached_user()
    cto_detail = cto.get(f"/procurement/contracts/{contract.id}")
    assert cto_detail.status_code == 200
    assert "Edit contract" not in cto_detail.get_data(as_text=True)
    _forget_cached_user()
    assert cto.get("/procurement/contracts/new").status_code == 403

    _forget_cached_user()
    proc_list = procurement.get("/procurement/contracts").get_data(as_text=True)
    assert "New contract" in proc_list
    assert 'href="/procurement/contracts/new"' in proc_list
    _forget_cached_user()
    assert "Edit contract" in procurement.get(f"/procurement/contracts/{contract.id}").get_data(as_text=True)


def test_another_organisations_contract_is_not_found(app, db_session, make_org):
    mine, theirs = make_org("spend-mine"), make_org("spend-theirs")
    foreign = _contract(db_session, theirs, "Their private contract")
    cto = _signed_in(app, db_session, mine, "cto")
    _forget_cached_user()
    assert cto.get(f"/procurement/contracts/{foreign.id}").status_code == 404
    _forget_cached_user()
    assert "Their private contract" not in cto.get("/procurement/contracts").get_data(as_text=True)
