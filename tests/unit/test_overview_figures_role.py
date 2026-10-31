"""The overview's figures service carries the cost figures only for a role that may see spend.

Two people of one organisation: a finance lead, whom the one rule (``may_see_spend_pages``) admits to the
spend pages, and a solution architect, whom it refuses. The context for the first carries the annual run
cost, the top vendor's share and every application's cost; the context for the second carries none of
them and nothing that reads as money, while every other figure agrees between the two.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

MONEY = re.compile(r"[£$€]\s?\d")


def _user(db_session, org, role):
    from app.models.user import User

    user = User(email=f"overview-{role}-{uuid.uuid4().hex[:8]}@example.com", first_name=role.title(),
                last_name="Reader", organization_id=org.id, confirmed=True, enterprise_role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _costed_org(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    org = make_org("overview-cost")
    tag = uuid.uuid4().hex[:6]
    for name, cost in (("Ledger Core", 120000.0), ("Payments Hub", 80000.0), ("Flow Tracker", None)):
        db_session.add(ApplicationComponent(name=f"{name} {tag}", organization_id=org.id, lifecycle_status="operational",
                                            component_type="Application", total_cost_of_ownership=cost))
    for vendor, annual in (("Northbeam", 200000.0), ("Quillon", 50000.0)):
        vendor_row = VendorOrganization(name=f"{vendor} {tag}")
        db_session.add(vendor_row)
        db_session.flush()
        db_session.add(VendorContract(vendor_id=vendor_row.id, organization_id=org.id, status="active", annual_cost=annual,
                                      contract_name=f"{vendor} agreement {tag}", contract_number="OV-" + uuid.uuid4().hex[:5],
                                      start_date=date.today() - timedelta(days=200)))
    db_session.flush()
    return org


def test_the_context_carries_the_cost_figures_only_for_a_role_that_may_see_spend(app, db_session, make_org, tenant_ctx):
    from app.decorators.requires_role import may_see_spend_pages
    from app.modules.dashboard.services.overview_figures import overview_context

    org = _costed_org(db_session, make_org)
    finance = _user(db_session, org, "finance")
    architect = _user(db_session, org, "solution_architect")
    assert may_see_spend_pages(finance) and not may_see_spend_pages(architect)

    with tenant_ctx(org.id):
        shown = overview_context(finance, org.id)["overview"]
        withheld = overview_context(architect, org.id)["overview"]

    # the finance lead reads every cost figure
    assert shown["may_see_spend"] is True
    cost = shown["run_cost"]
    assert cost["total"] == 250000.0 and cost["contracts"] == 2
    assert cost["top"]["percent"] == 80 and cost["top"]["name"].startswith("Northbeam")
    assert cost["basis"] == "from 2 active contracts"
    assert "50k" in cost["difference"] and "not yet tied" in cost["difference"]
    assert [row["cost"] for row in shown["table"]["rows"]] == [120000.0, 80000.0, None]
    assert shown["table"]["cost_total"] == 200000.0

    # the solution architect reads none of them, in the shape the page renders as not shown
    assert withheld["may_see_spend"] is False
    assert withheld["run_cost"] == {"total": None, "contracts": 0, "withheld": True}
    assert withheld["table"]["cost_total"] is None
    assert withheld["links"]["spend"] is None
    rows = withheld["table"]["rows"]
    assert len(rows) == 3 and all(row["cost"] is None and row["cost_label"] is None for row in rows)
    assert not MONEY.search(json.dumps(withheld, default=str))

    # every other figure is the same for both
    for key in ("applications", "relationships", "sourced", "areas"):
        assert shown[key] == withheld[key], key
    assert sorted(row["name"] for row in rows) == sorted(row["name"] for row in shown["table"]["rows"])
