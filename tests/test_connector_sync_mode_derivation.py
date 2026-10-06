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
for the exact matching rule and its exceptions (keys containing "auth" that
end in a metadata suffix like "_name" or "_enabled", e.g. auth_header_name or
basic_auth_enabled, are not treated as secrets; neither is "auth" appearing
merely as a substring of an unrelated word, e.g. "oauth", which is not itself
a credential).

Second security follow-up: a URL is itself often a credential -- a webhook
URL's path is typically the secret (e.g. Slack/Teams incoming webhooks), and
any URL can carry userinfo (user:pass@host) or a secret query parameter
(?token=/?key=/?sig=...). A key naming a webhook (containing "webhook",
anywhere, no exceptions) is now always fully masked rather than merely
sanitized. Every other key ending in "_url" is sanitized via
_sanitize_url -- real URL parsing (urllib.parse), not a regex guess --
stripping userinfo, query string and fragment while keeping scheme/host/path.
public_webhook_config() additionally treats every URL-shaped key inside
webhook_config as fully sensitive (url_keys_fully_sensitive=True), since
every URL there names a webhook endpoint, not just an incidental link.

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
            "token_endpoint",
            "authorization",
            "oauth_token",
            "auth_token",
            "webhook_url",
            "webhook_secret",
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
            "auth_url",
            "callback_url",
            "sync_endpoint_url",
        ],
    )
    def test_harmless_or_url_keys_are_not_fully_masked(self, key):
        """"auth" is broad enough to catch harmless field names describing
        an auth setting rather than holding one (the header's NAME, or
        whether auth is enabled); a key ending in "_url" that isn't
        otherwise sensitive (including "auth_url" itself) is sanitized
        rather than fully masked -- see TestSanitizeUrl and
        TestPublicConfigMasking for that path. Neither case is "fully
        sensitive" by this function."""
        from app.models.connector_config import _is_sensitive_key

        assert _is_sensitive_key(key) is False, key

    def test_oauth_is_not_treated_as_an_auth_match(self):
        """"auth" must start a word, not just appear inside one -- "oauth"
        contains the substring "auth" but is an extremely common, entirely
        harmless config section name. Masking it wholesale would hide an
        entire nested dict of otherwise-fine settings, not just a label."""
        from app.models.connector_config import _is_sensitive_key

        assert _is_sensitive_key("oauth") is False
        assert _is_sensitive_key("oauth_settings") is False
        # "oauth_token" is still masked -- via the separate "token" term,
        # not via "auth" -- so this isn't a blanket oauth exemption.
        assert _is_sensitive_key("oauth_token") is True


class TestSanitizeUrl:
    """Unit coverage for _sanitize_url: strips userinfo/query/fragment via
    real URL parsing, keeps scheme/host/path."""

    def test_strips_userinfo_query_and_fragment(self):
        from app.models.connector_config import _sanitize_url

        assert (
            _sanitize_url("https://user:pass@api.example.com/v1/sync?token=abc123#frag")
            == "https://api.example.com/v1/sync"
        )

    def test_clean_url_is_unchanged(self):
        from app.models.connector_config import _sanitize_url

        assert (
            _sanitize_url("https://prod.service-now.com")
            == "https://prod.service-now.com"
        )

    def test_port_is_preserved(self):
        from app.models.connector_config import _sanitize_url

        assert (
            _sanitize_url("https://user:pass@api.example.com:8443/path?key=secret")
            == "https://api.example.com:8443/path"
        )

    def test_non_string_value_passes_through(self):
        from app.models.connector_config import _sanitize_url

        assert _sanitize_url(None) is None


