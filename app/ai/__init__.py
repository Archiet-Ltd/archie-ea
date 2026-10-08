"""
AI Package

Provides comprehensive AI functionality with graceful degradation, including:
- Feature flag system for AI capabilities
- Cost controls and budget monitoring
- Data classification and filtering
- AI interaction audit trails
- Non-AI fallback functionality
"""

from .feature_flags import AIFeatureFlags, ai_feature_flags
from .cost_monitor import AICostMonitor, ai_cost_monitor
from .data_classifier import AIDataClassifier, ai_data_classifier
from .audit_trail import AIAuditTrail, ai_audit_trail
from .fallback_service import AIFallbackService, ai_fallback_service

__all__ = [
    'AIFeatureFlags',
    'ai_feature_flags',
    'AICostMonitor',
    'ai_cost_monitor',
    'AIDataClassifier',
    'ai_data_classifier',
    'AIAuditTrail',
    'ai_audit_trail',
    'AIFallbackService',
    'ai_fallback_service'
]
