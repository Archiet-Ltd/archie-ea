"""Team allocation model: which team works on which element, and the share.

A team is a ``BusinessActor`` with ``actor_type='Team'``. Allocations record
the share of a team's time spent on an application or technology element.
Shares can be derived (from the applications a team owns that run on a
technology) or recorded directly by a person.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint

from app import db
from app.models.mixins import TenantMixin


class TeamAllocation(TenantMixin, db.Model):
    """Allocation of a team's time to an application or technology element.

    Each row records what share of a team's time is spent on one element.
    Shares for one team add up to at most 100 percent.
    """

    __tablename__ = "team_allocations"
    __table_args__ = (
        CheckConstraint(
            "share_percent IS NULL OR (share_percent >= 0 AND share_percent <= 100)",
            name="check_share_percent_range",
        ),
        CheckConstraint(
            "source IN ('derived', 'recorded')",
            name="check_allocation_source",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)

    # The team (BusinessActor with actor_type='Team')
    team_id = db.Column(
        db.Integer,
        db.ForeignKey("business_actors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The element this team works on (application or technology)
    element_type = db.Column(db.String(30), nullable=False, index=True)
    element_id = db.Column(db.Integer, nullable=False, index=True)

    # Share of the team's time (0-100)
    share_percent = db.Column(db.Integer, nullable=False, default=100)

    # Source: 'derived' (computed from ownership) or 'recorded' (set by a person)
    source = db.Column(db.String(20), nullable=False, default="recorded")

    # When this allocation was recorded or derived
    allocation_date = db.Column(db.Date, default=date.today)

    # Metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    team = db.relationship(
        "BusinessActor",
        foreign_keys=[team_id],
        backref=db.backref("team_allocations", lazy="dynamic", cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return (
            f"<TeamAllocation team={self.team_id} "
            f"element={self.element_type}:{self.element_id} "
            f"{self.share_percent}% [{self.source}]>"
        )