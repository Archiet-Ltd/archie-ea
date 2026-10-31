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
    a = seed_overview_org("a", APPS_A, CONTRACTS_A, last_year=True)
    b = seed_overview_org("b", APPS_B, CONTRACTS_B)
    yield {"a": a, "b": b}
    purge_org(a["org_id"])
    purge_org(b["org_id"])


def _shot(page, name, width, theme="light"):
    if WALK:
        page.wait_for_timeout(1800)  # let the theme colours finish their transition
        size = page.viewport_size
        page.set_viewport_size({"width": width, "height": 2600})
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))
        page.set_viewport_size(size)


def _open(page, live_server, email):
    _login(page, live_server, email)
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _content(page):
    return page.locator("main").first.inner_text().strip()


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
        expect(dash.get_by_text("%s, %s" % (_greeting(email), first))).to_be_visible()
        assert _int(dash.locator('[data-testid="welcome-applications"] span').first.inner_text()) == 3
        assert re.fullmatch(r"\d+%", dash.locator('[data-testid="welcome-sourced"] span').first.inner_text().strip())

        # the three cards each say what the figure means, naming the record
        assert "Flow Tracker has no owner" in dash.locator('[data-testid="card-no-owner-sentence"]').inner_text()
        assert _int(dash.locator('[data-testid="card-no-owner-value"]').inner_text()) == 1
        assert _int(dash.locator('[data-testid="card-relationships-value"]').inner_text()) == info["relationships"]
        assert "most connected" in dash.locator('[data-testid="card-relationships-sentence"]').inner_text()
        cost_sentence = dash.locator('[data-testid="card-run-cost-sentence"]').inner_text()
        if persona == "enterprise_architect":
            # the enterprise architect may not see spend: the card carries no figure and names no vendor
            assert "Northbeam" not in cost_sentence and "%" not in cost_sentence, cost_sentence
            assert dash.locator('[data-testid="card-run-cost-value"]').count() == 0
            assert not re.search(r"[£$€]\s?\d", body), body
        else:
            assert "Ledger Core" in cost_sentence and "60%" in cost_sentence, cost_sentence
            assert dash.locator('[data-testid="card-run-cost-value"]').inner_text().strip() == "£200k"
        for testid in ("card-run-cost", "card-relationships", "card-no-owner"):
            text = dash.locator('[data-testid="%s"]' % testid).inner_text()
            assert not re.search(r"(^|\s)(—|-|0)(\s|$)", text), (testid, text)

        # the chart is drawn with the records added, and the period select shows the other ranges
        chart = page.locator('[data-testid="activity-chart"]')
        panel = chart.locator('[data-testid="activity-range-90d"]')
        page.select_option('[data-testid="activity-range"]', "90d")
        expect(panel.locator("svg").first).to_be_visible()
        assert panel.locator("svg .recharts-bar-rectangle, svg .recharts-rectangle").count() >= 1
        total_90 = _int(chart.locator('[data-testid="activity-total-90d"]').inner_text())
        assert total_90 >= 8
        page.select_option('[data-testid="activity-range"]', "7d")
        panel7 = chart.locator('[data-testid="activity-range-7d"]')
        expect(panel7.locator("svg").first).to_be_visible()
        expect(panel).to_be_hidden()
        assert panel7.locator("svg .recharts-bar-rectangle, svg .recharts-rectangle").count() >= 1
        assert 0 < _int(chart.locator('[data-testid="activity-total-7d"]').inner_text()) < total_90
        page.select_option('[data-testid="activity-range"]', "90d")

        # the applications table: the three active applications, with owner and cost
        rows = page.locator('[data-testid="applications-row"]')
        assert rows.count() == 3
        table_text = page.locator('[data-testid="applications-table"]').inner_text()
        assert "Old Billing" not in table_text and "(Duplicate)" not in table_text
        assert "Dana Reed" in table_text and "No owner" in table_text
        if persona == "enterprise_architect":
            assert page.locator('[data-testid="monthly-cost-value"]').count() == 0
            assert "£" not in table_text
        else:
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
        cost = page.locator('[data-testid="card-run-cost-value"]').get_attribute("title")
        no_owner = _int(page.locator('[data-testid="card-no-owner-value"]').inner_text())
        page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        listed = page.locator("table tbody tr").count()
        assert listed == welcome
        page.goto(live_server + "/applications/?no_owner=1", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("table tbody tr").count() == no_owner
        # the run cost is the sum of the cost column of the applications list
        page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        column = page.locator('[data-testid^="app-cost-"]').all_inner_texts()
        listed_cost = sum(int(re.sub(r"[^0-9]", "", c)) for c in column if re.search(r"\d", c))
        assert cost == "£{:,.0f}".format(listed_cost), (cost, column)
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
        assert "Harlow" not in page.locator('[data-testid="card-run-cost"]').inner_text()
        # the owner may see spend: the card says the company's own applications state no cost, naming no other company's
        context.close()
        context, page = _page(browser, FOLD)
        _open(page, live_server, b["emails"]["owner"])
        card = page.locator('[data-testid="card-run-cost"]').inner_text()
        assert "2 applications have no annual cost recorded yet." in card
        assert "Ledger Core" not in card
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
            # a signed-up architect has no cost visibility, so the run-cost card says that (cost round wording)
            # instead of "Appears here once ..."; every card still says what it is waiting for.
            assert ("once" in card.inner_text() or "Appear" in card.inner_text()
                    or "Cost figures are shown to roles" in card.inner_text()), card.inner_text()
            assert card.locator('[data-testid="%s-value"]' % testid).count() == 0
        text = dash.inner_text()
        assert not re.search(r"(^|\s)(—|0)(\s|$)", text), text
        dash.locator('[data-testid="overview-connect"]').click()
        page.wait_for_url("**/admin/connectors/github**", timeout=PAGE_TIMEOUT)
        assert len(_content(page)) > 50
    finally:
        context.close()


def _account_rows_in_history(org_id):
    """Change-history rows of user accounts in this organisation (the signing-up person's own account)."""
    from app import create_app, db
    from app.models.audit_log import AuditLog

    app = create_app("testing")
    with app.app_context():
        count = db.session.execute(
            db.select(db.func.count(AuditLog.id)).where(AuditLog.organization_id == org_id, AuditLog.table_name == "user")
        ).scalar_one()
        db.session.remove()
        return count


def _add_element_by_hand(org_id, name):
    """One model record added by a person: a single element, which writes a single change-history row."""
    from app import create_app, db
    from app.models import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        db.session.add(ArchiMateElement(name=name, type="BusinessActor", layer="Business", organization_id=org_id))
        db.session.commit()
        db.session.remove()


@pytest.mark.parametrize("viewport,theme", [(FOLD, "light"), (PHONE, "dark")], ids=["1440-light", "390-dark"])
def test_a_new_company_counts_model_records_never_its_own_account(browser, live_server, viewport, theme):
    """A company whose only change-history row is its creator's account has added no model records.

    Asserts on the numbers, not the labels. After one element is added by hand the figure reads 1.
    (An application also writes its element, so it counts as two rows on the line; not changed here.)
    """
    email, org_id = _empty_organisation_user(live_server)
    context, page = _page(browser, viewport)
    try:
        # the precondition that makes this red before the fix: the history does hold the person's account
        assert _account_rows_in_history(org_id) >= 1
        _open(page, live_server, email)
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        chart = page.locator('[data-testid="activity-chart"]')
        expect(chart.locator('[data-testid="activity-empty"]')).to_be_visible()
        assert chart.locator('[data-testid^="activity-total-"]').count() == 0, chart.inner_text()
        assert "By hand" not in chart.inner_text(), chart.inner_text()
        _shot(page, "a-overview-new-company", viewport["width"], theme)

        _add_element_by_hand(org_id, "First Actor")
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        chart = page.locator('[data-testid="activity-chart"]')
        total = chart.locator('[data-testid="activity-total-7d"]')
        expect(total).to_be_visible()
        assert _int(total.inner_text()) == 1, chart.inner_text()
        by_hand = chart.locator('[data-testid="activity-summary-7d"] span.text-xl').nth(1)
        assert _int(by_hand.inner_text()) == 1, chart.inner_text()
        _shot(page, "a-overview-one-element", viewport["width"], theme)
    finally:
        context.close()
        purge_org(org_id)


# ---- the chart, the select, the money, the year card, the facts card and the dark welcome card ----

BARS = "svg .recharts-bar-rectangle"
LABELS = "svg .recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value"


def _boxes(locator):
    return locator.evaluate_all(
        "els => els.map(e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, t: e.textContent}; })"
    )


def _signed_in_as_the_architect(info):
    email = info["emails"]["enterprise_architect"]
    _assert_role(email, "enterprise_architect")
    return email


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_main_chart_has_wide_bars_and_labels_that_do_not_touch(browser, live_server, orgs, viewport):
    """The architect sees what was added, day by day (week by week over 3 months), readable at a glance."""
    info = orgs["a"]
    email = _signed_in_as_the_architect(info)
    context, page = _page(browser, viewport)
    try:
        _open(page, live_server, email)
        select = page.locator('[data-testid="activity-range"]')
        assert select.input_value() == "7d", "records exist in the last 7 days, so the shortest range is shown first"
        for key in ("7d", "30d", "90d"):
            page.select_option('[data-testid="activity-range"]', key)
            panel = page.locator('[data-testid="activity-range-%s"]' % key)
            expect(panel.locator("svg").first).to_be_visible()
            page.wait_for_function(
                "sel => [...document.querySelectorAll(sel + ' .recharts-bar-rectangle')].some(e => e.getBoundingClientRect().height > 0)",
                arg='[data-testid="activity-range-%s"]' % key, timeout=PAGE_TIMEOUT,
            )
            page.wait_for_timeout(1200)  # the bars finish growing before they are measured
            everything = _boxes(panel.locator(BARS))
            bars = [b for b in everything if b["h"] > 0]
            wide = [b for b in bars if b["w"] > 4]
            assert len(wide) >= 2, (key, bars, everything)
            labels = _boxes(panel.locator(LABELS))
            assert len(labels) >= 2, (key, labels)
            for first, second in zip(labels, labels[1:]):
                assert first["x"] + first["w"] + 2 <= second["x"], (key, first["t"], second["t"])
            if key == "90d":
                assert len(bars) <= 14, "grouped by week, not one bar a day"
    finally:
        context.close()


def test_the_range_select_keeps_its_arrow_clear_of_its_text(browser, live_server, orgs):
    info = orgs["a"]
    email = _signed_in_as_the_architect(info)
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, email)
        for key in ("7d", "30d", "90d"):
            page.select_option('[data-testid="activity-range"]', key)
            gap = page.evaluate(
                """() => {
                  const select = document.querySelector('[data-testid="activity-range"]');
                  const style = getComputedStyle(select);
                  const canvas = document.createElement('canvas').getContext('2d');
                  canvas.font = style.fontSize + ' ' + style.fontFamily;
                  const label = select.options[select.selectedIndex].text;
                  const box = select.getBoundingClientRect();
                  const textEnd = box.left + parseFloat(style.paddingLeft) + canvas.measureText(label).width;
                  const icon = select.parentElement.querySelector('svg').getBoundingClientRect();
                  return icon.left - textEnd;
                }"""
            )
            assert gap >= 4, (key, gap)
    finally:
        context.close()


