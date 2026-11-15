"""An organisation's Administrator reads its own contracts and licences; the menu offers the link exactly then.

The Administrator opens the contracts list, a contract page, the licences list and a licence page,
and is offered the link in the sidebar and the Licences button on the contracts page. A reader
without cost rights is neither offered the link nor allowed the page, and an Administrator of
another organisation finds neither this organisation's contract nor its licence, and does find
its own.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

LINK = 'href="/procurement/contracts"'
LICENCES_LINK = 'href="/procurement/licenses"'


def _forget_cached_user():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _signed_in(app, db_session, org, role, *, administrator=False):
    from app.models.user import Permission, Role, User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"contracts-{role}-{uuid.uuid4().hex[:8]}@example.com", first_name="Contract", last_name="Reader",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    if administrator:
        admin_role = Role.query.filter(
            Role.permissions.op("&")(Permission.ADMINISTER) == Permission.ADMINISTER
        ).first()
        if admin_role is None:
            pytest.skip("no ADMINISTER role seeded in this database")
        user.role = admin_role
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


def _licence(db_session, org, contract, name):
    from app.models.license_entitlement import LicenseEntitlement

    licence = LicenseEntitlement(organization_id=org.id, contract_id=contract.id, product_name=name, license_type="named_user", quantity_entitled=25)
    db_session.add(licence)
    db_session.flush()
    return licence


def _get(client, path):
    _forget_cached_user()
    return client.get(path, follow_redirects=True)


def test_an_administrator_reads_the_contracts_and_is_offered_the_link(app, db_session, make_org):
    org = make_org("contracts-admin")
    contract = _contract(db_session, org, "Admin readable support contract")
    admin = _signed_in(app, db_session, org, "enterprise_architect", administrator=True)

    listing = _get(admin, "/procurement/contracts")
    assert listing.status_code == 200
    assert "Admin readable support contract" in listing.get_data(as_text=True)

    detail = _get(admin, f"/procurement/contracts/{contract.id}")
    assert detail.status_code == 200
    assert "Admin readable support contract" in detail.get_data(as_text=True)

    home = _get(admin, "/dashboard/")
    assert home.status_code == 200
    assert LINK in home.get_data(as_text=True)


def test_an_administrator_who_is_offered_the_licences_button_can_open_it(app, db_session, make_org):
    org = make_org("licences-admin")
    host = _contract(db_session, org, "Admin licence host contract")
    licence = _licence(db_session, org, host, "Admin readable seat pack")
    admin = _signed_in(app, db_session, org, "enterprise_architect", administrator=True)

    contracts = _get(admin, "/procurement/contracts").get_data(as_text=True)
    assert LICENCES_LINK in contracts

    listing = _get(admin, "/procurement/licenses")
    assert listing.status_code == 200
    page = listing.get_data(as_text=True)
    assert "Admin readable seat pack" in page
    assert f'href="/procurement/licenses/{licence.id}"' in page
    assert 'href="/procurement/compliance"' not in page
    assert 'href="/procurement/licenses/new"' not in page

    detail = _get(admin, f"/procurement/licenses/{licence.id}")
    assert detail.status_code == 200
    assert "Admin readable seat pack" in detail.get_data(as_text=True)

    assert LINK in _get(admin, "/dashboard/").get_data(as_text=True)


def test_a_reader_without_cost_rights_is_neither_offered_the_link_nor_allowed_the_page(app, db_session, make_org):
    org = make_org("contracts-reader")
    contract = _contract(db_session, org, "Reader refused contract")
    reader = _signed_in(app, db_session, org, "enterprise_architect")

    assert _get(reader, "/procurement/contracts").status_code == 403
    assert _get(reader, "/procurement/licenses").status_code == 403
    assert _get(reader, f"/procurement/contracts/{contract.id}").status_code == 403
    home = _get(reader, "/dashboard/")
    assert home.status_code == 200
    assert LINK not in home.get_data(as_text=True)


def test_an_administrator_of_another_organisation_does_not_find_the_contract(app, db_session, make_org):
    mine, theirs = make_org("contracts-mine"), make_org("contracts-theirs")
    foreign = _contract(db_session, mine, "First organisation private contract")
    foreign_licence = _licence(db_session, mine, foreign, "First organisation private licence")
    own = _contract(db_session, theirs, "Second organisation own contract")
    _licence(db_session, theirs, own, "Second organisation own licence")
    other_admin = _signed_in(app, db_session, theirs, "enterprise_architect", administrator=True)

    assert _get(other_admin, f"/procurement/contracts/{foreign.id}").status_code == 404
    assert _get(other_admin, f"/procurement/licenses/{foreign_licence.id}").status_code == 404
    listing = _get(other_admin, "/procurement/contracts")
    assert listing.status_code == 200
    body = listing.get_data(as_text=True)
    assert "Second organisation own contract" in body
    assert "First organisation private contract" not in body
    licences = _get(other_admin, "/procurement/licenses")
    assert licences.status_code == 200
    body = licences.get_data(as_text=True)
    assert "Second organisation own licence" in body
    assert "First organisation private licence" not in body
