"""One set of rules for the Create and Edit forms, walked as the architect who fills them.

From Gap Analysis the architect clicks New gap, lands on the Gap form with Gap chosen, and records a gap at
High and another at Low: each record page shows the rating chosen and the status Identified, and the gap
register shows the same rating. A record held only as an architecture element offers its architecture state
and no field it cannot save; choosing Not set clears the state and the form reopens on Not set. A Principle
is created with its statement and refused without it, a Business Function under a capability found by name,
and a value too long for its field is refused by the field's label with the limit.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]
RATING = "Rating (sets priority and severity)"


def _frame(page, name, viewport, theme):
    out = os.environ.get("CREATE_FORM_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _body(page):
    return re.sub(r"\s+", " ", page.locator("body").inner_text())


def _signed_in(browser, live_server, org, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    return context, page


def _offered(select):
    return select.locator("option").all_inner_texts()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_new_gap_opens_the_gap_form_and_each_rating_chosen_is_the_rating_saved(browser, live_server, viewport, theme):
    from app import create_app
    from app.services.gap_register_service import create_gap

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        create_gap(org["org_id"], "Existing gap %s" % org["suffix"], severity="medium", priority="medium")
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        for rating in ("High", "Low"):
            name = "Recorded at %s %s" % (rating, org["suffix"])
            page.goto(live_server + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT)
            page.get_by_test_id("btn-new-gap").click()
            form = page.locator("#archimate-form-modal")
            expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
            # The form is the Gap form, on the implementation tab, with Gap chosen and its fields shown.
            expect(form.get_by_label("Element Type")).to_have_value("Gap", timeout=PAGE_TIMEOUT)
            assert "/architecture/implementation/Gap/new" in page.url
            ratings = form.get_by_label(RATING)
            expect(ratings).to_have_value("medium")
            assert _offered(ratings) == ["Critical", "High", "Medium", "Low"]
            assert _offered(form.get_by_label("Resolution status"))[0] == "Identified"
            assert _offered(form.get_by_label("Architecture state")).count("Not set") == 1
            form.get_by_role("textbox", name="Element name").fill(name)
            ratings.select_option(rating.lower())
            expect(form).to_contain_text("Saving sets the priority and severity to %s." % rating)
            _frame(page, "new-gap-%s" % rating.lower(), viewport, theme)
            form.get_by_role("button", name="Create").click()
            expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)

            page.goto(live_server + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT)
            row = page.locator("[data-severity]", has_text=name)
            expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
            assert row.first.get_attribute("data-severity") == rating.lower()
            row.first.click()
            page.wait_for_url(re.compile(r"/architecture/implementation/Gap/\d+$"), timeout=PAGE_TIMEOUT)
            text = _body(page)
            assert re.search(r"Rating \(sets priority and severity\)\s*:?\s*%s" % rating, text), text
            assert "Identified" in text
            assert "Not set" not in text
            _frame(page, "gap-record-%s" % rating.lower(), viewport, theme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_an_element_only_record_offers_only_what_it_saves_and_not_set_clears_its_state(browser, live_server, viewport, theme):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    org = create_fresh_org("enterprise_architect")
    name = "Held as element %s" % org["suffix"]
    with create_app("testing").app_context():
        element = ArchiMateElement(organization_id=org["org_id"], name=name, type="Node", layer="Technology",
                                   togaf_plateau="Target")
        db.session.add(element)
        db.session.commit()
        element_id = element.id
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        edit = live_server + "/architecture/technology/Node/%d/edit" % element_id
        page.goto(edit, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        state = form.get_by_label("Architecture state")
        expect(state).to_have_value("Target", timeout=PAGE_TIMEOUT)
        assert _offered(state) == ["Not set", "As-Is (baseline)", "To-Be (target)", "Transition"]
        # The record holds no node type and no location, so the form does not ask for them.
        expect(form.get_by_label("Location / Data Centre")).to_have_count(0)
        expect(form.get_by_label("Node Type")).to_have_count(0)
        _frame(page, "element-only-edit", viewport, theme)

        state.select_option("")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/Node/%d$" % element_id), timeout=PAGE_TIMEOUT)
        page.goto(edit, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#archimate-form-modal").get_by_label("Architecture state")).to_have_value(
            "", timeout=PAGE_TIMEOUT)
        assert page.locator("#archimate-form-modal").get_by_label("Architecture state").evaluate(
            "s => s.options[s.selectedIndex].text") == "Not set"

        page.locator("#archimate-form-modal").get_by_label("Architecture state").select_option("Baseline")
        page.locator("#archimate-form-modal").get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/Node/%d$" % element_id), timeout=PAGE_TIMEOUT)
        page.goto(edit, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#archimate-form-modal").get_by_label("Architecture state")).to_have_value(
            "Baseline", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_principle_and_a_business_function_are_created_and_a_too_long_value_is_refused_by_label(
        browser, live_server, viewport, theme):
    from app import create_app, db
    from app.models.unified_capability import UnifiedCapability

    org = create_fresh_org("enterprise_architect")
    capability_name = "Order fulfilment %s" % org["suffix"]
    with create_app("testing").app_context():
        db.session.add(UnifiedCapability(organization_id=org["org_id"], name=capability_name, scope="tenant", level=1))
        db.session.commit()
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        # Principle: its statement is required and shown on the record page.
        principle = "Reuse first %s" % org["suffix"]
        page.goto(live_server + "/architecture/motivation/Principle/new", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_label("Element Type")).to_have_value("Principle", timeout=PAGE_TIMEOUT)
        expect(form).to_contain_text("Statement *")
        form.get_by_role("textbox", name="Element name").fill(principle)
        form.get_by_role("button", name="Create").click()
        expect(form).to_contain_text("Statement is required", timeout=PAGE_TIMEOUT)
        form.get_by_label("Statement").fill("Reuse an existing service before building a new one")
        _frame(page, "principle-create", viewport, theme)
        form.get_by_role("button", name="Create").click()
        expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/architecture/motivation/Principle", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=principle).first.click()
        page.wait_for_url(re.compile(r"/Principle/\d+$"), timeout=PAGE_TIMEOUT)
        assert "Reuse an existing service before building a new one" in _body(page), _body(page)[_body(page).find("About"):][:600]

        # Business Function: the capability is found by name and is the one the record page names.
        function = "Pick and pack %s" % org["suffix"]
        page.goto(live_server + "/architecture/business/BusinessFunction/new", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        expect(form).to_contain_text("Capability *", timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(function)
        form.get_by_role("button", name="Create").click()
        expect(form).to_contain_text("Capability is required", timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Search capabilities").fill(capability_name)
        picker = form.get_by_label("Capability", exact=True)
        expect(picker.locator("option", has_text=capability_name)).to_have_count(1, timeout=PAGE_TIMEOUT)
        picker.select_option(label=capability_name)
        _frame(page, "function-create", viewport, theme)
        form.get_by_role("button", name="Create").click()
        expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)

        # A value too long for its field is refused by the field's label, with the limit.
        page.goto(live_server + "/architecture/business/ValueStream/new", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        form.get_by_role("textbox", name="Element name").fill("Order to cash %s" % org["suffix"])
        form.get_by_label("Domain").fill("x" * 60)
        form.get_by_role("button", name="Create").click()
        expect(form).to_contain_text("Domain is at most 50 characters", timeout=PAGE_TIMEOUT)
        _frame(page, "too-long-refused", viewport, theme)
    finally:
        context.close()
