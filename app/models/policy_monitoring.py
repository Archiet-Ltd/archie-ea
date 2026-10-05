"""
Policy Monitoring Models

Comprehensive architecture policy monitoring system for compliance tracking,
violation detection, and exemption management. Supports enterprise governance
and architecture standards enforcement.

Features:
- Architecture policy definitions with rule-based evaluation
- Policy violation detection and tracking
- Compliance status aggregation
- Exemption workflow management
"""

from datetime import datetime
from typing import Any, Dict, List, Optional  # dead-code-ok

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from .. import db
from .mixins import TenantMixin


class ArchitecturePolicy(TenantMixin, db.Model):
    """
    Architecture Policy Model

    Defines architecture policies for enterprise governance.
    Supports technology, security, data, integration, and governance policies.
    """

    __tablename__ = "architecture_policies"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Policy identity
    name = Column(String(256), nullable=False, index=True)
    description = Column(Text)

    # Policy classification
    category = Column(String(50), index=True)  # technology, security, data, integration, governance
    policy_type = Column(String(50))  # mandatory, recommended, deprecated
    scope = Column(String(50))  # enterprise, domain, application

    # Rule definition (JSON structure defining the rule logic)
    rule_definition = Column(Text)

    # Severity and enforcement
    severity = Column(String(20))  # critical, high, medium, low
    enforcement_level = Column(String(20))  # blocking, warning, informational

    # Effective period
    effective_date = Column(Date)
    expiry_date = Column(Date, nullable=True)

    # Ownership
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, index=True)

    # Exemption settings
    exemption_allowed = Column(Boolean, default=True)
    exemption_approval_required = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = relationship("User", foreign_keys=[owner_id], backref="owned_policies")
    violations = relationship(
        "PolicyViolation", back_populates="policy", cascade="all, delete-orphan"
    )

    # Indexes for performance
    __table_args__ = (
        Index("idx_policy_category_active", "category", "is_active"),
        Index("idx_policy_severity", "severity", "enforcement_level"),
        Index("idx_policy_effective", "effective_date", "expiry_date"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<ArchitecturePolicy {self.name}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "policy_type": self.policy_type,
            "scope": self.scope,
            "rule_definition": self.rule_definition,
            "severity": self.severity,
            "enforcement_level": self.enforcement_level,
            "effective_date": self.effective_date.isoformat() if self.effective_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "owner_id": self.owner_id,
            "is_active": self.is_active,
            "exemption_allowed": self.exemption_allowed,
            "exemption_approval_required": self.exemption_approval_required,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def is_effective(self) -> bool:
        """Check if policy is currently effective"""
        today = datetime.utcnow().date()
        if not self.is_active:
            return False
        if self.effective_date and self.effective_date > today:
            return False
        if self.expiry_date and self.expiry_date < today:
            return False
        return True


class PolicyViolation(TenantMixin, db.Model):
    """
    Policy Violation Model

    Tracks detected violations of architecture policies.
    Supports violation lifecycle management and remediation tracking.
    """

    __tablename__ = "policy_violations"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Link to policy
    policy_id = Column(Integer, ForeignKey("architecture_policies.id"), nullable=False, index=True)

    # Entity that violated the policy
    entity_type = Column(String(50), index=True)  # application, capability, technology, integration
    entity_id = Column(Integer, index=True)
    entity_name = Column(String(256))

    # Violation details
    violation_details = Column(Text)
    severity = Column(String(20))  # inherited from policy or overridden

    # Violation status
    status = Column(
        String(50), default="open", index=True
    )  # open, acknowledged, remediated, exempted, false_positive

    # Detection timestamp
    detected_at = Column(DateTime, default=datetime.utcnow)

    # Acknowledgement
    acknowledged_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    # Remediation
    remediated_at = Column(DateTime, nullable=True)
    remediation_notes = Column(Text)

    # Exemption
    exemption_reason = Column(Text, nullable=True)
    exemption_approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    exemption_expiry = Column(Date, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    policy = relationship("ArchitecturePolicy", back_populates="violations")
    acknowledger = relationship(
        "User", foreign_keys=[acknowledged_by], backref="acknowledged_violations"
    )
    exemption_approver = relationship(
        "User", foreign_keys=[exemption_approved_by], backref="approved_exemptions"
    )
    exemption_requests = relationship(
        "PolicyExemption", back_populates="violation", cascade="all, delete-orphan"
    )

    # Indexes for performance
    __table_args__ = (
        Index("idx_violation_policy_status", "policy_id", "status"),
        Index("idx_violation_entity", "entity_type", "entity_id"),
        Index("idx_violation_severity_status", "severity", "status"),
        Index("idx_violation_detected", "detected_at"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<PolicyViolation {self.id} - {self.entity_name}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "policy_id": self.policy_id,
            "policy_name": self.policy.name if self.policy else None,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "violation_details": self.violation_details,
            "severity": self.severity,
            "status": self.status,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "remediated_at": self.remediated_at.isoformat() if self.remediated_at else None,
            "remediation_notes": self.remediation_notes,
            "exemption_reason": self.exemption_reason,
            "exemption_approved_by": self.exemption_approved_by,
            "exemption_expiry": self.exemption_expiry.isoformat()
            if self.exemption_expiry
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ComplianceStatus(TenantMixin, db.Model):
    """
    Compliance Status Model

    Aggregated compliance tracking for entities.
    Provides compliance metrics and risk scoring.
    """

    __tablename__ = "compliance_status"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Entity identification
    entity_type = Column(String(50), index=True)  # application, domain, enterprise
    entity_id = Column(Integer, nullable=True, index=True)  # null for enterprise-level
    entity_name = Column(String(256))

    # Compliance metrics
    total_policies = Column(Integer, default=0)
    compliant_count = Column(Integer, default=0)
    violation_count = Column(Integer, default=0)
    exemption_count = Column(Integer, default=0)

    # Calculated compliance percentage
    compliance_percentage = Column(Float, default=0.0)

    # Scan information
    last_scan_at = Column(DateTime)

    # Risk assessment (0 - 100 based on violation severity)
    risk_score = Column(Float, default=0.0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Indexes for performance
    __table_args__ = (
        Index("idx_compliance_entity", "entity_type", "entity_id"),
        Index("idx_compliance_risk", "risk_score"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<ComplianceStatus {self.entity_type}:{self.entity_name}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "total_policies": self.total_policies,
            "compliant_count": self.compliant_count,
            "violation_count": self.violation_count,
            "exemption_count": self.exemption_count,
            "compliance_percentage": self.compliance_percentage,
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "risk_score": self.risk_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def calculate_compliance_percentage(self):
        """Calculate compliance percentage based on counts"""
        if self.total_policies == 0:
            self.compliance_percentage = 100.0
        else:
            compliant = self.compliant_count + self.exemption_count
            self.compliance_percentage = (compliant / self.total_policies) * 100


class PolicyExemption(db.Model):
    """
    Policy Exemption Model

    Manages exemption requests and approvals for policy violations.
    Supports workflow for exemption lifecycle management.
    """

    __tablename__ = "policy_exemptions"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)

    # Link to violation
    violation_id = Column(Integer, ForeignKey("policy_violations.id"), nullable=False, index=True)

    # Request details
    requested_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow)

    # Justification
    reason = Column(Text, nullable=False)
    business_justification = Column(Text)
    mitigation_plan = Column(Text)

    # Exemption period
    expiry_date = Column(Date)

    # Approval status
    status = Column(String(50), default="pending", index=True)  # pending, approved, rejected

    # Approval details
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    violation = relationship("PolicyViolation", back_populates="exemption_requests")
    requester = relationship("User", foreign_keys=[requested_by], backref="exemption_requests")
    approver = relationship("User", foreign_keys=[approved_by], backref="exemption_approvals")

    # Indexes for performance
    __table_args__ = (
        Index("idx_exemption_status", "status"),
        Index("idx_exemption_violation", "violation_id"),
        Index("idx_exemption_expiry", "expiry_date"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<PolicyExemption {self.id} - {self.status}>"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "violation_id": self.violation_id,
            "requested_by": self.requested_by,
            "requester_name": self.requester.full_name()
            if self.requester and hasattr(self.requester, "full_name")
            else None,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "reason": self.reason,
            "business_justification": self.business_justification,
            "mitigation_plan": self.mitigation_plan,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "status": self.status,
            "approved_by": self.approved_by,
            "approver_name": self.approver.full_name()
            if self.approver and hasattr(self.approver, "full_name")
            else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def is_expired(self) -> bool:
        """Check if exemption has expired"""
        if not self.expiry_date:
            return False
        return self.expiry_date < datetime.utcnow().date()


class MonitoringBaseline(TenantMixin, db.Model):
    """
    Architecture Monitoring Baseline

    Persists architecture baseline snapshots so drift detection data
    survives application restarts. Tenant-scoped: a baseline belongs to one
    organisation and is never visible to another.
    """

    __tablename__ = "monitoring_baselines"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    baseline_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    created_by = Column(String(128), nullable=True)
    is_active = Column(Boolean, default=False, index=True)
    snapshot_data = Column(Text, nullable=False)  # JSON blob of all snapshots
    checksum = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "baseline_id": self.baseline_id,
            "name": self.name,
            "description": self.description,
            "created_by": self.created_by,
            "is_active": self.is_active,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MonitoringAlert(TenantMixin, db.Model):
    """
    Architecture Monitoring Alert

    Persists architecture drift alerts so alert history and acknowledgement
    state survives application restarts. Tenant-scoped: an alert belongs to
    one organisation and is never visible to another.
    """

    __tablename__ = "monitoring_alerts"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    alert_id = Column(String(64), unique=True, nullable=False, index=True)
    alert_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    title = Column(String(512), nullable=False)
    description = Column(Text)
    affected_element_id = Column(Integer, nullable=True)
    affected_element_type = Column(String(50), nullable=True)
    affected_element_name = Column(String(256), nullable=True)
    baseline_value = Column(Text, nullable=True)  # JSON
    current_value = Column(Text, nullable=True)  # JSON
    delta = Column(Float, nullable=True)
    recommended_action = Column(Text, nullable=True)
    acknowledged = Column(Boolean, default=False)
    acknowledged_by = Column(String(128), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    alert_metadata = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "affected_element_id": self.affected_element_id,
            "affected_element_type": self.affected_element_type,
            "affected_element_name": self.affected_element_name,
            "delta": self.delta,
            "recommended_action": self.recommended_action,
            "acknowledged": self.acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat()
            if self.acknowledged_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
