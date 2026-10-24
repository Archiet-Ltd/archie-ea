"""The dashboard is the admin dashboard frame filled with the company's own figures.

As the enterprise architect of a workspace that holds data: reads four figure cards in one row, each
opening a list that has content; changes the chart's range and sees the other range's panel; filters
the coverage table with its tabs and pages through it; types a question and lands in the AI chat with
the question waiting, while the dashboard itself never shows an answer. As a new architect in an
empty workspace: sees the two first actions as the page's empty state.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_first_screen_one_message_journey import FOLD, PHONE, _assert_persona, _page
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("DASH_WALK_DIR")


def _shot(page, name, width, theme="light"):
    if WALK:
        # the page scrolls inside the shell, so a tall window shows all of it
        size = page.viewport_size
        page.set_viewport_size({"width": width, "height": 2600})
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)))
        page.set_viewport_size(size)


def _tops(page, testids):
    return [page.locator('[data-testid="%s"]' % t).first.evaluate("e => Math.round(e.getBoundingClientRect().top)") for t in testids]


def _link_capabilities(email, covered=3, partial=3, gaps=7):
    """Give the architect's organisation top-level capabilities linked to its applications: some with two
    applications (covered), some with one (partial), the rest with none (gaps). Returns the ids to remove."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        apps = ApplicationComponent.query.filter_by(organization_id=org_id).limit(2).all()
        assert len(apps) == 2
        made = []
        for index, take in enumerate([2] * covered + [1] * partial + [0] * gaps):
            cap = BusinessCapability(name="Dash capability %02d" % index, level=1, organization_id=org_id)
            db.session.add(cap)
            db.session.flush()
            for row in apps[:take]:
                db.session.add(ApplicationCapabilityMapping(application_component_id=row.id, business_capability_id=cap.id,
                                                            organization_id=org_id))
            made.append(cap.id)
        db.session.commit()
        db.session.remove()
        return made


def _unlink_capabilities(ids):
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        ApplicationCapabilityMapping.query.filter(ApplicationCapabilityMapping.business_capability_id.in_(ids)).delete(synchronize_session=False)
        BusinessCapability.query.filter(BusinessCapability.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


CARDS = ("kpi-tile-applications", "kpi-tile-solutions", "kpi-tile-elements", "kpi-tile-health-score")


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_dashboard_frame_cards_chart_and_table(browser, live_server, seeded, viewport):
    email = seeded["emails"]["enterprise_architect"]
    _assert_persona(email)
    capability_ids = _link_capabilities(email)
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        for theme in ("light", "dark"):
            page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
            _shot(page, "dashboard", viewport["width"], theme)
        page.evaluate("document.documentElement.classList.remove('dark')")

        # four figure cards; in one row on a wide screen, each with a number
        tops = _tops(page, CARDS)
        if viewport["width"] == FOLD["width"]:
            assert len(set(tops)) == 1, tops
        for testid in CARDS:
            expect(page.locator('[data-testid="%s"]' % testid)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

        # the area chart: the 90-day panel shows, choosing 7 days shows only that panel
        chart = page.locator('[data-testid="activity-chart"]')
        expect(chart).to_be_visible()
        expect(chart.locator('[data-testid="activity-range-90d"] svg')).to_be_visible()
        page.locator("#tab-range-7d").click()
        expect(chart.locator('[data-testid="activity-range-7d"] svg')).to_be_visible()
        expect(chart.locator('[data-testid="activity-range-90d"]')).to_be_hidden()

        # the coverage table: tabs filter it, pages move
        table = page.locator('[data-testid="capability-health-widget"]')
        expect(table).to_be_visible()
        page.locator("#tab-coverage-gap").click()
        rows = table.locator('[data-testid="coverage-row"]:visible')
        assert rows.count() == 7
        for text in rows.all_inner_texts():
            assert "Gap" in text, text
        expect(page.locator("#tab-coverage-gap")).to_contain_text("7")
        # a row opens the capability on its map, with content
        rows.first.get_by_role("link").click()
        page.wait_for_url("**/capability-map/**", timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").first.inner_text().strip()) > 50
        page.go_back(wait_until="networkidle")
        page.locator("#tab-coverage-all").click()
        label = table.locator('[data-testid="coverage-page-label"]')
        expect(label).to_have_text("Page 1 of 2")
        assert table.locator('[data-testid="coverage-row"]:visible').count() == 10
        table.locator('[data-testid="coverage-next"]').click()
        expect(label).to_have_text("Page 2 of 2")
        assert table.locator('[data-testid="coverage-row"]:visible').count() == 3

        # a card opens a list that has content
        if viewport["width"] == FOLD["width"]:
            page.locator('[data-testid="kpi-tile-applications"]').click()
            page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
            assert len(page.locator("main").first.inner_text().strip()) > 50
    finally:
        context.close()
        _unlink_capabilities(capability_ids)


def test_question_opens_the_chat_and_the_dashboard_shows_no_answer(browser, live_server, seeded):
    email = seeded["emails"]["enterprise_architect"]
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="overview-cascade"]')).to_have_count(0)
        page.locator('[data-testid="first-question-input"]').fill("what breaks if payments fails?")
        page.locator('[data-testid="first-question-submit"]').click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)
        expect(page.locator("#user-input")).to_have_value("what breaks if payments fails?")
    finally:
        context.close()


def test_empty_workspace_shows_the_two_first_actions_as_the_empty_state(browser, live_server, seeded):
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        empty = page.locator('[data-testid="dashboard-empty-state"]')
        expect(empty).to_be_visible()
        expect(empty.locator('[data-testid="first-action-connect"]')).to_be_visible()
        expect(empty.locator('[data-testid="first-action-sample"]')).to_be_visible()
        for testid in CARDS:
            expect(page.locator('[data-testid="%s"]' % testid)).to_have_count(0)
        _shot(page, "dashboard-empty", 1440)
    finally:
        context.close()
