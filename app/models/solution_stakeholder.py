"""
Solution Stakeholder Models for ArchiMate 3.2

ArchiMate 3.2 Stakeholder element implementation for the solutions module.
Stakeholders represent individuals, groups, or organizations with interests
in or concerns about the architecture.

ArchiMate 3.2 References:
- Stakeholder: Motivation Layer element representing the role of an individual,
  team, or organization that has interests in, or concerns relative to, the outcome
  of the architecture.

This module provides:
- SolutionStakeholder: Core stakeholder entity with influence/interest grid
- SolutionStakeholderConcern: Specific concerns a stakeholder has
- SolutionStakeholderMapping: Links stakeholders to solutions/sessions with roles
"""

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app import db
from app.models.mixins import TenantMixin

# ============================================================================
# ENUMS
# ============================================================================


class StakeholderType(enum.Enum):
    """Type classification for stakeholders"""

    INDIVIDUAL = "individual"  # Single person (e.g., CTO, Product Owner)
    GROUP = "group"  # Team or committee (e.g., Architecture Board)
    ORGANIZATION = "organization"  # Business unit or department (e.g., Finance Dept)
    ROLE = "role"  # Generic role (e.g., End User, System Admin)


class StakeholderAttitude(enum.Enum):
    """Stakeholder attitude toward a solution/change"""

    CHAMPION = "champion"  # Actively promoting and supporting
    SUPPORTER = "supporter"  # Positive but not actively pushing
    NEUTRAL = "neutral"  # No strong opinion either way
    SKEPTIC = "skeptic"  # Has doubts but may be persuaded
    BLOCKER = "blocker"  # Actively opposing or blocking


class ConcernType(enum.Enum):
    """Types of stakeholder concerns"""

    COST = "cost"  # Budget, TCO, ROI concerns
    TIMELINE = "timeline"  # Schedule, delivery date concerns
    QUALITY = "quality"  # Performance, reliability, usability
    RISK = "risk"  # Security, operational, strategic risks
    COMPLIANCE = "compliance"  # Regulatory, audit, governance
    CAPABILITY = "capability"  # Functional requirements, features
    INTEGRATION = "integration"  # System connectivity, data flow


class StakeholderRole(enum.Enum):
    """Role a stakeholder plays in relation to a solution/session"""

    SPONSOR = "sponsor"  # Executive sponsor, funding authority
    OWNER = "owner"  # Business/product owner
    CONTRIBUTOR = "contributor"  # Active participant in design/decisions
    REVIEWER = "reviewer"  # Reviews and approves deliverables
    INFORMED = "informed"  # Needs to be kept informed (RACI: I)
    CONSULTED = "consulted"  # Provides input when asked (RACI: C)


class EngagementLevel(enum.Enum):
    """Level of engagement required/expected"""

    HIGH = "high"  # Frequent, active engagement required
    MEDIUM = "medium"  # Regular but not intensive engagement
    LOW = "low"  # Minimal engagement, periodic updates


class CommunicationPreference(enum.Enum):
    """How stakeholder prefers to receive information"""

    DETAILED = "detailed"  # Full technical details
    SUMMARY = "summary"  # Executive summary level
    EXCEPTIONS_ONLY = "exceptions_only"  # Only notify on issues/risks


# ============================================================================
# SOLUTION STAKEHOLDER MODEL
# ============================================================================


