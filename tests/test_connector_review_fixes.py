"""PR 448 independent review fixes (cloud-review-pr448-961a0c35-v1.md): H1, H2, H3.

H1: a secret typed into the configuration box was stored in plain text and
served to every approver. H2: approving an update replaced the whole config
dict and so could erase a sensitive key the new payload simply did not
resend. H3: approving a connector change needed only Permission.GENERAL,
not administrator rights, although every connector route itself requires
admin_required.

Each test here reproduces the review's own probe before asserting the fix,
using the same two-organisation-admin fixtures as test_connector_approval_flow.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_admin(db_session, org_id, label):
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="platform_administrator",
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER, index="main", default=False)
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _make_general_user(db_session, org_id, label):
    """A user with write permission (GENERAL) but no admin rights."""
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="solution_architect",
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="General").first()
    if role is None:
        role = Role(name="General", permissions=Permission.GENERAL, index="main", default=False)
        db_session.add(role)
        db_session.flush()
    user.role = role
    user.is_org_admin = False
    db_session.flush()
    return user


@pytest.fixture
def org_a(make_org):
    return make_org("review-a")


@pytest.fixture
def admin_a(db_session, org_a):
    return _make_admin(db_session, org_a.id, "ReviewAdmin")


@pytest.fixture
def client(app):
    return app.test_client()


class TestH1SecretsRejectedFromConfigBox:
    def test_a_sensitive_key_in_config_json_is_refused_and_stores_nothing(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.models.connector_config import ConnectorConfig

        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Probe ServiceNow",
                "config_json": '{"client_secret": "PROBE-PLAINTEXT-1"}',
                "enabled": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"cannot hold a secret" in resp.data

        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None
        assert AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors"
        ).first() is None

    def test_a_pending_proposals_config_is_masked_even_if_it_somehow_held_a_secret(
        self, db_session, org_a, admin_a
    ):
        """Defence in depth for data written before this fix: the proposal
        view must never surface a sensitive key in plain text, regardless of
        how it got into the payload."""
        import json as _json

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.services.connector_framework import _proposal_connector_view

        approval = AIChatCRUDApproval(
            organization_id=org_a.id,
            user_id=admin_a.id,
            entity_type="connectors",
            entity_id=None,
            operation_type="create",
            original_command="system: create connectors",
            operation_payload=_json.dumps(
                {
                    "connector_type": "servicenow",
                    "name": "Legacy Probe",
                    "config": {"client_secret": "LEGACY-PLAINTEXT"},
                }
            ),
            status=ApprovalStatus.PENDING,
            summary="Add connector 'Legacy Probe'",
            expires_at=datetime.utcnow() + timedelta(days=1),
        )
        db_session.add(approval)
        db_session.flush()

        view = _proposal_connector_view(approval, org_a.id)
        assert view["config"].get("client_secret") != "LEGACY-PLAINTEXT"


class TestH2UpdateNeverErasesAnExistingSensitiveKey:
    def test_approved_update_with_no_config_key_keeps_the_existing_secret(
        self, db_session, org_a, admin_a
    ):
        from app.models.connector_config import ConnectorConfig
        from app.services.connector_framework import CONNECTOR_ACTION_UPDATE, apply_connector_change

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira",
            config={"email": "old@example.com", "api_token_encrypted": "ENCRYPTED-TOKEN"},
            status="active",
        )
        db_session.add(cfg)
        db_session.flush()

        apply_connector_change(
            org_id=org_a.id,
            action=CONNECTOR_ACTION_UPDATE,
            delegated=True,
            payload={
                "connector_id": str(cfg.id),
                "connector_type": "jira",
                "config": {"email": "new@example.com"},
            },
        )
        db_session.flush()
        db_session.refresh(cfg)

        assert cfg.config["email"] == "new@example.com"
        assert cfg.config["api_token_encrypted"] == "ENCRYPTED-TOKEN", (
            "an update that does not resend the token must not erase it"
        )

    def test_approved_update_that_does_resend_the_key_still_replaces_it(
        self, db_session, org_a, admin_a
    ):
        """The carry-forward only fills a gap; an explicit new value in the
        payload must still win, or a genuine credential rotation would be
        silently ignored."""
        from app.models.connector_config import ConnectorConfig
        from app.services.connector_framework import CONNECTOR_ACTION_UPDATE, apply_connector_change

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira Rotate",
            config={"api_token_encrypted": "OLD-TOKEN"},
            status="active",
        )
        db_session.add(cfg)
        db_session.flush()

        apply_connector_change(
            org_id=org_a.id,
            action=CONNECTOR_ACTION_UPDATE,
            delegated=True,
            payload={
                "connector_id": str(cfg.id),
                "connector_type": "jira",
                "config": {"api_token_encrypted": "NEW-TOKEN"},
            },
        )
        db_session.flush()
        db_session.refresh(cfg)

        assert cfg.config["api_token_encrypted"] == "NEW-TOKEN"


class TestH3OnlyAnAdministratorMayApproveAConnectorChange:
    def test_a_general_permission_user_cannot_approve_a_connector_change(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        login_as(client, admin_a)
        client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Admin-Only Probe",
                "config_json": '{"instance_url": "https://probe.service-now.com"}',
                "enabled": "1",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        approver = _make_general_user(db_session, org_a.id, "GeneralApprover")
        db_session.commit()

        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is False
        assert result.get("code") == "FORBIDDEN"

        proposal_after = AIChatCRUDApproval.query.get(proposal.id)
        assert proposal_after.status == ApprovalStatus.PENDING
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None

    def test_an_administrator_can_still_approve_a_connector_change(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        login_as(client, admin_a)
        client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Admin Approve Probe",
                "config_json": '{"instance_url": "https://probe2.service-now.com"}',
                "enabled": "1",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        approver = _make_admin(db_session, org_a.id, "RealApprover")
        db_session.commit()

        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is True, result
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is not None
