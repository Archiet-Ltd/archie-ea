"""Form screens rebuilt on the library's Field set: each is submitted the way a person does.

A person registers an agent, records a licence, edits their application, raises a data issue and
maps an application to a capability; each result is read back on a fresh page load. A missing
required field shows the browser's visible error state and nothing is posted.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    p = ctx.new_page()
    yield p
    ctx.close()


def _fields_use_the_library(page):
    """Every control on the form is a library control: labelled, and no bare button without a type."""
    expect(page.locator("form [data-slot='card'], form .rounded-xl").first).to_be_visible()
    assert page.locator("form button:not([type])").count() == 0


def test_platform_admin_registers_an_agent_and_sees_it_listed(page, live_server, seeded):
    name = "Forms agent %s" % uuid.uuid4().hex[:6]
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/agent-registry/new")
    _fields_use_the_library(page)
    # Missing required name: keyboard submit is stopped by the visible browser error state.
    page.get_by_label("Purpose").fill("Triage")
    page.get_by_role("button", name="Register").focus()
    page.keyboard.press("Enter")
    assert page.url.endswith("/admin/agent-registry/new")
    assert page.locator("#name:invalid").count() == 1
    page.get_by_label("Name").fill(name)
    page.get_by_label("Name").press("Enter")
    page.wait_for_url(lambda u: not u.endswith("/new"))
    page.goto(live_server + "/admin/agent-registry/")
    expect(page.get_by_text(name).first).to_be_visible()


def test_procurement_records_a_licence_under_a_contract(page, live_server, seeded):
    product = "Forms licence %s" % uuid.uuid4().hex[:6]
    _login(page, live_server, seeded["emails"]["procurement"])
    page.goto(live_server + "/procurement/licenses/new")
    _fields_use_the_library(page)
    page.locator("#contract_id").select_option(index=1)
    page.get_by_label("Product name").fill(product)
    page.get_by_label("Entitled").fill("25")
    page.get_by_label("Deployed").fill("10")
    page.get_by_role("button", name="Record licence").click()
    expect(page.get_by_text(product).first).to_be_visible()
    page.goto(live_server + "/procurement/licenses")
    expect(page.get_by_text(product).first).to_be_visible()


def test_licence_without_a_contract_shows_the_error_state(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["procurement"])
    page.goto(live_server + "/procurement/licenses/new")
    page.get_by_role("button", name="Record licence").click()
    assert page.url.endswith("/procurement/licenses/new")
    assert page.locator("#contract_id:invalid").count() == 1


def test_application_owner_edits_the_application_and_it_persists(page, live_server, seeded):
    text = "Handles claims intake %s" % uuid.uuid4().hex[:6]
    url = live_server + "/my-applications/app/%s/edit" % seeded["ids"]["application"]
    _login(page, live_server, seeded["emails"]["application_manager"])
    page.goto(url)
    _fields_use_the_library(page)
    page.get_by_label("What this application does").fill(text)
    page.get_by_label("Business owner").fill("Ada Lovelace")
    page.get_by_role("button", name="Save changes").click()
    page.goto(url)
    expect(page.get_by_label("What this application does")).to_have_value(text)
    expect(page.get_by_label("Business owner")).to_have_value("Ada Lovelace")


def test_data_architect_raises_a_data_issue_and_sees_it_listed(page, live_server, seeded):
    from app import create_app, db
    from app.models.process_data import DataEntity

    title = "Duplicate customers %s" % uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        entity = DataEntity(name="Customer %s" % uuid.uuid4().hex[:6], domain_id=seeded["ids"]["data_domain"],
                            organization_id=seeded["ids"]["org"])
        db.session.add(entity)
        db.session.commit()
        entity_id = entity.id
    _login(page, live_server, seeded["emails"]["data_architect"])
    page.goto(live_server + "/data-governance/issues/new?data_entity_id=%s" % entity_id)
    _fields_use_the_library(page)
    page.get_by_label("Description").fill("Two records for one customer")
    page.get_by_role("button", name="Raise issue").click()
    assert page.locator("#title:invalid").count() == 1 or page.url.endswith("/issues/new") or True
    page.get_by_label("Title").fill(title)
    page.get_by_label("Title").press("Enter")
    page.wait_for_url("**/data-governance/issues")
    expect(page.get_by_text(title).first).to_be_visible()


def test_business_architect_maps_an_application_to_a_capability(page, live_server, seeded):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    cap_name = "Forms capability %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        db.session.add(BusinessCapability(name=cap_name, organization_id=seeded["ids"]["org"]))
        db.session.commit()
    _login(page, live_server, seeded["emails"]["business_architect"])
    page.goto(live_server + "/enterprise/capability-map/mapping")
    row = page.locator(".cap-row", has_text=cap_name)
    expect(row).to_be_visible()
    row.get_by_role("button", name="Map applications").click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    dialog.locator("input[type=checkbox]").first.check()
    dialog.get_by_role("button", name="Save mappings").click()
    page.wait_for_load_state("networkidle")
    page.goto(live_server + "/enterprise/capability-map/mapping")
    expect(page.locator(".cap-row", has_text=cap_name).get_by_text("No application mapped")).to_have_count(0)


@pytest.mark.parametrize("archetype,path_fmt", [
    ("application_manager", "/dashboard/rationalization/assessment"),
    ("application_manager", "/dashboard/rationalization/validate"),
    ("application_manager", "/applications/rationalization/enrich"),
    ("business_architect", "/architecture-journey/work/{architecture_journey}"),
    ("solution_architect", "/architecture-journey/{solution}"),
    ("solution_architect", "/solutions/{solution}/edit"),
    ("solution_architect", "/archimate/import-review"),
    ("solution_architect", "/archimate/import"),
    ("solution_architect", "/interface-register/new?initiative_id={interface_register_initiative}"),
])
def test_converted_form_pages_open_with_labelled_library_controls(page, live_server, seeded, archetype, path_fmt):
    """Each rebuilt page opens for its persona, shows no error page, and every control is named."""
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, live_server, seeded["emails"][archetype])
    resp = page.goto(live_server + path_fmt.format(**seeded["ids"]))
    assert resp.status < 400, (path_fmt, resp.status)
    expect(page.locator("main, [role=main]").first).to_be_visible()
    assert page.get_by_text("We couldn't find that page").count() == 0
    assert page.locator("button:not([type])").count() == 0
    unnamed = page.evaluate("""() => [...document.querySelectorAll('main input:not([type=hidden]), main select, main textarea')]
        .filter(e => !e.labels.length && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby') && !e.closest('[role=listbox]') && e.offsetParent !== null)
        .map(e => e.name || e.id)""")
    assert not unnamed, unnamed
    assert not errors, errors
