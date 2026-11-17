"""The rationalisation decision dossier and the application dashboard show money only to people the one cost rule allows.

Goal: "I open the rationalisation page, pick an application and load its decision dossier. If my role may see
spend I read the savings, the investment and the cost of ownership; if it may not, I read one sentence saying
cost figures are withheld for my role, and no amount in the panel or in the reply behind it. The application
dashboard, reached from each of its four routes, shows no amount to either of us."

Two organisations each score one application with its own figures; the finance person and the solution architect
of the first read the dossier, and nothing of the second shows anywhere.
"""
import os
import pathlib
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="dossier-cost-visibility-"))
HELD_BACK = "Cost figures are withheld for your role."
OURS = dict(estimated_annual_savings=61234, estimated_investment_needed=15877, total_cost_of_ownership=188451)
THEIRS = dict(estimated_annual_savings=71911, estimated_investment_needed=22013, total_cost_of_ownership=399887)
OUR_FIGURES = ("61,234", "15,877", "188,451")
THEIR_FIGURES = ("71,911", "22,013", "399,887")
# licence, monthly infrastructure, development, maintenance, total cost of ownership of the application itself
APP_COSTS = dict(license_cost_annual=48210.0, infrastructure_cost_monthly=3175.0, development_cost_annual=91340.0,
                 maintenance_cost=27655.0, total_cost_of_ownership=207005.0)
APP_FIGURES = ("48,210", "3,175", "91,340", "27,655", "207,005")


def _organisation(figures):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationRationalizationScore

    org = create_fresh_org("finance", extra_roles=("solution_architect",))
    name = "Ledgerline %s" % uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        app_row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="operational",
                                       description="Holds the ledger.", **APP_COSTS)
        db.session.add(app_row)
        db.session.flush()
        db.session.add(ApplicationRationalizationScore(
            application_component_id=app_row.id, organization_id=org["org_id"], disposition_action="retain", rationalization_action="INVEST",
            overall_health_score=72, priority="medium",
            action_rationale="Keeps the ledger running.", **figures))
        db.session.commit()
        org["app_id"], org["app_name"] = app_row.id, name
    org["admin_email"] = "launch.admin.%s@example.com" % org["suffix"]
    return org


