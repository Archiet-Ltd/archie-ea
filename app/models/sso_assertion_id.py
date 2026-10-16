"""SAML assertion ids already used to sign someone in.

A signed assertion is bearer proof of who someone is until it expires, so one that has been
accepted once must never be accepted again. Each accepted assertion's id is written here,
unique per organisation, and kept until its own expiry. The table is read and written during
the sign-in callback, before anyone is signed in and so before there is a session
organisation; it is keyed by the organisation the callback already names.
"""

from datetime import datetime

from app import db


class SSOAssertionId(db.Model):  # migration-exempt
    __tablename__ = "sso_assertion_ids"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "assertion_id", name="uq_sso_assertion_org_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assertion_id = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
