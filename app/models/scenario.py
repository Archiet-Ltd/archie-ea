"""Scenario branches: an organisation's what-if changes laid over the base model.

A branch is a name and an ordered list of changes. Nothing is copied: the base
element and relationship tables are never written to, and the impact answer
reads the branch's changes on top of them (see
``app/modules/intelligence/services/scenario_overlay.py``).

An element or relationship that a branch adds has no row of its own in the base
tables. Inside answers it is named by a negative number, ``-<change id>``, so it
can never collide with a real record.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

CHANGE_KINDS = ("add", "change", "remove")
TARGET_KINDS = ("element", "relationship")


class ScenarioBranch(TenantMixin, db.Model):
    __tablename__ = "scenario_branches"
    __table_args__ = (
        db.UniqueConstraint("organization_id", "name", name="uq_scenario_branches_org_name"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    changes = db.relationship(
        "ScenarioChange",
        backref="branch",
        order_by="ScenarioChange.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self, with_changes=False):
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if with_changes:
            data["changes"] = [c.to_dict() for c in self.changes]
        return data


class ScenarioChange(TenantMixin, db.Model):
    __tablename__ = "scenario_changes"

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(
        db.Integer, db.ForeignKey("scenario_branches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    change_kind = db.Column(db.String(10), nullable=False)  # add, change, remove
    target_kind = db.Column(db.String(12), nullable=False)  # element, relationship
    # The record changed or removed: a base id, or the negative id of something
    # this branch added. None for an addition (its id is -<this change's id>).
    target_id = db.Column(db.Integer, nullable=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def added_id(self):
        """The id this change's addition goes by inside answers."""
        return -int(self.id)

    def to_dict(self):
        return {
            "id": self.id,
            "branch_id": self.branch_id,
            "change_kind": self.change_kind,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "added_id": self.added_id if self.change_kind == "add" else None,
            "payload": self.payload or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
