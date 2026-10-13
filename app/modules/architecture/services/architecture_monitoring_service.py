"""
-> app.modules.architecture.services.governance_service

Architecture Monitoring Service

Continuous monitoring service for architecture drift detection and proactive alerting.
Integrates with existing gap detection, capability health, and gap discovery services
to provide comprehensive architecture surveillance.

Key Features:
- Architecture baseline capture (snapshot of current state)
- Drift detection (compare current vs baseline)
- Alert types: NEW_GAP, COVERAGE_DECREASE, MATURITY_REGRESSION, VENDOR_RISK_CHANGE
- Alert severity levels: info, warning, critical
- Integration with existing gap detection services
- Scans run on request only (no scheduler); scan_interval_minutes is stored and reported, not acted on

Reuses:
- gap_discovery_service.py for scanning
- capability_health_service.py for health metrics
- ai_gap_detection_service.py for intelligent detection
- CapabilityGapDashboard patterns for visualization
"""

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


from app import db
from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
from app.models.unified_capability import UnifiedCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Enums and Data Classes
# =============================================================================


class AlertType(Enum):
    """Types of architecture drift alerts."""

    NEW_GAP = "new_gap"
    COVERAGE_DECREASE = "coverage_decrease"
    MATURITY_REGRESSION = "maturity_regression"
    VENDOR_RISK_CHANGE = "vendor_risk_change"
    NEW_CAPABILITY_UNCOVERED = "new_capability_uncovered"
    APPLICATION_DEPRECATED = "application_deprecated"
    COMPLIANCE_DRIFT = "compliance_drift"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MonitoringStatus(Enum):
    """Monitoring service status."""

    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"
    INITIALIZING = "initializing"


@dataclass
class ArchitectureAlert:
    """Represents an architecture drift alert."""

    id: str
    alert_type: str
    severity: str
    title: str
    description: str
    affected_element_id: Optional[int]
    affected_element_type: Optional[str]
    affected_element_name: Optional[str]
    baseline_value: Optional[Any]
    current_value: Optional[Any]
    delta: Optional[float]
    recommended_action: Optional[str]
    created_at: str
    acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftAnalysis:
    """Represents drift analysis results."""

    baseline_id: str
    baseline_name: str
    analysis_timestamp: str
    total_drifts: int
    critical_drifts: int
    warning_drifts: int
    info_drifts: int
    coverage_drift: Dict[str, Any]
    health_drift: Dict[str, Any]
    gap_drift: Dict[str, Any]
    vendor_drift: Dict[str, Any]
    alerts: List[Dict[str, Any]]
    summary: str


