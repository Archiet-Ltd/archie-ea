"""A change to a connection, a read of one, and the hourly re-read all settle on the server.

The person who saves a connection (or the clock, for the hourly read) is the only one who acts. The change is
checked, reviewed, then applied or declined with its reasons; nobody is asked to approve it. Two real
organisations hold a connection each.
"""

from __future__ import annotations

import json

import pytest

from tests.test_connector_approval_flow import (  # noqa: F401
    _connector,
    _connector_hosts_need_no_dns,
    _make_admin,
    admin_a,
    admin_b,
    client,
    org_a,
    org_b,
)
from tests.test_proposals_settle_without_a_person import Reviewer, _fail

pytestmark = pytest.mark.usefixtures("db_session")


def _proposals(org_id, operation):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval

    return AIChatCRUDApproval.query.filter_by(
        organization_id=org_id, entity_type="connectors", operation_type=operation
    ).order_by(AIChatCRUDApproval.id).all()


def _settle(tenant_ctx, db_session, org):
    """What the job worker does with the settle job the save queued."""
    from app.modules.ai_chat.services import change_review

    db_session.commit()
    with tenant_ctx(org.id):
        return change_review.settle_proposals(org.id)


def _save_servicenow(client, name, credential="a-secret-value"):
    return client.post("/admin/connectors", data={
        "connector_type": "servicenow", "name": name,
        "config_json": '{"instance_url": "https://org-a.service-now.com"}',
        "credential": credential, "enabled": "1",
    })


def _decided_by_the_system(approval_id):
    """No person approved it: the record names no approving user and its audit rows are the system's."""
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval

    row = AIChatCRUDApproval.query.filter_by(id=approval_id).one()
    assert row.approved_by_id is None
    approved = AIChatApprovalAuditLog.query.filter_by(approval_id=approval_id, event="approved").all()
    assert approved and all(a.actor_type == "system" for a in approved)


def test_connector_change_settles_without_a_person(db_session, org_a, admin_a, client, login_as, tenant_ctx):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.models.job import Job

    login_as(client, admin_a)
    assert _save_servicenow(client, "Settled ServiceNow").status_code == 302
    proposal = _proposals(org_a.id, "create")[0]
    assert proposal.status == ApprovalStatus.PENDING
    assert Job.query.filter(Job.task == "settle_proposals").count() >= 1, "the save queued its settling"
    assert ConnectorConfig.query.filter_by(organization_id=org_a.id, connector_type="servicenow").first() is None

    counts = _settle(tenant_ctx, db_session, org_a)

    db_session.expire_all()
    assert counts["applied"] == 1 and counts["in_review"] == 0, counts
    assert _proposals(org_a.id, "create")[0].status == ApprovalStatus.APPROVED
    cfg = ConnectorConfig.query.filter_by(organization_id=org_a.id, connector_type="servicenow").one()
    assert cfg.name == "Settled ServiceNow"
    with tenant_ctx(org_a.id):
        assert OrgCredentialVault().retrieve_credentials(org_a.id, "servicenow") == {"credential": "a-secret-value"}
    _decided_by_the_system(proposal.id)


def test_connector_update_settles_without_a_person(db_session, org_a, admin_a, client, login_as, tenant_ctx):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import ConnectorConfig

    cfg = _connector(db_session, org_a.id, "jira", "Jira Prime")
    db_session.commit()
    login_as(client, admin_a)
    client.post("/admin/connectors", data={
        "connector_type": "jira", "connector_id": cfg.id, "name": "Jira Prime",
        "config_json": '{"instance_url": "https://prime.atlassian.net"}', "enabled": "1",
    })
    update = _proposals(org_a.id, "update")[0]
    assert update.status == ApprovalStatus.PENDING
    assert ConnectorConfig.query.get(cfg.id).config.get("instance_url") is None

    _settle(tenant_ctx, db_session, org_a)

    db_session.expire_all()
    assert _proposals(org_a.id, "update")[0].status == ApprovalStatus.APPROVED
    assert ConnectorConfig.query.get(cfg.id).config.get("instance_url") == "https://prime.atlassian.net"
    _decided_by_the_system(update.id)


