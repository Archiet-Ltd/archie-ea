"""Journey: an architect imports a model file, then corrects and removes what it brought in.

A person imports a small Open Exchange file (a Goal, a Product, a Technology Service and an Application
Component), opens each element at its model address, edits its name, saves and sees the new name after a
reload, then deletes one and finds it gone from its page, from the search service and from the model lookup. A second
organisation's architect cannot edit or delete the first one's imported element. An import of a file whose
element looks like one already stored says so, and nothing is merged on its own.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_WALK_DIR", "test-results/imported-element-editable")

_XML = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-model-{tag}">
  <name xml:lang="en">Editable kinds {tag}</name>
  <elements>
    <element identifier="id-goal" xsi:type="Goal"><name xml:lang="en">Faster onboarding {tag}</name></element>
    <element identifier="id-product" xsi:type="Product"><name xml:lang="en">Onboarding bundle {tag}</name></element>
    <element identifier="id-tech" xsi:type="TechnologyService"><name xml:lang="en">Hosting service {tag}</name></element>
    <element identifier="id-app" xsi:type="ApplicationComponent"><name xml:lang="en">Onboarding portal {tag}</name></element>
  </elements>
</model>
"""

_SIMILAR = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-model-sim-{tag}">
  <name xml:lang="en">Similar {tag}</name>
  <elements>
    <element identifier="id-bundle" xsi:type="Product"><name xml:lang="en">Onboarding bundle {tag} - extended</name></element>
  </elements>
