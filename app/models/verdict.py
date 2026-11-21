"""Verdict — the Ifiok verifier's recorded judgement on a requirement.

Entelim had no store for test results or verdicts; ENT-V-6 closes that gap. A
Verdict records the verifier's judgement (Verified or Failed) on an ArchiMate
Requirement element, on a specific served commit, with the command run, its
result line, and any unmet parts. Only the verifier key may write one — enforced
in the route (ENT-V-5), not here.

A requirement counts as verified only while a Verified verdict exists for the
*current* served commit (ENT-V-7); the served commit comes from
app/_bootstrap/build_info.get_build_id().
"""

from app.extensions import db
from app.models.mixins.core import TenantMixin, TimestampMixin


class Verdict(db.Model, TenantMixin, TimestampMixin):
    __tablename__ = "verdicts"

    VERIFIED = "Verified"
    FAILED = "Failed"

    id = db.Column(db.Integer, primary_key=True)

    # The ArchiMate Requirement element this verdict is about.
    requirement_element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status = db.Column(db.String(20), nullable=False)  # VERIFIED | FAILED
    served_commit = db.Column(db.String(64), nullable=False, index=True)
    command = db.Column(db.Text, nullable=True)
    result_line = db.Column(db.Text, nullable=True)
    unmet_parts = db.Column(db.JSON, nullable=True)

    # Label of the verifier key that recorded it (who).
    recorded_by = db.Column(db.String(120), nullable=True)

    @property
    def is_verified(self) -> bool:
        return self.status == self.VERIFIED

    def __repr__(self):
        return f"<Verdict {self.id} req={self.requirement_element_id} {self.status}@{self.served_commit}>"
