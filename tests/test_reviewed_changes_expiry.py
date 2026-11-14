"""Reviewed changes: what is past its expiry is decided, the badge counts only what is in review, and
"Requested by" names what proposed the change."""

import uuid
from datetime import datetime, timedelta

from tests.test_approval_inbox import _make_user


def _row(org_id, user_id, *, expired, source_table=None, label="Thing"):
    from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

    approval = create_approval_record(
        organization_id=org_id,
        operation_type="create",
        entity_type="capability",
        summary="Create capability '%s'" % label,
        operation_payload={"name": label},
        user_id=user_id,
        source_table=source_table,
        source_id=uuid.uuid4().int % 2_000_000_000 if source_table else None,
        settle=False,
    )
    if expired:
        approval.expires_at = datetime.utcnow() - timedelta(days=3)
    return approval


def test_expired_waiting_item_is_declined_with_its_reason_and_not_counted(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.user import User
    from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
    from app.modules.ai_chat.services.change_review import EXPIRED_REASON

    org, other = make_org("expiry-a"), make_org("expiry-b")
    person = _make_user(db_session, org.id, "Asker")
    stranger = _make_user(db_session, other.id, "Stranger")
    with tenant_ctx(org.id):
        old = _row(org.id, None, expired=True, label="Old Blueprint")
        fresh = _row(org.id, person.id, expired=False, label="Fresh Cap")
        db_session.flush()
        old_id, fresh_id = old.id, fresh.id
        db_session.commit()
    with tenant_ctx(other.id):
        theirs = _row(other.id, stranger.id, expired=True, label="Other Org Cap")
        db_session.flush()
        theirs_id = theirs.id
        db_session.commit()
    person_id = person.id
    with tenant_ctx(org.id):
        # Counted before the page decides it: the expired one is already not in review.
        assert AIChatApprovalService(person_id).count_in_review() == 1

    client = app.test_client()
    login_as(client, User.query.get(person_id))
    html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert 'data-testid="approval-card-%s"' % fresh_id in html
    assert 'data-testid="approval-card-%s"' % old_id not in html
    assert 'data-testid="approval-decided-%s"' % old_id in html
    assert "Expired before review." in html
    assert 'data-testid="approval-propose-again-%s"' % old_id in html
    assert "Other Org Cap" not in html

    with tenant_ctx(org.id):
        row = AIChatCRUDApproval.query.filter_by(id=old_id, organization_id=org.id).one()
        assert row.status == ApprovalStatus.REJECTED and row.rejected_reason == EXPIRED_REASON
    with tenant_ctx(other.id):
        row = AIChatCRUDApproval.query.filter_by(id=theirs_id, organization_id=other.id).one()
        assert row.status == ApprovalStatus.PENDING  # another organisation's page did not decide it


def test_badge_is_hidden_with_only_expired_or_decided_items(app, db_session, make_org, tenant_ctx, login_as):
    from app.models.user import User

    org = make_org("badge-hidden")
    person = _make_user(db_session, org.id, "Badge")
    with tenant_ctx(org.id):
        _row(org.id, None, expired=True, label="Gone One")
        _row(org.id, None, expired=True, label="Gone Two")
        db_session.commit()
    client = app.test_client()
    login_as(client, User.query.get(person.id))
    html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert 'data-testid="nav-count-approvals"' not in html

    with tenant_ctx(org.id):
        _row(org.id, person.id, expired=False, label="Live One")
        db_session.commit()
    html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    assert 'data-testid="nav-count-approvals"' in html


def test_requested_by_names_the_import_that_proposed_the_change(app, db_session, make_org, tenant_ctx, login_as):
    from app.connectors.github import PROPOSAL_SOURCE
    from app.models.user import User

    org = make_org("proposer")
    person = _make_user(db_session, org.id, "Viewer")
    with tenant_ctx(org.id):
        proposal = _row(org.id, None, expired=False, source_table=PROPOSAL_SOURCE, label="Imported Cap")
        db_session.flush()
        proposal_id = proposal.id
        db_session.commit()
    client = app.test_client()
    login_as(client, User.query.get(person.id))
    html = client.get("/ai-chat/approvals/inbox").get_data(as_text=True)
    card = html.split('data-testid="approval-card-%s"' % proposal_id, 1)[1].split("approval-card-", 1)[0]
    assert "Requested by" in card and "Github connector" in card
    assert "The system" not in html
