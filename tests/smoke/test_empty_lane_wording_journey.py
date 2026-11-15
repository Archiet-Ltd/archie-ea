"""An enterprise architect opens the rich view of a layer with nothing recorded and reads what each lane is missing.

Goal: "which motivation elements and which technology lifecycle phases are still empty?" Each
empty lane names what it lacks; none prints a bare None. It runs at 1440 wide in the light
theme and at 390 wide in the dark theme.
"""

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("EMPTY_LANE_SHOTS", "") or "/tmp/empty-lane-shots")


def _open_rich_view(page, base, layer):
    page.goto(base + "/architecture/dashboard?layer=" + layer, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name=re.compile("Rich view")).first.click()


@pytest.mark.parametrize("viewport,scheme", [({"width": 1440, "height": 900}, "light"),
                                             ({"width": 390, "height": 844}, "dark")])
def test_empty_lanes_say_what_is_missing(browser, live_server, viewport, scheme):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    page = browser.new_page(viewport=viewport, color_scheme=scheme)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        _open_rich_view(page, live_server, "motivation")
        body = page.locator("body")
        for lane in ("stakeholders", "drivers", "goals", "requirements", "constraints"):
            expect(page.get_by_text("No %s recorded." % lane)).to_be_visible()
        expect(page.get_by_text(re.compile(r"^None$"))).to_have_count(0)
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("motivation-%s-%d.png" % (scheme, viewport["width"]))), full_page=True)

        _open_rich_view(page, live_server, "technology")
        for phase in ("current", "transitional", "target", "retire", "unassigned"):
            expect(page.get_by_text("No elements marked %s." % phase)).to_be_visible()
        expect(page.get_by_text(re.compile(r"^None$"))).to_have_count(0)
        expect(body).not_to_contain_text("Something went wrong")
        page.screenshot(path=str(SHOTS / ("technology-%s-%d.png" % (scheme, viewport["width"]))), full_page=True)
    finally:
        page.close()
