"""RequirementTask — a builder's claim on a Confirmed requirement (ENT-V-3/4/6).

A builder identity claims a Confirmed requirement, which creates a task showing
its holder and the time it was claimed (ENT-V-3). When the builder reports a
delivery (commit, files, test, command, result line), the task shows
"Delivered, awaiting verification" — never Done (ENT-V-4). A Failed verdict
reopens the task to its builder with the unmet parts listed (ENT-V-6).

A new table, so it is created by create_all at boot (init-db); no change to any
existing table. TenantMixin scopes it to the organisation, like the requirement
it belongs to.
"""

from app.extensions import db
from app.models.mixins.core import TenantMixin, TimestampMixin


class RequirementTask(db.Model, TenantMixin, TimestampMixin):
    __tablename__ = "requirement_tasks"

    CLAIMED = "Claimed"
    DELIVERED = "Delivered, awaiting verification"
    REOPENED = "Reopened, awaiting delivery"

    id = db.Column(db.Integer, primary_key=True)

    requirement_element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    holder = db.Column(db.String(120), nullable=False)  # builder key label
    status = db.Column(db.String(40), nullable=False, default=CLAIMED)

    claimed_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)

    # Delivery evidence (ENT-V-4).
    commit = db.Column(db.String(64), nullable=True)
    files = db.Column(db.JSON, nullable=True)
    test = db.Column(db.Text, nullable=True)
    command = db.Column(db.Text, nullable=True)
    result_line = db.Column(db.Text, nullable=True)

    # Set when a Failed verdict reopens the task (ENT-V-6).
    unmet_parts = db.Column(db.JSON, nullable=True)

    def __repr__(self):
        return f"<RequirementTask {self.id} req={self.requirement_element_id} {self.status}>"