def test_money_is_short_on_cards_and_whole_pounds_in_the_table(browser, live_server, orgs):
    info = orgs["a"]
    email = info["emails"]["owner"]  # the owner may see spend; the architect reads no money here
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, email)
        run = page.locator('[data-testid="card-run-cost-value"]')
        assert run.inner_text().strip() == "£200k"
        assert run.get_attribute("title") == "£200,000"
        month = page.locator('[data-testid="monthly-cost-value"]')
        assert month.inner_text().strip() == "£16.7k"
        assert month.get_attribute("title") == "£16,667"
        table = page.locator('[data-testid="applications-table"]').inner_text()
        assert "£120,000" in table and "£80,000" in table
        assert ".00" not in table
        assert not re.search(r"£[\d,]+\.\d\d\b", page.locator('[data-testid="overview-dashboard"]').inner_text())
    finally:
        context.close()


def test_status_reads_as_a_word_with_a_capital(browser, live_server, orgs):
    info = orgs["a"]
    email = _signed_in_as_the_architect(info)
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, email)
        table = page.locator('[data-testid="applications-table"]')
        assert table.get_by_text("Operational", exact=True).count() >= 1
        assert table.get_by_text("operational", exact=True).count() == 0
    finally:
        context.close()


def test_year_card_sets_last_year_beside_this_year(browser, live_server, orgs):
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, _signed_in_as_the_architect(orgs["a"]))
        card = page.locator('[data-testid="year-card"]')
        last = _int(card.locator('[data-testid="year-last"]').inner_text())
        this = _int(card.locator('[data-testid="year-this"]').inner_text())
        assert last == 1 and this >= 8
        assert _int(card.locator('[data-testid="year-value"]').inner_text()) == this
        page.wait_for_timeout(700)
        assert card.locator("svg .recharts-bar-rectangle").count() >= 2
        context.close()
        context, page = _page(browser, FOLD)
        _open(page, live_server, orgs["b"]["emails"]["enterprise_architect"])
        card = page.locator('[data-testid="year-card"]')
        assert "No records were added last year" in card.locator('[data-testid="year-empty"]').inner_text()
        assert card.locator("svg").count() == 0
    finally:
        context.close()


