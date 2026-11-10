"""Journey: renaming an element keeps every value the person did not change.

An architect has a Goal, a Driver and a Technology Service with typed values filled in. From each element's
page they click Edit, change only the name, save, and open Edit again: every typed value is still there. On an
element brought in from a model file the form offers the same typed values and they are really stored; the
person's own description that happens to begin "Inferred from" is shown as their description; "Full Edit
Page" is a link that opens the same element; and the date card says it is the date recorded, not a last edit.
A match between two elements of one file is reported as matched, with a link to a page that has content.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login
from .test_imported_element_editable_journey import _XML, _finish_import, _import

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_WALK_DIR", "test-results/element-rename")

_PAIR = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-model-pair-{tag}">
  <name xml:lang="en">Pair {tag}</name>
  <elements>
    <element identifier="id-a" xsi:type="Goal"><name xml:lang="en">Alpha {tag}</name></element>
    <element identifier="id-b" xsi:type="Goal"><name xml:lang="en">Alpha {tag} beta</name></element>
  </elements>
</model>
"""

# element type, layer, typed values stored when it is created
_TYPED = [
    ("Goal", "motivation", {"goal_type": "strategic", "category": "growth"}),
    ("Driver", "motivation", {"driver_type": "regulatory", "source": "external"}),
    ("TechnologyService", "technology", {"service_type": "storage"}),
]


def _shot(page, name):
    os.makedirs(WALK_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _csrf(page):
    return page.evaluate(
        "document.querySelector('meta[name=csrf-token]') ? document.querySelector('meta[name=csrf-token]').content : ''"
    )


def _create(page, base, kind, layer, name, typed):
    resp = page.request.post(
        "%s/architecture/%s/%s/new" % (base, layer, kind),
        data={"name": name, "description": "Kept description", **typed},
        headers={"X-CSRFToken": _csrf(page), "Content-Type": "application/json"},
    )
    assert resp.ok, (resp.status, resp.text()[:200])
    body = resp.json()
    assert body.get("success"), body
    return body["id"]


def _typed_values_in_form(page, count):
    """Value shown by each of the first ``count`` typed fields of the edit form (the type's own fields; the
    architecture state every type shares comes last)."""
    boxes = page.locator("#archimate-form-modal select[x-model='formData[fld.name]']")
    expect(boxes.first).to_be_visible(timeout=PAGE_TIMEOUT)
    return boxes.evaluate_all("els => els.map(e => e.value)")[:count]


def _open_edit_from_page(page):
    page.get_by_role("link", name="Edit", exact=True).first.click()
    page.wait_for_url("**/edit**", timeout=PAGE_TIMEOUT)
    expect(page.locator("input[x-model='formData.name']")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_rename_keeps_typed_values(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for kind, layer, typed in _TYPED:
            name = "%s kept %s" % (kind, tag)
            element_id = _create(page, live_server, kind, layer, name, typed)
            detail = "%s/architecture/%s/%s/%s" % (live_server, layer, kind, element_id)
            page.goto(detail, wait_until="networkidle", timeout=PAGE_TIMEOUT)
            _shot(page, "before-%s" % kind)
            _open_edit_from_page(page)
            before = _typed_values_in_form(page, len(typed))
            assert before == list(typed.values()), (kind, before)
            new_name = "%s renamed %s" % (kind, tag)
            page.locator("input[x-model='formData.name']").fill(new_name)
            page.locator("#archimate-form-modal").get_by_role("button", name="Update").click()
            page.wait_for_url(lambda u: "/edit" not in u, timeout=PAGE_TIMEOUT)
            page.reload(wait_until="networkidle")
            expect(page.locator("h1").first).to_contain_text(new_name)
            expect(page.locator("[data-testid='element-description']")).to_contain_text("Kept description")
            _shot(page, "after-%s" % kind)
            _open_edit_from_page(page)
            assert _typed_values_in_form(page, len(typed)) == list(typed.values()), kind
    finally:
        context.close()


def test_imported_element_keeps_what_the_form_offers(browser, live_server, seeded, tmp_path):
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
        name = "Faster onboarding %s" % tag
        resp = page.request.get(live_server + "/archimate/api/elements/search?q=" + name.replace(" ", "%20"))
        body = resp.json()
        row = [r for r in (body.get("data") or body.get("elements") or body.get("results") or []) if r.get("name") == name][0]
        address = "%s/architecture/%s/element/%s" % (live_server, (row.get("layer") or "motivation").lower(), row["id"])
        page.goto(address, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        # The date card says what the date is: when it was added, not a last edit.
        card = page.locator("[data-testid='kpi-updated']").inner_text()
        assert "Recorded" in card and "Last updated" not in card, card
        _open_edit_from_page(page)
        # Full Edit Page is a link that lands on this same element's edit page.
        full = page.get_by_role("link", name="Full Edit Page")
        expect(full).to_have_attribute("href", "/architecture/motivation/Goal/%s/edit?ae=1" % row["id"])
        # The form offers Goal Type and Category; choose both, give a description that starts "Inferred from".
        selects = page.locator("#archimate-form-modal select[x-model='formData[fld.name]']")
        expect(selects).to_have_count(3, timeout=PAGE_TIMEOUT)
        selects.nth(0).select_option("strategic")
        selects.nth(1).select_option("growth")
        page.locator("textarea[x-model='formData.description']").fill("Inferred from customer interviews in March")
        page.locator("#archimate-form-modal").get_by_role("button", name="Update").click()
        page.wait_for_url(lambda u: "/edit" not in u, timeout=PAGE_TIMEOUT)
        page.reload(wait_until="networkidle")
        # The person's own words are shown as their description, and the page says where they came from.
        expect(page.locator("[data-testid='element-description']")).to_contain_text(
            "Inferred from customer interviews in March")
        expect(page.locator("[data-testid='element-description-origin']")).to_contain_text("This element was imported from")
        _shot(page, "imported-after")
        # What the form offered was stored: open it again.
        _open_edit_from_page(page)
        assert _typed_values_in_form(page, 2) == ["strategic", "growth"]
        full = page.get_by_role("link", name="Full Edit Page")
        full.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("input[x-model='formData.name']")).to_have_value(name)
    finally:
        context.close()


def test_match_inside_one_file_is_reported_as_matched(browser, live_server, seeded, tmp_path):
    tag = uuid.uuid4().hex[:6]
    file_path = tmp_path / "pair.xml"
    file_path.write_text(_PAIR.format(tag=tag), encoding="utf-8")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        _import(page, live_server, str(file_path))
        _finish_import(page)
        match = page.locator("[data-testid='import-match']")
        expect(match).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(match).to_contain_text("created earlier in this file")
        assert "the stored" not in match.inner_text()
        # The link goes to the Goal's page under its own layer, and the page has the Goal's name.
        link = match.get_by_role("link")
        assert "/architecture/motivation/element/" in link.get_attribute("href")
        link.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("h1").first).to_contain_text("Alpha %s" % tag)
    finally:
        context.close()
