"""Create -> document -> field-save -> delete journey for Business Case.

Wave 1 (measurement) found the Business Case module (app/modules/business_case)
had ZERO smoke coverage despite being a named, core surface -- unlike most
modules it also has no sidebar entry yet (routes.py's own docstring: "linked
from the sidebar by the orchestrator post-merge" -- that merge has not landed),
so a `business_architect` reaches it only by a direct URL. That is a real
information-architecture gap (reported separately), not a reason to skip
proving the CRUD works: the route is live, requires only login, and is the
kind of screen a solution owner is handed a link to.

Follows the tests/smoke/test_roadmap_crud_journey.py pattern: real login (so
CSRF flows exactly as the browser sends it), `expect_response` on the actual
network call for each verb, and a page reload before the final assertion so
we are proving server-side persistence, not client DOM state.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
FIELD_API = re.compile(r"/business-case/\d+/api/field")
UPDATE_API = re.compile(r"/business-case/\d+/update")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
def test_business_case_create_document_and_delete(browser, live_server, seeded):
    """A business_architect creates a Business Case, fills in the document,
    confirms the field survived a reload, then deletes it and confirms the
    row is gone from the server's own list on a fresh load."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])

        # ---- CREATE (POST /business-case/create) ---------------------------
        index_url = live_server + "/business-case/"
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("New Business Case", re.I)).first.click()
        form = page.locator('[data-testid="bc-create-form"]')
        expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
        form.locator("#bc-title").fill("Smoke Business Case")
        form.locator("#bc-description").fill("Created by the smoke journey.")
        with page.expect_response(
            lambda r: "/business-case/create" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.get_by_test_id("bc-create-submit").click()
        assert created.value.status < 400, f"create POST failed: {created.value.status}"
        page.wait_for_url(re.compile(r"/business-case/\d+$"), timeout=PAGE_TIMEOUT)
        # The case opens as a document; its canvas boards are one click on.
        page.get_by_role("link", name="Canvas boards").click()
        page.wait_for_url(re.compile(r"/business-case/\d+/canvas$"), timeout=PAGE_TIMEOUT)
        detail_url = page.url

        # ---- DOCUMENT FIELD SAVE (POST/PUT .../api/field, inline @blur) ----
        problem = page.get_by_test_id("bc-textarea-problem_statement")
        expect(problem).to_be_visible(timeout=PAGE_TIMEOUT)
        problem.fill("Regional CRM instances have diverged and cost too much to run separately.")
        with page.expect_response(
            lambda r: bool(FIELD_API.search(r.url)),
            timeout=PAGE_TIMEOUT,
        ) as field_saved:
            problem.blur()
        assert field_saved.value.status < 400, f"field save failed: {field_saved.value.status}"

        # ---- STATUS SAVE (inline select, POST/PUT .../update) --------------
        status_select = page.get_by_test_id("bc-status-select")
        with page.expect_response(
            lambda r: bool(UPDATE_API.search(r.url)),
            timeout=PAGE_TIMEOUT,
        ) as status_saved:
            status_select.select_option("submitted")
        assert status_saved.value.status < 400, f"status save failed: {status_saved.value.status}"

        # ---- PERSISTENCE (reload -- both writes must survive a fresh GET) --
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("bc-textarea-problem_statement")).to_have_value(
            re.compile("Regional CRM instances"), timeout=PAGE_TIMEOUT
        )
        expect(page.get_by_test_id("bc-status-select")).to_have_value(
            "submitted", timeout=PAGE_TIMEOUT
        )

        # Row also carries the status on the index list, from the same store.
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Smoke Business Case", exact=False)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )

        # ---- DELETE (POST .../delete) ---------------------------------------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("^Delete$", re.I)).click()
        delete_form = page.locator('[data-testid="bc-delete-form"]')
        expect(delete_form).to_be_visible(timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/delete" in r.url and "/business-case/" in r.url
            and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as deleted:
            page.get_by_test_id("bc-delete-submit").click()
        assert deleted.value.status < 400, f"delete failed: {deleted.value.status}"

        # ---- PERSISTENCE (reload the index -- row is really gone) -----------
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Smoke Business Case", exact=False)).to_have_count(
            0, timeout=PAGE_TIMEOUT
        )
    finally:
        context.close()
