"""A platform admin registers an agent, sees activation refused with the
missing fields named, sets owner/limits/charter, activates, reloads, and
sees it active.

Covers the three new templates introduced with R1-B56
(ai_chat/agent_registry/list.html, new.html, detail.html), which had no
browser smoke journey before this.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_platform_admin_registers_and_activates_an_agent(browser, live_server):
    org = create_fresh_org("platform_admin", org_admin=True)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', '1'); } catch (e) {}")
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["platform_admin"])

    page.goto(live_server + "/admin/agent-registry/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#name", "Risk Monitor")
    page.fill("#purpose", "Flags risk exposure for review.")
    page.get_by_role("button", name="Register").click()
    page.wait_for_url(lambda url: "/admin/agent-registry/" in url and url.rstrip("/").split("/")[-1].isdigit(),
                       timeout=PAGE_TIMEOUT)

    expect(page.get_by_test_id("activate-button")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_test_id("activate-button").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("activation-missing")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Security fix (6 Oct 2026): owner is chosen from a search of this
    # organisation's own users (Platform.fetch.get against
    # organization.stakeholder_search), not typed as a raw user id.
    page.fill("[data-testid=owner-search-input]", "Launch")
    expect(page.get_by_test_id("owner-search-result").first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.get_by_test_id("owner-search-result").first.click()
    page.get_by_test_id("set-owner-button").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    page.fill("[data-testid=max-writes-input]", "10")
    page.get_by_role("button", name="Set limits").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    page.fill("[data-testid=charter-persona-input]", "risk_monitor")
    page.get_by_test_id("propose-charter-button").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    # The charter proposal needs a second reviewer; execute it directly
    # through the service exactly as the approval dispatcher would, since
    # this journey is testing the registry screen, not the inbox.
    registration_id = int(page.url.rstrip("/").split("/")[-1])
    _approve_latest_charter_proposal(org["org_id"], registration_id)

    page.get_by_test_id("activate-button").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("agent-status")).to_have_text("active", timeout=PAGE_TIMEOUT)
    context.close()


def _approve_latest_charter_proposal(org_id, registration_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.models.agent_registration import AgentRegistration
        from app.modules.ai_chat.services.agent_registry_service import execute_charter_change

        approval = (
            AIChatCRUDApproval.query.filter_by(
                organization_id=org_id, operation_type="agent_charter_change",
                entity_id=registration_id,
            )
            .order_by(AIChatCRUDApproval.id.desc())
            .first()
        )
        reg = AgentRegistration.query.filter_by(id=registration_id, organization_id=org_id).first()
        import json

        payload = json.loads(approval.operation_payload)
        execute_charter_change(reg, payload)
        db.session.commit()
        db.session.remove()
