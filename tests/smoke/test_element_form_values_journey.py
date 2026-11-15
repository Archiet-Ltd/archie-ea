"""Journey: the element form keeps every value it offers, and any element can be renamed.

An architect has a work package with no target date and one with a date. From each page they click Edit,
change only the name, save and see the new name after a reload; the dated one shows its date as 2027-03-15 in
the box. They then fill in three fields the form always offered but never stored (a role's assigned actor type,
a node's location, an application service's protocol), save, and see each value on the element's page under its
label and again in the form. A value stored outside the field's options is shown as the selected choice, not as
"Select...", and the form has one close button.
"""
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_WALK_DIR", "test-results/element-form-values")

FORM = "#archimate-form-modal"


def _shot(page, name, target=None):
    """Save a screenshot for the walk: the page, or one block of it scrolled into view."""
    os.makedirs(WALK_DIR, exist_ok=True)
    path = os.path.join(WALK_DIR, name + ".png")
    if target is not None:
        target.scroll_into_view_if_needed()
        target.screenshot(path=path)
    else:
        page.screenshot(path=path, full_page=True)


def _dark(page):
    page.evaluate("document.documentElement.classList.add('dark')")


def _csrf(page):
    return page.evaluate(
        "document.querySelector('meta[name=csrf-token]') ? document.querySelector('meta[name=csrf-token]').content : ''"
    )


def _create(page, base, kind, layer, name, values=None):
    resp = page.request.post(
        "%s/architecture/%s/%s/new" % (base, layer, kind),
        data={"name": name, "description": "Kept description", **(values or {})},
        headers={"X-CSRFToken": _csrf(page), "Content-Type": "application/json"},
    )
    assert resp.ok, (resp.status, resp.text()[:200])
    body = resp.json()
    assert body.get("success"), body
    return "%s/architecture/%s/%s/%s" % (base, layer, kind, body["id"])


def _store_outside_the_options(record_url, value):
    """Give a Goal a category its form does not offer, as an older import left it: the form refuses to create one."""
    from app import create_app, db
    from app.modules.architecture.routes.archimate_crud.routes import MODEL_REGISTRY

    goal_class = MODEL_REGISTRY["Goal"]
    with create_app("testing").app_context():
        db.session.execute(
            db.update(goal_class).where(goal_class.id == int(record_url.rsplit("/", 1)[1])).values(category=value)
        )
        db.session.commit()
        db.session.remove()


