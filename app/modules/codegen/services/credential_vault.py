"""Encrypted credential storage for connector secrets.

Reuses phase 1's credential_encryption.py Fernet pattern.

``CredentialVault`` (existing): stores credentials in ``codegen_connector_credentials``
table encrypted with the shared ``CREDENTIAL_ENCRYPTION_KEY``.

``OrgCredentialVault``: stores credentials in
``org_connector_credentials`` table encrypted per-organisation with that org's
own Fernet key. Never returns secrets after entry; retrieves only for sync
operations and returns a masked representation on read.
"""

import json
import logging
from datetime import datetime

from cryptography.fernet import InvalidToken
from app.extensions import db
from app.modules.codegen.services.credential_encryption import (
    CredentialUnreadable,
    decrypt_credential,
    encrypt_credential,
    encrypt_for_org,
    decrypt_for_org,
    rotate_org_encryption_key,
    get_org_key_version,
)

logger = logging.getLogger(__name__)


class ConnectorCredential(db.Model):
    """Encrypted credential storage. migration-exempt — db.create_all()"""
    __tablename__ = "codegen_connector_credentials"

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(db.Integer, nullable=False, index=True)
    connector_type = db.Column(db.String(50), nullable=False)
    encrypted_data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("solution_id", "connector_type", name="uq_solution_connector_cred"),)


class CredentialVault:
    """Encrypted CRUD for connector credentials."""

    def store(self, solution_id: int, connector_type: str, credentials: dict) -> None:
        """Store credentials encrypted. Upserts if already exists."""
        encrypted = encrypt_credential(json.dumps(credentials))
        existing = ConnectorCredential.query.filter_by(
            solution_id=solution_id, connector_type=connector_type
        ).first()
        if existing:
            existing.encrypted_data = encrypted
            existing.updated_at = datetime.utcnow()
        else:
            db.session.add(ConnectorCredential(
                solution_id=solution_id,
                connector_type=connector_type,
                encrypted_data=encrypted,
            ))
        db.session.flush()

    def retrieve(self, solution_id: int, connector_type: str) -> dict | None:
        """Retrieve and decrypt credentials. Returns None if not found."""
        cred = ConnectorCredential.query.filter_by(
            solution_id=solution_id, connector_type=connector_type
        ).first()
        if not cred:
            return None
        decrypted = decrypt_credential(cred.encrypted_data)
        return json.loads(decrypted) if decrypted else None

    def delete(self, solution_id: int, connector_type: str) -> None:
        """Delete credentials for a connector."""
        ConnectorCredential.query.filter_by(
            solution_id=solution_id, connector_type=connector_type
        ).delete()
        db.session.flush()


# ---------------------------------------------------------------------------
# Per-organisation credential vault
# ---------------------------------------------------------------------------

def _resolve_org_id(org_id: int | None) -> int:
    """Resolve the effective organisation id.

    Inside a request context (``g.current_org_id`` is set), the passed
    ``org_id`` must match. Outside a request (CLI/jobs), the passed
    ``org_id`` is required.
    """
    from flask import g
    from flask import has_app_context

    if not has_app_context():
        if org_id is None:
            raise ValueError(
                "org_id is required outside a request context"
            ) from None
        return org_id

    try:
        current = g.current_org_id
    except (AttributeError, RuntimeError):
        current = None

    if current is not None:
        if org_id is not None and org_id != current:
            raise ValueError(
                f"Passed org_id ({org_id}) does not match current request "
                f"organisation ({current}). Derive from tenant context."
            )
        return current

    # No current_org_id set (not in a tenant-aware request)
    if org_id is None:
        raise ValueError(
            "org_id is required when no tenant context is active"
        )
    return org_id


def _now() -> datetime:
    return datetime.utcnow()


