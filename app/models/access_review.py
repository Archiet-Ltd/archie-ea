"""Quarterly access recertification (R1-B88, PB-0395).

An organisation administrator opens a cycle; every member's grant is listed as
one item, with the grants nobody has used for ``unused_days`` flagged from the
audit trail. Each item is kept or removed. A closed cycle is the evidence pack.

The grants under review are the existing ``OrgRole`` rows; the audit trail
(``soc2_audit_log``) is the only source of "used". This module adds the review
record itself and nothing else.
"""

from datetime import datetime, timedelta

from .. import db
from .mixins import TenantMixin

CYCLE_OPEN = "open"
CYCLE_CLOSED = "closed"

DECISION_PENDING = "pending"
DECISION_KEPT = "kept"
DECISION_REMOVED = "removed"

DEFAULT_UNUSED_DAYS = 90
CYCLE_LIFETIME = timedelta(days=30)


class AccessReviewCycle(TenantMixin, db.Model):
    """One recertification round for one organisation."""

    __tablename__ = "access_review_cycles"
    __table_args__ = (
        # At most one open cycle per organisation, enforced by the database so
        # two administrators opening at once cannot both succeed.
        db.Index(
            "uq_access_review_one_open",
            "organization_id",
            unique=True,
            postgresql_where=db.text("status = 'open'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    opened_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    opened_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    due_at = db.Column(db.DateTime, nullable=False)
    unused_days = db.Column(db.Integer, nullable=False, default=DEFAULT_UNUSED_DAYS)
    status = db.Column(db.String(10), nullable=False, default=CYCLE_OPEN, index=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    closed_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    summary_json = db.Column(db.JSON, nullable=True)

    items = db.relationship(
        "AccessReviewItem",
        back_populates="cycle",
        order_by="AccessReviewItem.id",
        cascade="all, delete-orphan",
    )

    @property
    def is_open(self):
        return self.status == CYCLE_OPEN

    def __repr__(self):
        return "<AccessReviewCycle id=%s org=%s status=%s>" % (
            self.id, self.organization_id, self.status,
        )


class AccessReviewItem(TenantMixin, db.Model):
    """One member's grant within a cycle, with the decision taken on it."""

    __tablename__ = "access_review_items"
    __table_args__ = (
        db.UniqueConstraint("cycle_id", "user_id", name="uq_access_review_item_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("access_review_cycles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Snapshots taken when the cycle opened, so the evidence shows what was reviewed.
    org_role = db.Column(db.String(50), nullable=False)
    persona = db.Column(db.String(80), nullable=True)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    last_sign_in_at = db.Column(db.DateTime, nullable=True)  # context only
    unused = db.Column(db.Boolean, nullable=False, default=False)

    decision = db.Column(db.String(10), nullable=False, default=DECISION_PENDING)
    decided_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.Text, nullable=True)

    cycle = db.relationship("AccessReviewCycle", back_populates="items")
    user = db.relationship("User", foreign_keys=[user_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])

    def __repr__(self):
        return "<AccessReviewItem id=%s cycle=%s user=%s decision=%s>" % (
            self.id, self.cycle_id, self.user_id, self.decision,
        )
