"""Connector framework change path: approval queue, delegation or refusal.

Every connector change now reaches the model only as an approval proposal in
the acting organisation's own queue, unless that organisation has delegated
the action type (``Organization.settings["connector_action_delegation"]``);
a write with neither is refused. These tests prove it end to end with two
real organisations (no mocked organisation context) and drive the one
connectors page journey: add a connector with a credential, see it masked
and never retrievable, run a sync against a recorded fixture, and see
health, last synchronisation and the proposals in the approval inbox.

Every test here fails on main (writes applied directly, no queue, no page)
and passes on this branch.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "connector_sync_records.json")


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


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
        role = Role(
            name="Administrator",
            permissions=Permission.ADMINISTER,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _delegate(db_session, org_id, *actions):
    """Mark *actions* as delegated by *org_id* so they apply directly."""
    from app.models.organization import Organization

    org = Organization.query.get(org_id)
    settings = dict(org.settings or {})
    delegation = dict(settings.get("connector_action_delegation") or {})
    for action in actions:
        delegation[action] = True
    settings["connector_action_delegation"] = delegation
    org.settings = settings
    db_session.flush()


def _connector(db_session, org_id, connector_type, name, config=None):
    from app.models.connector_config import ConnectorConfig

    cfg = ConnectorConfig(
        organization_id=org_id,
        connector_type=connector_type,
        name=name,
        config=config or {"enabled": True},
        status="active",
    )
    db_session.add(cfg)
    db_session.flush()
    return cfg


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(
        name=name,
        type="ApplicationComponent",
        scope="enterprise",
        organization_id=org_id,
    )
    db_session.add(element)
    db_session.flush()
    return element


def _recorded_fixture_records():
    with open(_FIXTURE_PATH) as handle:
        data = json.load(handle)
    return data.get("records", data)


@pytest.fixture
def org_a(make_org):
    return make_org("flow-a")


@pytest.fixture
def org_b(make_org):
    return make_org("flow-b")


@pytest.fixture
def admin_a(db_session, org_a):
    return _make_admin(db_session, org_a.id, "FlowA")


@pytest.fixture
def admin_b(db_session, org_b):
    return _make_admin(db_session, org_b.id, "FlowB")


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# Acceptance 1 — every change is proposed, delegated or refused            #
# --------------------------------------------------------------------- #


class TestEveryConnectorChangeIsProposedOrDelegatedOrRefused:
    def test_change_is_queued_as_an_approval_proposal_and_not_written(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig, OrgConnectorCredential
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        secret = "plaintext-secret-value"
        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Production ServiceNow",
                "config_json": '{"instance_url": "https://org-a.service-now.com"}',
                "credential": secret,
                "enabled": "1",
            },
        )
        assert resp.status_code == 302

        # One pending proposal in the acting organisation's own queue.
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
            status=ApprovalStatus.PENDING,
        ).first()
        assert proposal is not None, (
            "The connector change must be queued as an approval proposal in "
            "the acting organisation's queue."
        )
        assert proposal.organization_id == org_a.id

        # The change has NOT reached the model: no config row yet.
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None, (
            "A connector change must not write the model before its proposal "
            "is approved."
        )

        # The credential is in the per-organisation vault, encrypted.
        vault = OrgCredentialVault()
        assert vault.retrieve_credentials(org_a.id, "servicenow") == {
            "credential": secret
        }
        row = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first()
        assert secret.encode() not in row.encrypted_value, (
            "The credential must be stored encrypted, never plaintext."
        )

    def test_delegated_organisation_applies_the_change_directly(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.models.connector_config import ConnectorConfig

        _delegate(db_session, org_a.id, "create")
        db_session.commit()

        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "jira",
                "name": "Direct Jira",
                "config_json": '{"instance_url": "https://direct.atlassian.net"}',
                "enabled": "1",
            },
        )
        assert resp.status_code == 302

        cfg = ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="jira"
        ).first()
        assert cfg is not None, (
            "An organisation that delegated the create action must see the "
            "change applied directly."
        )
        assert cfg.config["instance_url"] == "https://direct.atlassian.net"
        assert AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
        ).first() is None, (
            "A delegated change must not also queue an approval proposal."
        )

    def test_update_and_delete_are_proposed_too(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig

        cfg = _connector(db_session, org_a.id, "jira", "Jira Prime")
        db_session.commit()

        login_as(client, admin_a)
        client.post(
            "/admin/connectors",
            data={
                "connector_type": "jira",
                "connector_id": cfg.id,
                "name": "Jira Prime",
                "config_json": '{"instance_url": "https://prime.atlassian.net"}',
                "enabled": "1",
            },
        )
        update_proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="update",
            status=ApprovalStatus.PENDING,
        ).first()
        assert update_proposal is not None, "An update must queue a proposal."

        reloaded = ConnectorConfig.query.get(cfg.id)
        assert reloaded.config.get("instance_url") is None, (
            "The update must not be applied before approval."
        )

        client.post(f"/admin/connectors/{cfg.id}/delete")
        delete_proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="delete",
            status=ApprovalStatus.PENDING,
        ).first()
        assert delete_proposal is not None, "A delete must queue a proposal."
        assert ConnectorConfig.query.get(cfg.id) is not None, (
            "The delete must not run before approval."
        )

    def test_write_without_approval_or_delegation_is_refused(
        self, db_session, org_a, org_b, tenant_ctx
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval
        from app.services.connector_framework import (
            ConnectorWriteRefused,
            apply_connector_change,
        )

        payload = {
            "connector_type": "jira",
            "name": "Sneaky Jira",
            "config": {"instance_url": "https://sneaky.atlassian.net"},
        }
        with tenant_ctx(org_a.id):
            with pytest.raises(ConnectorWriteRefused):
                apply_connector_change(
                    org_id=org_a.id, action="create", payload=payload
                )
            # The refusal is top-level: an approval that belongs to another
            # organisation must not authorise a write into this one.
            foreign_proposal = AIChatCRUDApproval(
                organization_id=org_b.id,
                entity_type="connectors",
                operation_type="create",
                summary="other org",
                original_command="system: create connectors",
                expires_at=datetime.utcnow() + timedelta(minutes=15),
                operation_payload=json.dumps(payload),
            )
            db_session.add(foreign_proposal)
            db_session.flush()
            with pytest.raises(ConnectorWriteRefused):
                apply_connector_change(
                    org_id=org_a.id,
                    action="create",
                    payload=payload,
                    approval=foreign_proposal,
                )

    def test_approving_the_proposal_applies_the_write(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            AIChatApprovalService,
        )

        login_as(client, admin_a)
        client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Approved ServiceNow",
                "config_json": '{"instance_url": "https://approved.service-now.com"}',
                "enabled": "1",
            },
        )

        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
            status=ApprovalStatus.PENDING,
        ).first()
        assert proposal is not None

        approver = _make_admin(db_session, org_a.id, "FlowApprover")
        db_session.commit()
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(
            proposal.id
        )
        assert result.get("success") is True, result

        cfg = ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first()
        assert cfg is not None, (
            "Approving the proposal must apply the connector write."
        )
        assert cfg.name == "Approved ServiceNow"


# --------------------------------------------------------------------- #
# Acceptance 2 — two-organisation isolation                                #
# --------------------------------------------------------------------- #


class TestTwoOrganisationIsolation:
    def test_connectors_page_never_lists_another_orgs_connectors(
        self, db_session, org_a, org_b, admin_a, admin_b, client, login_as
    ):
        _connector(db_session, org_a.id, "jira", "A Jira")
        _connector(db_session, org_b.id, "jira", "B Jira")
        _connector(
            db_session,
            org_b.id,
            "servicenow",
            "B ServiceNow",
            config={"instance_url": "https://b-secret.service-now.com"},
        )
        db_session.commit()

        login_as(client, admin_a)
        resp = client.get("/admin/connectors")
        assert resp.status_code == 200
        assert b"A Jira" in resp.data
        assert b"B Jira" not in resp.data
        assert b"B ServiceNow" not in resp.data
        assert b"b-secret.service-now.com" not in resp.data

        login_as(client, admin_b)
        resp = client.get("/admin/connectors")
        assert resp.status_code == 200
        assert b"B Jira" in resp.data
        assert b"B ServiceNow" in resp.data
        assert b"A Jira" not in resp.data

    def test_sync_proposes_only_into_the_acting_orgs_queue(
        self, db_session, org_a, org_b, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        cfg = _connector(db_session, org_a.id, "jira", "A Jira")
        db_session.commit()

        login_as(client, admin_a)
        resp = client.post(f"/admin/connectors/{cfg.id}/sync")
        assert resp.status_code == 302

        proposals = AIChatCRUDApproval.query.filter_by(
            status=ApprovalStatus.PENDING,
            entity_type="connectors",
            operation_type="sync",
        ).all()
        assert [p.organization_id for p in proposals] == [org_a.id], (
            "A sync for organisation A must propose only into A's queue."
        )

    def test_credentials_cannot_be_decrypted_with_the_other_orgs_key(
        self, db_session, org_a, org_b, admin_a, client, login_as, tenant_ctx
    ):
        from app.modules.codegen.services.credential_encryption import _get_org_fernet
        from app.models.connector_config import OrgConnectorCredential

        secret_a = "org-a-secret"
        # Store the credential through the one connectors page: the vault is
        # written at proposal time, encrypted with the organisation's own key.
        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Keyed ServiceNow",
                "config_json": "{}",
                "credential": secret_a,
                "enabled": "1",
            },
        )
        assert resp.status_code == 302

        a_row = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first()
        assert a_row is not None

        a_key = _get_org_fernet(org_a.id)
        decrypted = json.loads(a_key.decrypt(a_row.encrypted_value))
        assert decrypted == {"credential": "org-a-secret"}

        # Organisation B must have its own key before the cross-key check:
        # giving B a credential of its own is what creates B's key row.
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        with tenant_ctx(org_b.id):
            OrgCredentialVault().store(
                org_b.id, "servicenow", "client_secret", "org-b-secret"
            )

        # Decrypting org A's row with org B's key must fail: every
        # organisation's credentials are wrapped in its own key.
        b_key = _get_org_fernet(org_b.id)
        with pytest.raises(Exception):
            b_key.decrypt(a_row.encrypted_value)


# --------------------------------------------------------------------- #
# Acceptance 4 — the connectors page journey                              #
# --------------------------------------------------------------------- #


class TestConnectorsPageJourney:
    def test_added_connector_shows_masked_credential_and_never_retrieves_it(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig

        secret = "ultra-secret-credential-42"
        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "servicenow",
                "name": "Masked ServiceNow",
                "config_json": '{"instance_url": "https://masked.service-now.com"}',
                "credential": secret,
                "enabled": "1",
            },
        )
        assert resp.status_code == 302

        # Reload the page: the credential renders masked and only masked.
        resp = client.get("/admin/connectors")
        assert resp.status_code == 200
        assert b"******" in resp.data, (
            "The credential must be shown masked on the connectors page."
        )
        assert secret.encode() not in resp.data, (
            "The plaintext credential must never appear on the page."
        )

        # The proposal is waiting in this organisation's queue, and the
        # change is not applied until it is approved.
        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
            status=ApprovalStatus.PENDING,
        ).first()
        assert proposal is not None
        assert proposal.summary == "Add connector 'Masked ServiceNow'"
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None

        # Approve, then reload again: still masked, still never the secret.
        approver = _make_admin(db_session, org_a.id, "MaskedApprover")
        db_session.commit()
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            AIChatApprovalService,
        )

        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(
            proposal.id
        )
        assert result.get("success") is True, result

        resp = client.get("/admin/connectors")
        assert b"******" in resp.data
        assert secret.encode() not in resp.data

    def test_sync_against_recorded_fixture_shows_health_last_sync_and_proposals(
        self, db_session, org_a, org_b, admin_a, admin_b, client, login_as, monkeypatch
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk
        from app.models.connector_config import SyncLog
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            AIChatApprovalService,
        )

        # Two real organisations: A syncs, B must never see or receive it.
        a_cfg = _connector(db_session, org_a.id, "jira", "Fixture Jira A")
        a_element = _element(db_session, org_a.id, "A's Ticket System")
        db_session.commit()

        # Syncing is not delegated: the sync must be proposed first.
        login_as(client, admin_a)
        resp = client.post(f"/admin/connectors/{a_cfg.id}/sync")
        assert resp.status_code == 302

        sync_proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="sync",
            status=ApprovalStatus.PENDING,
        ).first()
        assert sync_proposal is not None
        # Nobody in org B holds a copy of this proposal.
        assert AIChatCRUDApproval.query.filter_by(
            organization_id=org_b.id,
            entity_type="connectors",
            operation_type="sync",
            status=ApprovalStatus.PENDING,
        ).count() == 0

        # The two queues agree: the proposal is visible in the approval
        # inbox surfaces -- "pending" for the requester, "queue" for a
        # same-org reviewer.
        pending = client.get("/ai-chat/approvals/pending")
        assert pending.status_code == 200
        assert b"Run sync for connector 'Fixture Jira A'" in pending.data

        approver = _make_admin(db_session, org_a.id, "FixtureApprover")
        db_session.commit()
        login_as(client, approver)
        queue = client.get("/ai-chat/approvals/queue")
        assert queue.status_code == 200
        assert b"Run sync for connector 'Fixture Jira A'" in queue.data

        # Approve against a recorded fixture: the concrete connector's run
        # is replaced by a batch_sync that replays the recorded API records
        # and resolves their external ids to organisation A's elements.
        records = _recorded_fixture_records()
        external_ids = [record["id"] for record in records]

        async def _recorded_batch_sync(since=None):
            return {
                "status": "completed",
                "records_processed": len(records),
                "records_created": len(external_ids),
                "records_updated": 0,
                "records_deleted": 0,
                "elements_touched": [
                    {"external_id": external_id, "element_id": a_element.id}
                    for external_id in external_ids
                ],
            }

        from app.connectors.jira import JiraALMConnector

        monkeypatch.setattr(JiraALMConnector, "batch_sync", _recorded_batch_sync)

        login_as(client, admin_a)
        result = AIChatApprovalService(user_id=approver.id).approve_and_execute(
            sync_proposal.id
        )
        assert result.get("success") is True, result

        # Health + last synchronisation on the org A connector.
        db_session.expire_all()
        from app.models.connector_config import ConnectorConfig

        reloaded = ConnectorConfig.query.get(a_cfg.id)
        assert reloaded.status == "active"
        assert reloaded.last_sync is not None

        sync_log = (
            SyncLog.query.filter_by(connector_id=a_cfg.id)
            .order_by(SyncLog.started_at.desc())
            .first()
        )
        assert sync_log is not None and sync_log.status == "completed"

        # Identifier links written for every element the sync touched, for
        # org A only — org B's element must never be named.
        links = ExternalIdentityCrosswalk.query.filter_by(
            organization_id=org_a.id, source_system="jira"
        ).all()
        assert len(links) == len(external_ids)
        assert {link.external_id for link in links} == set(external_ids)
        assert {link.element_id for link in links} == {a_element.id}
        b_links = ExternalIdentityCrosswalk.query.filter_by(
            organization_id=org_b.id, source_system="jira"
        ).all()
        assert b_links == [], (
            "TENANT LEAK: organisation A's sync wrote crosswalk links into "
            "organisation B."
        )

        # The page now shows health and the last synchronisation.
        login_as(client, admin_a)
        page = client.get("/admin/connectors")
        assert page.status_code == 200
        assert b"Fixture Jira A" in page.data
        assert b"active" in page.data
        assert b"UTC" in page.data, "Last synchronisation must be shown."


# --------------------------------------------------------------------- #
# Legacy per-connector forms use the same change path                    #
# --------------------------------------------------------------------- #


class TestLegacyFormsQueueThroughTheSamePath:
    def test_m365_save_queues_instead_of_writing_directly(
        self, db_session, org_a, org_b, admin_a, client, login_as, tenant_ctx
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig

        _connector(db_session, org_b.id, "m365", "B's M365", {"tenant_id": "b-tenant"})
        db_session.commit()

        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors/m365",
            data={
                "tenant_id": "a-tenant",
                "client_id": uuid.uuid4().hex,
                "client_secret": uuid.uuid4().hex,
                "enabled": "1",
            },
        )
        assert resp.status_code in (200, 302)

        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
            status=ApprovalStatus.PENDING,
        ).first()
        assert proposal is not None, (
            "The legacy M365 save must queue an approval proposal in the "
            "acting organisation's queue."
        )

        with tenant_ctx(org_b.id):
            reloaded = ConnectorConfig.query.filter_by(
                organization_id=org_b.id, connector_type="m365"
            ).first()
            assert reloaded is not None
            assert reloaded.config.get("tenant_id") == "b-tenant", (
                "TENANT LEAK: org A's M365 save must not touch org B's row."
            )

    def test_jira_save_queues_instead_of_writing_directly(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import ConnectorConfig

        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://a.atlassian.net",
                "email": "a@example.com",
                "api_token": uuid.uuid4().hex,
                "enabled": "on",
            },
        )
        assert resp.status_code in (200, 302)
        assert AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            operation_type="create",
            status=ApprovalStatus.PENDING,
        ).first() is not None
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="jira"
        ).first() is None, (
            "The Jira save must not write the row before its proposal is "
            "approved."
        )

    def test_unpermitted_connector_type_is_refused_before_anything_is_stored(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors",
            data={
                "connector_type": "siem",
                "name": "Mystery SIEM",
                "config_json": "{}",
                "enabled": "1",
            },
        )
        assert resp.status_code == 302
        assert AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id, entity_type="connectors"
        ).count() == 0, (
            "An unpermitted connector type must be refused, not queued."
        )