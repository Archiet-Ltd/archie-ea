"""A reader of the capability roadmap sees what is there, and is told why they cannot change it.

An architect fills the roadmap with a plateau, a gap and a work package. A reader of the
same organisation opens it and finds all three, with "Detect Gaps", "Add Plateau", "Add Gap"
and "Add Work Package" disabled and each saying who can do it; the work package has no Edit or
Delete. Asking the server to change or remove the gap or the package is refused and both are
still there after a reload. In an empty organisation the reader's empty state names who can add.
"""
import os

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/capability-roadmap"
BASE = "/capability-map/api/roadmap"


def _people():
    return create_fresh_org("viewer", platform_role="Viewer", extra_roles=("enterprise_architect",))


def _page(browser, width, theme):
    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": width, "height": 1000}, color_scheme=theme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, label, width, theme):
    out = os.environ.get("ROADMAP_FRAMES_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.screenshot(path="%s/roadmap-%s-%d-%s.png" % (out, label, width, theme), full_page=True)


def test_a_reader_sees_the_roadmap_and_cannot_change_it(browser, live_server):
    org = _people()
    other = _people()
    suffix = org["suffix"]
    gap_name = "Duplicate customer records %s" % suffix
    wp_name = "Merge customer records %s" % suffix

    context, page = _page(browser, 1440, "light")
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for label in ("Detect Gaps", "Add Plateau", "Add Gap"):
            expect(page.get_by_role("button", name=label, exact=True)).to_be_enabled()
        status, plateau = api(page, "POST", BASE + "/plateaus", {"name": "Target %s" % suffix, "sequence_order": 1})
        assert status == 201, plateau
        status, gap = api(page, "POST", BASE + "/gaps", {"name": gap_name, "gap_type": "coverage", "priority": "high"})
        assert status in (200, 201), gap
        gap_id = gap["gap"]["id"]
        status, wp = api(page, "POST", BASE + "/work-packages", {
            "name": wp_name, "plateau_id": plateau["plateau"]["id"],
            "start_date": "2026-11-01", "target_date": "2027-01-31"})
        assert status == 201, wp
        wp_id = (wp.get("work_package") or wp)["id"]
    finally:
        context.close()

    for width, theme in ((1440, "light"), (390, "dark")):
        context, page = _page(browser, width, theme)
        try:
            sign_in(page, live_server, org["emails"]["viewer"])
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text(wp_name).first).to_be_visible(timeout=PAGE_TIMEOUT)

            for label, reason in (
                ("Detect Gaps", "Only an architect can detect gaps"),
                ("Add Plateau", "Only an architect can add a plateau"),
                ("Add Gap", "Only an architect can add a gap"),
                ("Add Work Package", "Only an architect can add a work package"),
            ):
                button = page.get_by_role("button", name=label, exact=True)
                expect(button).to_be_visible()
                expect(button).to_be_disabled()
                expect(page.get_by_text(reason)).to_be_visible()
            assert page.get_by_role("button", name="Delete", exact=True).count() == 0
            assert page.get_by_role("button", name="Edit", exact=True).count() == 0
            _frame(page, "reader", width, theme)

            if width == 1440:
                for method, path, body in (
                    ("PUT", "/gaps/%d" % gap_id, {"name": "Renamed by a reader"}),
                    ("DELETE", "/gaps/%d" % gap_id, None),
                    ("POST", "/gaps/detect", {}),
                    ("DELETE", "/work-packages/%d" % wp_id, None),
                ):
                    status, answer = api(page, method, BASE + path, body)
                    assert status == 403, (method, path, status)
                    assert "may read the roadmap but not change it" in str(answer)

            page.reload(wait_until="domcontentloaded")
            expect(page.get_by_text(wp_name).first).to_be_visible(timeout=PAGE_TIMEOUT)
            status, listed = api(page, "GET", BASE + "/gaps")
            assert listed["recorded_gaps"] == 1
        finally:
            context.close()

    context, page = _page(browser, 1440, "light")
    try:
        sign_in(page, live_server, other["emails"]["viewer"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No plateaus defined")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Only an architect can add a plateau. Ask an architect").first).to_be_visible()
        assert page.get_by_role("button", name="Add First Plateau").count() == 0
        assert gap_name not in page.content()
    finally:
        context.close()
