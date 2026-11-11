"""An address built from an element's number opens that element's own record, even when another record of the same
type carries the same number. For a Goal and a Product, record A is held with element number E and record B has
the record number E. A person who clicks A's element on a solution page, or follows the chat's link for it, sees A;
Edit saves A and leaves B; Delete removes A and leaves B in the list. Every other organisation's record stays out of it.
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


def _title_field(model):
    return "name" if hasattr(model, "name") else "title"


def _seed(org, kind, name_a, name_b, with_solution=True):
    """Record A (its own id, element number E) and record B (record number E), in one organisation."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.solution_models import Solution
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    layer = "Motivation" if kind == "Goal" else "Business"
    with create_app("testing").app_context():
        model = MODEL_REGISTRY[kind]
        field = _title_field(model)
        record_a = model(organization_id=org["org_id"], **{field: name_a})
        db.session.add(record_a)
        db.session.flush()
        for _attempt in range(60):
            element = ArchiMateElement(organization_id=org["org_id"], name=name_a, type=kind, layer=layer)
            db.session.add(element)
            db.session.flush()
            taken = db.session.execute(
                text("SELECT count(*) FROM %s WHERE id = :id" % model.__tablename__), {"id": element.id}).scalar()
            if element.id != record_a.id and not taken:
                break
            db.session.delete(element)
            db.session.flush()
        else:
            raise AssertionError("no free record number to match the element's number")
        record_a.archimate_element_id = element.id
        record_b = model(id=element.id, organization_id=org["org_id"], **{field: name_b})
        db.session.add(record_b)
        db.session.flush()
        table = model.__tablename__
        db.session.execute(text("SELECT setval(pg_get_serial_sequence(:t, 'id'), greatest((SELECT max(id) FROM %s), "
                                "nextval(pg_get_serial_sequence(:t, 'id'))))" % table), {"t": table})
        ids = {"a": record_a.id, "b": record_b.id, "element": element.id, "solution": None}
        if with_solution:
            solution = Solution(name="Link solution %s" % org["suffix"], description="Holds element A.",
                                organization_id=org["org_id"], governance_status="draft")
            db.session.add(solution)
            db.session.flush()
            db.session.add(SolutionArchiMateElement(solution_id=solution.id, element_id=element.id))
            ids["solution"] = solution.id
        db.session.commit()
        db.session.remove()
    assert ids["a"] != ids["b"] and ids["b"] == ids["element"], ids
    return ids


def _names(org, kind):
    from app import create_app
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    with create_app("testing").app_context():
        model = MODEL_REGISTRY[kind]
        field = _title_field(model)
        return sorted(getattr(r, field) for r in model.query.filter_by(organization_id=org["org_id"]).all())


def _chat_link(layer, kind, element_id):
    """The address the assistant's answer gives for an architecture element."""
    from app import create_app
    from app.modules.ai_chat.services.agent_runner import AgentRunner

    app = create_app("testing")
    with app.test_request_context("/"):
        return AgentRunner._source_url("archimate_element", {"id": element_id, "layer": layer, "type": kind})


def _open_from_solution(page, live_server, ids, name_a):
    page.goto("%s/solutions/%d?tab=design" % (live_server, ids["solution"]), wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    link = page.get_by_role("link", name=name_a, exact=True).first
    expect(link).to_be_visible(timeout=PAGE_TIMEOUT)
    href = link.get_attribute("href")
    assert "ae=1" in href, "the element's link does not say the number is an element's: %s" % href
    link.click()
    page.wait_for_url(re.compile(r"/architecture/"), timeout=PAGE_TIMEOUT)


def _assert_is_a(page, name_a, name_b):
    expect(page.locator("main")).to_contain_text(name_a, timeout=PAGE_TIMEOUT)
    assert name_b not in page.locator("main").inner_text(), "the page shows record B, not the element's own record"


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
@pytest.mark.parametrize("layer,kind", KINDS)
def test_a_solution_pages_element_link_opens_edits_and_deletes_that_elements_own_record(
        browser, live_server, layer, kind, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    name_a, name_b = "%s A %s" % (kind, tag), "%s B %s" % (kind, tag)
    ids = _seed(org, kind, name_a, name_b)
    other_ids = _seed(other, kind, "Other A " + tag, "Other B " + tag, with_solution=False)
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        _open_from_solution(page, live_server, ids, name_a)
        _assert_is_a(page, name_a, name_b)
        _frame(page, "%s-from-solution" % kind, viewport, theme)

        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name_a, timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(name_a + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/%s/%d$" % (kind, ids["a"])), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name_a + " renamed", timeout=PAGE_TIMEOUT)
        _frame(page, "%s-edited" % kind, viewport, theme)
        assert _names(org, kind) == sorted([name_a + " renamed", name_b])

        page.get_by_test_id("delete-element").click()
        dialog = page.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(name_a + " renamed", timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Confirm").click()
        page.wait_for_url(re.compile(r"/architecture/%s/%s$" % (layer, kind)), timeout=PAGE_TIMEOUT)
        assert _names(org, kind) == [name_b], "Delete removed the wrong record: %s" % _names(org, kind)
        expect(page.locator("main")).to_contain_text(name_b, timeout=PAGE_TIMEOUT)
        _frame(page, "%s-deleted" % kind, viewport, theme)
        assert _names(other, kind) == sorted(["Other A " + tag, "Other B " + tag]), "another organisation's records changed"
        assert other_ids["a"] != other_ids["b"]
    finally:
        context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
@pytest.mark.parametrize("layer,kind", KINDS)
def test_the_chats_link_for_an_element_opens_edits_and_deletes_that_elements_own_record(
        browser, live_server, layer, kind, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    name_a, name_b = "%s chat A %s" % (kind, tag), "%s chat B %s" % (kind, tag)
    ids = _seed(org, kind, name_a, name_b, with_solution=False)
    href = _chat_link(layer, kind, ids["element"])
    assert href and "ae=1" in href, "the chat's link does not say the number is an element's: %s" % href
    context, page = _signed_in(browser, live_server, org, viewport, theme)
    try:
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _assert_is_a(page, name_a, name_b)
        _frame(page, "%s-from-chat" % kind, viewport, theme)

        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name_a, timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(name_a + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/%s/%d$" % (kind, ids["a"])), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name_a + " renamed", timeout=PAGE_TIMEOUT)
        assert _names(org, kind) == sorted([name_a + " renamed", name_b])

        page.get_by_test_id("delete-element").click()
        dialog = page.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(name_a + " renamed", timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Confirm").click()
        page.wait_for_url(re.compile(r"/architecture/%s/%s$" % (layer, kind)), timeout=PAGE_TIMEOUT)
        assert _names(org, kind) == [name_b], "Delete removed the wrong record: %s" % _names(org, kind)
    finally:
        context.close()
