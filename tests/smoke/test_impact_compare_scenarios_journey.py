"""An enterprise architect on Impact Analysis clicks Compare Scenarios and gets the
comparison dialog with the scenarios saved in this browser to pick from."""
import json
import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
WALK_DIR = os.environ.get("SMOKE_WALK_DIR")
VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]
SAVED = [
    {"id": "s1", "name": "Retire the legacy billing system", "change_type": "retire",
     "element": {"name": "Legacy Billing"}, "analysis_result": {"severity": "HIGH"}},
    {"id": "s2", "name": "Move billing to the cloud", "change_type": "migrate",
     "element": {"name": "Billing Cloud"}, "analysis_result": {"severity": "LOW"}},
]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_compare_scenarios_opens_the_dialog_with_saved_scenarios(browser, live_server, seeded, viewport, scheme):
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.evaluate("s => localStorage.setItem('impactAnalysisScenarios', s)", json.dumps(SAVED))
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    trigger = page.get_by_role("button", name="Compare Scenarios")
    expect(trigger).to_be_visible()
    trigger.focus()
    trigger.click()

    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Retire the legacy billing system")).to_be_visible()
    expect(dialog.get_by_text("Move billing to the cloud")).to_be_visible()
    dialog.get_by_text("Retire the legacy billing system").click()
    expect(dialog.get_by_text("Legacy Billing").first).to_be_visible()
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path="%s/compare-dialog-%d-%s.png" % (WALK_DIR, viewport["width"], scheme))

    dialog.get_by_role("button", name="Close scenario comparison dialog").click()
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()
    ctx.close()
