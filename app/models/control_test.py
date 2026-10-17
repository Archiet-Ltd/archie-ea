"""A test of an adopted compliance control: did its design hold, did it operate.

One row per test. The latest test of each kind decides whether the control is
effective (see ``app.modules.compliance.services.control_evidence``). A row is
tenant-owned and names the adopted control row of the same organisation.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

TEST_KINDS = ("design", "operating")
TEST_RESULTS = ("effective", "ineffective", "exception")


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

    application_control = db.relationship(
        "ApplicationComplianceControl",
        backref=db.backref("tests", lazy="dynamic", cascade="all, delete-orphan"),
    )
    tested_by = db.relationship("User", foreign_keys=[tested_by_id])

    def to_dict(self):
        return {
            "id": self.id,
            "application_control_id": self.application_control_id,
            "test_kind": self.test_kind,
            "result": self.result,
            "tested_at": self.tested_at.isoformat() if self.tested_at else None,
            "tested_by_id": self.tested_by_id,
            "notes": self.notes,
            "evidence_url": self.evidence_url,
        }
