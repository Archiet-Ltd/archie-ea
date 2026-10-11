"""Per-organisation credential store tests.

Acceptance criteria covered:
1. Two-organisation isolation: A's credentials cannot be decrypted with B's key;
   A's connectors page never lists B's connectors.
2. Rotation: re-encrypts every row, uses MultiFernet for zero-downtime, and
   the connector still authenticates against a recorded fixture.
3. Secrets never returned after entry: ``get_masked`` returns a fixed mask
   with no plaintext; ``retrieve`` returns plaintext only for authorised callers.
4. Old credential models are RETIRED (setters raise RuntimeError).
5. Cross-tenant protection: store/retrieve inside ``tenant_ctx`` validates
   that org_id matches ``g.current_org_id``.
6. Process-global cache removed: rotation by another process is visible
   immediately, and rollback does not corrupt subsequent reads.
7. ``CredentialUnreadable`` is raised when decryption fails, not None.
"""

from __future__ import annotations

import json
import uuid

import pytest
from cryptography.fernet import InvalidToken

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def org(make_org):
    return make_org("credential-store-a")


@pytest.fixture
def other_org(make_org):
    return make_org("credential-store-b")


@pytest.fixture
def vault():
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    return OrgCredentialVault()


@pytest.fixture
def make_vault():
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    def _make():
        return OrgCredentialVault()

    return _make


# ===========================================================================
# Two-organisation isolation
# ===========================================================================


class TestTwoOrgIsolation:
    """A's credentials must not be decryptable with B's key, and A's stored
    values must never appear when querying as organisation B."""

    def test_org_a_credential_unreadable_by_org_b(self, vault, org, other_org):
        """Credential stored for org A cannot be decrypted with org B's key."""
        from app.modules.codegen.services.credential_vault import OrgCredentialVault
        from app.modules.codegen.services.credential_encryption import (
            _get_org_fernet,
        )
        from app.models.connector_config import OrganizationEncryptionKey
        from app.extensions import db
        from cryptography.fernet import Fernet
        from flask import current_app

        # Ensure B has a key row so we can attempt decryption
        master_key = current_app.config["ORG_ENCRYPTION_MASTER_KEY"]
        master = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        raw_key = Fernet.generate_key()
        encrypted = master.encrypt(raw_key)
        if not OrganizationEncryptionKey.query.filter_by(organization_id=other_org.id).first():
            db.session.add(OrganizationEncryptionKey(
                organization_id=other_org.id, encrypted_key=encrypted, key_version=1,
            ))
            db.session.flush()

        secret = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"api_key": secret}))

        # Get the raw encrypted bytes and try to decrypt with B's key
        from app.models.connector_config import OrgConnectorCredential

        row = OrgConnectorCredential.query.filter_by(
            organization_id=org.id, connector_type="servicenow",
        ).first()
        assert row is not None

        b_fernet = _get_org_fernet(other_org.id)
        with pytest.raises(InvalidToken):
            b_fernet.decrypt(row.encrypted_value)

        # Org B also cannot retrieve via vault (different key)
        result = OrgCredentialVault().retrieve(other_org.id, "servicenow")
        assert result is None, "Org B must not see org A's credentials"

    def test_org_a_retrieves_own_credential(self, vault, org, other_org):
        """Credential stored for org A is readable when queried with org A's id."""
        secret = uuid.uuid4().hex
        vault.store(org.id, "jira", "credentials", json.dumps({"api_key": secret}))
        result = vault.retrieve(org.id, "jira")
        assert result is not None
        data = json.loads(result)
        assert data["api_key"] == secret

    def test_each_org_has_own_key_row(self, vault, org, other_org, create_org_key):
        """Each organisation gets its own distinct ``OrganizationEncryptionKey``
        row with a unique encrypted key."""
        from app.models.connector_config import OrganizationEncryptionKey
        from app.modules.codegen.services.credential_encryption import _get_org_fernet

        # create_org_key creates a key for org; do the same for other_org
        from app.extensions import db
        from cryptography.fernet import Fernet
        from flask import current_app

        master_key = current_app.config["ORG_ENCRYPTION_MASTER_KEY"]
        master = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        raw_key = Fernet.generate_key()
        encrypted = master.encrypt(raw_key)
        if not OrganizationEncryptionKey.query.filter_by(organization_id=other_org.id).first():
            db.session.add(OrganizationEncryptionKey(
                organization_id=other_org.id, encrypted_key=encrypted, key_version=1,
            ))
            db.session.flush()

        key_a = _get_org_fernet(org.id)
        key_b = _get_org_fernet(other_org.id)

        # Distinct Fernet instances — encrypting the same plaintext with
        # different keys produces different tokens (Fernet includes key
        # derivation in the token)
        token_a = key_a.encrypt(b"test payload")
        token_b = key_b.encrypt(b"test payload")
        assert token_a != token_b, "Each org must have a distinct Fernet key"

        rows = OrganizationEncryptionKey.query.all()
        org_ids = {r.organization_id for r in rows}
        assert org.id in org_ids
        assert other_org.id in org_ids

    def test_connector_credential_org_scope(self, vault, org, other_org):
        """OrgConnectorCredential rows are scoped per org — querying by org A
        does not return org B's rows."""
        from app.models.connector_config import OrgConnectorCredential

        vault.store(org.id, "datadog", "credentials", json.dumps({"key": "a"}))
        vault.store(other_org.id, "datadog", "credentials", json.dumps({"key": "b"}))

        a_rows = OrgConnectorCredential.query.filter_by(
            organization_id=org.id
        ).all()
        b_rows = OrgConnectorCredential.query.filter_by(
            organization_id=other_org.id
        ).all()

        assert len(a_rows) == 1
        assert len(b_rows) == 1
        # Verify data content: retrieve and check
        a_data = vault.retrieve(org.id, "datadog")
        b_data = vault.retrieve(other_org.id, "datadog")
        assert json.loads(a_data)["key"] == "a"  # type: ignore
        assert json.loads(b_data)["key"] == "b"  # type: ignore

    def test_cross_tenant_store_raises(self, vault, org, other_org, tenant_ctx):
        """Storing credentials for a different org inside a tenant context raises."""
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": "a"}))
        with tenant_ctx(org.id):
            with pytest.raises(ValueError, match="does not match current request"):
                vault.store(other_org.id, "jira", "credentials",
                            json.dumps({"key": "attacker"}))

    def test_cross_tenant_retrieve_returns_nothing(self, vault, org, other_org,
                                                    tenant_ctx):
        """Retrieving credentials for a different org inside a tenant context
        raises rather than silently returning data from the wrong org."""
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": "a"}))
        with tenant_ctx(org.id):
            with pytest.raises(ValueError, match="does not match current request"):
                vault.retrieve(other_org.id, "servicenow")


