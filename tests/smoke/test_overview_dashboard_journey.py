"""The Overview dashboard: flask-basecoat's Overview page filled with the organisation's own figures.

As the enterprise architect and as the owner of a company that holds linked records: reads the welcome
card, the three figure cards (each with its change and the sentence naming the record), the chart of
records added with its period select, the sparkline, the year comparison, the three cards below and
the applications table; opens every card and lands on a page with content. A second company's
records never appear. A company that has just signed up sees the same layout saying what will appear.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .overview_data import purge_org, seed_overview_org
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DASH_WALK_DIR")

APPS_A = [
    {"name": "Flow Tracker", "domain": "Operations"},
    {"name": "Ledger Core", "owner_record": True, "cost": 120000, "domain": "Finance"},
    {"name": "Payments Hub", "owner_text": "Dana Reed", "cost": 80000, "domain": "Finance"},
    {"name": "Old Billing", "status": "5. decommissioned", "domain": "Finance"},
    {"name": "(Duplicate) Ledger Core", "domain": "Finance"},
]
CONTRACTS_A = [("Northbeam", 200000, 8), ("Quillon", 50000, 3)]
APPS_B = [{"name": "Zeta Intake", "domain": "Sales"}, {"name": "Zeta Archive", "domain": "Sales"}]
CONTRACTS_B = [("Harlow", 9000, 2)]
OLD_PAGE = ("Read it another way", "View as", "Priority actions", "Layer tabs", "Applications by lifecycle")


@pytest.fixture(scope="module")
def orgs(live_server):
    a = seed_overview_org("a", APPS_A, CONTRACTS_A)
    b = seed_overview_org("b", APPS_B, CONTRACTS_B)
    yield {"a": a, "b": b}
    purge_org(a["org_id"])
    purge_org(b["org_id"])


def _shot(page, name, width, theme="light"):
    if WALK:
        page.wait_for_timeout(600)  # let the theme colours finish their transition
        size = page.viewport_size
        page.set_viewport_size({"width": width, "height": 2600})
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))
        page.set_viewport_size(size)


def _open(page, live_server, email):
    _login(page, live_server, email)
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _content(page):
    return page.locator("main").first.inner_text().strip()


def _assert_role(email, expected):
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == expected, user.enterprise_role
        admin = bool(rbac_service.is_org_admin(user, user.organization_id))
        assert admin == (expected == "platform_admin")


def _int(text):
    return int(re.sub(r"[^0-9]", "", text))


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
@pytest.mark.parametrize("persona", ["enterprise_architect", "owner"])
def test_overview_figures_chart_table_and_every_card_opens(browser, live_server, orgs, persona, viewport):
    info = orgs["a"]
    email = info["emails"][persona]
    _assert_role(email, "enterprise_architect" if persona == "enterprise_architect" else "platform_admin")
    context, page = _page(browser, viewport)
    try:
        _open(page, live_server, email)
        for theme in ("light", "dark"):
            page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
            _shot(page, "overview-%s" % persona, viewport["width"], theme)
        page.evaluate("document.documentElement.classList.remove('dark')")
        dash = page.locator('[data-testid="overview-dashboard"]')
        body = dash.inner_text()
        for old in OLD_PAGE:
            assert old not in body, old
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

        # welcome card: first name, the applications the Systems list shows, and the share of facts with a source
        first = "Launch" if persona == "enterprise_architect" else "Owen"
        expect(dash.get_by_text("Welcome back, %s" % first)).to_be_visible()
        assert _int(dash.locator('[data-testid="welcome-applications"] span').first.inner_text()) == 3
        assert re.fullmatch(r"\d+%", dash.locator('[data-testid="welcome-sourced"] span').first.inner_text().strip())

        # the three cards each say what the figure means, naming the record
        assert "Flow Tracker has no owner" in dash.locator('[data-testid="card-no-owner-sentence"]').inner_text()
        assert _int(dash.locator('[data-testid="card-no-owner-value"]').inner_text()) == 1
        assert _int(dash.locator('[data-testid="card-relationships-value"]').inner_text()) == info["relationships"]
        assert "most connected" in dash.locator('[data-testid="card-relationships-sentence"]').inner_text()
        cost_sentence = dash.locator('[data-testid="card-run-cost-sentence"]').inner_text()
        assert "Northbeam" in cost_sentence and "80%" in cost_sentence, cost_sentence
        assert "250,000" in dash.locator('[data-testid="card-run-cost-value"]').inner_text()
        for testid in ("card-run-cost", "card-relationships", "card-no-owner"):
            text = dash.locator('[data-testid="%s"]' % testid).inner_text()
            assert not re.search(r"(^|\s)(—|-|0)(\s|$)", text), (testid, text)

        # the chart is drawn with the records added, and the period select shows the other ranges
        chart = page.locator('[data-testid="activity-chart"]')
        panel = chart.locator('[data-testid="activity-range-90d"]')
        expect(panel.locator("svg").first).to_be_visible()
        assert panel.locator("svg .recharts-bar-rectangle, svg .recharts-rectangle").count() >= 1
        total_90 = _int(chart.locator('[data-testid="activity-total-90d"]').inner_text())
        assert total_90 >= 8
        page.select_option('[data-testid="activity-range"]', "7d")
        panel7 = chart.locator('[data-testid="activity-range-7d"]')
        expect(panel7.locator("svg").first).to_be_visible()
        expect(panel).to_be_hidden()
        assert panel7.locator("svg .recharts-bar-rectangle, svg .recharts-rectangle").count() >= 1
        assert _int(chart.locator('[data-testid="activity-total-7d"]').inner_text()) == total_90
        page.select_option('[data-testid="activity-range"]', "90d")

        # the applications table: the three active applications, with owner and cost
        rows = page.locator('[data-testid="applications-row"]')
        assert rows.count() == 3
        table_text = page.locator('[data-testid="applications-table"]').inner_text()
        assert "Old Billing" not in table_text and "(Duplicate)" not in table_text
        assert "Dana Reed" in table_text and "No owner" in table_text
        assert page.locator('[data-testid="monthly-cost-value"]').inner_text().strip()

        # every card opens a page that has content
        page.locator('[data-testid="card-no-owner"]').click()
        page.wait_for_url("**/applications/**no_owner=1**", timeout=PAGE_TIMEOUT)
        assert "Flow Tracker" in _content(page) and "Ledger Core" not in _content(page)
        page.go_back(wait_until="networkidle")
        page.locator('[data-testid="card-relationships"]').click()
        page.wait_for_url("**/architecture/**", timeout=PAGE_TIMEOUT)
        assert len(_content(page)) > 50
        page.go_back(wait_until="networkidle")
        page.locator('[data-testid="welcome-applications"]').click()
        page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
        assert "Ledger Core" in _content(page)
        page.go_back(wait_until="networkidle")
        page.locator('[data-testid="recent-changes-link"]').click()
        page.wait_for_url("**/history/changes**", timeout=PAGE_TIMEOUT)
        assert len(_content(page)) > 50
        page.go_back(wait_until="networkidle")
        page.locator('[data-testid="applications-table-link"]').click()
        page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
        page.go_back(wait_until="networkidle")
        page.get_by_role("link", name="Ledger Core").first.click()
        page.wait_for_url("**/applications/%d**" % info["app_ids"]["Ledger Core"], timeout=PAGE_TIMEOUT)
        assert "Ledger Core" in _content(page)
        page.go_back(wait_until="networkidle")

        # these two roles may not open the Spend page, so the run cost card is not offered as a link to it
        assert page.locator('[data-testid="card-run-cost"]').evaluate("e => e.tagName") == "DIV"
    finally:
        context.close()


def test_run_cost_card_opens_the_spend_page_for_the_portfolio_manager(browser, live_server, orgs):
    info = orgs["a"]
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, info["emails"]["portfolio_manager"])
        page.locator('[data-testid="card-run-cost"]').click()
        page.wait_for_url("**/procurement/spend**", timeout=PAGE_TIMEOUT)
        assert "250,000" in _content(page) and "Annual Recurring Cost" in _content(page)
    finally:
        context.close()


def test_figures_agree_with_the_pages_they_summarise(browser, live_server, orgs):
    info = orgs["a"]
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, info["emails"]["portfolio_manager"])
        welcome = _int(page.locator('[data-testid="welcome-applications"] span').first.inner_text())
        cost = page.locator('[data-testid="card-run-cost-value"]').inner_text().strip()
        no_owner = _int(page.locator('[data-testid="card-no-owner-value"]').inner_text())
        page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        listed = page.locator("table tbody tr").count()
        assert listed == welcome
        page.goto(live_server + "/applications/?no_owner=1", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("table tbody tr").count() == no_owner
        page.goto(live_server + "/procurement/spend", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert cost in _content(page), (cost, _content(page)[:400])
    finally:
        context.close()


def test_another_company_never_appears(browser, live_server, orgs):
    b = orgs["b"]
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, b["emails"]["enterprise_architect"])
        text = page.locator('[data-testid="overview-dashboard"]').inner_text()
        for foreign in ("Flow Tracker", "Ledger Core", "Payments Hub", "Northbeam", "Quillon", "Dana Reed", "Finance"):
            assert foreign not in text, foreign
        assert "Zeta Intake" in text and "Sales" in text
        assert _int(page.locator('[data-testid="welcome-applications"] span').first.inner_text()) == 2
        assert _int(page.locator('[data-testid="card-no-owner-value"]').inner_text()) == 2
        assert "Harlow" in page.locator('[data-testid="card-run-cost-sentence"]').inner_text()
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_a_new_company_sees_the_same_page_saying_what_will_appear(browser, live_server, viewport):
    email, _ = _empty_organisation_user(live_server)
    context, page = _page(browser, viewport)
    try:
        _open(page, live_server, email)
        dash = page.locator('[data-testid="overview-dashboard"]')
        expect(dash.get_by_text("Connect GitHub and your model is built for you")).to_be_visible()
        for theme in ("light", "dark"):
            page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
            _shot(page, "overview-empty", viewport["width"], theme)
        page.evaluate("document.documentElement.classList.remove('dark')")
        for testid in ("card-run-cost", "card-relationships", "card-no-owner"):
            card = dash.locator('[data-testid="%s"]' % testid)
            expect(card).to_be_visible()
            assert "once" in card.inner_text() or "Appear" in card.inner_text(), card.inner_text()
            assert card.locator('[data-testid="%s-value"]' % testid).count() == 0
        text = dash.inner_text()
        assert not re.search(r"(^|\s)(—|0)(\s|$)", text), text
        dash.locator('[data-testid="overview-connect"]').click()
        page.wait_for_url("**/admin/connectors/github**", timeout=PAGE_TIMEOUT)
        assert len(_content(page)) > 50
    finally:
        context.close()
