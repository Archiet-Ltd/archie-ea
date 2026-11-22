"""Verifier API key — the scoped inbound credential for the Ifiok verifier.

This is the ONLY header-presented credential in the application; every other
route authenticates with a Flask-Login session cookie. A verifier key authorises
writing *Proposed* requirements, task links and verdicts into ONE organisation
only. The key is bound to that organisation at issue time and can never reach
another. See app/auth/api_key.py for the guard and the ENT-V-1..8 behaviour.

The key is deliberately NOT a TenantMixin: an authentication credential must be
resolvable *before* any tenant context exists, so it must never be hidden by the
tenant read filter. It carries an explicit organization_id instead.

The plaintext secret is shown once at issue and never stored — only its SHA-256.
"""

import hashlib
import secrets

from app.extensions import db
from app.models.mixins.core import TimestampMixin


class VerifierApiKey(db.Model, TimestampMixin):
    __tablename__ = "verifier_api_keys"

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(120), nullable=False)

    # SHA-256 of the plaintext secret; the secret itself is never stored.
    key_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # Space-separated scopes. The only scope today is "verifier:write".
    scopes = db.Column(db.String(255), nullable=False, default="verifier:write")

    # Explicit org binding (not TenantMixin — see module docstring).
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    revoked_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)

    @staticmethod
    def hash_secret(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, organization_id: int, label: str, scopes: str = "verifier:write"):
        """Create a key bound to one organisation.

        Returns (model, plaintext_secret). The caller persists the model and must
        show the secret to a person exactly once, then discard it — it cannot be
        recovered from the stored hash.
        """
        secret = "evk_" + secrets.token_urlsafe(40)
        key = cls(
            label=label,
            key_hash=cls.hash_secret(secret),
            scopes=scopes or "verifier:write",
            organization_id=organization_id,
        )
        return key, secret

    @property
    def scope_list(self):
        return (self.scopes or "").split()

    def has_scope(self, scope: str) -> bool:
        return scope in self.scope_list

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self):
        state = "active" if self.is_active else "revoked"
        return f"<VerifierApiKey {self.id} org={self.organization_id} {state}>"
