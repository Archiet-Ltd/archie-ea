"""Journey, in a browser, as the enterprise architect: the owner of an
application, a capability-free element page and a business process can be
assigned, changed and cleared; the owner is shown once, with the date the
assignment was made; and the search picker works from the keyboard and tells a
screen reader what it is.
"""

import re
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"  # the signed-in persona for every journey here
FIRST = "application_manager"
SECOND = "cto"


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateElement
    from app.models.process_data import BusinessProcess

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        top = db.session.execute(db.text(
            "SELECT GREATEST(COALESCE((SELECT MAX(id) FROM archimate_elements), 0),"
            " COALESCE((SELECT MAX(id) FROM application_components), 0))"
        )).scalar()
        element = ArchiMateElement(
            id=top + 1000, name="Change app %s" % suffix, type="ApplicationComponent",
            layer="application", organization_id=org_id,
        )
        db.session.add(element)
        db.session.flush()
        component = ApplicationComponent(
            name=element.name, organization_id=org_id, lifecycle_status="operational",
            archimate_element_id=element.id,
        )
        process = BusinessProcess(
            name="Change process %s" % suffix, organization_id=org_id, status="active",
        )
        db.session.add_all([component, process])
        db.session.commit()
        ids = {"element_id": element.id, "application_id": component.id,
               "process_id": process.id}
        db.session.remove()
    return ids


def _name(org, role):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        name = db.session.get(User, org["user_ids"][role]).full_name()
        db.session.remove()
    return name


@pytest.fixture
def orgs():
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False, extra_roles=(FIRST, SECOND))
        org["ids"] = _seed(org["org_id"])
        out.append(org)
    return out


def _choose_and_save(page, name):
    box = page.locator("[data-owner-picker] [data-owner-search]")
    assert box.count() == 1, "the page offers no owner step"
    box.fill(name.split()[-1])
    page.locator("[data-owner-picker] [role=option]", has_text=name).first.click()
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/owners" in r.url
        ) as saved:
            page.locator("[data-owner-picker] [data-owner-save]").click()
    assert saved.value.status == 201


def _clear(page):
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        with page.expect_response(
            lambda r: r.request.method == "DELETE" and "/owners/" in r.url
        ) as cleared:
            page.get_by_role("button", name=re.compile("^Clear owner")).click()
            page.get_by_role("button", name=re.compile("^(Clear|Confirm|Yes|OK)")).last.click()
    assert cleared.value.status in (200, 204)


def _open(page, url):
    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)


def test_architect_changes_then_clears_owner_and_every_page_follows(page, live_server, orgs):
    org_a, org_b = orgs
    ids = org_a["ids"]
    first, second = _name(org_a, FIRST), _name(org_a, SECOND)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    app_url = "%s/applications/%d" % (live_server, ids["application_id"])
    element_url = "%s/architecture/application/ApplicationComponent/%d" % (
        live_server, ids["element_id"])

    _open(page, app_url)
    _choose_and_save(page, first)

    # Once set, the step offers to change it, and the owner is shown once with
    # the date the assignment was made.
    _open(page, app_url)
    card = page.locator("[data-testid='application-owner']")
    assert card.inner_text().count(first) == 1, card.inner_text()
    assert "Assigned" in card.inner_text()
    ownership = page.locator("[data-slot='card']", has=page.locator("[data-testid='application-owner']"))
    assert ownership.inner_text().count(first) == 1, (
        "the Ownership section names the owner more than once:\n" + ownership.inner_text())
    today = datetime.utcnow().strftime("%d %b %Y")
    assert today in card.inner_text(), "the assignment's own date is not shown"
    assert page.get_by_role("button", name=re.compile("^Clear owner")).count() == 1
    assert page.locator("[data-owner-picker]").inner_text().startswith("Change owner")

    # Change to the second person: the first is gone from both pages.
    _choose_and_save(page, second)
    _open(page, app_url)
    text = page.locator("[data-testid='application-owner']").inner_text()
    assert second in text and first not in text, text
    _open(page, element_url)
    cell = page.locator("[data-testid='element-owner']").inner_text()
    assert second in cell and first not in cell, cell
    assert today in cell, "the element page does not show when the owner was assigned"

    # Clear: both pages say no owner and offer the step again.
    _clear(page)
    _open(page, element_url)
    cell = page.locator("[data-testid='element-owner']")
    assert "No owner recorded" in cell.inner_text()
    assert cell.locator("[data-owner-picker]").inner_text().startswith("Assign owner")
    _open(page, app_url)
    assert "No owner recorded" in page.locator("[data-testid='application-owner-empty']").inner_text()

    # The other organisation's pages never saw any of it.
    page.goto(live_server + "/account/logout", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    fresh_org.sign_in(page, live_server, org_b["emails"][ARCHITECT])
    _open(page, "%s/applications/%d" % (live_server, org_b["ids"]["application_id"]))
    assert first not in page.inner_text("main") and second not in page.inner_text("main")


def test_owner_picker_is_a_combobox_driven_by_the_keyboard(page, live_server, orgs):
    org_a = orgs[0]
    ids = org_a["ids"]
    second = _name(org_a, SECOND)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    _open(page, "%s/applications/%d" % (live_server, ids["application_id"]))

    box = page.locator("[data-owner-picker] [data-owner-search]")
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-expanded") == "false"
    box.focus()
    page.keyboard.type(second.split()[-1])
    page.locator("[data-owner-picker] [role=listbox] [role=option]").first.wait_for(
        state="visible", timeout=PAGE_TIMEOUT)
    assert box.get_attribute("aria-expanded") == "true"
    assert box.get_attribute("aria-controls") == page.locator("[data-owner-picker] [role=listbox]").get_attribute("id")
    assert "found" in page.locator("[data-owner-picker] [role=status]").inner_text()

    page.keyboard.press("ArrowDown")
    active = box.get_attribute("aria-activedescendant")
    assert active, "the arrow key does not mark an option as the active one"
    assert page.locator("#%s" % active).get_attribute("aria-selected") == "true"
    page.keyboard.press("Enter")
    assert box.input_value() == second
    save = page.locator("[data-owner-picker] [data-owner-save]")
    assert save.is_enabled()

    # Tab to Save owner and press it with the keyboard.
    page.keyboard.press("Tab")
    assert save.evaluate("el => el === document.activeElement")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.keyboard.press("Enter")
    assert second in page.locator("[data-testid='application-owner']").inner_text()


def test_business_process_page_has_the_owner_step(page, live_server, orgs):
    org_a = orgs[0]
    first = _name(org_a, FIRST)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    url = "%s/architecture/business/BusinessProcess/%d" % (live_server, org_a["ids"]["process_id"])
    _open(page, url)
    cell = page.locator("[data-testid='element-owner']")
    assert "No owner recorded" in cell.inner_text()
    _choose_and_save(page, first)
    _open(page, url)
    cell = page.locator("[data-testid='element-owner']")
    assert first in cell.inner_text()
    assert page.locator("main").inner_text().count(first) == 1, "the process owner is shown twice"
    _clear(page)
    _open(page, url)
    assert "No owner recorded" in page.locator("[data-testid='element-owner']").inner_text()
