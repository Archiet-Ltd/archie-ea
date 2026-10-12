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


class TestM2ServiceNowFormAndServiceReadTheSameStore:
    def test_an_approved_save_is_visible_to_the_servicenow_service(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.services.servicenow_connector_service import ServiceNowConnectorService

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/servicenow",
            data={
                "instance_url": "https://probe.service-now.com",
                "client_id": "probe-client-id",
                "field_mapping": "{}",
                "enabled": "on",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        approver = _make_admin(db_session, org_a.id, "ServiceNowApprover")
        db_session.commit()
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is True, result

        seen = ServiceNowConnectorService._get_config(org_a.id, "servicenow")
        assert seen is not None, "the service must see what the approved save wrote"
        assert seen.instance_url == "https://probe.service-now.com"
        assert seen.client_id == "probe-client-id"
        assert seen.enabled is True

    def test_backfill_copies_a_legacy_row_into_connectorconfig_once(self, db_session, org_a):
        from app.commands.backfill_servicenow_connector_config import backfill_servicenow_config
        from app.models.connector_config import ConnectorConfig, OrgConnectorConfig
        from app.services.servicenow_connector_service import ServiceNowConnectorService

        legacy = OrgConnectorConfig(
            organization_id=org_a.id,
            connector_type="servicenow",
            instance_url="https://legacy.service-now.com",
            client_id="legacy-client-id",
            field_mapping={"ci_query_filter": "active=true"},
            enabled=True,
        )
        db_session.add(legacy)
        db_session.commit()

        stats = backfill_servicenow_config()
        assert stats["connector_config_rows_created"] == 1

        seen = ServiceNowConnectorService._get_config(org_a.id, "servicenow")
        assert seen.instance_url == "https://legacy.service-now.com"
        assert seen.client_id == "legacy-client-id"

        # Idempotent: a second run creates nothing further, and never
        # touches a ConnectorConfig row a real save already wrote.
        stats_again = backfill_servicenow_config()
        assert stats_again["connector_config_rows_created"] == 0
        assert stats_again["already_had_a_connector_config_row"] == 1
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).count() == 1


class TestM4BuiltConnectorCanActuallyAuthenticate:
    def test_a_jira_connector_built_from_a_form_save_has_every_required_field(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.connector_framework import build_connector

        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://probe.atlassian.net",
                "email": "probe@example.com",
                "api_token": "probe-jira-token",
                "default_project_key": "ARCH",
                "enabled": "on",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        approver = _make_admin(db_session, org_a.id, "JiraM4Approver")
        db_session.commit()
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is True, result

        from app.models.connector_config import ConnectorConfig

        cfg = ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="jira"
        ).first()
        assert cfg is not None

        connector = build_connector(cfg, org_a.id)
        required = connector.get_required_config_fields()
        for field in required:
            assert connector.config.config.get(field), f"missing required field: {field}"
        assert connector.config.config["username"] == "probe@example.com"
        assert connector.config.config["api_token"] == "probe-jira-token"
        assert connector.config.config["project_keys"] == ["ARCH"]

        # Vault retrieval, not the deprecated "credential" blob key.
        assert OrgCredentialVault().retrieve_credentials(org_a.id, "jira") in (None, {})


class TestM1CredentialDoesNotTakeEffectBeforeApproval:
    def test_a_submitted_secret_is_not_live_while_the_proposal_is_pending(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://probe.atlassian.net",
                "email": "probe@example.com",
                "api_token": "not-yet-approved-token",
                "default_project_key": "ARCH",
                "enabled": "on",
            },
        )

        live = OrgCredentialVault().retrieve(org_a.id, "jira", "api_token")
        assert live is None, (
            "an unapproved proposal must not make its secret live"
        )

    def test_approving_promotes_the_staged_secret_to_live(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://probe.atlassian.net",
                "email": "probe@example.com",
                "api_token": "now-approved-token",
                "default_project_key": "ARCH",
                "enabled": "on",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        approver = _make_admin(db_session, org_a.id, "M1Approver")
        db_session.commit()
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is True, result

        vault = OrgCredentialVault()
        assert vault.retrieve(org_a.id, "jira", "api_token") == "now-approved-token"
        # The staged row is gone, not left behind as an orphan.
        assert vault.retrieve(org_a.id, "jira", f"pending:{proposal.id}:api_token") is None

    def test_rejecting_discards_the_staged_secret_without_ever_making_it_live(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://probe.atlassian.net",
                "email": "probe@example.com",
                "api_token": "never-approved-token",
                "default_project_key": "ARCH",
                "enabled": "on",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        rejecter = _make_admin(db_session, org_a.id, "M1Rejecter")
        db_session.commit()
        result = AIChatApprovalService(user_id=rejecter.id).reject_approval(proposal.id)
        assert result.get("success") is True, result

        vault = OrgCredentialVault()
        assert vault.retrieve(org_a.id, "jira", "api_token") is None, (
            "a rejected proposal's secret must never become live"
        )
        assert vault.retrieve(org_a.id, "jira", f"pending:{proposal.id}:api_token") is None, (
            "the staged row must be discarded, not left as an orphan secret"
        )


class TestN1ServicesActuallyReadTheVault:
    """cloud-review-pr448-505c3c2e-v1.md's N1: the save paths store Jira's
    api_token and M365's client_secret only in the vault, but the services
    that use them still read the config row -- a new setup authenticated
    with nothing, and a rotation kept using the old value."""

    def test_jira_headers_use_the_vault_token_for_a_new_setup(self, db_session, org_a):
        from app.models.connector_config import ConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.jira_connector_service import JiraConnectorService

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira",
            config={"email": "probe@example.com"},  # no api_token key at all
        )
        db_session.add(cfg)
        db_session.flush()
        OrgCredentialVault().store(
            org_id=org_a.id, connector_type="jira", credential_type="api_token", value="vault-token"
        )

        headers = JiraConnectorService()._get_headers(cfg)

        import base64
        expected = base64.b64encode(b"probe@example.com:vault-token").decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_jira_headers_use_the_rotated_vault_token_not_the_stale_row_value(self, db_session, org_a):
        from app.models.connector_config import ConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.jira_connector_service import JiraConnectorService

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira Rotate",
            config={"email": "probe@example.com", "api_token": "old-legacy-token"},
        )
        db_session.add(cfg)
        db_session.flush()
        OrgCredentialVault().store(
            org_id=org_a.id, connector_type="jira", credential_type="api_token", value="rotated-token"
        )

        headers = JiraConnectorService()._get_headers(cfg)

        import base64
        expected = base64.b64encode(b"probe@example.com:rotated-token").decode()
        assert headers["Authorization"] == f"Basic {expected}", (
            "a rotation must use the new vault token, not the stale row value"
        )

    def test_m365_token_fetch_uses_the_vault_secret_for_a_new_setup(self, db_session, org_a, monkeypatch):
        from app.models.connector_config import ConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.m365_service import M365Service

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="m365",
            name="Probe M365",
            config={"tenant_id": "tenant-1", "client_id": "client-1"},  # no client_secret key
        )
        db_session.add(cfg)
        db_session.flush()
        OrgCredentialVault().store(
            org_id=org_a.id, connector_type="m365", credential_type="client_secret", value="vault-secret"
        )

        captured = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"access_token": "token-xyz", "expires_in": 3600}

        def _fake_post(url, data=None, timeout=None):
            captured.update(data or {})
            return _Resp()

        monkeypatch.setattr("app.services.m365_service.requests.post", _fake_post)

        token = M365Service()._get_token(cfg)

        assert token == "token-xyz"
        assert captured["client_secret"] == "vault-secret"


