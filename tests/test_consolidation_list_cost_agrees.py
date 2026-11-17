"""The consolidation list states the run cost the Overview and the application list state.

Two organisations. The first records a cost on two applications, the second records none.
The list's Portfolio Annual Cost is that organisation's run cost and never the other's.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from tests.test_compliance_policies_org_fence import _user
from tests.test_consolidation_isolation import _app


def _summary(client, login_as, user):
    login_as(client, user)
    body = client.get("/consolidation-list/api/entries").get_json()
    return body["summary"]


def test_portfolio_annual_cost_is_the_run_cost_the_overview_states(client, login_as, db_session, make_org):
    from app.modules.dashboard.services.overview_figures import run_cost_line
    from datetime import date, timedelta

    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("costA"), make_org("costB")
    user_a, user_b = _user(db_session, org_a), _user(db_session, org_b)
    tag = uuid.uuid4().hex[:6]
    first, second = _app(db_session, org_a, f"Ledger {tag}"), _app(db_session, org_a, f"Relay {tag}")
    _app(db_session, org_b, f"Silent {tag}")
    for application, cost in ((first, Decimal("1200000")), (second, Decimal("518400"))):
        vendor = VendorOrganization(name=f"Vendor {application.name}")
        vendor.named_by_organization_id = org_a.id
        db_session.add(vendor)
        db_session.flush()
        db_session.add(VendorContract(
            vendor_id=vendor.id, organization_id=org_a.id, application_id=application.id,
            contract_name=f"Agreement {application.name}", status="active", annual_cost=cost,
            start_date=date.today() - timedelta(days=30), end_date=date.today() + timedelta(days=300),
        ))
    db_session.commit()

    mine = _summary(client, login_as, user_a)
    assert mine["portfolio_annual_cost"] == 1718400
    login_as(client, user_a)
    assert run_cost_line(org_a.id, user_a)["total"] == mine["portfolio_annual_cost"]

    theirs = _summary(client, login_as, user_b)
    assert theirs["portfolio_annual_cost"] is None
