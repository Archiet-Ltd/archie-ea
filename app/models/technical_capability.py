"""
Technical Capability Model (Application Capability Model - ACM)

Implements the 7 - domain Application Capability Model for technical architecture:
- USER-EXPERIENCE: Frontend, UI/UX, accessibility, responsive design
- APPLICATION-SERVICES: APIs, microservices, business logic, integration
- DATA-STORAGE: Databases, data lakes, caching, file storage
- SECURITY-IDENTITY: Authentication, authorization, encryption, compliance
- DEVOPS-PLATFORM: CI/CD, infrastructure, monitoring, containerization
- AI-ANALYTICS: Machine learning, BI, data science, predictive analytics
- COMMUNICATION: Messaging, notifications, real-time, collaboration

Each domain has L0 - L4 abstraction levels:
- L0: Domain (e.g., USER-EXPERIENCE)
- L1: Capability Area (e.g., Web Interfaces)
- L2: Capability Group (e.g., Responsive Design)
- L3: Specific Capability (e.g., Mobile-First Layouts)
- L4: Implementation Pattern (e.g., CSS Grid with Breakpoints)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from .. import db


# ACM Domain Constants
class ACMDomain:
    """Application Capability Model Domain Constants"""

    USER_EXPERIENCE = "USER-EXPERIENCE"
    APPLICATION_SERVICES = "APPLICATION-SERVICES"
    DATA_STORAGE = "DATA-STORAGE"
    SECURITY_IDENTITY = "SECURITY-IDENTITY"
    DEVOPS_PLATFORM = "DEVOPS-PLATFORM"
    AI_ANALYTICS = "AI-ANALYTICS"
    COMMUNICATION = "COMMUNICATION"

    ALL_DOMAINS = [
        USER_EXPERIENCE,
        APPLICATION_SERVICES,
        DATA_STORAGE,
        SECURITY_IDENTITY,
        DEVOPS_PLATFORM,
        AI_ANALYTICS,
        COMMUNICATION,
    ]

    DOMAIN_DESCRIPTIONS = {
        USER_EXPERIENCE: "Frontend interfaces, UI/UX design, accessibility, and user interaction patterns",
        APPLICATION_SERVICES: "Backend services, APIs, business logic, and system integration",
        DATA_STORAGE: "Databases, data lakes, caching layers, and persistent storage solutions",
        SECURITY_IDENTITY: "Authentication, authorization, encryption, and security compliance",
        DEVOPS_PLATFORM: "CI/CD pipelines, infrastructure, monitoring, and platform operations",
        AI_ANALYTICS: "Machine learning, business intelligence, analytics, and data science",
        COMMUNICATION: "Messaging, notifications, real-time communication, and collaboration tools",
    }

    # APQC PCF Category mappings (primary relationships)
    APQC_MAPPINGS = {
        USER_EXPERIENCE: ["12.0"],  # IT Management - Service Delivery
        APPLICATION_SERVICES: ["12.0"],  # IT Management - Application Development
        DATA_STORAGE: ["12.0"],  # IT Management - Data Management
        SECURITY_IDENTITY: ["12.0"],  # IT Management - Security Operations
        DEVOPS_PLATFORM: ["12.0"],  # IT Management - Infrastructure
        AI_ANALYTICS: ["11.0", "12.0"],  # Business Intelligence + IT Management
        COMMUNICATION: ["12.0"],  # IT Management - Service Delivery
    }


# Mapping table: Technical Capability <-> Business Capability (LEGACY - DEPRECATED)
technical_capability_business_mapping = db.Table(
    "technical_capability_business_mapping",
    Column("id", Integer, primary_key=True),
    Column(
        "technical_capability_id",
        Integer,
        ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "business_capability_id",
        Integer,
        ForeignKey("business_capability.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_type", String(50), default="supports"),  # supports, enables, implements
    Column("strength", String(20), default="medium"),  # strong, medium, weak
    Column("created_at", DateTime, default=datetime.utcnow),
    Index(
        "idx_tech_bus_cap_mapping", "technical_capability_id", "business_capability_id", unique=True
    ),
    extend_existing=True,
)

# Mapping table: Technical Capability <-> Unified Capability (NEW - CURRENT STANDARD)
technical_capability_unified_mapping = db.Table(
    "technical_capability_unified_mapping",
    Column("id", Integer, primary_key=True),
    Column(
        "technical_capability_id",
        Integer,
        ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "unified_capability_id",
        Integer,
        ForeignKey("unified_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_type", String(50), default="implements"),  # implements, enables, supports
    Column("strength", String(20), default="medium"),  # strong, medium, weak
    Column("mapping_source", String(50), default="manual"),  # manual, migrated, auto-derived
    Column("created_at", DateTime, default=datetime.utcnow),
    Index(
        "idx_tech_unified_cap_mapping",
        "technical_capability_id",
        "unified_capability_id",
        unique=True,
    ),
    extend_existing=True,
)


# Mapping table: Application <-> Technical Capability
application_technical_capability_mapping = db.Table(
    "application_technical_capability_mapping",
    Column("id", Integer, primary_key=True),
    Column(
        "application_id",
        Integer,
        ForeignKey("application_components.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "technical_capability_id",
        Integer,
        ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("capability_coverage", String(20), default="partial"),  # full, partial, minimal
    Column("maturity_level", String(20)),  # initial, developing, defined, managed, optimized
    Column("notes", Text),
    Column("created_at", DateTime, default=datetime.utcnow),
    Index("idx_app_tech_cap_mapping", "application_id", "technical_capability_id", unique=True),
    extend_existing=True,
)


# Mapping table: Technical Capability <-> APQC Process
technical_capability_apqc_mapping = db.Table(
    "technical_capability_apqc_mapping",
    Column("id", Integer, primary_key=True),
    Column(
        "technical_capability_id",
        Integer,
        ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "apqc_process_id",
        Integer,
        ForeignKey("apqc_process.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relationship_type", String(50), default="implements"),  # implements, supports, enables
    Column("created_at", DateTime, default=datetime.utcnow),
    Index("idx_tech_cap_apqc_mapping", "technical_capability_id", "apqc_process_id", unique=True),
    extend_existing=True,
)


# Mapping table: Technical Capability <-> Vendor Product
technical_capability_vendor_mapping = db.Table(
    "technical_capability_vendor_mapping",
    Column("id", Integer, primary_key=True),
    Column(
        "technical_capability_id",
        Integer,
        ForeignKey("technical_capabilities.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "vendor_product_id",
        Integer,
        ForeignKey("vendor_products.id", ondelete="CASCADE", use_alter=True, name="fk_tech_cap_vendor_product_id"),
        nullable=False,
    ),
    Column("capability_coverage", String(20), default="partial"),  # full, partial, minimal
    Column("created_at", DateTime, default=datetime.utcnow),
    Index(
        "idx_tech_cap_vendor_mapping", "technical_capability_id", "vendor_product_id", unique=True
    ),
    extend_existing=True,
)


class TechnicalCapability(db.Model):
    """
    Technical Capability Model (ACM)

    Represents technical capabilities organized by the 7 ACM domains.
    Supports L0 - L4 hierarchy for detailed capability decomposition.
    """

    __tablename__ = "technical_capabilities"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Capability identity
    name = Column(String(256), nullable=False, index=True)
    code = Column(String(50), unique=True, index=True)  # e.g., UX - 01 - 02 - 03
    description = Column(Text)

    # Specialization type marker
    specialization_type = Column(
        String(50), default="TECHNICAL", index=True
    )  # Explicit type: TECHNICAL

    # ACM Domain (L0)
    acm_domain = Column(String(50), nullable=False, index=True)  # One of 7 domains

    # Hierarchy level (L0 - L4)
    level = Column(String(10), nullable=False, default="L1")  # L0, L1, L2, L3, L4
    level_number = Column(Integer, default=1)  # 0, 1, 2, 3, 4

    # Parent-child hierarchy
    parent_id = Column(Integer, ForeignKey("technical_capabilities.id"), nullable=True)
    parent = relationship("TechnicalCapability", remote_side=[id], backref="children")

    # Capability details
    capability_type = Column(String(50))  # functional, non-functional, cross-cutting
    technology_patterns = Column(Text)  # JSON array of common implementation patterns
    common_technologies = Column(Text)  # JSON array of technologies that provide this

    # Maturity and assessment
    industry_maturity = Column(String(20))  # emerging, growing, mature, declining
    complexity = Column(String(20))  # low, medium, high, very_high

    # Strategic attributes
    is_differentiating = Column(Boolean, default=False)  # Core differentiator vs commodity
    is_foundational = Column(Boolean, default=False)  # Required for other capabilities

    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Set once by `flask backfill-capability-catalogs` (ADR 0008): this table
    # has no organisation of its own (it is a fixed ACM domain taxonomy), so
    # every row is projected as a reference-scope `unified_capabilities` row,
    # matched first by `code` and otherwise by normalised name. NULL means
    # "not yet processed"; the backfill is idempotent on it.
    retired_into_id = Column(
        BigInteger,
        ForeignKey("unified_capabilities.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )

    # Dual mapping for backward compatibility
    # Legacy: maps to BusinessCapability (deprecated)
    # New: maps to UnifiedCapability (current standard)
    # Both can coexist for gradual migration

    # Relationships
    # LEGACY: maps to BusinessCapability (deprecated, kept for backward compatibility)
    business_capabilities = relationship(
        "BusinessCapability",
        secondary=technical_capability_business_mapping,
        backref="technical_capabilities",
        lazy="dynamic",
    )

    # CURRENT: maps to UnifiedCapability (new standard)
    unified_capabilities = relationship(
        "UnifiedCapability",
        secondary=technical_capability_unified_mapping,
        backref="technical_capabilities",
        lazy="dynamic",
    )

    applications = relationship(
        "ApplicationComponent",
        secondary=application_technical_capability_mapping,
        backref="technical_capabilities",
        lazy="dynamic",
    )

    apqc_processes = relationship(
        "APQCProcess",
        secondary=technical_capability_apqc_mapping,
        backref="technical_capabilities",
        lazy="dynamic",
    )

    vendor_products = relationship(
        "VendorProduct",
        secondary=technical_capability_vendor_mapping,
        backref="technical_capabilities",
        lazy="dynamic",
    )

    def __repr__(self):
        return f"<TechnicalCapability {self.code}: {self.name}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "acm_domain": self.acm_domain,
            "level": self.level,
            "level_number": self.level_number,
            "parent_id": self.parent_id,
            "capability_type": self.capability_type,
            "technology_patterns": self.technology_patterns,
            "common_technologies": self.common_technologies,
            "industry_maturity": self.industry_maturity,
            "complexity": self.complexity,
            "is_differentiating": self.is_differentiating,
            "is_foundational": self.is_foundational,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def get_full_path(self) -> str:
        """Get full hierarchy path (e.g., 'USER-EXPERIENCE > Web Interfaces > Responsive Design')"""
        path_parts = [self.name]
        current = self.parent
        while current:
            path_parts.insert(0, current.name)
            current = current.parent
        return " > ".join(path_parts)

    def get_domain_description(self) -> str:
        """Get description for this capability's ACM domain"""
        return ACMDomain.DOMAIN_DESCRIPTIONS.get(self.acm_domain, "")

    def get_related_apqc_categories(self) -> List[str]:
        """Get APQC PCF categories related to this capability's domain"""
        return ACMDomain.APQC_MAPPINGS.get(self.acm_domain, [])

    @classmethod
    def get_by_domain(cls, domain: str) -> List["TechnicalCapability"]:
        """Get all capabilities for a specific ACM domain"""
        return cls.query.filter_by(acm_domain=domain).order_by(cls.code).all()

    @classmethod
    def get_hierarchy(cls, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get hierarchical structure of capabilities"""
        query = cls.query.filter_by(parent_id=None)
        if domain:
            query = query.filter_by(acm_domain=domain)

        roots = query.order_by(cls.acm_domain, cls.code).all()

        def build_tree(capability):
            node = capability.to_dict()
            node["children"] = [build_tree(child) for child in capability.children]
            return node

        return [build_tree(root) for root in roots]

    @classmethod
    def get_domain_summary(cls) -> Dict[str, Dict[str, Any]]:
        """Get summary statistics for each ACM domain"""
        summary = {}
        for domain in ACMDomain.ALL_DOMAINS:
            caps = cls.query.filter_by(acm_domain=domain).all()
            summary[domain] = {
                "total_capabilities": len(caps),
                "by_level": {
                    "L1": len([c for c in caps if c.level == "L1"]),
                    "L2": len([c for c in caps if c.level == "L2"]),
                    "L3": len([c for c in caps if c.level == "L3"]),
                    "L4": len([c for c in caps if c.level == "L4"]),
                },
                "description": ACMDomain.DOMAIN_DESCRIPTIONS.get(domain, ""),
            }
        return summary
