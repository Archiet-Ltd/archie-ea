"""An enterprise architect opens an element from a global search hit, edits its name and deletes it. This holds
for an element whose type has a record table and for one whose type has none (a Location). The page names the
element's own type, and another organisation's element answers 404 to Edit and to Delete."""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]
KINDS = [("Location", "physical"), ("Deliverable", "implementation")]


def _frame(page, name, viewport, theme):
    out = os.environ.get("ELEMENT_SEARCH_HIT_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (out, name, viewport["width"], theme), full_page=True)


def _make(org, kind, layer, name):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        element = ArchiMateElement(organization_id=org["org_id"], name=name, type=kind, layer=layer)
        db.session.add(element)
        db.session.commit()
        element_id = element.id
        db.session.remove()
    return element_id


def _held(element_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    with create_app("testing").app_context():
        row = db.session.execute(db.select(ArchiMateElement.name).where(ArchiMateElement.id == element_id)).scalar()
        db.session.remove()
        return row


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
@pytest.mark.parametrize("kind,layer", KINDS)
def test_edit_and_delete_work_on_an_element_page_opened_from_a_search_hit(
        browser, live_server, kind, layer, viewport, theme):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    tag = uuid.uuid4().hex[:6]
    name = "Hit %s mine %s" % (kind, tag)
    mine_id = _make(mine, kind, layer, name)
    foreign_name = "Hit %s theirs %s" % (kind, tag)
    foreign_id = _make(theirs, kind, layer, foreign_name)
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, mine["emails"]["enterprise_architect"])
    page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    try:
        status, body = api(page, "GET", "/api/sidebar/search?q=%s" % ("Hit %s mine %s" % (kind, tag)).replace(" ", "%20"))
        assert status == 200, body
        hit = next(h for h in (body.get("results") or body.get("data") or []) if h["name"] == name)
        page.goto(live_server + hit["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("element-type")).to_contain_text(re.compile(kind, re.I))
        assert "not yet supported" not in page.locator("body").inner_text()
        _frame(page, "%s-page" % kind, viewport, theme)

        page.get_by_role("link", name="Edit").first.click()
        form = page.locator("#archimate-form-modal")
        expect(form.get_by_role("textbox", name="Element name")).to_have_value(name, timeout=PAGE_TIMEOUT)
        form.get_by_role("textbox", name="Element name").fill(name + " renamed")
        form.get_by_role("button", name="Update").click()
        page.wait_for_url(re.compile(r"/architecture/[a-z]+/%s/%d(\?ae=1)?$" % (kind, mine_id)), timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name=name + " renamed").first).to_be_visible(timeout=PAGE_TIMEOUT)
        _frame(page, "%s-saved" % kind, viewport, theme)
        assert _held(mine_id) == name + " renamed"

        # Another organisation's element: Edit and Delete answer 404 and change nothing.
        other = page.goto(live_server + "/architecture/%s/%s/%d/edit?ae=1" % (layer, kind, foreign_id),
                          wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert other.status == 404
        page.goto(live_server + "/architecture/%s/%s/%d?ae=1" % (layer, kind, mine_id),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        status, _ = api(page, "POST", "/architecture/%s/%s/%d/delete?ae=1" % (layer, kind, foreign_id), {})
        assert status == 404
        assert _held(foreign_id) == foreign_name

        page.get_by_test_id("delete-element").click()
        dialog = page.get_by_role("dialog", name="Confirm")
        expect(dialog).to_contain_text(name + " renamed", timeout=PAGE_TIMEOUT)
        dialog.get_by_role("button", name="Confirm").click()
        page.wait_for_url(re.compile(r"/architecture/(%s/%s|dashboard|[a-z]*)/?(\?.*)?$" % (layer, kind)), timeout=PAGE_TIMEOUT)
        assert re.search(r"/architecture/(dashboard|%s/%s)?/?$" % (layer, kind), page.url), page.url
        expect(page.get_by_role("heading").first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "not yet supported" not in page.locator("body").inner_text()
        _frame(page, "%s-deleted" % kind, viewport, theme)
        assert _held(mine_id) is None
        assert _held(foreign_id) == foreign_name
    finally:
        context.close()
