"""The Overview page shows money only to the roles that may see spend.

A solution architect of a company with costed applications and active contracts opens /dashboard/overview
and reads no money figure at all (no currency symbol followed by digits anywhere on the page, no short form
on the cost cards), and the page raises no failed request or console error; a finance lead of the same
company reads the annual run cost, the top vendor's share and the monthly run cost. At 1440 wide in light
and 390 wide in dark.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .overview_data import purge_org, seed_overview_org
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("COST_ROLE_WALK_DIR")
MONEY = re.compile(r"[£$€]\s?\d")
SHORT = re.compile(r"\d\s?[kKmM]\b")

APPS = [
    {"name": "Flow Tracker", "domain": "Operations"},
    {"name": "Ledger Core", "owner_record": True, "cost": 120000, "domain": "Finance"},
    {"name": "Payments Hub", "owner_text": "Dana Reed", "cost": 80000, "domain": "Finance"},
]
CONTRACTS = [("Northbeam", 200000, 8), ("Quillon", 50000, 3)]


def _add_user(org_id, suffix, role, first_name):
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        email = "%s.%s@example.com" % (role.replace("_", "."), suffix)
        user = User(email=email, first_name=first_name, last_name="Reader", organization_id=org_id,
                    enterprise_role=role, confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        db.session.remove()
    return email


def _lift_plan(org_id):
    """The seeded company holds three people on the Community plan; two more readers need the enterprise plan."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.services.billing_plans import set_contract_plan

    app = create_app("testing")
    with app.app_context():
        set_contract_plan(db.session.get(Organization, org_id), "enterprise", None)
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def org(live_server):
    info = seed_overview_org("cost-role", APPS, CONTRACTS)
    _lift_plan(info["org_id"])
    info["emails"]["solution_architect"] = _add_user(info["org_id"], info["suffix"], "solution_architect", "Sam")
    info["emails"]["finance"] = _add_user(info["org_id"], info["suffix"], "finance", "Fay")
    yield info
    purge_org(info["org_id"])


def _watch(page):
    """Every failed response, failed request and console error from now on."""
    failed = []
    page.on("response", lambda r: failed.append((r.status, r.url)) if r.status >= 400 else None)
    page.on("requestfailed", lambda r: failed.append(("request failed", r.url)))
    page.on("console", lambda m: failed.append(("console", m.text)) if m.type == "error" else None)
    return failed


def _open(page, live_server, email):
    _login(page, live_server, email)
    page.wait_for_load_state("networkidle")
    failed = _watch(page)
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return failed


def _shot(page, name):
    if WALK:
        page.wait_for_timeout(1200)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _greeting(email):
    """"Welcome" on this user's first-ever session, "Welcome back" on every later one --
    read from the user_sessions rows the login just created/extended, not assumed from
    test order (round welcome01)."""
    from app import create_app
    from app.models.user import User
    from app.models.user_session import UserSession

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        sessions = UserSession.query.filter_by(user_id=user.id).count()
    return "Welcome" if sessions <= 1 else "Welcome back"


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_a_member_who_may_not_see_spend_reads_no_money_and_no_request_fails(browser, live_server, org, viewport, theme):
    context, page = _page(browser, viewport)
    try:
        failed = _open(page, live_server, org["emails"]["solution_architect"])
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        dash = page.locator('[data-testid="overview-dashboard"]')
        expect(dash).to_be_visible()
        expect(dash.get_by_text("%s, Sam" % _greeting(org["emails"]["solution_architect"]))).to_be_visible()

        # no money anywhere on the page, and no short form on the cost cards
        body = page.locator("body").inner_text()
        assert not MONEY.search(body), MONEY.search(body).group(0)
        assert dash.locator('[data-testid="card-run-cost-value"]').count() == 0
        assert dash.locator('[data-testid="monthly-cost-value"]').count() == 0
        text = dash.locator('[data-testid="card-run-cost"]').inner_text()
        assert not SHORT.search(text), text

        # the applications themselves are still listed, without their costs
        table = dash.locator('[data-testid="applications-table"]').inner_text()
        assert "Ledger Core" in table and "Payments Hub" in table and "Dana Reed" in table
        assert not SHORT.search(table), table
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        _shot(page, "architect-%d-%s" % (viewport["width"], theme))

        # nothing the page loaded or called was refused
        page.wait_for_timeout(500)
        assert failed == [], failed
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_a_finance_lead_of_the_same_company_reads_the_run_cost_and_the_top_vendor_share(browser, live_server, org,
                                                                                          viewport, theme):
    context, page = _page(browser, viewport)
    try:
        failed = _open(page, live_server, org["emails"]["finance"])
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        dash = page.locator('[data-testid="overview-dashboard"]')
        expect(dash.get_by_text("%s, Fay" % _greeting(org["emails"]["finance"]))).to_be_visible()
        run = dash.locator('[data-testid="card-run-cost-value"]')
        assert run.inner_text().strip() == "£250k"
        assert run.get_attribute("title") == "£250,000"
        sentence = dash.locator('[data-testid="card-run-cost-sentence"]').inner_text()
        assert "From 2 active contracts" in sentence and "Northbeam" in sentence and "80%" in sentence, sentence
        assert "£50k of contract cost is not yet tied to an application" in sentence
        assert dash.locator('[data-testid="monthly-cost-value"]').inner_text().strip() == "£20.8k"
        table = dash.locator('[data-testid="applications-table"]').inner_text()
        assert "£120,000" in table and "£80,000" in table and "Not shown for your role" not in table
        assert "Annual cost recorded for the application" in table
        _shot(page, "finance-%d-%s" % (viewport["width"], theme))
        page.wait_for_timeout(500)
        assert failed == [], failed
    finally:
        context.close()