def _open_edit(page, detail):
    page.goto(detail, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Edit", exact=True).first.click()
    page.wait_for_url("**/edit**", timeout=PAGE_TIMEOUT)
    expect(page.locator("input[x-model='formData.name']")).to_be_visible(timeout=PAGE_TIMEOUT)


def _select_for(page, label):
    return page.locator(FORM).locator("xpath=.//label[normalize-space()='%s']/following::select[1]" % label)


def _save(page):
    page.locator(FORM).get_by_role("button", name="Update").click()
    page.wait_for_url(lambda u: "/edit" not in u, timeout=PAGE_TIMEOUT)
    page.reload(wait_until="networkidle")


def _fact(page, label):
    return page.locator("[data-testid='about-card'] dl > div", has=page.locator("dt", has_text=label)).locator("dd")


def test_rename_a_work_package_with_and_without_a_date(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        undated = _create(page, live_server, "WorkPackage", "implementation", "Undated package %s" % tag)
        dated = _create(page, live_server, "WorkPackage", "implementation", "Dated package %s" % tag,
                        {"target_date": "2027-03-15"})

        _open_edit(page, undated)
        expect(page.get_by_label("Target Date")).to_have_value("")
        _shot(page, "rename-empty-fields")
        page.locator("input[x-model='formData.name']").fill("Undated package renamed %s" % tag)
        _save(page)
        expect(page.locator("h1").first).to_contain_text("Undated package renamed %s" % tag)

        _open_edit(page, dated)
        # The date reaches its box as a plain date, not as a weekday and a time.
        expect(page.get_by_label("Target Date")).to_have_value("2027-03-15")
        _shot(page, "stored-date")
        page.locator("input[x-model='formData.name']").fill("Dated package renamed %s" % tag)
        _save(page)
        expect(page.locator("h1").first).to_contain_text("Dated package renamed %s" % tag)
        _open_edit(page, dated)
        expect(page.get_by_label("Target Date")).to_have_value("2027-03-15")
    finally:
        context.close()


def test_fields_the_form_always_offered_are_stored_and_shown(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        role = _create(page, live_server, "BusinessRole", "business", "Claims handler %s" % tag)
        node = _create(page, live_server, "Node", "technology", "Primary node %s" % tag)
        service = _create(page, live_server, "ApplicationService", "application", "Quote service %s" % tag)

        _open_edit(page, role)
        _select_for(page, "Assigned Actor Type").select_option("Team")
        _save(page)
        expect(_fact(page, "Assigned Actor Type")).to_have_text("Team")
        _shot(page, "stored-no-column-field", page.locator("[data-testid='about-card']"))
        _open_edit(page, role)
        expect(_select_for(page, "Assigned Actor Type")).to_have_value("Team")

        _open_edit(page, node)
        page.get_by_label("Location / Data Centre").fill("Dublin data centre")
        _save(page)
        expect(_fact(page, "Location / Data Centre")).to_have_text("Dublin data centre")
        _open_edit(page, node)
        expect(page.get_by_label("Location / Data Centre")).to_have_value("Dublin data centre")

        _open_edit(page, service)
        protocol = _select_for(page, "Protocol")
        protocol.select_option(index=1)
        chosen = protocol.evaluate("e => e.value")
        label = protocol.evaluate("e => e.selectedOptions[0].textContent.trim()")
        tier = _select_for(page, "SLA Tier")
        tier.select_option(index=1)
        tier_label = tier.evaluate("e => e.selectedOptions[0].textContent.trim()")
        _save(page)
        expect(_fact(page, "Protocol")).to_have_text(label)
        expect(_fact(page, "SLA Tier")).to_have_text(tier_label)
        _open_edit(page, service)
        expect(_select_for(page, "Protocol")).to_have_value(chosen)

        # A rename on its own keeps them.
        page.locator("input[x-model='formData.name']").fill("Quote service renamed %s" % tag)
        _save(page)
        expect(_fact(page, "Protocol")).to_have_text(label)
    finally:
        context.close()


def test_stored_value_outside_the_options_is_shown_and_the_form_has_one_close_button(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        goal = _create(page, live_server, "Goal", "motivation", "Regulated goal %s" % tag, {"category": "growth"})
        _store_outside_the_options(goal, "regulator")
        _open_edit(page, goal)
        category = _select_for(page, "Category")
        expect(category).to_have_value("regulator")
        assert category.evaluate("e => e.selectedOptions[0].textContent.trim()") == "regulator"
        _shot(page, "stored-outside-options")
        expect(page.locator(FORM).get_by_role("button", name="Close")).to_have_count(1)
        _shot(page, "one-close-button")
        page.locator("input[x-model='formData.name']").fill("Regulated goal renamed %s" % tag)
        _save(page)
        _open_edit(page, goal)
        expect(_select_for(page, "Category")).to_have_value("regulator")
    finally:
        context.close()


def test_the_form_is_usable_at_phone_width_in_dark_mode(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/architecture/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        undated = _create(page, live_server, "WorkPackage", "implementation", "Phone package %s" % tag)
        dated = _create(page, live_server, "Plateau", "implementation", "Phone plateau %s" % tag,
                        {"target_date": "2027-03-15"})
        node = _create(page, live_server, "Node", "technology", "Phone node %s" % tag, {"datacenter": "Cork"})
        goal = _create(page, live_server, "Goal", "motivation", "Phone goal %s" % tag, {"category": "growth"})
        _store_outside_the_options(goal, "regulator")

        _open_edit(page, undated)
        _dark(page)
        _shot(page, "phone-dark-rename-empty-fields")
        page.locator("input[x-model='formData.name']").fill("Phone package renamed %s" % tag)
        _save(page)
        expect(page.locator("h1").first).to_contain_text("Phone package renamed %s" % tag)

        _open_edit(page, dated)
        _dark(page)
        expect(page.get_by_label("Target Date")).to_have_value("2027-03-15")
        _shot(page, "phone-dark-stored-date")

        page.goto(node, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        _dark(page)
        expect(_fact(page, "Location / Data Centre")).to_have_text("Cork")
        _shot(page, "phone-dark-stored-no-column-field", page.locator("[data-testid='about-card']"))

        _open_edit(page, goal)
        _dark(page)
        expect(_select_for(page, "Category")).to_have_value("regulator")
        _shot(page, "phone-dark-stored-outside-options")
        expect(page.locator(FORM).get_by_role("button", name="Close")).to_have_count(1)
        _shot(page, "phone-dark-one-close-button")
    finally:
        context.close()
