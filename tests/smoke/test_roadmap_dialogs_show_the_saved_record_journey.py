"""Saving a gap, a plateau or a work package on the roadmap shows the result at once.

A business architect on /capability-roadmap opens the Add Gap, Add Plateau and Add
Work Package dialogs. Saving each one closes the dialog, moves the matching stat
card, and announces what was created to a screen reader -- all without a reload.
The plateau and the work package are also visible on the page by name; a gap has
no name surface on this page today (only its stat card and the work-package row
it ends up linked to), so that half of the assertion does not apply to it. A
failed save (a work package whose target date is before its start date) keeps
the dialog open and shows the server's reason. A second organisation never sees
the first's records.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser, viewport, scheme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


@pytest.mark.parametrize(
    "viewport,scheme",
    [({"width": 1440, "height": 1000}, "light"), ({"width": 390, "height": 844}, "dark")],
    ids=["1440-light", "390-dark"],
)
def test_roadmap_dialogs_show_the_saved_record_without_a_reload(browser, live_server, viewport, scheme):
    org = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    suffix = org["suffix"]
    gap_name = "Submit01b gap %s" % suffix
    plateau_name = "Submit01b plateau %s" % suffix
    wp_name = "Submit01b work package %s" % suffix

    # The modal id (e.g. "#rdm-create-wp-modal") names the outer wrapper that
    # Platform.modal toggles `hidden` on; it carries no size of its own (its
    # backdrop and panel are `position: fixed` children, so the wrapper's own
    # box is always zero-height). Visibility is asserted on `.modal-panel`,
    # which is what a person actually sees.
    plateau_panel = "#rdm-create-plateau-modal .modal-panel"
    gap_panel = "#rdm-create-gap-modal .modal-panel"
    wp_panel = "#rdm-create-wp-modal .modal-panel"

    context, page = _new_page(browser, viewport, scheme)
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)

        alert_region = page.locator('[role="alert"]')

        gaps_before = int((page.get_by_test_id("rdm-metric-gaps").inner_text() or "0").strip())
        plateaus_before = int((page.get_by_test_id("rdm-metric-plateaus").inner_text() or "0").strip())
        wps_before = int((page.get_by_test_id("rdm-metric-wps").inner_text() or "0").strip())

        # ── Plateau: name on the page, stat card moved, announced -- no reload ──
        page.locator("button", has_text="Add Plateau").first.click()
        page.locator("#rdm-plateau-name").fill(plateau_name)
        page.locator("#rdm-plateau-order").fill("1")
        with page.expect_response(
            lambda r: r.url.endswith("/capability-map/api/roadmap/plateaus") and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.locator("#rdm-create-plateau-modal").get_by_role("button", name="Create Plateau").click()
        assert resp.value.status == 201, resp.value.text()
        expect(page.locator(plateau_panel)).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-plateau-name").filter(has_text=plateau_name)).to_have_count(
            1, timeout=PAGE_TIMEOUT
        )
        expect(page.get_by_test_id("rdm-metric-plateaus")).to_have_text(
            str(plateaus_before + 1), timeout=PAGE_TIMEOUT
        )
        expect(alert_region).to_contain_text(plateau_name, timeout=PAGE_TIMEOUT)
        expect(alert_region).not_to_contain_text("dialog closed")

        # ── Gap: stat card moved and announced -- no reload.
        # No gap name is ever rendered on this page (confirmed by reading the
        # template: the only gap-related text nodes are the metric, a work
        # package's linked-gap column, and the gap picker inside the work
        # package dialog), so "the name is on the page" is proven for the
        # plateau and the work package only, below. ──
        page.locator("button", has_text="Add Gap").first.click()
        page.locator("#rdm-gap-name").fill(gap_name)
        with page.expect_response(
            lambda r: r.url.endswith("/capability-map/api/roadmap/gaps") and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.locator("#rdm-create-gap-modal").get_by_role("button", name="Create Gap").click()
        assert resp.value.status in (200, 201), resp.value.text()
        expect(page.locator(gap_panel)).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-metric-gaps")).to_have_text(str(gaps_before + 1), timeout=PAGE_TIMEOUT)
        expect(alert_region).to_contain_text(gap_name, timeout=PAGE_TIMEOUT)
        expect(alert_region).not_to_contain_text("dialog closed")

        # ── Work package: name on the page, stat card moved, announced -- no reload ──
        page.locator("button", has_text="Add Work Package").first.click()
        page.locator("#rdm-wp-name").fill(wp_name)
        page.locator("#rdm-wp-start").fill("2026-11-01")
        page.locator("#rdm-wp-target").fill("2027-01-31")
        with page.expect_response(
            lambda r: "/capability-map/api/roadmap/work-packages" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
        assert resp.value.status == 201, resp.value.text()
        expect(page.locator(wp_panel)).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-wp-link").filter(has_text=wp_name)).to_have_count(
            1, timeout=PAGE_TIMEOUT
        )
        expect(page.get_by_test_id("rdm-metric-wps")).to_have_text(str(wps_before + 1), timeout=PAGE_TIMEOUT)
        expect(alert_region).to_contain_text(wp_name, timeout=PAGE_TIMEOUT)
        expect(alert_region).not_to_contain_text("dialog closed")

        # ── A failed save (target date before start date) keeps the dialog open
        # and shows the server's reason, rather than a silent close. ──
        page.locator("button", has_text="Add Work Package").first.click()
        page.locator("#rdm-wp-name").fill("Should not save " + suffix)
        page.locator("#rdm-wp-start").fill("2027-01-31")
        page.locator("#rdm-wp-target").fill("2026-11-01")
        with page.expect_response(
            lambda r: "/capability-map/api/roadmap/work-packages" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as resp:
            page.locator("#rdm-create-wp-modal").get_by_role("button", name="Create Work Package").click()
        assert resp.value.status == 400, resp.value.text()
        reason_dialog = page.locator('[role="alertdialog"]')
        expect(reason_dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(reason_dialog).to_contain_text("target_date must be on or after start_date")
        reason_dialog.get_by_role("button", name="OK").click()
        expect(page.locator(wp_panel)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("roadmap-wp-link").filter(has_text="Should not save")).to_have_count(0)
        page.locator("#rdm-create-wp-modal").get_by_role("button", name="Cancel").click()
        expect(page.locator(wp_panel)).to_be_hidden(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    other_context, other_page = _new_page(browser, viewport, scheme)
    try:
        sign_in(other_page, live_server, other["emails"]["business_architect"])
        other_page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert gap_name not in other_page.content()
        assert plateau_name not in other_page.content()
        assert wp_name not in other_page.content()
        expect(other_page.get_by_test_id("rdm-metric-plateaus")).to_have_text("0")
        expect(other_page.get_by_test_id("rdm-metric-wps")).to_have_text("0")
    finally:
        other_context.close()