class TestPublicConfigMasking:
    def test_public_config_masks_top_level_and_nested_secrets(self, db_session, org):
        """A config with a password, an API key, and a client secret nested
        inside an "oauth" dict: every secret value is replaced with
        "configured", every harmless value (including the nested dict's own
        key, "oauth") passes through/recurses unchanged. "token_endpoint" is
        also masked -- it contains "token", and this module masks that term
        unconditionally (see its module-level docstring)."""
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
        # "oauth" itself must recurse, not be wholly masked (it is not an
        # auth-word match -- see test_oauth_is_not_treated_as_an_auth_match).
        assert isinstance(public["oauth"], dict)
        assert public["oauth"]["client_secret"] == "configured"
        assert public["oauth"]["token_endpoint"] == "configured"
        # The real config column is untouched -- public_config() returns a copy.
        assert cfg.config["password"] == "hunter2-super-secret"

    def test_public_config_sanitizes_non_webhook_urls_instead_of_masking(
        self, db_session, org
    ):
        """A key ending in "_url" that isn't otherwise sensitive still
        shouldn't come back raw -- it's sanitized (userinfo/query/fragment
        stripped), not fully masked, so the still-useful host/path survive."""
        cfg = _make_connector_config(
            db_session,
            org.id,
            config={
                "instance_url": "https://prod.service-now.com",
                "callback_url": "https://user:s3cr3tpass@api.example.com/callback",
                "sync_endpoint_url": "https://api.example.com/sync?token=zzz999secrettoken",
            },
        )

        public = cfg.public_config()

        assert public["instance_url"] == "https://prod.service-now.com"
        assert public["callback_url"] == "https://api.example.com/callback"
        assert public["sync_endpoint_url"] == "https://api.example.com/sync"

    def test_public_config_fully_masks_webhook_named_urls(self, db_session, org):
        """A webhook URL is never safe to return even sanitized -- its path
        is typically itself the credential (e.g. a Slack incoming-webhook
        URL) -- so a key naming one is fully masked, not just stripped of
        its query string."""
        cfg = _make_connector_config(
            db_session,
            org.id,
            config={
                "webhook_url": (
                    "https://hooks.slack.com/services/T000/B000/"
                    "XXXXXXXXXXXXXXXXXXXXXXXX"
                ),
            },
        )

        public = cfg.public_config()

        assert public["webhook_url"] == "configured"

    def test_public_webhook_config_masks_secrets_and_url_fields(self, db_session, org):
        """public_webhook_config() treats every URL-shaped key as fully
        sensitive (not just sanitized), since every URL inside this column
        is a webhook endpoint, not an incidental link."""
        cfg = _make_connector_config(
            db_session,
            org.id,
            webhook_config={
                "url": "https://hooks.example.invalid/servicenow/T000/B000/XYZ",
                "signing_secret": "whsec_real_secret_value",
            },
        )

        public = cfg.public_webhook_config()

        assert public["url"] == "configured"
        assert public["signing_secret"] == "configured"

    def test_public_config_empty_or_none_passes_through(self, db_session, org):
        cfg = _make_connector_config(db_session, org.id, config={})
        assert cfg.public_config() == {}


class TestApiGetConnectorNeverLeaksSecretValues:
    def test_get_connector_response_contains_no_secret_values(
        self, db_session, org, admin, client, login_as
    ):
        """The literal regression test for the credential-leak: a connector
        whose config has a password, an API key, a token nested inside
        another dict, a Slack-style webhook URL, a URL carrying userinfo,
        and a URL carrying a ?token= query secret -- plus webhook_config's
        own signing secret and endpoint URL. The route must still return
        200, and -- critically -- none of the seeded secret VALUES may
        appear anywhere in the raw response body. Checking the masked keys
        individually would not catch a masking bug that touched the wrong
        key while leaving the real secret value reachable under another key
        or in stringified form, so this searches the full raw response text
        for each secret value.
        """
        secret_password = "hunter2-super-secret-value-9f3a"
        secret_api_key = "sk-live-abcdef0123456789"
        secret_nested_token = "nested-oauth-token-xyz-77213"
        secret_webhook_signing = "whsec_do_not_leak_this_either"
        secret_slack_webhook_url = (
            "https://hooks.slack.com/services/T0000AAAA/B1111BBBB/"
            "cccccccccccccccccccccccc"
        )
        secret_url_userinfo = "report-user:sup3r-s3cr3t-pw@"
        secret_url_token_param = "token=qqq888zzz777querysecrettoken"

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
                "webhook_url": secret_slack_webhook_url,
                "callback_url": f"https://{secret_url_userinfo}api.example.com/callback",
                "sync_endpoint_url": f"https://api.example.com/sync?{secret_url_token_param}",
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
            secret_slack_webhook_url,
            secret_url_userinfo,
            secret_url_token_param,
        ):
            assert secret_value not in raw_body, (
                f"secret value {secret_value!r} was found in the raw API "
                "response body -- a credential leaked"
            )

        # And the masked/sanitized keys are genuinely present with their
        # expected safe form, not just absent (proving the fields render,
        # masked or sanitized, not silently dropped).
        body = resp.get_json()
        config = body["connector"]["config"]
        assert config["password"] == "configured"
        assert config["api_key"] == "configured"
        assert config["oauth"]["token"] == "configured"
        assert config["instance_url"] == "https://prod.service-now.com"
        assert config["webhook_url"] == "configured"
        assert config["callback_url"] == "https://api.example.com/callback"
        assert config["sync_endpoint_url"] == "https://api.example.com/sync"

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
