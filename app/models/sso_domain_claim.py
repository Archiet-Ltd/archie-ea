"""An organisation's claim on an email domain for its single sign-on, and the proof of it.

Listing a domain in an SSO configuration only says the organisation wants it. The domain
routes sign-ins to that organisation's identity provider only once the organisation has shown
it controls the domain by publishing a one-time token in a DNS TXT record. At most one
organisation can hold a verified claim on a domain: the database enforces it with a partial
unique index. The table is read before anyone is signed in (the sign-in form looks a domain
up), so it is keyed by domain, not fenced to a session organisation.
"""

from datetime import datetime

from app import db


class SSODomainClaim(db.Model):  # migration-exempt
    __tablename__ = "sso_domain_claims"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "domain", name="uq_sso_domain_claim_org_domain"),
        db.Index(
            "uq_sso_domain_claim_verified_domain",
            "domain",
            unique=True,
            postgresql_where=db.text("verified_at IS NOT NULL"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain = db.Column(db.String(255), nullable=False, index=True)
    token = db.Column(db.String(64), nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None
