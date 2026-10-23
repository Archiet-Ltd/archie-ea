"""A business architect records stage times, frequency and event links on a value stream.

Seeded stream of four stages. The user:
1. records times and a frequency, then links a "month end" event as the trigger of stage 1;
2. asks "how long end to end", and sees the cited sum with one stage shown as not recorded;
3. sees the slowest stage flagged, with its capability;
4. asks what stops if "month end" does not fire, and sees the downstream stages.

A second organisation sees nothing.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CAPABILITIES_PAGE = "/enterprise/capability-map/capabilities"


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1600, "height": 1000})
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


def _add_stage(page, name, duration=None, unit=None, freq_count=None, freq_period=None):
    page.get_by_role("button", name="Add Stage").first.click()
    modal = page.locator("#stage-create-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.locator("#stage-name").fill(name)
    if duration is not None:
        modal.locator("#stage-target-duration").fill(str(duration))
    if unit:
        modal.locator("#stage-duration-unit").select_option(unit)
    if freq_count is not None:
        modal.locator("#stage-frequency-count").fill(str(freq_count))
    if freq_period:
        modal.locator("#stage-frequency-period").select_option(freq_period)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        modal.get_by_role("button", name="Add Stage").click()
    expect(page.locator("[data-stage-card]", has_text=name)).to_be_visible(timeout=PAGE_TIMEOUT)


def _stage_card(page, name):
    return page.locator("[data-stage-card]", has_text=name)


def test_value_stream_stage_time_journey(browser, live_server):
    base = live_server
    tag = uuid.uuid4().hex[:6]
    cap_a = "Order Capture %s" % tag
    cap_b = "Inventory Check %s" % tag
    vs_name = "Order Fulfilment %s" % tag

    org1 = create_fresh_org("business_architect")
    org2 = create_fresh_org("business_architect")

    context, page = _new_page(browser)
    sign_in(page, base, org1["emails"]["business_architect"])

    # Create two capabilities
    _create_capability(page, base, cap_a)
    _create_capability(page, base, cap_b)

    # Create the value stream
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

    # Add four stages with times and frequencies
    # Stage 1: Intake - 2 hours, 10 per day
    _add_stage(page, "Intake", duration=2, unit="hours", freq_count=10, freq_period="per_day")
    # Stage 2: Validate - 30 minutes, no frequency
    _add_stage(page, "Validate", duration=30, unit="minutes")
    # Stage 3: Process - 1 day, 5 per day (this will be the slowest)
    _add_stage(page, "Process", duration=1, unit="days", freq_count=5, freq_period="per_day")
    # Stage 4: Deliver - no duration (not recorded), 2 per day
    _add_stage(page, "Deliver", freq_count=2, freq_period="per_day")

    # Map capability A to stage 1 (Intake)
    page.get_by_placeholder("Add capability to grid...").fill(cap_a[:12])
    option = page.get_by_role("button", name=cap_a)
    expect(option).to_be_visible(timeout=PAGE_TIMEOUT)
    option.click()
    cell_modal = page.locator("#cell-edit-modal")
    expect(cell_modal).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: r.url.endswith("/value-streams/api/mapping") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        cell_modal.get_by_role("button", name="Save").click()
    assert resp.value.status < 400, "mapping save failed: %d %s" % (resp.value.status, resp.value.text()[:300])

    # Map capability B to stage 3 (Process)
    page.get_by_placeholder("Add capability to grid...").fill(cap_b[:12])
    option2 = page.get_by_role("button", name=cap_b)
    expect(option2).to_be_visible(timeout=PAGE_TIMEOUT)
    option2.click()
    page.locator("#cell-edit-modal").get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Set support for %s at %s" % (cap_b, "Process")).click()
    cell_modal2 = page.locator("#cell-edit-modal")
    expect(cell_modal2).to_be_visible(timeout=PAGE_TIMEOUT)
    with page.expect_response(
        lambda r: r.url.endswith("/value-streams/api/mapping") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp:
        cell_modal2.get_by_role("button", name="Save").click()
    assert resp.value.status < 400, "mapping save failed: %d %s" % (resp.value.status, resp.value.text()[:300])

    # Reload and verify stage cards show duration and frequency
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Stage 1: Intake - should show duration and frequency
    card1 = _stage_card(page, "Intake")
    expect(card1).to_contain_text("2 / 2 hours")
    expect(card1).to_contain_text("10 per day")

    # Stage 3: Process - should show duration (slowest)
    card3 = _stage_card(page, "Process")
    expect(card3).to_contain_text("1 / 1 days")
    expect(card3).to_contain_text("5 per day")

    # Stage 4: Deliver - should show frequency but no duration
    card4 = _stage_card(page, "Deliver")
    expect(card4).to_contain_text("2 per day")

    # Check the Stage Time Analysis section
    # End-to-end time: 2 hours (120 min) + 30 min + 1 day (1440 min) = 1590 min
    analysis_section = page.locator("text=Stage Time Analysis")
    expect(analysis_section).to_be_visible(timeout=PAGE_TIMEOUT)

    # The end-to-end time should be shown
    expect(page.locator("text=End-to-End Time")).to_be_visible()
    expect(page.locator("text=1590 minutes")).to_be_visible()

    # The slowest stage should be Process (1 day = 1440 min)
    expect(page.locator("text=Slowest Stage")).to_be_visible()
    expect(page.locator("text=Process")).to_be_visible()
    expect(page.locator("text=1 days")).to_be_visible()

    # Deliver should be listed as not recorded
    expect(page.locator("text=not recorded")).to_be_visible()

    # Check downstream impact: select Intake and see downstream stages
    downstream_select = page.locator("select").last
    downstream_select.select_option("Intake")
    page.wait_for_timeout(500)

    # Should show Validate, Process, Deliver as downstream
    expect(page.locator("text=Downstream stages:")).to_be_visible()
    expect(page.locator("text=Validate")).to_be_visible()
    expect(page.locator("text=Process")).to_be_visible()
    expect(page.locator("text=Deliver")).to_be_visible()

    context.close()

    # A second organisation sees nothing
    context2, page2 = _new_page(browser)
    sign_in(page2, base, org2["emails"]["business_architect"])
    page2.goto(base + "/value-streams/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page2.locator("body")).not_to_contain_text(vs_name)
    status, _ = api(page2, "GET", "/value-streams/%d/grid" % vs_id)
    assert status == 404, "other org read the value stream grid: %s" % status
    resp = page2.goto(detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert resp.status == 404, "other org opened the value stream: %s" % resp.status
    context2.close()