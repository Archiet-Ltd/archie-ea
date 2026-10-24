"""Requirements backlog: the demo enterprise architect opens the backlog of an
organisation that has requirements linked to a capability, sees the same count
in the header, chips and rows, follows the capability link to a page that names
it, edits a requirement in the dialog, and the change is still there after a
reload.

The page is built from the shared components, so the journey also pins that the
list is the shared dense table and that both dialogs are the shared modal: they
open from the buttons a person presses, close from Cancel, the close control and
Escape, and can be opened again afterwards.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/enterprise/requirements-backlog"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _open_backlog(page, base):
    page.goto(base + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector("[data-testid=requirements-table]", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        "() => !document.body.innerText.includes('Loading requirements')", timeout=PAGE_TIMEOUT
    )


def _person(seeded, persona, platform_role="Viewer"):
    """A signed-in person of ``persona`` whose PLATFORM role carries no write
    permission, like the demo enterprise architect: what they may do comes from
    the persona alone, not from the legacy Architect/Administrator role."""
    from app import create_app, db
    from app.models.user import Role, User

    email = "req-%s-%s@example.com" % (persona.replace("_", "-"), uuid.uuid4().hex[:8])
    app = create_app("testing")
    with app.app_context():
        user = User(email=email, first_name="Demo", last_name=persona[:12],
                    organization_id=seeded["ids"]["org"], enterprise_role=persona,
                    confirmed=True)
        user.role = Role.query.filter_by(name=platform_role).one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def _assert_persona(email, persona):
    """The signed-in user is the persona named and holds no administrator role."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona
        assert user.role.name == "Viewer"
        assert not user.is_admin()


def _seed_requirements(seeded, owner_email=None):
    """Two requirements in the standard organisation, one linked to a capability."""
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_architect_models import Solution, SolutionRequirement

    tag = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    app = create_app("testing")
    with app.app_context():
        cap = BusinessCapability(name="Invoicing %s" % tag, organization_id=org_id)
        db.session.add(cap)
        solution = Solution(name="Billing platform %s" % tag, organization_id=org_id,
                            description="Replaces the invoice export job.",
                            solution_owner=owner_email)
        private = Solution(name="Treasury platform %s" % tag, organization_id=org_id,
                           description="Not shared with the architect.")
        db.session.add_all([solution, private])
        db.session.flush()
        linked = SolutionRequirement(
            name="Customers can export invoices %s" % tag,
            description="Finance can download every invoice as a file.",
            status="Draft", priority=2, capability_id=cap.id, solution_id=solution.id,
            organization_id=org_id,
        )
        unlinked = SolutionRequirement(
            name="Payment reminders go out on day 7 %s" % tag,
            description="Overdue customers get one reminder.",
            status="Refined", priority=3, solution_id=private.id, organization_id=org_id,
        )
        db.session.add_all([linked, unlinked])
        db.session.commit()
        return {"tag": tag, "cap_id": cap.id, "cap_name": cap.name,
                "solution_id": solution.id, "solution_name": solution.name,
                "private_name": private.name,
                "linked": linked.name, "unlinked": unlinked.name}


