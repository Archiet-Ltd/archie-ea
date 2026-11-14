"""Money on an application's pages reaches only the roles that may see it, and the Add Application form saves.

Goals:
- "I am an architect without spend authority, or a reader. I open an application's fact sheet and its Cost tab and
  read no pound figure, no cost-of-ownership number and no link to edit costs; I am told who can set what is missing."
- "I am an Administrator. I read the same figures on the fact sheet, the Cost tab and the list, and the Cost tab
  says which total was entered and which was calculated."
- "I fill in the Add Application form, with the longer fields too, and the new application opens with everything I
  typed."

Two organisations each hold an application with its own recorded cost; nothing of the second shows to the first.
"""
import os
import pathlib
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="application-money-and-add-form-"))
HELD_BACK = "Cost figures are withheld for your role."
# licence, monthly infrastructure, development, maintenance, entered total of cost of ownership
OURS = dict(license_cost_annual=4200.0, infrastructure_cost_monthly=100.0, development_cost_annual=12000.0,
            maintenance_cost=7800.0, total_cost_of_ownership=31500.0)
THEIRS = dict(license_cost_annual=88123.0, infrastructure_cost_monthly=4417.0, development_cost_annual=73991.0,
              maintenance_cost=61337.0, total_cost_of_ownership=999111.0)
OUR_FIGURES = ("4,200", "12,000", "7,800", "25,200", "31,500")
THEIR_FIGURES = ("88,123", "4,417", "73,991", "61,337", "999,111")


def _estate(figures, label):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    # the first person is a reader; the architect and the finance person join the same organisation
    org = create_fresh_org("non_technical_owner", extra_roles=("enterprise_architect",), platform_role="Viewer")
    org["emails"]["administrator"] = "launch.admin.%s@example.com" % org["suffix"]
    tag = uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        costed = ApplicationComponent(name="%s ledger %s" % (label, tag), organization_id=org["org_id"],
                                      lifecycle_status="operational", description="Holds the ledger.", **figures)
        bare = ApplicationComponent(name="%s bare %s" % (label, tag), organization_id=org["org_id"],
                                    lifecycle_status="planning")
        for column in ("business_domain", "technology_stack", "data_classification", "vendor_name", "description"):
            setattr(bare, column, None)
        db.session.add_all([costed, bare])
        db.session.commit()
        org["costed_id"], org["costed_name"] = costed.id, costed.name
        org["bare_id"] = bare.id
    return org


