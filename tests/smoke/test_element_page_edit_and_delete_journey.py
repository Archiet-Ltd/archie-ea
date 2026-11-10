"""An architect works from an element's own page. Edit opens that element's form and saving lands back on its
page showing the change, for a Goal and a Product (each held with a record whose id differs from its
element's). On the page of an element held with no record, Edit and Delete act on that element and not on
the record that happens to carry the same number. Export of only such elements says why nothing is exported.
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
KINDS = [("motivation", "Goal"), ("business", "Product")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("ELEMENT_PAGE_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _signed_in(browser, live_server, org, viewport, theme):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    return context, page


def _make_record(org, kind, name):
    """A record of this kind whose id differs from its architecture element's id."""
    from app import create_app, db
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    with create_app("testing").app_context():
        model = MODEL_REGISTRY[kind]
        row = model(organization_id=org["org_id"], **{"title" if kind == "Goal" and not hasattr(model, "name") else "name": name})
        db.session.add(row)
        db.session.commit()
        ids = {"record": row.id, "element": row.archimate_element_id}
        db.session.remove()
    return ids


def _names(org, kind):
    from app import create_app
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    with create_app("testing").app_context():
        model = MODEL_REGISTRY[kind]
        return sorted((getattr(r, "name", None) or r.title) for r in model.query.filter_by(organization_id=org["org_id"]).all())


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
@pytest.mark.parametrize("layer,kind", KINDS)
def test_edit_on_an_element_page_opens_its_form_and_saves_back_to_its_page(
        browser, live_server, layer, kind, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    name = "%s page edit %s" % (kind, uuid.uuid4().hex[:6])
    ids = _make_record(org, kind, name)
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        record = "/architecture/%s/%s/%d" % (layer, kind, ids["record"])
        page.goto(live_server + record, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        _frame(page, "%s-page" % kind, viewport, theme)
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        assert re.search(r"/%s/%s/%d/edit" % (layer, kind, ids["record"]), page.url), page.url
        _frame(page, "%s-form" % kind, viewport, theme)
        form.get_by_role("textbox", name="Element name").fill(name + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/%s/%d$" % (kind, ids["record"])), timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("body")).to_contain_text(name + " renamed", timeout=PAGE_TIMEOUT)
        _frame(page, "%s-saved" % kind, viewport, theme)
        assert _names(org, kind) == [name + " renamed"]
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
@pytest.mark.parametrize("layer,kind", KINDS)
def test_edit_on_the_page_of_a_goal_or_product_held_only_as_an_element_opens_its_form_and_saves(
        browser, live_server, layer, kind, viewport, theme):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    org = create_fresh_org("enterprise_architect")
    name = "%s element only %s" % (kind, uuid.uuid4().hex[:6])
    with create_app("testing").app_context():
        element = ArchiMateElement(organization_id=org["org_id"], name=name, type=kind, layer=layer.capitalize())
        db.session.add(element)
        db.session.commit()
        element_id = element.id
        db.session.remove()
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/%s/%s/%d?ae=1" % (layer, kind, element_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(name + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/%s/%d\?ae=1$" % (kind, element_id)), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(name + " renamed", timeout=PAGE_TIMEOUT)
        _frame(page, "%s-element-only-saved" % kind, viewport, theme)
    finally:
        context.close()


def _make_element_and_record_of_its_number(org):
    """An element held with no record, and a Node whose record id is that element's id."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    with create_app("testing").app_context():
        for _attempt in range(40):
            element = ArchiMateElement(organization_id=org["org_id"], name="Lonely " + org["suffix"], type="Node",
                                       layer="Technology")
            db.session.add(element)
            db.session.flush()
            if not db.session.execute(text("SELECT count(*) FROM %s WHERE id = :id" % Node.__tablename__), {"id": element.id}).scalar():
                break
        else:
            raise AssertionError("no free record id to force an overlap")
        holder = Node(id=element.id, organization_id=org["org_id"], name="Holder " + org["suffix"])
        db.session.add(holder)
        db.session.commit()
        ids = {"element": element.id}
        table = Node.__tablename__
        db.session.execute(text("SELECT setval(pg_get_serial_sequence(:t, 'id'), greatest((SELECT max(id) FROM %s), "
                                "nextval(pg_get_serial_sequence(:t, 'id'))))" % table), {"t": table})
        db.session.commit()
        db.session.remove()
    return ids


def _state(org):
    from app import create_app
    from app.models.archimate_core import ArchiMateElement
    from app.models.technology_layer import Node

    with create_app("testing").app_context():
        return (sorted(n.name for n in Node.query.filter_by(organization_id=org["org_id"]).all()),
                sorted(e.name for e in ArchiMateElement.query.filter_by(organization_id=org["org_id"], type="Node").all()))


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_edit_and_delete_on_the_page_of_an_element_with_no_record_act_on_that_element(
        browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    ids = _make_element_and_record_of_its_number(org)
    lonely, holder = "Lonely " + org["suffix"], "Holder " + org["suffix"]
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page_url = "/architecture/technology/Node/%d?ae=1" % ids["element"]
        page.goto(live_server + page_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(lonely, timeout=PAGE_TIMEOUT)
        assert holder not in page.locator("body").inner_text()
        _frame(page, "lonely-page", viewport, theme)

        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(lonely, timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(lonely + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/Node/%d\?ae=1$" % ids["element"]), timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(lonely + " renamed", timeout=PAGE_TIMEOUT)
        assert holder not in page.locator("body").inner_text()
        _frame(page, "lonely-saved", viewport, theme)
        records, elements = _state(org)
        assert records == [holder] and lonely + " renamed" in elements

        page.get_by_test_id("delete-element").click()
        dialog = page.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(lonely + " renamed", timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Confirm").click()
        page.wait_for_url(re.compile(r"/architecture/technology/Node$"), timeout=PAGE_TIMEOUT)
        records, elements = _state(org)
        assert records == [holder], "the record sharing the element's number was deleted: %s" % records
        assert lonely + " renamed" not in elements
    finally:
        context.close()


# Bulk selection is a table feature: the 390-wide card list carries no checkboxes, so this is walked at 1440.
@pytest.mark.parametrize("viewport,theme", VIEWPORTS[:1])
def test_export_of_only_elements_held_with_no_record_says_why_nothing_is_exported(
        browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    _make_element_and_record_of_its_number(org)
    lonely = "Lonely " + org["suffix"]
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/technology/Node", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Toggle bulk selection").locator("visible=true").first.click()
        page.get_by_label("Select " + lonely).locator("visible=true").first.check()
        page.get_by_label("Export selected architecture elements").locator("visible=true").first.click()
        expect(page.locator("body")).to_contain_text("Nothing exported", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("no record to export")
        _frame(page, "export-nothing", viewport, theme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_a_refusal_on_the_create_form_is_in_view_where_the_person_pressed_create(
        browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + "/architecture/motivation/Goal/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_be_visible(timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill("x" * 400)
        form.get_by_role("button", name="Create").scroll_into_view_if_needed()
        form.get_by_role("button", name="Create").click()
        notice = page.locator("#archimate-form-error")
        expect(notice).to_contain_text("at most 255", timeout=PAGE_TIMEOUT)
        box = notice.bounding_box()
        assert box and box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], (
            "the refusal is outside the visible screen: %s" % box)
        _frame(page, "create-refused", viewport, theme)
    finally:
        context.close()
