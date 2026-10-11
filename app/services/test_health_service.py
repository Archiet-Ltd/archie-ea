"""
Test Health Dashboard Service

Provides test health monitoring, pass rate tracking, coverage trends,
and alerting integration for Slack and email notifications.
"""

import os
import json
import smtplib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from html import escape


@dataclass
class TestMetrics:
    """Test run metrics."""
    run_id: str
    timestamp: str
    total_tests: int
    passed: int
    failed: int
    skipped: int
    flaky: int
    duration_seconds: float
    coverage_percent: Optional[float] = None
    category_breakdown: Dict[str, Dict[str, int]] = None
    
    def __post_init__(self):
        if self.category_breakdown is None:
            self.category_breakdown = {}
    
    @property
    def pass_rate(self) -> float:
        """Calculate pass rate percentage."""
        if self.total_tests == 0:
            return 0.0
        return round((self.passed / self.total_tests) * 100, 2)
    
    @property
    def fail_rate(self) -> float:
        """Calculate fail rate percentage."""
        if self.total_tests == 0:
            return 0.0
        return round((self.failed / self.total_tests) * 100, 2)


class TestHealthService:
    """
    Service for tracking test health metrics and trends.
    
    Features:
    - Test run history tracking
    - Pass rate trend analysis
    - Coverage trend tracking
    - Flaky test detection
    - KPI monitoring
    """
    
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir or 'test_results/health_data')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_file = self.data_dir / 'test_metrics.json'
        self.kpi_file = self.data_dir / 'kpi_thresholds.json'
        self._ensure_files()
    
    def _ensure_files(self):
        """Initialize data files if they don't exist."""
        if not self.metrics_file.exists():
            self._save_metrics([])
        if not self.kpi_file.exists():
            self._save_kpis({
                'pass_rate_min': 80.0,
                'coverage_min': 70.0,
                'flaky_max': 5,
                'duration_max_seconds': 3600,
                'alert_enabled': True
            })
    
    def _load_metrics(self) -> List[Dict]:
        """Load test metrics from disk."""
        try:
            with open(self.metrics_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _save_metrics(self, metrics: List[Dict]):
        """Save test metrics to disk."""
        with open(self.metrics_file, 'w') as f:
            json.dump(metrics, f, indent=2, default=str)
    
    def _load_kpis(self) -> Dict:
        """Load KPI thresholds from disk."""
        try:
            with open(self.kpi_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def _save_kpis(self, kpis: Dict):
        """Save KPI thresholds to disk."""
        with open(self.kpi_file, 'w') as f:
            json.dump(kpis, f, indent=2)
    
    def record_test_run(self, metrics: TestMetrics) -> Dict:
        """
        Record a test run with metrics.
        
        Returns:
            Dict with status and any alert triggers
        """
        # Load existing metrics
        all_metrics = self._load_metrics()
        
        # Add new metric
        all_metrics.append(asdict(metrics))
        
        # Keep only last 90 days of data
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        all_metrics = [m for m in all_metrics if m['timestamp'] >= cutoff]
        
        # Save updated metrics
        self._save_metrics(all_metrics)
        
        # Check KPIs and generate alerts
        alerts = self._check_kpis(metrics)
        
        return {
            'success': True,
            'run_id': metrics.run_id,
            'pass_rate': metrics.pass_rate,
            'alerts': alerts
        }
    
    def _check_kpis(self, metrics: TestMetrics) -> List[Dict]:
        """Check KPIs and return any triggered alerts."""
        kpis = self._load_kpis()
        alerts = []
        
        if not kpis.get('alert_enabled', True):
            return alerts
        
        # Check pass rate
        if metrics.pass_rate < kpis.get('pass_rate_min', 80.0):
            alerts.append({
                'type': 'pass_rate_low',
                'severity': 'warning' if metrics.pass_rate >= 70 else 'critical',
                'message': f'Pass rate {metrics.pass_rate}% below threshold {kpis["pass_rate_min"]}%',
                'value': metrics.pass_rate,
                'threshold': kpis['pass_rate_min']
            })
        
        # Check coverage
        if metrics.coverage_percent is not None and metrics.coverage_percent < kpis.get('coverage_min', 70.0):
            alerts.append({
                'type': 'coverage_low',
                'severity': 'warning',
                'message': f'Coverage {metrics.coverage_percent}% below threshold {kpis["coverage_min"]}%',
                'value': metrics.coverage_percent,
                'threshold': kpis['coverage_min']
            })
        
        # Check flaky tests
        if metrics.flaky > kpis.get('flaky_max', 5):
            alerts.append({
                'type': 'flaky_high',
                'severity': 'warning',
                'message': f'{metrics.flaky} flaky tests exceed threshold {kpis["flaky_max"]}',
                'value': metrics.flaky,
                'threshold': kpis['flaky_max']
            })
        
        # Check duration
        if metrics.duration_seconds > kpis.get('duration_max_seconds', 3600):
            alerts.append({
                'type': 'duration_high',
                'severity': 'warning',
                'message': f'Test duration {metrics.duration_seconds}s exceeds threshold {kpis["duration_max_seconds"]}s',
                'value': metrics.duration_seconds,
                'threshold': kpis['duration_max_seconds']
            })
        
        return alerts
    
    def get_trend_data(self, days: int = 30) -> Dict:
        """Get test health trend data for the specified number of days."""
        metrics = self._load_metrics()
        
        # Filter to specified time range
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        filtered = [m for m in metrics if m['timestamp'] >= cutoff]
        
        # Sort by timestamp
        filtered.sort(key=lambda x: x['timestamp'])
        
        # Extract trend data
        dates = []
        pass_rates = []
        coverage = []
        durations = []
        
        for m in filtered:
            # Parse timestamp
            ts = m['timestamp']
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00').replace('+00:00', ''))
            dates.append(ts.strftime('%Y-%m-%d'))
            
            # Calculate pass rate if not stored
            if 'pass_rate' in m:
                pass_rates.append(m['pass_rate'])
            else:
                total = m.get('total_tests', 0)
                passed = m.get('passed', 0)
                rate = (passed / total * 100) if total > 0 else 0
                pass_rates.append(round(rate, 2))
            
            coverage.append(m.get('coverage_percent'))
            durations.append(round(m.get('duration_seconds', 0), 2))
        
        return {
            'dates': dates,
            'pass_rates': pass_rates,
            'coverage': coverage,
            'durations': durations,
            'data_points': len(filtered)
        }
    
    def get_current_health(self) -> Dict:
        """Get current test health summary."""
        metrics = self._load_metrics()
        kpis = self._load_kpis()
        
        if not metrics:
            return {
                'status': 'unknown',
                'message': 'No test data available',
                'metrics': None
            }
        
        # Get most recent run
        max(metrics, key=lambda x: x['timestamp'])
        
        # Calculate trend (compare to previous run)
        sorted_metrics = sorted(metrics, key=lambda x: x['timestamp'], reverse=True)
        current = sorted_metrics[0] if sorted_metrics else None
        previous = sorted_metrics[1] if len(sorted_metrics) > 1 else None
        
        # Calculate changes
        pass_rate_change = 0
        coverage_change = 0
        flaky_change = 0
        
        if current and previous:
            current_pass = current.get('pass_rate') or (current.get('passed', 0) / max(current.get('total_tests', 1), 1) * 100)
            prev_pass = previous.get('pass_rate') or (previous.get('passed', 0) / max(previous.get('total_tests', 1), 1) * 100)
            pass_rate_change = round(current_pass - prev_pass, 2)
            
            if current.get('coverage_percent') and previous.get('coverage_percent'):
                coverage_change = round(current['coverage_percent'] - previous['coverage_percent'], 2)
            
            flaky_change = current.get('flaky', 0) - previous.get('flaky', 0)
        
        # Determine overall health status
        status = 'healthy'
        issues = []
        
        current_pass_rate = current.get('pass_rate') or (current.get('passed', 0) / max(current.get('total_tests', 1), 1) * 100)
        
        if current_pass_rate < kpis.get('pass_rate_min', 80.0):
            status = 'at_risk'
            issues.append(f"Pass rate {current_pass_rate:.1f}% below threshold")
        
        if current.get('flaky', 0) > kpis.get('flaky_max', 5):
            status = 'at_risk'
            issues.append(f"{current['flaky']} flaky tests exceed threshold")
        
        if current_pass_rate < 50:
            status = 'critical'
        
        return {
            'status': status,
            'message': '; '.join(issues) if issues else 'All KPIs within thresholds',
            'metrics': {
                'total_tests': current.get('total_tests', 0),
                'passed': current.get('passed', 0),
                'failed': current.get('failed', 0),
                'skipped': current.get('skipped', 0),
                'flaky': current.get('flaky', 0),
                'pass_rate': round(current_pass_rate, 2),
                'pass_rate_change': pass_rate_change,
                'coverage': current.get('coverage_percent'),
                'coverage_change': coverage_change,
                'flaky_change': flaky_change,
                'duration_seconds': current.get('duration_seconds', 0),
                'timestamp': current.get('timestamp')
            },
            'kpis': kpis
        }
    
    def get_category_breakdown(self) -> List[Dict]:
        """Get test breakdown by category."""
        metrics = self._load_metrics()
        
        if not metrics:
            return []
        
        # Get most recent run
        latest = max(metrics, key=lambda x: x['timestamp'])
        categories = latest.get('category_breakdown', {})
        
        # Calculate previous run for trends
        sorted_metrics = sorted(metrics, key=lambda x: x['timestamp'], reverse=True)
        previous = sorted_metrics[1] if len(sorted_metrics) > 1 else None
        prev_categories = previous.get('category_breakdown', {}) if previous else {}
        
        result = []
        for name, data in categories.items():
            total = data.get('total', 0)
            passed = data.get('passed', 0)
            failed = data.get('failed', 0)
            skipped = data.get('skipped', 0)
            
            pass_rate = round((passed / total * 100), 1) if total > 0 else 0
            
            # Determine trend
            trend = 'stable'
            if name in prev_categories:
                prev_data = prev_categories[name]
                prev_total = prev_data.get('total', 0)
                prev_passed = prev_data.get('passed', 0)
                prev_rate = (prev_passed / prev_total * 100) if prev_total > 0 else 0
                
                if pass_rate > prev_rate + 5:
                    trend = 'up'
                elif pass_rate < prev_rate - 5:
                    trend = 'down'
            
            result.append({
                'name': name,
                'total': total,
                'passed': passed,
                'failed': failed,
                'skipped': skipped,
                'pass_rate': pass_rate,
                'trend': trend
            })
        
        # Sort by total tests descending
        result.sort(key=lambda x: x['total'], reverse=True)
        return result
    
    def update_kpi_thresholds(self, thresholds: Dict) -> Dict:
        """Update KPI threshold values."""
        current = self._load_kpis()
        current.update(thresholds)
        self._save_kpis(current)
        return current
    
    def get_flaky_tests(self, threshold: int = 2) -> List[Dict]:
        """Identify potentially flaky tests from history."""
        metrics = self._load_metrics()
        
        if not metrics:
            return []
        
        # Track test stability across runs
        
        for run in metrics:
            # This is a simplified version - in production you'd track individual test results
            pass
        
        # For now, return flaky count from latest run
        latest = max(metrics, key=lambda x: x['timestamp'])
        
        return [{
            'test_name': 'Flaky Tests',
            'times_failed': latest.get('flaky', 0),
            'total_runs': len(metrics),
            'flakiness_score': latest.get('flaky', 0) / max(len(metrics), 1)
        }]


class TestAlertService:
    """
    Service for sending test health alerts via Slack and Email.
    """
    
    def __init__(self):
        self.slack_webhook_url = os.environ.get('SLACK_TEST_ALERTS_WEBHOOK')
        self.email_recipients = os.environ.get('TEST_ALERT_EMAILS', '').split(',')
        self.email_from = os.environ.get('TEST_ALERT_FROM', 'test-alerts@example.com')
        self.smtp_host = os.environ.get('SMTP_HOST', 'localhost')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.smtp_user = os.environ.get('SMTP_USER')
        self.smtp_password = os.environ.get('SMTP_PASSWORD')
    
    def send_slack_alert(self, alert: Dict) -> bool:
        """Send alert to Slack webhook."""
        if not self.slack_webhook_url:
            return False
        
        try:
            # Determine color based on severity
            colors = {
                'critical': '#ef4444',
                'warning': '#f59e0b',
                'info': '#3b82f6'
            }
            
            payload = {
                'attachments': [{
                    'color': colors.get(alert.get('severity', 'info'), '#3b82f6'),
                    'title': f"Test Health Alert: {alert.get('type', 'Unknown')}",
                    'text': alert.get('message', ''),
                    'fields': [
                        {
                            'title': 'Severity',
                            'value': alert.get('severity', 'info').upper(),
                            'short': True
                        },
                        {
                            'title': 'Value',
                            'value': str(alert.get('value', 'N/A')),
                            'short': True
                        },
                        {
                            'title': 'Threshold',
                            'value': str(alert.get('threshold', 'N/A')),
                            'short': True
                        }
                    ],
                    'footer': 'Entelim Test Health Dashboard',
                    'ts': int(datetime.utcnow().timestamp())
                }]
            }
            
            response = requests.post(
                self.slack_webhook_url,
                json=payload,
                timeout=10
            )
            
            return response.status_code == 200
        except Exception as e:
            print(f"Failed to send Slack alert: {e}")
            return False
    
    def send_email_alert(self, alert: Dict) -> bool:
        """Send alert via email."""
        if not self.email_recipients or not self.email_recipients[0]:
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[TEST ALERT] {alert.get('type', 'Unknown').replace('_', ' ').title()}"
            msg['From'] = self.email_from
            msg['To'] = ', '.join(self.email_recipients)
            
            # Plain text version
            text_body = f"""
Test Health Alert

Type: {alert.get('type', 'Unknown')}
Severity: {alert.get('severity', 'info').upper()}
Message: {alert.get('message', '')}

Value: {alert.get('value', 'N/A')}
Threshold: {alert.get('threshold', 'N/A')}

View dashboard: http://localhost:5000/testing/health-dashboard
            """
            
            # HTML version
            html_body = f"""
<html>
<body style="font-family: sans-serif; line-height: 1.6;">
    <h2 style="color: #dc2626;">🚨 Test Health Alert</h2>
    
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold;">Type</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{escape(str(alert.get('type', 'Unknown')))}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold;">Severity</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb; color: {'#dc2626' if alert.get('severity') == 'critical' else '#f59e0b'};">
                {alert.get('severity', 'info').upper()}
            </td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold;">Message</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{escape(str(alert.get('message', '')))}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold;">Value</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{escape(str(alert.get('value', 'N/A')))}</td>
        </tr>
        <tr>
            <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold;">Threshold</td>
            <td style="padding: 8px; border: 1px solid #e5e7eb;">{escape(str(alert.get('threshold', 'N/A')))}</td>
        </tr>
    </table>
    
    <p style="margin-top: 20px;">
        <a href="http://localhost:5000/testing/health-dashboard" style="background: #2563eb; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
            View Dashboard
        </a>
    </p>
</body>
</html>
            """
            
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            # Send email
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.email_from, self.email_recipients, msg.as_string())
            
            return True
        except Exception as e:
            print(f"Failed to send email alert: {e}")
            return False
    
    def send_alerts(self, alerts: List[Dict]) -> Dict:
        """Send all alerts through configured channels."""
        results = {
            'slack_sent': 0,
            'email_sent': 0,
            'failed': 0
        }
        
        for alert in alerts:
            # Only send critical and warning alerts
            if alert.get('severity') not in ['critical', 'warning']:
                continue
            
            if self.send_slack_alert(alert):
                results['slack_sent'] += 1
            
            if self.send_email_alert(alert):
                results['email_sent'] += 1
            
            if not self.send_slack_alert(alert) and not self.send_email_alert(alert):
                results['failed'] += 1
        
        return results


# Global instances
_test_health_service: Optional[TestHealthService] = None
_test_alert_service: Optional[TestAlertService] = None


def get_test_health_service(data_dir: Optional[str] = None) -> TestHealthService:
    """Get or create the global test health service instance."""
    global _test_health_service
    if _test_health_service is None:
        _test_health_service = TestHealthService(data_dir)
    return _test_health_service


def get_test_alert_service() -> TestAlertService:
    """Get or create the global test alert service instance."""
    global _test_alert_service
    if _test_alert_service is None:
        _test_alert_service = TestAlertService()
    return _test_alert_service
