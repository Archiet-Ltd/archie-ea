"""The Overview for the two organisations people actually open: the demonstration company and a sample company.

As the enterprise architect of the Lantern Quay demonstration company, and as a new sign-up who loads the
sample company from the first screen: reads the run cost cards, the chart of records added, the recent changes,
the facts with a source and the applications by area. The run cost is the figure the Spend page and the
business model canvas state, the chart has bars on at least three days of the last 30, every recent change names
its record, every element states its source, every application sits in a business area, and the message that
announces the load is inside the window.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_first_screen_one_message_journey import FOLD, PHONE, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DASH_WALK_DIR")
DEMO_EMAIL = "demo@lantern-quay.example.com"
BARS = "svg .recharts-bar-rectangle"


@pytest.fixture(scope="module")
def demo_org(live_server):
    """The demonstration organisation, seeded with the product's own seed, and a portfolio manager in it."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_EMAIL).one().organization_id
            manager = User(email="pm.demo@example.com", first_name="Pat", last_name="Manager",
                           organization_id=org_id, enterprise_role="portfolio_manager", confirmed=True)
            manager.role = Role.query.filter_by(name="Architect").one()
            manager.password = PASSWORD
            if not User.query.filter_by(email=manager.email).count():
                db.session.add(manager)
            db.session.commit()
            db.session.remove()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return {"org_id": org_id, "architect": DEMO_EMAIL, "manager": "pm.demo@example.com"}


def _shot(page, name, width, theme="light"):
    if WALK:
        page.wait_for_timeout(1800)
        size = page.viewport_size
        page.set_viewport_size({"width": width, "height": 2600})
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))
        page.set_viewport_size(size)


def _both_themes(page, name, width):
    for theme in ("light", "dark"):
        page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        _shot(page, name, width, theme)
    page.evaluate("document.documentElement.classList.remove('dark')")


def _read_the_overview(page):
    """Everything the page's cards, chart and lists say, as a person reads them."""
    dash = page.locator('[data-testid="overview-dashboard"]')
    expect(dash).to_be_visible()
    page.wait_for_load_state("networkidle")
    return dash


def _assert_a_full_overview(page, dash):
    # the run cost figure is stated, with the sentence saying where it comes from
    run = dash.locator('[data-testid="card-run-cost-value"]')
    expect(run).to_be_visible()
    assert re.fullmatch(r"£\d[\d.,]*[kmM]?", run.inner_text().strip()), run.inner_text()
    assert "Appears here once" not in dash.inner_text()
    assert "applications" in dash.locator('[data-testid="card-run-cost-sentence"]').inner_text()
    assert dash.locator('[data-testid="monthly-cost-value"]').inner_text().strip()
    assert "appears once an application" not in dash.locator('[data-testid="monthly-cost-value"]').locator("xpath=../..").inner_text()
    # the chart has bars on at least three different days of the last 30
    page.select_option('[data-testid="activity-range"]', "30d")
    panel = dash.locator('[data-testid="activity-range-30d"]')
    page.wait_for_function(
        "sel => [...document.querySelectorAll(sel + ' .recharts-bar-rectangle')].filter(e => e.getBoundingClientRect().height > 0).length >= 3",
        arg='[data-testid="activity-range-30d"]', timeout=PAGE_TIMEOUT,
    )
    drawn = panel.locator(BARS).evaluate_all("els => els.filter(e => e.getBoundingClientRect().height > 0).length")
    assert drawn >= 3, drawn
    # every recent change names its record
    recent = page.locator('[data-testid="recent-changes"] [data-slot="item-title"]').all_inner_texts()
    assert len(recent) >= 3, recent
    for title in recent:
        assert title.strip() and title.strip() not in ("An element", "Element", "Record"), recent
    # every element states its source, and every application sits in a business area
    assert dash.locator('[data-testid="welcome-sourced"]').inner_text().split("\n")[0].strip() == "100%"
    assert "names its source" in dash.locator('[data-testid="sourced-sentence"]').inner_text()
    areas = page.locator('[data-testid="areas-chart"]')
    assert "No area recorded" not in areas.get_attribute("data-chart-data")


def test_the_demonstration_company_overview_is_full_and_its_figures_agree_with_the_pages_they_summarise(
        browser, live_server, demo_org):
    """The Lantern Quay architect reads a run cost that the Spend page and the canvas state, at both widths."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.services.application_cost_accessor import annual_cost_figures, counted_applications_query

    for viewport in (FOLD, PHONE):
        context, page = _page(browser, viewport)
        try:
            _login(page, live_server, demo_org["architect"])
            page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            dash = _read_the_overview(page)
            _assert_a_full_overview(page, dash)
            _both_themes(page, "overview-demo", viewport["width"])
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            exact = dash.locator('[data-testid="card-run-cost-value"]').get_attribute("title")
        finally:
            context.close()

    app = create_app("testing")
    with app.app_context():
        contracts = VendorContract.query.filter_by(organization_id=demo_org["org_id"], status="active").all()
        costs = {c.application_id: c.annual_cost for c in contracts}
        names = {a.id: a.name for a in ApplicationComponent.query.filter_by(organization_id=demo_org["org_id"]).all()}
        db.session.remove()
    assert len(set(costs.values())) >= 5, "the contracts state differing annual costs"
    with app.app_context():
        counted = db.session.execute(counted_applications_query(demo_org["org_id"])).scalars().all()
        run_cost = sum(f["value"] for f in annual_cost_figures(counted).values() if f["value"] is not None)
        db.session.remove()
    assert run_cost > 0
    assert exact == "£{:,.0f}".format(run_cost), (exact, run_cost)

    # the portfolio manager opens the Spend page from the card and reads the contracts there
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, demo_org["manager"])
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="card-run-cost"]').click()
        page.wait_for_url("**/procurement/spend**", timeout=PAGE_TIMEOUT)
        assert "Annual Recurring Cost" in page.locator("main").first.inner_text()
    finally:
        context.close()

    # the canvas's cost box lists the application the largest contract is for, with a cost a year
    one = next(app_id for app_id, cost in sorted(costs.items(), key=lambda kv: -kv[1]))
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, demo_org["architect"])
        page.goto(live_server + "/business-model/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=re.compile("Lantern Quay", re.I)).first.click()
        page.wait_for_url(re.compile(r"/business-model/\d+"), timeout=PAGE_TIMEOUT)
        cost_box = page.locator('[data-canvas-notes="cost-structure"]')
        expect(cost_box).to_contain_text(names[one])
        assert re.search(r"%s: [\d,]+ GBP a year" % re.escape(names[one]), cost_box.inner_text())
    finally:
        context.close()


def test_a_new_company_that_loads_the_sample_reads_a_full_overview_and_sees_the_message_in_the_window(
        browser, live_server):
    for viewport in (FOLD, PHONE):
        email, org_id = _empty_organisation_user(live_server)
        context, page = _page(browser, viewport)
        try:
            _login(page, live_server, email)
            page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_test_id("chat-first-sample").click()
            toast = page.locator("#platform-toast-container").get_by_text("Sample company loaded")
            expect(toast).to_be_visible(timeout=PAGE_TIMEOUT * 2)
            page.wait_for_timeout(1500)  # the message slides in; it is measured once it has settled
            box = toast.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"], (box, viewport)
            _shot(page, "sample-loaded-message", viewport["width"])
            page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            dash = _read_the_overview(page)
            _assert_a_full_overview(page, dash)
            _both_themes(page, "overview-sample", viewport["width"])
        finally:
            context.close()