</model>
"""


def _shot(page, name):
    os.makedirs(WALK_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _import(page, base, path):
    page.goto(base + "/architecture/import/oef", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.set_input_files("#oef_file", path)
    page.click("[data-testid='btn-preview-import']")
    expect(page.locator("[data-testid='import-preview']")).to_be_visible(timeout=PAGE_TIMEOUT)


def _finish_import(page):
    page.click("[data-testid='btn-import-model']")
    result = page.locator("[data-testid='import-result']")
    expect(result).to_be_visible(timeout=PAGE_TIMEOUT)
    return result


def _find(page, base, name):
    """Search hits for an element's exact name. An application the search opens on its own record page is
    also reached the way a link from the model graph reaches it: by the generic element address."""
    resp = page.request.get(base + "/api/sidebar/search?q=" + name.replace(" ", "%20"))
    assert resp.ok, resp.status
    return [r for r in resp.json().get("results", []) if r.get("name") == name]


def _element_address(page, base, name):
    """The generic /architecture/<layer>/element/<id> address of an element in the model."""
    resp = page.request.get(base + "/archimate/api/elements/search?q=" + name.replace(" ", "%20"))
    assert resp.ok, resp.status
    body = resp.json()
    rows = body.get("data") or body.get("elements") or body.get("results") or []
    rows = [r for r in rows if r.get("name") == name]
    if not rows:
        return None
    return "/architecture/%s/element/%s" % ((rows[0].get("layer") or "application").lower(), rows[0]["id"])


def _second_org_architect(tag):
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from .fresh_org import add_administrator

    email = "smoke.import.second.%s@example.com" % tag
    app = create_app("testing")
    with app.app_context():
        second = Organization(name="Second %s" % tag, slug="second-%s" % tag)
        db.session.add(second)
        db.session.flush()
        user = User(email=email, first_name="Smoke", last_name="Second", organization_id=second.id,
                    enterprise_role="solution_architect", confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        add_administrator(second.id, tag)
    return email


def _sign_in_as(context, page, base, email):
    """Replace the person signed in on this browser with another, and check who is signed in.

    The first person's page is closed before the cookies are cleared: a page still loading would send its
    responses back with the first person's cookies and the sign-in would be a no-op. The account menu says
    whose session the next requests carry, so a 404 for the second person is a real cross-organisation answer.
    """
    page.close()
    context.clear_cookies()
    page = context.new_page()
    _login(page, base, email)
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert email in (page.locator("#user-menu-btn").get_attribute("aria-label") or ""), (
        "expected %s to be signed in" % email)
    return page


def test_architect_corrects_and_removes_imported_elements(browser, live_server, seeded, tmp_path):
    tag = uuid.uuid4().hex[:6]
    file_path = tmp_path / "kinds.xml"
    file_path.write_text(_XML.format(tag=tag), encoding="utf-8")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        _import(page, live_server, str(file_path))
        _finish_import(page)

        names = ["Faster onboarding", "Onboarding bundle", "Hosting service", "Onboarding portal"]
        for old in names:
            old_name = "%s %s" % (old, tag)
            address = _element_address(page, live_server, old_name)
            assert address, "the model has no imported %r" % old_name
            page.goto(live_server + address, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            expect(page.locator("h1").first).to_contain_text(old_name)
            _shot(page, "open-" + old.replace(" ", "-"))
            page.get_by_role("link", name="Edit", exact=True).first.click()
            page.wait_for_url("**/edit**", timeout=PAGE_TIMEOUT)
            _shot(page, "edit-" + old.replace(" ", "-"))
            new_name = "%s corrected %s" % (old, tag)
            name_box = page.locator("input[x-model='formData.name']")
            expect(name_box).to_be_visible(timeout=PAGE_TIMEOUT)
            name_box.fill(new_name)
            page.locator("#archimate-form-modal").get_by_role("button", name="Update").click()
            page.wait_for_url(lambda u: "/edit" not in u, timeout=PAGE_TIMEOUT)
            page.reload(wait_until="networkidle")
            expect(page.locator("h1").first).to_contain_text(new_name)
            assert "Not recorded" not in page.locator("[data-testid='kpi-updated']").inner_text()
            # The record's raw column names are kept to the platform administrator's Technical details panel.
            shown = page.evaluate(
                "() => { const c = document.body.cloneNode(true);"
                " c.querySelectorAll(\"[data-testid='technical-details']\").forEach(e => e.remove());"
                " return c.innerText; }"
            )
            assert "Element recorded at" not in shown
            assert "0001" not in page.inner_text("body") and "Inferred from" not in page.inner_text("body")
            assert _element_address(page, live_server, new_name) == address

        # Delete one: the page, search and the list no longer have it.
        doomed = "Hosting service corrected %s" % tag
        url = _element_address(page, live_server, doomed)
        page.goto(live_server + url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.click("[data-testid='delete-element']")
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("This cannot be undone", timeout=PAGE_TIMEOUT)
        _shot(page, "delete-confirm")
        dialog.get_by_role("button", name="Confirm").or_(dialog.get_by_role("button", name="Delete")).first.click()
        page.wait_for_url(lambda u: u.rstrip("/") != (live_server + url).rstrip("/"), timeout=PAGE_TIMEOUT)
        assert _find(page, live_server, doomed) == []
        assert _element_address(page, live_server, doomed) is None
        gone = page.goto(live_server + url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert gone.status == 404
    finally:
        context.close()


def test_other_organisation_cannot_edit_or_delete(browser, live_server, seeded, tmp_path):
    tag = uuid.uuid4().hex[:6]
    file_path = tmp_path / "kinds.xml"
    file_path.write_text(_XML.format(tag=tag), encoding="utf-8")
    other = _second_org_architect(tag)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        _import(page, live_server, str(file_path))
        _finish_import(page)
        url = _element_address(page, live_server, "Onboarding bundle %s" % tag)
        element_id = url.rstrip("/").split("/")[-1].split("?")[0]
        layer = url.split("/")[2]
        page = _sign_in_as(context, page, live_server, other)
        edit = page.goto("%s/architecture/%s/Product/%s/edit?ae=1" % (live_server, layer, element_id),
                         wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert edit.status == 404
        token = page.evaluate("document.querySelector('meta[name=csrf-token]') ? document.querySelector('meta[name=csrf-token]').content : ''")
        resp = page.request.post("%s/architecture/%s/Product/%s/delete?ae=1" % (live_server, layer, element_id),
                                 headers={"X-CSRFToken": token, "Content-Type": "application/json"})
        assert resp.status == 404
        check = page.request.get("%s/architecture/%s/Product/%s/delete-check?ae=1" % (live_server, layer, element_id))
        assert check.status == 404
        page = _sign_in_as(context, page, live_server, seeded["emails"]["solution_architect"])
        assert _element_address(page, live_server, "Onboarding bundle %s" % tag) == url
    finally:
        context.close()


def test_import_says_what_it_matched(browser, live_server, seeded, tmp_path):
    tag = uuid.uuid4().hex[:6]
    first = tmp_path / "first.xml"
    first.write_text(_XML.format(tag=tag), encoding="utf-8")
    second = tmp_path / "second.xml"
    second.write_text(_SIMILAR.format(tag=tag), encoding="utf-8")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        _import(page, live_server, str(first))
        _finish_import(page)
        _import(page, live_server, str(second))
        expect(page.locator("[data-testid='preview-decision-reason']").first).to_contain_text(
            "Onboarding bundle %s" % tag, timeout=PAGE_TIMEOUT)
        result = _finish_import(page)
        match = page.locator("[data-testid='import-match']")
        expect(match).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(match).to_contain_text("Onboarding bundle %s - extended" % tag)
        expect(result.locator("[data-testid='import-matches']")).to_contain_text("Nothing is merged on its own")
        _shot(page, "import-match")
        link = match.get_by_role("link")
        link.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("h1").first).to_contain_text("Onboarding bundle %s" % tag)
    finally:
        context.close()