@pytest.fixture(scope="module")
def estates(live_server):
    return _estate(OURS, "Ours"), _estate(THEIRS, "Theirs")


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def _open(browser, live_server, email, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _goto(page, live_server, path):
    return page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _main(page):
    return page.locator("main").inner_text()


@pytest.mark.parametrize("who", ["enterprise_architect", "non_technical_owner"])
@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_a_person_without_spend_authority_reads_no_money_on_either_page(browser, live_server, estates, who, width,
                                                                        scheme):
    mine, theirs = estates
    context, page = _open(browser, live_server, mine["emails"][who], width, scheme)
    try:
        _goto(page, live_server, "/applications/%d/fact-sheet" % mine["costed_id"])
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        html = page.content()
        for figure in OUR_FIGURES + THEIR_FIGURES:
            assert figure not in html, figure
        assert "£" not in _main(page)
        _shot(page, "%s-fact-sheet" % who, width, scheme)

        _goto(page, live_server, "/applications/%d?tab=cost" % mine["costed_id"])
        expect(page.get_by_test_id("costs-withheld")).to_have_text(HELD_BACK, timeout=PAGE_TIMEOUT)
        html = page.content()
        for figure in OUR_FIGURES + THEIR_FIGURES:
            assert figure not in html, figure
        assert "Edit Cost Details" not in html
        assert "£" not in _main(page)
        assert "Total Cost of Ownership" not in _main(page)
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        _shot(page, "%s-cost-tab" % who, width, scheme)

        response = _goto(page, live_server, "/applications/%d" % theirs["costed_id"])
        assert response.status == 404, response.status
    finally:
        context.close()


def test_an_administrator_reads_the_same_figures_everywhere_and_the_cost_tab_says_which_total_is_which(
        browser, live_server, estates):
    mine, theirs = estates
    context, page = _open(browser, live_server, mine["emails"]["administrator"], 1440, "light")
    try:
        _goto(page, live_server, "/applications/%d/fact-sheet" % mine["costed_id"])
        expect(page.get_by_test_id("fact-sheet-stats")).to_contain_text("31,500", timeout=PAGE_TIMEOUT)
        assert HELD_BACK not in page.content()
        _shot(page, "administrator-fact-sheet", 1440, "light")

        _goto(page, live_server, "/applications/%d?tab=cost" % mine["costed_id"])
        body = _main(page)
        for figure in OUR_FIGURES:
            assert figure in body, figure
        for figure in THEIR_FIGURES:
            assert figure not in page.content(), figure
        assert HELD_BACK not in page.content()
        explained = page.get_by_test_id("cost-total-explained")
        expect(explained).to_contain_text("£31,500")
        expect(explained).to_contain_text("entered")
        expect(explained).to_contain_text("£25,200")
        expect(explained).to_contain_text("calculated")
        _shot(page, "administrator-cost-tab", 1440, "light")
        link = page.get_by_role("link", name=re.compile("Edit Cost Details"))
        expect(link).to_have_count(1)
        link.click()
        page.wait_for_url(re.compile(r"/applications/%d/edit" % mine["costed_id"]), timeout=PAGE_TIMEOUT)
        assert len(_main(page).strip()) > 100

        _goto(page, live_server, "/applications")
        assert mine["costed_name"] in _main(page)
        assert theirs["costed_name"] not in page.content()
    finally:
        context.close()


def test_the_banner_tells_a_reader_who_can_set_what_is_missing_and_an_administrator_where(
        browser, live_server, estates):
    mine, _ = estates
    reader_context, reader = _open(browser, live_server, mine["emails"]["non_technical_owner"], 1440, "light")
    try:
        _goto(reader, live_server, "/applications/%d" % mine["bare_id"])
        steps = reader.get_by_test_id("completeness-steps").first
        expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
        text = steps.inner_text()
        assert "set in" not in text, text
        assert "an Administrator or an architect can set this" in text, text
        assert steps.get_by_role("link").count() == 0
        _shot(reader, "reader-banner", 1440, "light")
    finally:
        reader_context.close()
    admin_context, admin = _open(browser, live_server, mine["emails"]["administrator"], 1440, "light")
    try:
        _goto(admin, live_server, "/applications/%d" % mine["bare_id"])
        steps = admin.get_by_test_id("completeness-steps").first
        expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "set in the editor" in steps.inner_text()
        link = steps.get_by_role("link", name="Business domain")
        expect(link).to_have_count(1)
        link.click()
        admin.wait_for_url(re.compile(r"/applications/%d/edit" % mine["bare_id"]), timeout=PAGE_TIMEOUT)
        assert len(_main(admin).strip()) > 100
    finally:
        admin_context.close()


def _stored(org_id, name):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = ApplicationComponent.query.filter_by(name=name, organization_id=org_id).first()
        out = None if row is None else dict(
            id=row.id, application_code=row.application_code, business_criticality=row.business_criticality,
            business_purpose=row.business_purpose, technology_stack=row.technology_stack,
            description=row.description, component_type=row.component_type)
        db.session.remove()
        return out


def test_the_add_application_form_saves_every_field_and_the_new_application_opens(browser, live_server, estates):
    mine, theirs = estates
    name = "Intake portal %s" % uuid.uuid4().hex[:6]
    code = "APP-%s" % uuid.uuid4().hex[:8].upper()
    context, page = _open(browser, live_server, mine["emails"]["administrator"], 1440, "light")
    try:
        _goto(page, live_server, "/dashboard/applications/create")
        page.fill("#name", name)
        page.fill("#application_code", code)
        page.fill("#description", "Takes in new customer requests.")
        page.select_option("#component_type", "Web Application")
        page.select_option("#business_criticality", index=1)
        criticality = page.eval_on_selector("#business_criticality", "e => e.value")
        page.select_option("#deployment_status", index=1)
        page.fill("#technology_stack", "Python, PostgreSQL")
        page.fill("#business_purpose", "Lets customers raise requests without a phone call.")
        _shot(page, "add-application-filled", 1440, "light")
        page.get_by_role("button", name=re.compile("Create|Save", re.I)).first.click()
        page.wait_for_url(lambda url: "/create" not in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name, timeout=PAGE_TIMEOUT)
        assert len(_main(page).strip()) > 200
        _shot(page, "add-application-opened", 1440, "light")
    finally:
        context.close()
    row = _stored(mine["org_id"], name)
    assert row is not None, "the application was not saved"
    assert row["application_code"] == code
    assert row["business_criticality"] == criticality and criticality
    assert row["business_purpose"] == "Lets customers raise requests without a phone call."
    assert row["technology_stack"] == "Python, PostgreSQL"
    assert row["description"] == "Takes in new customer requests."
    assert _stored(theirs["org_id"], name) is None


def test_the_full_add_application_form_with_the_longer_fields_saves_and_opens(browser, live_server, estates):
    mine, _ = estates
    name = "Full intake %s" % uuid.uuid4().hex[:6]
    context, page = _open(browser, live_server, mine["emails"]["administrator"], 1440, "light")
    try:
        _goto(page, live_server, "/dashboard/applications/create")
        token = page.eval_on_selector("input[name=csrf_token]", "e => e.value")
        reply = page.evaluate(
            """async ([name, token]) => {
                const body = new URLSearchParams({
                    csrf_token: token, name: name, component_type: 'Web Application',
                    deployment_status: 'design', application_category: 'Business Application',
                    architecture_style: 'Microservices', description: 'The longer form.',
                    technology_stack: 'Go', version: '2.3', business_owner: 'Pat Owner',
                    business_criticality: 'High', deployment_region: 'eu-west-2', cloud_provider: 'aws',
                    user_count: '120'});
                const r = await fetch('/dashboard/applications/create', {method: 'POST', body: body,
                    credentials: 'same-origin', redirect: 'follow'});
                return {status: r.status, url: r.url};
            }""", [name, token])
        assert reply["status"] == 200, reply
        assert "/create" not in reply["url"], reply
        page.goto(reply["url"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(name, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        row = ApplicationComponent.query.filter_by(name=name, organization_id=mine["org_id"]).one()
        saved = (row.version, row.business_owner, row.deployment_region, row.cloud_provider, row.user_count,
                 row.application_category, row.architecture_style, row.business_criticality)
        db.session.remove()
    assert saved == ("2.3", "Pat Owner", "eu-west-2", "aws", 120, "Business Application", "Microservices", "High")
