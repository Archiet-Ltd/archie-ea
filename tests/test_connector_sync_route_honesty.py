"""POST /integrations/api/connectors/<id>/sync used to always 500.

The route built a fresh `ConnectorManager()` per request and called
`manager.get_connector(connector_id)` -- a method that does not exist on
`ConnectorManager` (only the module-level `get_connector_manager()` does),
and no connector is ever registered with the manager anyway, so the call
raised `AttributeError` before ever reaching a real connector. The broad
`except Exception` then reported that as a generic "An internal error
occurred" 500, which reads as a bug rather than as the missing feature it
actually is.

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
    def test_sync_on_existing_connector_returns_a_clear_non_500_error(
        self, db_session, org, admin, client, login_as
    ):
        """The route must not 500, and must say plainly that sync cannot run."""
        cfg = _make_connector_config(db_session, org.id, "jira", "Org Jira")
        db_session.commit()

        login_as(client, admin)
        resp = client.post(f"/integrations/api/connectors/{cfg.id}/sync")

        assert resp.status_code != 500, (
            f"sync must not 500 (the old AttributeError-via-broad-except bug), "
            f"got {resp.status_code}: {resp.get_data(as_text=True)}"
        )
        assert resp.status_code not in (200, 201, 202), (
            "a connector that cannot sync must not answer with a success status"
        )

        payload = resp.get_json()
        assert payload is not None
        assert payload.get("success") is False
        message = (payload.get("error") or {}).get("message", "")
        assert message, "error response must carry a human-readable message"
        assert "internal error" not in message.lower(), (
            "the message must explain that sync isn't available, not hide "
            "behind a generic internal-error string"
        )

    def test_sync_on_unknown_connector_still_reports_not_found(
        self, db_session, org, admin, client, login_as
    ):
        login_as(client, admin)
        resp = client.post("/integrations/api/connectors/does-not-exist/sync")

        assert resp.status_code == 404
        payload = resp.get_json()
        assert payload.get("success") is False