class ArchitectureMonitoringService:
    """
    Service for continuous architecture monitoring and drift detection.

    Provides comprehensive monitoring capabilities:
    - Baseline architecture capture
    - Drift detection algorithms
    - Alert generation and management
    - Integration with existing services

    No baseline or alert is ever held in an instance or class attribute across
    calls: every method reads from and writes to MonitoringBaseline /
    MonitoringAlert (both TenantMixin) directly, so the tenant ORM filter
    (do_orm_execute, app.middleware.tenant_isolation) scopes every query to
    g.current_org_id and two organisations' data can never mix in this
    service. Two instances of this class share nothing (F-3,
    SECURITY-FINDINGS-R2-5.md).
    """

    def __init__(self):
        """Initialize per-call defaults. None of these are class attributes:
        each instance gets its own, so setting one on this instance can never
        leak into another instance or another organisation's request."""
        self._status = MonitoringStatus.ACTIVE
        # Scan cadence is platform-wide configuration, not per-organisation
        # state, so it is a plain instance default rather than a stored row.
        self._scan_interval_minutes = 60
        self.COVERAGE_DECREASE_WARNING_THRESHOLD = 5  # 5% decrease
        self.COVERAGE_DECREASE_CRITICAL_THRESHOLD = 15  # 15% decrease
        self.HEALTH_SCORE_WARNING_THRESHOLD = 10  # 10 point decrease
        self.HEALTH_SCORE_CRITICAL_THRESHOLD = 20  # 20 point decrease

    def _active_baseline_row(self):
        """The current organisation's active baseline row, or None."""
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        return MBModel.query.filter_by(is_active=True).first()

    def _persist_alert(self, alert: "ArchitectureAlert"):
        """Insert a newly generated alert row."""
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        row = MAModel(
            alert_id=alert.id,
            alert_type=alert.alert_type,
            severity=alert.severity,
            title=alert.title,
            description=alert.description,
            affected_element_id=alert.affected_element_id,
            affected_element_type=alert.affected_element_type,
            affected_element_name=alert.affected_element_name,
            baseline_value=json.dumps(alert.baseline_value) if alert.baseline_value is not None else None,
            current_value=json.dumps(alert.current_value) if alert.current_value is not None else None,
            delta=alert.delta,
            recommended_action=alert.recommended_action,
            acknowledged=alert.acknowledged,
            acknowledged_by=alert.acknowledged_by,
            alert_metadata=json.dumps(alert.metadata) if alert.metadata else None,
        )
        db.session.add(row)
        db.session.commit()

    def _unknown_drift(self, row, reason: str) -> Dict[str, Any]:
        """A drift result that honestly reports nothing could be compared,
        instead of fabricating a zero or crashing on a None comparison."""
        return {
            "success": True,
            "status": "unknown",
            "baseline_id": row.baseline_id,
            "baseline_name": row.name,
            "reason": reason,
        }

    def _alert_to_api_dict(self, row) -> Dict[str, Any]:
        """Render a MonitoringAlert row in the shape the API has always returned."""
        return {
            "id": row.alert_id,
            "alert_type": row.alert_type,
            "severity": row.severity,
            "title": row.title,
            "description": row.description or "",
            "affected_element_id": row.affected_element_id,
            "affected_element_type": row.affected_element_type,
            "affected_element_name": row.affected_element_name,
            "baseline_value": json.loads(row.baseline_value) if row.baseline_value else None,
            "current_value": json.loads(row.current_value) if row.current_value else None,
            "delta": row.delta,
            "recommended_action": row.recommended_action,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "acknowledged": row.acknowledged or False,
            "acknowledged_by": row.acknowledged_by,
            "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
            "metadata": json.loads(row.alert_metadata) if row.alert_metadata else {},
        }

    # =========================================================================
    # Monitoring Status
    # =========================================================================

    def get_monitoring_status(self) -> Dict[str, Any]:
        """
        Get current monitoring status and configuration.

        Returns:
            Dict with monitoring status information
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        active_row = self._active_baseline_row()
        active_baseline = None
        if active_row:
            active_baseline = {
                "id": active_row.baseline_id,
                "name": active_row.name,
                "created_at": active_row.created_at.isoformat() if active_row.created_at else None,
            }

        total_baselines = MBModel.query.count()

        # Count alerts by severity
        alert_counts = {"info": 0, "warning": 0, "critical": 0, "total": 0, "unacknowledged": 0}
        for row in MAModel.query.all():
            alert_counts["total"] += 1
            alert_counts[row.severity] = alert_counts.get(row.severity, 0) + 1
            if not row.acknowledged:
                alert_counts["unacknowledged"] += 1

        # No dedicated "last scan" column exists on either model; the most
        # recent alert timestamp is the honest proxy (alerts are only ever
        # created during a scan). None, not a fabricated time, when no scan
        # has produced an alert yet.
        last_alert = MAModel.query.order_by(MAModel.created_at.desc()).first()
        last_scan_time = (
            last_alert.created_at.isoformat() if last_alert and last_alert.created_at else None
        )

        return {
            "success": True,
            "status": self._status.value,
            "last_scan_time": last_scan_time,
            "scan_interval_minutes": self._scan_interval_minutes,
            "active_baseline": active_baseline,
            "total_baselines": total_baselines,
            "alerts": alert_counts,
            "thresholds": {
                "coverage_decrease_warning": self.COVERAGE_DECREASE_WARNING_THRESHOLD,
                "coverage_decrease_critical": self.COVERAGE_DECREASE_CRITICAL_THRESHOLD,
                "health_score_warning": self.HEALTH_SCORE_WARNING_THRESHOLD,
                "health_score_critical": self.HEALTH_SCORE_CRITICAL_THRESHOLD,
            },
        }

    def set_monitoring_status(self, status: str) -> Dict[str, Any]:
        """
        Set monitoring status (active, paused).

        Args:
            status: New status (active, paused)

        Returns:
            Dict with result
        """
        try:
            self._status = MonitoringStatus(status)
            return {
                "success": True,
                "status": self._status.value,
                "message": f"Monitoring status set to {status}",
            }
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid status: {status}. Must be one of: active, paused",
            }

    def configure_monitoring(
        self,
        scan_interval_minutes: Optional[int] = None,
        coverage_warning_threshold: Optional[int] = None,
        coverage_critical_threshold: Optional[int] = None,
        health_warning_threshold: Optional[int] = None,
        health_critical_threshold: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Configure monitoring parameters.

        Args:
            scan_interval_minutes: Interval between scans
            coverage_warning_threshold: Coverage decrease warning threshold
            coverage_critical_threshold: Coverage decrease critical threshold
            health_warning_threshold: Health score warning threshold
            health_critical_threshold: Health score critical threshold

        Returns:
            Dict with configuration result
        """
        if scan_interval_minutes is not None:
            self._scan_interval_minutes = max(5, scan_interval_minutes)  # Min 5 minutes

        if coverage_warning_threshold is not None:
            self.COVERAGE_DECREASE_WARNING_THRESHOLD = coverage_warning_threshold

        if coverage_critical_threshold is not None:
            self.COVERAGE_DECREASE_CRITICAL_THRESHOLD = coverage_critical_threshold

        if health_warning_threshold is not None:
            self.HEALTH_SCORE_WARNING_THRESHOLD = health_warning_threshold

        if health_critical_threshold is not None:
            self.HEALTH_SCORE_CRITICAL_THRESHOLD = health_critical_threshold

        return {
            "success": True,
            "configuration": {
                "scan_interval_minutes": self._scan_interval_minutes,
                "coverage_warning_threshold": self.COVERAGE_DECREASE_WARNING_THRESHOLD,
                "coverage_critical_threshold": self.COVERAGE_DECREASE_CRITICAL_THRESHOLD,
                "health_warning_threshold": self.HEALTH_SCORE_WARNING_THRESHOLD,
                "health_critical_threshold": self.HEALTH_SCORE_CRITICAL_THRESHOLD,
            },
        }

    # =========================================================================
    # Baseline Management
    # =========================================================================

    def capture_baseline(
        self,
        name: str,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
        set_as_active: bool = True,
    ) -> Dict[str, Any]:
        """
        Capture current architecture state as a baseline.

        Args:
            name: Name for the baseline
            description: Optional description
            created_by: User who created the baseline
            set_as_active: Whether to set this as the active baseline

        Returns:
            Dict with baseline details
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        try:
            baseline_id = str(uuid4())

            # Capture capability snapshot
            capabilities_snapshot = self._capture_capabilities_snapshot()

            # Capture coverage metrics
            coverage_snapshot = self._capture_coverage_snapshot()

            # Capture health metrics
            health_snapshot = self._capture_health_snapshot()

            # Capture gap analysis
            gap_snapshot = self._capture_gap_snapshot()

            # Capture vendor status
            vendor_snapshot = self._capture_vendor_snapshot()

            # Calculate checksum for integrity
            checksum = self._calculate_baseline_checksum(
                capabilities_snapshot,
                coverage_snapshot,
                health_snapshot,
                gap_snapshot,
                vendor_snapshot,
            )

            snapshot_data = json.dumps(
                {
                    "capabilities": capabilities_snapshot,
                    "coverage": coverage_snapshot,
                    "health": health_snapshot,
                    "gaps": gap_snapshot,
                    "vendors": vendor_snapshot,
                    "metadata": {},
                }
            )

            if set_as_active:
                # Bulk UPDATE is tenant-filtered (ADR-0003), so this only
                # touches the current organisation's own baselines.
                MBModel.query.update({MBModel.is_active: False})

            row = MBModel(
                baseline_id=baseline_id,
                name=name,
                description=description,
                created_by=created_by,
                is_active=set_as_active,
                snapshot_data=snapshot_data,
                checksum=checksum,
            )
            db.session.add(row)
            db.session.commit()

            return {
                "success": True,
                "baseline": {
                    "id": row.baseline_id,
                    "name": row.name,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "created_by": row.created_by,
                    "description": row.description,
                    "checksum": row.checksum,
                    "is_active": row.is_active,
                    "stats": {
                        "capabilities_count": len(capabilities_snapshot),
                        "gaps_count": len(gap_snapshot),
                        "average_coverage": coverage_snapshot.get("average_coverage", 0),
                        "average_health": health_snapshot.get("average_health", 0),
                    },
                },
                "message": f"Baseline '{name}' captured successfully",
            }

        except Exception as e:
            logger.error(f"Error capturing baseline: {e}")
            db.session.rollback()
            return {"success": False, "error": str(e)}

    def get_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Get a specific baseline by ID.

        Args:
            baseline_id: ID of the baseline

        Returns:
            Dict with baseline details
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        row = MBModel.query.filter_by(baseline_id=baseline_id).first()
        if not row:
            return {"success": False, "error": "Baseline not found"}

        snapshot = json.loads(row.snapshot_data) if row.snapshot_data else {}

        return {
            "success": True,
            "baseline": {
                "id": row.baseline_id,
                "name": row.name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "created_by": row.created_by,
                "description": row.description,
                "checksum": row.checksum,
                "is_active": row.is_active,
                "capabilities_snapshot": snapshot.get("capabilities", []),
                "coverage_snapshot": snapshot.get("coverage", {}),
                "health_snapshot": snapshot.get("health", {}),
                "gap_snapshot": snapshot.get("gaps", []),
                "vendor_snapshot": snapshot.get("vendors", []),
                "metadata": snapshot.get("metadata", {}),
            },
        }

    def list_baselines(self) -> Dict[str, Any]:
        """
        List all captured baselines.

        Returns:
            Dict with list of baselines
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        rows = MBModel.query.order_by(MBModel.created_at.desc()).all()
        baselines = [
            {
                "id": row.baseline_id,
                "name": row.name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "created_by": row.created_by,
                "description": row.description,
                "checksum": row.checksum,
                "is_active": row.is_active,
            }
            for row in rows
        ]

        active_baseline_id = next((b["id"] for b in baselines if b["is_active"]), None)

        return {
            "success": True,
            "baselines": baselines,
            "total": len(baselines),
            "active_baseline_id": active_baseline_id,
        }

    def set_active_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Set a baseline as the active baseline for drift comparison.

        Args:
            baseline_id: ID of the baseline to set as active

        Returns:
            Dict with result
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        row = MBModel.query.filter_by(baseline_id=baseline_id).first()
        if not row:
            return {"success": False, "error": "Baseline not found"}

        # Bulk UPDATE is tenant-filtered (ADR-0003): only this organisation's
        # own baselines are cleared.
        MBModel.query.update({MBModel.is_active: False})
        row.is_active = True
        db.session.commit()

        return {
            "success": True,
            "active_baseline": {"id": row.baseline_id, "name": row.name},
            "message": f"Baseline '{row.name}' set as active",
        }

    def delete_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Delete a baseline.

        Args:
            baseline_id: ID of the baseline to delete

        Returns:
            Dict with result
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        row = MBModel.query.filter_by(baseline_id=baseline_id).first()
        if not row:
            return {"success": False, "error": "Baseline not found"}

        db.session.delete(row)
        db.session.commit()

        return {"success": True, "message": "Baseline deleted successfully"}

    # =========================================================================
    # Scanning and Drift Detection
    # =========================================================================

    def trigger_scan(self, created_by: Optional[str] = None) -> Dict[str, Any]:
        """
        Trigger a manual architecture scan and drift analysis.

        Args:
            created_by: User who triggered the scan

        Returns:
            Dict with scan results and any new alerts
        """
        if self._status == MonitoringStatus.PAUSED:
            return {
                "success": False,
                "error": "Monitoring is paused. Resume monitoring to trigger scans.",
            }

        try:
            scan_start = datetime.utcnow()
            new_alerts = []

            active_row = self._active_baseline_row()

            # If no active baseline, just capture current state
            if not active_row:
                # Run gap discovery
                gap_results = self._run_gap_discovery()

                return {
                    "success": True,
                    "scan_time": scan_start.isoformat(),
                    "message": "Scan completed. No active baseline for drift comparison.",
                    "gap_summary": gap_results.get("summary", {}),
                    "new_alerts": [],
                    "recommendation": "Capture a baseline to enable drift detection",
                }

            # Perform drift analysis against active baseline
            drift_analysis = self.analyze_drift(active_row.baseline_id)

            if drift_analysis.get("success"):
                new_alerts = drift_analysis.get("alerts", [])

            scan_duration = (datetime.utcnow() - scan_start).total_seconds()

            return {
                "success": True,
                "scan_time": scan_start.isoformat(),
                "scan_duration_seconds": round(scan_duration, 2),
                "drift_analysis": drift_analysis.get("drift_analysis"),
                "new_alerts_count": len(new_alerts),
                "new_alerts": new_alerts[:10],  # Return first 10 alerts
                "message": f"Scan completed. {len(new_alerts)} alerts generated.",
            }

        except Exception as e:
            logger.error(f"Error during scan: {e}")
            self._status = MonitoringStatus.ERROR
            return {"success": False, "error": str(e)}

    def analyze_drift(self, baseline_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze architecture drift against a baseline.

        Args:
            baseline_id: ID of baseline to compare against (uses active if not provided)

        Returns:
            Dict with drift analysis results
        """
        from app.models.policy_monitoring import MonitoringBaseline as MBModel

        if baseline_id:
            row = MBModel.query.filter_by(baseline_id=baseline_id).first()
        else:
            row = self._active_baseline_row()

        if not row:
            return {"success": False, "error": "No valid baseline for comparison"}

        snapshot = json.loads(row.snapshot_data) if row.snapshot_data else {}
        capabilities_snapshot = snapshot.get("capabilities", [])

        # A baseline with no capability snapshot, or where no capability in it
        # has an assessed maturity level, has nothing to compare against.
        # Treating that as zero drift would fabricate a measurement, so this
        # is reported honestly as "unknown" (HTTP 200 from the route, not a
        # 500 and not a silent zero).
        unassessed = (not capabilities_snapshot) or all(
            c.get("current_maturity") is None for c in capabilities_snapshot
        )
        if unassessed:
            return self._unknown_drift(
                row,
                "Baseline has no capability snapshot, or no capability in it has an "
                "assessed maturity level, so drift cannot be computed.",
            )

        baseline_coverage = snapshot.get("coverage", {})
        baseline_health = snapshot.get("health", {})
        baseline_gaps = snapshot.get("gaps", [])
        baseline_vendors = snapshot.get("vendors", [])

        analysis_time = datetime.utcnow()

        try:
            # Capture current state
            current_capabilities = self._capture_capabilities_snapshot()
            current_coverage = self._capture_coverage_snapshot()
            current_health = self._capture_health_snapshot()
            current_gaps = self._capture_gap_snapshot()
            current_vendors = self._capture_vendor_snapshot()

            # Coverage and health drift are both a subtraction over an
            # "average_*" figure that is None, by design, when nothing was
            # measured (see _capture_health_snapshot / the health service's
            # own None-not-zero comment) -- never 0. None minus None crashes
            # a real tenant with capabilities but no health scores yet
            # (reported against this service before this guard existed).
            # Treating a missing measure on either side as 0 would fabricate
            # a drift figure, so report the comparison as impossible instead.
            missing_measures = []
            if baseline_coverage.get("average_coverage") is None:
                missing_measures.append("the baseline's average coverage")
            if current_coverage.get("average_coverage") is None:
                missing_measures.append("the current average coverage")
            if baseline_health.get("average_health") is None:
                missing_measures.append("the baseline's average health score")
            if current_health.get("average_health") is None:
                missing_measures.append("the current average health score")

            if missing_measures:
                return self._unknown_drift(
                    row,
                    "Drift cannot be computed because "
                    f"{', '.join(missing_measures)} {'is' if len(missing_measures) == 1 else 'are'} "
                    "not available.",
                )

            # Analyze each dimension
            coverage_drift = self._analyze_coverage_drift(baseline_coverage, current_coverage)

            health_drift = self._analyze_health_drift(baseline_health, current_health)

            gap_drift = self._analyze_gap_drift(baseline_gaps, current_gaps)

            vendor_drift = self._analyze_vendor_drift(baseline_vendors, current_vendors)

            capability_drift = self._analyze_capability_drift(
                capabilities_snapshot, current_capabilities
            )

            # Generate alerts based on drift
            alerts = self._generate_drift_alerts(
                coverage_drift, health_drift, gap_drift, vendor_drift, capability_drift
            )

            # Store new alerts
            for alert in alerts:
                self._persist_alert(alert)

            # Calculate totals
            critical_count = sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL.value)
            warning_count = sum(1 for a in alerts if a.severity == AlertSeverity.WARNING.value)
            info_count = sum(1 for a in alerts if a.severity == AlertSeverity.INFO.value)

            # Generate summary
            summary = self._generate_drift_summary(
                coverage_drift, health_drift, gap_drift, len(alerts)
            )

            drift_result = DriftAnalysis(
                baseline_id=row.baseline_id,
                baseline_name=row.name,
                analysis_timestamp=analysis_time.isoformat(),
                total_drifts=len(alerts),
                critical_drifts=critical_count,
                warning_drifts=warning_count,
                info_drifts=info_count,
                coverage_drift=coverage_drift,
                health_drift=health_drift,
                gap_drift=gap_drift,
                vendor_drift=vendor_drift,
                alerts=[asdict(a) for a in alerts],
                summary=summary,
            )

            return {
                "success": True,
                "drift_analysis": asdict(drift_result),
                "alerts": [asdict(a) for a in alerts],
            }

        except Exception as e:
            logger.error(f"Error analyzing drift: {e}")
            return {"success": False, "error": str(e)}

    # =========================================================================
    # Alert Management
    # =========================================================================

    def get_alerts(
        self,
        severity: Optional[str] = None,
        alert_type: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get alerts with optional filters.

        Args:
            severity: Filter by severity (info, warning, critical)
            alert_type: Filter by alert type
            acknowledged: Filter by acknowledgment status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Dict with alerts
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        query = MAModel.query
        if severity:
            query = query.filter_by(severity=severity)
        if alert_type:
            query = query.filter_by(alert_type=alert_type)
        if acknowledged is not None:
            query = query.filter_by(acknowledged=acknowledged)

        total = query.count()
        rows = query.order_by(MAModel.created_at.desc()).offset(offset).limit(limit).all()

        return {
            "success": True,
            "alerts": [self._alert_to_api_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "filters_applied": {
                "severity": severity,
                "alert_type": alert_type,
                "acknowledged": acknowledged,
            },
        }

    def get_alert(self, alert_id: str) -> Dict[str, Any]:
        """
        Get a specific alert by ID.

        Args:
            alert_id: ID of the alert

        Returns:
            Dict with alert details
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        row = MAModel.query.filter_by(alert_id=alert_id).first()
        if not row:
            return {"success": False, "error": "Alert not found"}

        return {"success": True, "alert": self._alert_to_api_dict(row)}

    def acknowledge_alert(
        self, alert_id: str, acknowledged_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Acknowledge an alert.

        Args:
            alert_id: ID of the alert to acknowledge
            acknowledged_by: User who acknowledged

        Returns:
            Dict with result
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        row = MAModel.query.filter_by(alert_id=alert_id).first()
        if not row:
            return {"success": False, "error": "Alert not found"}

        row.acknowledged = True
        row.acknowledged_by = acknowledged_by
        row.acknowledged_at = datetime.utcnow()
        db.session.commit()

        return {
            "success": True,
            "alert": self._alert_to_api_dict(row),
            "message": "Alert acknowledged",
        }

    def bulk_acknowledge_alerts(
        self, alert_ids: List[str], acknowledged_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Acknowledge multiple alerts.

        Args:
            alert_ids: List of alert IDs to acknowledge
            acknowledged_by: User who acknowledged

        Returns:
            Dict with result
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        acknowledged = 0
        not_found = 0
        now = datetime.utcnow()

        for alert_id in alert_ids:
            row = MAModel.query.filter_by(alert_id=alert_id).first()
            if row:
                row.acknowledged = True
                row.acknowledged_by = acknowledged_by
                row.acknowledged_at = now
                acknowledged += 1
            else:
                not_found += 1

        db.session.commit()

        return {
            "success": True,
            "acknowledged": acknowledged,
            "not_found": not_found,
            "message": f"{acknowledged} alerts acknowledged",
        }

    def clear_acknowledged_alerts(self) -> Dict[str, Any]:
        """
        Clear all acknowledged alerts.

        Returns:
            Dict with result
        """
        from app.models.policy_monitoring import MonitoringAlert as MAModel

        rows = MAModel.query.filter_by(acknowledged=True).all()
        cleared = len(rows)
        for row in rows:
            db.session.delete(row)
        db.session.commit()

        return {
            "success": True,
            "cleared": cleared,
            "message": f"{cleared} acknowledged alerts cleared",
        }

    # =========================================================================
    # Internal Helper Methods - Snapshot Capture
    # =========================================================================

    def _capture_capabilities_snapshot(self) -> List[Dict[str, Any]]:
        """Capture snapshot of all capabilities."""
        try:
            capabilities = UnifiedCapability.query.all()
            snapshot = []

            for cap in capabilities:
                # Get mapping count
                mapping_count = UnifiedApplicationCapabilityMapping.query.filter_by(
                    unified_capability_id=cap.id, is_active=True
                ).count()

                snapshot.append(
                    {
                        "id": cap.id,
                        "name": cap.name,
                        "code": cap.code,
                        "level": cap.level,
                        "domain_id": cap.domain_id,
                        "strategic_importance": cap.strategic_importance,
                        "business_criticality": cap.business_criticality,
                        # T-002: these are UnifiedCapability rows (the maturity
                        # authority) — the columns are *_maturity_level, not
                        # *_maturity; the old names never existed on this model
                        # and silently raised AttributeError, caught by this
                        # method's outer try/except and logged as "Error
                        # capturing capabilities snapshot" instead of reaching
                        # the caller.
                        "target_maturity": cap.target_maturity_level,
                        "current_maturity": cap.current_maturity_level,
                        "mapping_count": mapping_count,
                    }
                )

            return snapshot

        except Exception as e:
            logger.error(f"Error capturing capabilities snapshot: {e}")
            return []

    def _capture_coverage_snapshot(self) -> Dict[str, Any]:
        """Capture snapshot of coverage metrics."""
        try:
            capabilities = UnifiedCapability.query.all()

            total_coverage = 0
            covered_count = 0
            uncovered_count = 0
            coverage_by_domain = defaultdict(lambda: {"total": 0, "covered": 0})

            for cap in capabilities:
                mappings = UnifiedApplicationCapabilityMapping.query.filter_by(
                    unified_capability_id=cap.id, is_active=True
                ).all()

                if mappings:
                    avg_coverage = sum(m.coverage_percentage or 0 for m in mappings) / len(mappings)
                    total_coverage += avg_coverage
                    covered_count += 1
                else:
                    uncovered_count += 1

                domain_key = str(cap.domain_id) if cap.domain_id else "unknown"
                coverage_by_domain[domain_key]["total"] += 1
                if mappings:
                    coverage_by_domain[domain_key]["covered"] += 1

            total_capabilities = len(capabilities)
            average_coverage = total_coverage / covered_count if covered_count > 0 else 0

            return {
                "total_capabilities": total_capabilities,
                "covered_capabilities": covered_count,
                "uncovered_capabilities": uncovered_count,
                "average_coverage": round(average_coverage, 2),
                "coverage_percentage": round(
                    (covered_count / total_capabilities * 100) if total_capabilities > 0 else 0, 2
                ),
                "coverage_by_domain": dict(coverage_by_domain),
            }

        except Exception as e:
            logger.error(f"Error capturing coverage snapshot: {e}")
            raise  # do not persist a fabricated zero-coverage snapshot; caller returns an honest failure

    def _capture_health_snapshot(self) -> Dict[str, Any]:
        """Capture snapshot of health metrics."""
        try:
            # Try to use the capability health service
            from app.services.capability_health_service import CapabilityHealthService

            health_service = CapabilityHealthService()
            health_metrics = health_service.get_capability_health_metrics()

            return {
                "average_health": health_metrics.get("average_health", 0),
                "total_capabilities": health_metrics.get("total_capabilities", 0),
                "critical_capabilities": health_metrics.get("critical_capabilities", 0),
                "at_risk_capabilities": health_metrics.get("at_risk_capabilities", 0),
                "health_by_domain": health_metrics.get("health_by_domain", []),
            }

        except Exception as e:
            logger.warning(f"Could not capture health snapshot: {e}")
            raise  # do not persist a fabricated zero-health snapshot; caller returns an honest failure

    def _capture_gap_snapshot(self) -> List[Dict[str, Any]]:
        """Capture snapshot of current gaps."""
        try:
            # Try to use the AI gap detection service
            from app.services.ai_gap_detection_service import AIGapDetectionService

            gap_service = AIGapDetectionService()

            gaps = []

            # Get various gap types
            low_coverage = gap_service.find_low_coverage_capabilities(threshold=50)
            uncovered = gap_service.find_uncovered_capabilities()
            legacy_only = gap_service.find_capabilities_with_only_legacy_apps()

            for gap in low_coverage:
                gaps.append(
                    {
                        "type": "low_coverage",
                        "capability_id": gap.get("capability_id"),
                        "capability_name": gap.get("capability_name"),
                        "coverage": gap.get("current_coverage", 0),
                        "severity": gap.get("gap_severity"),
                    }
                )

            for gap in uncovered:
                gaps.append(
                    {
                        "type": "uncovered",
                        "capability_id": gap.get("capability_id"),
                        "capability_name": gap.get("capability_name"),
                        "coverage": 0,
                        "severity": gap.get("gap_severity"),
                    }
                )

            for gap in legacy_only:
                gaps.append(
                    {
                        "type": "legacy_only",
                        "capability_id": gap.get("capability_id"),
                        "capability_name": gap.get("capability_name"),
                        "legacy_app_count": gap.get("legacy_app_count", 0),
                        "severity": gap.get("modernization_urgency"),
                    }
                )

            return gaps

        except Exception as e:
            logger.warning(f"Could not capture gap snapshot: {e}")
            return []

    def _capture_vendor_snapshot(self) -> List[Dict[str, Any]]:
        """Capture snapshot of vendor product status."""
        try:
            from app.models.vendor.vendor_organization import VendorProduct

            vendors = []
            products = VendorProduct.query.all()

            for product in products:
                vendors.append(
                    {
                        "id": product.id,
                        "name": getattr(product, "name", "Unknown"),
                        "vendor_id": getattr(product, "vendor_id", None),
                        "status": getattr(product, "status", "unknown"),
                        "is_active": getattr(product, "is_active", True),
                    }
                )

            return vendors

        except Exception as e:
            logger.warning(f"Could not capture vendor snapshot: {e}")
            return []

    def _calculate_baseline_checksum(self, *snapshots) -> str:
        """Calculate checksum of baseline data for integrity."""
        data = json.dumps(snapshots, sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # =========================================================================
    # Internal Helper Methods - Drift Analysis
    # =========================================================================

    def _analyze_coverage_drift(
        self, baseline: Dict[str, Any], current: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze coverage drift.

        ``average_coverage`` is only absent from a very old snapshot; a
        current one always sets it. Even so, this never treats a missing
        value as 0 -- a caller that reaches this directly (bypassing
        analyze_drift's own upfront guard) still gets ``None``, not a
        fabricated zero delta or a crash on ``None - None``.
        """
        baseline_avg = baseline.get("average_coverage")
        current_avg = current.get("average_coverage")
        comparable = baseline_avg is not None and current_avg is not None

        baseline_uncovered = baseline.get("uncovered_capabilities", 0)
        current_uncovered = current.get("uncovered_capabilities", 0)

        return {
            "baseline_average_coverage": baseline_avg,
            "current_average_coverage": current_avg,
            "coverage_delta": round(current_avg - baseline_avg, 2) if comparable else None,
            "baseline_uncovered": baseline_uncovered,
            "current_uncovered": current_uncovered,
            "uncovered_delta": current_uncovered - baseline_uncovered,
            "has_regression": (current_avg < baseline_avg) if comparable else None,
        }

    def _analyze_health_drift(
        self, baseline: Dict[str, Any], current: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze health score drift.

        ``average_health`` is ``None``, by design, whenever no capability has
        been assessed yet (a realistic tenant state, not an edge case) -- so
        this never subtracts through a missing value as if it were 0. A
        caller that reaches this directly (bypassing analyze_drift's own
        upfront guard) still gets ``None`` deltas rather than a crash.
        """
        baseline_health = baseline.get("average_health")
        current_health = current.get("average_health")
        comparable = baseline_health is not None and current_health is not None

        baseline_at_risk = baseline.get("at_risk_capabilities", 0)
        current_at_risk = current.get("at_risk_capabilities", 0)

        return {
            "baseline_average_health": baseline_health,
            "current_average_health": current_health,
            "health_delta": round(current_health - baseline_health, 2) if comparable else None,
            "baseline_at_risk": baseline_at_risk,
            "current_at_risk": current_at_risk,
            "at_risk_delta": current_at_risk - baseline_at_risk,
            "has_regression": (current_health < baseline_health) if comparable else None,
        }

    def _analyze_gap_drift(
        self, baseline_gaps: List[Dict[str, Any]], current_gaps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze gap drift."""
        baseline_ids = {g.get("capability_id") for g in baseline_gaps}
        current_ids = {g.get("capability_id") for g in current_gaps}

        new_gaps = current_ids - baseline_ids
        resolved_gaps = baseline_ids - current_ids

        return {
            "baseline_gap_count": len(baseline_gaps),
            "current_gap_count": len(current_gaps),
            "gap_delta": len(current_gaps) - len(baseline_gaps),
            "new_gaps_count": len(new_gaps),
            "resolved_gaps_count": len(resolved_gaps),
            "new_gap_ids": list(new_gaps),
            "resolved_gap_ids": list(resolved_gaps),
            "has_new_gaps": len(new_gaps) > 0,
        }

    def _analyze_vendor_drift(
        self, baseline_vendors: List[Dict[str, Any]], current_vendors: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze vendor/product drift."""
        baseline_active = sum(1 for v in baseline_vendors if v.get("is_active", True))
        current_active = sum(1 for v in current_vendors if v.get("is_active", True))

        return {
            "baseline_vendor_count": len(baseline_vendors),
            "current_vendor_count": len(current_vendors),
            "baseline_active": baseline_active,
            "current_active": current_active,
            "active_delta": current_active - baseline_active,
            "has_changes": len(baseline_vendors) != len(current_vendors),
        }

    def _analyze_capability_drift(
        self, baseline_caps: List[Dict[str, Any]], current_caps: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Analyze capability drift."""
        baseline_ids = {c.get("id") for c in baseline_caps}
        current_ids = {c.get("id") for c in current_caps}

        new_caps = current_ids - baseline_ids
        removed_caps = baseline_ids - current_ids

        # Check for maturity regressions
        baseline_map = {c.get("id"): c for c in baseline_caps}
        maturity_regressions = []

        for cap in current_caps:
            cap_id = cap.get("id")
            if cap_id in baseline_map:
                baseline_maturity = baseline_map[cap_id].get("current_maturity")
                current_maturity = cap.get("current_maturity")
                # Cannot compare against an unassessed state: neither None baseline nor
                # None current may be coerced to 0, which would fabricate a CMM level 0
                # that does not exist on the scale (fabricated-data gate).
                if baseline_maturity is None or current_maturity is None:
                    continue
                if current_maturity < baseline_maturity:
                    maturity_regressions.append(
                        {
                            "capability_id": cap_id,
                            "capability_name": cap.get("name"),
                            "baseline_maturity": baseline_maturity,
                            "current_maturity": current_maturity,
                        }
                    )

        return {
            "baseline_count": len(baseline_caps),
            "current_count": len(current_caps),
            "new_capabilities": len(new_caps),
            "removed_capabilities": len(removed_caps),
            "new_capability_ids": list(new_caps),
            "removed_capability_ids": list(removed_caps),
            "maturity_regressions": maturity_regressions,
            "has_changes": len(new_caps) > 0
            or len(removed_caps) > 0
            or len(maturity_regressions) > 0,
        }

    def _run_gap_discovery(self) -> Dict[str, Any]:
        """Run gap discovery service."""
        try:
            from app.services.gap_discovery_service import GapDiscoveryService

            service = GapDiscoveryService()
            return service.discover_all_gaps()
        except Exception as e:  # fabricated-ok: empty gap list on discovery failure, no fabricated scalar
            logger.warning(f"Could not run gap discovery: {e}")
            return {"gaps": [], "summary": {}}

    # =========================================================================
    # Internal Helper Methods - Alert Generation
    # =========================================================================

    def _generate_drift_alerts(
        self,
        coverage_drift: Dict[str, Any],
        health_drift: Dict[str, Any],
        gap_drift: Dict[str, Any],
        vendor_drift: Dict[str, Any],
        capability_drift: Dict[str, Any],
    ) -> List[ArchitectureAlert]:
        """Generate alerts based on drift analysis."""
        alerts = []
        now = datetime.utcnow().isoformat()

        # Coverage decrease alerts
        coverage_delta = coverage_drift.get("coverage_delta", 0)
        if coverage_delta < -self.COVERAGE_DECREASE_CRITICAL_THRESHOLD:
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.COVERAGE_DECREASE.value,
                    severity=AlertSeverity.CRITICAL.value,
                    title="Critical Coverage Decrease",
                    description=f"Average coverage decreased by {abs(coverage_delta):.1f}% from baseline",
                    affected_element_id=None,
                    affected_element_type="architecture",
                    affected_element_name="Overall Coverage",
                    baseline_value=coverage_drift.get("baseline_average_coverage"),
                    current_value=coverage_drift.get("current_average_coverage"),
                    delta=coverage_delta,
                    recommended_action="Investigate root cause and implement coverage improvement plan",
                    created_at=now,
                )
            )
        elif coverage_delta < -self.COVERAGE_DECREASE_WARNING_THRESHOLD:
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.COVERAGE_DECREASE.value,
                    severity=AlertSeverity.WARNING.value,
                    title="Coverage Decrease Detected",
                    description=f"Average coverage decreased by {abs(coverage_delta):.1f}% from baseline",
                    affected_element_id=None,
                    affected_element_type="architecture",
                    affected_element_name="Overall Coverage",
                    baseline_value=coverage_drift.get("baseline_average_coverage"),
                    current_value=coverage_drift.get("current_average_coverage"),
                    delta=coverage_delta,
                    recommended_action="Review capability coverage and address gaps",
                    created_at=now,
                )
            )

        # Health score decrease alerts
        health_delta = health_drift.get("health_delta", 0)
        if health_delta < -self.HEALTH_SCORE_CRITICAL_THRESHOLD:
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.MATURITY_REGRESSION.value,
                    severity=AlertSeverity.CRITICAL.value,
                    title="Critical Health Score Decrease",
                    description=f"Average health score decreased by {abs(health_delta):.0f} points from baseline",
                    affected_element_id=None,
                    affected_element_type="architecture",
                    affected_element_name="Overall Health",
                    baseline_value=health_drift.get("baseline_average_health"),
                    current_value=health_drift.get("current_average_health"),
                    delta=health_delta,
                    recommended_action="Immediate review of capability health required",
                    created_at=now,
                )
            )
        elif health_delta < -self.HEALTH_SCORE_WARNING_THRESHOLD:
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.MATURITY_REGRESSION.value,
                    severity=AlertSeverity.WARNING.value,
                    title="Health Score Decrease Detected",
                    description=f"Average health score decreased by {abs(health_delta):.0f} points from baseline",
                    affected_element_id=None,
                    affected_element_type="architecture",
                    affected_element_name="Overall Health",
                    baseline_value=health_drift.get("baseline_average_health"),
                    current_value=health_drift.get("current_average_health"),
                    delta=health_delta,
                    recommended_action="Review at-risk capabilities and prioritize remediation",
                    created_at=now,
                )
            )

        # New gap alerts
        new_gaps_count = gap_drift.get("new_gaps_count", 0)
        if new_gaps_count > 0:
            severity = (
                AlertSeverity.CRITICAL
                if new_gaps_count >= 5
                else (AlertSeverity.WARNING if new_gaps_count >= 2 else AlertSeverity.INFO)
            )
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.NEW_GAP.value,
                    severity=severity.value,
                    title=f"{new_gaps_count} New Gap(s) Detected",
                    description=f"{new_gaps_count} new capability gap(s) identified since baseline",
                    affected_element_id=None,
                    affected_element_type="capability",
                    affected_element_name="Multiple Capabilities",
                    baseline_value=gap_drift.get("baseline_gap_count"),
                    current_value=gap_drift.get("current_gap_count"),
                    delta=new_gaps_count,
                    recommended_action="Review new gaps and create remediation plans",
                    created_at=now,
                    metadata={"new_gap_ids": gap_drift.get("new_gap_ids", [])},
                )
            )

        # Maturity regression alerts
        for regression in capability_drift.get("maturity_regressions", []):
            alerts.append(
                ArchitectureAlert(
                    id=str(uuid4()),
                    alert_type=AlertType.MATURITY_REGRESSION.value,
                    severity=AlertSeverity.WARNING.value,
                    title=f"Maturity Regression: {regression.get('capability_name', 'Unknown')}",
                    description=f"Capability maturity decreased from {regression.get('baseline_maturity')} to {regression.get('current_maturity')}",
                    affected_element_id=regression.get("capability_id"),
                    affected_element_type="capability",
                    affected_element_name=regression.get("capability_name"),
                    baseline_value=regression.get("baseline_maturity"),
                    current_value=regression.get("current_maturity"),
                    delta=regression.get("current_maturity", 0)
                    - regression.get("baseline_maturity", 0),
                    recommended_action="Investigate maturity regression and take corrective action",
                    created_at=now,
                )
            )

        # Vendor changes alerts
        if vendor_drift.get("has_changes"):
            active_delta = vendor_drift.get("active_delta", 0)
            if active_delta < 0:
                alerts.append(
                    ArchitectureAlert(
                        id=str(uuid4()),
                        alert_type=AlertType.VENDOR_RISK_CHANGE.value,
                        severity=AlertSeverity.WARNING.value,
                        title="Vendor Product Changes Detected",
                        description=f"{abs(active_delta)} vendor product(s) became inactive",
                        affected_element_id=None,
                        affected_element_type="vendor",
                        affected_element_name="Vendor Products",
                        baseline_value=vendor_drift.get("baseline_active"),
                        current_value=vendor_drift.get("current_active"),
                        delta=active_delta,
                        recommended_action="Review affected vendor products and assess impact",
                        created_at=now,
                    )
                )

        return alerts

    def _generate_drift_summary(
        self,
        coverage_drift: Dict[str, Any],
        health_drift: Dict[str, Any],
        gap_drift: Dict[str, Any],
        alert_count: int,
    ) -> str:
        """Generate human-readable drift summary."""
        parts = []

        coverage_delta = coverage_drift.get("coverage_delta", 0)
        if abs(coverage_delta) > 0.5:
            direction = "increased" if coverage_delta > 0 else "decreased"
            parts.append(f"Coverage {direction} by {abs(coverage_delta):.1f}%")

        health_delta = health_drift.get("health_delta", 0)
        if abs(health_delta) > 1:
            direction = "improved" if health_delta > 0 else "declined"
            parts.append(f"Health score {direction} by {abs(health_delta):.0f} points")

        new_gaps = gap_drift.get("new_gaps_count", 0)
        resolved_gaps = gap_drift.get("resolved_gaps_count", 0)
        if new_gaps > 0:
            parts.append(f"{new_gaps} new gap(s) detected")
        if resolved_gaps > 0:
            parts.append(f"{resolved_gaps} gap(s) resolved")

        if not parts:
            return f"No significant drift detected. {alert_count} alert(s) generated."

        return f"{'; '.join(parts)}. {alert_count} alert(s) generated."
