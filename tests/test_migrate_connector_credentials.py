"""``flask migrate-connector-credentials`` must leave credentials in the shape
the live readers actually look for.

Before this fix, the command copied every field into a single JSON blob under
credential_type="credentials" -- but ServiceNowConnectorService._get_token
reads credential_type="client_secret", and LucidchartConnectorService.
get_access_token / get_refresh_token read credential_type="access_token" /
"refresh_token". After running the migration against pre-fix rows (old-style
columns on OrgConnectorConfig / LucidchartConnectorConfig, populated directly
the way an operator or an older build would have), those readers still got
None -- ServiceNow silently failed to authenticate and Lucidchart demanded
reconnection, for every organisation configured before this PR.

These tests seed two organisations with old-style rows, run the command, and
then call the real reader methods (not just OrgCredentialVault.retrieve) to
prove each organisation gets its own value back, and that running the
command twice changes nothing.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org_a(make_org):
    return make_org("migrate-creds-a")


@pytest.fixture
def org_b(make_org):
    return make_org("migrate-creds-b")


def _old_servicenow_config(db_session, org, *, instance_url, client_id, secret):
    """An OrgConnectorConfig row the way it looked before the retirement --
    client_secret written straight to the private column, bypassing the now-
    retired setter, exactly as the getter's pre-fix-plaintext tolerance
    expects (see tests/test_connector_credential_encryption.py)."""
    from app.extensions import db
    from app.models.connector_config import OrgConnectorConfig

    cfg = OrgConnectorConfig(
        organization_id=org.id,
        connector_type="servicenow",
        instance_url=instance_url,
        client_id=client_id,
        enabled=True,
    )
    cfg._client_secret_encrypted = secret
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _old_lucidchart_config(db_session, org, *, client_id, client_secret, access_token, refresh_token):
    from app.extensions import db
    from app.models.connector_config import LucidchartConnectorConfig

    cfg = LucidchartConnectorConfig(organization_id=org.id, client_id=client_id, enabled=True)
    cfg._client_secret_encrypted = client_secret
    cfg._access_token_encrypted = access_token
    cfg._refresh_token_encrypted = refresh_token
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _run(dry_run=False):
    """Invoke the migration CLI command and return its result.

    ``migrate-connector-credentials`` is registered by manage.py's own
    ``register_cli_commands`` (not by anything under app/commands/, which is
    what app.create_app() wires up on its own), so the command only exists
    on the app instance manage.py builds at import time -- same pattern as
    tests/test_schema_migrations.py's ``from manage import app``.
    """
    from manage import app as manage_app

    runner = manage_app.test_cli_runner()
    args = ["migrate-connector-credentials"]
    if dry_run:
        args.append("--dry-run")
    return runner.invoke(args=args)


def _mock_token_response(monkeypatch, captured):
    """Capture the ServiceNow token request's form data and return a canned
    OAuth2 token response, mirroring the ServiceNow instance's reply."""
    import app.services.servicenow_connector_service as svc_module

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "bearer-" + uuid.uuid4().hex, "expires_in": 1740}

    def _fake_post(url, data=None, timeout=None):
        captured.append(data)
        return _Resp()

    monkeypatch.setattr(svc_module.requests, "post", _fake_post)


