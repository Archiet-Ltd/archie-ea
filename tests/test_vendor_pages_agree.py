"""Both vendor pages state one coverage, an Administrator reads annual cost on them, and another
organisation's systems and contracts never count."""
from __future__ import annotations

import re
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


def _admin_client(app, db_session, org):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"adm-{uuid.uuid4().hex[:8]}@example.com", first_name="Ad", last_name="Min",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    user.grant_org_admin()
    db_session.flush()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sid = mint_test_sid(user.id)
        if sid:
            sess["_sid"] = sid
    return client


def _estate(db_session, org, tag, other=False):
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Vendor {tag}")
    db_session.add(vendor)
    db_session.flush()
    apps = []
    for i, (vendor_id, status) in enumerate([(vendor.id, "operational"), (vendor.id, "operational"),
                                              (None, "operational"), (None, "5. Decommissioned")]):
        a = ApplicationComponent(name=f"Sys {tag} {i}", organization_id=org.id, lifecycle_status=status,
                                 vendor_organization_id=vendor_id)
        db_session.add(a)
        apps.append(a)
    db_session.flush()
    db_session.add(VendorContract(contract_name=f"Deal {tag}", organization_id=org.id, vendor_id=vendor.id,
                                  application_id=apps[0].id, annual_cost=48000.0, status="active",
                                  start_date=date.today() - timedelta(days=90)))
    db_session.flush()
    return vendor


def _answer(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text


def test_both_vendor_pages_state_the_same_coverage_and_the_administrator_reads_cost(app, db_session, make_org):
    mine, theirs = make_org("vend-a"), make_org("vend-b")
    tag = uuid.uuid4().hex[:6]
    _estate(db_session, mine, "mine" + tag)
    _estate(db_session, theirs, "theirs" + tag)
    client = _admin_client(app, db_session, mine)
    pages = {}
    for path in ("/applications/vendors", "/vendors/technical/scenarios"):
        _forget_cached_user()
        resp = client.get(path)
        assert resp.status_code == 200, path
        pages[path] = _answer(resp.get_data(as_text=True))
    for path, text in pages.items():
        found = re.search(r"(\d+) vendors? suppl(?:y|ies) (\d+) of your (\d+) systems?", text)
        assert found, (path, text[:600])
        # Three live systems, two of them with a vendor; the decommissioned one is left out and said so.
        assert found.groups() == ("1", "2", "3"), (path, found.groups())
        assert "None of your" not in text, path
        assert "Not shown for your role" not in text, path
        assert "Decommissioned systems are not counted (1)" in text, path
        assert "48,000" in text or "48k" in text.lower() or "48.0k" in text.lower(), path
        assert f"theirs{tag}" not in text, path
