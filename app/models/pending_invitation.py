"""
PendingInvitation — the one record of an invitation to join an organisation
(COM-007, ADR 0008).

Every invitation an organisation has sent and nobody has taken up is a row
here, whether the invitee already has an account or not. Nothing is granted
while the row exists; it is removed when the invitation is accepted (the
OrgRole is created), declined or withdrawn.

The e-mailed link's secret is stored only as a SHA-256 digest
(``token_hash``). Sending a new link replaces the digest, so only the newest
link works, and taking an invitation up deletes the row in one conditional
statement, so a link works once.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from app import db
from app.models.org_role import VALID_ORG_ROLES


def _utcnow():
    """Naive UTC, matching how the other timestamp columns are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def link_digest(raw_token):
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


class PendingInvitation(db.Model):  # migration-exempt
    """Stores an invitation for an existing user to join an organisation.

    The invitation must be accepted before the user gains any role or membership
    in the target organisation. Duplicate invitations for the same
    (organisation, user) pair are refused while the first is still valid. An
    invitation expires ``LIFETIME`` after it was created; an expired one can no
    longer be accepted, and inviting the same person again renews it.
    """

    LIFETIME = timedelta(days=14)

    __tablename__ = "pending_invitations"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "user_id", name="uq_pending_invite_org_user"
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    role = db.Column(db.String(50), nullable=False, default="viewer")
    invited_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    # Digest of the secret in the newest e-mailed link; None when no link was sent.
    token_hash = db.Column(db.String(64), nullable=True, index=True)
    # What happened when that link was handed to the mail server:
    # "sent" or "failed" (with the reason in delivery_error).
    delivery_status = db.Column(db.String(20), nullable=True)
    delivery_error = db.Column(db.String(255), nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id], lazy="joined")
    inviter = db.relationship("User", foreign_keys=[invited_by], lazy="select")

    @property
    def effective_expiry(self):
        """When this invitation stops being valid.

        Rows created before ``expires_at`` existed have no value; they expire
        ``LIFETIME`` after they were created.
        """
        if self.expires_at is not None:
            return self.expires_at
        return (self.created_at or _utcnow()) + self.LIFETIME

    def is_expired(self, now=None):
        return (now or _utcnow()) >= self.effective_expiry

    @property
    def state(self):
        return "expired" if self.is_expired() else "pending"

    def issue_link(self):
        """Give this invitation a new link and return its secret.

        The earlier link stops working, and the invitation runs for another
        ``LIFETIME`` from now. The caller commits.
        """
        raw = secrets.token_urlsafe(32)
        now = _utcnow()
        self.token_hash = link_digest(raw)
        self.created_at = now
        self.expires_at = now + self.LIFETIME
        self.delivery_status = None
        self.delivery_error = None
        self.delivered_at = None
        db.session.flush()
        return raw

    def record_delivery(self, delivered, error=None):
        self.delivery_status = "sent" if delivered else "failed"
        self.delivery_error = None if delivered else (error or "")[:255]
        self.delivered_at = _utcnow() if delivered else None

    @classmethod
    def find_by_link(cls, raw_token):
        """The open invitation behind an e-mailed link, or None."""
        if not raw_token:
            return None
        # tenant-scoping-ok: an anonymous link redemption has no organisation yet;
        # the digest, which only the holder of the message can produce, scopes the read
        row = cls.query.filter_by(token_hash=link_digest(raw_token)).first()
        if row is None or row.is_expired():
            return None
        return row

    @classmethod
    def take_up(cls, raw_token):
        """Remove the open invitation behind a link and return what it offered.

        One conditional DELETE, so two submissions of the same link race
        safely: exactly one of them gets the row. Returns a dict of the
        row's organization_id, user_id, role and invited_by, or None.
        """
        if not raw_token:
            return None
        row = db.session.execute(
            delete(cls)
            .where(cls.token_hash == link_digest(raw_token), cls.expires_at > _utcnow())
            .returning(cls.organization_id, cls.user_id, cls.role, cls.invited_by)
            .execution_options(synchronize_session=False)
        ).first()
        if row is None:
            return None
        return dict(row._mapping)

    @classmethod
    def create_for(cls, org_id, user_id, role, invited_by_id=None):
        """Create a pending invitation. Returns (invitation, created: bool).

        If a valid pending invitation already exists for this org+user, returns
        the existing row and created=False. An expired one is renewed (new
        role, inviter and expiry) and returned with created=True. Two requests
        racing to create the same invitation are safe: the loser gets the
        winner's row and created=False instead of an error.
        """
        if role not in VALID_ORG_ROLES:
            raise ValueError(
                f"Invalid role '{role}'. Must be one of {VALID_ORG_ROLES}"
            )
        existing = cls.find_one_or_none(org_id, user_id)
        if existing is not None:
            if not existing.is_expired():
                return existing, False
            now = _utcnow()
            existing.role = role
            existing.invited_by = invited_by_id
            existing.created_at = now
            existing.expires_at = now + cls.LIFETIME
            # The expired invitation's link must not come back to life.
            existing.token_hash = None
            existing.delivery_status = None
            existing.delivery_error = None
            existing.delivered_at = None
            db.session.flush()
            return existing, True
        invitation = cls(
            organization_id=org_id,
            user_id=user_id,
            role=role,
            invited_by=invited_by_id,
            expires_at=_utcnow() + cls.LIFETIME,
        )
        try:
            with db.session.begin_nested():
                db.session.add(invitation)
                db.session.flush()
        except IntegrityError:
            winner = cls.find_one_or_none(org_id, user_id)
            if winner is None:
                raise
            return winner, False
        return invitation, True

    @classmethod
    def find_for_user(cls, user_id):
        """Return the pending, unexpired invitations for a user."""
        rows = cls.query.filter_by(user_id=user_id).order_by(cls.created_at).all()
        return [row for row in rows if not row.is_expired()]

    @classmethod
    def find_one_or_none(cls, org_id, user_id):
        """Return the pending invitation for (org, user) or None."""
        return cls.query.filter_by(
            organization_id=org_id, user_id=user_id
        ).first()

    def __repr__(self):
        return (
            f"<PendingInvitation org={self.organization_id} "
            f"user={self.user_id} role={self.role}>"
        )
