"""The Lucidchart OAuth token exchange writes access/refresh tokens.

``LucidchartConnectorConfig.access_token`` / ``.refresh_token`` are retired
(their setters raise RuntimeError — see tests/test_connector_credential_encryption.py
and tests/test_org_credential_store.py). ``LucidchartConnectorService._store_token_payload``,
reached from the OAuth callback route
(app/modules/architecture/routes/lucidchart_import_routes.py::lucidchart_oauth_callback),
is the one live writer of those two fields and must store them through
``OrgCredentialVault`` instead — encrypted with the calling organisation's own
key, isolated from every other organisation.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org_a(make_org):
    return make_org("lucidchart-a")


@pytest.fixture
def org_b(make_org):
    return make_org("lucidchart-b")


def _mock_token_response(monkeypatch, access_token, refresh_token, expires_in=3600):
    """Replace requests.post in the service module with a canned OAuth response."""
    import app.services.lucidchart_connector_service as svc_module

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "scope": "documents:read folders:read offline_access",
                "account_id": "acct-" + uuid.uuid4().hex[:8],
            }

    def _fake_post(url, data=None, headers=None, timeout=None):
        return _Resp()

    monkeypatch.setattr(svc_module.requests, "post", _fake_post)


class TestTokenExchangeStoresSecretsInVault:
    def test_exchange_succeeds_and_stores_tokens_encrypted_via_the_vault(
        self, db_session, org_a, monkeypatch
    ):
        from app.extensions import db
        from app.models.connector_config import LucidchartConnectorConfig, OrgConnectorCredential
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.lucidchart_connector_service import LucidchartConnectorService

        config = LucidchartConnectorConfig(
            organization_id=org_a.id,
            client_id="org-a-lucid-client-id",
        )
        # The client_secret column is pre-populated here the way an operator
        # would seed it directly (the setter has no live writer in-app —
        # see the module docstring on app/models/connector_config.py).
        config._client_secret_encrypted = uuid.uuid4().hex
        db.session.add(config)
        db.session.flush()

        access_token = uuid.uuid4().hex
        refresh_token = uuid.uuid4().hex
        _mock_token_response(monkeypatch, access_token, refresh_token)

        service = LucidchartConnectorService()
        # Before this fix, config.access_token = ... inside _store_token_payload
        # raised RuntimeError ("LucidchartConnectorConfig is RETIRED").
        token_payload = service.exchange_code_for_tokens(
            config=config, code="auth-code", redirect_uri="https://app.example/cb"
        )
        assert token_payload["access_token"] == access_token

        # The retired columns must never receive the new tokens.
        assert config._access_token_encrypted is None
        assert config._refresh_token_encrypted is None

        access_row = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id, connector_type="lucidchart", credential_type="access_token",
        ).first()
        refresh_row = OrgConnectorCredential.query.filter_by(
            organization_id=org_a.id, connector_type="lucidchart", credential_type="refresh_token",
        ).first()
        assert access_row is not None
        assert refresh_row is not None
        assert access_token.encode() not in access_row.encrypted_value, (
            "The access token must be stored encrypted, not as plaintext."
        )
        assert refresh_token.encode() not in refresh_row.encrypted_value, (
            "The refresh token must be stored encrypted, not as plaintext."
        )

        vault = OrgCredentialVault()
        assert vault.retrieve(org_a.id, "lucidchart", "access_token") == access_token
        assert vault.retrieve(org_a.id, "lucidchart", "refresh_token") == refresh_token

        # The service's own read path (used by routes to gate on "needs_auth")
        # must also come from the vault.
        assert service.get_access_token(config) == access_token
        assert service.get_refresh_token(config) == refresh_token

    def test_tokens_are_isolated_between_organisations(
        self, db_session, org_a, org_b, monkeypatch
    ):
        from app.extensions import db
        from app.models.connector_config import LucidchartConnectorConfig
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.services.lucidchart_connector_service import LucidchartConnectorService

        service = LucidchartConnectorService()

        config_a = LucidchartConnectorConfig(organization_id=org_a.id, client_id="a-client")
        config_a._client_secret_encrypted = uuid.uuid4().hex
        config_b = LucidchartConnectorConfig(organization_id=org_b.id, client_id="b-client")
        config_b._client_secret_encrypted = uuid.uuid4().hex
        db.session.add_all([config_a, config_b])
        db.session.flush()

        access_a, refresh_a = uuid.uuid4().hex, uuid.uuid4().hex
        _mock_token_response(monkeypatch, access_a, refresh_a)
        service.exchange_code_for_tokens(config_a, code="code-a", redirect_uri="https://app/cb")

        access_b, refresh_b = uuid.uuid4().hex, uuid.uuid4().hex
        _mock_token_response(monkeypatch, access_b, refresh_b)
        service.exchange_code_for_tokens(config_b, code="code-b", redirect_uri="https://app/cb")

        vault = OrgCredentialVault()
        assert vault.retrieve(org_a.id, "lucidchart", "access_token") == access_a
        assert vault.retrieve(org_b.id, "lucidchart", "access_token") == access_b, (
            "TENANT LEAK: org B's access token was not the one org B's exchange "
            "returned — org A's exchange may have overwritten it."
        )
        assert vault.retrieve(org_a.id, "lucidchart", "refresh_token") == refresh_a
        assert vault.retrieve(org_b.id, "lucidchart", "refresh_token") == refresh_b

        assert service.get_access_token(config_a) == access_a
        assert service.get_access_token(config_b) == access_b
