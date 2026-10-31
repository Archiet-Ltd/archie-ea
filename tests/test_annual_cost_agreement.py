"""One annual cost on every screen.

For an organisation whose applications carry cost in different places (a recorded annual total,
the application's own cost columns, an active contract, nothing at all, and a decommissioned
application), the Overview total, the cost column of the applications list, each record's figure,
the list API and the chat's application read must all state the same figures. A second
organisation holds its own costed application to prove none of it leaks across.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

RECORDED, COMPONENTS, CONTRACT, DECOMMISSIONED, OTHER_ORG = 100_000, 50_000, 20_000, 999_999, 777
EXPECTED_TOTAL = RECORDED + COMPONENTS + CONTRACT


def _application(db_session, org, name, **columns):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(organization_id=org.id, name=name, **columns)
    db_session.add(row)
    db_session.flush()
    return row


def _estate(db_session, make_org, make_user):
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    org, other = make_org("cost-agree"), make_org("cost-other")
    apps = {
        "recorded": _application(db_session, org, "Ledger", lifecycle_status="operational",
                                 total_cost_of_ownership=RECORDED),
        "components": _application(db_session, org, "Billing", lifecycle_status="operational",
                                   license_cost_annual=30_000, infrastructure_cost_monthly=1_000,
                                   development_cost_annual=5_000, maintenance_cost=3_000),
        "contract": _application(db_session, org, "Payroll", lifecycle_status="operational"),
        "none": _application(db_session, org, "Intranet", lifecycle_status="operational"),
        "gone": _application(db_session, org, "Old portal", lifecycle_status="5. DECOMMISSIONED",
                             total_cost_of_ownership=DECOMMISSIONED),
    }
    _application(db_session, other, "Elsewhere", lifecycle_status="operational", total_cost_of_ownership=OTHER_ORG)
    vendor = VendorOrganization(name="Cost vendor " + uuid.uuid4().hex[:6])
    db_session.add(vendor)
    db_session.flush()
    db_session.add(VendorContract(
        organization_id=org.id, application_id=apps["contract"].id, vendor_id=vendor.id,
        contract_name="Payroll agreement", contract_number="C-" + uuid.uuid4().hex[:6],
        annual_cost=CONTRACT, status="active", start_date=date.today() - timedelta(days=60),
    ))
    db_session.flush()
    return org, apps, make_user(org, "cto")


@pytest.fixture
def make_user(db_session):
    def _make(org, role):
        from app.models.user import User

        user = User(email=f"cost-{uuid.uuid4().hex[:8]}@example.com", first_name="Cost", last_name="Viewer",
                    organization_id=org.id, confirmed=True, enterprise_role=role)
        db_session.add(user)
        db_session.flush()
        return user

    return _make


def _money(amount):
    return "£{:,.0f}".format(amount)


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def test_every_screen_states_the_same_annual_cost(app, db_session, make_org, make_user, login_as, tenant_ctx):
    org, apps, user = _estate(db_session, make_org, make_user)
    expected = {"recorded": RECORDED, "components": COMPONENTS, "contract": CONTRACT, "none": None}
    client = app.test_client()

    # Overview: the organisation total.
    from app.modules.dashboard.services.overview_figures import build_overview

    with tenant_ctx(org.id):
        assert build_overview(org.id, user)["run_cost"]["total"] == EXPECTED_TOTAL
    page = _get(client, login_as, user, "/dashboard/overview").get_data(as_text=True)
    assert _money(EXPECTED_TOTAL)[:2] in page
    assert "Appears here once" not in page

    # The applications list: a cost per row, and the column adds up to the Overview total.
    html = _get(client, login_as, user, "/applications/?page_size=100").get_data(as_text=True)
    listed = {}
    for key, row in apps.items():
        cell = re.search(r'data-testid="app-cost-%d"[^>]*>(.*?)</td>' % row.id, html, re.S)
        if key == "gone":
            assert cell is None
            continue
        assert cell, key
        text = " ".join(re.sub(r"<[^>]+>", " ", cell.group(1)).split())
        listed[key] = None if "Not recorded" in text else float(text.replace("£", "").replace(",", ""))
    assert listed == {k: (float(v) if v is not None else None) for k, v in expected.items()}
    assert sum(v for v in listed.values() if v is not None) == EXPECTED_TOTAL

    # Each record.
    for key, value in expected.items():
        if value is None:
            continue
        record = _get(client, login_as, user, "/applications/%d?tab=cost" % apps[key].id).get_data(as_text=True)
        figure = re.search(r'data-testid="annual-cost-value">\s*([^<]+)<', record)
        assert figure and figure.group(1).strip() == _money(value), key
        assert re.search(r'data-testid="annual-cost-source">\s*From ', record)

    # The list API: same figures, decommissioned left out.
    body = _get(client, login_as, user, "/applications/api/list").get_json()
    by_name = {row["name"]: row for row in body["applications"]}
    assert "Old portal" not in by_name and "Elsewhere" not in by_name
    assert {n: r["annual_cost"] for n, r in by_name.items()} == {
        "Ledger": RECORDED, "Billing": COMPONENTS, "Payroll": CONTRACT, "Intranet": None}
    assert body["total"] == 4
    assert by_name["Payroll"]["annual_cost_source"]

    # The chat's application read.
    from app.modules.ai_chat.tools.executor import ToolExecutor

    login_as(client, user)
    with tenant_ctx(org.id):
        result = ToolExecutor(user_id=user.id)._tool_find_applications({"limit": 50})
    assert result["total"] == 4
    assert result["annual_cost_total"] == EXPECTED_TOTAL
    assert {r["name"]: r["annual_cost"] for r in result["result"]} == {
        "Ledger": RECORDED, "Billing": COMPONENTS, "Payroll": CONTRACT, "Intranet": None}


def test_a_role_without_cost_visibility_is_shown_no_cost(app, db_session, make_org, make_user, login_as, tenant_ctx):
    org, apps, cto = _estate(db_session, make_org, make_user)
    viewer = make_user(org, "solution_architect")
    client = app.test_client()
    assert 'data-testid="app-cost-' in _get(client, login_as, cto, "/applications/?page_size=100").get_data(as_text=True)
    html = _get(client, login_as, viewer, "/applications/?page_size=100").get_data(as_text=True)
    assert "Annual cost" not in html and 'data-testid="app-cost-' not in html
    body = _get(client, login_as, viewer, "/applications/api/list").get_json()
    assert all("annual_cost" not in row for row in body["applications"])
    from app.modules.ai_chat.tools.executor import ToolExecutor

    login_as(client, viewer)
    with tenant_ctx(org.id):
        result = ToolExecutor(user_id=viewer.id)._tool_find_applications({"limit": 50})
    assert "annual_cost_total" not in result
    assert all("annual_cost" not in row for row in result["result"])


def test_an_organisation_with_no_cost_says_so_everywhere(app, db_session, make_org, make_user, login_as, tenant_ctx):
    org = make_org("cost-empty")
    _application(db_session, org, "Bare", lifecycle_status="operational")
    user = make_user(org, "cto")
    client = app.test_client()
    page = _get(client, login_as, user, "/dashboard/overview").get_data(as_text=True)
    assert "1 application has no annual cost recorded yet." in page
    html = _get(client, login_as, user, "/applications/?page_size=100").get_data(as_text=True)
    assert "No annual cost is recorded for this application" in html
