"""Tenant scope for the shared connector configuration (M365, Jira and others
stored via app.models.connector_config.ConnectorConfig).

Follows tests/test_tenant_isolation.py's pattern: the shared db_session /
make_org / tenant_ctx / login_as fixtures from tests/conftest.py, never a
hand-rolled module-scoped app fixture. Route-level assertions use the
session-scoped app fixture's test client.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def _make_admin(db_session, org_id, label):
    """A real User row pinned to *org_id*, with the Administrator role.

    Role rows are seeded by Role.insert_roles() in normal deploys; a fresh
    test database may not have run it, so create-if-missing here, exactly
    like tests/test_r32_ai_permission_gate.py's _make_user.
    """
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


def _make_connector_config(db_session, org_id, connector_type, name, config=None):
    from app.models.connector_config import ConnectorConfig

    cfg = ConnectorConfig(
        organization_id=org_id,
        connector_type=connector_type,
        name=name,
        config=config or {"instance_url": "https://example.invalid"},
    )
    db_session.add(cfg)
    db_session.flush()
    return cfg


@pytest.fixture
def org_a(make_org):
    return make_org("connector-a")


@pytest.fixture
def org_b(make_org):
    return make_org("connector-b")


@pytest.fixture
def admin_a(db_session, org_a):
    return _make_admin(db_session, org_a.id, "AdminA")


@pytest.fixture
def admin_b(db_session, org_b):
    return _make_admin(db_session, org_b.id, "AdminB")


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# Model-level tenant scoping
# --------------------------------------------------------------------- #


class TestConnectorConfigModelIsTenantScoped:
    def test_select_is_scoped_to_current_org(self, db_session, org_a, org_b, tenant_ctx):
        """The core invariant: org A must not see org B's saved connector."""
        from app.models.connector_config import ConnectorConfig

        _make_connector_config(db_session, org_a.id, "m365", "A's M365")
        b_row = _make_connector_config(db_session, org_b.id, "m365", "B's M365")

        with tenant_ctx(org_a.id):
            visible = ConnectorConfig.query.filter_by(connector_type="m365").all()
            visible_ids = {row.id for row in visible}

        assert b_row.id not in visible_ids, (
            "TENANT LEAK: org A's query returned org B's connector configuration."
        )

    def test_filtered_select_cannot_reach_other_org_by_id(
        self, db_session, org_a, org_b, tenant_ctx
    ):
        from app.models.connector_config import ConnectorConfig

        b_row = _make_connector_config(db_session, org_b.id, "jira", "B's Jira")

        with tenant_ctx(org_a.id):
            found = ConnectorConfig.query.filter_by(id=b_row.id).first()

        assert found is None, (
            f"TENANT LEAK: org A retrieved org B's connector row (id={b_row.id}) by id."
        )

    def test_insert_inherits_current_org(self, db_session, org_a, tenant_ctx):
        from app.models.connector_config import ConnectorConfig

        with tenant_ctx(org_a.id):
            cfg = ConnectorConfig(
                connector_type="m365", name="New M365", config={"tenant_id": "x"}
            )
            db_session.add(cfg)
            db_session.flush()
            assert cfg.organization_id == org_a.id

    def test_row_with_no_organization_is_visible_to_nobody(
        self, db_session, org_a, org_b, tenant_ctx
    ):
        """A row backfilled to NULL (origin undeterminable) is hidden from
        every organisation, not shared with all of them.

        org_b is requested (and otherwise unused) solely to keep the
        database shaped like a genuine multi-tenant install while the
        orphan row below is flushed: ConnectorConfig.organization_id's
        column default (_default_org_id in app/models/mixins/core.py)
        deliberately falls back to the one existing Organization when
        exactly one is present, as a convenience for a fresh self-hosted
        single-tenant install -- and that default fires for an explicit
        organization_id=None just as it would for an unset one, since
        SQLAlchemy cannot tell the two apart at flush time. With only
        org_a in the database this test's own setup would accidentally
        look single-tenant and silently get the orphan row assigned to
        org_a, masking the very no-provenance case under test. A second
        organisation makes the fallback correctly decline to guess,
        matching the multi-tenant scenario this assertion describes.
        """
        from app.models.connector_config import ConnectorConfig

        orphan = ConnectorConfig(
            connector_type="m365", name="Undated M365", config={}, organization_id=None
        )
        db_session.add(orphan)
        db_session.flush()

        with tenant_ctx(org_a.id):
            found = ConnectorConfig.query.filter_by(id=orphan.id).first()

        assert found is None, (
            "A connector config with no recorded organisation must not be visible "
            "to any organisation."
        )


