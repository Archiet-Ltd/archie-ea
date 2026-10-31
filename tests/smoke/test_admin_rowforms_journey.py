"""The forms and row buttons on five administration pages, used the way an administrator uses them.

Single sign-on group mappings, the GitHub import review, the application report, an organisation's
detail page and the consolidation status: each is driven in a browser as a platform administrator in
a seeded organisation, through the controls on the page, and asserts the outcome persisted.
"""

import json
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed():
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"suffix": suffix}
    with app.app_context():
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record
        from app.connectors import github as gh

        Role.insert_roles()
        if Organization.query.filter_by(slug="default").first() is None:
            db.session.add(Organization(name="Default", slug="default", plan="free"))
            db.session.commit()
        role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Rowforms %s" % suffix, slug="rowforms-%s" % suffix, plan="enterprise")
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id

        def make(email, first, admin=False):
            user = User(email=email, first_name=first, last_name="Rowe", organization_id=org.id,
                        enterprise_role="solution_architect", confirmed=True)
            user.role = role
            user.is_org_admin = admin
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            return user

        admin = make("rf.admin.%s@example.com" % suffix, "Platform", admin=True)
        admin.is_platform_admin = True
        db.session.commit()
        out["admin_email"] = admin.email
        member = make("rf.member.%s@example.com" % suffix, "Member")
        out["member_email"], out["member_id"] = member.email, member.id

        from app.models.application_portfolio import ApplicationComponent
        for i, (name, lifecycle) in enumerate([("Zephyr Ledger", "Production"), ("Quill Notes", "Pilot"), ("Zephyr Portal", "Production")]):
            db.session.add(ApplicationComponent(name=name, organization_id=org.id, lifecycle_status=lifecycle,
                                                description="Seeded %d" % i))
        db.session.commit()

        def classify(name, group):
            create_approval_record(
                organization_id=org.id, operation_type=gh.OP_CLASSIFY, entity_type="repository",
                summary="Add %s" % name,
                operation_payload={"name": name, "classification": "application", "group": group,
                                   "reason": "It has a deployable service.", "owners": [], "repository": "acme/%s" % name.lower(),
                                   "path": None, "commit": "a1b2c3d4e5f6", "evidence_file": "package.json", "description": name},
                source_table=gh.PROPOSAL_SOURCE, expiry_minutes=60 * 24,
            )

        for name in ("Billing", "Checkout", "Search"):
            classify(name, "Shop")
        db.session.commit()
        out["pending"] = AIChatCRUDApproval.query.filter_by(organization_id=org.id, source_table=gh.PROPOSAL_SOURCE).count()
        db.session.remove()
    return out


@pytest.fixture(scope="module")
def seeded(live_server):
    return _seed()


def _sign_in(page, live_server, email):
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _confirm(page):
    dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog")).last
    expect(dialog).to_be_visible()
    dialog.get_by_role("button").last.click()


@pytest.fixture
def admin_page(live_server, browser, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _sign_in(page, live_server, seeded["admin_email"])
    yield page
    context.close()


def test_administrator_saves_an_sso_group_mapping_and_disables_it_again(live_server, admin_page, seeded):
    page = admin_page
    group = "EA-Rowforms-%s" % seeded["suffix"]
    page.goto(live_server + "/admin/sso-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#sso_group_name", group)
    page.select_option("#role_name", "enterprise_architect")
    page.fill("#description", "Rowforms team")
    page.keyboard.press("Enter")  # a form submits from the keyboard
    page.wait_for_load_state("domcontentloaded")
    row = page.get_by_test_id("sso-mapping-row").filter(has_text=group)
    expect(row).to_be_visible()
    expect(row).to_contain_text("Enterprise Architect")
    expect(row).to_contain_text("Active")

    row.get_by_role("button", name="Actions for " + group).click()
    page.get_by_role("menuitem", name="Disable").click()
    page.wait_for_load_state("domcontentloaded")
    row = page.get_by_test_id("sso-mapping-row").filter(has_text=group)
    expect(row).to_contain_text("Inactive")

    # a reload keeps it, and delete confirms first
    page.reload()
    row = page.get_by_test_id("sso-mapping-row").filter(has_text=group)
    expect(row).to_contain_text("Inactive")
    row.get_by_role("button", name="Actions for " + group).click()
    page.get_by_role("menuitem", name="Delete").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("sso-mapping-row").filter(has_text=group)).to_have_count(0)