class SolutionStakeholder(TenantMixin, db.Model):
    """
    ArchiMate 3.2 Stakeholder element for solutions.

    Represents individuals, groups, or organizations with interests in
    the solution architecture. Supports power/interest grid analysis
    for stakeholder management.

    ArchiMate Definition:
    "The role of an individual, team, or organization (or classes thereof)
    that represents their interests in the effects of the architecture."
    """

    __tablename__ = "solution_stakeholders"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text)

    # Link to canonical entities — a stakeholder IS a BusinessActor or User, not a copy
    business_actor_id = Column(Integer, ForeignKey("business_actors.id"), nullable=True, index=True)  # migration-exempt
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # migration-exempt

    # Stakeholder classification
    stakeholder_type = Column(
        Enum(StakeholderType), nullable=False, default=StakeholderType.INDIVIDUAL
    )

    # Power/Interest Grid positioning (1 - 5 scale)
    # Used for stakeholder analysis and prioritization
    influence_level = Column(Integer, default=3)  # 1=Low, 5=High power/influence
    interest_level = Column(Integer, default=3)  # 1=Low, 5=High interest

    # Attitude toward changes/solutions
    attitude = Column(Enum(StakeholderAttitude), default=StakeholderAttitude.NEUTRAL)

    # Stakeholder concerns (JSON list for flexibility)
    # Example: ["data security", "system downtime", "user adoption"]
    concerns = Column(JSON)

    # Contact information (structured JSON)
    # Example: {"email": "...", "phone": "...", "department": "..."}
    contact_info = Column(JSON)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships — canonical entity links
    business_actor = relationship("BusinessActor", foreign_keys=[business_actor_id], lazy="select")
    linked_user = relationship("User", foreign_keys=[user_id], lazy="select")

    # Relationships — solution mappings
    detailed_concerns = relationship(
        "SolutionStakeholderConcern", back_populates="stakeholder", cascade="all, delete-orphan"
    )
    solution_mappings = relationship(
        "SolutionStakeholderMapping", back_populates="stakeholder", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_stakeholder_type", "stakeholder_type"),
        Index("idx_stakeholder_influence", "influence_level"),
        Index("idx_stakeholder_interest", "interest_level"),
        Index("idx_stakeholder_attitude", "attitude"),
    )

    def __repr__(self):
        return f"<SolutionStakeholder {self.id}: {self.name} ({self.stakeholder_type.value})>"

    @property
    def quadrant(self):
        """
        Determine stakeholder quadrant in power/interest grid.

        Returns:
            str: One of 'manage_closely', 'keep_satisfied',
                 'keep_informed', 'monitor'
        """
        high_influence = self.influence_level >= 4
        high_interest = self.interest_level >= 4

        if high_influence and high_interest:
            return "manage_closely"  # High power, high interest
        elif high_influence and not high_interest:
            return "keep_satisfied"  # High power, low interest
        elif not high_influence and high_interest:
            return "keep_informed"  # Low power, high interest
        else:
            return "monitor"  # Low power, low interest

    def to_dict(self, include_details=True):
        """Serialize stakeholder to dictionary."""
        base_dict = {
            "id": self.id,
            "name": self.name,
            "stakeholder_type": self.stakeholder_type.value if self.stakeholder_type else None,
            "influence_level": self.influence_level,
            "interest_level": self.interest_level,
            "attitude": self.attitude.value if self.attitude else None,
            "quadrant": self.quadrant,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

        if include_details:
            base_dict.update(
                {
                    "description": self.description,
                    "concerns": self.concerns,
                    "contact_info": self.contact_info,
                    "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                }
            )

        return base_dict


# ============================================================================
# STAKEHOLDER CONCERN MODEL
# ============================================================================


class SolutionStakeholderConcern(db.Model):
    """
    Specific concerns a stakeholder has about a solution.

    Provides detailed tracking of individual concerns with priority,
    resolution status, and notes. Enables systematic concern
    management throughout the solution lifecycle.
    """

    __tablename__ = "solution_stakeholder_concerns"

    id = Column(Integer, primary_key=True)
    stakeholder_id = Column(
        Integer, ForeignKey("solution_stakeholders.id", ondelete="CASCADE"), nullable=False
    )

    # Concern classification
    concern_type = Column(Enum(ConcernType), nullable=False)

    # Concern details
    description = Column(Text, nullable=False)
    priority = Column(Integer, default=3)  # 1=Highest, 5=Lowest

    # Resolution tracking
    is_addressed = Column(Boolean, default=False)
    resolution_notes = Column(Text)
    addressed_at = Column(DateTime)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    stakeholder = relationship("SolutionStakeholder", back_populates="detailed_concerns")

    # Indexes
    __table_args__ = (
        Index("idx_concern_stakeholder", "stakeholder_id"),
        Index("idx_concern_type", "concern_type"),
        Index("idx_concern_priority", "priority"),
        Index("idx_concern_addressed", "is_addressed"),
    )

    def __repr__(self):
        return (
            f"<SolutionStakeholderConcern {self.id}: {self.concern_type.value} (P{self.priority})>"
        )

    def to_dict(self):
        """Serialize concern to dictionary."""
        return {
            "id": self.id,
            "stakeholder_id": self.stakeholder_id,
            "concern_type": self.concern_type.value if self.concern_type else None,
            "description": self.description,
            "priority": self.priority,
            "is_addressed": self.is_addressed,
            "resolution_notes": self.resolution_notes,
            "addressed_at": self.addressed_at.isoformat() if self.addressed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================================
# STAKEHOLDER MAPPING MODEL
# ============================================================================


class SolutionStakeholderMapping(db.Model):
    """
    Junction table linking stakeholders to solutions and analysis sessions.

    Enables many-to-many relationships between stakeholders and:
    - SolutionAnalysisSession (from solution_architect_models.py)
    - Solution (from truly_missing_models.py)

    Each mapping captures the stakeholder's specific role and engagement
    level for that particular solution/session.
    """

    __tablename__ = "solution_stakeholder_mappings"

    id = Column(Integer, primary_key=True)
    stakeholder_id = Column(
        Integer, ForeignKey("solution_stakeholders.id", ondelete="CASCADE"), nullable=False
    )

    # Polymorphic linking - one of these should be set
    session_id = Column(
        Integer, ForeignKey("solution_analysis_sessions.id", ondelete="CASCADE"), nullable=True
    )
    solution_id = Column(Integer, ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True)
    # A transformation programme (the platform's programme store is
    # StrategicInitiative). Nullable like the two links above; the same
    # stakeholder row can sit on a solution's map and a programme's map.
    programme_id = Column(
        Integer, ForeignKey("strategic_initiatives.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Role and engagement
    role = Column(Enum(StakeholderRole), nullable=False, default=StakeholderRole.INFORMED)
    engagement_level = Column(Enum(EngagementLevel), default=EngagementLevel.MEDIUM)
    communication_preference = Column(
        Enum(CommunicationPreference), default=CommunicationPreference.SUMMARY
    )

    # Additional notes for this specific mapping
    notes = Column(Text)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    stakeholder = relationship("SolutionStakeholder", back_populates="solution_mappings")
    session = relationship("SolutionAnalysisSession", backref="stakeholder_mappings")
    solution = relationship("Solution", backref="stakeholder_mappings")

    # Indexes and constraints
    __table_args__ = (
        # Ensure a stakeholder is only mapped once per session
        UniqueConstraint("stakeholder_id", "session_id", name="uq_stakeholder_session"),
        # Ensure a stakeholder is only mapped once per solution
        UniqueConstraint("stakeholder_id", "solution_id", name="uq_stakeholder_solution"),
        Index("idx_mapping_stakeholder", "stakeholder_id"),
        Index("idx_mapping_session", "session_id"),
        Index("idx_mapping_solution", "solution_id"),
        Index("idx_mapping_role", "role"),
    )

    def __repr__(self):
        target = f"session={self.session_id}" if self.session_id else f"solution={self.solution_id}"
        return (
            f"<SolutionStakeholderMapping {self.id}: stakeholder={self.stakeholder_id} -> {target}>"
        )

    def to_dict(self, include_stakeholder=False):
        """Serialize mapping to dictionary."""
        result = {
            "id": self.id,
            "stakeholder_id": self.stakeholder_id,
            "session_id": self.session_id,
            "solution_id": self.solution_id,
            "role": self.role.value if self.role else None,
            "engagement_level": self.engagement_level.value if self.engagement_level else None,
            "communication_preference": self.communication_preference.value
            if self.communication_preference
            else None,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        if include_stakeholder and self.stakeholder:
            result["stakeholder"] = self.stakeholder.to_dict(include_details=False)

        return result
