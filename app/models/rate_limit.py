"""Rate limits an administrator can see and set: allow and deny entries, shared counters, violations.

rate_limit_rules       one row per allow or deny entry, for an address (a single address or a range) or for a
                       user. Platform-wide: only the platform administrator writes them.
rate_limit_counters    the limiter's shared counters (one row per key and window), so two workers count one limit.
rate_limit_violations  one row per refused request: address, user, route and the rule that refused it. Kept
                       thirty days. The row carries the organisation of the signed-in user, if any, so an
                       organisation's administrator reads only its own people's refusals.
"""

from datetime import datetime

from app import db
from app.models.mixins.core import TenantMixin

ALLOW = "allow"
DENY = "deny"
ACTIONS = (ALLOW, DENY)
ADDRESS = "address"
USER = "user"
KINDS = (ADDRESS, USER)

VIOLATION_RETENTION_DAYS = 30


class RateLimitRule(db.Model):
    __tablename__ = "rate_limit_rules"
    __table_args__ = (
        db.UniqueConstraint("action", "kind", "value", name="uq_rate_limit_rules_entry"),
    )

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(8), nullable=False)  # allow | deny
    kind = db.Column(db.String(8), nullable=False)  # address | user
    value = db.Column(db.String(64), nullable=False)  # "203.0.113.7", "203.0.113.0/24" or a user id
    note = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class RateLimitCounter(db.Model):
    __tablename__ = "rate_limit_counters"

    key = db.Column(db.String(255), primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.Float, nullable=False)  # epoch seconds


class RateLimitViolation(TenantMixin, db.Model):
    __tablename__ = "rate_limit_violations"

    id = db.Column(db.Integer, primary_key=True)
    # Nullable on purpose: a refusal of someone who is not signed in belongs to no organisation and is
    # visible to the platform administrator only (the tenant policy never admits a NULL organisation).
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    address = db.Column(db.String(64), index=True)
    user_id = db.Column(db.Integer, index=True)
    route = db.Column(db.String(255))
    rule = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
