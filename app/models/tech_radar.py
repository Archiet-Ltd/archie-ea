"""Technology Radar (ARCH-124).

The register's ask was "no technology standards catalogue or tech radar".
The Technology ArchiMate layer, Vendor records and Application lifecycle
status already exist — a tech radar is a classification layer over what is
already modelled, not a new inventory. This model holds that classification:
one row per (organization, technology-layer ArchiMateElement), carrying an
adopt/trial/assess/hold ring set by a human architect.

Nothing here is inferred or defaulted to a ring — an unclassified technology
element simply has no TechRadarEntry row, and the UI must render that as
"not yet classified", never as a default ring.

Retiring a standard: moving an entry to the hold ring with a sunset date
(and, optionally, the replacement standard) is how a technology standard is
phased out. The columns for that are nullable -- an entry that was never
sunset simply has none -- and the applications running the technology, and
their owners, are read live from the model at the time they are needed
(app/modules/tech_radar/service.py), never copied onto this row.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

RADAR_RINGS = ("adopt", "trial", "assess", "hold")
RADAR_RING_LABELS = {
    "adopt": "Adopt",
    "trial": "Trial",
    "assess": "Assess",
    "hold": "Hold",
}


class TechRadarEntry(TenantMixin, db.Model):
    """An architect's adopt/trial/assess/hold classification of one
    Technology-layer ArchiMateElement, already backed by a real Node,
    Device, SystemSoftware or TechnologyService record (see
    app/models/technology_layer.py's before_insert listeners)."""

    __tablename__ = "tech_radar_entries"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "archimate_element_id", name="uq_tech_radar_entry_element"
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    archimate_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=False, index=True
    )
    ring = db.Column(db.String(10), nullable=False)  # one of RADAR_RINGS
    rationale = db.Column(db.Text, nullable=True)
    # When the classification is next due for review. Nullable: an entry with
    # no recorded date reads as "—", never as a guessed date.
    review_date = db.Column(db.Date, nullable=True)
    # The programme or initiative that asked for this technology. The platform's
    # initiative store is StrategicInitiative; nullable, and cleared rather than
    # left dangling if that initiative is deleted.
    requesting_initiative_id = db.Column(
        db.Integer,
        db.ForeignKey("strategic_initiatives.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    set_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    # Sunset: the date after which the technology may no longer be used, the
    # standard that replaces it, and when the owners of the applications
    # running it were told. All nullable: reconcile-schema adds them to
    # existing databases, and an entry that was never sunset leaves them NULL.
    sunset_date = db.Column(db.Date, nullable=True)
    replacement_element_id = db.Column(
        db.Integer, db.ForeignKey("archimate_elements.id"), nullable=True
    )
    owners_notified_at = db.Column(db.DateTime, nullable=True)
    owners_notified_count = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True
    )

    element = db.relationship("ArchiMateElement", foreign_keys=[archimate_element_id])
    replacement = db.relationship("ArchiMateElement", foreign_keys=[replacement_element_id])
    set_by = db.relationship("User", foreign_keys=[set_by_user_id])
    requesting_initiative = db.relationship(
        "StrategicInitiative", foreign_keys=[requesting_initiative_id]
    )

    def to_dict(self):
        return {
            "id": self.id,
            "archimate_element_id": self.archimate_element_id,
            "element_name": self.element.name if self.element else None,
            "element_type": self.element.type if self.element else None,
            "ring": self.ring,
            "ring_label": RADAR_RING_LABELS.get(self.ring, self.ring),
            "rationale": self.rationale,
            "review_date": self.review_date.isoformat() if self.review_date else None,
            "requesting_initiative_id": self.requesting_initiative_id,
            "requesting_initiative_name": (
                self.requesting_initiative.name if self.requesting_initiative else None
            ),
            "set_by_user_id": self.set_by_user_id,
            "sunset_date": self.sunset_date.isoformat() if self.sunset_date else None,
            "replacement_element_id": self.replacement_element_id,
            "replacement_name": self.replacement.name if self.replacement else None,
            "owners_notified_at": (
                self.owners_notified_at.isoformat() if self.owners_notified_at else None
            ),
            "owners_notified_count": self.owners_notified_count,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