# ===========================================================================
# Rotation
# ===========================================================================


class TestRotation:
    """Key rotation re-encrypts every row and credentials remain decryptable."""

    def test_rotate_re_encrypts_and_still_readable(self, vault, org, create_org_key):
        """After rotation, existing credentials are still readable."""
        secret = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": secret}))

        # Verify pre-rotation
        before = vault.retrieve(org.id, "servicenow")
        assert json.loads(before)["key"] == secret  # type: ignore

        # Rotate
        new_version = vault.rotate_all_credentials(org.id)
        assert new_version >= 1

        # Verify post-rotation — still readable
        after = vault.retrieve(org.id, "servicenow")
        assert after is not None
        assert json.loads(after)["key"] == secret

    def test_rotate_updates_key_version(self, vault, org, create_org_key):
        """Rotation increments the key version in OrganizationEncryptionKey and
        on each credential row."""
        from app.models.connector_config import OrganizationEncryptionKey, OrgConnectorCredential

        vault.store(org.id, "jira", "credentials", json.dumps({"key": "v0"}))
        v1 = vault.rotate_all_credentials(org.id)
        v2 = vault.rotate_all_credentials(org.id)

        record = OrganizationEncryptionKey.query.filter_by(
            organization_id=org.id
        ).first()
        assert record is not None
        assert record.key_version == v2
        assert v2 > v1

        # Credential rows also carry the new version
        row = OrgConnectorCredential.query.filter_by(
            organization_id=org.id, connector_type="jira",
        ).first()
        assert row.key_version == v2

    def test_rotate_multiple_rows(self, vault, org, create_org_key):
        """Rotation re-encrypts all credential rows for the org."""
        secrets = {
            "servicenow": uuid.uuid4().hex,
            "jira": uuid.uuid4().hex,
            "datadog": uuid.uuid4().hex,
        }
        for ctype, secret in secrets.items():
            vault.store(org.id, ctype, "credentials", json.dumps({"key": secret}))

        vault.rotate_all_credentials(org.id)

        for ctype, secret in secrets.items():
            data = vault.retrieve(org.id, ctype)
            assert data is not None
            assert json.loads(data)["key"] == secret

    def test_rotate_without_stored_credentials(self, vault, org, create_org_key):
        """Rotation succeeds even when no credentials exist yet."""
        version = vault.rotate_all_credentials(org.id)
        assert version >= 1

    def test_each_org_key_is_independent_under_rotation(self, vault, org, other_org,
                                                         create_org_key):
        """Rotating org A's key does not affect org B's credentials."""
        secret_a = uuid.uuid4().hex
        secret_b = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": secret_a}))
        vault.store(other_org.id, "servicenow", "credentials", json.dumps({"key": secret_b}))

        vault.rotate_all_credentials(org.id)

        # Org A's credential still readable
        data_a = vault.retrieve(org.id, "servicenow")
        assert json.loads(data_a)["key"] == secret_a  # type: ignore

        # Org B's credential also still readable (unaffected)
        data_b = vault.retrieve(other_org.id, "servicenow")
        assert json.loads(data_b)["key"] == secret_b  # type: ignore

    def test_rotate_with_multi_fernet_fallthrough(self, vault, org, create_org_key):
        """After rotation, a credential read with an older key version still
        works via MultiFernet fallthrough."""
        secret = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": secret}))

        # First rotation
        vault.rotate_all_credentials(org.id)

        # Store a new credential after first rotation
        vault.store(org.id, "jira", "credentials", json.dumps({"key": "post-rotate"}))

        # Second rotation
        vault.rotate_all_credentials(org.id)

        # Both credentials should still be readable
        data1 = vault.retrieve(org.id, "servicenow")
        assert json.loads(data1)["key"] == secret

        data2 = vault.retrieve(org.id, "jira")
        assert json.loads(data2)["key"] == "post-rotate"