def _delegate(db_session, org_id, *actions):
    from app.models.organization import Organization

    org = Organization.query.get(org_id)
    settings = dict(org.settings or {})
    delegation = dict(settings.get("connector_action_delegation") or {})
    for action in actions:
        delegation[action] = True
    settings["connector_action_delegation"] = delegation
    org.settings = settings
    db_session.flush()


class TestN3DelegatedSyncOnTheOldEndpointDoesNotCrash:
    def test_a_delegated_sync_runs_instead_of_raising_unsupported_action(
        self, db_session, org_a, admin_a, client, login_as, monkeypatch
    ):
        from app.models.connector_config import ConnectorConfig
        from app.services.connector_framework import CONNECTOR_ACTION_SYNC

        _delegate(db_session, org_a.id, CONNECTOR_ACTION_SYNC)
        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira",
            config={"instance_url": "https://probe.atlassian.net"},
        )
        db_session.add(cfg)
        db_session.commit()

        from app.connectors.jira import JiraALMConnector

        monkeypatch.setattr(
            JiraALMConnector, "batch_sync", lambda self, **kw: {"records_synced": 0}
        )

        login_as(client, admin_a)
        resp = client.post(f"/integrations/api/connectors/{cfg.id}/sync")

        assert resp.status_code != 500, (
            f"a delegated sync must run, not raise 'Unsupported connector action': "
            f"got {resp.status_code}: {resp.get_data(as_text=True)}"
        )
        payload = resp.get_json()
        assert payload.get("queued") is False


class TestN4CredentialPromotedOnlyAfterTheWriteSucceeds:
    def test_a_failed_update_leaves_the_credential_staged_not_live(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig
        from app.modules.ai_chat.services.ai_chat_approval_service import AIChatApprovalService
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        cfg = ConnectorConfig(
            organization_id=org_a.id,
            connector_type="jira",
            name="Probe Jira",
            config={"instance_url": "https://probe.atlassian.net", "email": "probe@example.com"},
        )
        db_session.add(cfg)
        db_session.commit()
        cfg_id = cfg.id

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://probe.atlassian.net",
                "email": "probe@example.com",
                "api_token": "doomed-rotation-token",
                "default_project_key": "ARCH",
                "enabled": "on",
            },
        )
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors", status=ApprovalStatus.PENDING
        ).first()
        assert proposal is not None

        # The connector is deleted between proposal and approval -- the
        # approved update's write must fail (LookupError inside
        # apply_connector_change), and the staged secret must never have
        # been promoted ahead of that failure.
        ConnectorConfig.query.filter_by(id=cfg_id).delete()
        db_session.commit()

        approver = _make_admin(db_session, org_a.id, "N4Approver")
        db_session.commit()
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(proposal.id)
        assert result.get("success") is False, (
            "the write for a deleted connector must fail, not silently succeed"
        )

        vault = OrgCredentialVault()
        assert vault.retrieve(org_a.id, "jira", "api_token") is None, (
            "a credential must not go live when the write it belongs to failed"
        )
