"""
AI Audit Trail

Provides comprehensive audit trail system for AI interactions.
"""

import logging
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import threading

from flask import current_app, g, request

logger = logging.getLogger(__name__)

class AuditEventType(Enum):
    """Audit event types."""
    AI_REQUEST = "ai_request"
    AI_RESPONSE = "ai_response"
    AI_ERROR = "ai_error"
    DATA_CLASSIFICATION = "data_classification"
    COST_TRACKING = "cost_tracking"
    FEATURE_FLAG_CHECK = "feature_flag_check"
    DATA_FILTERING = "data_filtering"
    USER_CONSENT = "user_consent"
    POLICY_VIOLATION = "policy_violation"
    SECURITY_INCIDENT = "security_incident"

class AuditSeverity(Enum):
    """Audit event severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

@dataclass
class AuditEvent:
    """Represents an audit event."""
    id: str
    event_type: AuditEventType
    severity: AuditSeverity
    user_id: Optional[str]
    session_id: Optional[str]
    ip_address: str
    user_agent: Optional[str]
    feature: Optional[str]
    action: Optional[str]
    resource: Optional[str]
    details: Dict[str, Any]
    timestamp: datetime
    correlation_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert audit event to dictionary."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['event_type'] = self.event_type.value
        data['severity'] = self.severity.value
        return data
    
    def to_json(self) -> str:
        """Convert audit event to JSON string."""
        return json.dumps(self.to_dict(), default=str)

class AIAuditTrail:
    """
    Manages audit trail for AI interactions and compliance.
    """
    
    def __init__(self):
        """Initialize the AI audit trail system."""
        self._events = []  # In-memory storage (in production, use database)
        self._correlation_map = {}  # correlation_id -> event_ids
        self._lock = threading.Lock()
        
        # Initialize retention policies
        self._retention_days = current_app.config.get('AI_AUDIT_RETENTION_DAYS', 90)
        self._max_events = current_app.config.get('AI_AUDIT_MAX_EVENTS', 10000)
    
    def log_event(self, event_type: AuditEventType, severity: AuditSeverity = AuditSeverity.INFO,
                  feature: Optional[str] = None, action: Optional[str] = None,
                  resource: Optional[str] = None, details: Optional[Dict[str, Any]] = None,
                  correlation_id: Optional[str] = None):
        """
        Log an audit event.
        
        Args:
            event_type: Type of audit event
            severity: Event severity level
            feature: AI feature involved
            action: Action performed
            resource: Resource being accessed
            details: Additional event details
            correlation_id: Optional correlation ID for related events
        """
        # Get request context
        user_id = getattr(g, 'user_id', None) if hasattr(g, 'user_id') else None
        session_id = getattr(g, 'session_id', None) if hasattr(g, 'session_id') else None
        ip_address = getattr(request, 'remote_addr', 'unknown') if request else 'unknown'
        user_agent = getattr(request, 'user_agent', {}).get('string', 'unknown') if request and hasattr(request, 'user_agent') else 'unknown'
        
        # Generate correlation ID if not provided
        if not correlation_id:
            correlation_id = self._generate_correlation_id()
        
        # Create audit event
        event = AuditEvent(
            id=self._generate_event_id(),
            event_type=event_type,
            severity=severity,
            user_id=user_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            feature=feature,
            action=action,
            resource=resource,
            details=details or {},
            timestamp=datetime.utcnow(),
            correlation_id=correlation_id
        )
        
        with self._lock:
            self._events.append(event)
            
            # Update correlation map
            if correlation_id not in self._correlation_map:
                self._correlation_map[correlation_id] = []
            self._correlation_map[correlation_id].append(event.id)
            
            # Cleanup old events
            self._cleanup_old_events()
        
        # Log to system logger for critical events
        if severity in [AuditSeverity.ERROR, AuditSeverity.CRITICAL]:
            log_level = {
                AuditSeverity.INFO: logger.info,
                AuditSeverity.WARNING: logger.warning,
                AuditSeverity.ERROR: logger.error,
                AuditSeverity.CRITICAL: logger.critical
            }.get(severity, logger.info)
            
            log_level(f"AI Audit Event: {event_type.value} - {feature} - {user_id} - {details}")
        
        # Check for policy violations
        if event_type == AuditEventType.POLICY_VIOLATION:
            self._handle_policy_violation(event)
    
    def log_ai_request(self, feature: str, prompt: str, user_id: Optional[str] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Log an AI request event.
        
        Args:
            feature: AI feature name
            prompt: User prompt/input
            user_id: Optional user ID
            metadata: Additional metadata
            
        Returns:
            Correlation ID for the request
        """
        correlation_id = self._generate_correlation_id()
        
        # Hash sensitive prompt data for privacy
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        
        details = {
            'prompt_length': len(prompt),
            'prompt_hash': prompt_hash,
            'metadata': metadata or {}
        }
        
        self.log_event(
            event_type=AuditEventType.AI_REQUEST,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='request',
            details=details,
            correlation_id=correlation_id
        )
        
        return correlation_id
    
    def log_ai_response(self, correlation_id: str, feature: str, response: str,
                       tokens_used: Optional[int] = None, cost: Optional[float] = None,
                       metadata: Optional[Dict[str, Any]] = None):
        """
        Log an AI response event.
        
        Args:
            correlation_id: Request correlation ID
            feature: AI feature name
            response: AI response
            tokens_used: Number of tokens used
            cost: Cost of the request
            metadata: Additional metadata
        """
        # Hash sensitive response data for privacy
        response_hash = hashlib.sha256(response.encode()).hexdigest()
        
        details = {
            'response_length': len(response),
            'response_hash': response_hash,
            'tokens_used': tokens_used,
            'cost': cost,
            'metadata': metadata or {}
        }
        
        self.log_event(
            event_type=AuditEventType.AI_RESPONSE,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='response',
            details=details,
            correlation_id=correlation_id
        )
    
    def log_ai_error(self, correlation_id: str, feature: str, error: str,
                    error_type: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        """
        Log an AI error event.
        
        Args:
            correlation_id: Request correlation ID
            feature: AI feature name
            error: Error message
            error_type: Type of error
            metadata: Additional metadata
        """
        details = {
            'error_message': error,
            'error_type': error_type,
            'metadata': metadata or {}
        }
        
        self.log_event(
            event_type=AuditEventType.AI_ERROR,
            severity=AuditSeverity.ERROR,
            feature=feature,
            action='error',
            details=details,
            correlation_id=correlation_id
        )
    
    def log_data_classification(self, data: str, classification_result: Dict[str, Any],
                              feature: Optional[str] = None):
        """
        Log a data classification event.
        
        Args:
            data: Data that was classified
            classification_result: Classification analysis result
            feature: AI feature involved
        """
        details = {
            'data_length': len(data),
            'classification': classification_result.get('classification'),
            'risk': classification_result.get('risk'),
            'patterns_found': len(classification_result.get('patterns_found', [])),
            'safe_for_ai': classification_result.get('safe_for_ai'),
            'classification_result': classification_result
        }
        
        self.log_event(
            event_type=AuditEventType.DATA_CLASSIFICATION,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='classify',
            details=details
        )
    
    def log_cost_tracking(self, feature: str, cost: float, user_id: Optional[str] = None,
                         budget_status: Optional[str] = None):
        """
        Log a cost tracking event.
        
        Args:
            feature: AI feature name
            cost: Cost incurred
            user_id: Optional user ID
            budget_status: Budget status information
        """
        details = {
            'cost': cost,
            'budget_status': budget_status,
            'currency': 'USD'
        }
        
        self.log_event(
            event_type=AuditEventType.COST_TRACKING,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='cost',
            details=details
        )
    
    def log_feature_flag_check(self, feature: str, flag_status: str, decision: str,
                              user_id: Optional[str] = None):
        """
        Log a feature flag check event.
        
        Args:
            feature: AI feature name
            flag_status: Feature flag status
            decision: Decision made (allow/deny/degrade)
            user_id: Optional user ID
        """
        details = {
            'flag_status': flag_status,
            'decision': decision
        }
        
        self.log_event(
            event_type=AuditEventType.FEATURE_FLAG_CHECK,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='flag_check',
            details=details
        )
    
    def log_data_filtering(self, original_data: str, filtered_data: str, mode: str,
                          patterns_filtered: int, feature: Optional[str] = None):
        """
        Log a data filtering event.
        
        Args:
            original_data: Original data before filtering
            filtered_data: Data after filtering
            mode: Filtering mode used
            patterns_filtered: Number of patterns filtered
            feature: AI feature involved
        """
        details = {
            'original_length': len(original_data),
            'filtered_length': len(filtered_data),
            'filtering_mode': mode,
            'patterns_filtered': patterns_filtered,
            'reduction_ratio': len(filtered_data) / len(original_data) if original_data else 0
        }
        
        self.log_event(
            event_type=AuditEventType.DATA_FILTERING,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='filter',
            details=details
        )
    
    def log_user_consent(self, feature: str, consent_given: bool, consent_type: str,
                        user_id: Optional[str] = None):
        """
        Log a user consent event.
        
        Args:
            feature: AI feature name
            consent_given: Whether consent was given
            consent_type: Type of consent
            user_id: Optional user ID
        """
        details = {
            'consent_given': consent_given,
            'consent_type': consent_type
        }
        
        self.log_event(
            event_type=AuditEventType.USER_CONSENT,
            severity=AuditSeverity.INFO,
            feature=feature,
            action='consent',
            details=details
        )
    
    def log_policy_violation(self, feature: str, violation_type: str, severity: AuditSeverity,
                           details: Optional[Dict[str, Any]] = None):
        """
        Log a policy violation event.
        
        Args:
            feature: AI feature name
            violation_type: Type of policy violation
            severity: Violation severity
            details: Additional details
        """
        violation_details = {
            'violation_type': violation_type,
            'violation_details': details or {}
        }
        
        self.log_event(
            event_type=AuditEventType.POLICY_VIOLATION,
            severity=severity,
            feature=feature,
            action='violation',
            details=violation_details
        )
    
    def get_events(self, limit: int = 100, event_type: Optional[AuditEventType] = None,
                   severity: Optional[AuditSeverity] = None, user_id: Optional[str] = None,
                   feature: Optional[str] = None, correlation_id: Optional[str] = None,
                   time_delta: Optional[timedelta] = None) -> List[Dict[str, Any]]:
        """
        Get audit events with filtering options.
        
        Args:
            limit: Maximum number of events to return
            event_type: Filter by event type
            severity: Filter by severity
            user_id: Filter by user ID
            feature: Filter by feature
            correlation_id: Filter by correlation ID
            time_delta: Filter by time range
            
        Returns:
            List of audit events
        """
        with self._lock:
            events = self._events.copy()
        
        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        if severity:
            events = [e for e in events if e.severity == severity]
        
        if user_id:
            events = [e for e in events if e.user_id == user_id]
        
        if feature:
            events = [e for e in events if e.feature == feature]
        
        if correlation_id:
            events = [e for e in events if e.correlation_id == correlation_id]
        
        if time_delta:
            cutoff_time = datetime.utcnow() - time_delta
            events = [e for e in events if e.timestamp > cutoff_time]
        
        # Sort by timestamp (newest first) and limit
        events.sort(key=lambda x: x.timestamp, reverse=True)
        
        return [event.to_dict() for event in events[:limit]]
    
    def get_correlation_events(self, correlation_id: str) -> List[Dict[str, Any]]:
        """
        Get all events for a specific correlation ID.
        
        Args:
            correlation_id: Correlation ID to retrieve
            
        Returns:
            List of related events
        """
        return self.get_events(correlation_id=correlation_id, limit=100)
    
    def get_audit_summary(self, time_delta: timedelta = timedelta(days=1)) -> Dict[str, Any]:
        """
        Get audit summary statistics.
        
        Args:
            time_delta: Time period to analyze
            
        Returns:
            Audit summary statistics
        """
        events = self.get_events(limit=10000, time_delta=time_delta)
        
        # Event counts by type
        event_counts = {}
        for event in events:
            event_type = event['event_type']
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        # Event counts by severity
        severity_counts = {}
        for event in events:
            severity = event['severity']
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        # Feature usage
        feature_counts = {}
        for event in events:
            feature = event.get('feature', 'unknown')
            feature_counts[feature] = feature_counts.get(feature, 0) + 1
        
        # User activity
        user_counts = {}
        for event in events:
            user_id = event.get('user_id')
            if user_id:
                user_counts[user_id] = user_counts.get(user_id, 0) + 1
        
        # Top users
        top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Policy violations
        violations = [e for e in events if e['event_type'] == 'policy_violation']
        
        return {
            'time_period': f"{time_delta.days} days",
            'total_events': len(events),
            'events_by_type': event_counts,
            'events_by_severity': severity_counts,
            'feature_usage': feature_counts,
            'user_activity': user_counts,
            'top_users': [{'user': user, 'events': count} for user, count in top_users],
            'policy_violations': len(violations),
            'violation_rate': len(violations) / len(events) if events else 0,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        timestamp = str(int(datetime.utcnow().timestamp()))
        data = f"audit_{timestamp}_{threading.get_ident()}"
        return hashlib.md5(data.encode(), usedforsecurity=False).hexdigest()[:16]
    
    def _generate_correlation_id(self) -> str:
        """Generate unique correlation ID."""
        timestamp = str(int(datetime.utcnow().timestamp()))
        data = f"corr_{timestamp}_{threading.get_ident()}"
        return hashlib.md5(data.encode(), usedforsecurity=False).hexdigest()[:12]
    
    def _cleanup_old_events(self):
        """Clean up old audit events based on retention policy."""
        cutoff_time = datetime.utcnow() - timedelta(days=self._retention_days)
        
        # Remove events older than retention period
        original_length = len(self._events)
        self._events = [e for e in self._events if e.timestamp > cutoff_time]
        
        # Remove excess events if over limit
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        
        # Clean up correlation map for removed events
        self._correlation_map = {
            corr_id: [eid for eid in event_ids if eid in event_ids]
            for corr_id, event_ids in self._correlation_map.items()
            if any(eid in event_ids for eid in event_ids)
        }
        
        cleaned_count = original_length - len(self._events)
        if cleaned_count > 0:
            logger.debug(f"Cleaned up {cleaned_count} old audit events")
    
    def _handle_policy_violation(self, event: AuditEvent):
        """Handle policy violation events."""
        try:
            from app.monitoring.alerting_service import alerting_service, AlertSeverity
            
            # Map audit severity to alert severity
            alert_severity = {
                AuditSeverity.INFO: AlertSeverity.INFO,
                AuditSeverity.WARNING: AlertSeverity.WARNING,
                AuditSeverity.ERROR: AlertSeverity.WARNING,
                AuditSeverity.CRITICAL: AlertSeverity.CRITICAL
            }.get(event.severity, AlertSeverity.WARNING)
            
            violation_type = event.details.get('violation_type', 'unknown')
            
            alerting_service.create_manual_alert(
                name=f"ai_policy_violation_{violation_type}",
                severity=alert_severity,
                message=f"AI Policy Violation: {violation_type} in feature {event.feature}",
                source='ai_audit_trail',
                metadata={
                    'event_id': event.id,
                    'violation_type': violation_type,
                    'feature': event.feature,
                    'user_id': event.user_id,
                    'correlation_id': event.correlation_id
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to handle policy violation: {e}")

# Global AI audit trail instance
ai_audit_trail = AIAuditTrail()
