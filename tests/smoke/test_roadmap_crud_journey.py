"""Deterministic create → edit → delete journey for an application roadmap work
package.

Why this exists: an ad-hoc Playwright pass proved the CREATE path (a 200 POST that
persisted) but could not deterministically prove EDIT and DELETE — the modal-timing
in a throwaway script flaked, and a raw test-client POST hit CSRF. This test closes
that gap with the smoke harness's real login (so the session carries CSRF exactly
as the browser does) and ``expect_response`` assertions on the actual network calls
— so a regression in any of the three verbs fails loudly rather than silently.

Two harness realities shape the navigation, both established by watching the real
page and both matching the rest of ``tests/smoke``:

* The roadmap page **polls** (notifications, scope) after first paint, so the
  network never goes idle — ``wait_until="networkidle"`` would hang until timeout.
  We wait on ``domcontentloaded`` and then on the specific control being visible.
* For the first few seconds the layout shifts as capability/plateau data streams
  in, so a control can be visible-but-not-yet-stable. ``.click()`` auto-waits for
  stability; an explicit ``expect(...).to_be_visible()`` before each click makes
  the settle deterministic rather than a fixed sleep.

Per "Done means DEMONSTRATED": the final assertion reloads the page and confirms the
row is really gone from the server's response, not just from the client DOM.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
MODAL = "#roadmap-work-package-modal"
NAME_INPUT = f"{MODAL} input[type=text]"
WP_API = re.compile(r"/api/applications/\d+/work-packages")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open_roadmap(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # the page streams data in after paint; wait for the primary control to settle
    expect(
        page.get_by_role("button", name=re.compile("Add Work Package", re.I)).first
    ).to_be_visible(timeout=PAGE_TIMEOUT)


def _fill_required(page, name):
    """Fill the modal's required fields (name/type/start/target) so submitForm's
    client-side validation passes. fill/select_option fire the input/change events
    Alpine's x-model listens for."""
    modal = page.locator(MODAL)
    modal.locator("input[type=text]").first.fill(name)
    modal.locator("select").first.select_option(index=1)
    dates = modal.locator("input[type=date]")
    dates.nth(0).fill("2026-10-01")
    dates.nth(1).fill("2027-06-01")


@pytest.mark.smoke
def test_roadmap_work_package_create_edit_delete(browser, live_server, seeded):
    """Full CRUD cycle on a roadmap work package, asserting each HTTP verb and a
    post-reload persistence check."""
    app_id = seeded["ids"]["application"]
    roadmap_url = live_server + f"/applications/{app_id}/roadmap"
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())  # native confirm(), if the delete uses one
    try:
        _login(page, live_server, seeded["emails"]["application_manager"])
        _open_roadmap(page, roadmap_url)

        # ---- CREATE (POST) ------------------------------------------------
        page.get_by_role("button", name=re.compile("Add Work Package", re.I)).first.click()
        expect(page.locator(NAME_INPUT).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _fill_required(page, "Smoke CRUD WP")
        with page.expect_response(
            lambda r: bool(WP_API.search(r.url)) and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.get_by_role("button", name=re.compile("Create Work Package", re.I)).click()
        assert created.value.status < 400, f"create POST failed: {created.value.status}"
        expect(page.get_by_text("Smoke CRUD WP", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- EDIT (PUT) ---------------------------------------------------
        # the saved page reloads on the timeline; the rows are on the List tab
        page.get_by_role("tab", name="List").click()
        row = page.locator("tr", has_text="Smoke CRUD WP")
        row.get_by_role("button", name=re.compile("^Edit$", re.I)).click()
        name = page.locator(NAME_INPUT).first
        expect(name).to_have_value(re.compile("Smoke CRUD WP"), timeout=PAGE_TIMEOUT)
        name.fill("Smoke CRUD WP EDITED")
        with page.expect_response(
            lambda r: bool(WP_API.search(r.url))
            and r.request.method in ("PUT", "PATCH", "POST"),
            timeout=PAGE_TIMEOUT,
        ) as edited:
            page.get_by_role("button", name=re.compile("Save Changes", re.I)).click()
        assert edited.value.status < 400, f"edit failed: {edited.value.status}"
        expect(page.get_by_text("Smoke CRUD WP EDITED", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- DELETE (DELETE) ----------------------------------------------
        page.get_by_role("tab", name="List").click()
        row = page.locator("tr", has_text="Smoke CRUD WP EDITED")
        with page.expect_response(
            lambda r: bool(WP_API.search(r.url)) and r.request.method == "DELETE",
            timeout=PAGE_TIMEOUT,
        ) as deleted:
            row.get_by_role("button", name=re.compile("^Delete$", re.I)).click()
            # an in-page confirm modal, if present
            confirm = page.get_by_role("button", name=re.compile("^(Delete|Confirm|Yes)$", re.I))
            try:
                if confirm.count() and confirm.last.is_visible():
                    confirm.last.click()
            except Exception:
                pass
        assert deleted.value.status < 400, f"delete failed: {deleted.value.status}"

        # ---- PERSISTENCE (reload — the row is gone from the SERVER too) ----
        _open_roadmap(page, roadmap_url)
        expect(page.get_by_text("Smoke CRUD WP EDITED", exact=False)).to_have_count(0, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
