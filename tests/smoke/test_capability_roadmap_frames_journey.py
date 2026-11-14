"""An architect saves a work package, a gap and a plateau; the tiles and the evidence line never disagree.

Every animation frame from the click to the settled page is compared: each tile against the figure the
evidence line gives for the same thing. Run in a new organisation and in a second organisation holding its own
records, so a figure cannot pass by counting the other organisation's rows.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .roadmap_frames import settle, start_sampler, stop_sampler

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_tiles_and_line_agree_on_every_frame_through_saves(browser, live_server):
    other = create_fresh_org("enterprise_architect")
    # The other organisation holds records of its own that must never be counted here.
    ctx, other_page = _page(browser, 1440, "light")
    try:
        sign_in(other_page, live_server, other["emails"]["enterprise_architect"])
        for i in range(2):
            status, _ = api(other_page, "POST", BASE + "/plateaus", {"name": "Other %d %s" % (i, other["suffix"]), "sequence_order": i + 1})
            assert status == 201
    finally:
        ctx.close()

    for width, theme in ((1440, "light"), (390, "dark")):
        org = create_fresh_org("enterprise_architect")
        suffix = org["suffix"]
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["enterprise_architect"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            settle(page)
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text("0", timeout=PAGE_TIMEOUT)
            wp_before = int(page.get_by_test_id("rdm-work-packages-tile").inner_text())
            gaps_before = int(page.get_by_test_id("rdm-recorded-gaps").inner_text())
            plateaus_before = int(page.get_by_test_id("rdm-plateaus-tile").inner_text())

            start_sampler(page)

            page.get_by_role("button", name="Add Work Package").click()
            page.fill("#rdm-wp-name", "Package %s %d" % (suffix, width))
            page.fill("#rdm-wp-start", "2026-11-01")
            page.fill("#rdm-wp-target", "2027-01-31")
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
            expect(page.get_by_test_id("rdm-work-packages-tile")).to_have_text(str(wp_before + 1), timeout=PAGE_TIMEOUT)
            settle(page)

            page.get_by_role("button", name="Add Gap").click()
            page.fill("#rdm-gap-name", "Gap %s %d" % (suffix, width))
            page.locator("#rdm-create-gap-modal").get_by_role("button", name="Create Gap").click()
            expect(page.get_by_test_id("rdm-recorded-gaps")).to_have_text(str(gaps_before + 1), timeout=PAGE_TIMEOUT)
            settle(page)

            page.get_by_role("button", name="Add Plateau").click()
            page.fill("#rdm-plateau-name", "Plateau %s %d" % (suffix, width))
            page.fill("#rdm-plateau-order", "%d" % (plateaus_before + 1))
            page.locator("#rdm-create-plateau-modal").get_by_role("button", name="Create Plateau").click()
            expect(page.get_by_test_id("rdm-plateaus-tile")).to_have_text(str(plateaus_before + 1), timeout=PAGE_TIMEOUT)
            settle(page)

            frames, bad = stop_sampler(page)
            assert frames > 20, frames
            assert bad == [], bad[:5]
            line = page.get_by_test_id("roadmap-summary-footer").inner_text()
            assert "Plateaus: %d" % (plateaus_before + 1) in line, line
            assert "Work packages: %d" % (wp_before + 1) in line, line
            assert "Gaps: %d" % (gaps_before + 1) in line, line
        finally:
            context.close()
