"""Create -> block-save -> operating-model save -> delete journey for the
Business Model Canvas module.

Wave 1 (measurement) found this module (app/modules/business_model_canvas) had
ZERO smoke coverage. Same information-architecture gap as Business Case: it
has no sidebar entry, so a `business_architect` reaches it only by a direct
URL -- reported separately, not a reason to skip proving the CRUD works.

While instrumenting this journey, delete_canvas required "admin" only, while
create/update/save_block accept admin/architect/business_architect -- the
exact DEF-075 bug already fixed once on business_case.delete. Fixed in
app/modules/business_model_canvas/routes.py alongside this test so the
journey below (which deletes as `business_architect`) is real coverage of the
fix, not proof it still needs making.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
BLOCK_API = re.compile(r"/business-model/\d+/api/block")
UPDATE_API = re.compile(r"/business-model/\d+/update")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
def test_business_model_canvas_create_edit_and_delete(browser, live_server, seeded):
    """A business_architect creates a canvas, fills in a block and the
    operating-model archetype, confirms both survived a reload, then deletes
    the canvas (proving the business_architect role can, per the fix above)
    and confirms it is gone from the server's own list on a fresh load."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])

        # ---- CREATE (POST /business-model/create) --------------------------
        index_url = live_server + "/business-model/"
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("New Canvas", re.I)).first.click()
        form = page.locator('[data-testid="bmc-create-form"]')
        expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
        form.locator("#bmc-name").fill("Smoke Canvas")
        form.locator("#bmc-description").fill("Created by the smoke journey.")
        with page.expect_response(
            lambda r: "/business-model/create" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.get_by_test_id("bmc-create-submit").click()
        assert created.value.status < 400, f"create POST failed: {created.value.status}"
        page.wait_for_url(re.compile(r"/business-model/\d+$"), timeout=PAGE_TIMEOUT)
        detail_url = page.url

        # ---- BLOCK SAVE (POST/PUT .../api/block, inline @blur) -------------
        vp_block = page.get_by_test_id("bmc-textarea-value_propositions")
        expect(vp_block).to_be_visible(timeout=PAGE_TIMEOUT)
        vp_block.fill("Single pane of glass for enterprise architecture.")
        with page.expect_response(
            lambda r: bool(BLOCK_API.search(r.url)),
            timeout=PAGE_TIMEOUT,
        ) as block_saved:
            vp_block.blur()
        assert block_saved.value.status < 400, f"block save failed: {block_saved.value.status}"

        # ---- OPERATING MODEL SAVE (select, POST/PUT .../update) ------------
        select = page.get_by_test_id("bmc-operating-model-select")
        options = select.locator("option").all_inner_texts()
        chosen = next(o for o in options if o.strip() and o.strip() != "Not set")
        with page.expect_response(
            lambda r: bool(UPDATE_API.search(r.url)),
            timeout=PAGE_TIMEOUT,
        ) as model_saved:
            select.select_option(label=chosen)
        assert model_saved.value.status < 400, f"operating model save failed: {model_saved.value.status}"

        # ---- PERSISTENCE (reload -- both writes must survive a fresh GET) --
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("bmc-textarea-value_propositions")).to_have_value(
            re.compile("Single pane of glass"), timeout=PAGE_TIMEOUT
        )
        expect(page.get_by_test_id("bmc-operating-model-select")).to_have_value(
            re.compile(r".+"), timeout=PAGE_TIMEOUT
        )

        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Smoke Canvas", exact=False)).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- DELETE (POST .../delete) as business_architect -----------------
        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("Delete Canvas", re.I)).click()
        delete_form = page.locator('[data-testid="bmc-delete-form"]')
        expect(delete_form).to_be_visible(timeout=PAGE_TIMEOUT)
        with page.expect_response(
            lambda r: "/delete" in r.url and "/business-model/" in r.url
            and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as deleted:
            page.get_by_test_id("bmc-delete-submit").click()
        assert deleted.value.status < 400, f"delete failed: {deleted.value.status}"

        # ---- PERSISTENCE (reload the index -- canvas is really gone) --------
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Smoke Canvas", exact=False)).to_have_count(0, timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_business_model_canvas_grid_aligns_with_the_page_title(browser, live_server, seeded):
    """The grid of canvas cards must start at the same left edge as the page
    title above it -- an extra padding wrapper around the grid alone used to
    push every card in further than the title, by about 24px."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])

        index_url = live_server + "/business-model/"
        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name=re.compile("New Canvas", re.I)).first.click()
        form = page.locator('[data-testid="bmc-create-form"]')
        expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
        form.locator("#bmc-name").fill("Alignment Smoke Canvas")
        with page.expect_response(
            lambda r: "/business-model/create" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as created:
            page.get_by_test_id("bmc-create-submit").click()
        assert created.value.status < 400, f"create POST failed: {created.value.status}"

        page.goto(index_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        grid = page.locator('[data-testid="bmc-canvas-grid"]')
        expect(grid).to_be_visible(timeout=PAGE_TIMEOUT)

        title_left = page.locator("h1").first.evaluate("el => el.getBoundingClientRect().left")
        grid_left = grid.evaluate("el => el.getBoundingClientRect().left")
        assert abs(title_left - grid_left) <= 1, (
            f"canvas grid left edge ({grid_left}) does not match the page "
            f"title's left edge ({title_left})"
        )
    finally:
        context.close()
