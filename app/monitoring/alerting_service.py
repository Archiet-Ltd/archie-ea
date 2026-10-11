"""
Alerting Service

Provides comprehensive alerting and notification system for system events.
"""

import logging
import smtplib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum
import threading

from flask import current_app

logger = logging.getLogger(__name__)

class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

class AlertStatus(Enum):
    """Alert status levels."""
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"

@dataclass
class Alert:
    """Represents an alert."""
    id: str
    name: str
    severity: AlertSeverity
    status: AlertStatus
    message: str
    source: str
    timestamp: datetime
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert alert to dictionary."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        if self.acknowledged_at:
            data['acknowledged_at'] = self.acknowledged_at.isoformat()
        if self.resolved_at:
            data['resolved_at'] = self.resolved_at.isoformat()
        return data

class AlertingService:
    """
    Service for managing alerts and notifications.
    """
    
    def __init__(self):
        """Initialize the alerting service."""
        self._alerts = {}  # alert_id -> Alert
        self._alert_rules = {}
        self._notification_channels = []
        self._channels_initialized = False
        self._lock = threading.Lock()

        # Initialize default alert rules
        self._initialize_default_rules()

        # Defer notification channel init until inside app context
        try:
            self._initialize_notification_channels()
        except RuntimeError:
            pass  # Will be initialized on first use via _ensure_channels
    
    def _initialize_default_rules(self):
        """Initialize default alert rules."""
        self._alert_rules = {
            'high_memory_usage': {
                'condition': lambda metrics: metrics.get('system_memory_percent', {}).get('value', 0) > 90,
                'severity': AlertSeverity.WARNING,
                'message': 'System memory usage is above 90%',
                'cooldown': 300  # 5 minutes
            },
            'high_cpu_usage': {
                'condition': lambda metrics: metrics.get('system_cpu_percent', {}).get('value', 0) > 80,
                'severity': AlertSeverity.WARNING,
                'message': 'System CPU usage is above 80%',
                'cooldown': 300
            },
            'low_disk_space': {
                'condition': lambda metrics: metrics.get('system_disk_percent', {}).get('value', 0) > 85,
                'severity': AlertSeverity.CRITICAL,
                'message': 'Disk space usage is above 85%',
                'cooldown': 600  # 10 minutes
            },
            'database_connection_issues': {
                'condition': lambda metrics: metrics.get('database_pool_checked_out', {}).get('value', 0) > metrics.get('database_pool_size', {}).get('value', 10) * 0.8,
                'severity': AlertSeverity.WARNING,
                'message': 'Database connection pool usage is above 80%',
                'cooldown': 300
            },
            'high_error_rate': {
                'condition': lambda metrics: self._calculate_error_rate(metrics) > 0.05,  # 5% error rate
                'severity': AlertSeverity.WARNING,
                'message': 'HTTP error rate is above 5%',
                'cooldown': 600
            },
            'llm_service_down': {
                'condition': lambda metrics: metrics.get('llm_service_status', {}).get('value', 'healthy') != 'healthy',
                'severity': AlertSeverity.CRITICAL,
                'message': 'LLM service is unhealthy or unavailable',
                'cooldown': 300
            }
        }
    
    def _ensure_channels(self):
        """Lazily initialize notification channels if not yet done."""
        if not self._channels_initialized:
            try:
                self._initialize_notification_channels()
            except RuntimeError:
                pass

    def _initialize_notification_channels(self):
        """Initialize notification channels."""
        self._notification_channels = [
            {
                'type': 'log',
                'enabled': True,
                'config': {}
            },
            {
                'type': 'email',
                'enabled': current_app.config.get('ALERT_EMAIL_ENABLED', False),
                'config': {
                    'smtp_server': current_app.config.get('SMTP_SERVER', 'localhost'),
                    'smtp_port': current_app.config.get('SMTP_PORT', 587),
                    'smtp_username': current_app.config.get('SMTP_USERNAME', ''),
                    'smtp_password': current_app.config.get('SMTP_PASSWORD', ''),
                    'from_address': current_app.config.get('ALERT_FROM_EMAIL', 'alerts@archie.local'),
                    'to_addresses': current_app.config.get('ALERT_EMAIL_RECIPIENTS', [])
                }
            },
            {
                'type': 'slack',
                'enabled': current_app.config.get('SLACK_ALERTS_ENABLED', False),
                'config': {
                    'webhook_url': current_app.config.get('SLACK_WEBHOOK_URL', ''),
                    'channel': current_app.config.get('SLACK_ALERT_CHANNEL', '#alerts')
                }
            }
        ]
        self._channels_initialized = True
    
    def _calculate_error_rate(self, metrics: Dict[str, Any]) -> float:
        """Calculate HTTP error rate from metrics."""
        total_requests = metrics.get('http_requests_total', {}).get('value', 0)
        total_errors = metrics.get('http_errors_total', {}).get('value', 0)
        
        if total_requests == 0:
            return 0.0
        
        return total_errors / total_requests
    
    def check_alert_rules(self, metrics: Dict[str, Any]) -> List[Alert]:
        """
        Check all alert rules against current metrics.
        
        Args:
            metrics: Current system metrics
            
        Returns:
            List of new alerts
        """
        new_alerts = []
        
        with self._lock:
            for rule_name, rule in self._alert_rules.items():
                try:
                    # Check if rule condition is met
                    if rule['condition'](metrics):
                        # Check if we're in cooldown period
                        if self._is_in_cooldown(rule_name, rule.get('cooldown', 300)):
                            continue
                        
                        # Create alert
                        alert = Alert(
                            id=f"{rule_name}_{int(datetime.utcnow().timestamp())}",
                            name=rule_name,
                            severity=rule['severity'],
                            status=AlertStatus.ACTIVE,
                            message=rule['message'],
                            source='system',
                            timestamp=datetime.utcnow(),
                            metadata={'rule': rule_name}
                        )
                        
                        self._alerts[alert.id] = alert
                        new_alerts.append(alert)
                        
                        # Send notifications
                        self._send_notifications(alert)
                        
                        logger.warning(f"Alert triggered: {alert.name} - {alert.message}")
                        
                except Exception as e:
                    logger.error(f"Error checking alert rule {rule_name}: {e}")
        
        return new_alerts
    
    def _is_in_cooldown(self, rule_name: str, cooldown_seconds: int) -> bool:
        """Check if a rule is in cooldown period."""
        cutoff_time = datetime.utcnow() - timedelta(seconds=cooldown_seconds)
        
        for alert in self._alerts.values():
            if (alert.metadata and alert.metadata.get('rule') == rule_name and
                alert.timestamp > cutoff_time and alert.status == AlertStatus.ACTIVE):
                return True
        
        return False
    
    def create_manual_alert(self, name: str, severity: AlertSeverity, message: str, source: str = 'manual', metadata: Optional[Dict[str, Any]] = None) -> Alert:
        """
        Create a manual alert.
        
        Args:
            name: Alert name
            severity: Alert severity
            message: Alert message
            source: Alert source
            metadata: Additional metadata
            
        Returns:
            Created alert
        """
        alert = Alert(
            id=f"manual_{name}_{int(datetime.utcnow().timestamp())}",
            name=name,
            severity=severity,
            status=AlertStatus.ACTIVE,
            message=message,
            source=source,
            timestamp=datetime.utcnow(),
            metadata=metadata or {}
        )
        
        with self._lock:
            self._alerts[alert.id] = alert
        
        # Send notifications
        self._send_notifications(alert)
        
        logger.warning(f"Manual alert created: {alert.name} - {alert.message}")
        
        return alert
    
    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        """
        Acknowledge an alert.
        
        Args:
            alert_id: Alert ID
            acknowledged_by: User acknowledging the alert
            
        Returns:
            True if alert was acknowledged, False if not found
        """
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert and alert.status == AlertStatus.ACTIVE:
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.utcnow()
                
                logger.info(f"Alert acknowledged: {alert_id} by {acknowledged_by}")
                return True
        
        return False
    
    def resolve_alert(self, alert_id: str) -> bool:
        """
        Resolve an alert.
        
        Args:
            alert_id: Alert ID
            
        Returns:
            True if alert was resolved, False if not found
        """
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert and alert.status in [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = datetime.utcnow()
                
                logger.info(f"Alert resolved: {alert_id}")
                return True
        
        return False
    
    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """
        Get all active alerts.
        
        Returns:
            List of active alerts
        """
        with self._lock:
            active_alerts = [
                alert.to_dict() for alert in self._alerts.values()
                if alert.status in [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED]
            ]
            
            # Sort by timestamp (newest first)
            active_alerts.sort(key=lambda x: x['timestamp'], reverse=True)
            
            return active_alerts
    
    def get_all_alerts(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all alerts (limited).
        
        Args:
            limit: Maximum number of alerts to return
            
        Returns:
            List of alerts
        """
        with self._lock:
            all_alerts = [alert.to_dict() for alert in self._alerts.values()]
            
            # Sort by timestamp (newest first)
            all_alerts.sort(key=lambda x: x['timestamp'], reverse=True)
            
            return all_alerts[:limit]
    
    def _send_notifications(self, alert: Alert):
        """
        Send notifications for an alert.

        Args:
            alert: Alert to send notifications for
        """
        self._ensure_channels()
        for channel in self._notification_channels:
            if not channel.get('enabled', False):
                continue
            
            try:
                if channel['type'] == 'log':
                    self._send_log_notification(alert)
                elif channel['type'] == 'email':
                    self._send_email_notification(alert, channel['config'])
                elif channel['type'] == 'slack':
                    self._send_slack_notification(alert, channel['config'])
                    
            except Exception as e:
                logger.error(f"Failed to send {channel['type']} notification for alert {alert.id}: {e}")
    
    def _send_log_notification(self, alert: Alert):
        """Send log notification."""
        log_level = {
            AlertSeverity.INFO: logger.info,
            AlertSeverity.WARNING: logger.warning,
            AlertSeverity.CRITICAL: logger.error
        }.get(alert.severity, logger.info)
        
        log_level(f"ALERT: {alert.name} - {alert.message}")
    
    def _send_email_notification(self, alert: Alert, config: Dict[str, Any]):
        """Send email notification."""
        try:
            subject = f"[{alert.severity.value.upper()}] {alert.name}"
            body = f"""
Alert: {alert.name}
Severity: {alert.severity.value.upper()}
Message: {alert.message}
Source: {alert.source}
Time: {alert.timestamp.isoformat()}

This is an automated alert from the Entelim platform.
            """.strip()
            
            # Create email message
            msg = f"Subject: {subject}\n\n{body}"
            
            # Send email (simplified - in production use proper email library)
            with smtplib.SMTP(config['smtp_server'], config['smtp_port']) as server:
                if config['smtp_username']:
                    server.starttls()
                    server.login(config['smtp_username'], config['smtp_password'])
                
                server.sendmail(config['from_address'], config['to_addresses'], msg)
                
            logger.info(f"Email notification sent for alert {alert.id}")
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
    
    def _send_slack_notification(self, alert: Alert, config: Dict[str, Any]):
        """Send Slack notification."""
        try:
            import requests
            
            color = {
                AlertSeverity.INFO: 'good',
                AlertSeverity.WARNING: 'warning',
                AlertSeverity.CRITICAL: 'danger'
            }.get(alert.severity, 'warning')
            
            payload = {
                'channel': config['channel'],
                'username': 'Entelim Alerts',
                'icon_emoji': ':warning:',
                'attachments': [{
                    'color': color,
                    'title': f"Alert: {alert.name}",
                    'text': alert.message,
                    'fields': [
                        {'title': 'Severity', 'value': alert.severity.value.upper(), 'short': True},
                        {'title': 'Source', 'value': alert.source, 'short': True},
                        {'title': 'Time', 'value': alert.timestamp.isoformat(), 'short': True}
                    ],
                    'footer': 'Entelim Platform',
                    'ts': int(alert.timestamp.timestamp())
                }]
            }
            
            response = requests.post(config['webhook_url'], json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Slack notification sent for alert {alert.id}")
            
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

# Global alerting service instance
alerting_service = AlertingService()