class OrgCredentialVault:
    """Per-organisation credential store.

    Credentials are encrypted with the organisation's own Fernet key and
    stored in ``org_connector_credentials``. Secrets are never returned after
    entry — ``store`` accepts plaintext, ``retrieve`` returns decrypted data
    only for authorised internal callers, and ``get_masked`` never decrypts
    the value (returns only metadata).
    """

    def _require_org_id(self, org_id: int | None) -> int:
        """Validate and return the effective org id."""
        return _resolve_org_id(org_id)

    def _get_or_create_key(self, org_id: int) -> tuple:
        """Ensure an encryption key exists for this org, return (fernet, version)."""
        from app.modules.codegen.services.credential_encryption import (
            _get_org_fernet, _create_org_fernet,
        )

        version = get_org_key_version(org_id)
        if version is None:
            return _create_org_fernet(org_id)
        return _get_org_fernet(org_id), version

    def store(
        self,
        org_id: int | None = None,
        connector_type: str = "",
        credential_type: str = "credentials",
        value: str = "",
    ) -> None:
        """Store a credential value encrypted with the organisation's key.

        Upserts if a row with the same (org_id, connector_type, credential_type)
        already exists.

        ``org_id`` may be omitted inside a tenant-aware request context.
        """
        from app.models.connector_config import OrgConnectorCredential

        resolved = self._require_org_id(org_id)
        # Ensure a key row exists before encrypting
        self._get_or_create_key(resolved)
        encrypted = encrypt_for_org(resolved, value)
        existing = OrgConnectorCredential.query.filter_by(
            organization_id=resolved,
            connector_type=connector_type,
            credential_type=credential_type,
        ).first()
        if existing:
            existing.encrypted_value = encrypted
            existing.updated_at = _now()
        else:
            version = get_org_key_version(resolved)
            db.session.add(OrgConnectorCredential(
                organization_id=resolved,
                connector_type=connector_type,
                credential_type=credential_type,
                encrypted_value=encrypted,
                key_version=version or 1,
            ))
        db.session.flush()

    def store_credentials(
        self,
        org_id: int | None = None,
        connector_type: str = "",
        credentials: dict | None = None,
    ) -> None:
        """Store multiple credential fields as a JSON blob under
        credential_type='credentials'."""
        self.store(org_id, connector_type, "credentials", json.dumps(credentials or {}))

    def retrieve(
        self,
        org_id: int | None = None,
        connector_type: str = "",
        credential_type: str = "credentials",
    ) -> str | None:
        """Retrieve and decrypt a credential. Returns the plaintext value
        (caller must ensure authorised use). Returns None if not found.
        Raises ``CredentialUnreadable`` if the stored value cannot be decrypted."""
        from app.models.connector_config import OrgConnectorCredential

        resolved = self._require_org_id(org_id)
        cred = OrgConnectorCredential.query.filter_by(
            organization_id=resolved,
            connector_type=connector_type,
            credential_type=credential_type,
        ).first()
        if not cred:
            return None
        return decrypt_for_org(resolved, cred.encrypted_value,
                               key_version=cred.key_version)

    def retrieve_credentials(
        self,
        org_id: int | None = None,
        connector_type: str = "",
    ) -> dict | None:
        """Retrieve and decrypt the full credentials JSON blob."""
        raw = self.retrieve(org_id, connector_type, "credentials")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Failed to parse credentials JSON for org %s connector %s",
                org_id,
                connector_type,
            )
            return None

    def get_masked(
        self,
        org_id: int | None = None,
        connector_type: str = "",
        credential_type: str = "credentials",
    ) -> str | None:
        """Return a masked representation that never contains plaintext.

        Returns ``"******"`` when a credential exists (proving it is set),
        or ``None`` when no credential is stored. Never decrypts the value,
        so even a short secret cannot leak.
        """
        from app.models.connector_config import OrgConnectorCredential

        resolved = self._require_org_id(org_id)
        cred = OrgConnectorCredential.query.filter_by(
            organization_id=resolved,
            connector_type=connector_type,
            credential_type=credential_type,
        ).first()
        if cred is None:
            return None
        return "******"

    def delete(
        self,
        org_id: int | None = None,
        connector_type: str = "",
        credential_type: str = "credentials",
    ) -> None:
        """Delete credentials for a connector."""
        from app.models.connector_config import OrgConnectorCredential

        resolved = self._require_org_id(org_id)
        OrgConnectorCredential.query.filter_by(
            organization_id=resolved,
            connector_type=connector_type,
            credential_type=credential_type,
        ).delete()
        db.session.flush()

    def rotate_all_credentials(self, org_id: int) -> int:
        """Rotate the organisation's encryption key and re-encrypt every
        credential row.

        Returns the new ``key_version``.

        Uses ``MultiFernet([new, old])`` so that any process still holding
        the old key can continue reading while rows are re-encrypted.
        Takes ``SELECT ... FOR UPDATE`` on the key row to prevent races.
        """
        from app.models.connector_config import OrgConnectorCredential, OrganizationEncryptionKey
        from app.modules.codegen.services.credential_encryption import _get_org_fernet

        resolved = self._require_org_id(org_id)

        # Take a FOR UPDATE lock on the key row
        key_row = OrganizationEncryptionKey.query.filter_by(
            organization_id=resolved
        ).with_for_update().first()
        if key_row is None:
            raise RuntimeError(f"No encryption key found for org {resolved}")

        # Get old Fernet before rotation (still current)
        old_fernet = _get_org_fernet(resolved)

        # Rotate — returns (new_fernet, old_fernet_from_db, new_version)
        new_fernet, previous_fernet, new_version = rotate_org_encryption_key(resolved)

        # Re-encrypt every row
        rows = OrgConnectorCredential.query.filter_by(
            organization_id=resolved
        ).all()
        for row in rows:
            if row.encrypted_value:
                try:
                    plaintext = old_fernet.decrypt(row.encrypted_value).decode("utf-8")
                except InvalidToken:
                    raise CredentialUnreadable(
                        f"Credential row {row.id} for org {resolved} "
                        "cannot be decrypted with current key — data may be corrupted."
                    ) from None
                row.encrypted_value = new_fernet.encrypt(plaintext.encode("utf-8"))
                row.key_version = new_version

        db.session.commit()
        return new_version