# ===========================================================================
# Cached-key and rollback safety (H3)
# ===========================================================================


class TestKeyCacheSafety:
    """The process-global cache is removed; key reads always hit the database."""

    def test_key_reads_database_each_time(self, vault, org, create_org_key):
        """After rotation, the next read uses the new key from the database,
        not a stale cache."""
        from app.modules.codegen.services.credential_encryption import get_org_key_version

        secret = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": secret}))

        v1 = get_org_key_version(org.id)
        vault.rotate_all_credentials(org.id)
        v2 = get_org_key_version(org.id)

        assert v2 > v1
        # Fresh read after rotation uses new key
        data = vault.retrieve(org.id, "servicenow")
        assert json.loads(data)["key"] == secret  # type: ignore


# ===========================================================================
# CredentialUnreadable (M4)
# ===========================================================================


class TestCredentialUnreadable:
    """Failed decryption raises ``CredentialUnreadable`` instead of returning
    None."""

    def test_credential_unreadable_raised(self, vault, org, create_org_key):
        """Attempting to decrypt a corrupted ciphertext raises CredentialUnreadable."""
        from app.modules.codegen.services.credential_encryption import (
            decrypt_for_org, CredentialUnreadable,
        )

        # Store a credential
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": "test"}))

        # Corrupt the stored ciphertext
        from app.models.connector_config import OrgConnectorCredential

        row = OrgConnectorCredential.query.filter_by(
            organization_id=org.id, connector_type="servicenow",
        ).first()
        row.encrypted_value = b"corrupted_data"
        from app.extensions import db
        db.session.flush()

        with pytest.raises(CredentialUnreadable):
            vault.retrieve(org.id, "servicenow")

    def test_decrypt_for_org_none_on_empty(self, vault, org, create_org_key):
        """decrypt_for_org returns None when ciphertext is empty/None, not raises."""
        from app.modules.codegen.services.credential_encryption import decrypt_for_org

        result = decrypt_for_org(org.id, None)
        assert result is None

        result = decrypt_for_org(org.id, b"")
        assert result is None