def test_run_cost_and_the_applications_table_agree_or_say_why(browser, live_server, orgs):
    """The run cost is the sum of the applications' own costs, the same figures the table lists; the card says how many."""
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, orgs["a"]["emails"]["owner"])  # the owner may see spend
        card = page.locator('[data-testid="card-run-cost"]').inner_text()
        assert "From the annual cost of 2 applications" in card
        assert "1 application has no cost recorded." in card
        costs = page.locator('[data-testid="applications-row"] td:last-child').all_inner_texts()
        listed = sum(int(re.sub(r"[^0-9]", "", c)) for c in costs if re.search(r"\d", c))
        assert listed == 200000
        assert "Annual cost recorded for the application" in page.locator('[data-testid="applications-table"]').inner_text()
        assert page.locator('[data-testid="card-run-cost-value"]').get_attribute("title") == "£200,000"
        # a company whose applications state no cost is told so, and shown no figure
        context.close()
        context, page = _page(browser, FOLD)
        _open(page, live_server, orgs["b"]["emails"]["owner"])
        text = page.locator('[data-testid="card-run-cost"]').inner_text()
        assert "2 applications have no annual cost recorded yet." in text
        assert page.locator('[data-testid="card-run-cost-value"]').count() == 0
    finally:
        context.close()


