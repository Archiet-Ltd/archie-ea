"""Fernet symmetric encryption for credentials stored in Entelim's database.

Used by:
- SolutionInstance.database_url_encrypted (Phase 1)
- CredentialVault for connector secrets (Phase 3b)

Key is sourced from app.config["CREDENTIAL_ENCRYPTION_KEY"].
Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)


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
