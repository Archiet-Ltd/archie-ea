"""Pins the AI chat approval-execution path: a decision on a queued change runs
AIChatApprovalService.approve_and_execute, which mutates the target record, or
declines it with the reviewer's reason and leaves the record alone. The page has
no approve or reject route, so these tests drive the service the way the
automatic review does.

Uses the shared fixtures in tests/conftest.py.
"""

import uuid

import pytest


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_admin(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Ai",
        last_name="Approve",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="platform_admin",
    )
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def admin_client(app, db_session, make_org):
    org = make_org("aiapprove")
    user = _make_admin(db_session, org, "aiapprove")
    client = app.test_client()
    _login(client, user.id)
    return org, client, user


def _approve_through_service(tenant_ctx, org_id, user_id, approval_id):
    """The decision as the automatic review makes it; the page has no approve route any more."""
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org_id):
        return AIChatApprovalService(user_id).approve_and_execute(approval_id, user_id)


def test_approving_actually_executes_the_operation(admin_client, app, tenant_ctx, db_session, monkeypatch):
    """Uses a SECOND admin as approver, not the requester (V-01, 17 Aug 2026
    QA register): approve_and_execute now refuses self-approval, so this test
    would fail with 400 APPROVAL_DENIED if it reused the requester as
    approver -- see test_ai_r32_ai_permission_gate.py for that refusal
    itself. The behaviour this file exists to pin (the route actually
    executes, not just 500s on a missing method) is orthogonal to who does
    the approving, so a second, distinct approver preserves the original
    intent without exercising the vulnerability the fix closes.
    """
    org, client, user = admin_client
    approver = _make_admin(db_session, org, "aiapprove-approver")
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org.id):
        appc = ApplicationComponent(name="Pre-approval name")
        db.session.add(appc)
        db.session.commit()
        app_id = appc.id

        service = AIChatApprovalService(user.id)
        created = service.create_pending_approval(
            operation_type="update",
            entity_type="application",
            original_command="rename this application",
            operation_payload={"name": "Post-approval name"},
            summary="Rename Pre-approval name to Post-approval name",
            entity_id=app_id,
        )
        assert created.get("success") is True, created
        approval_id = created["approval_id"]

    # A person's Approve goes through the review, so a reviewer that passes the change stands in.
    from tests.test_change_review import PASS_ALL, FakeReviewer

    FakeReviewer([PASS_ALL]).install(monkeypatch)
    _login(client, approver.id)

    body = _approve_through_service(tenant_ctx, org.id, approver.id, approval_id)
    assert body["success"] is True, body

    with tenant_ctx(org.id):
        db.session.refresh(appc)
        assert appc.name == "Post-approval name"


def test_approve_with_a_failing_review_does_not_execute_and_says_why(
    admin_client, app, tenant_ctx, db_session, monkeypatch
):
    org, client, user = admin_client
    approver = _make_admin(db_session, org, "aiapprove-failing")
    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org.id):
        appc = ApplicationComponent(name="Kept name")
        db.session.add(appc)
        db.session.commit()
        app_id = appc.id
        created = AIChatApprovalService(user.id).create_pending_approval(
            operation_type="update",
            entity_type="application",
            original_command="rename this application",
            operation_payload={"name": "Rejected name"},
            summary="Rename Kept name to Rejected name",
            entity_id=app_id,
        )
        approval_id = created["approval_id"]

    from tests.test_change_review import FakeReviewer, _fail

    reason = "The new name does not match what the person asked for."
    FakeReviewer([_fail(1, reason)]).install(monkeypatch)
    body = _approve_through_service(tenant_ctx, org.id, approver.id, approval_id)
    assert body["success"] is False, body
    assert reason in str(body), body

    with tenant_ctx(org.id):
        db.session.refresh(appc)
        assert appc.name == "Kept name"


def test_a_requesters_own_approve_with_a_failing_review_does_not_execute_and_says_why(
    admin_client, app, tenant_ctx, db_session, monkeypatch
):
    """With no second person, the independent review is the control: the requester presses Approve, the
    reviewer fails the change, nothing is written, and the person is told the reviewer's reason."""
    org, client, user = admin_client
    from app import db
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

    with tenant_ctx(org.id):
        appc = ApplicationComponent(name="Own kept name")
        db.session.add(appc)
        db.session.commit()
        app_id = appc.id
        created = AIChatApprovalService(user.id).create_pending_approval(
            operation_type="update",
            entity_type="application",
            original_command="rename this application",
            operation_payload={"name": "Own rejected name"},
            summary="Rename Own kept name to Own rejected name",
            entity_id=app_id,
        )
        approval_id = created["approval_id"]

    from tests.test_change_review import FakeReviewer, _fail

    reason = "The new name does not match what the person asked for."
    FakeReviewer([_fail(1, reason)] * 3, revisions=[{"arguments": {"name": "Own rejected name"},
                                                    "summary": "same"}] * 2).install(monkeypatch)

    body = _approve_through_service(tenant_ctx, org.id, user.id, approval_id)
    assert body["success"] is False, body
    assert reason in str(body), body

    with tenant_ctx(org.id):
        db.session.refresh(appc)
        assert appc.name == "Own kept name"
        assert db.session.get(AIChatCRUDApproval, approval_id).status == ApprovalStatus.REJECTED