def test_facts_card_lists_the_least_sourced_layers_and_each_opens_the_model(browser, live_server, orgs):
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, _signed_in_as_the_architect(orgs["a"]))
        card = page.locator('[data-testid="sourced-card"]')
        rows = card.locator('[data-testid="sourced-layer"]')
        assert 1 <= rows.count() <= 4
        for index in range(rows.count()):
            assert re.search(r"\d+%", rows.nth(index).inner_text())
        rows.first.get_by_role("link").click()
        page.wait_for_url(lambda url: "/architecture" in url and "layer=" in url, timeout=PAGE_TIMEOUT)
        assert len(_content(page)) > 50
    finally:
        context.close()


def _contrast(page, selector):
    return page.evaluate(
        """sel => {
          const el = document.querySelector(sel);
          const parse = c => c.match(/[\\d.]+/g).slice(0, 3).map(Number);
          const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
          const bg = parse(getComputedStyle(el).backgroundColor);
          const fg = parse(getComputedStyle(el.querySelector('[data-slot=card-title]')).color);
          const [hi, lo] = [lum(bg), lum(fg)].sort((a, b) => b - a);
          return { ratio: (hi + 0.05) / (lo + 0.05), bg: bg, lightness: lum(bg) };
        }""",
        selector,
    )


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_the_welcome_card_stays_dark_in_the_dark_theme(browser, live_server, orgs, viewport):
    context, page = _page(browser, viewport)
    try:
        _open(page, live_server, _signed_in_as_the_architect(orgs["a"]))
        selector = '[data-testid="overview-welcome"]'
        page.evaluate("document.documentElement.classList.add('dark')")
        page.wait_for_timeout(600)
        dark = _contrast(page, selector)
        assert dark["ratio"] >= 4.5, dark
        assert dark["lightness"] < 0.1, dark
        page.evaluate("document.documentElement.classList.remove('dark')")
        page.wait_for_timeout(600)
        assert _contrast(page, selector)["ratio"] >= 4.5
    finally:
        context.close()


# ---- every figure, legend and label reads in both themes ----

