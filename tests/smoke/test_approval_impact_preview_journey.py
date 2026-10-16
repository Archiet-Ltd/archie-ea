"""Approval inbox: see what a change affects before deciding, reject with a
reason that survives a reload, and open a proposal from the notification the
owner received.

Two journeys in one fresh organisation:

* Application Manager opens a pending proposal, sees the impact preview
  (services, interfaces, owners), rejects another with a reason, reloads and
  still sees it marked rejected with the reason.
* Business Owner (the owner of the changed application) follows the link in the
  in-app notification the proposal produced, sees the preview open on that
  proposal, approves it, reloads and sees it applied.

The estate and the proposals are created through the product's own services
(the proposal through AIChatApprovalService, which also writes the owner's
notification), so the screens read what a real run would have produced.
"""

import uuid

import pytest

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

ROLE_REVIEWER = "application_manager"
ROLE_OWNER = "non_technical_owner"
ROLE_REQUESTER = "solution_architect"


def _seed(org):
    """An application with a service and an interface behind it, owned by the
    business owner, and two pending proposals from the requester."""
    from app import create_app, db
    from flask import g

    app = create_app("testing")
    suffix = org["suffix"]
    out = {}
    with app.app_context():
        from app.models import ArchiMateElement, ArchiMateRelationship
        from app.models.application_owner import ApplicationOwner
        from app.models.application_portfolio import ApplicationComponent
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        org_id = org["org_id"]

        def element(name, etype):
            el = ArchiMateElement(
                name=name, type=etype, layer="application", organization_id=org_id
            )
            db.session.add(el)
            db.session.flush()
            return el

        root = element("Journey Billing %s" % suffix, "ApplicationComponent")
        service = element("Journey Invoice Service %s" % suffix, "ApplicationService")
        interface = element("Journey Invoice API %s" % suffix, "ApplicationInterface")
        for source, target in ((root, service), (service, interface)):
            db.session.add(
                ArchiMateRelationship(
                    source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id
                )
            )
        component = ApplicationComponent(
            name=root.name, organization_id=org_id, archimate_element_id=root.id
        )
        db.session.add(component)
        db.session.flush()
        db.session.add(
            ApplicationOwner(
                user_id=org["user_ids"][ROLE_OWNER],
                application_id=component.id,
                organization_id=org_id,
                ownership_type="primary",
            )
        )
        db.session.commit()

        with app.test_request_context("/"):
            g.current_org_id = org_id
            service_api = AIChatApprovalService(org["user_ids"][ROLE_REQUESTER])
            affecting = service_api.create_pending_approval(
                operation_type="update",
                entity_type="application",
                entity_id=component.id,
                original_command="journey",
                operation_payload={"description": "Journey description %s" % suffix},
                summary="Update description of %s" % root.name,
                chat_session_id="journey-%s" % suffix,
            )
            to_reject = service_api.create_pending_approval(
                operation_type="create",
                entity_type="capability",
                original_command="journey",
                operation_payload={"name": "Journey Capability %s" % suffix},
                summary="Create capability Journey Capability %s" % suffix,
                chat_session_id="journey-%s" % suffix,
            )
        assert affecting["success"] and to_reject["success"], (affecting, to_reject)
        out["affecting_id"] = affecting["approval_id"]
        out["reject_id"] = to_reject["approval_id"]
        out["service_name"] = service.name
        out["interface_name"] = interface.name
        db.session.remove()
    return out


@pytest.mark.smoke
@pytest.mark.journey
def test_application_manager_sees_impact_and_rejection_reason_survives_reload(browser, live_server):
    org = create_fresh_org(ROLE_REVIEWER, extra_roles=(ROLE_OWNER, ROLE_REQUESTER))
    seeded = _seed(org)
    context = browser.new_context(ignore_https_errors=True)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE_REVIEWER])
        page.goto(live_server + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        card = page.get_by_test_id("approval-card-%d" % seeded["affecting_id"])
        card.wait_for(timeout=PAGE_TIMEOUT)
        card.get_by_test_id("approval-impact-toggle-%d" % seeded["affecting_id"]).click()
        impact = page.get_by_test_id("approval-impact-%d" % seeded["affecting_id"])
        impact.get_by_text(seeded["service_name"]).wait_for(timeout=PAGE_TIMEOUT)
        text = impact.inner_text()
        assert seeded["interface_name"] in text
        assert "owner" in text.lower()

        # A new record has nothing depending on it, and says so.
        reject_card = page.get_by_test_id("approval-card-%d" % seeded["reject_id"])
        reject_card.get_by_test_id("approval-impact-toggle-%d" % seeded["reject_id"]).click()
        page.get_by_test_id("approval-impact-%d" % seeded["reject_id"]).get_by_text(
            "creates something new"
        ).wait_for(timeout=PAGE_TIMEOUT)

        reject_card.get_by_role("button", name="Reject").click()
        reject_card.get_by_label("Why are you rejecting this?").fill("We already have this capability")
        reject_card.get_by_role("button", name="Confirm Reject").click()
        page.get_by_test_id("approval-decided-%d" % seeded["reject_id"]).wait_for(timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        decided = page.get_by_test_id("approval-decided-%d" % seeded["reject_id"])
        decided.wait_for(timeout=PAGE_TIMEOUT)
        assert "Rejected" in decided.inner_text()
        assert "We already have this capability" in decided.inner_text()
        assert page.get_by_test_id("approval-card-%d" % seeded["reject_id"]).count() == 0
    finally:
        context.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_business_owner_follows_the_notification_and_approves(browser, live_server):
    org = create_fresh_org(ROLE_REVIEWER, extra_roles=(ROLE_OWNER, ROLE_REQUESTER))
    seeded = _seed(org)
    context = browser.new_context(ignore_https_errors=True)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE_OWNER])

        # The bell has the proposal; follow its link as the owner would.
        page.goto(live_server + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        bell = page.evaluate(
            """async () => {
                const r = await fetch('/api/notifications', {headers: {'Accept': 'application/json'}, credentials: 'same-origin'});
                return [r.status, await r.json()];
            }"""
        )
        assert bell[0] == 200, bell
        links = [item["url"] for item in bell[1]["items"] if item.get("url")]
        wanted = [link for link in links if link.endswith("approval=%d" % seeded["affecting_id"])]
        assert len(wanted) == 1, links
        page.goto(live_server + wanted[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The preview is already open on that proposal.
        impact = page.get_by_test_id("approval-impact-%d" % seeded["affecting_id"])
        impact.get_by_text(seeded["service_name"]).wait_for(timeout=PAGE_TIMEOUT)

        page.get_by_test_id("approval-card-%d" % seeded["affecting_id"]).get_by_role("button", name="Approve").click()
        page.get_by_test_id("approval-decided-%d" % seeded["affecting_id"]).wait_for(timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        decided = page.get_by_test_id("approval-decided-%d" % seeded["affecting_id"])
        decided.wait_for(timeout=PAGE_TIMEOUT)
        assert "Approved" in decided.inner_text()
        assert page.get_by_test_id("approval-card-%d" % seeded["affecting_id"]).count() == 0
    finally:
        context.close()
