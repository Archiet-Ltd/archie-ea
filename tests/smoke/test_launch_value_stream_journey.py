"""A business architect models a customer onboarding value stream.

Fresh organisation, every record made through the product's own screens:
two capabilities on the capability page, a value stream and two ordered
stages (with entry/exit criteria, stakeholders and value items) on the value
stream pages, each stage mapped to a capability through the grid's own
picker and cell editor. The capability's maturity is then set through the
capability page's edit dialog, and the value stream shows it per stage and
per grid row - read from the capability record, so an unassessed capability
reads "—", never a level. Everything is asserted again after a reload, and a
second organisation can see none of it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The "Capabilities" tab of the capability map: the create/edit screen.
CAPABILITIES_PAGE = "/enterprise/capability-map/capabilities"


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1600, "height": 1000})
    # A first sign-in shows the one-time welcome tour over every page; this
    # person has already been through it.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _create_capability(page, base, name):
    page.goto(base + CAPABILITIES_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New Capability").click()
    dialog = page.locator("#cap-dialog")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#cap-name").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/enterprise/capabilities") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        dialog.locator("#cap-save").click()
    assert resp.value.status < 400, "capability create failed: %d %s" % (resp.value.status, resp.value.text()[:300])
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator(".cap-card", has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)


def _add_stage(page, name, entry, exit_, stakeholders, value_items):
    page.get_by_role("button", name="Add Stage").first.click()
    modal = page.locator("#stage-create-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#stage-name").fill(name)
    modal.locator("#stage-entry-criteria").fill(entry)
    modal.locator("#stage-exit-criteria").fill(exit_)
    modal.locator("#stage-stakeholders").fill(stakeholders)
    modal.locator("#stage-value-items").fill(value_items)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        modal.get_by_role("button", name="Add Stage").click()
    expect(page.locator("[data-stage-card]", has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)


def _save_cell(page):
    modal = page.locator("#cell-edit-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: r.url.endswith("/value-streams/api/mapping") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        modal.get_by_role("button", name="Save").click()
    assert resp.value.status < 400, "mapping save failed: %d %s" % (resp.value.status, resp.value.text()[:300])
    expect(modal).to_be_hidden(timeout=PAGE_TIMEOUT)


def _pick_capability(page, name):
    search = page.get_by_placeholder("Add capability to grid...")
    search.fill(name[:12])
    option = page.get_by_role("button", name=name)
    expect(option).to_be_visible(timeout=PAGE_TIMEOUT)
    option.click()


def _stage_card(page, name):
    return page.locator("[data-stage-card]", has_text=name)


def _grid_row(page, cap_name):
    return page.locator("table tbody tr", has_text=cap_name)


def _assert_stage_definitions(page, s1, s2, cap_a, cap_b, mat_a):
    card1 = _stage_card(page, s1)
    expect(card1.locator('[data-stage-field="entry_criteria"]')).to_have_text("Signed application received")
    expect(card1.locator('[data-stage-field="exit_criteria"]')).to_have_text("Application complete and accepted")
    expect(card1.locator('[data-stage-field="stakeholders"] li')).to_have_text(["Customer", "Onboarding clerk"])
    expect(card1.locator('[data-stage-field="value_items"] li')).to_have_text(["Accepted application"])
    expect(card1.locator('[data-stage-field="capabilities"] li', has_text=cap_a).locator("[data-maturity]")).to_have_text(mat_a)

    card2 = _stage_card(page, s2)
    expect(card2.locator('[data-stage-field="entry_criteria"]')).to_have_text("Application accepted")
    expect(card2.locator('[data-stage-field="stakeholders"] li')).to_have_text(["Compliance officer"])
    # Nothing recorded for this stage's value items: an em dash, not an empty list or a 0.
    expect(card2.locator('[data-stage-field="value_items"]')).to_have_text("—")
    expect(card2.locator('[data-stage-field="capabilities"] li', has_text=cap_b).locator("[data-maturity]")).to_have_text("—")

    # Stage order is the order the stages were added.
    names = page.locator("[data-stage-card] h4").all_inner_texts()
    assert names.index(s1) < names.index(s2), names

    expect(_grid_row(page, cap_a).locator("td[data-maturity]")).to_have_text(mat_a)
    expect(_grid_row(page, cap_b).locator("td[data-maturity]")).to_have_text("—")


def test_business_architect_models_onboarding_value_stream(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    cap_a = "Application Intake %s" % tag
    cap_b = "Identity Verification %s" % tag
    vs_name = "Customer Onboarding %s" % tag
    s1, s2 = "Capture application", "Verify identity"

    org1 = create_fresh_org("business_architect")
    org2 = create_fresh_org("business_architect")

    context, page = _new_page(browser)
    sign_in(page, base, org1["emails"]["business_architect"])

    _create_capability(page, base, cap_a)
    _create_capability(page, base, cap_b)

    page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New Value Stream").first.click()
    create_modal = page.locator("#vs-create-modal")
    expect(create_modal).to_be_visible(timeout=PAGE_TIMEOUT)
    create_modal.locator("#vs-name").fill(vs_name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        create_modal.get_by_role("button", name="Create").click()
    assert "/value-streams/" in page.url and page.url.rstrip("/").split("/")[-1].isdigit(), page.url
    vs_id = int(page.url.rstrip("/").split("/")[-1])
    detail_url = page.url

    _add_stage(page, s1, "Signed application received", "Application complete and accepted",
               "Customer\nOnboarding clerk", "Accepted application")
    _add_stage(page, s2, "Application accepted", "Identity confirmed", "Compliance officer", "")

    # Map capability A to stage 1 through the grid's own picker and cell editor.
    _pick_capability(page, cap_a)
    _save_cell(page)
    # Capability B: picked, then mapped to stage 2 by clicking that cell.
    _pick_capability(page, cap_b)
    page.locator("#cell-edit-modal").get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Set support for %s at %s" % (cap_b, s2)).click()
    _save_cell(page)

    # Neither capability has been assessed yet.
    expect(_grid_row(page, cap_a).locator("td[data-maturity]")).to_have_text("—")

    # Set capability A's maturity on the capability page's own edit dialog.
    page.goto(base + CAPABILITIES_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Edit %s" % cap_a).click()
    dialog = page.locator("#cap-dialog")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#cap-maturity").select_option("2")
    dialog.locator("#cap-target-maturity").select_option("4")
    with page.expect_response(
        lambda r: "/enterprise/capabilities/" in r.url and r.request.method == "PUT",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        dialog.locator("#cap-save").click()
    assert resp.value.status < 400, "capability edit failed: %d %s" % (resp.value.status, resp.value.text()[:300])

    page.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _assert_stage_definitions(page, s1, s2, cap_a, cap_b, "L2 → L4")

    # Record stage 2's value item through the stage edit form.
    _stage_card(page, s2).get_by_role("button", name="Edit stage").click()
    edit_modal = page.locator("#stage-edit-modal")
    expect(edit_modal).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(edit_modal.locator("#stage-edit-entry-criteria")).to_have_value("Application accepted")
    edit_modal.locator("#stage-edit-value-items").fill("Verified customer identity")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        edit_modal.get_by_role("button", name="Save").click()

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_stage_card(page, s2).locator('[data-stage-field="value_items"] li')).to_have_text(["Verified customer identity"])
    expect(_stage_card(page, s2).locator('[data-stage-field="exit_criteria"]')).to_have_text("Identity confirmed")
    expect(_stage_card(page, s1).locator('[data-stage-field="stakeholders"] li')).to_have_text(["Customer", "Onboarding clerk"])
    expect(_grid_row(page, cap_a).locator("td[data-maturity]")).to_have_text("L2 → L4")
    expect(_grid_row(page, cap_b).locator("td[data-maturity]")).to_have_text("—")
    context.close()

    # A second organisation sees none of it.
    context2, page2 = _new_page(browser)
    sign_in(page2, base, org2["emails"]["business_architect"])
    page2.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text(vs_name)
    status, _ = api(page2, "GET", "/value-streams/%d/grid" % vs_id)
    assert status == 404, "other org read the value stream grid: %s" % status
    resp = page2.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert resp.status == 404, "other org opened the value stream: %s" % resp.status
    page2.goto(base + CAPABILITIES_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text(cap_a)
    context2.close()
