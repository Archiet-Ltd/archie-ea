"""R1-B30 (TB-0106): the stored answer.

Every question the intelligence module answers (what breaks if this fails, what
breaks if these fail together, which risks reach this service) is stored once
as an immutable snapshot with a permanent address: the rows as they were, the
truth class and confidence of what they rest on, the citations back to the
records they came from, and the time they were computed. Opening the address
later shows exactly that snapshot, not a recomputation.

One table for every answer kind. Saving, sharing and exporting answers
(R1-B40) and the other consumers build on this store; none creates a second one.
"""

from __future__ import annotations

import datetime as _dt

from app import db
from app.models.mixins.core import TenantMixin


class AnswerRecord(TenantMixin, db.Model):
    """One stored answer. Immutable after insert: nothing in the product
    updates a row, a new question stores a new row."""

    __tablename__ = "answer_records"
    __table_args__ = (
        db.UniqueConstraint("address", name="uq_answer_record_address"),
        db.Index("ix_answer_records_org_created", "organization_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # The permanent address: unguessable, global-unique. A lookup always also
    # names the caller's organisation, so another organisation's address reads
    # exactly like one that was never issued.
    address = db.Column(db.String(32), nullable=False)
    question_kind = db.Column(db.String(32), nullable=False)
    params = db.Column(db.JSON, nullable=False, default=dict)

    # Drawn / derived / synchronised (query_catalogue.TRUTH_CLASS_*); None when
    # the answer holds no rows and so rests on nothing.
    truth_class = db.Column(db.String(16), nullable=True)
    # Lowest confidence among derived rows; None when every row is a drawn fact
    # (confidence_basis says which) or the answer is empty.
    confidence = db.Column(db.Float, nullable=True)
    confidence_basis = db.Column(db.String(24), nullable=False, default="no_rows")

    rows = db.Column(db.JSON, nullable=False, default=list)
    summary = db.Column(db.JSON, nullable=False, default=dict)
    reasons = db.Column(db.JSON, nullable=False, default=list)
    elements = db.Column(db.JSON, nullable=False, default=dict)
    citations = db.Column(db.JSON, nullable=False, default=list)
    freshness = db.Column(db.JSON, nullable=False, default=dict)
    # True when the stored rows were cut at the storage ceiling; the summary
    # still carries the true total.
    truncated = db.Column(db.Boolean, nullable=False, default=False)

    computed_at = db.Column(db.DateTime, nullable=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_dt.datetime.utcnow)
