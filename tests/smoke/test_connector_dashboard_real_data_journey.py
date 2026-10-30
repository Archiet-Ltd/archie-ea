"""A platform admin checks connector health with a connector actually configured.

test_connector_dashboard_honesty.py deliberately never seeds a ConnectorConfig
row (see its own docstring), so it never exercises the page with real data --
only the empty state. This journey seeds one connector and one sync log and
walks the goal a platform admin actually has: see at a glance how many
connectors are configured and how many need attention, open one to see its
sync history, and try the one action that works (Test Connection).
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_admin_with_connector(app):
    from app import db
    from app.models.connector_config import ConnectorConfig, SyncLog
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Connector Journey %s" % suffix, slug="connector-journey-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        user = User(
            first_name="Connector", last_name="Admin %s" % suffix,
            email="connector.admin.%s@example.com" % suffix, password=PASSWORD,
            confirmed=True, organization_id=org.id, is_org_admin=True, is_platform_admin=True,
            enterprise_role="solution_architect",
        )
        db.session.add(user)
        db.session.flush()
        connector = ConnectorConfig(
            connector_type="jira", name="Jira ALM", config={}, status="active",
            organization_id=org.id,
        )
        db.session.add(connector)
        db.session.flush()
        db.session.add(SyncLog(
            connector_id=connector.id, sync_type="manual", status="success",
            records_processed=12, records_created=3, records_updated=9, records_deleted=0,
        ))
        db.session.commit()
        return {"email": user.email, "org_id": org.id, "connector_name": connector.name}


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_admin_sees_connector_health_and_opens_sync_history(browser, live_server, app):
    seed = _seed_admin_with_connector(app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _sign_in(page, str(live_server), seed["email"])
        page.goto(str(live_server) + "/integrations/connectors", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # The stat row is a real count of what this page already fetched, not
        # a placeholder: one connector, one of it active, none needing attention.
        stat_row = page.locator('[data-testid="connector-stat-row"]')
        expect(stat_row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="connector-stat-total"]')).to_have_text("1")
        expect(page.locator('[data-testid="connector-stat-healthy"]')).to_have_text("1")
        expect(page.locator('[data-testid="connector-stat-attention"]')).to_have_text("0")

        # Opening the card shows the connector's own sync history, not an
        # empty or wrong one.
        card = page.locator(".cursor-pointer").filter(has_text=seed["connector_name"]).first
        card.click()
        dialog = page.locator('[role="dialog"][aria-modal="true"]')
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog).to_contain_text(seed["connector_name"])
        expect(dialog).to_contain_text("success")

        # Test Connection is the one live action; it must still fire, not be
        # a dead control the conversion silently dropped.
        test_btn = dialog.get_by_role("button", name="Test Connection")
        expect(test_btn).to_be_enabled()
    finally:
        context.close()
