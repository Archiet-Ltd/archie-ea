"""Requirements backlog: a business architect opens the backlog of an
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


def _seed_requirements(seeded):
    """Two requirements in the standard organisation, one linked to a capability."""
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.solution_architect_models import SolutionRequirement

    tag = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    app = create_app("testing")
    with app.app_context():
        cap = BusinessCapability(name="Invoicing %s" % tag, organization_id=org_id)
        db.session.add(cap)
        db.session.flush()
        linked = SolutionRequirement(
            name="Customers can export invoices %s" % tag,
            description="Finance can download every invoice as a file.",
            status="Draft", priority=2, capability_id=cap.id, organization_id=org_id,
        )
        unlinked = SolutionRequirement(
            name="Payment reminders go out on day 7 %s" % tag,
            description="Overdue customers get one reminder.",
            status="Refined", priority=3, organization_id=org_id,
        )
        db.session.add_all([linked, unlinked])
        db.session.commit()
        return {"tag": tag, "cap_id": cap.id, "cap_name": cap.name,
                "linked": linked.name, "unlinked": unlinked.name}


def test_backlog_shows_linked_requirements_and_edits_persist(browser, live_server, seeded):
    data = _seed_requirements(seeded)
    tag = data["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        _open_backlog(page, live_server)

        # The list is the shared table, with a column for every cell the rows carry.
        table = page.locator("[data-testid=requirements-table]")
        assert table.locator("thead th").count() == 7
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

        # The capability link lands on a page that names the capability.
        linked_row.get_by_role("link", name=data["cap_name"]).click()
        page.wait_for_url("**/enterprise/capabilities/%s" % data["cap_id"])
        assert data["cap_name"] in page.inner_text("body")
        _open_backlog(page, live_server)

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
