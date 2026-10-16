"""Tests of an adopted compliance control.

A control is tested twice over: a design test asks whether the control as
written would meet the requirement, an operating test asks whether it was
followed in the period. A row records one test of one adopted control row
(``ApplicationComplianceControl``, the per-organisation register). The latest
test of each kind decides the control's effectiveness; see
``app.modules.compliance.services.control_evidence``.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

__all__ = ["ControlTest", "CONTROL_TEST_KINDS", "CONTROL_TEST_RESULTS"]

CONTROL_TEST_KINDS = ("design", "operating")
CONTROL_TEST_RESULTS = ("effective", "ineffective", "exception")


class ControlTest(TenantMixin, db.Model):
    __tablename__ = "control_tests"

    id = db.Column(db.Integer, primary_key=True)
    application_control_id = db.Column(
        db.Integer,
        db.ForeignKey("application_compliance_controls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    test_kind = db.Column(db.String(20), nullable=False)  # design, operating
    result = db.Column(db.String(20), nullable=False)  # effective, ineffective, exception
    tested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tested_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    notes = db.Column(db.Text)
    evidence_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    tested_by = db.relationship("User", foreign_keys=[tested_by_id])

    def __repr__(self):
        return f"<ControlTest {self.application_control_id} {self.test_kind}:{self.result}>"
