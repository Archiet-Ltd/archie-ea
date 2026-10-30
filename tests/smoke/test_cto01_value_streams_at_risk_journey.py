"""A CTO asks which value streams are at risk and trusts the answer: each figure
equals what the value stream's own page shows, and every name opens a page with
the stream's stages and capabilities on it."""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_value_streams_at_risk_journey import (  # noqa: F401  (vsr_seed is a fixture)
    PAGE_PATH,
    _login,
    _ok,
    _row,
    _wait_for_rows,
    vsr_seed,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_at_risk_figures_equal_the_value_stream_pages_and_names_open_them(
    width, scheme, browser, live_server, seeded, vsr_seed
):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _wait_for_rows(page)

        for key in ("onboard", "settle"):
            vs_id = vsr_seed[key]
            grid = _ok(page.request.get(live_server + "/value-streams/%d/grid" % vs_id), "grid").json()
            row = _row(page, vs_id)
            # The linked-capability figure is the number of capabilities on the stream's own grid.
            expect(row.locator("[data-vsr-capability-count]")).to_have_text(str(len(grid["capabilities"])))

            # The name opens the stream's page, which carries what the row summarises.
            link = row.locator("[data-vsr-open]")
            expect(link).to_have_attribute("href", "/value-streams/%d" % vs_id)
            link.click()
            page.wait_for_url("**/value-streams/%d" % vs_id, wait_until="commit", timeout=PAGE_TIMEOUT)
            page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_function("() => !document.querySelector('main').innerText.includes('Loading…')", timeout=PAGE_TIMEOUT)
            body = page.locator("main").inner_text()
            assert vsr_seed["names"][key] in body
            for stage in grid["stages"]:
                assert stage["name"] in body, "stage %s missing on the stream page" % stage["name"]
            for cap in grid["capabilities"]:
                assert cap["name"] in body, "capability %s missing on the stream page" % cap["name"]
            page.goto(live_server + PAGE_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _wait_for_rows(page)

        # The threshold control is the library's input and still moves the answer.
        expect(page.locator('input[data-slot="input"]#vsr-threshold-3')).to_be_checked()
        expect(page.locator('[data-vsr-page] [data-slot="card"]').first).to_be_visible()
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), "page overflows"
        import os
        folder = os.environ.get("CTO01_SHOTS")
        if folder:
            os.makedirs(folder, exist_ok=True)
            page.locator("[data-vsr-toggle]").first.click()
            page.screenshot(path=os.path.join(folder, "B-value-streams-at-risk-%d-%s.png" % (width, scheme)), full_page=True)
    finally:
        ctx.close()
