"""
Monitoring Package

Provides comprehensive monitoring infrastructure for the Entelim platform including:
- Metrics collection and aggregation
- Alerting and notification system
- Security event monitoring
- Operational dashboards
"""

from .metrics_service import MetricsService, metrics_service
from .alerting_service import AlertingService, alerting_service
from .security_monitoring import SecurityMonitoringService, security_monitoring_service
from .metrics_decorator import (
    track_http_requests,
    track_database_queries,
    track_llm_requests,
    track_file_uploads,
    track_business_events,
    track_application_created,
    track_vendor_created,
    track_workflow_started,
    track_workflow_completed,
    track_consolidation_created
)

__all__ = [
    'MetricsService',
    'metrics_service',
    'AlertingService',
    'alerting_service',
    'SecurityMonitoringService',
    'security_monitoring_service',
    'track_http_requests',
    'track_database_queries',
    'track_llm_requests',
    'track_file_uploads',
    'track_business_events',
    'track_application_created',
    'track_vendor_created',
    'track_workflow_started',
    'track_workflow_completed',
    'track_consolidation_created'
]