# --------------------------------------------------------------------- #
# Route-level tenant scoping (M365 and Jira admin routes)
# --------------------------------------------------------------------- #


class TestConnectorAdminRoutesAreTenantScoped:
    def test_m365_get_does_not_return_another_orgs_config(
        self, db_session, org_a, org_b, admin_a, client, login_as
    ):
        _make_connector_config(
            db_session,
            org_b.id,
            "m365",
            "B's M365",
            config={"tenant_id": "b-secret-tenant-id"},
        )
        db_session.commit()

        login_as(client, admin_a)
        resp = client.get("/admin/connectors/m365")

        assert resp.status_code == 200
        assert b"b-secret-tenant-id" not in resp.data, (
            "TENANT LEAK: org A's admin saw org B's M365 tenant id on the config form."
        )

    def test_m365_save_does_not_overwrite_another_orgs_config(
        self, db_session, org_a, org_b, admin_a, client, login_as, tenant_ctx
    ):
        from app.models.connector_config import ConnectorConfig

        b_cfg = _make_connector_config(
            db_session,
            org_b.id,
            "m365",
            "B's M365",
            config={"tenant_id": "b-original-tenant-id"},
        )
        db_session.commit()
        b_cfg_id = b_cfg.id

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/m365",
            data={
                "tenant_id": "a-attempted-overwrite",
                "client_id": uuid.uuid4().hex,
                "client_secret": uuid.uuid4().hex,
                "enabled": "1",
            },
        )

        # Reload from org B's own perspective — g.current_org_id from the
        # save request above must not leak into this check either way.
        with tenant_ctx(org_b.id):
            reloaded = ConnectorConfig.query.get(b_cfg_id)
            assert reloaded is not None
            assert reloaded.config.get("tenant_id") == "b-original-tenant-id", (
                "TENANT LEAK: org A's save overwrote org B's M365 configuration."
            )

    def test_jira_get_does_not_return_another_orgs_config(
        self, db_session, org_a, org_b, admin_a, client, login_as
    ):
        _make_connector_config(
            db_session,
            org_b.id,
            "jira",
            "B's Jira",
            config={"instance_url": "https://b-secret.atlassian.net"},
        )
        db_session.commit()

        login_as(client, admin_a)
        resp = client.get("/admin/connectors/jira")

        assert resp.status_code == 200
        assert b"b-secret.atlassian.net" not in resp.data, (
            "TENANT LEAK: org A's admin saw org B's Jira instance URL on the config form."
        )

    def test_jira_save_does_not_overwrite_another_orgs_config(
        self, db_session, org_a, org_b, admin_a, client, login_as, tenant_ctx
    ):
        from app.models.connector_config import ConnectorConfig

        b_cfg = _make_connector_config(
            db_session,
            org_b.id,
            "jira",
            "B's Jira",
            config={"instance_url": "https://b-original.atlassian.net", "email": "b@example.com"},
        )
        db_session.commit()
        b_cfg_id = b_cfg.id

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/jira",
            data={
                "instance_url": "https://a-attempted-overwrite.atlassian.net",
                "email": "a@example.com",
                "api_token": uuid.uuid4().hex,
                "enabled": "on",
            },
        )

        with tenant_ctx(org_b.id):
            reloaded = ConnectorConfig.query.get(b_cfg_id)
            assert reloaded is not None
            assert reloaded.config.get("instance_url") == "https://b-original.atlassian.net", (
                "TENANT LEAK: org A's save overwrote org B's Jira configuration."
            )


# --------------------------------------------------------------------- #
# ServiceNow admin route (org-scoped OrgConnectorConfig + OrgCredentialVault)
# --------------------------------------------------------------------- #


