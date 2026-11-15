"""A scenario a person saved from the impact analysis or simulation screen, kept for the organisation."""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class SavedScenario(TenantMixin, db.Model):
    """One named change tried on an element, with the analysis it produced.

    The organisation owns it: a colleague signing in later lists it and compares it. The analysis stored is the
    server's own figures for that change, recomputed when it is saved, not figures sent from the browser.
    """

    __tablename__ = "saved_scenarios"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    element_id = db.Column(db.Integer, nullable=True, index=True)
    element_name = db.Column(db.String(500), nullable=True)
    element_type = db.Column(db.String(100), nullable=True)
    change_type = db.Column(db.String(30), nullable=False)
    change_label = db.Column(db.String(100), nullable=True)
    analysis_result = db.Column(db.JSON, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": "scenario-%d" % self.id,
            "name": self.name,
            "element": {"id": self.element_id, "name": self.element_name, "type": self.element_type},
            "change_type": self.change_type,
            "change_label": self.change_label,
            "analysis_result": self.analysis_result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
