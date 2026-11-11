"""An architect works from a layer's list: Delete on a record removes that record and no other, even when another
record's id equals this record's architecture element id; Edit renames a type that has no form configuration and
its record page shows the new name; a Principle keeps its description; a Business Function's record page names its
capability, links to it and is not marked as found by the AI; the capability error goes once one is chosen; and a
Value Stream's form opens with its element type chosen.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect
from sqlalchemy import text

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("LISTED_RECORD_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _body(page):
    return re.sub(r"\s+", " ", page.locator("body").inner_text())


def _control(page, label):
    """The one control carrying this label that a person can see (the table and the card list both carry it)."""
    return page.get_by_label(label).locator("visible=true")


def _signed_in(browser, live_server, org, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    return context, page


def _seed_overlapping_nodes(org):
    """Two mirrored Nodes. 'Second node' sits on the record id that is 'First node''s architecture element id."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    with create_app("testing").app_context():
        table = Node.__tablename__
        for _attempt in range(40):
            first_element = ArchiMateElement(organization_id=org["org_id"], name="First node " + org["suffix"],
                                             type="Node", layer="Technology")
            second_element = ArchiMateElement(organization_id=org["org_id"], name="Second node " + org["suffix"],
                                              type="Node", layer="Technology")
            db.session.add_all([first_element, second_element])
            db.session.flush()
            if not db.session.execute(text("SELECT count(*) FROM %s WHERE id = :id" % table),
                                      {"id": first_element.id}).scalar():
                break
        else:
            raise AssertionError("no free record id to force an overlap")
        first = Node(organization_id=org["org_id"], name=first_element.name, archimate_element_id=first_element.id)
        second = Node(organization_id=org["org_id"], name=second_element.name, archimate_element_id=second_element.id)
        db.session.add_all([first, second])
        db.session.flush()
        apart = 800000000 + uuid.uuid4().int % 100000000
        db.session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % table), {"new": apart, "old": first.id})
        db.session.execute(text("UPDATE %s SET id = :new WHERE id = :old" % table),
                           {"new": first_element.id, "old": second.id})
        # The forced ids are real rows; keep the table's own counter clear of them so a later create elsewhere
        # in the shared database does not take a number a record already holds.
        db.session.execute(text("SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                                "greatest((SELECT max(id) FROM %s WHERE id < 800000000), nextval(pg_get_serial_sequence(:t, 'id'))))" % table),
                           {"t": table})
        db.session.commit()
        ids = {"first_record": apart, "second_record": first_element.id}
        db.session.remove()
    return ids


def _node_names(org_id):
    from app import create_app
    from app.models.technology_layer import Node

    with create_app("testing").app_context():
        return sorted(n.name for n in Node.query.filter_by(organization_id=org_id).all())


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_delete_on_a_listed_node_removes_that_node_and_not_the_one_sharing_its_element_id(
        browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    ids = _seed_overlapping_nodes(org)
    first, second = "First node " + org["suffix"], "Second node " + org["suffix"]
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/technology/Node", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_control(page, "Delete " + first).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(_control(page, "Delete " + second).first).to_be_visible(timeout=PAGE_TIMEOUT)
        _frame(page, "nodes-before-delete", viewport, theme)
        _control(page, "Delete " + first).first.click()
        dialog = page.get_by_role("alertdialog")
        expect(dialog).to_contain_text(first, timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Delete").click()
        expect(_control(page, "Delete " + first)).to_have_count(0, timeout=PAGE_TIMEOUT)
        _frame(page, "nodes-after-delete", viewport, theme)

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_control(page, "Delete " + second).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _node_names(org["org_id"]) == [second]
        # The surviving node's own page opens by its own record id and shows its name.
        page.goto(live_server + "/architecture/technology/Node/%d" % ids["second_record"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert second in _body(page)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_type_with_no_form_configuration_is_renamed_from_its_list_and_its_page_shows_the_new_name(
        browser, live_server, viewport, theme):
    from app import create_app, db
    from app.models.application_layer import ApplicationFunction
    from app.models.archimate_core import ArchiMateElement

    org = create_fresh_org("enterprise_architect")
    old, new = "Order entry " + org["suffix"], "Order capture " + org["suffix"]
    with create_app("testing").app_context():
        element = ArchiMateElement(organization_id=org["org_id"], name=old, type="ApplicationFunction",
                                   layer="Application")
        db.session.add(element)
        db.session.flush()
        row = ApplicationFunction(organization_id=org["org_id"], name=old, archimate_element_id=element.id)
        db.session.add(row)
        db.session.commit()
        record_id = row.id
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/application/ApplicationFunction", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        _control(page, "Edit " + old).first.click()
        form = page.locator("#archimate-form-modal")
        expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(new)
        _frame(page, "application-function-edit", viewport, theme)
        form.get_by_role("button", name="Update").click()
        expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(_control(page, "Delete " + new).first).to_be_visible(timeout=PAGE_TIMEOUT)
        with create_app("testing").app_context():
            assert db.session.get(ApplicationFunction, record_id).name == new
        page.goto(live_server + "/architecture/application/ApplicationFunction/%d" % record_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        text_ = _body(page)
        assert new in text_ and old not in text_
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_principle_description_and_a_business_function_capability_are_kept_and_shown(
        browser, live_server, viewport, theme):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    org = create_fresh_org("enterprise_architect")
    capability_name = "Order fulfilment " + org["suffix"]
    with create_app("testing").app_context():
        business = BusinessCapability(organization_id=org["org_id"], name=capability_name, level=1)
        db.session.add(business)
        db.session.commit()
        assert UnifiedCapability.query.filter_by(source_table="business_capability",
                                                 source_id=str(business.id)).count() == 1
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        principle, description = "Reuse first " + org["suffix"], "Applies to every new build " + org["suffix"]
        page.goto(live_server + "/architecture/motivation/Principle/new", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_label("Element Type")).to_have_value("Principle", timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(principle)
        form.get_by_label("Statement").fill("Reuse before buying")
        form.get_by_label("Description").fill(description)
        form.get_by_role("button", name="Create").click()
        expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/architecture/motivation/Principle", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=principle).first.click()
        page.wait_for_url(re.compile(r"/Principle/\d+$"), timeout=PAGE_TIMEOUT)
        assert description in _body(page) and "No description yet" not in _body(page)
        _frame(page, "principle-record", viewport, theme)

        function = "Pick and pack " + org["suffix"]
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
        expect(form).not_to_contain_text("Capability is required", timeout=PAGE_TIMEOUT)
        form.get_by_role("button", name="Create").click()
        expect(form).to_be_hidden(timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/architecture/business/BusinessFunction", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=function).first.click()
        page.wait_for_url(re.compile(r"/BusinessFunction/\d+$"), timeout=PAGE_TIMEOUT)
        text_ = _body(page)
        assert re.search(r"Capability\s+%s" % re.escape(capability_name), text_), text_
        assert not re.search(r"Discovered by ai\s+Yes", text_)
        _frame(page, "function-record", viewport, theme)
        page.get_by_role("link", name=capability_name).click()
        page.wait_for_url(re.compile(r"/fact-sheet$"), timeout=PAGE_TIMEOUT)
        assert capability_name in _body(page)
        _frame(page, "function-capability-fact-sheet", viewport, theme)

        page.goto(live_server + "/architecture/strategy/ValueStream/new", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        expect(page.locator("#archimate-form-modal").get_by_label("Element Type")).to_have_value(
            "ValueStream", timeout=PAGE_TIMEOUT)
    finally:
        context.close()