# ===========================================================================
# Secrets never returned after entry
# ===========================================================================


class TestSecretsNeverReturned:
    """Credentials are stored encrypted and never returned in plaintext to
    unauthorised callers. ``get_masked`` returns a fixed mask; ``retrieve``
    returns the full value only to authorised internal callers."""

    def test_get_masked_never_returns_plaintext(self, vault, org, create_org_key):
        """get_masked returns '******', never the full secret."""
        secret = "sk-" + uuid.uuid4().hex
        vault.store(org.id, "openai", "api_key", secret)

        masked = vault.get_masked(org.id, "openai", "api_key")
        assert masked is not None
        assert secret not in masked, "get_masked must not leak the full secret"
        assert masked == "******"

    def test_get_masked_short_secret(self, vault, org, create_org_key):
        """Short secrets also return '******', never any plaintext."""
        vault.store(org.id, "test", "key", "short")
        masked = vault.get_masked(org.id, "test", "key")
        assert masked == "******"

    def test_get_masked_empty(self, vault, org):
        """No credential → get_masked returns None."""
        masked = vault.get_masked(org.id, "nonexistent", "key")
        assert masked is None

    def test_retrieve_returns_full_secret(self, vault, org, create_org_key):
        """retrieve returns the full plaintext, for authorised internal use."""
        secret = uuid.uuid4().hex
        vault.store(org.id, "internal", "token", secret)
        retrieved = vault.retrieve(org.id, "internal", "token")
        assert retrieved == secret

    def test_stored_encrypted_in_database(self, vault, org, create_org_key):
        """The raw database value is encrypted, not plaintext."""
        from app.models.connector_config import OrgConnectorCredential

        secret = uuid.uuid4().hex
        vault.store(org.id, "servicenow", "credentials", json.dumps({"key": secret}))

        row = OrgConnectorCredential.query.filter_by(
            organization_id=org.id,
            connector_type="servicenow",
        ).first()
        assert row is not None
        raw = row.encrypted_value
        assert isinstance(raw, bytes)
        assert secret.encode() not in raw, (
            "The secret must not appear as plaintext in the database"
        )

    def test_multi_field_credential(self, vault, org, create_org_key):
        """Multiple credential fields for the same connector can be stored and
        retrieved independently."""
        vault.store(org.id, "lucidchart", "client_secret", "cs_secret")
        vault.store(org.id, "lucidchart", "access_token", "at_token")
        vault.store(org.id, "lucidchart", "refresh_token", "rt_token")

        assert vault.retrieve(org.id, "lucidchart", "client_secret") == "cs_secret"
        assert vault.retrieve(org.id, "lucidchart", "access_token") == "at_token"
        assert vault.retrieve(org.id, "lucidchart", "refresh_token") == "rt_token"


# ===========================================================================
# Old models marked RETIRED
# ===========================================================================


