"""Journey, in a browser, as an enterprise architect whose platform role carries
no general write permission: the owner step is operable by keyboard alone, clear
goes through the permission matrix like assign, a process modelled only as an
element can be given an owner, and every save says it saved.
"""

import re

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org
from .test_owner_change_clear_journey import FIRST, _name, _open, _seed

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"  # the signed-in persona for every journey here


@pytest.fixture
def org():
    org = fresh_org.create_fresh_org(
        ARCHITECT, org_admin=False, extra_roles=(FIRST,), platform_role="Viewer")
    org["ids"] = _seed(org["org_id"])
    return org


def _element_only_process(org_id):
    from app import create_app, db
    from app.models.models import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        top = db.session.execute(db.text("SELECT COALESCE(MAX(id), 0) FROM archimate_elements")).scalar()
        element = ArchiMateElement(
            id=top + 2000, name="Release element-only batch", type="BusinessProcess",
            layer="business", organization_id=org_id,
        )
        db.session.add(element)
        db.session.commit()
        element_id = element.id
        db.session.remove()
    return element_id


def _toast(page, text):
    page.get_by_text(text, exact=True).first.wait_for(state="visible", timeout=PAGE_TIMEOUT)


def _keyboard_assign(page, name):
    box = page.locator("[data-owner-picker] [data-owner-search]")
    box.focus()
    page.keyboard.type(name.split()[-1])
    # No waiting for the list: the arrow key asks for it and lands on the first person.
    page.keyboard.press("ArrowDown")
    page.locator("[data-owner-picker] [role=option][aria-selected=true]").wait_for(
        state="visible", timeout=PAGE_TIMEOUT)
    assert box.get_attribute("aria-activedescendant")
    page.keyboard.press("Enter")
    assert box.input_value() == name
    save = page.locator("[data-owner-picker] [data-owner-save]")
    assert save.is_enabled()
    page.keyboard.press("Tab")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.keyboard.press("Enter")


def _clear(page):
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        with page.expect_response(
            lambda r: r.request.method == "DELETE" and "/owners/" in r.url
        ) as cleared:
            page.get_by_role("button", name=re.compile("^Clear owner")).click()
            page.get_by_role("button", name=re.compile("^(Clear|Confirm|Yes|OK)")).last.click()
    assert cleared.value.status in (200, 204), cleared.value.status


def test_keyboard_assign_confirms_and_clear_moves_the_list_summary(page, live_server, org):
    ids = org["ids"]
    owner = _name(org, FIRST)
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    app_url = "%s/applications/%d" % (live_server, ids["application_id"])
    element_url = "%s/architecture/application/ApplicationComponent/%d" % (live_server, ids["element_id"])

    _open(page, live_server + "/applications/")
    before = page.locator("[data-testid=dq-no-owner]").inner_text()
    assert before.startswith("1 of 1"), before

    _open(page, app_url)
    _keyboard_assign(page, owner)
    _toast(page, "Owner saved")
    assert owner in page.locator("[data-testid='application-owner']").inner_text()
    _open(page, live_server + "/applications/")
    assert page.locator("[data-testid^=app-owner]").first.inner_text().strip() == owner or owner in page.locator("main").inner_text()
    assert page.locator("[data-testid=dq-no-owner]").count() == 0 or page.locator("[data-testid=dq-no-owner]").inner_text().startswith("0 of")

    _open(page, app_url)
    _clear(page)
    _toast(page, "Owner cleared")
    assert "No owner recorded" in page.locator("[data-testid='application-owner-empty']").inner_text()
    _open(page, live_server + "/applications/")
    assert page.locator("[data-testid=dq-no-owner]").inner_text() == before

    # The element page: keyboard assign, then Escape closes the list.
    _open(page, element_url)
    box = page.locator("[data-owner-picker] [data-owner-search]")
    box.focus()
    page.keyboard.type(owner.split()[-1])
    page.keyboard.press("ArrowDown")
    page.locator("[data-owner-picker] [role=option][aria-selected=true]").wait_for(
        state="visible", timeout=PAGE_TIMEOUT)
    page.keyboard.press("Escape")
    assert box.get_attribute("aria-expanded") == "false"
    assert page.locator("[data-owner-picker] [role=option]").count() == 0
    box.fill("")
    _keyboard_assign(page, owner)
    _toast(page, "Owner saved")
    assert owner in page.locator("[data-testid='element-owner']").inner_text()
    _clear(page)
    _toast(page, "Owner cleared")
    assert "No owner recorded" in page.locator("[data-testid='element-owner']").inner_text()


def test_process_modelled_only_as_an_element_gets_and_loses_an_owner(page, live_server, org):
    owner = _name(org, FIRST)
    element_id = _element_only_process(org["org_id"])
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    url = "%s/architecture/business/BusinessProcess/%d" % (live_server, element_id)
    _open(page, url)
    cell = page.locator("[data-testid='element-owner']")
    assert "No owner recorded" in cell.inner_text()
    _keyboard_assign(page, owner)
    _toast(page, "Owner saved")
    cell = page.locator("[data-testid='element-owner']")
    assert owner in cell.inner_text() and "Assigned" in cell.inner_text()
    assert page.locator("main").inner_text().count(owner) == 1
    _clear(page)
    _toast(page, "Owner cleared")
    assert "No owner recorded" in page.locator("[data-testid='element-owner']").inner_text()
