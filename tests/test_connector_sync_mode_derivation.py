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

Security follow-up: reading conn.config instead of the broken conn.config_data
made api_get_connector() return config's real contents -- including any
credential stored in it (config is a plain JSON column whose own comment is
"API endpoints, credentials, etc."; there is no separate encrypted-credentials
column on this model). Before this fix the route crashed, so nothing ever
leaked; fixing the crash without masking would have turned a 500 into a 200
with real secrets in the body. ConnectorConfig.public_config() (and
public_webhook_config(), for the same reason on webhook_config) now masks any
key that looks like it names a secret -- recursively, including nested dicts
and lists -- with the literal string "configured" before either column is
serialised. See _is_sensitive_key's docstring in app/models/connector_config.py
for the exact matching rule and its one documented exception (keys containing
"auth" that end in a metadata suffix like "_name" or "_enabled", e.g.
auth_header_name or basic_auth_enabled, are not treated as secrets).

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


# --------------------------------------------------------------------- #
# Security: config/webhook_config secrets must never be returned raw    #
# --------------------------------------------------------------------- #


class TestSensitiveKeyDetection:
    """Unit coverage for _is_sensitive_key's exact matching rule."""

    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "api_key",
            "apikey",
            "client_secret",
            "private_key",
            "credential",
            "credentials",
            "token",
            "token_type",
            "authorization",
            "oauth_token",
            "auth_token",
        ],
    )
    def test_sensitive_keys_are_detected(self, key):
        from app.models.connector_config import _is_sensitive_key

        assert _is_sensitive_key(key) is True, key

    @pytest.mark.parametrize(
        "key",
        [
            "instance_url",
            "batch_size",
            "auth_header_name",
            "basic_auth_enabled",
            "auth_method",
        ],
    )
    def test_harmless_keys_naming_auth_metadata_are_not_masked(self, key):
        """"auth" is broad enough to catch harmless field names describing
        an auth setting rather than holding one (the header's NAME, or
        whether auth is enabled) -- these must pass through unmasked."""
        from app.models.connector_config import _is_sensitive_key

        assert _is_sensitive_key(key) is False, key


class TestPublicConfigMasking:
    def test_public_config_masks_top_level_and_nested_secrets(self, db_session, org):
        """A config with a password, an API key, and a token nested inside
        a dict inside the config: every secret value is replaced with
        "configured", every harmless value passes through unchanged."""
        cfg = _make_connector_config(
            db_session,
            org.id,
            config={
                "instance_url": "https://prod.service-now.com",
                "password": "hunter2-super-secret",
                "api_key": "sk-live-abcdef123456",
                "oauth": {
                    "client_secret": "cs-nested-secret-value",
                    "token_endpoint": "https://prod.service-now.com/oauth/token",
                },
            },
        )

        public = cfg.public_config()

        assert public["instance_url"] == "https://prod.service-now.com"
        assert public["password"] == "configured"
        assert public["api_key"] == "configured"
        assert public["oauth"]["client_secret"] == "configured"
        assert public["oauth"]["token_endpoint"] == (
            "https://prod.service-now.com/oauth/token"
        )
        # The real config column is untouched -- public_config() returns a copy.
        assert cfg.config["password"] == "hunter2-super-secret"

    def test_public_webhook_config_masks_secrets_too(self, db_session, org):
        cfg = _make_connector_config(
            db_session,
            org.id,
            webhook_config={
                "url": "https://hooks.example.invalid/servicenow",
                "signing_secret": "whsec_real_secret_value",
            },
        )

        public = cfg.public_webhook_config()

        assert public["url"] == "https://hooks.example.invalid/servicenow"
        assert public["signing_secret"] == "configured"

    def test_public_config_empty_or_none_passes_through(self, db_session, org):
        cfg = _make_connector_config(db_session, org.id, config={})
        assert cfg.public_config() == {}


class TestApiGetConnectorNeverLeaksSecretValues:
    def test_get_connector_response_contains_no_secret_values(
        self, db_session, org, admin, client, login_as
    ):
        """The literal regression test for the credential-leak: a connector
        whose config has a password, an API key, and a token nested inside
        another dict. The route must still return 200, and -- critically --
        none of the seeded secret VALUES may appear anywhere in the raw
        response body. Checking the masked keys individually would not catch
        a masking bug that touched the wrong key while leaving the real
        secret value reachable under another key or in stringified form, so
        this searches the full raw response text for each secret value.
        """
        secret_password = "hunter2-super-secret-value-9f3a"
        secret_api_key = "sk-live-abcdef0123456789"
        secret_nested_token = "nested-oauth-token-xyz-77213"
        secret_webhook_signing = "whsec_do_not_leak_this_either"

        cfg = _make_connector_config(
            db_session,
            org.id,
            connector_type="servicenow",
            name="Prod ServiceNow",
            status="active",
            config={
                "instance_url": "https://prod.service-now.com",
                "password": secret_password,
                "api_key": secret_api_key,
                "oauth": {"token": secret_nested_token},
            },
            webhook_config={
                "url": "https://hooks.example.invalid/servicenow",
                "signing_secret": secret_webhook_signing,
            },
        )
        db_session.commit()

        login_as(client, admin)
        resp = client.get(f"/integrations/api/connectors/{cfg.id}")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        raw_body = resp.get_data(as_text=True)

        for secret_value in (
            secret_password,
            secret_api_key,
            secret_nested_token,
            secret_webhook_signing,
        ):
            assert secret_value not in raw_body, (
                f"secret value {secret_value!r} was found in the raw API "
                "response body -- a credential leaked"
            )

        # And the masked keys are genuinely present as "configured", not
        # just absent (proving the fields render, masked, not dropped).
        body = resp.get_json()
        config = body["connector"]["config"]
        assert config["password"] == "configured"
        assert config["api_key"] == "configured"
        assert config["oauth"]["token"] == "configured"
        assert config["instance_url"] == "https://prod.service-now.com"

    def test_list_connectors_response_contains_no_secret_values(
        self, db_session, org, admin, client, login_as
    ):
        """api_list_connectors() doesn't currently serialise config at all,
        but prove it stays that way under a row with real secrets set --
        nothing about this route should ever leak them either."""
        secret_password = "hunter2-list-route-secret-4471"

        _make_connector_config(
            db_session,
            org.id,
            connector_type="jira",
            name="Prod Jira",
            status="active",
            config={"instance_url": "https://jira.example.invalid", "password": secret_password},
        )
        db_session.commit()

        login_as(client, admin)
        resp = client.get("/integrations/api/connectors")

        assert resp.status_code == 200, resp.get_data(as_text=True)
        raw_body = resp.get_data(as_text=True)
        assert secret_password not in raw_body