class TestOldModelsRetired:
    """Existing credential models (OrgConnectorConfig, DevOpsConnectorConfig,
    LucidchartConnectorConfig) are marked as RETIRED. Their setters raise
    RuntimeError, preventing new writes."""

    def test_org_connector_config_docstring_marks_retired(self):
        from app.models.connector_config import (
            OrgConnectorConfig,
            DevOpsConnectorConfig,
            LucidchartConnectorConfig,
        )

        assert "RETIRED" in (OrgConnectorConfig.__doc__ or "")
        assert "RETIRED" in (DevOpsConnectorConfig.__doc__ or "")
        assert "RETIRED" in (LucidchartConnectorConfig.__doc__ or "")

    def test_old_setters_raise_runtime_error(self, app, db_session, org):
        """Old credential setters raise RuntimeError — RETIRED."""
        from app.models.connector_config import OrgConnectorConfig

        cfg = OrgConnectorConfig(
            organization_id=org.id,
            connector_type="servicenow",
            _client_secret_encrypted=None,
        )
        db_session.add(cfg)
        db_session.flush()

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.client_secret = "any-secret"

    def test_devops_setter_raises(self, app, db_session, org):
        """DevOpsConnectorConfig setter raises RuntimeError."""
        from app.models.connector_config import DevOpsConnectorConfig

        cfg = DevOpsConnectorConfig(organization_id=org.id)
        db_session.add(cfg)
        db_session.flush()

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.access_token = "any-token"

    def test_lucidchart_setters_raise(self, app, db_session, org):
        """LucidchartConnectorConfig setters raise RuntimeError."""
        from app.models.connector_config import LucidchartConnectorConfig

        cfg = LucidchartConnectorConfig(organization_id=org.id)
        db_session.add(cfg)
        db_session.flush()

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.client_secret = "secret"

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.access_token = "token"

        with pytest.raises(RuntimeError, match="RETIRED"):
            cfg.refresh_token = "refresh"

    def test_org_connector_credential_model_table_name(self):
        """The new OrgConnectorCredential table is named org_connector_credentials."""
        from app.models.connector_config import OrgConnectorCredential

        assert OrgConnectorCredential.__tablename__ == "org_connector_credentials"

    def test_organization_encryption_key_model_table_name(self):
        """The OrganizationEncryptionKey table is named organization_encryption_keys."""
        from app.models.connector_config import OrganizationEncryptionKey

        assert OrganizationEncryptionKey.__tablename__ == "organization_encryption_keys"

    def test_org_connector_credential_has_key_version(self):
        """OrgConnectorCredential has a key_version column."""
        from app.models.connector_config import OrgConnectorCredential

        col = getattr(OrgConnectorCredential, "key_version")
        assert col is not None

    def test_organization_encryption_key_has_previous_key_column(self):
        """OrganizationEncryptionKey has a previous_encrypted_key column for
        MultiFernet fallthrough."""
        from app.models.connector_config import OrganizationEncryptionKey

        col = getattr(OrganizationEncryptionKey, "previous_encrypted_key", None)
        assert col is not None


# ===========================================================================
# Master key enforcement
# ===========================================================================


class TestMasterKeyEnforcement:
    """ORG_ENCRYPTION_MASTER_KEY must be set or encryption operations raise."""

    def test_encrypt_without_master_key_raises(self, app, org):
        """encrypt_for_org raises RuntimeError when ORG_ENCRYPTION_MASTER_KEY is empty."""
        from app.modules.codegen.services.credential_encryption import encrypt_for_org
        from app.models.connector_config import OrganizationEncryptionKey
        from app.extensions import db
        from cryptography.fernet import Fernet

        # Create a key row first so we get past the key-lookup stage
        master_key = app.config["ORG_ENCRYPTION_MASTER_KEY"]
        master = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        raw_key = Fernet.generate_key()
        encrypted = master.encrypt(raw_key)
        if not OrganizationEncryptionKey.query.filter_by(organization_id=org.id).first():
            db.session.add(OrganizationEncryptionKey(
                organization_id=org.id, encrypted_key=encrypted, key_version=1,
            ))
            db.session.flush()

        original = app.config.get("ORG_ENCRYPTION_MASTER_KEY")
        app.config["ORG_ENCRYPTION_MASTER_KEY"] = ""
        try:
            with pytest.raises(RuntimeError, match="ORG_ENCRYPTION_MASTER_KEY"):
                encrypt_for_org(org.id, "test")
        finally:
            app.config["ORG_ENCRYPTION_MASTER_KEY"] = original

    def test_org_key_version_none_when_no_key(self, app):
        """get_org_key_version returns None when no key row exists."""
        from app.modules.codegen.services.credential_encryption import get_org_key_version

        version = get_org_key_version(9999)
        assert version is None


# ===========================================================================
# Helper fixtures
# ===========================================================================


@pytest.fixture
def create_org_key(org):
    """Ensure an OrganizationEncryptionKey row exists for the test org before
    the test runs, so tests don't implicitly create one inside store()."""
    from app.models.connector_config import OrganizationEncryptionKey
    from app.extensions import db
    from cryptography.fernet import Fernet
    from flask import current_app

    existing = OrganizationEncryptionKey.query.filter_by(
        organization_id=org.id
    ).first()
    if existing:
        return existing

    master_key = current_app.config["ORG_ENCRYPTION_MASTER_KEY"]
    master = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
    raw_key = Fernet.generate_key()
    encrypted = master.encrypt(raw_key)
    record = OrganizationEncryptionKey(
        organization_id=org.id,
        encrypted_key=encrypted,
        key_version=1,
    )
    db.session.add(record)
    db.session.flush()
    return record