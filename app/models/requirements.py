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

        # WSJF components — empty until a person records them
        business_value = db.Column(db.Integer)
        time_criticality = db.Column(db.Integer)
        risk_reduction = db.Column(db.Integer)
        job_size = db.Column(db.Integer)

        # RICE components — empty until a person records them
        reach = db.Column(db.Integer)
        impact = db.Column(db.Integer)
        confidence = db.Column(db.Integer)

        @property
        def wsjf_score(self):
            """None until all four WSJF inputs are recorded."""
            from app.services.wsjf_service import compute_wsjf_score

            parts = (self.business_value, self.time_criticality, self.risk_reduction, self.job_size)
            if any(part is None for part in parts) or self.job_size < 1:
                return None
            return compute_wsjf_score(*parts)

        @property
        def rice_score(self):
            """None until reach, impact, confidence and story points are all recorded."""
            parts = (self.reach, self.impact, self.confidence, self.story_points)
            if any(part is None for part in parts) or self.story_points < 1:
                return None
            return round((self.reach * self.impact * (self.confidence / 100)) / self.story_points, 2)

        def __repr__(self):
            return f"<Requirement {self.title or self.category or self.id}>"
