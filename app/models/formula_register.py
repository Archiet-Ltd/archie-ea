"""
Formula Register — R1-B34 (TB-0135).

Every composite score Archie computes (starting with the rationalization
TIME score) names the reviewed formula and version it was computed with,
instead of carrying its weights as an unversioned, unattributed constant
baked into the scoring service. A ``FormulaRegister`` row is immutable once
created: editing weights creates a new version rather than mutating history,
so a score computed last quarter can still be traced to the exact formula
that produced it.

System of record: this is the ONLY place formula weights live for a
registered formula key. ``ScoringConfiguration`` (application_rationalization.py)
remains the resolver of effective per-tenant overrides for the
rationalization formula specifically; this model generalises the pattern so
other composite scores can register here too, per the brief's own
"generalises the rationalisation scoring configuration" reuse note.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin


class FormulaRegister(TenantMixin, db.Model):
    """One versioned formula: its inputs, weights, owner and reviewer.

    ``formula_key`` names the composite score this formula computes (e.g.
    ``"rationalization_overall"``). Multiple versions of the same key can
    exist; ``is_active`` marks the one currently used for new computations.
    Creating a new active version for a key deactivates the previous one —
    see ``FormulaRegister.activate_new_version`` — so there is always at
    most one active version per (organization, formula_key).
    """

    __tablename__ = "formula_registers"

    id = db.Column(db.Integer, primary_key=True)

    formula_key = db.Column(db.String(80), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # {"input_name": weight, ...} — weights are expected to sum to 1.0 but
    # that is a review-time convention, not an enforced invariant here (a
    # formula mid-review may not yet balance).
    inputs = db.Column(db.JSON, nullable=False, default=dict)

    owner_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewer_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    reviewer = db.relationship("User", foreign_keys=[reviewer_user_id])

    __table_args__ = (
        db.Index("ix_formula_registers_org_key_active", "organization_id", "formula_key", "is_active"),
    )

    @classmethod
    def active_for(cls, organization_id, formula_key):
        """The currently active formula version for this org and key, or
        ``None`` when nothing has been registered yet — callers must treat
        that as "formula missing" (score is unmeasurable, renders ``—``),
        never fall back to a hardcoded weight silently."""
        return (
            cls.query.filter_by(
                organization_id=organization_id, formula_key=formula_key, is_active=True,
            )
            .order_by(cls.version.desc())
            .first()
        )

    @classmethod
    def activate_new_version(cls, organization_id, formula_key, *, inputs, owner_user_id=None,
                              reviewer_user_id=None):
        """Create the next version for this (org, key), deactivate the
        previous active one, and return the new row. Never mutates a past
        version's ``inputs`` — history stays exactly as it was computed."""
        current = cls.active_for(organization_id, formula_key)
        next_version = (current.version + 1) if current else 1
        if current is not None:
            current.is_active = False
        new_row = cls(
            organization_id=organization_id,
            formula_key=formula_key,
            version=next_version,
            inputs=inputs,
            owner_user_id=owner_user_id,
            reviewer_user_id=reviewer_user_id,
            reviewed_at=datetime.utcnow() if reviewer_user_id else None,
            is_active=True,
        )
        db.session.add(new_row)
        return new_row

    def to_dict(self):
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "formula_key": self.formula_key,
            "version": self.version,
            "is_active": self.is_active,
            "inputs": self.inputs,
            "owner_user_id": self.owner_user_id,
            "reviewer_user_id": self.reviewer_user_id,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
