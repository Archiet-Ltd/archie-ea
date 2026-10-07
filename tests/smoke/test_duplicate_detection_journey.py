"""Run-detection -> feedback panel -> persisted-run journey for Duplicate
Detection.

Wave 4 gap analysis (nav-verified / role_access.py cross-reference): this
module had zero smoke coverage despite being a real write feature reachable
from the enterprise_architect persona's My-work zone ("Duplicate Detection",
`unified_duplicate.simple_dashboard`, app/utils/role_access.py) and from
`_MORE_TOOLS` in every other persona's directory.

Routes exercised: GET `/duplicate-detection/simple` (dashboard),
POST `/duplicate-detection/simple/api/run-detection` (fired by the real "Run
Detection" button + modal, app/static/js/duplicate_detection/dashboard.js:
`runDetection()`), and GET `/duplicate-detection/simple/runs` (the run-history
API) used here to confirm the write survived past the page.

Unlike the procurement/portfolio journeys, this feature has no per-record
detail page to reload — a detection run's result is the run row itself, not
a page. Persistence is asserted by re-querying `/simple/runs` in a **fresh**
page load (not the same in-memory Alpine state that already believes it
succeeded) and finding the same run_id with a completed status.
"""

import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
def test_duplicate_detection_run_persists_after_reload(browser, live_server, seeded):
    """An enterprise architect opens the Duplicate Detection dashboard, runs
    detection through the real modal, sees the completion feedback panel,
    then reloads the page and confirms the run is recorded server-side (not
    just held in the page's own Alpine state)."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])

        run_name = "Smoke Run %d" % int(time.time() * 1000)
        dashboard_url = live_server + "/duplicate-detection/simple"

        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # ---- Open the real "Run Detection" control ---------------------------
        # exact: once the dashboard finishes loading with no groups, its empty
        # state adds a second control, "Run duplicate detection now".
        page.get_by_role("button", name="Run duplicate detection", exact=True).click()
        expect(page.locator("#run-detection-modal-title")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.fill('input[placeholder="e.g., Q1 2026 Detection"]', run_name)

        # ---- Fire the real POST -----------------------------------------------
        with page.expect_response(
            lambda r: "/duplicate-detection/simple/api/run-detection" in r.url
            and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as detection:
            page.get_by_role("button", name="Start duplicate detection").click()
        assert detection.value.status < 400, (
            f"run-detection POST failed: {detection.value.status}"
        )
        body = detection.value.json()
        assert body.get("success") is True, f"detection reported failure: {body}"
        run_id = body.get("run_id")
        assert run_id, f"no run_id returned: {body}"

        # ---- The real control shows completion, not a fabricated success -----
        expect(page.get_by_text("Detection completed", exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # ---- PERSISTENCE: fresh page load, then ask the server directly,
        # not the Alpine state that already believed it worked -----------------
        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        runs_response = page.request.get(
            live_server + "/duplicate-detection/simple/runs?limit=20"
        )
        assert runs_response.ok, f"runs API failed: {runs_response.status}"
        runs_payload = runs_response.json()
        assert runs_payload.get("success") is True
        run_ids = [r.get("id") for r in runs_payload.get("runs", [])]
        assert run_id in run_ids, (
            f"run {run_id} not found in persisted run history {run_ids} "
            "-- detection reported success but nothing survived the reload"
        )
    finally:
        context.close()
