import os

from sqlalchemy.orm import relationship

from .. import db
from .mixins import TenantMixin

_FAST_INIT = os.getenv("APP_FAST_INIT", "0") == "1"


if not _FAST_INIT:
    # Full models live in the monolithic module.
    from .models import Outcome, Principle  # noqa: F401
else:

    class Outcome(TenantMixin, db.Model):
        # Fast-init twin of models.py's Outcome. Unlike ArchiMateRelationship/
        # Principle/ApplicationComponent above, models.py's Outcome ALSO
        # lacked TenantMixin until this same change -- this was a live,
        # currently-active tenant-scoping gap in both branches, not a dormant
        # fast-init-only trap. See app/commands/backfill_outcome_org.py.
        __tablename__ = "outcomes"

        organization_id = db.Column(
            db.Integer,
            db.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )

        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(255), nullable=False)
        description = db.Column(db.Text)

        # Note: relationship() connections to ArchiMate elements can be added as needed

        def __repr__(self):
            return f"<Outcome {self.name}>"

    class Principle(TenantMixin, db.Model):
        # Fast-init twin of models.py:1633 -- that class carries TenantMixin
        # with organization_id overridden nullable (reconcile-schema is
        # ADD-only; see the comment there). Match it here so tenancy semantics
        # don't silently change if the fast-init flag is ever set.
        __tablename__ = "principles"

        id = db.Column(db.Integer, primary_key=True)
        organization_id = db.Column(
            db.Integer,
            db.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )
        name = db.Column(db.String(255), nullable=False)
        statement = db.Column(db.Text, nullable=True)
        description = db.Column(db.Text, nullable=True)
        rationale = db.Column(db.Text, nullable=True)
        implications = db.Column(db.Text, nullable=True)
        category = db.Column(db.String(50), nullable=True)
        enforcement_level = db.Column(db.String(20), nullable=True)
        enforcement_status = db.Column(db.String(20), nullable=False, default='advisory')  # mandatory/advisory/retired
        adm_phase = db.Column(db.String(5), nullable=True)  # e.g. 'A', 'B', 'D'

        def __repr__(self):
            return f"<Principle {self.name}>"