def test_backlog_shows_linked_requirements_and_edits_persist(browser, live_server, seeded):
    email = _person(seeded, "enterprise_architect")
    data = _seed_requirements(seeded, owner_email=email)
    tag = data["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _assert_persona(email, "enterprise_architect")
        _login(page, live_server, email)
        _open_backlog(page, live_server)

        # The list is the shared table, with a column for every cell the rows carry.
        table = page.locator("[data-testid=requirements-table]")
        assert table.locator("thead th").count() == 8
        page.fill("input[aria-label='Search requirements']", tag)
        page.wait_for_function(
            "() => document.querySelectorAll('[data-testid=requirements-table] tbody tr').length === 2"
        )
        # The header count, the rows and the status chips agree.
        assert page.get_by_text("2 requirements", exact=True).is_visible()
        assert table.locator("tbody tr", has_text=data["linked"]).count() == 1
        assert table.locator("tbody tr", has_text=data["unlinked"]).count() == 1
        linked_row = table.locator("tbody tr", has_text=data["linked"])
        assert "Not linked" not in linked_row.inner_text()
        assert "Not linked" in table.locator("tbody tr", has_text=data["unlinked"]).inner_text()

        # The capability link lands on the capability's page, not a data payload.
        linked_row.get_by_role("link", name=data["cap_name"]).click()
        page.wait_for_url("**/enterprise/capabilities/%s/fact-sheet" % data["cap_id"])
        assert page.locator("h1").first.inner_text().strip() == data["cap_name"]
        assert not page.inner_text("body").lstrip().startswith("{")
        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", tag)
        table.locator("tbody tr", has_text=data["linked"]).wait_for()

        # The row names its solution and the link opens that solution's page.
        linked_row = table.locator("tbody tr", has_text=data["linked"])
        linked_row.get_by_role("link", name=data["solution_name"]).click()
        page.wait_for_url("**/solutions/%s*" % data["solution_id"])
        assert data["solution_name"] in page.inner_text("body")
        assert page.locator("h1").count() >= 1
        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", tag)
        # A solution the architect cannot open is named, but is not offered as a dead link.
        unlinked_row = table.locator("tbody tr", has_text=data["unlinked"])
        assert data["private_name"] in unlinked_row.inner_text()
        assert unlinked_row.get_by_role("link", name=data["private_name"]).count() == 0

        # Dialogs are the shared modal: open, cancel, escape, open again.
        create = page.locator("#create-req-modal")
        assert not create.is_visible()
        page.get_by_role("button", name="Add requirement").first.click()
        create.wait_for(state="visible")
        assert create.get_attribute("role") == "dialog"
        assert create.locator("[data-modal-panel]").count() == 1
        create.get_by_role("button", name="Cancel").click()
        create.wait_for(state="hidden")
        page.get_by_role("button", name="Add requirement").first.click()
        create.wait_for(state="visible")
        page.keyboard.press("Escape")
        create.wait_for(state="hidden")

        # Edit the linked requirement in the dialog.
        page.fill("input[aria-label='Search requirements']", data["linked"])
        page.get_by_role("button", name="Edit requirement " + data["linked"]).click()
        detail = page.locator("#req-detail-modal")
        detail.wait_for(state="visible")
        assert "Edit Requirement" in detail.inner_text()
        assert data["linked"] in detail.locator("textarea").first.input_value()
        assert data["cap_name"] in detail.inner_text()
        detail.locator("[id^=req-status-]").select_option("Approved")
        detail.get_by_role("button", name="Save changes").click()
        detail.wait_for(state="hidden")

        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", data["linked"])
        status = page.get_by_label("Status for " + data["linked"])
        status.wait_for(state="visible")
        assert status.input_value() == "Approved"

        # The close control hands the page back its state, and the dialog opens again.
        page.get_by_role("button", name="Edit requirement " + data["linked"]).click()
        detail.wait_for(state="visible")
        detail.get_by_role("button", name="Close").first.click()
        detail.wait_for(state="hidden")
        page.get_by_role("button", name="Edit requirement " + data["linked"]).click()
        detail.wait_for(state="visible")
    finally:
        ctx.close()


def test_backlog_filters_are_labelled_and_capabilities_are_the_organisations(browser, live_server, seeded):
    data = _seed_requirements(seeded)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, _person(seeded, "enterprise_architect"))
        _open_backlog(page, live_server)
        for field, text in (("filter-status", "Status"), ("filter-moscow", "Priority"),
                            ("filter-capability", "Capability")):
            label = page.locator("label[for=%s]" % field)
            assert label.is_visible() and label.inner_text().strip() == text
        options = page.locator("#filter-capability option").all_inner_texts()
        assert data["cap_name"] in options
        names = [o for o in options if o != "All capabilities"]
        assert names, "the organisation's own capability is listed"
    finally:
        ctx.close()


def test_backlog_create_edit_score_and_delete_from_the_page(browser, live_server, seeded):
    tag = uuid.uuid4().hex[:6]
    name = "Auditors can replay any approval %s" % tag
    renamed = "Auditors can replay every approval %s" % tag
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, _person(seeded, "enterprise_architect"))
        _open_backlog(page, live_server)
        table = page.locator("[data-testid=requirements-table]")
        search = page.locator("input[aria-label='Search requirements']")

        # Create: the dialog closes and the row is there, and still there after a reload.
        page.get_by_role("button", name="Add requirement").first.click()
        create = page.locator("#create-req-modal")
        create.wait_for(state="visible")
        create.locator("#new-req-name").fill(name)
        create.locator("#new-req-desc").fill("Every approval decision can be replayed with its evidence.")
        create.get_by_role("button", name="Add Requirement").click()
        create.wait_for(state="hidden")
        search.fill(tag)
        assert table.locator("tbody tr", has_text=name).count() == 1
        _open_backlog(page, live_server)
        search.fill(tag)
        assert table.locator("tbody tr", has_text=name).count() == 1
        assert page.get_by_label("Status for " + name).input_value() == "Draft"

        # Edit: rename it in the dialog.
        page.get_by_role("button", name="Edit requirement " + name).click()
        detail = page.locator("#req-detail-modal")
        detail.wait_for(state="visible")
        detail.locator("textarea").first.fill(renamed)
        detail.get_by_role("button", name="Save changes").click()
        detail.wait_for(state="hidden")
        _open_backlog(page, live_server)
        search.fill(tag)
        assert table.locator("tbody tr", has_text=renamed).count() == 1
        assert table.locator("tbody tr", has_text=name).count() == 0

        # Score: RICE = reach * impact * confidence% / effort = 1000 * 2 * 0.5 / 10 = 100.
        page.get_by_role("button", name="Edit requirement " + renamed).click()
        detail.wait_for(state="visible")
        detail.locator("details summary").first.click()
        detail.get_by_label("Reach", exact=True).fill("1000")
        detail.get_by_label("Impact", exact=True).fill("2")
        detail.get_by_label("Confidence%", exact=True).fill("50")
        detail.get_by_label("Effort", exact=True).fill("10")
        detail.get_by_role("button", name="Save Scores").click()
        detail.get_by_text("RICE Score:").locator("span").wait_for(state="visible")
        page.wait_for_function(
            "() => document.querySelector('#req-detail-modal').innerText.includes('RICE Score: 100')"
        )
        detail.get_by_role("button", name="Close").first.click()
        detail.wait_for(state="hidden")
        _open_backlog(page, live_server)
        search.fill(tag)
        page.get_by_role("button", name="Edit requirement " + renamed).click()
        detail.wait_for(state="visible")
        detail.locator("details summary").first.click()
        assert "RICE Score: 100" in detail.inner_text()

        # Delete: asks once more, then the row is gone and stays gone after a reload.
        detail.get_by_role("button", name="Delete requirement").click()
        confirm = page.locator("#confirm-dialog-delete-requirement [role=alertdialog]")
        confirm.wait_for(state="visible")
        assert "Delete this requirement?" in confirm.inner_text()
        confirm.get_by_role("button", name="Delete requirement").click()
        detail.wait_for(state="hidden")
        toast = page.get_by_text('Deleted "%s".' % renamed)
        toast.wait_for(state="visible")
        search.fill(tag)
        assert table.locator("tbody tr", has_text=renamed).count() == 0
        _open_backlog(page, live_server)
        search.fill(tag)
        assert table.locator("tbody tr", has_text=tag).count() == 0
    finally:
        ctx.close()


