"""The demo organisation's architect finds ranked opportunities, believable candidates and one run cost.

Signed in to the seeded Lantern Quay Systems organisation, a person:
1. opens Opportunities and reads them ranked, each with a reason and an evidence footer, each opening a
   page with content;
2. runs "Find consolidation candidates" (after an earlier run left pairs that share nothing) and reads
   only pairs that share a capability, each with its reason, then dismisses one and finds it dismissed
   after a reload;
3. opens the consolidation list and reads the same run cost the application list states.
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
SHOTS = os.environ.get("OPPORTUNITY_SHOTS")
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
    """The demo organisation, seeded the way the product seeds it."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.consolidation import ConsolidationCandidate
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            demo_user = User.query.filter_by(email=DEMO_EMAIL, organization_id=org_id).one()
            demo_user.password = PASSWORD
            # the walk signs in as the organisation's Administrator, who reads cost
            demo_user.is_org_admin = True
            OrgRole.set_role(org_id, demo_user.id, "org_admin", granted_by_id=demo_user.id)
            db.session.commit()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    yield {"org_id": org_id}
    with app.app_context():
        ConsolidationCandidate.query.filter_by(organization_id=org_id).delete()
        db.session.commit()


@pytest.fixture
def stale_pairs(demo, app):
    """The pairs an earlier run of the old comparison left behind: unrelated systems proposed at 100%."""
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import ConsolidationCandidate

    org_id = demo["org_id"]
    with app.app_context():
        names = {a.name: a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()}
        ConsolidationCandidate.query.filter_by(organization_id=org_id).delete()
        for other in ("Identity Provider", "Monitoring Dashboard", "Alert Manager", "Integration Bus", "Data Warehouse"):
            db.session.add(ConsolidationCandidate(
                organization_id=org_id, primary_application_id=names["Event Relay"],
                duplicate_application_id=names[other], similarity_score=1.0,
                detection_method="function_overlap", status="pending_review"))
        db.session.commit()
        db.session.remove()
    return 5


def _signed_in(browser, live_server, viewport, scheme):
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    sign_in(page, live_server, DEMO_EMAIL)
    return page


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_opportunities_are_ranked_with_reasons_and_each_opens_its_record(browser, live_server, demo, viewport, scheme):
    page = _signed_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert "waiting list" not in body.lower() and "Early access" not in body, body[:300]
        rows = page.get_by_test_id("opportunity-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert 5 <= rows.count() <= 50, rows.count()
        # ranked: the first row states a cost, and costs never rise down the list
        costs = []
        for index in range(rows.count()):
            text = rows.nth(index).inner_text()
            found = re.findall(r"[£$€]([\d,]+)(?:\.\d+)?\s*$", text.strip())
            if found:
                costs.append(int(found[-1].replace(",", "")))
        assert costs and costs == sorted(costs, reverse=True), (costs, rows.first.inner_text())
        first = rows.first
        assert "support" in first.get_by_test_id("opportunity-reason").inner_text()
        expect(first.get_by_test_id("result-footer")).to_be_visible()
        expect(first.get_by_test_id("result-footer-evidence")).to_contain_text("Evidence")
        # a system being retired still carries a contract
        assert "Document Management" in page.locator("body").inner_text()
        _shot(page, "opp-list", viewport["width"])
        # every opportunity opens a page with content for the thing clicked
        hrefs = page.locator("[data-testid='opportunity-link']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        assert len(hrefs) == rows.count() and all(hrefs), hrefs
        for index, href in enumerate(dict.fromkeys(hrefs)):
            if index >= 6:
                break
            page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            text = page.locator("main, body").first.inner_text()
            assert len(text) > 200 and "404" not in page.title() and "Not Found" not in text[:200], (href, text[:200])
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("opportunity-link").first.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/opportunities" not in page.url
        _shot(page, "opp-record", viewport["width"])
    finally:
        page.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_find_candidates_proposes_only_pairs_that_share_a_capability(browser, live_server, demo, stale_pairs, viewport, scheme):
    page = _signed_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("candidate-row").count() == stale_pairs
        page.get_by_role("button", name="Find consolidation candidates").first.click()
        page.wait_for_load_state("domcontentloaded")
        text = page.locator("body").inner_text()
        assert "earlier candidates with nothing in common were dismissed" in text, text[:600]
        _shot(page, "candidates-after-find", viewport["width"])
        rows = page.get_by_test_id("candidate-row")
        pending = []
        for index in range(rows.count()):
            row = rows.nth(index).inner_text()
            if "Rejected" in row or "rejected" in row:
                continue
            pending.append(row)
        # four pairs of live systems in the demo support one capability each; a decommissioned system is not paired
        assert len(pending) == 4, pending
        for row in pending:
            assert "Both support the business capability" in row, row
        joined = " ".join(pending)
        assert "Identity Provider" in joined and "Integration Bus" in joined
        assert "Alert Manager" not in joined, "a decommissioned system is paired"
        assert "Event Relay" not in joined, "unrelated systems are paired again"
        # dismiss one pair; it is still dismissed after a reload
        target = page.get_by_test_id("candidate-row").filter(has_text="Order Fulfilment").first
        label = re.sub(r"\s+", " ", target.inner_text())
        target.get_by_role("button", name="Dismiss").click()
        page.wait_for_load_state("domcontentloaded")
        page.reload(wait_until="domcontentloaded")
        after = page.get_by_test_id("candidate-row").filter(has_text="Order Fulfilment").first
        assert "Dismiss" not in after.inner_text() or after.get_by_role("button", name="Dismiss").count() == 0, (label, after.inner_text())
        assert re.search(r"reject|dismiss", after.inner_text(), re.I), after.inner_text()
        _shot(page, "candidates-dismissed", viewport["width"])
    finally:
        page.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS)
def test_consolidation_list_states_the_run_cost_the_application_list_states(browser, live_server, demo, viewport, scheme):
    page = _signed_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        stated = page.get_by_test_id("run-cost-total").first.inner_text().strip()
        assert re.search(r"\d", stated), stated
        with page.expect_response(lambda r: "/consolidation-list/api/entries" in r.url and r.status == 200, timeout=PAGE_TIMEOUT):
            page.goto(live_server + "/consolidation-list/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("div.bg-card", has=page.get_by_text("Portfolio Annual Cost", exact=True)).first
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        reading = re.sub(r"\s+", " ", card.inner_text())
        assert "Not recorded" not in reading, reading

        def whole(text):
            return re.sub(r"\D", "", re.search(r"[£$€][\d,]+", text).group(0))

        assert whole(reading) == whole(stated), (reading, stated)
        _shot(page, "consolidation-list", viewport["width"])
    finally:
        page.close()
