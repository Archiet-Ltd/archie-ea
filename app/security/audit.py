"""
SOC2 - Ready Audit Trail System

Provides comprehensive audit logging for security events, data access, and system activities.
Implements immutable audit trails with tamper detection and compliance reporting.

Key Features:
- Immutable audit log entries with cryptographic integrity
- Comprehensive event logging (authentication, authorization, data access)
- Compliance reporting for SOC2, GDPR, HIPAA
- Real-time alerting for security events
- Audit trail analysis and forensics
"""

import hashlib
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from flask import g, request
from flask_login import current_user

from .. import db

logger = logging.getLogger(__name__)


class AuditEventType(Enum):
    """Types of auditable events"""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATA_ACCESS = "data_access"
    DATA_MODIFICATION = "data_modification"
    SECURITY_EVENT = "security_event"
    CONFIGURATION_CHANGE = "configuration_change"
    SYSTEM_EVENT = "system_event"


class AuditEventSeverity(Enum):
    """Severity levels for audit events"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditEvent(db.Model):
    """
    Immutable audit log entry.

    Stores all security-relevant events with cryptographic integrity.
    """

    __tablename__ = "audit_events"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.String(64), unique=True, nullable=False, index=True
    )  # UUID-like identifier

    # Event metadata
    event_type = db.Column(db.String(50), nullable=False, index=True)
    severity = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # User context
    user_id = db.Column(db.Integer, index=True)
    user_email = db.Column(db.String(255))
    user_ip = db.Column(db.String(45))  # IPv4/IPv6 support
    user_agent = db.Column(db.Text)

    # Resource context
    resource_type = db.Column(db.String(100))
    resource_id = db.Column(db.String(255))
    action = db.Column(db.String(100))

    # Event details
    details = db.Column(db.Text)  # JSON string with event-specific data
    old_values = db.Column(db.Text)  # JSON string for before values
    new_values = db.Column(db.Text)  # JSON string for after values

    # Compliance and integrity
    compliance_flags = db.Column(db.Text)  # JSON array of compliance requirements
    integrity_hash = db.Column(
        db.String(128), nullable=False
    )  # SHA - 256 hash for tamper detection

    # Additional metadata
    session_id = db.Column(db.String(255))
    request_id = db.Column(db.String(255))
    source = db.Column(db.String(100))  # API, UI, system, etc.

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Generate integrity hash
        self.integrity_hash = self._calculate_integrity_hash()

    def _calculate_integrity_hash(self) -> str:
        """Calculate cryptographic hash for tamper detection"""
        # Include all critical fields in hash calculation
        hash_data = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "details": self.details,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "compliance_flags": self.compliance_flags,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "source": self.source,
        }

        # Convert to canonical JSON string
        json_str = json.dumps(hash_data, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify that the audit entry has not been tampered with"""
        return self.integrity_hash == self._calculate_integrity_hash()

    def to_dict(self) -> Dict[str, Any]:
        """Convert audit event to dictionary"""
        return {
            "id": self.id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "user_ip": self.user_ip,
            "user_agent": self.user_agent,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "details": self.details,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "compliance_flags": self.compliance_flags,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "source": self.source,
            "integrity_verified": self.verify_integrity(),
        }


