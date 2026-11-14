"""An architect edits a work package that has no description and the page never reads "None".

The architect adds a package with a name only, opens its edit form, changes the name and saves.
The detail page shows the new name and no word "None" as its subtitle, and the edit form's
description box is empty rather than holding the text "None". On the capability roadmap an
architect sees the controls to add and delete a work package; a Viewer sees neither, and the
page still reads normally.
"""
import os
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tempfile.gettempdir())


def _page(browser, width=1440):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_saved_work_package_without_description_never_reads_none(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context, page = _page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        status, body = api(page, "POST", "/implementation/api/work-packages", {"name": "Retire the old ledger"})
        assert status in (200, 201), body
        page.goto(live_server + "/implementation/work-packages", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("Retire the old ledger").first.click()
        page.wait_for_load_state("domcontentloaded")
        detail_url = page.url
        page.goto(detail_url.rstrip("/") + "/edit", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#description")).to_have_value("")
        page.fill("#name", "Retire the old general ledger")
        page.get_by_role("button", name="Edit Work Package").click()
        page.wait_for_load_state("domcontentloaded")
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        detail = page.get_by_test_id("work-package-detail")
        expect(detail).to_contain_text("Retire the old general ledger")
        assert "None" not in page.locator("main").inner_text()
        page.screenshot(path=str(SHOT_DIR / "wp-detail-1440.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 900})
        page.screenshot(path=str(SHOT_DIR / "wp-detail-390.png"), full_page=True)
    finally:
        context.close()


def test_capability_roadmap_controls_follow_the_role(browser, live_server):
    org = create_fresh_org("procurement", platform_role="Viewer", extra_roles=("enterprise_architect",))
    for who, can in (("enterprise_architect", True), ("procurement", False)):
        context, page = _page(browser)
        try:
            sign_in(page, live_server, org["emails"][who])
            page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", name="Work Packages")).to_be_visible()
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                page.screenshot(path=str(SHOT_DIR / ("roadmap-%s-%d.png" % (who, width))), full_page=True)
            add = page.get_by_role("button", name="Add Work Package")
            if can:
                expect(add).to_be_enabled()
            else:
                expect(add).to_be_disabled()
                expect(page.get_by_role("button", name="Delete")).to_have_count(0)
        finally:
            context.close()