class TestServiceNowConnectorRouteStoresSecretInVault:
    """The ServiceNow connector admin route (``/admin/connectors/servicenow``)
    must save the client secret through ``OrgCredentialVault`` instead of the
    retired setter — encrypted per organisation, never shared or readable
    across organisations — and route the configuration change through the
    organisation's approval queue: the row is written when the proposal is
    approved, not by the save itself. The retiring ``OrgConnectorConfig``
    forever store is no longer written."""

    def test_save_queues_a_proposal_and_approval_applies_the_config(
        self, db_session, org_a, admin_a, client, login_as
    ):
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus
        from app.models.connector_config import (
            ConnectorConfig,
            OrgConnectorConfig,
            OrgConnectorCredential,
        )
        from app.modules.ai_chat.services.ai_chat_approval_service import (
            AIChatApprovalService,
        )
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        secret = uuid.uuid4().hex
        login_as(client, admin_a)
        resp = client.post(
            "/admin/connectors/servicenow",
            data={
                "instance_url": "https://org-a.service-now.com",
                "client_id": "org-a-client-id",
                "client_secret": secret,
                "ci_query_filter": "",
                "field_mapping": "{}",
                "enabled": "on",
            },
        )

        # Before this change, the setter's RuntimeError surfaced as a 500.
        assert resp.status_code in (200, 302), (
            f"ServiceNow save must not fail now that the setter is retired "
            f"(got {resp.status_code})."
        )

        # The change is queued, not written: no config row exists yet.
        queued = AIChatCRUDApproval.query.filter_by(
            organization_id=org_a.id,
            entity_type="connectors",
            status=ApprovalStatus.PENDING,
        ).first()
        assert queued is not None, (
            "The connector change must be queued as an approval proposal in "
            "the organisation's own queue."
        )
        assert ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None, (
            "A connector change must not reach the model before its proposal "
            "is approved."
        )
        # The retired forever store is no longer written at all.
        assert OrgConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first() is None

        # The secret is already in the vault, encrypted, at proposal time.
        row = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id,
            connector_type="servicenow",
            credential_type="client_secret",
        ).first()
        assert row is not None, "The secret must be stored via OrgConnectorCredential."
        assert secret.encode() not in row.encrypted_value, (
            "The secret must be stored encrypted, not as plaintext."
        )
        assert OrgCredentialVault().retrieve(org_a.id, "servicenow", "client_secret") == secret

        # Approving the proposal applies the configuration to the framework row.
        admin_a2 = _make_admin(db_session, org_a.id, "AdminA2")
        db_session.commit()
        result = AIChatApprovalService(user_id=admin_a2.id).approve_and_execute(queued.id)
        assert result.get("success") is True, result

        cfg = ConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).first()
        assert cfg is not None
        assert cfg.config.get("instance_url") == "https://org-a.service-now.com"
        assert cfg.config.get("client_id") == "org-a-client-id"
        assert cfg.config.get("enabled") is True
        assert "client_secret" not in cfg.config, (
            "Credentials never live on the configuration row."
        )

    def test_secret_is_isolated_between_organisations(
        self, db_session, org_a, org_b, admin_a, admin_b, client, login_as, tenant_ctx
    ):
        from app.modules.codegen.services.credential_vault import OrgCredentialVault

        secret_a = uuid.uuid4().hex
        secret_b = uuid.uuid4().hex

        login_as(client, admin_a)
        client.post(
            "/admin/connectors/servicenow",
            data={
                "instance_url": "https://org-a.service-now.com",
                "client_id": "org-a-client-id",
                "client_secret": secret_a,
                "enabled": "on",
            },
        )

        login_as(client, admin_b)
        client.post(
            "/admin/connectors/servicenow",
            data={
                "instance_url": "https://org-b.service-now.com",
                "client_id": "org-b-client-id",
                "client_secret": secret_b,
                "enabled": "on",
            },
        )

        # The test client's last request left g.current_org_id set to org B
        # (the app context is held open for the whole test, per login_as's
        # docstring) — read each org's secret back inside its own tenant
        # context, exactly as a real request for that org would.
        vault = OrgCredentialVault()
        with tenant_ctx(org_a.id):
            assert vault.retrieve(org_a.id, "servicenow", "client_secret") == secret_a
        with tenant_ctx(org_b.id):
            assert vault.retrieve(org_b.id, "servicenow", "client_secret") == secret_b, (
                "TENANT LEAK: org B's saved secret was not the one org B submitted — "
                "org A's save may have overwritten it."
            )