@pytest.fixture(scope="module")
def organisations(live_server):
    return _organisation(OURS), _organisation(THEIRS)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def _open(browser, live_server, email, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _load_dossier(page, live_server, org):
    """Do what a person does: open the page, pick the application by name, press Load Dossier."""
    page.goto("%s/dashboard/rationalization" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    search = page.locator("#dossier-app-search")
    search.scroll_into_view_if_needed()
    search.fill(org["app_name"][:9])
    page.get_by_role("option", name=org["app_name"]).click(timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Load decision dossier for selected application").click()
    panel = page.locator("[x-data='decisionDossier()']")
    expect(panel.get_by_text("Financial Impact")).to_be_visible(timeout=PAGE_TIMEOUT)
    return panel


def test_a_finance_person_reads_the_dossier_figures(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = _open(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        panel = _load_dossier(page, live_server, mine)
        card = panel.locator("div.rounded-md", has_text="Financial Impact").last
        for figure in OUR_FIGURES:
            expect(card).to_contain_text(figure)
        assert HELD_BACK not in page.content()
        for figure in THEIR_FIGURES:
            assert figure not in page.content(), figure
        status, body = api(page, "GET", "/applications/rationalization/api/decision-dossier/%d" % mine["app_id"])
        assert status == 200 and body["financial"]["total_cost_of_ownership"] == 188451.0
        assert body["financial"]["estimated_annual_savings"] == 61234.0
        assert "costs_withheld" not in body["financial"]
        panel.scroll_into_view_if_needed()
        _shot(page, "finance-dossier", 1440, "light")
    finally:
        context.close()


def test_a_solution_architect_reads_one_sentence_and_no_amount_in_the_dossier(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = _open(browser, live_server, mine["emails"]["solution_architect"], 1440, "light")
    try:
        panel = _load_dossier(page, live_server, mine)
        expect(panel.get_by_text(HELD_BACK)).to_have_count(1)
        expect(panel.get_by_text("No financial data recorded.")).not_to_be_visible()
        # the rest of the dossier is still there to read
        expect(panel.get_by_text("Keeps the ledger running.")).to_be_visible()
        text = panel.inner_text()
        for figure in OUR_FIGURES + THEIR_FIGURES + APP_FIGURES:
            assert figure not in text, figure
        assert "£" not in text
        assert text.count(HELD_BACK) == 1, "the sentence is said once"
        status, body = api(page, "GET", "/applications/rationalization/api/decision-dossier/%d" % mine["app_id"])
        assert status == 200
        assert body["financial"]["total_cost_of_ownership"] is None
        assert body["financial"]["estimated_annual_savings"] is None
        assert body["financial"]["estimated_investment_needed"] is None
        assert body["costs_withheld_sentence"] == HELD_BACK
        raw = str(body)
        for figure in ("61234", "15877", "188451", "71911", "22013", "399887"):
            assert figure not in raw, figure
        panel.scroll_into_view_if_needed()
        _shot(page, "architect-dossier", 1440, "light")
    finally:
        context.close()


def test_a_solution_architect_on_a_phone_in_the_dark_reads_the_same_sentence(browser, live_server, organisations):
    mine, _ = organisations
    context, page = _open(browser, live_server, mine["emails"]["solution_architect"], 390, "dark")
    try:
        panel = _load_dossier(page, live_server, mine)
        expect(panel.get_by_text(HELD_BACK)).to_have_count(1)
        assert not any(figure in panel.inner_text() for figure in OUR_FIGURES)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        panel.scroll_into_view_if_needed()
        _shot(page, "architect-dossier", 390, "dark")
    finally:
        context.close()


def test_a_finance_person_on_a_phone_in_the_dark_reads_the_figures(browser, live_server, organisations):
    mine, _ = organisations
    context, page = _open(browser, live_server, mine["emails"]["finance"], 390, "dark")
    try:
        panel = _load_dossier(page, live_server, mine)
        for figure in OUR_FIGURES:
            assert figure in panel.inner_text(), figure
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        panel.scroll_into_view_if_needed()
        _shot(page, "finance-dossier", 390, "dark")
    finally:
        context.close()


DASHBOARD_ROUTES = (
    "/dashboard/dashboard",
    "/dashboard/applications/%(app_id)d/roadmap",
    "/dashboard/applications/%(app_id)d/gap-analysis",
    "/dashboard/admin/custom-fields",
)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_the_application_dashboard_shows_no_amount_on_any_of_its_four_routes(browser, live_server, organisations,
                                                                              width, scheme):
    mine, theirs = organisations
    # the custom-fields route is open to the organisation's administrator; the others to everyone who signs in
    people = (("solution_architect", mine["emails"]["solution_architect"]), ("finance", mine["emails"]["finance"]),
              ("administrator", mine["admin_email"]))
    for who, email in people:
        context, page = _open(browser, live_server, email, width, scheme)
        try:
            for route in DASHBOARD_ROUTES:
                path = route % mine
                response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                if response.status == 403:
                    assert who != "administrator" or "custom-fields" not in path, "the administrator reads %s" % path
                    continue
                assert response.status == 200, (who, path, response.status)
                main = page.locator("main").inner_text()
                assert len(main.strip()) > 100, (who, path)
                html = page.content()
                for figure in APP_FIGURES + OUR_FIGURES + THEIR_FIGURES:
                    assert figure not in html, (who, path, figure)
                assert "£" not in main, (who, path)
                assert "Edit Cost Details" not in html, (who, path)
                _shot(page, "%s-%s" % (who, path.strip("/").replace("/", "-").replace(str(mine["app_id"]), "app")),
                      width, scheme)
        finally:
            context.close()
