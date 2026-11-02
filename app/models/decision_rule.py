"""One rule row of a DMN decision table.

A decision is an ArchiMate element (a business function carrying a ``dmn`` entry in its custom
properties for the hit policy and the input and output columns). Its numbered rule rows are held
here: one row per rule, with one entry per input column and one per output column, in column order.
Written by ``app/modules/architecture/services/decision_table_service.py``.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


class DecisionRule(TenantMixin, db.Model):
    __tablename__ = "decision_rules"
    __table_args__ = (
        db.Index("ix_decision_rules_element_position", "element_id", "position"),
    )

    id = db.Column(db.Integer, primary_key=True)
    element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position = db.Column(db.Integer, nullable=False)  # the rule number, from 1
    input_entries = db.Column(db.JSON, nullable=False, default=list)  # one unary test per input column
    output_entries = db.Column(db.JSON, nullable=False, default=list)  # one value per output column
    annotation = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)