READABILITY_PROBE = """
() => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  const rgba = c => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (top, under) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a)).concat([1]); };
  const lum = ([r, g, b]) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const backdrop = el => { const stack = []; for (let n = el; n; n = n.parentElement) stack.push(n);
    let colour = rgba(getComputedStyle(document.documentElement).backgroundColor); if (colour[3] === 0) colour = [255, 255, 255, 1];
    for (const n of stack.reverse()) { const s = getComputedStyle(n); const bg = rgba(s.backgroundColor);
      if (bg[3] > 0) colour = over(bg, colour); } return colour; };
  const opacity = el => { let o = 1; for (let n = el; n; n = n.parentElement) o *= parseFloat(getComputedStyle(n).opacity); return o; };
  const out = [];
  const root = document.querySelector('main') || document.body;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim(); const el = node.parentElement;
    if (!text || seen.has(el) || /^(SCRIPT|STYLE)$/i.test(el.tagName)) continue;
    seen.add(el);
    const s = getComputedStyle(el); const box = el.getBoundingClientRect();
    if (s.visibility === 'hidden' || s.display === 'none' || (box.width === 0 && box.height === 0)) continue;
    if (el.closest('[x-cloak], [hidden]') || el.closest('[style*="display: none"]')) continue;
    const svg = el instanceof SVGElement;
    const fg = rgba(svg ? s.fill : s.color); const bg = backdrop(el);
    const o = opacity(el); const fgOver = over([fg[0], fg[1], fg[2], fg[3] * o], bg);
    const hi = Math.max(lum(fgOver), lum(bg)), lo = Math.min(lum(fgOver), lum(bg));
    const ratio = (hi + 0.05) / (lo + 0.05);
    const px = parseFloat(s.fontSize); const bold = parseInt(s.fontWeight, 10) >= 700;
    const need = (px >= 24 || (px >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio < need) out.push({text: text.slice(0, 40), ratio: Math.round(ratio * 100) / 100, need: need, fg: fgOver.slice(0, 3).map(Math.round).join(','), bg: bg.slice(0, 3).map(Math.round).join(','), tag: el.tagName, cls: String(el.getAttribute('class') || '').slice(0, 60)});
  }
  return out;
}
"""


def _unreadable(page):
    return page.evaluate(READABILITY_PROBE)


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_figure_legend_and_label_reads_in_both_themes(browser, live_server, orgs, theme, viewport):
    """The architect can read the records total, the legend under the bars and the layer names, light or dark."""
    context, page = _page(browser, viewport)
    try:
        _open(page, live_server, _signed_in_as_the_architect(orgs["a"]))
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.select_option('[data-testid="activity-range"]', "90d")
        page.wait_for_timeout(1500)
        page.evaluate("document.getAnimations().forEach(a => a.finish())")  # colours are measured once they have settled
        _shot(page, "overview-contrast", viewport["width"], theme)
        failures = _unreadable(page)
        assert failures == [], "\n".join("%s | %s | fg %s on %s | %s %s" % (f["text"], f["ratio"], f["fg"], f["bg"], f["tag"], f["cls"]) for f in failures)
        total = page.locator('[data-testid="activity-total-90d"]')
        legend = page.locator('[data-testid="activity-range-90d"]').get_by_text("Connected sources")
        layer = page.locator('[data-testid="sourced-layer"]').first
        for found in (total, legend, layer):
            expect(found).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_figure_cards_are_neutral_and_the_charts_use_the_brand_colour(browser, live_server, orgs, theme):
    """The cards sit on the plain card surface as in the library's Overview; the bars are the brand colour and its tint."""
    context, page = _page(browser, FOLD)
    try:
        _open(page, live_server, _signed_in_as_the_architect(orgs["a"]))
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.select_option('[data-testid="activity-range"]', "90d")
        page.wait_for_timeout(1500)
        page.evaluate("document.getAnimations().forEach(a => a.finish())")
        surfaces = page.evaluate(
            """() => ['card-run-cost', 'card-relationships', 'card-no-owner', 'year-card'].map(id => {
              const el = document.querySelector('[data-testid="' + id + '"]');
              const card = el.matches('[data-slot=card]') ? el : el.querySelector('[data-slot=card]');
              return getComputedStyle(card).backgroundColor; })"""
        )
        assert len(set(surfaces)) == 1, surfaces
        hues = page.evaluate(
            """() => {
              const hue = c => { const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(v => v / 255);
                const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min; if (!d) return -1;
                const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4; return Math.round(((h * 60) + 360) % 360); };
              const fills = new Set([...document.querySelectorAll('[data-testid="activity-range-90d"] .recharts-bar-rectangle path, [data-testid="year-card"] .recharts-bar-rectangle path')]
                .map(e => getComputedStyle(e).fill).filter(f => f && f !== 'none'));
              return [...fills].map(hue); }"""
        )
        assert len(hues) >= 2, hues
        assert all(200 <= h <= 230 for h in hues), hues  # every bar is a shade of the brand blue
        card = page.locator('[data-testid="year-card"]').inner_text()
        assert re.search(r"\+\d+%\s+on last year", card), card
    finally:
        context.close()
