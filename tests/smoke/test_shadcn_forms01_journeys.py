"""Journeys for form screens rebuilt on the field set.

Each test starts from a person's goal, fills the form with the real controls, submits it and asserts the
result the person needs on the page it lands on. A missing required field is refused in the browser.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _watch(page):
    """Collect script errors so a failure says what the page did, not only what it lacked."""
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)
    return errors


def _shoot(page, slug):
    """Judge the open screen the way a person would: 1440 wide in light, then 390 wide in dark.

    Nothing may overflow the phone width. With FORMS01_SHOTS set to a folder the screens are also saved there.
    """
    import os

    folder = os.environ.get("FORMS01_SHOTS")
    for width, height, dark in ((390, 844, True), (1440, 900, False)):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("d => document.documentElement.classList.toggle('dark', d)", dark)
        page.wait_for_timeout(250)
        if width == 390 and not os.environ.get("FORMS01_SKIP_OVERFLOW"):
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert overflow <= 1, "%s overflows a 390 wide screen by %spx" % (slug, overflow)
        if folder:
            os.makedirs(folder, exist_ok=True)
            page.screenshot(path="%s/%s-%d-%s.png" % (folder, slug, width, "dark" if dark else "light"), full_page=True)


def _open(page, base, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def test_platform_admin_registers_an_agent_and_sees_it(browser, live_server):
    org = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["platform_admin"])
        _open(page, live_server, "/admin/agent-registry/new")
        form = page.locator("[data-testid=new-agent-form]")
        _shoot(page, "agent-registry-new")
        expect(form.get_by_label("Name")).to_be_visible()
        # A missing name is refused in the browser and nothing is sent.
        form.get_by_role("button", name="Register").click()
        assert "/new" in page.url
        assert form.get_by_label("Name").evaluate("e => e.validity.valueMissing")
        form.get_by_label("Name").fill("Support triage agent")
        form.get_by_label("Purpose").fill("Sorts incoming tickets by urgency")
        form.get_by_label("Name").press("Enter")
        page.wait_for_url(lambda u: "/new" not in u, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Support triage agent")
        _open(page, live_server, "/admin/agent-registry/")
        expect(page.locator("body")).to_contain_text("Support triage agent")
    finally:
        page.close()


def test_data_architect_raises_a_data_issue_and_it_is_listed(browser, live_server):
    org = create_fresh_org("data_architect", org_admin=True)
    from app import create_app, db
    from app.models.process_data import DataDomain, DataEntity

    app = create_app("testing")
    with app.app_context():
        domain = DataDomain(name="Customer", organization_id=org["org_id"])
        db.session.add(domain)
        db.session.flush()
        entity = DataEntity(name="Customer record", domain_id=domain.id, organization_id=org["org_id"])
        db.session.add(entity)
        db.session.commit()
        entity_id = entity.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["data_architect"])
        _open(page, live_server, "/data-governance/issues/new?data_entity_id=%d" % entity_id)
        form = page.locator("[data-testid=new-data-issue-form]")
        _shoot(page, "new-data-issue")
        expect(form.get_by_label("Data entity ID")).to_have_value(str(entity_id))
        form.get_by_label("Title").fill("Duplicate customer ids")
        form.get_by_label("Description").fill("Two systems issue ids for the same customer")
        form.get_by_role("button", name="Raise issue").click()
        page.wait_for_url("**/data-governance/issues", timeout=PAGE_TIMEOUT)
        issues = page.locator("[data-testid=data-issues]")
        expect(issues).to_contain_text("Duplicate customer ids")
        expect(issues).to_contain_text("Customer record")
        # A title is required: the browser refuses an empty one.
        _open(page, live_server, "/data-governance/issues/new?data_entity_id=%d" % entity_id)
        form.get_by_role("button", name="Raise issue").click()
        assert form.get_by_label("Title").evaluate("e => e.validity.valueMissing")
    finally:
        page.close()


def test_architect_records_a_review_outcome_and_it_shows_on_the_decision(browser, live_server):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    from datetime import date

    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision

    app = create_app("testing")
    with app.app_context():
        d = ArchitectureDecision(title="Adopt managed Postgres", status="accepted", organization_id=org["org_id"],
                                 review_date=date(2026, 9, 1), rationale="Fewer operational chores")
        db.session.add(d)
        db.session.commit()
        decision_id = d.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _open(page, live_server, "/architecture/decisions/%d/record-outcome" % decision_id)
        form = page.locator("[data-testid=record-outcome-form]")
        _shoot(page, "record-outcome")
        expect(form).to_contain_text("Review date")
        form.get_by_role("button", name="Record Outcome").click()
        assert form.locator("#review_outcome").evaluate("e => e.validity.valueMissing")
        form.locator("#review_outcome").fill("The decision still holds; renewed for a year.")
        form.locator("#next_review_date").fill("2027-09-01")
        form.get_by_role("button", name="Record Outcome").click()
        page.wait_for_url("**/architecture/decisions/%d" % decision_id, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("The decision still holds; renewed for a year.")
    finally:
        page.close()


def test_procurement_records_a_licence_and_it_is_judged_against_the_contract(browser, live_server):
    org = create_fresh_org("procurement", org_admin=True)
    from app import create_app, db
    from app.models.application_portfolio import VendorContract

    app = create_app("testing")
    with app.app_context():
        from datetime import date

        c = VendorContract(contract_name="Atlas platform agreement", organization_id=org["org_id"],
                           start_date=date(2026, 1, 1))
        db.session.add(c)
        db.session.commit()
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["procurement"])
        _open(page, live_server, "/procurement/licenses/new")
        form = page.locator("[data-testid=license-form]")
        _shoot(page, "license-form")
        # The contract is required: the browser refuses the form without one.
        form.get_by_role("button", name="Record licence").click()
        assert form.locator("#contract_id").evaluate("e => e.validity.valueMissing")
        form.locator("#contract_id").select_option(label="Atlas platform agreement")
        form.locator("#product_name").fill("Atlas Pro seats")
        form.locator("#quantity_entitled").fill("10")
        form.locator("#quantity_deployed").fill("14")
        form.locator("#unit_cost").fill("42.50")
        form.locator("#unit_cost").press("Enter")
        page.wait_for_url("**/procurement/licenses/*", timeout=PAGE_TIMEOUT)
        assert "/new" not in page.url
        body = page.locator("body")
        expect(body).to_contain_text("Atlas Pro seats")
        expect(body).to_contain_text("14")
        # Edit it: the same form comes back filled and a change is kept.
        licence_url = page.url
        _open(page, live_server, licence_url.replace(live_server, "") + "/edit")
        form = page.locator("[data-testid=license-form]")
        expect(form.locator("#product_name")).to_have_value("Atlas Pro seats")
        expect(form.locator("#quantity_deployed")).to_have_value("14")
        form.locator("#quantity_deployed").fill("8")
        form.get_by_role("button", name="Save changes").click()
        page.wait_for_url(licence_url, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("8")
    finally:
        page.close()


def test_application_owner_edits_their_application_and_the_change_shows(browser, live_server):
    org = create_fresh_org("application_manager", org_admin=True)
    from app import create_app, db
    from app.models.application_layer import ApplicationComponent
    from app.models.application_owner import ApplicationOwner

    app = create_app("testing")
    with app.app_context():
        comp = ApplicationComponent(name="Billing engine", organization_id=org["org_id"])
        db.session.add(comp)
        db.session.flush()
        db.session.add(ApplicationOwner(application_id=comp.id, user_id=org["user_ids"]["application_manager"],
                                        organization_id=org["org_id"]))
        db.session.commit()
        app_id = comp.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["application_manager"])
        _open(page, live_server, "/my-applications/app/%d/edit" % app_id)
        form = page.locator("[data-testid=app-edit-form]")
        _shoot(page, "application-edit")
        expect(form.get_by_label("Lifecycle")).to_be_visible()
        form.get_by_label("What this application does").fill("Invoices and collects subscription payments")
        form.get_by_label("Health").select_option("at_risk")
        form.get_by_label("Business owner").fill("Priya Raman")
        form.get_by_label("Technical lead").fill("Tomas Berg")
        form.get_by_role("button", name="Save changes").click()
        page.wait_for_url("**/my-applications/app/%d" % app_id, timeout=PAGE_TIMEOUT)
        body = page.locator("body")
        expect(body).to_contain_text("Invoices and collects subscription payments")
        # The edit form shows what was saved, and an unset value reads "Not set".
        _open(page, live_server, "/my-applications/app/%d/edit" % app_id)
        form = page.locator("[data-testid=app-edit-form]")
        expect(form.get_by_label("Business owner")).to_have_value("Priya Raman")
        expect(form.get_by_label("Technical lead")).to_have_value("Tomas Berg")
        expect(form.get_by_label("Deployment")).to_have_value("")
        expect(form.get_by_label("Deployment").locator("option:checked")).to_have_text("Not set")
    finally:
        page.close()


def test_solution_architect_registers_an_interface_between_two_applications(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True)
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchitectureModel
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.services.archimate_backbone import create_backbone_element

    app = create_app("testing")
    with app.app_context():
        arch = ArchitectureModel(name="Target architecture", organization_id=org["org_id"])
        db.session.add(arch)
        db.session.flush()
        initiative = TechnologyRoadmapInitiative(name="CRM consolidation", fiscal_year_start=2026,
                                                 fiscal_year_end=2027, architecture_id=arch.id)
        db.session.add(initiative)
        for name in ("Customer CRM", "Order hub"):
            element = create_backbone_element(element_type="ApplicationComponent", layer="Application",
                                              name=name, organization_id=org["org_id"])
            db.session.add(ApplicationComponent(name=name, organization_id=org["org_id"],
                                                archimate_element_id=element.id))
        db.session.commit()
        initiative_id = initiative.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _open(page, live_server, "/interface-register/new?initiative_id=%d" % initiative_id)
        form = page.locator("[data-testid=interface-form]")
        _shoot(page, "interface-form")
        # Required fields are refused by the browser until they are filled.
        form.get_by_role("button", name="Create interface").click()
        assert form.locator("#name").evaluate("e => e.validity.valueMissing")
        form.locator("#name").fill("Customer master replication")
        form.locator("#interface_type").select_option("REST")
        form.locator("#protocol").select_option("HTTPS")
        form.locator("#operational_status").select_option("retired")
        expect(form.locator("#retirement_date")).to_be_visible()
        form.locator("#operational_status").select_option("production")
        expect(form.locator("#retirement_date")).to_be_hidden()
        form.locator("#provider-search").fill("Customer")
        page.get_by_role("button", name="Customer CRM").click()
        form.locator("#consumer-search").fill("Order")
        page.get_by_role("button", name="Order hub").click()
        form.get_by_role("button", name="Create interface").click()
        page.wait_for_url("**/interface-register/?initiative_id=%d" % initiative_id, timeout=PAGE_TIMEOUT)
        body = page.locator("body")
        expect(body).to_contain_text("Customer master replication")
        # The coupling was stored: the edit form brings both applications back into their pickers.
        edit_href = page.locator("a[href*='/edit']").first.get_attribute("href")
        _open(page, live_server, edit_href)
        form = page.locator("[data-testid=interface-form]")
        expect(form.locator("#name")).to_have_value("Customer master replication")
        expect(form.locator("#interface_type")).to_have_value("REST")
        expect(form).to_contain_text("Customer CRM")
        expect(form).to_contain_text("Order hub")
    finally:
        page.close()


def test_solution_architect_edits_a_solution_and_the_record_shows_it(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True)
    from app import create_app, db
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        sol = Solution(name="Customer data platform", description="One view of the customer",
                       solution_type="Technology", business_domain="Customer", status="Planning",
                       complexity_level="Medium", business_value="Fewer duplicate records",
                       organization_id=org["org_id"], created_by_id=org["user_ids"]["solution_architect"])
        db.session.add(sol)
        db.session.commit()
        solution_id = sol.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["solution_architect"])
        _open(page, live_server, "/solutions/%d/edit" % solution_id)
        form = page.locator("[data-testid=solution-edit-form]")
        _shoot(page, "solution-edit")
        expect(form.locator("#edit-solution-name")).to_have_value("Customer data platform")
        expect(form.locator("#edit-solution-type")).to_have_value("Technology")
        # A required field left empty is refused in the browser.
        form.locator("#edit-solution-name").fill("")
        form.get_by_role("button", name="Update Solution").click()
        assert form.locator("#edit-solution-name").evaluate("e => e.validity.valueMissing")
        form.locator("#edit-solution-name").fill("Customer data platform v2")
        form.locator("#edit-solution-status").select_option("Active")
        form.locator("#edit-in-scope").fill("CRM, Billing engine")
        form.locator("#edit-technical-lead").fill("Tomas Berg")
        form.get_by_role("button", name="Update Solution").click()
        page.wait_for_url(lambda u: "/edit" not in u, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Customer data platform v2")
        _open(page, live_server, "/solutions/%d/edit" % solution_id)
        form = page.locator("[data-testid=solution-edit-form]")
        expect(form.locator("#edit-solution-status")).to_have_value("Active")
        expect(form.locator("#edit-in-scope")).to_have_value("CRM, Billing engine")
        expect(form.locator("#edit-technical-lead")).to_have_value("Tomas Berg")
    finally:
        page.close()


def test_business_architect_maps_an_application_to_a_capability(browser, live_server):
    org = create_fresh_org("business_architect", org_admin=True)
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        db.session.add(BusinessCapability(name="Order management", organization_id=org["org_id"]))
        db.session.add(ApplicationComponent(name="Order hub", organization_id=org["org_id"]))
        db.session.add(ApplicationComponent(name="Billing engine", organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    page = browser.new_page()
    try:
        errors = _watch(page)
        sign_in(page, live_server, org["emails"]["business_architect"])
        _open(page, live_server, "/enterprise/capability-map/mapping")
        row = page.locator(".cap-row", has_text="Order management")
        expect(row).to_contain_text("No application mapped")
        _shoot(page, "capability-mapping")
        row.get_by_role("button", name="Map applications to Order management").click()
        dialog = page.get_by_role("dialog")
        try:
            expect(dialog).to_be_visible()
        except AssertionError:
            raise AssertionError("dialog did not open; page errors: %s" % errors)
        dialog.get_by_label("Order hub").check()
        dialog.get_by_role("button", name="Save mappings").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => document.querySelector('.cap-row').innerText.includes('Order hub')", timeout=PAGE_TIMEOUT)
        # Reopen: the saved mapping is ticked and Cancel closes the dialog without a change.
        row = page.locator(".cap-row", has_text="Order management")
        row.get_by_role("button", name="Map applications to Order management").click()
        expect(page.get_by_role("dialog").get_by_label("Order hub")).to_be_checked()
        page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        expect(page.get_by_role("dialog")).to_be_hidden()
    finally:
        page.close()


def test_application_manager_enriches_an_application_from_the_list(browser, live_server):
    org = create_fresh_org("application_manager", org_admin=True)
    from app import create_app, db
    from app.models.application_layer import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        db.session.add(ApplicationComponent(name="Legacy ledger", organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    page = browser.new_page()
    try:
        errors = _watch(page)
        sign_in(page, live_server, org["emails"]["application_manager"])
        _open(page, live_server, "/applications/rationalization/enrich")
        row = page.locator("tr", has_text="Legacy ledger")
        expect(row).to_be_visible()
        _shoot(page, "enrich-applications")
        expect(row).to_contain_text("Not scored")
        # Every cell sits under its heading: eight headings, eight cells, the first one the row number.
        expect(page.locator("table th")).to_have_count(8)
        expect(row.locator("td")).to_have_count(8)
        expect(row.locator("td").first).to_have_text("1")
        save = row.get_by_role("button", name="Save")
        expect(save).to_be_disabled()
        row.get_by_label("Owner name").fill("Dana Okafor")
        row.locator("select").select_option("High")
        expect(save).to_be_enabled()
        save.click()
        expect(row.get_by_role("button", name="Saved")).to_be_visible()
        # Reload: the owner and criticality were stored and come back into the same cells.
        _open(page, live_server, "/applications/rationalization/enrich")
        row = page.locator("tr", has_text="Legacy ledger")
        expect(row.get_by_label("Owner name")).to_have_value("Dana Okafor")
        assert not [e for e in errors if "pageerror" in e], errors
    finally:
        page.close()


def test_architect_imports_a_csv_and_the_created_elements_are_listed(browser, live_server, tmp_path):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    csv = tmp_path / "elements.csv"
    csv.write_text("name,type,description,layer\nOrder service,ApplicationService,Takes orders,Application\n"
                   "Billing engine,ApplicationComponent,Invoices customers,Application\n")
    page = browser.new_page()
    try:
        errors = _watch(page)
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _open(page, live_server, "/archimate/import")
        upload = page.get_by_role("button", name="Upload & Create Elements")
        expect(upload).to_be_disabled()
        _shoot(page, "import-file")
        page.locator("#import-file").set_input_files(str(csv))
        expect(page.get_by_text("Detected:")).to_contain_text("Structured Data")
        expect(page.locator("#import-default-type")).to_be_visible()
        expect(page.locator("#import-strategy")).to_be_hidden()
        upload.click()
        expect(page.locator("body")).to_contain_text("2 elements created")
        created = page.locator("table")
        expect(created).to_contain_text("Order service")
        expect(created).to_contain_text("Billing engine")
        assert not [e for e in errors if "pageerror" in e], errors
    finally:
        page.close()


def test_architect_reviews_what_an_import_guessed_retypes_and_confirms(browser, live_server):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        for name in ("Order box", "Billing box"):
            db.session.add(ArchiMateElement(name=name, type="Node", layer="Technology", organization_id=org["org_id"],
                                            custom_properties={"lucid_type_source": "fallback", "lucid_class": "rect"}))
        db.session.commit()
        db.session.remove()
    page = browser.new_page()
    try:
        errors = _watch(page)
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _open(page, live_server, "/archimate/import-review")
        table = page.locator("[data-testid=review-table]")
        expect(table).to_contain_text("Order box")
        expect(table).to_contain_text("Billing box")
        expect(table.locator("th")).to_have_count(5)
        expect(table.locator("tbody tr").first.locator("td")).to_have_count(5)
        expect(page.get_by_text("0 selected")).to_be_visible()
        _shoot(page, "import-review")
        confirm = page.get_by_role("button", name="Confirm as correct")
        expect(confirm).to_be_disabled()
        # Pick one row and give it a type: the page reports the change and the queue still holds both.
        table.get_by_label("Select Order box").check()
        expect(page.get_by_text("1 selected")).to_be_visible()
        page.get_by_label("Change element type to").select_option("DataObject")
        page.get_by_role("button", name="Apply to 1 element(s)").click()
        expect(page.get_by_text("Retyped 1 to DataObject.")).to_be_visible()
        # Select everything shown and confirm: the queue empties and says so.
        page.get_by_label("Select all shown").check()
        confirm.click()
        expect(page.get_by_text("Nothing waiting")).to_be_visible()
        page.get_by_role("button", name="Show items already reviewed").click()
        expect(page.locator("[data-testid=review-table]")).to_contain_text("Order box")
        assert not [e for e in errors if "pageerror" in e], errors
    finally:
        page.close()
