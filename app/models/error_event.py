"""ErrorEvent — aggregated server + client error telemetry.

Entelim had zero error-tracking of any kind before this: a WARNING/ERROR log
line went to stdout and nowhere else, and a JS exception in a user's browser
was invisible unless that user reported it. Silent degradation (a route
raising for every caller, a JS bug breaking one persona's page) had no signal
except the owner or a customer noticing.

This is not tenant-scoped data (``TenantMixin`` is deliberately not used): an
error is an operational fact about the platform, and a platform admin needs
to see every organisation's errors to tell "one customer hit a bug" from
"the deploy just broke everything". ``organization_id``/``user_id`` are kept
as plain nullable columns so a report can still be *attributed* without being
*filtered* — the admin route restricts to platform admins instead.

Deduplication is by ``fingerprint`` (a hash of source + logger/location +
normalised message) rather than storing one row per occurrence, matching how
every real error tracker (Sentry, GlitchTip, Rollbar) works: a single bug
firing 10,000 times must read as one row with a count, not flood the table.
"""

from datetime import datetime

from app import db


class ErrorEvent(db.Model):  # migration-exempt
    """One deduplicated error/exception, aggregated across occurrences."""

    __tablename__ = "error_events"

    id = db.Column(db.Integer, primary_key=True)

    fingerprint = db.Column(db.String(64), nullable=False, index=True)
    source = db.Column(db.String(16), nullable=False)  # 'server' | 'client'
    level = db.Column(db.String(16), nullable=False, default="ERROR")

    message = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(500), nullable=True)  # logger/module:line or file:line:col
    stack = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(1000), nullable=True)  # request path / page URL the error occurred on
    user_agent = db.Column(db.String(500), nullable=True)

    organization_id = db.Column(db.Integer, nullable=True, index=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)

    occurrence_count = db.Column(db.Integer, nullable=False, default=1)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    resolved = db.Column(db.Boolean, nullable=False, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index("ix_error_events_fingerprint_resolved", "fingerprint", "resolved"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "level": self.level,
            "message": self.message,
            "location": self.location,
            "url": self.url,
            "organization_id": self.organization_id,
            "user_id": self.user_id,
            "occurrence_count": self.occurrence_count,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "resolved": self.resolved,
        }
