"""A platform administrator runs a workflow from the integration catalog,
approves its approval gate, and sees the run in the instances list.

The integration pages (workflow catalog, workflow details, instance
details, instance list) serve the platform-admin persona. The journey signs
in as that persona, starts a real workflow instance through the product's
own Start button, drives it through its waiting-approval gate with the real
Approve dialog, and confirms the completed run appears in the instances
list and survives the filters. A second organisation sees none of the first
organisation's instances.
"""

import time

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_definition(org_id, user_id, suffix):
    """Create one workflow definition (global reference data) through the
    product's own service, so the catalog page has linked data to show."""
    from flask import g

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.services.ea_workflow_engine import EAWorkflowEngine

        code = "PILOT_%s" % suffix.upper()
        with app.test_request_context("/"):
            g.current_org_id = org_id
            engine = EAWorkflowEngine()
            engine.create_workflow_definition(
                workflow_code=code,
                workflow_name="Pilot approval flow %s" % suffix,
                workflow_category="architecture_governance",
                workflow_description=(
                    "A minimal two-step flow: one automated review, one approval gate."
                ),
                steps=[
                    {"step_id": "review", "step_name": "Begin review",
                     "step_type": "automated", "handler": "start"},
                    {"step_id": "approve", "step_name": "Confirm findings",
                     "step_type": "approval", "handler": "approval",
                     "requires_approval": True},
                ],
            )
            db.session.commit()
        return code


def _wait_for_status(page, status_badge, texts):
    """Poll via reload until the instance status badge reads one of *texts*.

    Steps run in a background thread server-side, so the UI status only
    updates on a fresh load (same pattern as test_ea_workflow_start_journey).
    """
    for _ in range(30):
        text = status_badge.inner_text(timeout=PAGE_TIMEOUT).strip()
        if text in texts:
            return text
        page.wait_for_timeout(1000)
        page.reload(timeout=PAGE_TIMEOUT)
    return status_badge.inner_text(timeout=PAGE_TIMEOUT).strip()


def test_platform_admin_starts_and_approves_a_workflow(browser, live_server):
    org = create_fresh_org("platform_admin")
    other = create_fresh_org("platform_admin")
    suffix = org["suffix"]
    code = _seed_definition(org["org_id"], org["user_ids"]["platform_admin"], suffix)
    flow_name = "Pilot approval flow %s" % suffix
    instance_code = "PILOT_%s" % suffix.upper()

    # The persona the pages serve: the platform administrator, not a generic
    # administrator or an org-admin account (brief: assert the role).
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        persona = db.session.get(User, org["user_ids"]["platform_admin"])
        assert persona is not None
        assert persona.enterprise_role == "platform_admin", persona.enterprise_role
        assert persona.is_org_admin is False, (
            "the platform-admin persona must not be an org-admin account"
        )
        assert persona.role.name != "Administrator", persona.role.name

    context = browser.new_context(
        ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["platform_admin"])

        # ---- Catalog: find the workflow, filter by category, open it ----
        page.goto(live_server + "/integration/workflows",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text(
            "Workflow Catalog", timeout=PAGE_TIMEOUT)

        row = page.locator("tr", has_text=flow_name)
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)

        page.select_option("#category", "architecture_governance")
        page.get_by_role("button", name="Filter").click()
        page.wait_for_url("**/integration/workflows*", timeout=PAGE_TIMEOUT)

        row = page.locator("tr", has_text=flow_name)
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        row.get_by_role("link", name="View Details").click()
        page.wait_for_url("**/integration/workflow/*", timeout=PAGE_TIMEOUT)

        expect(page.locator("h1")).to_contain_text(flow_name, timeout=PAGE_TIMEOUT)
        expect(page.locator("text=Begin review")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("text=Confirm findings")).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Start the workflow through the real button ----
        page.get_by_role("button", name="Start Workflow").click()
        page.wait_for_url("**/integration/instance/*", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text("Instance", timeout=PAGE_TIMEOUT)

        status_badge = page.get_by_test_id("instance-status-badge")
        started_status = _wait_for_status(page, status_badge, ("Waiting Approval",))
        assert started_status == "Waiting Approval", (
            "the started workflow never reached its approval gate; status was %r"
            % started_status
        )

        # Both steps are in the execution history
        expect(page.locator("text=Begin review")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("text=Confirm findings")).to_be_visible(timeout=PAGE_TIMEOUT)

        # ---- Approve the gate through the real modal ----
        page.get_by_role("button", name="Approve Step").click()
        modal = page.locator("#approve-step-modal")
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        modal.locator("textarea").fill("Findings look correct, approve.")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            modal.get_by_role("button", name="Approve & Resume").click()

        # The run completes after the approval
        status_badge = page.get_by_test_id("instance-status-badge")
        final_status = _wait_for_status(page, status_badge, ("Completed",))
        assert final_status == "Completed", (
            "the workflow did not complete after approval; status was %r"
            % final_status
        )

        # ---- The instances list shows the run, and the filter keeps it ----
        page.goto(live_server + "/integration/instances",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        inst_row = page.locator("tr", has_text=instance_code)
        expect(inst_row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(inst_row.locator("text=Completed")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.select_option("#status", "completed")
        page.get_by_role("button", name="Filter").click()
        page.wait_for_url("**/integration/instances*", timeout=PAGE_TIMEOUT)

        inst_row = page.locator("tr", has_text=instance_code)
        expect(inst_row).to_be_visible(timeout=PAGE_TIMEOUT)
        inst_row.get_by_role("link", name="View").click()
        page.wait_for_url("**/integration/instance/*", timeout=PAGE_TIMEOUT)
        expect(page.locator("h1")).to_contain_text(instance_code, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("instance-status-badge")).to_have_text(
            "Completed", timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    # ---- A second organisation sees none of the first org's instances ----
    other_context = browser.new_context(
        ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    other_context.add_init_script(
        "localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    other_page = other_context.new_page()
    try:
        sign_in(other_page, live_server, other["emails"]["platform_admin"])
        other_page.goto(live_server + "/integration/instances",
                        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Check the instances table specifically, not the filter dropdown
        # (workflow definitions are global reference data so they appear in
        # every org's catalog filter).
        table = other_page.locator("table[aria-label='Workflow instances']")
        table_text = table.inner_text(timeout=PAGE_TIMEOUT) if table.count() > 0 else ""
        assert instance_code not in table_text, (
            "the first organisation's instance leaked into the second"
        )
        expect(other_page.locator("text=No workflow instances yet")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        expect(other_page.get_by_role("link", name="Browse workflows")).to_be_visible(
            timeout=PAGE_TIMEOUT)
    finally:
        other_context.close()