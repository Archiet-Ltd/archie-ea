"""ADM Deliverable template — the TOGAF checklist a solution's own deliverables are matched to.

A solution's deliverables live in ``deliverables`` (linked by ``solution_deliverables``);
this table only names what TOGAF expects each phase to produce. Whether an item is
complete is read from the matching deliverable's own status, never stored here.
"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from app import db


class ADMDeliverable(db.Model):
    __tablename__ = "adm_deliverables"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    # A, B, C, D, E, F, G, H, Requirements
    phase = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    # True = seed/template row; False = board-specific instance
    is_template = Column(Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "phase": self.phase,
            "name": self.name,
            "description": self.description,
            "is_template": self.is_template,
        }