def test_administrator_reviews_a_github_import_skips_one_and_accepts_the_rest(live_server, admin_page, seeded):
    page = admin_page
    page.goto(live_server + "/admin/connectors/github/review", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("github-review-row")).to_have_count(seeded["pending"])
    expect(page.get_by_role("button", name="Approve all (%d)" % seeded["pending"])).to_be_visible()

    row = page.get_by_test_id("github-review-row").filter(has_text="Search")
    row.get_by_role("button", name="Skip").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("github-review-row")).to_have_count(seeded["pending"] - 1)
    expect(page.get_by_role("button", name="Approve all (%d)" % (seeded["pending"] - 1))).to_be_visible()

    page.get_by_role("button", name="Approve all").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_text("Nothing is waiting for review")).to_be_visible()

    # the connector page and the model agree: nothing left pending, the accepted ones are applications
    from app import create_app, db
    from app.connectors import github as gh
    from app.models import ArchiMateElement

    with create_app("testing").app_context():
        assert gh.pending_total(seeded["org_id"]) == 0
        names = {e.name for e in ArchiMateElement.query.filter_by(organization_id=seeded["org_id"], type="Application")}
        db.session.remove()
    assert {"Billing", "Checkout"} <= names and "Search" not in names


def test_administrator_builds_a_report_and_sees_its_rows(live_server, admin_page, seeded):
    page = admin_page
    page.goto(live_server + "/admin/reports", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#q", "Seeded")
    page.get_by_role("button", name="Filter").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("report-row")).to_have_count(3)
    page.fill("#q", "Zephyr")
    page.select_option("#lifecycle", "Production")
    page.get_by_role("button", name="Filter").click()
    page.wait_for_load_state("domcontentloaded")
    rows = page.get_by_test_id("report-row")
    expect(rows).to_have_count(2)
    expect(rows.first).to_contain_text("Zephyr")
    expect(page.get_by_test_id("report-pager")).to_contain_text("2 applications found")
    expect(page.locator("#lifecycle")).to_have_value("Production")  # the filter shows what produced the rows
    page.get_by_role("link", name="Clear").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#q")).to_have_value("")
    expect(page.get_by_test_id("report-row")).not_to_have_count(2)


def test_administrator_opens_an_organisation_and_every_row_menu_action_does_what_it_says(live_server, admin_page, seeded):
    page = admin_page
    url = live_server + "/admin/organizations/%d" % seeded["org_id"]
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("org-user-row").filter(has_text=seeded["member_email"])
    expect(row).to_contain_text("No")

    row.get_by_role("button", name="Actions for").click()
    page.get_by_role("menuitem", name="Make Admin").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    row = page.get_by_test_id("org-user-row").filter(has_text=seeded["member_email"])
    expect(row).to_contain_text("Yes")

    row.get_by_role("button", name="Actions for").click()
    page.get_by_role("menuitem", name="Revoke Admin").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("org-user-row").filter(has_text=seeded["member_email"])).to_contain_text("No")

    # deactivate then reactivate the organisation from the status button
    page.get_by_role("button", name="Deactivate").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("button", name="Activate")).to_be_visible()
    page.get_by_role("button", name="Activate").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("button", name="Deactivate")).to_be_visible()

    # removing a user confirms first and the user leaves the list
    row = page.get_by_test_id("org-user-row").filter(has_text=seeded["member_email"])
    row.get_by_role("button", name="Actions for").click()
    page.get_by_role("menuitem", name="Remove from organization").click()
    _confirm(page)
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("org-user-row").filter(has_text=seeded["member_email"])).to_have_count(0)


def test_consolidation_status_progress_bars_show_their_fraction(live_server, admin_page):
    page = admin_page
    page.goto(live_server + "/admin/consolidation", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("consolidation-targets-table")).to_be_visible()
    bars = page.locator("[data-testid=consolidation-targets-table] [role=progressbar]")
    assert bars.count() > 0
    for i in range(bars.count()):
        assert bars.nth(i).get_attribute("aria-valuenow") is not None
