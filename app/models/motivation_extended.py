import os

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
        __table_args__ = {"extend_existing": True}

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


def __getattr__(name):
    if name == "Principle":
        from .models import Principle

        return Principle
    raise AttributeError(name)
