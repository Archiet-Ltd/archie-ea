"""ConnectorConfig.connector_type and .status are plain strings, not Enum
members, so api_list_connectors() and api_get_connector() calling .value on
either raised AttributeError on every real connector row. The same two
routes also read conn.sync_mode, which isn't an attribute on the model at
all -- only sync_schedule (cron expressions for batch sync) and
webhook_config (webhook endpoints/secrets) exist, and either, both, or
neither may be set. api_get_connector() separately read conn.config_data,
which also doesn't exist on the model -- the column is just config.

Fixed by dropping .value from connector_type/status (they are already the
right plain strings), reading conn.config instead of the nonexistent
conn.config_data, and adding ConnectorConfig.derived_sync_mode(), which
both routes and the dashboard card now read instead of the nonexistent
sync_mode attribute, so the API and the card always agree:

  sync_schedule set, webhook_config not set -> "scheduled"
  webhook_config set, sync_schedule not set -> "event"
  neither set                               -> "manual"
  both set                                  -> "scheduled, event"

Follows tests/test_connector_config_tenant_scope.py's fixture pattern (the
shared db_session / make_org / tenant_ctx / login_as fixtures from
tests/conftest.py).
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

    Mirrors tests/test_connector_config_tenant_scope.py::_make_admin.
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


def _make_connector_config(db_session, org_id, **overrides):
    from app.models.connector_config import ConnectorConfig

    fields = {
        "organization_id": org_id,
        "connector_type": "servicenow",
        "name": "Test Connector",
        "config": {"instance_url": "https://example.invalid"},
        "status": "active",
    }
    fields.update(overrides)
    cfg = ConnectorConfig(**fields)
    db_session.add(cfg)
    db_session.flush()
    return cfg


@pytest.fixture
def org(make_org):
    return make_org("connector-sync-mode")


@pytest.fixture
def admin(db_session, org):
    return _make_admin(db_session, org.id, "Admin")


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# Model-level: the four derivation states                               #
# --------------------------------------------------------------------- #


class TestDerivedSyncMode:
    def test_only_sync_schedule_set_is_scheduled(self, db_session, org):
        cfg = _make_connector_config(
            db_session, org.id, sync_schedule={"cron": "0 * * * *"}
        )
        assert cfg.derived_sync_mode() == "scheduled"

    def test_only_webhook_config_set_is_event(self, db_session, org):
        cfg = _make_connector_config(
            db_session,
            org.id,
            webhook_config={"url": "https://hooks.example.invalid/connector"},
        )
        assert cfg.derived_sync_mode() == "event"

    def test_neither_set_is_manual(self, db_session, org):
        cfg = _make_connector_config(db_session, org.id)
        assert cfg.derived_sync_mode() == "manual"

    def test_both_set_shows_both_not_just_one(self, db_session, org):
        cfg = _make_connector_config(
            db_session,
            org.id,
            sync_schedule={"cron": "0 * * * *"},
            webhook_config={"url": "https://hooks.example.invalid/connector"},
        )
        assert cfg.derived_sync_mode() == "scheduled, event", (
            "both sync_schedule and webhook_config were set, so the result "
            "must represent both rather than silently preferring one"
        )

    def test_empty_dict_counts_as_not_set(self, db_session, org):
        """An empty JSON object is falsy -- not meaningfully 'configured'."""
        cfg = _make_connector_config(
            db_session, org.id, sync_schedule={}, webhook_config={}
        )
        assert cfg.derived_sync_mode() == "manual"


# --------------------------------------------------------------------- #
# Route-level regression: the literal "throws on any data" bug           #
# --------------------------------------------------------------------- #


class TestApiListConnectorsRegression:
    def test_list_connectors_does_not_throw_and_returns_derived_sync_mode(
        self, db_session, org, admin, client, login_as
    ):
        """api_list_connectors() previously raised AttributeError on any real
        row via conn.connector_type.value / conn.status.value / conn.sync_mode
        .value. A fully-configured row (both sync_schedule and webhook_config
        set) must now come back as 200 with the plain string fields and the
        derived "scheduled, event" sync mode.
        """
        cfg = _make_connector_config(
            db_session,
            org.id,
            connector_type="servicenow",
            name="Prod ServiceNow",
            status="active",
            sync_schedule={"cron": "0 */4 * * *"},
            webhook_config={"url": "https://hooks.example.invalid/servicenow"},
        )
        db_session.commit()

        login_as(client, admin)
        resp = client.get("/integrations/api/connectors")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        rows = [c for c in body["connectors"] if c["id"] == cfg.id]
        assert len(rows) == 1
        row = rows[0]
        assert row["connector_type"] == "servicenow"
        assert row["status"] == "active"
        assert row["sync_mode"] == "scheduled, event"

    def test_list_connectors_manual_mode_row(
        self, db_session, org, admin, client, login_as
    ):
        """A connector with neither sync_schedule nor webhook_config set
        (the common case for a freshly wired connector) must also render
        without error, as "manual"."""
        cfg = _make_connector_config(
            db_session,
            org.id,
            connector_type="jira",
            name="Plain Jira",
            status="inactive",
        )
        db_session.commit()

        login_as(client, admin)
        resp = client.get("/integrations/api/connectors")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        rows = [c for c in body["connectors"] if c["id"] == cfg.id]
        assert len(rows) == 1
        row = rows[0]
        assert row["connector_type"] == "jira"
        assert row["status"] == "inactive"
        assert row["sync_mode"] == "manual"


class TestApiGetConnectorRegression:
    def test_get_connector_does_not_throw_and_returns_config(
        self, db_session, org, admin, client, login_as
    ):
        """api_get_connector() previously raised AttributeError via the same
        conn.connector_type.value / conn.status.value / conn.sync_mode.value
        chain as the list route, plus its own separate bug: conn.config_data
        isn't an attribute on the model at all (the column is config). A
        real, fully-configured row must now come back as 200 with the
        correct config payload and derived sync mode.
        """
        cfg = _make_connector_config(
            db_session,
            org.id,
            connector_type="servicenow",
            name="Prod ServiceNow",
            status="active",
            config={"instance_url": "https://prod.service-now.com", "batch_size": 100},
            sync_schedule={"cron": "0 */4 * * *"},
            webhook_config={"url": "https://hooks.example.invalid/servicenow"},
        )
        db_session.commit()

        login_as(client, admin)
        resp = client.get(f"/integrations/api/connectors/{cfg.id}")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        connector = body["connector"]
        assert connector["connector_type"] == "servicenow"
        assert connector["status"] == "active"
        assert connector["sync_mode"] == "scheduled, event"
        assert connector["config"] == {
            "instance_url": "https://prod.service-now.com",
            "batch_size": 100,
        }
