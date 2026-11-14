"""An application's fact sheet and Cost tab show money only to people the one cost rule allows.

Goal: "I open an application's fact sheet and its Cost tab. If my role may see spend I read the figures and can
follow the link to change them; if it may not, I read one sentence saying cost figures are withheld for my role,
and no figure and no edit-cost link anywhere on either page."

Two organisations each hold an application with its own recorded cost; the finance person and the solution
architect of the first read both pages, and nothing of the second shows on either.
"""
import os
import pathlib
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="application-cost-visibility-"))
HELD_BACK = "Cost figures are withheld for your role."
# licence, monthly infrastructure, development, maintenance, total cost of ownership
FIGURES = dict(license_cost_annual=48210.0, infrastructure_cost_monthly=3175.0, development_cost_annual=91340.0,
               maintenance_cost=27655.0, total_cost_of_ownership=207005.0)
THEIRS = dict(license_cost_annual=88123.0, infrastructure_cost_monthly=4417.0, development_cost_annual=73991.0,
              maintenance_cost=61337.0, total_cost_of_ownership=999111.0)
SEEN_IN_PAGES = ("48,210", "3,175", "38,100", "91,340", "27,655", "207,005")
THEIR_FIGURES = ("88,123", "4,417", "73,991", "61,337", "999,111")


def _organisation(figures):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    org = create_fresh_org("finance", extra_roles=("solution_architect",))
    name = "Ledger service %s" % uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        app_row = ApplicationComponent(name=name, organization_id=org["org_id"], lifecycle_status="operational",
                                       description="Holds the ledger.", **figures)
        db.session.add(app_row)
        db.session.commit()
        org["app_id"], org["app_name"] = app_row.id, name
    return org


@pytest.fixture(scope="module")
def organisations(live_server):
    return _organisation(FIGURES), _organisation(THEIRS)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def _open(browser, live_server, email, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _visible_text(page):
    return page.locator("main").inner_text()


def test_a_finance_person_reads_the_figures_and_the_edit_link_lands_on_the_editor(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = _open(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        page.goto("%s/applications/%d/fact-sheet" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        stats = page.get_by_test_id("fact-sheet-stats")
        expect(stats).to_contain_text("207,005", timeout=PAGE_TIMEOUT)
        expect(stats).to_contain_text("License/yr: 48,210")
        assert HELD_BACK not in _visible_text(page)
        _shot(page, "finance-fact-sheet", 1440, "light")

        page.goto("%s/applications/%d?tab=cost" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        body = _visible_text(page)
        for figure in SEEN_IN_PAGES:
            assert figure in body, figure
        # licence + 12 x monthly infrastructure + development + maintenance
        assert "205,305" in body, "the calculated total agrees with its parts"
        assert HELD_BACK not in page.content()
        for figure in THEIR_FIGURES:
            assert figure not in page.content(), figure
        _shot(page, "finance-cost-tab", 1440, "light")
        link = page.get_by_role("link", name=re.compile("Edit Cost Details"))
        expect(link).to_have_count(1)
        link.click()
        page.wait_for_url(re.compile(r"/applications/%d/edit" % mine["app_id"]), timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").inner_text().strip()) > 100
    finally:
        context.close()


def test_a_solution_architect_reads_one_sentence_and_no_figure_on_either_page(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = _open(browser, live_server, mine["emails"]["solution_architect"], 1440, "light")
    try:
        page.goto("%s/applications/%d/fact-sheet" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("fact-sheet-stats")).to_contain_text("Record completeness", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK)
        html = page.content()
        assert html.count(HELD_BACK) == 1, "the sentence is said once"
        for figure in SEEN_IN_PAGES + THEIR_FIGURES:
            assert figure not in html, figure
        assert "License/yr" not in html and "No licence cost recorded" not in html
        _shot(page, "architect-fact-sheet", 1440, "light")

        page.goto("%s/applications/%d?tab=cost" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK)
        html = page.content()
        assert html.count(HELD_BACK) == 1, "the sentence is said once"
        for figure in SEEN_IN_PAGES + THEIR_FIGURES:
            assert figure not in html, figure
        assert "Edit Cost Details" not in html
        assert "Share of technology costs" not in html
        assert "£" not in _visible_text(page)
        _shot(page, "architect-cost-tab", 1440, "light")
    finally:
        context.close()


def test_a_solution_architect_on_a_phone_in_the_dark_reads_the_same_sentence(browser, live_server, organisations):
    mine, _ = organisations
    context, page = _open(browser, live_server, mine["emails"]["solution_architect"], 390, "dark")
    try:
        page.goto("%s/applications/%d/fact-sheet" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert not any(figure in page.content() for figure in SEEN_IN_PAGES)
        _shot(page, "architect-fact-sheet", 390, "dark")
        page.goto("%s/applications/%d?tab=cost" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert "Edit Cost Details" not in page.content()
        assert not any(figure in page.content() for figure in SEEN_IN_PAGES)
        _shot(page, "architect-cost-tab", 390, "dark")
    finally:
        context.close()


def test_a_finance_person_on_a_phone_in_the_dark_reads_the_figures(browser, live_server, organisations):
    mine, _ = organisations
    context, page = _open(browser, live_server, mine["emails"]["finance"], 390, "dark")
    try:
        page.goto("%s/applications/%d/fact-sheet" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("fact-sheet-stats")).to_contain_text("207,005", timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        _shot(page, "finance-fact-sheet", 390, "dark")
        page.goto("%s/applications/%d?tab=cost" % (live_server, mine["app_id"]), wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        assert "207,005" in _visible_text(page)
        _shot(page, "finance-cost-tab", 390, "dark")
    finally:
        context.close()
