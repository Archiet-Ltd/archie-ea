"""POST /integrations/api/connectors/<id>/sync used to always 500, then
always answer "sync is not available" after an interim honesty fix.

Both are now stale: PR 448 (M3, cloud-review-pr448-961a0c35-v1.md) wired
jira/servicenow/abacus into the one connector framework, so a sync genuinely
runs (queued for approval, or applied when delegated). This route now
forwards onto the same propose_or_apply_connector_change path the newer
/admin/connectors/<id>/sync uses, so the two URLs agree instead of this one
still claiming nothing can sync.

Follows tests/test_connector_config_tenant_scope.py's pattern: the shared
db_session / make_org / login_as fixtures from tests/conftest.py, never a
hand-rolled module-scoped app fixture.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_admin(db_session, org_id, label):
    """A real User row pinned to *org_id*, with the Administrator role.

    Role rows are seeded by Role.insert_roles() in normal deploys; a fresh
    test database may not have run it, so create-if-missing here, exactly
    like tests/test_connector_config_tenant_scope.py's _make_admin.
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


def _make_connector_config(db_session, org_id, connector_type, name):
    from app.models.connector_config import ConnectorConfig

    cfg = ConnectorConfig(
        organization_id=org_id,
        connector_type=connector_type,
        name=name,
        config={"instance_url": "https://example.invalid"},
    )
    db_session.add(cfg)
    db_session.flush()
    return cfg


@pytest.fixture
def org(make_org):
    return make_org("connector-sync-honesty")


@pytest.fixture
def admin(db_session, org):
    return _make_admin(db_session, org.id, "Admin")


@pytest.fixture
def client(app):
    return app.test_client()


class TestConnectorSyncRouteIsHonest:
    def test_sync_on_existing_connector_now_actually_runs(
        self, db_session, org, admin, client, login_as
    ):
        """The route must not 500, and a sync is genuinely queued (or run),
        not answered with the old "nothing can sync" refusal."""
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval, ApprovalStatus

        cfg = _make_connector_config(db_session, org.id, "jira", "Org Jira")
        db_session.commit()

        login_as(client, admin)
        resp = client.post(f"/integrations/api/connectors/{cfg.id}/sync")

        assert resp.status_code != 500, (
            f"sync must not 500 (the old AttributeError-via-broad-except bug), "
            f"got {resp.status_code}: {resp.get_data(as_text=True)}"
        )
        payload = resp.get_json()
        assert payload is not None
        assert payload.get("queued") is True, (
            f"a jira connector is a real registered connector type, so a sync "
            f"must queue for approval, not be refused: {payload}"
        )

        proposal = AIChatCRUDApproval.query.filter_by(
            organization_id=org.id,
            entity_type="connectors",
            operation_type="sync",
            status=ApprovalStatus.PENDING,
        ).first()
        assert proposal is not None, "the sync must actually reach the approval queue"

    def test_sync_on_unknown_connector_still_reports_not_found(
        self, db_session, org, admin, client, login_as
    ):
        login_as(client, admin)
        resp = client.post("/integrations/api/connectors/does-not-exist/sync")

        assert resp.status_code == 404
        payload = resp.get_json()
        assert payload.get("success") is False
