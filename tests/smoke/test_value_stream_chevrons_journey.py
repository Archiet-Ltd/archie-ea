"""A business architect reads a value stream at a glance.

Fresh organisation, records made through the product's own screens. The
stages read as a row of chevrons; a stage field nobody filled in says so and
offers the way to fill it; the cycle time and quality tiles of a stream with
none recorded explain themselves; each capability named on a stage opens that
capability, and the health figure beside it is the one the capability health
screen reports for the same capability.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .test_launch_value_stream_journey import (
    CAPABILITIES_PAGE,
    _create_capability,
    _new_page,
    _pick_capability,
    _save_cell,
    _stage_card,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_business_architect_reads_stages_as_chevrons_with_linked_capabilities(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    cap = "Order Fulfilment %s" % tag
    vs_name = "Order to Cash %s" % tag
    stage = "Pick and pack"

    org = create_fresh_org("business_architect")
    context, page = _new_page(browser)
    sign_in(page, base, org["emails"]["business_architect"])
    _create_capability(page, base, cap)

    page.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New Value Stream").first.click()
    modal = page.locator("#vs-create-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#vs-name").fill(vs_name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        modal.get_by_role("button", name="Create").click()

    # A stream with nothing recorded explains each empty tile and offers the next step.
    expect(page.locator('[data-vs-empty="cycle-time"]')).to_contain_text("No cycle time recorded yet")
    expect(page.locator('[data-vs-empty="quality"]')).to_contain_text("No quality figure recorded yet")
    page.get_by_role("button", name="Record cycle time").click()
    expect(page.locator("#vs-edit-modal")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.locator("#vs-edit-modal").get_by_role("button", name="Cancel").click()

    # One stage with only a name.
    page.get_by_role("button", name="Add Stage").first.click()
    create = page.locator("#stage-create-modal")
    expect(create).to_be_visible(timeout=PAGE_TIMEOUT)
    create.locator("#stage-name").fill(stage)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        create.get_by_role("button", name="Add Stage").click()
    detail_url = page.url

    # The stage is a chevron: its header is clipped to a point, not a plain box.
    chevron = _stage_card(page, stage).locator("[data-stage-chevron]")
    expect(chevron).to_be_visible(timeout=PAGE_TIMEOUT)
    assert chevron.evaluate("el => getComputedStyle(el).clipPath") not in ("none", ""), "stage header is a plain box"

    # Every field nobody filled in says why, and the offered action opens the stage editor.
    card = _stage_card(page, stage)
    for field, words in (("entry_criteria", "No entry criteria yet"), ("exit_criteria", "No exit criteria yet"),
                         ("stakeholders", "No stakeholders named yet"), ("value_items", "No value items yet")):
        expect(card.locator('[data-stage-field="%s"]' % field)).to_contain_text(words)
        assert card.locator('[data-stage-field="%s"]' % field).inner_text().strip() != "—"
    card.locator('[data-stage-field="entry_criteria"]').get_by_role("button", name="Add it").click()
    editor = page.locator("#stage-edit-modal")
    expect(editor).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(editor.locator("#stage-edit-name")).to_have_value(stage)
    editor.get_by_role("button", name="Cancel").first.click()

    # Map the capability to the stage through the grid.
    _pick_capability(page, cap)
    _save_cell(page)

    # The capability on the stage is a link; its health is what the health screen reports.
    row = _stage_card(page, stage).locator('[data-stage-field="capabilities"] li', has_text=cap)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    link = row.get_by_role("link", name=cap)
    expect(link).to_be_visible()
    status, metrics = api(page, "GET", "/strategic/api/capability-health")
    assert status == 200, metrics
    scores = [h["score"] for h in metrics["health_by_capability"] if h["name"] == cap]
    assert len(scores) == 1, "the health screen does not list %s" % cap
    expected = "—" if scores[0] is None else "%s%%" % scores[0]
    expect(row.locator("[data-health]")).to_have_text(expected)
    expect(page.locator("table tbody tr", has_text=cap).locator("td[data-health]")).to_have_text(expected)

    # The link lands on that capability's own page, with content.
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/fact-sheet" in page.url, page.url
    expect(page.locator("body")).to_contain_text(cap)
    context.close()

    # Narrow screen: the chevron row keeps its shape and scrolls sideways.
    context2 = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 800})
    context2.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page2 = context2.new_page()
    sign_in(page2, base, org["emails"]["business_architect"])
    page2.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    strip = page2.locator("[data-stage-chevrons]")
    expect(strip).to_be_visible(timeout=PAGE_TIMEOUT)
    assert page2.locator("[data-stage-chevron]").first.evaluate("el => getComputedStyle(el).clipPath") != "none"
    assert strip.evaluate("el => getComputedStyle(el).overflowX") in ("auto", "scroll")
    context2.close()
