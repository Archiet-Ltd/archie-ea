"""The Consolidation Opportunities page lists the opportunities the Opportunities page ranks.

Signed in to the seeded Lantern Quay Systems organisation as its Administrator, a person:
1. opens Opportunities and counts what is ranked;
2. opens Consolidation Opportunities and finds the same count, the same titles and the same run cost
   for each, in the same order;
3. reads a savings figure, or the sentence that names the step that gives one, and follows its link to a
   page with content.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
RESTRICTED_EMAIL = "reader.nocost@lantern-quay.example.com"
SHOTS = os.environ.get("CONSOLIDATION_OPPORTUNITY_SHOTS")
VIEWPORTS = [
    pytest.param({"width": 1440, "height": 900}, "light", id="1440-light"),
    pytest.param({"width": 390, "height": 844}, "dark", id="390-dark"),
]


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d.png" % (name, width))), full_page=True)


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demo organisation, seeded the way the product seeds it, its Administrator able to read cost."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            user = User.query.filter_by(email=DEMO_EMAIL, organization_id=org_id).one()
            user.password = PASSWORD
            user.is_org_admin = True
            OrgRole.set_role(org_id, user.id, "org_admin", granted_by_id=user.id)
            reader = User.query.filter_by(email=RESTRICTED_EMAIL).first()
            if reader is None:
                # a person with no budget authority in the same organisation
                reader = User(email=RESTRICTED_EMAIL, first_name="Reader", last_name="NoCost",
                              organization_id=org_id, enterprise_role="solution_architect", confirmed=True)
                reader.password = PASSWORD
                db.session.add(reader)
                db.session.flush()
                OrgRole.set_role(org_id, reader.id, "viewer", granted_by_id=user.id)
            db.session.commit()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return {"org_id": org_id}


def _rows(page, testid):
    """(title, cost text) for each row of the ranked table, in order."""
    out = []
    rows = page.get_by_test_id(testid)
    for index in range(rows.count()):
        row = rows.nth(index)
        title = row.get_by_test_id("opportunity-link").inner_text().strip()
        cost = re.sub(r"\s+", " ", row.get_by_test_id("opportunity-cost").inner_text()).strip()
        out.append((title, cost))
    return out


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_both_pages_list_the_same_opportunities_and_savings_is_never_a_dead_end(browser, live_server, demo, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("opportunity-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
        ranked = _rows(page, "opportunity-row")
        total = page.get_by_test_id("opportunities-total-value").inner_text().strip()
        assert len(ranked) >= 5, ranked

        page.goto(live_server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("ranked-opportunity-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
        here = _rows(page, "ranked-opportunity-row")
        assert here == ranked, (here, ranked)
        assert page.get_by_test_id("consolidation-opportunities-total-value").inner_text().strip() == total
        _shot(page, "consolidation-opportunities", viewport["width"])

        # the savings figure is a number, or a sentence naming the step that gives one, with a link that works
        savings = page.get_by_test_id("opportunities-savings")
        figures = page.locator("[data-testid^='opportunities-savings-2'][data-testid$='-value']")
        if figures.count():
            for i in range(figures.count()):
                assert any(ch.isdigit() for ch in figures.nth(i).inner_text())
        else:
            expect(savings).to_be_visible()
            step = page.get_by_test_id("savings-next-step")
            expect(step).to_be_visible()
            step.click()
            page.wait_for_load_state("domcontentloaded")
            assert "/consolidation/candidates" in page.url, page.url
            text = page.locator("main, body").first.inner_text()
            assert len(text) > 200 and "Not Found" not in text[:200], text[:200]

        # every opportunity listed here opens a page with content
        page.goto(live_server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        hrefs = page.locator("[data-testid='ranked-opportunity-row'] [data-testid='opportunity-link']").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))")
        assert len(hrefs) == len(ranked) and all(hrefs), hrefs
        for href in list(dict.fromkeys(hrefs))[:4]:
            response = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            text = page.locator("main, body").first.inner_text()
            assert response.status == 200 and len(text) > 200, (href, response.status, text[:200])
    finally:
        page.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_a_person_without_budget_authority_sees_the_opportunities_but_no_cost(browser, live_server, demo, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, RESTRICTED_EMAIL)
        page.goto(live_server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/consolidation/opportunities" in page.url, page.url
        expect(page.get_by_test_id("ranked-opportunity-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
        main = page.locator("main").inner_text()
        assert "Cost figures are not shown for your role" in main
        for sign in ("$", "£", "€"):
            assert sign not in main, (sign, main)
        costs = page.get_by_test_id("opportunity-cost")
        assert costs.count() >= 5
        for i in range(costs.count()):
            cell = costs.nth(i).inner_text()
            assert not any(ch.isdigit() for ch in cell) and cell.strip(), cell
        assert page.locator("[data-testid^='opportunities-savings-2']").count() == 0
        expect(page.get_by_test_id("opportunities-savings")).to_contain_text("not shown for your role")
        _shot(page, "consolidation-opportunities-restricted", viewport["width"])
    finally:
        page.close()
