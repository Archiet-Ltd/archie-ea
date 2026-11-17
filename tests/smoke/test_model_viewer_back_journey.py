"""A signed-in architect with no generated model returns to the Architecture Assistant from the model viewer.

The goal: get back to the assistant to generate a model. The back button must be named for where it goes,
be readable on a phone, and land on the assistant with content.
"""
import os

import pytest

from tests.smoke.test_archetype_journeys import _login

PAGE_TIMEOUT = 30000
BACK = "Back to Architecture Assistant"


@pytest.mark.smoke
@pytest.mark.parametrize("width", [1440, 390])
def test_empty_model_viewer_back_button_lands_on_the_assistant(browser, live_server, seeded, width):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/architecture-assistant/model-viewer", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        back = page.get_by_role("button", name=BACK, exact=True)
        back.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        box = back.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the back button fits the screen"
        assert back.evaluate("e => e.scrollWidth <= e.clientWidth"), "the label is not cut off"
        shots = os.environ.get("SMOKE_WALK_DIR")
        if shots:
            page.screenshot(path=os.path.join(shots, "model-viewer-empty-%d.png" % width))
        back.click()
        page.wait_for_url("**/architecture-assistant/", timeout=PAGE_TIMEOUT)
        assert page.locator("h1, h2").first.inner_text().strip(), "the assistant page shows a heading"
    finally:
        context.close()