def test_connector_delete_settles_without_a_person(db_session, org_a, admin_a, client, login_as, tenant_ctx):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import ConnectorConfig

    cfg = _connector(db_session, org_a.id, "jira", "Jira To Remove")
    db_session.commit()
    cfg_id = cfg.id
    login_as(client, admin_a)
    client.post(f"/admin/connectors/{cfg_id}/delete")
    delete = _proposals(org_a.id, "delete")[0]
    assert delete.status == ApprovalStatus.PENDING and ConnectorConfig.query.get(cfg_id) is not None

    _settle(tenant_ctx, db_session, org_a)

    db_session.expire_all()
    assert _proposals(org_a.id, "delete")[0].status == ApprovalStatus.APPROVED
    assert ConnectorConfig.query.get(cfg_id) is None
    _decided_by_the_system(delete.id)


def test_connector_sync_settles_without_a_person(db_session, org_a, admin_a, tenant_ctx, monkeypatch):
    """A read asked for by a person runs once it has been checked; nobody approves it."""
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import SyncLog
    from app.services import connector_framework
    from app.services.connector_framework import propose_connector_sync

    cfg = _connector(db_session, org_a.id, "jira", "Jira Reader")
    _fake_reads(monkeypatch, connector_framework)
    with tenant_ctx(org_a.id):
        proposal = propose_connector_sync(org_id=org_a.id, cfg=cfg, actor_user_id=admin_a.id)
    proposal_id = proposal.id
    assert proposal.status == ApprovalStatus.PENDING
    assert SyncLog.query.filter_by(connector_id=cfg.id).count() == 0

    counts = _settle(tenant_ctx, db_session, org_a)

    db_session.expire_all()
    assert counts["applied"] == 1, counts
    assert SyncLog.query.filter_by(connector_id=cfg.id, status="completed").count() == 1
    _decided_by_the_system(proposal_id)


def _fake_reads(monkeypatch, connector_framework):
    """A Jira connection that reads three records, so no network is needed."""

    class Reads:
        scope = None

        async def batch_sync(self, since=None):
            return {"status": "completed", "records_processed": 3, "records_created": 0, "records_updated": 0}

    monkeypatch.setattr(connector_framework, "build_connector", lambda cfg, org_id: Reads())


def test_a_declined_connector_change_discards_its_staged_credential(
    db_session, org_a, admin_a, client, login_as, tenant_ctx, monkeypatch
):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    login_as(client, admin_a)
    _save_servicenow(client, "Declined ServiceNow", credential="never-to-be-live")
    proposal = _proposals(org_a.id, "create")[0]
    proposal_id = proposal.id
    vault = OrgCredentialVault()
    with tenant_ctx(org_a.id):
        assert vault.retrieve(org_a.id, "servicenow", "pending:%d:credentials" % proposal_id) is not None
    Reviewer(monkeypatch, default=_fail(2, "The address is not one this workspace uses."))

    counts = _settle(tenant_ctx, db_session, org_a)

    db_session.expire_all()
    assert counts["declined"] == 1, counts
    row = _proposals(org_a.id, "create")[0]
    assert row.status == ApprovalStatus.REJECTED
    assert "The address is not one this workspace uses." in row.rejected_reason
    with tenant_ctx(org_a.id):
        assert vault.retrieve(org_a.id, "servicenow", "pending:%d:credentials" % proposal_id) is None
        assert vault.retrieve_credentials(org_a.id, "servicenow") is None
    assert ConnectorConfig.query.filter_by(organization_id=org_a.id, connector_type="servicenow").first() is None


def test_connector_settling_is_inside_the_organisation(db_session, org_a, org_b, admin_a, admin_b, client, login_as,
                                                         tenant_ctx):
    from app.models.ai_chat_crud_approval import ApprovalStatus
    from app.models.connector_config import ConnectorConfig

    login_as(client, admin_a)
    _save_servicenow(client, "A only ServiceNow")
    db_session.commit()

    counts_b = _settle(tenant_ctx, db_session, org_b)  # the other organisation's run

    db_session.expire_all()
    assert sum(counts_b.values()) == 0
    with tenant_ctx(org_a.id):
        assert _proposals(org_a.id, "create")[0].status == ApprovalStatus.PENDING
    with tenant_ctx(org_b.id):
        assert ConnectorConfig.query.filter_by(organization_id=org_b.id).count() == 0
    assert admin_b.organization_id == org_b.id
