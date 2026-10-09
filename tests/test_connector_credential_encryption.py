"""Connector credential setters must raise, not silently store plaintext,
when no encryption key is configured.

Before this fix, OrgConnectorConfig.client_secret, DevOpsConnectorConfig.
access_token and LucidchartConnectorConfig.{client_secret,access_token,
refresh_token} each read a FERNET_KEY environment variable that nothing in
this codebase ever sets (not TestingConfig, not .env.example, not any
docker-compose or deploy file) and silently fell back to storing the
plaintext value when it was absent -- which, given FERNET_KEY is never set
anywhere, was the only path any of them ever actually took. They now delegate
to app.modules.codegen.services.credential_encryption, which reads the
CREDENTIAL_ENCRYPTION_KEY the rest of the codebase (M365, Jira) already uses,
and raises RuntimeError instead of degrading.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("credential-encryption")


class TestSetterRaisesWithoutAKey:
    """Each setter must refuse to store a credential unencrypted."""

    def test_org_connector_config_client_secret_raises(self, app, db_session, org):
        from app.models.connector_config import OrgConnectorConfig

        cfg = OrgConnectorConfig(organization_id=org.id, connector_type="servicenow")
        original_key = app.config.get("CREDENTIAL_ENCRYPTION_KEY")
        app.config["CREDENTIAL_ENCRYPTION_KEY"] = ""
        try:
            with pytest.raises(RuntimeError):
                cfg.client_secret = uuid.uuid4().hex
        finally:
            app.config["CREDENTIAL_ENCRYPTION_KEY"] = original_key
        assert cfg._client_secret_encrypted is None, (
            "The setter must not have stored anything when it raised."
        )

    def test_devops_connector_config_access_token_raises(self, app, db_session, org):
        from app.models.connector_config import DevOpsConnectorConfig

        cfg = DevOpsConnectorConfig(organization_id=org.id, provider="github")
        original_key = app.config.get("CREDENTIAL_ENCRYPTION_KEY")
        app.config["CREDENTIAL_ENCRYPTION_KEY"] = ""
        try:
            with pytest.raises(RuntimeError):
                cfg.access_token = uuid.uuid4().hex
        finally:
            app.config["CREDENTIAL_ENCRYPTION_KEY"] = original_key
        assert cfg._access_token_encrypted is None

    def test_lucidchart_connector_config_client_secret_raises(self, app, db_session, org):
        from app.models.connector_config import LucidchartConnectorConfig

        cfg = LucidchartConnectorConfig(organization_id=org.id)
        original_key = app.config.get("CREDENTIAL_ENCRYPTION_KEY")
        app.config["CREDENTIAL_ENCRYPTION_KEY"] = ""
        try:
            with pytest.raises(RuntimeError):
                cfg.client_secret = uuid.uuid4().hex
        finally:
            app.config["CREDENTIAL_ENCRYPTION_KEY"] = original_key
        assert cfg._client_secret_encrypted is None


class TestSettersAreRetired:
    """``OrgConnectorConfig`` and ``LucidchartConnectorConfig`` are retired:
    their credential setters always raise, even with a key configured,
    whether or not a row for this organisation already exists. New
    credentials must go through ``OrgConnectorCredential`` via
    ``OrgCredentialVault`` instead — the round-trip (store, encrypt per
    organisation, decrypt) is covered there, in
    tests/test_org_credential_store.py (``TestSecretsNeverReturned
    .test_multi_field_credential`` for the three Lucidchart fields,
    ``TestTwoOrgIsolation.test_org_a_retrieves_own_credential`` for the
    general case), so it is not duplicated here."""

    def test_org_connector_config_client_secret_round_trips(self, db_session, org):
        from app.models.connector_config import OrgConnectorConfig

        cfg = OrgConnectorConfig(organization_id=org.id, connector_type="servicenow")
        secret = uuid.uuid4().hex
        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.client_secret = secret
        assert cfg._client_secret_encrypted is None, (
            "The setter must not have stored anything when it raised."
        )

    def test_lucidchart_connector_config_three_fields_round_trip(self, db_session, org):
        from app.models.connector_config import LucidchartConnectorConfig

        cfg = LucidchartConnectorConfig(organization_id=org.id)
        secret, access, refresh = (uuid.uuid4().hex for _ in range(3))

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.client_secret = secret
        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.access_token = access
        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.refresh_token = refresh

        assert cfg._client_secret_encrypted is None
        assert cfg._access_token_encrypted is None
        assert cfg._refresh_token_encrypted is None


class TestGetterToleratesPreFixPlaintext:
    """Every row saved before this fix is plaintext under the
    _client_secret_encrypted/_access_token_encrypted column name (FERNET_KEY
    was never configured anywhere, so the old degrade-to-plaintext branch was
    the only path any setter ever took). The getter must still return that
    value rather than silently discarding it as an undecryptable token."""

    def test_org_connector_config_reads_back_pre_fix_plaintext(self, db_session, org):
        from app.models.connector_config import OrgConnectorConfig

        cfg = OrgConnectorConfig(organization_id=org.id, connector_type="servicenow")
        legacy_plaintext = uuid.uuid4().hex
        # Bypass the setter — this is what the OLD setter actually wrote.
        cfg._client_secret_encrypted = legacy_plaintext

        assert cfg.client_secret == legacy_plaintext, (
            "A pre-fix plaintext credential must still be readable, not "
            "silently returned as None."
        )

    def test_lucidchart_connector_config_reads_back_pre_fix_plaintext(
        self, db_session, org
    ):
        from app.models.connector_config import LucidchartConnectorConfig

        cfg = LucidchartConnectorConfig(organization_id=org.id)
        legacy_plaintext = uuid.uuid4().hex
        cfg._access_token_encrypted = legacy_plaintext

        assert cfg.access_token == legacy_plaintext
