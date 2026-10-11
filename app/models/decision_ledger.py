from datetime import datetime

from sqlalchemy.orm import relationship

from app import db
from app.models.mixins.core import TenantMixin


class DecisionLedger(TenantMixin, db.Model):
    """Append-only decision ledger for Capability Governance.

    Each row represents one decision event related to a capability. Snapshot
    fields store minimal capability metadata to avoid joins for historical
    reporting. Use `(capability_id, decision_sequence)` or `decision_date`
    to retrieve latest decision per capability.

    This table had no organisation column at all before this change -- a now-
    removed, unreachable service class queried every row with no predicate,
    so any organisation's ARB session would have loaded every other
    organisation's governance decisions into memory (ADR 0012 -- that
    service was retired rather than fixed, since nothing constructed it).
    The mixin's organization_id is overridden nullable here (reconcile-schema is
    ADD-only; see application_owner.py/ai_chat_crud_approval.py for the same
    pattern) so it can be added to this existing table and backfilled by
    resolving each row's capability_id against UnifiedCapability; a row whose
    capability cannot be resolved keeps organization_id NULL and is
    therefore invisible to every organisation (the tenant filter, not a
    guess) until a platform administrator resolves it by hand.
    """

    __tablename__ = "decision_ledger"

    id = db.Column(db.Integer, primary_key=True)

    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    capability_id = db.Column(db.String(64), nullable=False, index=True)
    # Snapshot fields (minimal): copy the capability name and owner at time
    capability_name_snapshot = db.Column(db.String(255), nullable=False)
    business_owner_snapshot = db.Column(db.String(128), nullable=True)

    # Decision-specific fields
    decision_id = db.Column(db.String(64), nullable=False)
    decision_sequence = db.Column(db.Integer, nullable=False, default=1)
    decision_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decision_summary = db.Column(db.Text, nullable=True)
    decision_owner = db.Column(db.String(128), nullable=True)
    decision_type = db.Column(db.String(64), nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    impact_estimate = db.Column(db.Text, nullable=True)
    approval_status = db.Column(db.String(32), nullable=True, index=True)

    # Additional metadata and extensible payload
    related_docs = db.Column(db.JSON, nullable=True)  # list of doc references
    tags = db.Column(db.JSON, nullable=True)  # list of tag strings

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Note: additional composite indexes are created via migrations
