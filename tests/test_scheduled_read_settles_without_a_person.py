"""The hourly re-read of a connection has no requesting person, and it settles on the server all the same.

It is decided as the organisation's own administrator account, the read runs, what the read proposed is settled
in the same pass, and nothing is left waiting for anyone. A GitHub connection and a Jira connection are read in
turn; a second organisation's connection is never touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_connector import connect, github, org_a, org_b  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def _not_delegated(*orgs):
    from app.extensions import db

    for org in orgs:
        settings = dict(org.settings or {})
        settings["connector_action_delegation"] = {"sync": False}
        org.settings = settings
    db.session.flush()


def _admin(db_session, org):
    from tests.test_connector_approval_flow import _make_admin

    return _make_admin(db_session, org.id, "Founder")


def _reread(tenant_ctx, org):
    from app.services.connector_schedule import reread_due_connectors

    with tenant_ctx(org.id):
        return reread_due_connectors(org.id, now=datetime.utcnow() + timedelta(hours=2))


def _the_read_request(org_id):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    return AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, entity_type="connectors", operation_type="sync").one()


def _nobody_approved(approval):
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, ApprovalStatus

    assert approval.user_id is None and approval.status == ApprovalStatus.APPROVED
    assert approval.approved_by_id is None
    approved = AIChatApprovalAuditLog.query.filter_by(approval_id=approval.id, event="approved").all()
    assert approved and all(a.actor_type == "system" for a in approved)


def test_scheduled_read_settles_without_a_person_for_a_github_connection(
    db_session, github, connect, tenant_ctx, org_a, org_b
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.connector_config import SyncLog

    _not_delegated(org_a, org_b)
    _admin(db_session, org_a)
    _admin(db_session, org_b)
    cfg_a, cfg_b = connect(org_a), connect(org_b)
    db_session.commit()

    outcome = _reread(tenant_ctx, org_a)

    db_session.expire_all()
    assert outcome["proposed"] == [cfg_a.id] and outcome["failed"] == []
    assert SyncLog.query.filter_by(connector_id=cfg_a.id, status="completed").count() >= 1
    _nobody_approved(_the_read_request(org_a.id))
    # What the read proposed was settled in the same pass: every one is applied or linked, none waits.
    proposals = AIChatCRUDApproval.query.filter(
        AIChatCRUDApproval.organization_id == org_a.id, AIChatCRUDApproval.source_table == "github_connector").all()
    assert proposals, "the read proposed something"
    assert all(p.status == ApprovalStatus.APPROVED for p in proposals), [(p.id, p.status) for p in proposals]
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_a.id, status=ApprovalStatus.PENDING).count() == 0
    # The other organisation's connection was not read.
    assert SyncLog.query.filter_by(connector_id=cfg_b.id).count() == 0
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_b.id).count() == 0


def test_scheduled_read_settles_without_a_person_for_a_jira_connection(
    db_session, tenant_ctx, org_a, org_b, github, monkeypatch
):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
    from app.models.connector_config import SyncLog
    from app.services import connector_framework
    from tests.test_connector_approval_flow import _connector

    _not_delegated(org_a, org_b)
    _admin(db_session, org_a)
    cfg_a = _connector(db_session, org_a.id, "jira", "Jira A")
    cfg_b = _connector(db_session, org_b.id, "jira", "Jira B")

    class Reads:
        scope = None

        async def batch_sync(self, since=None):
            return {"status": "completed", "records_processed": 3, "records_created": 0, "records_updated": 0}

    monkeypatch.setattr(connector_framework, "build_connector", lambda cfg, org_id: Reads())
    db_session.commit()

    outcome = _reread(tenant_ctx, org_a)

    db_session.expire_all()
    assert outcome["proposed"] == [cfg_a.id]
    assert SyncLog.query.filter_by(connector_id=cfg_a.id, status="completed").count() == 1
    _nobody_approved(_the_read_request(org_a.id))
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_a.id, status=ApprovalStatus.PENDING).count() == 0
    assert SyncLog.query.filter_by(connector_id=cfg_b.id).count() == 0
    assert AIChatCRUDApproval.query.filter_by(organization_id=org_b.id).count() == 0


def test_a_scheduled_read_of_an_organisation_with_no_administrator_is_declined_with_the_reason(
    db_session, github, connect, tenant_ctx, org_a
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import SyncLog
    from app.modules.ai_chat.services.change_review import NO_SYSTEM_ACTOR_REASON

    _not_delegated(org_a)
    cfg = connect(org_a)
    db_session.commit()

    _reread(tenant_ctx, org_a)

    db_session.expire_all()
    request = _the_read_request(org_a.id)
    assert request.status == ApprovalStatus.REJECTED and request.rejected_reason == NO_SYSTEM_ACTOR_REASON
    assert SyncLog.query.filter_by(connector_id=cfg.id).count() == 0
