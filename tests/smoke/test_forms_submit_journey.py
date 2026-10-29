"""Three forms on the startup path must submit when a person clicks their
primary button: the model import, the decision form and the value stream
detail page (create, add a stage, edit a stage).

The button macro renders type="button" unless told otherwise, so a form can
look finished and post nothing. Each journey fills the form, clicks the
primary button and asserts what was stored, after a reload.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "oef", "archiet_shaped.xml"
)


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    # The one-time welcome tour has already been seen by this person.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def test_import_model_button_posts_the_file_and_shows_the_result(browser, live_server):
    org = create_fresh_org("solution_architect")
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/import/oef", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.set_input_files("#oef_file", FIXTURE_PATH)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.click("[data-testid='btn-import-model']")
        result = page.locator("[data-testid='import-result']")
        expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
        created = int(page.locator("[data-testid='result-elements-created']").first.inner_text().strip())
        assert created == 15, result.inner_text()
    finally:
        context.close()


def test_decision_form_create_decision_button_stores_the_decision(browser, live_server):
    org = create_fresh_org("solution_architect")
    title = "Adopt event streaming %s" % uuid.uuid4().hex[:6]
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/decisions/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#title", title)
        page.fill("#decision", "We will stream order events through one broker.")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_role("button", name="Create Decision").click()
        assert "/architecture/decisions/" in page.url and not page.url.rstrip("/").endswith("/new"), page.url
        expect(page.locator("body")).to_contain_text(title)
        page.goto(live_server + "/architecture/decisions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(title)
    finally:
        context.close()


def test_value_stream_detail_forms_store_a_stage_and_its_edit(browser, live_server):
    org = create_fresh_org("business_architect")
    tag = uuid.uuid4().hex[:6]
    vs_name = "Order to Cash %s" % tag
    stage = "Take order %s" % tag
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])
        page.goto(live_server + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="New Value Stream").first.click()
        create_modal = page.locator("#vs-create-modal")
        expect(create_modal).to_be_visible(timeout=PAGE_TIMEOUT)
        create_modal.locator("#vs-name").fill(vs_name)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            create_modal.get_by_role("button", name="Create").click()
        detail_url = page.url
        assert detail_url.rstrip("/").split("/")[-1].isdigit(), detail_url

        page.get_by_role("button", name="Add Stage").first.click()
        modal = page.locator("#stage-create-modal")
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        modal.locator("#stage-name").fill(stage)
        modal.locator("#stage-entry-criteria").fill("Purchase order received")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            modal.get_by_role("button", name="Add Stage").click()
        card = page.locator("[data-stage-card]", has_text=stage)
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)

        card.get_by_role("button", name="Edit stage").click()
        edit_modal = page.locator("#stage-edit-modal")
        expect(edit_modal).to_be_visible(timeout=PAGE_TIMEOUT)
        edit_modal.locator("#stage-edit-exit-criteria").fill("Order confirmed to the customer")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            edit_modal.get_by_role("button", name="Save").click()

        page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.locator("[data-stage-card]", has_text=stage)
        expect(card.locator('[data-stage-field="entry_criteria"]')).to_have_text("Purchase order received")
        expect(card.locator('[data-stage-field="exit_criteria"]')).to_have_text("Order confirmed to the customer")
    finally:
        context.close()
