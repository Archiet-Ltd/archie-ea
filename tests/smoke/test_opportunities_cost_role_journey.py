"""A person the cost rule bars reads Opportunities without amounts; candidates read right on a phone.

Signed in to the seeded Lantern Quay Systems organisation:
1. an Administrator (who reads cost) sees amounts on Opportunities;
2. a solution architect (whom the one cost rule bars) sees the same opportunities and reasons, no
   contract or run-cost amount, and the sentence saying why; every link still lands on a page with content;
3. on the candidates page at 390 wide the decision on each pair is visible beside its name without
   scrolling sideways, and pairs awaiting a decision come before dismissed ones.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ADMIN_EMAIL = "demo@lantern-quay.example.com"
SHOTS = os.environ.get("OPPORTUNITY_SHOTS")
MONEY = re.compile(r"[£$€]\s?\d")


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d.png" % (name, width))), full_page=True)


@pytest.fixture(scope="module")
def world(live_server, app):
    from app import db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import ConsolidationCandidate
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    email = "solution.architect@lantern-quay.example.com"
    try:
        with app.app_context():
            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            admin = User.query.filter_by(email=ADMIN_EMAIL, organization_id=org_id).one()
            admin.password = PASSWORD
            admin.is_org_admin = True
            OrgRole.set_role(org_id, admin.id, "org_admin", granted_by_id=admin.id)
            if Role.query.filter_by(name="User").first() is None:
                Role.insert_roles()
            barred = User.query.filter_by(email=email).first()
            if barred is None:
                barred = User(email=email, first_name="Sol", last_name="Architect", organization_id=org_id,
                              role=Role.query.filter_by(name="User").first(), confirmed=True)
                db.session.add(barred)
            barred.enterprise_role = "solution_architect"
            barred.password = PASSWORD
            names = {a.name: a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()}
            ConsolidationCandidate.query.filter_by(organization_id=org_id).delete()
            db.session.add(ConsolidationCandidate(
                organization_id=org_id, primary_application_id=names["Event Relay"],
                duplicate_application_id=names["Monitoring Dashboard"], similarity_score=0.99,
                detection_method="function_overlap", status="rejected",
                notes="Dismissed: the two applications share no capability, business function or name."))
            db.session.add(ConsolidationCandidate(
                organization_id=org_id, primary_application_id=names["Identity Provider"],
                duplicate_application_id=names["Integration Bus"], similarity_score=0.5,
                detection_method="capability_overlap", status="pending_review",
                notes="Both support the business capability Order Fulfilment."))
            db.session.commit()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    yield {"org_id": org_id, "barred": email}
    with app.app_context():
        ConsolidationCandidate.query.filter_by(organization_id=org_id).delete()
        db.session.commit()


def _signed_in(browser, live_server, email, width, scheme):
    page = browser.new_page(viewport={"width": width, "height": 900 if width > 500 else 844}, color_scheme=scheme)
    sign_in(page, live_server, email)
    return page


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_the_administrator_reads_amounts_and_the_barred_role_does_not(browser, live_server, world, width, scheme):
    page = _signed_in(browser, live_server, ADMIN_EMAIL, width, scheme)
    try:
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("opportunity-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
        admin_rows = page.get_by_test_id("opportunity-row").count()
        assert MONEY.search(page.get_by_test_id("opportunities-table").inner_text())
        _shot(page, "opportunities-admin", width)
    finally:
        page.close()
    page = _signed_in(browser, live_server, world["barred"], width, scheme)
    try:
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("opportunity-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert rows.count() == admin_rows
        body = page.locator("main, body").first.inner_text()
        assert not MONEY.search(body), MONEY.findall(body)
        assert "Cost figures are withheld for your role." in body
        assert len(rows.first.get_by_test_id("opportunity-reason").inner_text()) > 40
        expect(rows.first.get_by_test_id("result-footer")).to_be_visible()
        _shot(page, "opportunities-barred", width)
        hrefs = page.locator("[data-testid='opportunity-link']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        assert hrefs and all(hrefs), hrefs
        for href in list(dict.fromkeys(hrefs))[:4]:
            response = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            text = page.locator("main, body").first.inner_text()
            assert response.status == 200 and len(text) > 200, (href, response.status, text[:200])
    finally:
        page.close()


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_candidates_show_the_decision_beside_the_name_and_pending_first(browser, live_server, world, width, scheme):
    page = _signed_in(browser, live_server, ADMIN_EMAIL, width, scheme)
    try:
        page.goto(live_server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("candidate-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert rows.count() == 2
        first, second = rows.nth(0), rows.nth(1)
        assert re.search(r"pending", first.get_by_test_id("candidate-status").inner_text(), re.I)
        assert re.search(r"reject", second.get_by_test_id("candidate-status").inner_text(), re.I)
        # the status sits inside the first column, so it is on screen without scrolling sideways
        status_box = second.get_by_test_id("candidate-status").bounding_box()
        assert status_box and status_box["x"] + status_box["width"] <= width, status_box
        _shot(page, "candidates-order", width)
    finally:
        page.close()