def test_backlog_create_failure_is_a_sentence_in_the_dialog(browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, _person(seeded, "enterprise_architect"))
        _open_backlog(page, live_server)
        page.route("**/api/enterprise/requirements", lambda route: route.fulfill(
            status=500, content_type="text/html", body="INTERNAL SERVER ERROR")
            if route.request.method == "POST" else route.continue_())
        page.get_by_role("button", name="Add requirement").first.click()
        create = page.locator("#create-req-modal")
        create.wait_for(state="visible")
        create.locator("#new-req-name").fill("Will not save")
        create.get_by_role("button", name="Add Requirement").click()
        alert = create.get_by_role("alert")
        alert.wait_for(state="visible")
        assert "could not be created" in alert.inner_text()
        assert "INTERNAL SERVER ERROR" not in page.inner_text("body")
        assert create.is_visible()
    finally:
        ctx.close()


def test_backlog_reader_cannot_save_or_delete_and_is_told_why(browser, live_server, seeded):
    data = _seed_requirements(seeded)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, _person(seeded, "procurement"))
        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", data["linked"])
        page.get_by_role("button", name="Edit requirement " + data["linked"]).click()
        detail = page.locator("#req-detail-modal")
        detail.wait_for(state="visible")
        detail.locator("[id^=req-status-]").select_option("Approved")
        detail.get_by_role("button", name="Save changes").click()
        page.get_by_text("can read requirements but not change them").first.wait_for(state="visible")
        assert "don't have access to this page" not in page.inner_text("body")
        assert detail.is_visible()

        detail.get_by_role("button", name="Delete requirement").click()
        confirm = page.locator("#confirm-dialog-delete-requirement [role=alertdialog]")
        confirm.wait_for(state="visible")
        confirm.get_by_role("button", name="Delete requirement").click()
        detail.get_by_role("alert").filter(has_text="not delete them").wait_for(state="visible")
        detail.get_by_role("button", name="Close").first.click()
        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", data["linked"])
        assert page.get_by_label("Status for " + data["linked"]).input_value() == "Draft"
    finally:
        ctx.close()


def test_backlog_copy_is_singular_ownerless_rows_say_so_and_fields_share_a_height(browser, live_server, seeded):
    data = _seed_requirements(seeded)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, _person(seeded, "enterprise_architect"))
        _open_backlog(page, live_server)
        page.fill("input[aria-label='Search requirements']", data["linked"])
        page.get_by_text("1 requirement", exact=True).wait_for(state="visible")
        row = page.locator("[data-testid=requirements-table] tbody tr", has_text=data["linked"])
        assert "No owner" in row.inner_text()
        page.get_by_role("button", name="Edit requirement " + data["linked"]).click()
        detail = page.locator("#req-detail-modal")
        detail.wait_for(state="visible")
        detail.locator("details summary").first.click()
        assert "\u2728" not in detail.inner_text()
        heights = detail.evaluate(
            "m => [...m.querySelectorAll('input:not([type=checkbox]), select')]"
            ".filter(e => e.offsetParent !== null && e.tagName !== 'TEXTAREA')"
            ".map(e => Math.round(e.getBoundingClientRect().height))")
        assert heights and set(heights) == {36}, heights
    finally:
        ctx.close()
