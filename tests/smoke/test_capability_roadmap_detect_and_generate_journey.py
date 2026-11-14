"""An architect detects gaps, sees a failed detection, generates packages with AI and reads the timeline.

Each journey starts in a new organisation holding a capability that is below its target maturity, so there is
something to detect. Figures are read from the tile and from the evidence line, and the two must agree.
"""
import json

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .roadmap_frames import settle, start_sampler, stop_sampler

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
VIEWPORTS = ((1440, "light"), (390, "dark"))


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _org_with_weak_capability():
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        db.session.add(BusinessCapability(
            name="Weak %s" % org["suffix"], level=1, organization_id=org["org_id"],
            current_maturity_level=1, target_maturity_level=3))
        db.session.commit()
        db.session.remove()
    return org


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_detect_gaps_updates_tile_and_line_together(browser, live_server, viewport):
    org = _org_with_weak_capability()
    context, page = _page(browser, *viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        settle(page)
        assert page.get_by_test_id("rdm-recorded-gaps").inner_text() == "0"
        assert "Gaps: none recorded" in page.get_by_test_id("roadmap-summary-footer").inner_text()

        start_sampler(page)
        page.get_by_role("button", name="Detect Gaps").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Detect gaps", exact=True).click()
        expect(page.get_by_test_id("rdm-recorded-gaps")).to_have_text("1", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-summary-footer")).to_contain_text("Gaps: 1", timeout=PAGE_TIMEOUT)
        settle(page)
        _, bad = stop_sampler(page)
        assert bad == [], bad[:5]
        # The figure holds after a reload: it was recorded, not only drawn.
        page.reload(wait_until="domcontentloaded")
        settle(page)
        assert page.get_by_test_id("rdm-recorded-gaps").inner_text() == "1"
        assert "Gaps: 1" in page.get_by_test_id("roadmap-summary-footer").inner_text()
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_failed_detect_shows_an_error_and_changes_no_figure(browser, live_server, viewport):
    org = _org_with_weak_capability()
    context, page = _page(browser, *viewport)

    def fail_detect(route):
        try:
            route.fulfill(status=500, body=json.dumps({"success": False, "error": "detection service unavailable"}),
                          headers={"Content-Type": "application/json"})
        except Exception as e:
            if "closed" not in str(e).lower():
                raise

    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.route("**/capability-map/api/roadmap/gaps/detect", fail_detect)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        settle(page)
        line_before = page.get_by_test_id("roadmap-summary-footer").inner_text()
        page.get_by_role("button", name="Detect Gaps").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Detect gaps", exact=True).click()
        error = page.get_by_test_id("rdm-detect-error")
        expect(error).to_be_visible(timeout=PAGE_TIMEOUT)
        assert error.inner_text().strip() != ""
        settle(page)
        assert page.get_by_test_id("rdm-recorded-gaps").inner_text() == "0"
        assert page.get_by_test_id("roadmap-summary-footer").inner_text() == line_before
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_ai_generated_package_reaches_table_tile_line_and_timeline(browser, live_server, viewport):
    org = _org_with_weak_capability()
    name = "Generated %s" % org["suffix"]
    context, page = _page(browser, *viewport)

    def preview(route):
        try:
            route.fulfill(status=200, headers={"Content-Type": "application/json"}, body=json.dumps({
                "success": True,
                "preview": [{
                    "gap_id": None,
                    "suggested_plateau": "Plateau 1",
                    "suggested_work_package": {
                        "name": name, "description": "Raise the capability to its target.",
                        "priority": "high", "estimated_start_date": "2026-11-01",
                        "target_resolution_date": "2027-02-28"},
                }]}))
        except Exception as e:
            if "closed" not in str(e).lower():
                raise

    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.route("**/capability-map/api/roadmap/ai-generate", preview)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        settle(page)
        assert page.get_by_test_id("rdm-work-packages-tile").inner_text() == "0"

        page.get_by_label("Select all work packages").check()
        page.get_by_role("button", name="Generate roadmap").click()
        expect(page.get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Confirm & Create").click()

        expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("1", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-summary-footer")).to_contain_text("Work packages: 1", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-wp-link").filter(has_text=name)).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(page.locator("#rdm-gantt-chart").get_by_text(name)).to_have_count(1, timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_saved_package_enters_the_timeline_and_deleted_one_leaves(browser, live_server, viewport):
    org = create_fresh_org("enterprise_architect")
    name = "Timeline %s" % org["suffix"]
    context, page = _page(browser, *viewport)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        settle(page)
        page.get_by_role("button", name="Add Work Package").click()
        page.fill("#rdm-wp-name", name)
        page.fill("#rdm-wp-start", "2026-11-01")
        page.fill("#rdm-wp-target", "2027-01-31")
        page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
        gantt = page.locator("#rdm-gantt-chart")
        expect(gantt.get_by_text(name)).to_have_count(1, timeout=PAGE_TIMEOUT)

        page.locator("tr").filter(has_text=name).get_by_role("button", name="Delete").first.click()
        page.get_by_role("alertdialog").get_by_role("button", name="Delete", exact=True).click()
        expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text("0", timeout=PAGE_TIMEOUT)
        expect(gantt.get_by_text(name)).to_have_count(0, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
