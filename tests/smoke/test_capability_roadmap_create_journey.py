"""A business architect adds a gap, a plateau and a work package on the capability roadmap
through the dialogs and sees each one at once, without a reload.

The goal: add the record, see its name on the page and in the stat cards, hear what was
created, never create a duplicate with a double click, and carry straight on to the next
dialog. Two organisations: the second one never sees the first one's records.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROADMAP = "/capability-roadmap"


def _open(browser, live_server, email, width=1440, height=1000, scheme="light"):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": height}, color_scheme=scheme
    )
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + ROADMAP, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("rdm-metric-gaps")).to_be_visible(timeout=PAGE_TIMEOUT)
    return context, page


def _metric(page, key):
    return page.get_by_test_id("rdm-metric-" + key).inner_text().strip()


def _alert_text(page):
    return page.locator('[role="status"][aria-live="polite"]').first.inner_text()


def _requests(page, fragment):
    seen = []
    page.on(
        "request",
        lambda r: seen.append(r) if fragment in r.url and r.method == "POST" else None,
    )
    return seen


def _walk(page, name):
    import os

    folder = "/home/ubuntu/verify/seats/submit01c/walk"
    if os.path.isdir(folder):
        page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


def _run(browser, live_server, width, height, scheme, tag):
    org = create_fresh_org("business_architect")
    other = create_fresh_org("business_architect")
    suffix = org["suffix"]
    gap_name = "Coverage shortfall %s" % suffix
    plateau_name = "Target plateau %s" % suffix
    wp_name = "Close the shortfall %s" % suffix

    context, page = _open(browser, live_server, org["emails"]["business_architect"], width, height, scheme)
    try:
        posts = _requests(page, "/capability-map/api/roadmap/")
        assert _metric(page, "gaps") == "0"

        # Gap: a double click must create exactly one record.
        page.get_by_role("button", name="Add Gap").click()
        dialog = page.locator("#rdm-create-gap-modal")
        expect(dialog.get_by_role("heading").first).to_be_visible()
        dialog.get_by_label("Gap Name").fill(gap_name)
        save = dialog.get_by_role("button", name="Create Gap")
        save.dblclick()
        expect(dialog.get_by_role("heading").first).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-metric-gaps")).to_have_text("1", timeout=PAGE_TIMEOUT)
        assert len([r for r in posts if r.url.endswith("/roadmap/gaps")]) == 1
        page.wait_for_function(
            "() => document.querySelector('[role=status][aria-live=polite]').textContent.includes('added')"
        )
        assert gap_name in _alert_text(page)
        gap_row = page.get_by_test_id("roadmap-gap-row").filter(has_text=gap_name)
        expect(gap_row).to_have_count(1)
        expect(gap_row).to_be_in_viewport()
        _walk(page, tag + "-gap")

        # Plateau straight away: the closed gap dialog must not block it.
        page.get_by_role("button", name="Add Plateau").click()
        dialog = page.locator("#rdm-create-plateau-modal")
        expect(dialog.get_by_role("heading").first).to_be_visible()
        dialog.get_by_label("Plateau Name").fill(plateau_name)
        dialog.get_by_role("button", name="Create Plateau").dblclick()
        expect(dialog.get_by_role("heading").first).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-metric-plateaus")).to_have_text("1", timeout=PAGE_TIMEOUT)
        assert len([r for r in posts if r.url.endswith("/roadmap/plateaus")]) == 1
        expect(page.get_by_test_id("roadmap-plateau-name").filter(has_text=plateau_name)).to_be_visible()
        page.wait_for_function(
            "() => document.querySelector('[role=status][aria-live=polite]').textContent.includes('Plateau')"
        )
        assert plateau_name in _alert_text(page)
        _walk(page, tag + "-plateau")

        # Work package.
        page.get_by_role("button", name="Add Work Package").click()
        dialog = page.locator("#rdm-create-wp-modal")
        expect(dialog.get_by_role("heading").first).to_be_visible()
        dialog.get_by_label("Name", exact=False).first.fill(wp_name)
        dates = dialog.locator('input[type="date"]')
        dates.nth(0).fill("2026-11-01")
        dates.nth(1).fill("2027-01-31")
        dialog.get_by_role("button", name="Create Work Package").dblclick()
        expect(dialog.get_by_role("heading").first).to_be_hidden(timeout=PAGE_TIMEOUT)
        row = page.locator("tr").filter(has_text=wp_name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(row).to_be_in_viewport()
        assert len([r for r in posts if r.url.endswith("/roadmap/work-packages")]) == 1
        page.wait_for_function(
            "() => document.querySelector('[role=status][aria-live=polite]').textContent.includes('Work package')"
        )
        assert wp_name in _alert_text(page)
        _walk(page, tag + "-work-package")

        # The new work package's link lands on a page with content.
        link = row.get_by_test_id("roadmap-wp-link")
        with page.expect_navigation(timeout=PAGE_TIMEOUT) as nav:
            link.click()
        assert nav.value.ok
        expect(page.locator("h1").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Persisted: after a reload the page still shows exactly one of each.
        page.goto(live_server + ROADMAP, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-metric-gaps")).to_have_text("1", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("rdm-metric-plateaus")).to_have_text("1", timeout=PAGE_TIMEOUT)
        expect(page.locator("tr").filter(has_text=wp_name)).to_have_count(1)
        expect(page.get_by_test_id("roadmap-gap-row").filter(has_text=gap_name)).to_have_count(1)
    finally:
        context.close()

    other_context, other_page = _open(browser, live_server, other["emails"]["business_architect"])
    try:
        assert _metric(other_page, "gaps") == "0"
        assert wp_name not in other_page.content()
        assert plateau_name not in other_page.content()
    finally:
        other_context.close()


def test_roadmap_create_wide_light(browser, live_server):
    _run(browser, live_server, 1440, 1000, "light", "wide-light")


def test_roadmap_create_narrow_dark(browser, live_server):
    _run(browser, live_server, 390, 844, "dark", "narrow-dark")
