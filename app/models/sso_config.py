"""
SSOConfig model — per-organisation SSO federation config (COM-005).

One record per organisation. Stores OIDC client credentials (encrypted)
and SAML IdP metadata URL. Email-domain matching routes login to IdP.
"""

import logging
import os

from app import db

logger = logging.getLogger(__name__)


class SSOConfig(db.Model):  # migration-exempt
    """Per-organisation SSO configuration for SAML 2.0 / OIDC federation."""

    __tablename__ = "sso_configs"

    id = db.Column(db.Integer, primary_key=True)
    # One SSO config per organisation
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    # 'saml' or 'oidc'
    protocol = db.Column(db.String(10), nullable=False, default="oidc")
    # SAML: IdP metadata URL; OIDC: OpenID Connect discovery document URL
    idp_metadata_url = db.Column(db.String(500))
    # OIDC client credentials
    client_id = db.Column(db.String(255))
    # Encrypted with Fernet; use the client_secret property for access
    _client_secret_encrypted = db.Column("client_secret_encrypted", db.String(1000))
    # JSON map of IdP claim names → platform attribute names (optional override)
    attribute_mapping = db.Column(db.JSON, default=dict)
    # Comma-separated email domains that trigger this config, e.g. "acme.com,acme.co.uk"
    email_domain = db.Column(db.String(500))
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    # -- SAML 2.0 (R1-B12 PR 2, TB-0141) --------------------------------
    # The IdP's SSO redirect-binding endpoint (where an AuthnRequest is sent).
    idp_sso_url = db.Column(db.String(500))
    # The IdP's signing certificate, PEM-encoded, used to verify the
    # <Response>/<Assertion> signature. Never trust a certificate carried
    # inside the response itself (that is what an attacker would forge) --
    # this column is the operator-entered, out-of-band trust anchor.
    idp_x509_cert = db.Column(db.Text)
    # This organisation's SAML entity id as the Service Provider. Nullable:
    # defaults to the platform's base URL + "/auth/sso/metadata/<org_id>"
    # when unset (see SSOConfig.sp_entity_id_or_default).
    sp_entity_id = db.Column(db.String(500))

    # -- Test sign-in mode (R1-B12 PR 2, TB-0142/PB-0129) ---------------
    # An administrator tests a draft config before it is enforced. The run
    # authenticates against the real IdP but never calls provision_user --
    # it only proves the flow and the claims it would produce. Nullable JSON:
    # {"status": "success"|"failure", "message": str, "tested_at": iso8601,
    #  "claims_preview": {...}}.
    last_test_result = db.Column(db.JSON)

    organization = db.relationship(
        "Organization", backref=db.backref("sso_config", uselist=False)
    )

    __table_args__ = (
        # Two enabled configs may not hold the same domain string. Overlap
        # between differently-written lists is refused by the save route.
        db.Index(
            "uq_sso_configs_enabled_email_domain",
            db.text("lower(btrim(email_domain))"),
            unique=True,
            postgresql_where=db.text("enabled AND email_domain IS NOT NULL AND btrim(email_domain) <> ''"),
        ),
    )

    # ------------------------------------------------------------------
    # Fernet-encrypted client_secret property
    # ------------------------------------------------------------------

    @property
    def client_secret(self):
        """Decrypt and return the OIDC client secret."""
        if not self._client_secret_encrypted:
            return None
        key = os.environ.get("FERNET_KEY")
        if not key:
            # No encryption key — value was stored as plaintext
            return self._client_secret_encrypted
        try:
            from cryptography.fernet import Fernet

            f = Fernet(key.encode())
            return f.decrypt(self._client_secret_encrypted.encode()).decode()
        except Exception as exc:
            logger.error("Failed to decrypt client_secret: %s", exc)
            return self._client_secret_encrypted

    @client_secret.setter
    def client_secret(self, value):
        """Encrypt and store the OIDC client secret."""
        if not value:
            self._client_secret_encrypted = None
            return
        key = os.environ.get("FERNET_KEY")
        if not key:
            logger.warning(
                "FERNET_KEY not set; storing SSO client_secret as plaintext. "
                "Set FERNET_KEY for production use."
            )
            self._client_secret_encrypted = value
            return
        try:
            from cryptography.fernet import Fernet

            f = Fernet(key.encode())
            self._client_secret_encrypted = f.encrypt(value.encode()).decode()
        except Exception as exc:
            logger.error(
                "Failed to encrypt client_secret (storing plaintext): %s", exc
            )
            self._client_secret_encrypted = value

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def email_domains(self):
        """Return list of configured email domains (supports comma-separated)."""
        if not self.email_domain:
            return []
        return [d.strip().lower() for d in self.email_domain.split(",") if d.strip()]

    def sp_entity_id_or_default(self, base_url):
        """This organisation's SAML SP entity id, falling back to a URL
        derived from the platform's own base URL when none was entered."""
        if self.sp_entity_id:
            return self.sp_entity_id
        return f"{base_url.rstrip('/')}/auth/sso/metadata/{self.organization_id}"

    def __repr__(self):
        return (
            f"<SSOConfig org={self.organization_id} protocol={self.protocol} "
            f"enabled={self.enabled}>"
        )
