"""
SA-001: SolutionElement join table.

Many-to-many: Solution ↔ ArchiMateElement with optional layer annotation,
allowing a Solution to link to N ArchiMate elements across multiple layers.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class SolutionElement(TenantMixin, db.Model):
    __tablename__ = "solution_elements"
    __tenant_parent__ = (('solution_id', 'solutions'),)

    id = db.Column(db.Integer, primary_key=True)
    solution_id = db.Column(
        db.Integer,
        db.ForeignKey("solutions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    archimate_element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Motivation, Business, Application, Technology, etc.
    layer = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "solution_id", "archimate_element_id", name="uq_solution_element"
        ),
        {"extend_existing": True},
    )

    solution = db.relationship("Solution", backref=db.backref("elements", lazy="dynamic"))
    archimate_element = db.relationship("ArchiMateElement")
