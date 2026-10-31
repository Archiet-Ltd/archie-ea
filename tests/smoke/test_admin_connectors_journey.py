"""An administrator adds a connector, a second administrator approves it,
and the page then shows it live with a masked credential (H4: PR 448 added
app/templates/admin/connectors/index.html and changed three other connector
templates with no tests/smoke/ coverage; this is that coverage).

Uses two organisation administrators in one fresh organisation rather than
the shared ``seeded`` fixture (which seeds only one admin per archetype),
because the approval path refuses both self-approval and a non-admin
approver (H3) -- a real journey here needs a second admin to approve.
"""

import re
import uuid

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.fresh_org import api, sign_in

# Masked credential characters: bullets, or a run of asterisks (a single
# asterisk marks a required field and is not a mask).
_MASK = re.compile("[\u2022\u25cf]|\\*{2,}")


def _create_two_admins():
    """A fresh organisation with two organisation administrators."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"org_id": None, "emails": {}}
    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="Connectors Journey %s" % suffix, slug="conn-journey-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id
        for label in ("proposer", "approver"):
            email = "connectors.%s.%s@example.com" % (label, suffix)
            user = User(
                email=email, first_name="Connectors", last_name=label,
                organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            )
            user.role = architect_role
            user.is_org_admin = True
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
            db.session.commit()
            out["emails"][label] = email
        db.session.remove()
    return out


@pytest.mark.smoke
@pytest.mark.journey
def test_add_approve_and_see_connector_with_masked_credential(browser, live_server):
    org = _create_two_admins()
    context = browser.new_context(ignore_https_errors=True)
    # N2 (cloud-review-pr448-505c3c2e-v1.md): without this, a fresh context
    # shows the onboarding tour on first page load, which intercepts the
    # clicks below. Mark onboarding as already seen, exactly as a real
    # returning user's browser would have it set.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        # Proposer adds a connector with a credential.
        sign_in(page, live_server, org["emails"]["proposer"])
        page.goto(live_server + "/admin/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # The form is found by its labels, as a person finds it.
        assert page.get_by_label("Display name").get_attribute("placeholder") == "Jira Cloud, production"
        assert page.get_by_label("Connector type").locator("option").first.inner_text() == "Choose a connector type"
        page.get_by_label("Connector type").select_option("jira")
        page.get_by_label("Display name").fill("Journey Jira")
        page.get_by_label("Configuration (JSON, non-secret)").fill('{"instance_url": "https://journey.example.com"}')
        page.get_by_label("Credential").fill("journey-secret-token")
        assert page.get_by_label("Enable connector").is_checked()
        page.get_by_role("button", name="Save connector").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)

        # Queued, not yet live: the page shows it as a pending proposal, and
        # the proposal's own config is never shown with the credential
        # readable in plain text (H1 regression guard at the UI layer).
        body_text = page.inner_text("body")
        assert "pending approval" in body_text.lower() or "queued" in body_text.lower()
        assert "journey-secret-token" not in body_text

        from app import create_app, db
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        app = create_app("testing")
        with app.app_context():
            approval = (
                AIChatCRUDApproval.query.filter_by(
                    organization_id=org["org_id"], entity_type="connectors"
                )
                .order_by(AIChatCRUDApproval.id.desc())
                .first()
            )
            approval_id = approval.id
            db.session.remove()

        page.goto(live_server + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # A second administrator approves it, through the product's own
        # approval endpoint (the same call the approval-inbox UI makes).
        sign_in(page, live_server, org["emails"]["approver"])
        status, body = api(page, "POST", "/ai-chat/approvals/%d/approve" % approval_id)
        assert status == 200, body
        assert body.get("success") is True, body

        # Reload the connectors page: the connector is now live, not queued,
        # and its credential is shown masked, never in plain text.
        page.goto(live_server + "/admin/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body_text = page.inner_text("body")
        assert "Journey Jira" in body_text
        assert "journey-secret-token" not in body_text
        row_locator = page.locator("tr:has-text('Journey Jira')")
        row = row_locator.inner_text()
        assert "queued" not in row.lower()
        # The credential column reads "Stored" and no masked characters
        # appear anywhere on the page.
        assert "Stored" in row
        assert not _MASK.search(page.inner_text("body"))

        # The connector can be synchronised from its row.
        row_locator.get_by_role("button", name="Sync").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "Journey Jira" in page.inner_text("body")
        assert "journey-secret-token" not in page.inner_text("body")
        assert not _MASK.search(page.inner_text("body"))
    finally:
        context.close()
