"""A gap on Opportunities agrees with the work-package list, opens its own page, and reads on a phone.

Signed in to the seeded Lantern Quay Systems organisation as its Administrator:
1. a gap that a work package is linked to is not listed as "Gap not started", and the gaps that are listed
   have no work package linked to them, by the same reader the work-package list and Recommendations use;
2. clicking a listed gap lands on that gap's own page, which names it;
3. at 390 wide every opportunity's reason, evidence and cost sentence are inside the screen, and the rows
   are not separated by blank space.
"""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ADMIN_EMAIL = "demo@lantern-quay.example.com"
SHOTS = os.environ.get("OPPORTUNITY_SHOTS")


def _shot(page, name, width):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / ("%s-%d.png" % (name, width))), full_page=True)


@pytest.fixture(scope="module")
def world(live_server, app):
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.implementation_migration import Gap
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import User
    from app.services import work_package_service

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            admin = User.query.filter_by(email=ADMIN_EMAIL, organization_id=org_id).one()
            admin.password = PASSWORD
            admin.is_org_admin = True
            OrgRole.set_role(org_id, admin.id, "org_admin", granted_by_id=admin.id)
            unlinked = Gap.query.filter_by(organization_id=org_id, name="Spare parts forecasting gap").first()
            if unlinked is None:
                unlinked = Gap(organization_id=org_id, name="Spare parts forecasting gap", gap_kind="capability_shortfall",
                               severity="high", resolution_status="identified")
                db.session.add(unlinked)
            db.session.commit()
            unlinked_id = unlinked.id
            linked = work_package_service.addressed_gap_ids(org_id)
            gaps = Gap.query.filter(Gap.organization_id == org_id, Gap.id.in_(linked or [0])).all()
            open_identified = Gap.query.filter_by(organization_id=org_id, resolution_status="identified").all()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    linked_names = {g.name for g in gaps if g.resolution_status == "identified"}
    assert linked_names, "the demonstration seed links no identified gap to a work package"
    yield {"org_id": org_id, "linked_names": linked_names, "open_identified": len(open_identified),
           "unlinked_name": "Spare parts forecasting gap", "unlinked_id": unlinked_id}
    with app.app_context():
        from app import db
        Gap.query.filter_by(id=unlinked_id, organization_id=org_id).delete()
        db.session.commit()


def _signed_in(browser, live_server, width, scheme):
    page = browser.new_page(viewport={"width": width, "height": 900 if width > 500 else 844}, color_scheme=scheme)
    sign_in(page, live_server, ADMIN_EMAIL)
    return page


def _gap_rows(page):
    return page.get_by_test_id("opportunity-row").filter(has_text="Gap not started")


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_listed_gaps_have_no_work_package_and_open_their_own_page(browser, live_server, app, world, width, scheme):
    from app.services import work_package_service

    page = _signed_in(browser, live_server, width, scheme)
    try:
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("opportunity-row").first).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = _gap_rows(page)
        titles = [rows.nth(i).get_by_test_id("opportunity-link").inner_text().strip() for i in range(rows.count())]
        # a gap the work-package list links a package to is not offered as one nobody has started
        assert not (set(titles) & world["linked_names"]), (titles, world["linked_names"])
        with app.app_context():
            addressed = work_package_service.addressed_gap_ids(world["org_id"])
        from app.models.implementation_migration import Gap
        with app.app_context():
            named = {g.name for g in Gap.query.filter(Gap.organization_id == world["org_id"], Gap.id.in_(addressed or [0]))}
            from app import db
            db.session.remove()
        assert not (set(titles) & named), (titles, named)
        # the count card agrees with the rows
        card = page.get_by_test_id("opportunities-gaps").inner_text()
        assert str(rows.count()) in card, (card, rows.count())
        _shot(page, "opportunities-gaps", width)
        assert world["unlinked_name"] in titles, titles
        first = rows.filter(has_text=world["unlinked_name"]).get_by_test_id("opportunity-link")
        name = first.inner_text().strip()
        first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.endswith("/%d" % world["unlinked_id"]) or "/%d" % world["unlinked_id"] in page.url, page.url
        body = page.locator("main, body").first.inner_text()
        assert "No critical gaps recorded yet" not in body and "Implementation Planning" not in page.title(), page.url
        assert name in body, (page.url, name, body[:300])
        assert len(body) > 200
        _shot(page, "opportunity-gap-page", width)
    finally:
        page.close()


def test_phone_rows_show_reason_evidence_and_cost_without_blank_space(browser, live_server, world):
    page = _signed_in(browser, live_server, 390, "dark")
    try:
        page.goto(live_server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.get_by_test_id("opportunity-row")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        _shot(page, "opportunities-phone", 390)
        if SHOTS:
            rows.first.scroll_into_view_if_needed()
            page.screenshot(path=str(Path(SHOTS) / "opportunities-phone-rows-390.png"))
        for i in range(min(rows.count(), 8)):
            row = rows.nth(i)
            row.scroll_into_view_if_needed()
            box = row.bounding_box()
            assert box["height"] < 700, ("blank space in row %d" % i, box)
            for part in ("opportunity-reason", "result-footer", "opportunity-cost"):
                el = row.get_by_test_id(part).first
                eb = el.bounding_box()
                assert eb and eb["width"] > 0, (i, part)
                assert eb["x"] >= 0 and eb["x"] + eb["width"] <= 390 + 1, (i, part, eb)
                assert eb["y"] >= box["y"] and eb["y"] + eb["height"] <= box["y"] + box["height"] + 1, (i, part, eb, box)
    finally:
        page.close()
