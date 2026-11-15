"""SignInIdentity: a Google or Microsoft account connected to a person.

One row says "this provider's subject is this person". The row belongs to the
person, not to an organisation: it is read before any organisation is known,
when someone chooses Continue with Google or Continue with Microsoft.

What is kept is the provider's own name for the person (the subject), the
issuer that vouched for it, and the address that was verified when the
account was connected. No access token, id_token or refresh token is kept,
and no profile picture.
"""

from datetime import datetime, timezone

from app import db

PROVIDERS = ("google", "microsoft")


def _utcnow():
    return datetime.now(timezone.utc)


class SignInIdentity(db.Model):  # migration-exempt
    __tablename__ = "sign_in_identities"
    __table_args__ = (
        db.UniqueConstraint("provider", "subject", name="uq_sign_in_identities_provider_subject"),
        db.UniqueConstraint("user_id", "provider", name="uq_sign_in_identities_user_provider"),
        db.CheckConstraint("provider IN ('google', 'microsoft')", name="ck_sign_in_identities_provider"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = db.Column(db.String(32), nullable=False)
    issuer = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    email_at_link = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    user = db.relationship("User", backref=db.backref("sign_in_identities", cascade="all, delete-orphan", passive_deletes=True))
