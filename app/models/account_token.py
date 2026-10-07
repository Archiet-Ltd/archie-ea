"""
AccountToken — the links sent by e-mail to reset a password or confirm an
address. (An invitation's link lives on its ``PendingInvitation`` row, the one
record of an invitation.)

Only a SHA-256 digest of each link's secret is stored; the secret itself exists
in the message and nowhere else. A token works once (``used_at``), stops working
at ``expires_at``, and is withdrawn (``revoked_at``) when a newer link of the
same purpose is issued for the same person, so only the latest one works.

Each row belongs to the organisation of the person it was issued for. The tenant filter scopes every signed-in read to the reader's
organisation; the anonymous reads that redeem a link are scoped by the digest,
which only the holder of the message can produce.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app import db
from app.models.mixins.core import TenantMixin

PURPOSE_PASSWORD_RESET = "password_reset"
PURPOSE_CONFIRM_EMAIL = "confirm_email"

LIFETIMES = {
    PURPOSE_PASSWORD_RESET: timedelta(hours=1),
    PURPOSE_CONFIRM_EMAIL: timedelta(days=7),
}


def _utcnow():
    """Naive UTC, matching how the other timestamp columns are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def digest(raw_token):
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


class AccountToken(TenantMixin, db.Model):  # migration-exempt
    __tablename__ = "account_tokens"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)
    purpose = db.Column(db.String(32), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    # What happened when the message was handed to the mail server:
    # "sent" or "failed" (with the reason in delivery_error).
    delivery_status = db.Column(db.String(20), nullable=True)
    delivery_error = db.Column(db.String(255), nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id], lazy="joined")

    @classmethod
    def issue(cls, user, purpose, *, organization_id=None):
        """Create a token for ``user`` and return ``(row, raw_secret)``.

        Every earlier outstanding token of the same purpose for the same user is
        revoked, so only the newest link works. The caller commits.
        """
        now = _utcnow()
        org_id = organization_id if organization_id is not None else user.organization_id
        db.session.execute(
            update(cls)
            .where(
                cls.user_id == user.id,
                cls.purpose == purpose,
                cls.organization_id == org_id,
                cls.used_at.is_(None),
                cls.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        raw = secrets.token_urlsafe(32)
        row = cls(
            purpose=purpose,
            token_hash=digest(raw),
            user_id=user.id,
            organization_id=org_id,
            created_at=now,
            expires_at=now + LIFETIMES[purpose],
        )
        db.session.add(row)
        db.session.flush()
        return row, raw

    @classmethod
    def find(cls, raw_token, purpose):
        """The row for this secret and purpose, whatever its state, or None."""
        if not raw_token:
            return None
        return cls.query.filter_by(token_hash=digest(raw_token), purpose=purpose).first()

    @classmethod
    def find_usable(cls, raw_token, purpose):
        row = cls.find(raw_token, purpose)
        return row if row is not None and row.is_usable() else None

    @classmethod
    def consume(cls, raw_token, purpose):
        """Mark the token used and return its row, or None if it cannot be used.

        A single conditional UPDATE, so two submissions of the same link race
        safely: exactly one of them gets the row.
        """
        if not raw_token:
            return None
        now = _utcnow()
        token_id = db.session.execute(
            update(cls)
            .where(
                cls.token_hash == digest(raw_token),
                cls.purpose == purpose,
                cls.used_at.is_(None),
                cls.revoked_at.is_(None),
                cls.expires_at > now,
            )
            .values(used_at=now)
            .returning(cls.id)
            .execution_options(synchronize_session=False)
        ).scalar()
        if token_id is None:
            return None
        row = db.session.get(cls, token_id)
        db.session.refresh(row)
        return row

    def is_expired(self, now=None):
        return (now or _utcnow()) >= self.expires_at

    def is_usable(self, now=None):
        return self.used_at is None and self.revoked_at is None and not self.is_expired(now)

    @property
    def state(self):
        if self.used_at is not None:
            return "used"
        if self.revoked_at is not None:
            return "revoked"
        if self.is_expired():
            return "expired"
        return "pending"

    def record_delivery(self, delivered, error=None):
        self.delivery_status = "sent" if delivered else "failed"
        self.delivery_error = None if delivered else (error or "")[:255]
        self.delivered_at = _utcnow() if delivered else None

    def __repr__(self):
        return "<AccountToken {} user={} org={} {}>".format(
            self.purpose, self.user_id, self.organization_id, self.state
        )
