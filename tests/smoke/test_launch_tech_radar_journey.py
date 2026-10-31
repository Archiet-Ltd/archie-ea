"""Technology radar and standards, from an empty organisation.

A technology architect places a new message broker on the "assess" ring with a
recorded rationale, a review date and the initiative that asked for it, and is
shown the other technology of the same kind already modelled. A CTO publishes
a technology standard for a domain with its radar ring and sunset date. Every
record is created through the product's own screens and read back after a
reload; a second organisation sees none of it.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    context = browser.new_context()
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    return context, context.new_page()


def _create_technology_element(page, base, name, element_type="SystemSoftware"):
    """Create a Technology-layer element with the element browser's Create dialog."""
    page.goto(base + "/architecture/dashboard?layer=technology", wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.get_by_test_id("btn-create-element").click()
    modal = page.locator("#archimate-form-modal")
    expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
    modal.get_by_label("Element Type").select_option(element_type)
    modal.get_by_label("Element name").fill(name)
    with page.expect_response(
        lambda r: r.url.endswith("/architecture/technology/%s/new" % element_type)
        and r.request.method == "POST"
    ) as created:
        modal.get_by_role("button", name="Create").click()
    assert created.value.status == 200, created.value.text()
    assert created.value.json()["success"] is True


def _create_programme(page, base, name):
    """Create a transformation programme with the new-programme wizard."""
    page.goto(base + "/solutions/new-programme", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    advance = page.get_by_test_id("wizard-next")

    def _advance(from_step, to_step):
        page.get_by_test_id("step-%s" % from_step).wait_for(state="visible", timeout=PAGE_TIMEOUT)
        advance.click()
        page.get_by_test_id("step-%s" % to_step).wait_for(state="visible", timeout=PAGE_TIMEOUT)

    page.locator('input[name="name"]').fill(name)
    page.locator('textarea[name="objective"]').fill("Move integration onto one supported broker")
    _advance("intent", "ownership")
    owner = page.get_by_role("combobox", name="Programme owner")
    owner.fill("Launch")
    page.wait_for_selector('#owner-results [role="option"]', timeout=PAGE_TIMEOUT)
    owner.press("ArrowDown")
    owner.press("Enter")
    page.locator('input[name="target_date"]').fill("2027-12-31")
    _advance("ownership", "workstream")
    page.locator('select[name="workstream_type"]').select_option("process")
    page.locator('input[name="scope_expression"]').fill("Integration")
    _advance("workstream", "outcome")
    page.locator('input[name="outcome"]').fill("One supported broker")
    _advance("outcome", "measure")
    page.locator('input[name="metric_name"]').fill("Brokers in use")
    page.locator('input[name="unit"]').fill("count")
    page.locator('input[name="unavailable_reason"]').fill("Inventory in progress")
    page.locator('input[name="target_value"]').fill("1")
    _advance("measure", "review")
    with page.expect_response(lambda r: r.url.endswith("/solutions/create-programme")) as done:
        page.get_by_test_id("wizard-submit").click()
    assert done.value.status < 400, done.value.text()
    page.wait_for_url("**/workstreams/*/objective", timeout=PAGE_TIMEOUT)


def _row(page, name):
    return page.locator("tr[data-row]").filter(has=page.locator("[data-element-name='%s']" % name))


def _element_id(page, name):
    """The radar row for *name*: its place link carries the element id."""
    link = _row(page, name).locator("[data-testid^='radar-place-']").first
    return link.get_attribute("data-testid").rsplit("-", 1)[1]


def _open_row_menu(page, name):
    _row(page, name).get_by_role("button", name="Actions for %s" % name).click()


def _open_place_page(page, name, element_id):
    """Radar row menu, then the link to the page that places the technology."""
    _open_row_menu(page, name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_test_id("radar-place-%s" % element_id).click()
    expect(page.get_by_test_id("radar-classify-form-%s" % element_id)).to_be_visible(timeout=PAGE_TIMEOUT)


def _move_to(page, name, element_id, ring):
    """Row menu item that moves the technology to *ring*; lands back on the radar."""
    _open_row_menu(page, name)
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        page.get_by_test_id("radar-move-%s-%s" % (element_id, ring)).click()


def _assert_assess_entry(page, name, element_id, rationale, programme, existing):
    expect(page.get_by_test_id("radar-ring-%s" % element_id)).to_have_text("Assess", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("radar-rationale-%s" % element_id)).to_have_text(rationale)
    expect(page.get_by_test_id("radar-review-date-%s" % element_id)).to_have_text("31 Mar 2027")
    expect(page.get_by_test_id("radar-initiative-%s" % element_id)).to_have_text(programme)
    # The place page shows the other technology of the same kind.
    _open_place_page(page, name, element_id)
    same = page.get_by_test_id("radar-same-type-%s" % element_id)
    expect(same).to_contain_text("Other system software elements and where they sit")
    expect(same).to_contain_text(existing)
    page.goto(page.url.rsplit("/", 2)[0] + "/", timeout=PAGE_TIMEOUT)


def test_architect_places_a_new_broker_on_assess_with_rationale_and_initiative(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    existing = "Legacy Queue %s" % tag
    broker = "Stream Broker %s" % tag
    programme = "Integration Programme %s" % tag
    rationale = "Event streaming for order flow; evaluate before committing"

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _create_programme(page, live_server, programme)
        _create_technology_element(page, live_server, existing)
        _create_technology_element(page, live_server, broker)

        assert page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT).status == 200
        broker_id = _element_id(page, broker)
        existing_id = _element_id(page, existing)
        expect(_row(page, broker).get_by_test_id("radar-ring-%s" % broker_id)).to_have_text("Not placed")

        # Before placing, the place page already shows the other broker.
        _open_place_page(page, broker, broker_id)
        expect(page.get_by_test_id("radar-same-type-%s" % broker_id)).to_contain_text(existing)

        form = page.get_by_test_id("radar-classify-form-%s" % broker_id)
        form.get_by_label("Ring").select_option("assess")
        form.get_by_label("Rationale").fill(rationale)
        form.get_by_label("Review by").fill("2027-03-31")
        picker = form.get_by_role("combobox", name="Requesting initiative")
        picker.press_sequentially("Integration Prog", delay=20)
        form.get_by_role("option").filter(has_text=programme).get_by_role("button").click()
        expect(picker).to_have_value(programme)
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("radar-classify-%s" % broker_id).click()
        expect(page).to_have_url(re.compile(r"/technology/radar/?$"))

        _assert_assess_entry(page, broker, broker_id, rationale, programme, existing)
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        _assert_assess_entry(page, broker, broker_id, rationale, programme, existing)

        # The existing broker, placed with the ring alone, has nothing
        # recorded for the other details: each says so in words.
        _move_to(page, existing, existing_id, "hold")
        expect(page.get_by_test_id("radar-ring-%s" % existing_id)).to_have_text("Hold")
        for field, unset in (("rationale", "None recorded"), ("review-date", "Not set"),
                             ("initiative", "Not linked")):
            expect(page.get_by_test_id("radar-%s-%s" % (field, existing_id))).to_have_text(unset)
        # Moving the new broker keeps what was recorded for it.
        _move_to(page, broker, broker_id, "trial")
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id("radar-ring-%s" % broker_id)).to_have_text("Trial")
        expect(page.get_by_test_id("radar-rationale-%s" % broker_id)).to_have_text(rationale)
        expect(page.get_by_test_id("radar-initiative-%s" % broker_id)).to_have_text(programme)
    finally:
        context.close()

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        assert page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_text(broker)).to_have_count(0)
        expect(page.get_by_text(programme)).to_have_count(0)
    finally:
        context.close()


def test_cto_publishes_a_standard_with_ring_and_sunset_date(browser, live_server):
    org = create_fresh_org("cto")
    other = create_fresh_org("cto")
    tag = uuid.uuid4().hex[:6]
    element = "Message Bus %s" % tag
    domain = "Messaging %s" % tag
    unlinked = "Legacy FTP %s" % tag

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        _create_technology_element(page, live_server, element)

        assert page.goto(live_server + "/governance/standards", timeout=PAGE_TIMEOUT).status == 200
        form = page.get_by_test_id("standard-form")
        form.locator('input[name="technology_name"]').fill(element)
        form.locator('input[name="category"]').fill(domain)
        form.locator('select[name="status"]').select_option("preferred")
        form.locator('input[name="approved_version"]').fill("3.x")
        form.get_by_role("combobox", name="Technology element").press_sequentially(
            "Message Bus", delay=20)
        form.get_by_role("option").filter(has_text=element).get_by_role("button").click()
        form.locator('select[name="ring"]').select_option("adopt")
        form.locator('input[name="sunset_date"]').fill("2030-06-30")
        form.locator('textarea[name="rationale"]').fill("Supported broker for all new integration")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("standard-submit").click()

        # A second standard in the same domain with no ring and no sunset date.
        form = page.get_by_test_id("standard-form")
        form.locator('input[name="technology_name"]').fill(unlinked)
        form.locator('input[name="category"]').fill(domain)
        form.locator('select[name="status"]').select_option("deprecated")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.get_by_test_id("standard-submit").click()

        def _check():
            section = page.locator("[data-testid='standards-domain'][data-domain='%s']" % domain)
            expect(section).to_be_visible(timeout=PAGE_TIMEOUT)
            linked_row = section.locator("tr", has_text=element)
            expect(linked_row).to_contain_text("Preferred")
            expect(linked_row.locator("[data-testid^='standard-ring-']")).to_have_text("Adopt")
            expect(linked_row.locator("[data-testid^='standard-sunset-']")).to_have_text("30 Jun 2030")
            expect(linked_row).to_contain_text("Supported broker for all new integration")
            bare_row = section.locator("tr", has_text=unlinked)
            expect(bare_row.locator("[data-testid^='standard-ring-']")).to_have_text("—")
            expect(bare_row.locator("[data-testid^='standard-sunset-']")).to_have_text("—")

        _check()
        assert page.reload(timeout=PAGE_TIMEOUT).status == 200
        _check()

        # The ring lives on the radar: the element is shown in Adopt there.
        assert page.goto(live_server + "/technology/radar/", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_test_id("radar-ring-%s" % _element_id(page, element))).to_have_text("Adopt")
    finally:
        context.close()

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, other["emails"]["cto"])
        assert page.goto(live_server + "/governance/standards", timeout=PAGE_TIMEOUT).status == 200
        expect(page.get_by_text(element)).to_have_count(0)
        expect(page.get_by_text(domain)).to_have_count(0)
    finally:
        context.close()
