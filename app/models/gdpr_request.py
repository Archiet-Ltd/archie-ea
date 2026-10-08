from datetime import datetime

from app.extensions import db
from app.models.mixins.core import TenantMixin

# A data-subject request about someone who holds no account on this platform
# (a customer or employee whose records sit in the organisation's own systems).
# ``user_id`` is NOT NULL on databases that predate this value, and relaxing it
# is a schema change reconcile-schema cannot make, so the absence of a platform
# account is recorded as 0 (no ``users`` row has id 0) and ``subject_reference``
# carries who the request is about.
NO_PLATFORM_ACCOUNT = 0

# Request types a Data Protection Officer can record. ``export`` and ``delete``
# are the older self-service/platform names for access and erasure and are kept
# so rows written before this list existed still read correctly.
REQUEST_TYPES = {
    "access": "Access",
    "erasure": "Erasure",
    "rectification": "Rectification",
    "restriction": "Restriction of processing",
    "portability": "Portability",
    "objection": "Objection",
}
LEGACY_REQUEST_TYPES = {"export": "access", "delete": "erasure"}


class GDPRRequest(TenantMixin, db.Model):
    """One data-subject request and what was done about it.

    Organisation-scoped (TenantMixin): the scope, the search assignments and
    the erasure evidence all belong to the organisation the request was made
    in, and no other organisation can read or act on them.
    """

    __tablename__ = "gdpr_requests"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    request_type = db.Column(db.String(16), nullable=False)  # see REQUEST_TYPES
    status = db.Column(db.String(16), nullable=False, default="pending")  # pending, reviewed, completed, failed
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    # Who the request is about when they hold no platform account, and who
    # recorded it. Both nullable so reconcile-schema can add them in place.
    subject_reference = db.Column(db.String(255), nullable=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # The systems and processors selected for the request, with the recorded
    # model links that put each one in scope, who was asked to search it and
    # whether that search is done.
    scope_json = db.Column(db.JSON, nullable=True)
    # What an erasure removed, when and by whom (the review taken before it
    # ran, then the result). The same facts are written to the audit trail.
    erasure_evidence_json = db.Column(db.JSON, nullable=True)

    @property
    def canonical_type(self):
        return LEGACY_REQUEST_TYPES.get(self.request_type, self.request_type)

    @property
    def type_label(self):
        return REQUEST_TYPES.get(self.canonical_type, self.request_type)

    @property
    def has_platform_account(self):
        return bool(self.user_id) and self.user_id != NO_PLATFORM_ACCOUNT

    def __repr__(self):
        return f"<GDPRRequest {self.request_type} for user {self.user_id} ({self.status})>"