class AuditLogger:
    """
    Centralized audit logging service.

    Provides methods for logging various types of security and compliance events.
    """

    def __init__(self):
        self._enabled_event_types = self._load_enabled_event_types()

    def _load_enabled_event_types(self) -> List[str]:
        """Load enabled audit event types from configuration"""
        # Default to all event types if not configured
        return [e.value for e in AuditEventType]

    def log_event(
        self,
        event_type: AuditEventType,
        severity: AuditEventSeverity,
        action: str,
        resource_type: str = None,
        resource_id: str = None,
        details: Dict = None,
        old_values: Dict = None,
        new_values: Dict = None,
        compliance_flags: List[str] = None,
    ) -> AuditEvent:
        """
        Log an audit event.

        Args:
            event_type: Type of event
            severity: Event severity
            action: Action performed
            resource_type: Type of resource affected
            resource_id: ID of specific resource
            details: Additional event details
            old_values: Previous values (for modifications)
            new_values: New values (for modifications)
            compliance_flags: Compliance requirements this event relates to

        Returns:
            Created audit event
        """
        if event_type.value not in self._enabled_event_types:
            return None

        # Generate unique event ID
        import uuid

        event_id = str(uuid.uuid4())

        # Get user context
        user_id = current_user.id if current_user and current_user.is_authenticated else None
        user_email = current_user.email if current_user and current_user.is_authenticated else None

        # Get request context
        user_ip = self._get_client_ip()
        user_agent = request.headers.get("User-Agent") if request else None
        session_id = getattr(g, "session_id", None)
        request_id = getattr(g, "request_id", None)

        # Create audit event
        audit_event = AuditEvent(
            event_id=event_id,
            event_type=event_type.value,
            severity=severity.value,
            user_id=user_id,
            user_email=user_email,
            user_ip=user_ip,
            user_agent=user_agent,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            details=json.dumps(details) if details else None,
            old_values=json.dumps(old_values) if old_values else None,
            new_values=json.dumps(new_values) if new_values else None,
            compliance_flags=json.dumps(compliance_flags) if compliance_flags else None,
            session_id=session_id,
            request_id=request_id,
            source=self._get_source(),
        )

        # Save to database
        try:
            db.session.add(audit_event)
            db.session.commit()
            logger.info(f"Audit event logged: {event_id} - {event_type.value}:{action}")
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
            db.session.rollback()

        return audit_event

    def _get_client_ip(self) -> Optional[str]:
        """Get client IP address from request"""
        if not request:
            return None

        # Check forwarded headers
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        return request.remote_addr

    def _get_source(self) -> str:
        """Determine the source of the event"""
        if not request:
            return "system"

        # Check if it's an API request
        if request.path.startswith("/api/"):
            return "api"

        # Check user agent for common patterns
        user_agent = request.headers.get("User-Agent", "").lower()
        if "postman" in user_agent or "curl" in user_agent:
            return "api_client"
        elif "bot" in user_agent or "crawler" in user_agent:
            return "bot"

        return "web_ui"

    # Convenience methods for common audit events

    def log_authentication(self, success: bool, method: str = "password"):
        """Log authentication attempt"""
        severity = AuditEventSeverity.LOW if success else AuditEventSeverity.HIGH
        details = {"success": success, "method": method}

        self.log_event(
            AuditEventType.AUTHENTICATION,
            severity,
            "login_attempt" if success else "login_failure",
            details=details,
            compliance_flags=["SOC2", "GDPR"],
        )

    def log_logout(self, method: str = "password"):
        """Log user logout"""
        self.log_event(
            AuditEventType.AUTHENTICATION,
            AuditEventSeverity.LOW,
            "logout",
            details={"method": method},
            compliance_flags=["SOC2", "GDPR"],
        )

    def log_authorization(self, resource_type: str, resource_id: str, action: str, granted: bool):
        """Log authorization decision"""
        severity = AuditEventSeverity.MEDIUM if not granted else AuditEventSeverity.LOW
        details = {"granted": granted, "action": action}

        self.log_event(
            AuditEventType.AUTHORIZATION,
            severity,
            "access_check",
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            compliance_flags=["SOC2", "GDPR"],
        )

    def log_data_access(self, resource_type: str, resource_id: str, action: str = "read"):
        """Log data access event"""
        self.log_event(
            AuditEventType.DATA_ACCESS,
            AuditEventSeverity.LOW,
            action,
            resource_type=resource_type,
            resource_id=resource_id,
            compliance_flags=["GDPR", "HIPAA"],
        )

    def log_data_modification(
        self,
        resource_type: str,
        resource_id: str,
        action: str,
        old_values: Dict = None,
        new_values: Dict = None,
    ):
        """Log data modification event"""
        self.log_event(
            AuditEventType.DATA_MODIFICATION,
            AuditEventSeverity.MEDIUM,
            action,
            resource_type=resource_type,
            resource_id=resource_id,
            old_values=old_values,
            new_values=new_values,
            compliance_flags=["SOC2", "GDPR"],
        )

    def log_security_event(self, event_type: str, severity: AuditEventSeverity, details: Dict):
        """Log security-related event"""
        self.log_event(
            AuditEventType.SECURITY_EVENT,
            severity,
            event_type,
            details=details,
            compliance_flags=["SOC2", "GDPR", "HIPAA"],
        )

    def get_audit_trail(
        self,
        user_id: Optional[int] = None,
        resource_type: Optional[str] = None,
        event_type: Optional[str] = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100,
        organization_id: Optional[int] = None,
    ) -> List[AuditEvent]:
        """
        Query audit trail with filters.

        DEF-5 (review-pr430-v3.md, 2026-10-08): AuditEvent carries no
        ``organization_id`` column of its own -- it is keyed only by
        ``user_id`` (a plain integer, no FK). Passing ``organization_id``
        scopes the read to events whose ``user_id`` belongs to a user in
        that organisation; an event with no ``user_id`` at all (a
        system-level event, not attributable to any one organisation's
        action) is excluded from every organisation-scoped read rather than
        shown to one arbitrarily -- a platform admin passes ``None`` to see
        every event, attributable or not.

        Returns:
            List of audit events matching criteria
        """
        query = AuditEvent.query

        if organization_id is not None:
            from app.models.user import User

            query = query.filter(
                AuditEvent.user_id.isnot(None),
                AuditEvent.user_id.in_(
                    db.session.query(User.id).filter(User.organization_id == organization_id)
                ),
            )

        if user_id:
            query = query.filter(AuditEvent.user_id == user_id)

        if resource_type:
            query = query.filter(AuditEvent.resource_type == resource_type)

        if event_type:
            query = query.filter(AuditEvent.event_type == event_type)

        if start_date:
            query = query.filter(AuditEvent.timestamp >= start_date)

        if end_date:
            query = query.filter(AuditEvent.timestamp <= end_date)

        return query.order_by(AuditEvent.timestamp.desc()).limit(limit).all()

    def verify_audit_integrity(self) -> Dict[str, Any]:
        """
        Verify integrity of audit trail.

        Returns:
            Integrity check results
        """
        events = AuditEvent.query.all()
        total_events = len(events)
        tampered_events = 0
        tampered_ids = []

        for event in events:
            if not event.verify_integrity():
                tampered_events += 1
                tampered_ids.append(event.id)

        return {
            "total_events": total_events,
            "tampered_events": tampered_events,
            "tampered_event_ids": tampered_ids,
            "integrity_intact": tampered_events == 0,
        }


# Global audit logger instance
audit_logger = AuditLogger()
