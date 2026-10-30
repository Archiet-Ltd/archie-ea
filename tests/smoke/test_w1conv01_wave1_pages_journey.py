"""Wave-1 pages built on the design-system macros still do what a person came to do.

Each journey starts from a goal (change an application, review what an import
produced, find a job, read the audit trail, pick a role, filter modules,
compare tools) in the one seeded organisation, clicks what a person clicks and
asserts the outcome: the content that was seeded is on the page, filters narrow
it, a save persists across a fresh load, a dialog opens and closes, every link
lands on a page with content. A final walk saves screenshots at 1440 and 390
wide, light and dark.
"""

import os
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login, _visit

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOT_DIR = os.environ.get("W1CONV01_SHOTS", "/home/ubuntu/verify/seats/w1conv01/shots")


@pytest.fixture
def admin_page(browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.add_init_script("try { localStorage.setItem('theme', 'light'); } catch (e) {}")
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    yield page
    ctx.close()


@pytest.fixture
def import_rows(app, seeded):
    """One import job with one batch ready for review and three generated elements."""
    from app import db
    from app.models.batch_import import (
        BatchImportApplication, BatchImportBatch, BatchImportElement, BatchImportJob,
    )
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        admin = User.query.filter_by(email=seeded["emails"]["platform_admin"]).one()
        job = BatchImportJob(user_id=admin.id, name="Wave one import %s" % suffix,
                             filename="wave-one-%s.csv" % suffix, total_applications=2,
                             total_batches=1, batch_size=2)
        db.session.add(job)
        db.session.flush()
        batch = BatchImportBatch(job_id=job.id, batch_number=1, total_applications=2)
        db.session.add(batch)
        db.session.flush()
        application = BatchImportApplication(batch_id=batch.id, row_number=1,
                                             source_data={"name": "Ledger %s" % suffix},
                                             application_name="Ledger %s" % suffix)
        db.session.add(application)
        db.session.flush()
        names = []
        for index, (name, layer, element_type, confidence) in enumerate([
                ("Ledger service %s" % suffix, "application", "application_component", 0.92),
                ("Billing team %s" % suffix, "business", "business_actor", 0.55),
                ("Ledger database %s" % suffix, "technology", "node", 0.31),
                ("Unscored thing %s" % suffix, None, "node", None)]):
            names.append(name)
            db.session.add(BatchImportElement(
                batch_id=batch.id, application_id=application.id, element_type=element_type,
                element_name=name, element_data={"name": name}, archimate_layer=layer,
                confidence_score=confidence))
        unnamed = BatchImportJob(user_id=admin.id, name=None, filename="orphan-%s.csv" % suffix,
                                 total_applications=1, total_batches=1, batch_size=1)
        db.session.add(unnamed)
        db.session.commit()
        ids = {"unnamed_job": unnamed.id, "orphan_file": "orphan-%s.csv" % suffix, "job": job.id, "batch": batch.id, "job_name": job.name, "names": names, "suffix": suffix}
        db.session.remove()
    yield ids
    with app.app_context():
        for key in ("job", "unnamed_job"):
            job = BatchImportJob.query.get(ids[key])
            if job is not None:
                db.session.delete(job)
        db.session.commit()
        db.session.remove()


def _body(page):
    return " ".join(page.inner_text("body").split())


def _no_template_failure(page):
    text = _body(page)
    for bad in ("Internal Server Error", "TemplateSyntaxError", "UndefinedError", "Traceback (most recent call last)"):
        assert bad not in text, "page shows a server failure: %s" % bad


def test_editing_an_application_keeps_every_field_and_saves(admin_page, live_server, seeded):
    page = admin_page
    app_id = seeded["ids"]["application"]
    _visit(page, live_server, "/applications/%d/edit" % app_id)
    _no_template_failure(page)
    expect(page.get_by_role("heading", name="Edit Application")).to_be_visible()
    name_field = page.locator("#name")
    assert name_field.input_value().startswith("Smoke Payroll"), "the application's own name is in the form"
    # the owner picker still needs two characters before it enables adding
    expect(page.locator("#add-owner-btn")).to_be_disabled()
    # the legacy columns are read only
    expect(page.locator("#business_owner")).to_have_attribute("readonly", "readonly")
    note = "Reviewed %s" % uuid.uuid4().hex[:6]
    page.fill("#business_domain", note)
    page.select_option("#criticality", "High")
    page.get_by_role("button", name="Save Changes").click()
    page.wait_for_url(lambda url: "/edit" not in url, timeout=PAGE_TIMEOUT)
    _visit(page, live_server, "/applications/%d/edit" % app_id)
    assert page.locator("#business_domain").input_value() == note, "the saved domain is back after a fresh load"
    assert page.locator("#criticality").input_value() == "High"


def test_reviewing_an_import_batch_filters_opens_and_closes(admin_page, live_server, import_rows):
    page = admin_page
    _visit(page, live_server, "/batch-import/batches/%d/review" % import_rows["batch"])
    _no_template_failure(page)
    for name in import_rows["names"]:
        expect(page.get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)
    # narrow by layer with the select on the same state
    layer = page.locator("select[aria-label='Filter by layer']")
    expect(layer).to_be_visible()
    layer.select_option("Business")
    expect(page.get_by_text(import_rows["names"][1]).first).to_be_visible()
    assert page.get_by_text(import_rows["names"][0]).count() == 0, "other layers are filtered out"
    layer.select_option("")
    # narrow by search
    page.get_by_placeholder("Search elements...").fill("database")
    expect(page.get_by_text(import_rows["names"][2]).first).to_be_visible()
    assert page.get_by_text(import_rows["names"][0]).count() == 0
    page.get_by_placeholder("Search elements...").fill("")
    # each element has a confidence bar a person can read
    bars = page.locator("[role='progressbar']")
    assert bars.count() >= 3
    # open the detail dialog from the first element and close it with Escape
    body = _body(page)
    assert "Review Batch #1" in body, "the header names the batch"
    assert "Not scored" in body, "an element with no score says so, not 0%"
    assert "Not set" in body, "an element with no layer says so"
    assert "undefined" not in body and "null" not in body
    page.get_by_text(import_rows["names"][0]).first.click()
    dialog = page.locator("[role='dialog']:visible").first
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text(import_rows["names"][0])
    page.keyboard.press("Escape")
    expect(page.locator("[role='dialog']:visible")).to_have_count(0)


def test_import_dashboard_lists_the_job_and_its_detail_page_opens(admin_page, live_server, import_rows):
    page = admin_page
    _visit(page, live_server, "/batch-import/")
    _no_template_failure(page)
    expect(page.get_by_text(import_rows["job_name"]).first).to_be_visible(timeout=PAGE_TIMEOUT)
    search = page.get_by_placeholder("Search jobs…")
    search.fill(import_rows["suffix"])
    assert search.input_value() == import_rows["suffix"], "the search box takes what the person types"
    expect(page.get_by_text(import_rows["job_name"]).first).to_be_visible()
    _visit(page, live_server, "/batch-import/jobs/%d" % import_rows["job"])
    _no_template_failure(page)
    assert import_rows["job_name"] in page.title()
    expect(page.get_by_role("heading", name=import_rows["job_name"]).first).to_be_visible()
    _visit(page, live_server, "/batch-import/jobs/%d" % import_rows["unnamed_job"])
    assert "None" not in _body(page) and "None" not in page.title()
    assert import_rows["orphan_file"] in page.title(), "an unnamed job is called by its file"


def test_new_import_offers_the_modes_and_a_working_form(admin_page, live_server):
    page = admin_page
    _visit(page, live_server, "/batch-import/new")
    _no_template_failure(page)
    expect(page.locator("#jobName")).to_be_visible(timeout=PAGE_TIMEOUT)
    # a person can pick an ArchiMate mode: the radios are real and exactly one is checked
    radios = page.locator("input[type='radio'][x-model='form.archimate_mode']")
    assert radios.count() >= 2
    assert page.locator("#programme-target option", has_text="No programme").count() == 1
    labels = page.locator("label:has(input[type='radio'][x-model='form.archimate_mode'])")
    labels.nth(1).click()
    assert page.locator("input[type='radio'][x-model='form.archimate_mode']:checked").count() == 1


def test_audit_log_and_user_admin_pages(admin_page, live_server, seeded):
    page = admin_page
    _visit(page, live_server, "/admin/audit-log")
    _no_template_failure(page)
    expect(page.locator("#date_from")).to_be_visible()
    page.fill("#user_email", "nobody-%s@example.com" % uuid.uuid4().hex[:6])
    page.get_by_role("button", name="Filter").first.click()
    page.wait_for_load_state("domcontentloaded")
    _no_template_failure(page)
    assert "nobody-" in page.locator("#user_email").input_value(), "the filter keeps what the person typed"

    _visit(page, live_server, "/admin/users")
    _no_template_failure(page)
    assert seeded["emails"]["platform_admin"] in _body(page), "the organisation's own users are listed"

    user_id = seeded["ids"]["app_manager_user"]
    _visit(page, live_server, "/admin/user/%d/role" % user_id)
    _no_template_failure(page)
    radios = page.locator("input[type='radio'][name='enterprise_role']")
    assert radios.count() >= 2
    assert page.locator("input[type='radio'][name='enterprise_role']:checked").count() == 1, "the current role is preselected"

    _visit(page, live_server, "/admin/user/%d/delete" % user_id)
    _no_template_failure(page)
    confirm = page.locator("#delete-confirm")
    expect(confirm).to_be_visible()
    submit = page.get_by_role("button", name="Delete this user")
    expect(submit).to_be_disabled()
    confirm.check()
    expect(submit).to_be_enabled()


def test_my_applications_sections_are_links_that_land_on_content(admin_page, live_server, seeded):
    page = admin_page
    _visit(page, live_server, "/my-applications/")
    _no_template_failure(page)
    nav = page.get_by_role("navigation", name="My Applications")
    expect(nav).to_be_visible()
    expect(nav.locator("a[aria-current='page']")).to_have_text("Dashboard")
    nav.get_by_role("link", name="Applications").click()
    page.wait_for_url("**/my-applications/list**")
    nav = page.get_by_role("navigation", name="My Applications")
    expect(nav.locator("a[aria-current='page']")).to_have_text("Applications")
    _no_template_failure(page)
    search = page.locator("input[name='search']")
    expect(search).to_be_visible()
    search.fill("zzz-no-such-application")
    page.get_by_role("button", name="Search").first.click()
    page.wait_for_load_state("domcontentloaded")
    _no_template_failure(page)
    assert "zzz-no-such-application" in page.locator("input[name='search']").input_value()


def test_status_health_and_modules_pages_have_their_content(admin_page, live_server):
    page = admin_page
    _visit(page, live_server, "/status")
    _no_template_failure(page)
    assert len(_body(page)) > 200
    _visit(page, live_server, "/dashboard/health")
    _no_template_failure(page)
    expect(page.get_by_role("button", name="Generate briefing")).to_be_visible()
    response, _state = _visit(page, live_server, "/pricing")
    assert response.status == 200, "pricing opens when signed in"
    _visit(page, live_server, "/modules/")
    _no_template_failure(page)
    box = page.locator("#modules-directory-search")
    expect(box).to_be_visible()
    box.fill("zzzzzz-no-module")
    clear = page.get_by_role("button", name="Clear the module filter")
    expect(clear).to_be_visible()
    clear.click()
    assert box.input_value() == "", "the clear button empties the filter"


def test_applications_create_fact_sheet_and_ownership_coverage(admin_page, live_server, seeded):
    page = admin_page
    app_id = seeded["ids"]["application"]
    _visit(page, live_server, "/applications/create")
    _no_template_failure(page)
    assert len(_body(page)) > 200
    _visit(page, live_server, "/applications/%d/fact-sheet" % app_id)
    _no_template_failure(page)
    assert "Record completeness" in _body(page)
    _visit(page, live_server, "/applications/ownership-coverage")
    _no_template_failure(page)
    assert len(_body(page)) > 200


def test_public_pricing_comparison_hub_and_demo_company(browser, live_server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    response, _state = _visit(page, live_server, "/pricing")
    assert response.status == 200, "pricing opens when signed out"
    _no_template_failure(page)
    year = page.locator("input[name='pp-interval'][value='year']")
    month = page.locator("input[name='pp-interval'][value='month']")
    assert year.count() == 1 and month.count() == 1
    assert year.is_checked() and not month.is_checked()
    page.get_by_text("Monthly", exact=True).first.click()
    assert month.is_checked() and not year.is_checked(), "the billing interval toggle still switches"
    _visit(page, live_server, "/vs")
    _no_template_failure(page)
    hrefs = []
    for link in page.locator("main li a").all():
        href = link.get_attribute("href")
        if href and "Entelim vs" in link.inner_text():
            hrefs.append(href)
    assert hrefs, "the hub lists comparison pages"
    first = hrefs[0]
    if first.startswith("/"):
        _visit(page, live_server, first)
        _no_template_failure(page)
        assert len(_body(page)) > 400, "a comparison link lands on a page with content"
    _visit(page, live_server, "/demo/lantern-quay")
    _no_template_failure(page)
    assert len(_body(page)) > 100
    ctx.close()


WALK = [
    ("application-edit", "/applications/{app}/edit"),
    ("batch-review", "/batch-import/batches/{batch}/review"),
    ("batch-dashboard", "/batch-import/"),
    ("batch-new", "/batch-import/new"),
    ("batch-job", "/batch-import/jobs/{job}"),
    ("audit-log", "/admin/audit-log"),
    ("admin-users", "/admin/users"),
    ("user-role", "/admin/user/{user}/role"),
    ("user-delete", "/admin/user/{user}/delete"),
    ("my-apps", "/my-applications/"),
    ("my-apps-list", "/my-applications/list"),
    ("status", "/status"),
    ("health", "/dashboard/health"),
    ("modules", "/modules/"),
    ("app-create", "/applications/create"),
    ("fact-sheet", "/applications/{app}/fact-sheet"),
    ("ownership", "/applications/ownership-coverage"),
]
PUBLIC_WALK = [("pricing", "/pricing"), ("vs-hub", "/vs"), ("demo", "/demo/lantern-quay")]


@pytest.mark.skipif(not os.environ.get("W1CONV01_WALK"), reason="screenshot walk is run on request")
@pytest.mark.parametrize("width,theme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_walk_screenshots(browser, live_server, seeded, import_rows, width, theme):
    os.makedirs(SHOT_DIR, exist_ok=True)
    ctx = browser.new_context(viewport={"width": width, "height": 900}, ignore_https_errors=True)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.add_init_script("try { localStorage.setItem('theme', '%s'); } catch (e) {}" % theme)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    values = {"app": seeded["ids"]["application"], "batch": import_rows["batch"],
              "job": import_rows["job"], "user": seeded["ids"]["app_manager_user"]}
    report = []
    for name, path in WALK + PUBLIC_WALK:
        _visit(page, live_server, path.format(**values))
        page.wait_for_timeout(600)
        text = _body(page)
        report.append("%s %s %dw %s chars=%d overflow=%s" % (
            name, path.format(**values), width, theme, len(text),
            page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")))
        page.screenshot(path=os.path.join(SHOT_DIR, "%s_%dw_%s.png" % (name, width, theme)), full_page=True)
    with open(os.path.join(SHOT_DIR, "walk_%dw_%s.txt" % (width, theme)), "w") as handle:
        handle.write("\n".join(report) + "\n")
    ctx.close()


def test_registered_users_on_a_phone_shows_whose_row_it_is(browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {}")
    page = ctx.new_page()
    email = seeded["emails"]["platform_admin"]
    _login(page, live_server, email)
    _visit(page, live_server, "/admin/users")
    cell = page.locator("td", has_text=email).first
    expect(cell).to_be_visible()
    box = cell.bounding_box()
    assert box["x"] >= 0 and box["x"] < 390, "the email cell starts on screen: %s" % box
    # the actions wrap inside the row instead of pushing the identity off screen
    actions = page.locator("tr", has_text=email).first.locator("td").last.bounding_box()
    assert actions["width"] < 250, "actions wrapped: %s" % actions
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()
