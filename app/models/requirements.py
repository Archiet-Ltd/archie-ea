import os

from .. import db
from .mixins import TenantMixin

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


if not _FAST_INIT:
    # Full model lives in the monolithic module.
    from .models import Requirement  # noqa: F401
else:

    class Requirement(TenantMixin, db.Model):
        __tablename__ = "requirements"

        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=True)
        description = db.Column(db.Text, nullable=True)
        type = db.Column(db.String(50), nullable=True)
        priority = db.Column(db.String(20), nullable=True)
        category = db.Column(db.String(50), nullable=True)
        application_component_id = db.Column(
            db.Integer,
            db.ForeignKey("application_components.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )
        # User Story / Epic fields (TPM-003)
        epic_id = db.Column(db.Integer, db.ForeignKey("requirements.id"), nullable=True)
        story_points = db.Column(db.Integer, nullable=True)
        dod_complete = db.Column(db.Boolean, default=False, nullable=False, server_default='false')
        requirement_type = db.Column(db.String(32), default='requirement')

        # MoSCoW prioritisation (TPM-006)
        moscow_priority = db.Column(db.String(16), nullable=True)  # MUST/SHOULD/COULD/WONT

        # WSJF components (TPM-006)
        business_value = db.Column(db.Integer, default=1)
        time_criticality = db.Column(db.Integer, default=1)
        risk_reduction = db.Column(db.Integer, default=1)
        job_size = db.Column(db.Integer, default=1)

        # RICE components (TPM-006)
        reach = db.Column(db.Integer, default=0)
        impact = db.Column(db.Integer, default=1)
        confidence = db.Column(db.Integer, default=100)

        @property
        def wsjf_score(self):
            cod = (self.business_value or 1) + (self.time_criticality or 1) + (self.risk_reduction or 1)
            return round(cod / max(self.job_size or 1, 1), 2)

        @property
        def rice_score(self):
            sp = self.story_points or 1
            return round(
                ((self.reach or 0) * (self.impact or 1) * ((self.confidence or 100) / 100))
                / max(sp, 1),
                2,
            )

        def __repr__(self):
            return f"<Requirement {self.title or self.category or self.id}>"