class TestMigrationMatchesWhatTheRealReadersUse:
    def test_servicenow_and_lucidchart_readers_recover_each_orgs_own_values(
        self, db_session, org_a, org_b, monkeypatch
    ):
        from app.models.connector_config import OrgConnectorConfig, LucidchartConnectorConfig
        from app.services.lucidchart_connector_service import LucidchartConnectorService
        from app.services.servicenow_connector_service import ServiceNowConnectorService

        secret_a = uuid.uuid4().hex
        secret_b = uuid.uuid4().hex
        _old_servicenow_config(
            db_session, org_a,
            instance_url="https://org-a.service-now.com", client_id="sn-client-a",
            secret=secret_a,
        )
        _old_servicenow_config(
            db_session, org_b,
            instance_url="https://org-b.service-now.com", client_id="sn-client-b",
            secret=secret_b,
        )

        lucid_secret_a, lucid_access_a, lucid_refresh_a = (uuid.uuid4().hex for _ in range(3))
        lucid_secret_b, lucid_access_b, lucid_refresh_b = (uuid.uuid4().hex for _ in range(3))
        _old_lucidchart_config(
            db_session, org_a, client_id="lucid-client-a",
            client_secret=lucid_secret_a, access_token=lucid_access_a, refresh_token=lucid_refresh_a,
        )
        _old_lucidchart_config(
            db_session, org_b, client_id="lucid-client-b",
            client_secret=lucid_secret_b, access_token=lucid_access_b, refresh_token=lucid_refresh_b,
        )
        db_session.commit()

        result = _run()
        assert result.exit_code == 0, f"migration failed: {result.output}"
        # Never prints a credential value.
        for secret in (secret_a, secret_b, lucid_secret_a, lucid_secret_b,
                       lucid_access_a, lucid_access_b, lucid_refresh_a, lucid_refresh_b):
            assert secret not in result.output

        # --- ServiceNow: the real reader, not just the vault directly ---
        sn_config_a = OrgConnectorConfig.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow"
        ).one()
        sn_config_b = OrgConnectorConfig.query.filter_by(
            organization_id=org_b.id, connector_type="servicenow"
        ).one()

        captured_a: list = []
        _mock_token_response(monkeypatch, captured_a)
        ServiceNowConnectorService()._get_token(sn_config_a, org_a.id)
        assert captured_a[0]["client_secret"] == secret_a, (
            "ServiceNowConnectorService._get_token must authenticate with org A's "
            "own migrated secret."
        )

        captured_b: list = []
        _mock_token_response(monkeypatch, captured_b)
        ServiceNowConnectorService()._get_token(sn_config_b, org_b.id)
        assert captured_b[0]["client_secret"] == secret_b, (
            "TENANT LEAK or missing data: org B's token request did not use org B's "
            "own migrated secret."
        )

        # --- Lucidchart: the real reader methods ---
        lucid_config_a = LucidchartConnectorConfig.query.filter_by(organization_id=org_a.id).one()
        lucid_config_b = LucidchartConnectorConfig.query.filter_by(organization_id=org_b.id).one()

        service = LucidchartConnectorService()
        assert service.get_access_token(lucid_config_a) == lucid_access_a
        assert service.get_refresh_token(lucid_config_a) == lucid_refresh_a
        assert service.get_access_token(lucid_config_b) == lucid_access_b, (
            "TENANT LEAK or missing data: org B's access token was not recovered."
        )
        assert service.get_refresh_token(lucid_config_b) == lucid_refresh_b

    def test_running_twice_changes_nothing(self, db_session, org_a, org_b, monkeypatch):
        from app.models.connector_config import LucidchartConnectorConfig
        from app.services.lucidchart_connector_service import LucidchartConnectorService

        _old_servicenow_config(
            db_session, org_a,
            instance_url="https://org-a.service-now.com", client_id="sn-client-a",
            secret=uuid.uuid4().hex,
        )
        access_a, refresh_a = uuid.uuid4().hex, uuid.uuid4().hex
        _old_lucidchart_config(
            db_session, org_a, client_id="lucid-client-a",
            client_secret=uuid.uuid4().hex, access_token=access_a, refresh_token=refresh_a,
        )
        db_session.commit()

        first = _run()
        assert first.exit_code == 0
        # 1 ServiceNow client_secret + 3 Lucidchart fields (client_secret,
        # access_token, refresh_token) for org A = 4 discrete credentials.
        assert "Migrated: 4, skipped (already present): 0" in first.output, first.output

        lucid_config_a = LucidchartConnectorConfig.query.filter_by(organization_id=org_a.id).one()
        service = LucidchartConnectorService()
        before_access = service.get_access_token(lucid_config_a)
        before_refresh = service.get_refresh_token(lucid_config_a)
        assert before_access == access_a
        assert before_refresh == refresh_a

        second = _run()
        assert second.exit_code == 0
        assert "Migrated: 0" in second.output, (
            f"second run must migrate nothing new, got: {second.output}"
        )

        lucid_config_a = LucidchartConnectorConfig.query.filter_by(organization_id=org_a.id).one()
        assert service.get_access_token(lucid_config_a) == before_access
        assert service.get_refresh_token(lucid_config_a) == before_refresh

    def test_dry_run_writes_nothing(self, db_session, org_a):
        from app.models.connector_config import OrgConnectorCredential

        _old_servicenow_config(
            db_session, org_a,
            instance_url="https://org-a.service-now.com", client_id="sn-client-a",
            secret=uuid.uuid4().hex,
        )
        db_session.commit()

        result = _run(dry_run=True)
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()

        rows = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id, connector_type="servicenow", credential_type="client_secret",
        ).all()
        assert rows == [], "a dry run must not write any credential to the vault"
