"""Fernet symmetric encryption for credentials stored in Entelim's database.

Used by:
- SolutionInstance.database_url_encrypted (Phase 1)
- CredentialVault for connector secrets (Phase 3b)
- OrgCredentialVault for per-organisation encrypted credentials

Single-key functions use ``CREDENTIAL_ENCRYPTION_KEY`` from app config.
Per-organisation functions use ``ORG_ENCRYPTION_MASTER_KEY`` from app config to
unwrap each organisation's Fernet key stored in ``OrganizationEncryptionKey``.
"""
import logging

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)


class CredentialUnreadable(Exception):
    """Raised when a stored credential cannot be decrypted (wrong key,
    corrupted ciphertext, or missing key row). Never silently returns None."""


def _get_fernet() -> Fernet:
    """Build Fernet instance from app config key."""
    key = current_app.config.get("CREDENTIAL_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY not set in app config. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as exc:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt_credential(plaintext: str) -> bytes:
    """Encrypt a credential string. Returns Fernet token as bytes."""
    if not plaintext:
        return b""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt_credential(ciphertext: bytes | None) -> str | None:
    """Decrypt a Fernet token back to string. Returns None if input is empty/None."""
    if not ciphertext:
        return None
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt credential — invalid token or wrong key")
        return None


# ---------------------------------------------------------------------------
# Per-organisation encryption
# ---------------------------------------------------------------------------

_CHAR_VS = "v"  # prefix for version-based key lookup


def _get_master_fernet() -> Fernet:
    """Build Fernet from the per-org master key."""
    key = current_app.config.get("ORG_ENCRYPTION_MASTER_KEY")
    if not key:
        raise RuntimeError(
            "ORG_ENCRYPTION_MASTER_KEY not set in app config. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    if isinstance(key, str):
        key = key.encode()
    try:
        return Fernet(key)
    except Exception as exc:
        raise RuntimeError(
            "ORG_ENCRYPTION_MASTER_KEY is not a valid Fernet key. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def _get_org_fernet(org_id: int, key_version: int | None = None) -> Fernet:
    """Return the Fernet instance for an organisation, reading the key row
    from the database (no process-global cache).

    When ``key_version`` is provided, loads the version-specific key:
    - If it matches the current version, returns the current key.
    - If it matches the previous version, returns the previous key.
    - Otherwise raises ``CredentialUnreadable``.

    When ``key_version`` is None, returns the current (latest) key.
    """
    from app.models.connector_config import OrganizationEncryptionKey

    record = OrganizationEncryptionKey.query.filter_by(
        organization_id=org_id
    ).first()
    if record is None:
        raise CredentialUnreadable(
            f"No encryption key found for organisation {org_id}. "
            "Run rotate-credential-key to create one."
        )

    master = _get_master_fernet()

    if key_version is not None and key_version != record.key_version:
        # Requested a version that is not current — try the previous key
        if (record.previous_encrypted_key is not None
                and key_version == record.key_version - 1):
            raw_key = master.decrypt(record.previous_encrypted_key)
            return Fernet(raw_key)
        raise CredentialUnreadable(
            f"Credential for org {org_id} uses key version {key_version} "
            f"but only version {record.key_version} (current) and "
            f"{record.key_version - 1} (previous) are available."
        )

    raw_key = master.decrypt(record.encrypted_key)
    return Fernet(raw_key)


def _create_org_fernet(org_id: int) -> tuple[Fernet, int]:
    """Create a new Fernet key for an organisation, returning (fernet, version).

    This is deliberately separated from ``_get_org_fernet`` so that key
    creation can be done inside a transaction that may be rolled back.
    """
    from app.models.connector_config import OrganizationEncryptionKey
    from app.extensions import db

    master = _get_master_fernet()
    raw_key = Fernet.generate_key()
    encrypted = master.encrypt(raw_key)

    record = OrganizationEncryptionKey(
        organization_id=org_id,
        encrypted_key=encrypted,
        key_version=1,
    )
    db.session.add(record)
    db.session.flush()
    return Fernet(raw_key), 1


def encrypt_for_org(org_id: int, plaintext: str) -> bytes:
    """Encrypt plaintext with the organisation's current Fernet key."""
    if not plaintext:
        return b""
    f = _get_org_fernet(org_id)
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt_for_org(org_id: int, ciphertext: bytes | None,
                    key_version: int | None = None) -> str | None:
    """Decrypt a token with the organisation's Fernet key.

    When ``key_version`` is provided, the version-specific key is loaded
    (either current or previous, for MultiFernet fallthrough).

    Returns None only when ``ciphertext`` is empty/None. Raises
    ``CredentialUnreadable`` when decryption fails, so callers can
    distinguish \"no credential stored\" from \"credential unreadable\".
    """
    if not ciphertext:
        return None
    f = _get_org_fernet(org_id, key_version=key_version)
    try:
        return f.decrypt(ciphertext).decode("utf-8")
    except InvalidToken:
        logger.error(
            "Failed to decrypt org credential for org %s — invalid token or wrong key",
            org_id,
        )
        raise CredentialUnreadable(
            f"Credential for org {org_id} is unreadable: "
            "the encryption key may have been rotated or the data is corrupted."
        ) from None


def get_org_key_version(org_id: int) -> int | None:
    """Return the current ``key_version`` for an organisation, or None if
    no key row exists yet."""
    from app.models.connector_config import OrganizationEncryptionKey

    record = OrganizationEncryptionKey.query.filter_by(
        organization_id=org_id
    ).first()
    if record is None:
        return None
    return record.key_version


def rotate_org_encryption_key(org_id: int) -> tuple[Fernet, Fernet | None, int]:
    """Rotate the encryption key for one organisation.

    Generates a new Fernet key, stores it encrypted under the master key,
    sets ``previous_encrypted_key`` to the old key, and increments
    ``key_version``. Takes ``SELECT ... FOR UPDATE`` on the key row to
    prevent concurrent rotation.

    Returns ``(new_fernet, old_fernet, new_key_version)`` so the caller can
    re-encrypt with ``new_fernet`` while readers in any process can still
    decrypt with the old key via ``_get_org_fernet(org_id, old_version)``.

    The caller is responsible for committing the transaction.
    """
    from app.models.connector_config import OrganizationEncryptionKey
    from app.extensions import db

    master = _get_master_fernet()

    # Lock the key row so two rotations cannot race
    record = OrganizationEncryptionKey.query.filter_by(
        organization_id=org_id
    ).with_for_update().first()

    raw_new = Fernet.generate_key()
    encrypted_new = master.encrypt(raw_new)

    if record is None:
        record = OrganizationEncryptionKey(
            organization_id=org_id,
            encrypted_key=encrypted_new,
            key_version=1,
        )
        db.session.add(record)
        db.session.flush()
        old_fernet = None
        new_version = 1
    else:
        # Move current key to previous, then set new key
        record.previous_encrypted_key = record.encrypted_key
        record.encrypted_key = encrypted_new
        record.key_version = (record.key_version or 0) + 1
        db.session.flush()
        new_version = record.key_version
        old_raw = master.decrypt(record.previous_encrypted_key)
        old_fernet = Fernet(old_raw)

    new_fernet = Fernet(raw_new)
    return new_fernet, old_fernet, new_version