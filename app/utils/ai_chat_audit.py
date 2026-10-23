"""
AI Chat Audit Utility

Provides comprehensive audit logging for all AI Chat operations.
Captures user actions, AI decisions, data modifications, and system events.
"""

import json
import logging
from typing import Dict, List, Optional

from flask import request

from app import db
from app.models.ai_chat_audit_log import AIChatAuditLog, AuditEventType

logger = logging.getLogger(__name__)


class AIChatAuditLogger:
    """
    Comprehensive audit logger for AI Chat operations.
    
    Provides methods to log:
    - Chat messages and responses
    - CRUD operations with state changes
    - Approval workflow events
    - Entity matching
    - ArchiMate generation
    - Model usage
    - Errors and security events
    """
    
    def __init__(self, user_id: int, user_name: Optional[str] = None):
        self.user_id = user_id
        self.user_name = user_name
        self.logger = logging.getLogger(__name__)
    
    def _get_client_ip(self) -> Optional[str]:
        """Get client IP address from request context."""
        try:
            if request:
                # Check for forwarded IP (behind proxy)
                if request.headers.get('X-Forwarded-For'):
                    return request.headers.get('X-Forwarded-For').split(',')[0].strip()
                return request.remote_addr
        except Exception:
            self.logger.debug("Failed to determine client IP address", exc_info=True)
        return None
    
    def _create_log_entry(
        self,
        event_type: AuditEventType,
        severity: str = "info",
        message: Optional[str] = None,
        ai_response: Optional[str] = None,
        operation_type: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        before_state: Optional[Dict] = None,
        after_state: Optional[Dict] = None,
        approval_id: Optional[int] = None,
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
        confidence_score: Optional[float] = None,
        validation_status: Optional[str] = None,
        validation_details: Optional[Dict] = None,
        error_message: Optional[str] = None,
        error_stack_trace: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        domain: Optional[str] = None,
        persona: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> AIChatAuditLog:
        """Create and save an audit log entry."""
        try:
            log_entry = AIChatAuditLog(
                event_type=event_type,
                severity=severity,
                user_id=self.user_id,
                user_name=self.user_name,
                message=message,
                ai_response=ai_response,
                operation_type=operation_type,
                entity_type=entity_type,
                entity_id=entity_id,
                before_state=json.dumps(before_state) if before_state else None,
                after_state=json.dumps(after_state) if after_state else None,
                approval_id=approval_id,
                model_used=model_used,
                provider_used=provider_used,
                confidence_score=confidence_score,
                validation_status=validation_status,
                validation_details=json.dumps(validation_details) if validation_details else None,
                error_message=error_message,
                error_stack_trace=error_stack_trace,
                ip_address=self._get_client_ip(),
                chat_session_id=chat_session_id,
                domain=domain,
                persona=persona,
                processing_time_ms=processing_time_ms,
                metadata=json.dumps(metadata) if metadata else None,
            )
            
            db.session.add(log_entry)
            db.session.commit()
            
            return log_entry
            
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Failed to create audit log: {e}")
            # Don't raise - audit failure shouldn't break operations
            return None
    
    def log_chat_message(
        self,
        message: str,
        ai_response: str,
        domain: str,
        persona: Optional[str] = None,
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        processing_time_ms: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log a chat message and response."""
        return self._create_log_entry(
            event_type=AuditEventType.CHAT_MESSAGE,
            message=message,
            ai_response=ai_response,
            domain=domain,
            persona=persona,
            model_used=model_used,
            provider_used=provider_used,
            chat_session_id=chat_session_id,
            processing_time_ms=processing_time_ms,
            metadata=metadata,
        )
    
    def log_crud_operation(
        self,
        operation_type: str,
        entity_type: str,
        entity_id: Optional[int],
        before_state: Optional[Dict],
        after_state: Optional[Dict],
        approval_id: Optional[int] = None,
        confidence_score: Optional[float] = None,
        message: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log a CRUD operation with before/after state."""
        return self._create_log_entry(
            event_type=AuditEventType.CRUD_OPERATION,
            operation_type=operation_type,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
            approval_id=approval_id,
            confidence_score=confidence_score,
            message=message,
            domain=domain,
        )
    
    def log_approval_created(
        self,
        approval_id: int,
        operation_type: str,
        entity_type: str,
        operation_payload: Dict,
        message: str,
        domain: Optional[str] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log when a pending approval is created."""
        return self._create_log_entry(
            event_type=AuditEventType.APPROVAL_CREATED,
            approval_id=approval_id,
            operation_type=operation_type,
            entity_type=entity_type,
            after_state=operation_payload,  # Future state
            message=message,
            domain=domain,
        )
    
    def log_approval_approved(
        self,
        approval_id: int,
        operation_type: str,
        entity_type: str,
    ) -> Optional[AIChatAuditLog]:
        """Log when an approval is approved by user."""
        return self._create_log_entry(
            event_type=AuditEventType.APPROVAL_APPROVED,
            approval_id=approval_id,
            operation_type=operation_type,
            entity_type=entity_type,
        )
    
    def log_approval_rejected(
        self,
        approval_id: int,
        operation_type: str,
        entity_type: str,
        reason: Optional[str] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log when an approval is rejected by user."""
        return self._create_log_entry(
            event_type=AuditEventType.APPROVAL_REJECTED,
            approval_id=approval_id,
            operation_type=operation_type,
            entity_type=entity_type,
            error_message=reason,
        )
    
    def log_approval_executed(
        self,
        approval_id: int,
        operation_type: str,
        entity_type: str,
        entity_id: Optional[int],
        execution_result: Dict,
    ) -> Optional[AIChatAuditLog]:
        """Log when an approved operation is executed."""
        return self._create_log_entry(
            event_type=AuditEventType.APPROVAL_EXECUTED,
            approval_id=approval_id,
            operation_type=operation_type,
            entity_type=entity_type,
            entity_id=entity_id,
            after_state=execution_result,
        )
    
    def log_approval_expired(
        self,
        approval_id: int,
        operation_type: str,
        entity_type: str,
    ) -> Optional[AIChatAuditLog]:
        """Log when an approval expires."""
        return self._create_log_entry(
            event_type=AuditEventType.APPROVAL_EXPIRED,
            severity="warning",
            approval_id=approval_id,
            operation_type=operation_type,
            entity_type=entity_type,
        )
    
    def log_entity_matched(
        self,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        match_confidence: float,
        search_query: str,
        alternative_matches: Optional[list] = None,
        domain: Optional[str] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log entity matching results."""
        metadata = {
            "entity_name": entity_name,
            "search_query": search_query,
            "alternative_matches": alternative_matches or [],
        }
        return self._create_log_entry(
            event_type=AuditEventType.ENTITY_MATCHED,
            entity_type=entity_type,
            entity_id=entity_id,
            confidence_score=match_confidence,
            domain=domain,
            metadata=metadata,
        )
    
    def log_archimate_generated(
        self,
        application_id: int,
        application_name: str,
        elements_count: int,
        validation_status: str,
        validation_details: Optional[Dict] = None,
        model_used: Optional[str] = None,
        confidence_score: Optional[float] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log ArchiMate element generation."""
        metadata = {
            "application_id": application_id,
            "application_name": application_name,
            "elements_count": elements_count,
        }
        return self._create_log_entry(
            event_type=AuditEventType.ARCHIMATE_GENERATED,
            entity_type="archimate_elements",
            model_used=model_used,
            confidence_score=confidence_score,
            validation_status=validation_status,
            validation_details=validation_details,
            metadata=metadata,
        )
    
    def log_model_switched(
        self,
        previous_model: str,
        new_model: str,
        reason: str,
        domain: Optional[str] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log when AI model is switched."""
        metadata = {
            "previous_model": previous_model,
            "reason": reason,
        }
        return self._create_log_entry(
            event_type=AuditEventType.MODEL_SWITCHED,
            model_used=new_model,
            domain=domain,
            metadata=metadata,
        )
    
    def log_error(
        self,
        error_message: str,
        error_stack_trace: Optional[str] = None,
        message: Optional[str] = None,
        domain: Optional[str] = None,
        severity: str = "error",
    ) -> Optional[AIChatAuditLog]:
        """Log an error event."""
        return self._create_log_entry(
            event_type=AuditEventType.ERROR_OCCURRED,
            severity=severity,
            error_message=error_message,
            error_stack_trace=error_stack_trace,
            message=message,
            domain=domain,
        )
    
    def log_security_event(
        self,
        event_description: str,
        severity: str = "warning",
        metadata: Optional[Dict] = None,
    ) -> Optional[AIChatAuditLog]:
        """Log a security-related event."""
        return self._create_log_entry(
            event_type=AuditEventType.SECURITY_EVENT,
            severity=severity,
            message=event_description,
            metadata=metadata,
        )

    def log_citation_gate_decision(
        self,
        validation_status: str,
        validation_details: Optional[List[str]] = None,
        domain: Optional[str] = None,
        persona: Optional[str] = None,
        chat_session_id: Optional[str] = None,
        severity: str = "warning",
    ) -> Optional[AIChatAuditLog]:
        """Record one citation-gate refusal or would-refusal decision.

        The uncited claims are stored in validation_details so the quality
        monitor can count the decision by organisation and period. The existing
        event_type is used; no new enum value is needed for this.
        """
        return self._create_log_entry(
            event_type=AuditEventType.ERROR_OCCURRED,
            severity=severity,
            validation_status=validation_status,
            validation_details=validation_details,
            chat_session_id=chat_session_id,
            domain=domain,
            persona=persona,
        )


def get_audit_logger(user_id: int, user_name: Optional[str] = None) -> AIChatAuditLogger:
    """Factory function to get an audit logger instance."""
    return AIChatAuditLogger(user_id=user_id, user_name=user_name)
