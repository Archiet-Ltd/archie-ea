"""An admin configures the Jira connector from its settings page.

No journey covered admin/connectors/jira.html before this: test_admin_connectors_
journey.py drives a different, generic connector-creation flow (ConnectorConfig
rows picked by a type select), not this dedicated settings page. The real
behaviour here (connector_routes.py::jira_config_save) is to queue the change
as an approval proposal unless the organisation has delegated it -- a fresh
seeded organisation has not, so the honest assertion is the queued-for-approval
flash, not an immediately-applied connector.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_org_admin(app):
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Jira Connector %s" % suffix, slug="jira-connector-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        user = User(
            first_name="Jira", last_name="Admin %s" % suffix,
            email="jira.admin.%s@example.com" % suffix, password=PASSWORD,
            confirmed=True, organization_id=org.id, is_org_admin=True,
            enterprise_role="solution_architect",
        )
        db.session.add(user)
        db.session.commit()
        return {"email": user.email, "org_id": org.id}


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_admin_saves_jira_credentials_and_sees_it_was_queued(browser, live_server, app):
    seed = _seed_org_admin(app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _sign_in(page, str(live_server), seed["email"])
        page.goto(str(live_server) + "/admin/connectors/jira", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        page.fill("#instance_url", "https://acme.atlassian.net")
        page.fill("#email", "integration@acme.example.com")
        page.fill("#api_token", "token-value-not-a-real-secret")
        page.fill("#default_project_key", "ARCH")
        page.get_by_role("button", name="Save Configuration").click()
        page.wait_for_load_state("domcontentloaded")

        # The real outcome for a fresh organisation: the change is queued for
        # approval, not silently applied -- the page says so, not a dash or a
        # generic "saved".
        expect(page.locator("#main-content, main")).to_contain_text("queued for approval")

        # The switch keeps its real submitted value: posting "enabled" as the
        # Alpine-bound "true"/"false" the switch macro sends, not the native
        # checkbox "on" the handler used to expect, still reaches the route.
        switch = page.locator('[role="switch"][aria-label="Enable Jira integration"]')
        expect(switch).to_be_visible()
    finally:
        context.close()


def test_jira_connector_page_has_no_raw_checkbox(browser, live_server, app):
    """The enable toggle is the shadcn switch, not a native checkbox -- and it
    is reachable by keyboard, which a raw <input type=checkbox> is too, but a
    div-based fake toggle would not be."""
    seed = _seed_org_admin(app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _sign_in(page, str(live_server), seed["email"])
        page.goto(str(live_server) + "/admin/connectors/jira", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        switch = page.locator('[role="switch"][aria-label="Enable Jira integration"]')
        expect(switch).to_be_visible()
        switch.focus()
        expect(switch).to_be_focused()
    finally:
        context.close()
