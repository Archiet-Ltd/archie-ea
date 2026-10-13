"""The edges of an architecture journey: what it references, and who is on it.

`ArchitectureJourney` shipped with exactly two typed edges -- `solution_id` and
`programme_id` -- while every other record in this repository is reached through
`solution_id`. The consequence was that a journey whose outcome is
``architecture_only`` or ``no_change_recommended`` could not own a decision, a risk,
a document or a participant: the very journeys the aggregate was designed to support
were the ones with nowhere to put their work.

Two tables close that, and the choice between them and the obvious alternative is
worth recording.

The alternative was a nullable ``architecture_journey_id`` on each of ``risks``,
``architecture_decision_records``, ``work_packages``, ``plateaus``, ``gaps``,
``strategic_roadmap_items``, ``decision_briefs``, ``arb_review_items`` and more --
eleven columns across eleven governed tables. A single association table expresses
the same edges without touching any system of record, which is the point: a journey
**references** records, it does not own them and must never copy them.

So there are no denormalised ``title``/``status`` columns here. Copying a decision's
title onto the journey would go stale silently the moment someone renamed the
decision, and the journey would then display, with apparent authority, a fact that is
no longer true anywhere else in the system. Names are resolved at read time from the
record itself, or not shown at all.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin


# What a journey may point at. Deliberately a closed set: an unconstrained string
# lets a typo create an edge to a record type that does not exist, and nothing ever
# reports it -- the link simply resolves to nothing at read time and the reader sees
# a shorter list than the truth.
JOURNEY_LINK_ENTITY_TYPES = (
    "decision",            # ArchitectureDecision (the register; R1-02 repoint)
    "decision_brief",      # DecisionBrief
    "risk",                # Risk
    "document",            # a registered document / evidence record
    "archimate_element",   # ArchiMateElement
    "architecture_model",  # ArchitectureModel
    "work_package",        # WorkPackage
    "capability",          # BusinessCapability
    "value_stream",
    "application",         # ApplicationComponent
    "arb_review",          # ARBReviewItem
    "programme",           # StrategicInitiative
    "solution",            # Solution
)

# How the journey relates to the thing. The relation is part of the meaning: a
# journey that *produced* a decision and one that is merely *informed by* it are
# telling the reader different things about accountability.
JOURNEY_LINK_RELATIONS = (
    "informs",     # evidence feeding the journey
    "produces",    # an output the journey created
    "governs",     # a governance obligation over the journey
    "impacts",     # something the journey changes
    "references",  # a plain pointer
)

JOURNEY_MEMBER_ROLES = (
    "owner",
    "chief_architect",
    "enterprise_architect",
    "business_architect",
    "solution_architect",
    "application_architect",
    "data_architect",
    "technology_architect",
    "security_architect",
    "programme_architect",
    "sponsor",
    "stakeholder",
    "reviewer",
    "contributor",
)


class ArchitectureJourneyLink(TenantMixin, db.Model):
    """One edge from a journey to a record that already exists elsewhere."""

    __tablename__ = "architecture_journey_links"

    id = db.Column(db.Integer, primary_key=True)
    journey_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_journeys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Polymorphic by necessity: the targets live in a dozen tables, several of which
    # are mapped twice via extend_existing. A real FK per type would mean a dozen
    # nullable columns and a CHECK constraint enumerating their exclusivity -- the
    # shape the ARB review tables already use, and the reason adding a fifth subject
    # type there is a manual migration rather than a column add.
    entity_type = db.Column(db.String(40), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    relation = db.Column(
        db.String(24), nullable=False, default="references", server_default="references"
    )
    # Optional, and free text on purpose: why this link exists is the architect's
    # judgement, not a derived fact.
    note = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    journey = db.relationship("ArchitectureJourney", foreign_keys=[journey_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        db.CheckConstraint(
            f"entity_type IN ({', '.join(repr(v) for v in JOURNEY_LINK_ENTITY_TYPES)})",
            name="ck_architecture_journey_link_entity_type",
        ),
        db.CheckConstraint(
            f"relation IN ({', '.join(repr(v) for v in JOURNEY_LINK_RELATIONS)})",
            name="ck_architecture_journey_link_relation",
        ),
        # The same record linked twice with the same relation is a duplicate, not
        # two facts. Scoped by organisation so two tenants cannot collide.
        db.UniqueConstraint(
            "organization_id",
            "journey_id",
            "entity_type",
            "entity_id",
            "relation",
            name="uq_architecture_journey_link",
        ),
    )

    def __repr__(self):  # pragma: no cover - diagnostic only
        return (
            f"<ArchitectureJourneyLink journey={self.journey_id} "
            f"{self.relation} {self.entity_type}:{self.entity_id}>"
        )


class ArchitectureJourneyMember(TenantMixin, db.Model):
    """A person on a journey.

    A real FK to ``users``, never a typed-in name. A name column would be fabricated
    data the moment the person is renamed or leaves, and a journey that lists a
    participant who no longer exists is worse than one that lists nobody -- the
    reader cannot tell which.
    """

    __tablename__ = "architecture_journey_members"

    id = db.Column(db.Integer, primary_key=True)
    journey_id = db.Column(
        db.Integer,
        db.ForeignKey("architecture_journeys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = db.Column(
        db.String(40), nullable=False, default="contributor", server_default="contributor"
    )
    added_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    journey = db.relationship("ArchitectureJourney", foreign_keys=[journey_id])
    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        db.CheckConstraint(
            f"role IN ({', '.join(repr(v) for v in JOURNEY_MEMBER_ROLES)})",
            name="ck_architecture_journey_member_role",
        ),
        db.UniqueConstraint(
            "organization_id", "journey_id", "user_id", name="uq_architecture_journey_member"
        ),
    )

    def __repr__(self):  # pragma: no cover - diagnostic only
        return f"<ArchitectureJourneyMember journey={self.journey_id} user={self.user_id}>"


__all__ = [
    "JOURNEY_LINK_ENTITY_TYPES",
    "JOURNEY_LINK_RELATIONS",
    "JOURNEY_MEMBER_ROLES",
    "ArchitectureJourneyLink",
    "ArchitectureJourneyMember",
]
