"""A signed-in person opens /opportunities and reads their own organisation's ranked opportunities.

Two organisations, each with linked data. A visitor who is not signed in still gets the waiting-list page.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

from tests.test_compliance_policies_org_fence import _user
from tests.test_consolidation_candidate_scoring import _capability, _support
from tests.test_consolidation_isolation import _app


def _contract(db_session, org, application, name, cost):
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Vendor {name}")
    vendor.named_by_organization_id = org.id
    db_session.add(vendor)
    db_session.flush()
    db_session.add(VendorContract(
        vendor_id=vendor.id, organization_id=org.id, application_id=application.id, contract_name=name,
        status="active", annual_cost=cost, start_date=date.today() - timedelta(days=30),
        end_date=date.today() + timedelta(days=300)))
    db_session.flush()


def _world(db_session, make_org):
    org_a, org_b = make_org("oppA"), make_org("oppB")
    tag = uuid.uuid4().hex[:6]
    w = {"org_a": org_a, "org_b": org_b, "tag": tag}
    for key, org in (("a", org_a), ("b", org_b)):
        w["user_" + key] = _user(db_session, org)
        apps = [_app(db_session, org, f"{n} {key} {tag}") for n in ("Billing", "Ledger", "Archive")]
        cap = _capability(db_session, org, f"Order Fulfilment {key} {tag}")
        _support(db_session, org, apps[0], cap)
        _support(db_session, org, apps[1], cap)
        apps[2].lifecycle_status = "3. sunset"
        _contract(db_session, org, apps[0], f"Billing licence {key} {tag}", 900000 if key == "a" else 5000)
        _contract(db_session, org, apps[1], f"Ledger licence {key} {tag}", 300000 if key == "a" else 4000)
        _contract(db_session, org, apps[2], f"Archive support {key} {tag}", 70000)
        w["apps_" + key], w["cap_" + key] = apps, cap
    db_session.commit()
    return w


def test_a_signed_in_person_reads_ranked_opportunities_with_reasons(client, login_as, db_session, make_org):
    w = _world(db_session, make_org)
    login_as(client, w["user_a"])
    response = client.get("/opportunities")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "waiting list" not in body.lower() and "Early access" not in body
    cap = w["cap_a"].name
    assert cap in body
    assert f"Billing a {w['tag']}, Ledger a {w['tag']}" in body
    assert f"Archive support a {w['tag']}" in body and "is marked sunset" in body
    # ranked by the run cost in play: the capability (1.2m across two systems) comes before the 70k contract
    assert body.index(cap) < body.index(f"Archive support a {w['tag']}")
    assert body.count('data-testid="result-footer"') >= 2
    # nothing of the other organisation
    assert w["cap_b"].name not in body and f"Archive support b {w['tag']}" not in body
    # every opportunity link lands on a page with content
    links = re.findall(r'<a href="([^"]+)" class="text-primary hover:underline" data-testid="opportunity-link">', body)
    assert len(links) >= 2
    for href in links:
        landed = client.get(href)
        assert landed.status_code == 200, href
        assert len(landed.get_data(as_text=True)) > 2000, href


def test_another_organisation_reads_only_its_own(client, login_as, db_session, make_org):
    w = _world(db_session, make_org)
    login_as(client, w["user_b"])
    body = client.get("/opportunities").get_data(as_text=True)
    assert w["cap_b"].name in body and w["cap_a"].name not in body
    assert f"Billing licence a {w['tag']}" not in body


def test_a_visitor_who_is_not_signed_in_still_gets_the_waiting_list(client):
    body = client.get("/opportunities").get_data(as_text=True)
    assert "waiting list" in body.lower() or "early access" in body.lower()


def _no_cost_user(db_session, org):
    user = _user(db_session, org, role_name="User")
    user.enterprise_role = "solution_architect"
    db_session.commit()
    return user


def test_a_role_barred_from_cost_reads_no_amount_on_opportunities(client, login_as, db_session, make_org):
    from app.decorators.requires_role import may_see_spend_pages

    w = _world(db_session, make_org)
    barred = _no_cost_user(db_session, w["org_a"])
    assert may_see_spend_pages(barred) is False
    login_as(client, barred)
    body = client.get("/opportunities").get_data(as_text=True)
    # the same opportunities are listed, with their reasons ...
    assert w["cap_a"].name in body and f"Archive support a {w['tag']}" in body
    # ... but no contract or run-cost amount reaches the page, and it says why
    assert not re.search(r"£\s?[\d,]{5,}", body), re.findall(r"£\s?[\d,]+", body)
    assert "Cost figures are withheld for your role." in body
    # a person the rule admits still reads the amounts
    login_as(client, w["user_a"])
    assert re.search(r"£\s?1,200,000", client.get("/opportunities").get_data(as_text=True))


def test_a_person_with_no_organisation_is_told_so_not_that_the_portfolio_is_empty(
        client, login_as, db_session, make_org, monkeypatch):
    w = _world(db_session, make_org)
    login_as(client, w["user_a"])
    monkeypatch.setattr("app.utils.tenant.current_organization_id", lambda: None)
    body = client.get("/opportunities").get_data(as_text=True)
    assert "No organisation is selected" in body
    assert "No opportunity has been found" not in body
