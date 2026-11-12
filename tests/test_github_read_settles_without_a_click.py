"""A read of GitHub ends with every finding applied or linked, with no one pressing approve.

The read runs as the job the connect page starts; the reviewer is a stub that passes every finding. Two real
organisations connect the same recorded GitHub organisation; each is settled inside its own workspace.
"""

from __future__ import annotations

import pytest

from tests.test_github_builds_the_model import (  # noqa: F401
    TOKEN,
    _entity_history_trigger,
    _install_guards,
    acme,
    connect,
    github,
    org_a,
    org_b,
    people,
)
from tests.test_proposals_settle_without_a_person import Reviewer

pytestmark = pytest.mark.usefixtures("db_session")


def _run_the_read_job(db_session, org, connector_id):
    """The worker picks up the job the connect page queued and runs it."""
    from app.services.job_queue_service import get_job_queue_service

    service = get_job_queue_service()
    job = service.create_job("GitHub: read repositories", "github_first_sync",
                             {"connector_id": connector_id}, organization_id=org.id)
    payload = dict(job.payload)
    return service._execute_task("github_first_sync", payload, job.id)


def test_a_github_read_leaves_every_finding_applied_or_linked_with_no_one_approving(
    db_session, org_a, org_b, people, acme, connect, monkeypatch, tenant_ctx
):
    from app.models import ArchiMateElement
    from app.models.ai_chat_crud_approval import AIChatApprovalAuditLog, AIChatCRUDApproval, ApprovalStatus

    cfg_a = connect(org_a, token=TOKEN)
    connect(org_b, token=TOKEN)
    reviewer = Reviewer(monkeypatch)
    db_session.commit()
    org_a_id, org_b_id, cfg_a_id = org_a.id, org_b.id, cfg_a.id

    outcome = _run_the_read_job(db_session, org_a, cfg_a_id)

    db_session.expire_all()
    with tenant_ctx(org_a_id):
        proposals = AIChatCRUDApproval.query.filter(
            AIChatCRUDApproval.organization_id == org_a_id, AIChatCRUDApproval.source_table == "github_connector"
        ).all()
        assert len(proposals) == 23 and outcome["proposals"] == 23
        # Every finding is applied or linked; none is waiting, declined or left for a person.
        assert {p.status for p in proposals} == {ApprovalStatus.APPROVED}, outcome
        assert outcome["settled"]["in_review"] == 0 and outcome["settled"]["declined"] == 0, outcome
        assert outcome["settled"]["applied"] + outcome["settled"]["linked"] == 23
        assert reviewer.prompts, "the reviewer was asked about the findings"
        # No person approved any of them: the server's own review did, as the workspace's administrator account.
        assert all(p.approved_by_id is None and p.user_id is None for p in proposals)
        audit = AIChatApprovalAuditLog.query.filter(
            AIChatApprovalAuditLog.approval_id.in_([p.id for p in proposals]), AIChatApprovalAuditLog.event == "approved").all()
        assert len(audit) == 23 and all(a.actor_type == "system" and a.actor_user_id is None for a in audit)
        assert ArchiMateElement.query.filter_by(organization_id=org_a_id, type="Application").count() == 8
        # The counts the job reports agree with the records.
        by_outcome = [p.review_reasons.get("outcome") for p in proposals]
        assert outcome["settled"]["applied"] == by_outcome.count("applied")
        assert outcome["settled"]["linked"] == by_outcome.count("linked")

    with tenant_ctx(org_b_id):
        assert AIChatCRUDApproval.query.filter_by(organization_id=org_b_id).count() == 0
        assert ArchiMateElement.query.filter_by(organization_id=org_b_id).count() == 0
