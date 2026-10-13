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
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_

from app import db
from app.models.application_portfolio import ApplicationComponent
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
class ArchitectureBaseline:
    """Represents an architecture baseline snapshot."""

    id: str
    name: str
    created_at: str
    created_by: Optional[str]
    description: Optional[str]
    capabilities_snapshot: List[Dict[str, Any]]
    coverage_snapshot: Dict[str, Any]
    health_snapshot: Dict[str, Any]
    gap_snapshot: List[Dict[str, Any]]
    vendor_snapshot: List[Dict[str, Any]]
    checksum: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    # None means "captured before the model dimension existed" -- never {}.
    # A baseline captured after this change always has a dict here (even an
    # empty-estate one), so the drift comparison can tell the two cases apart.
    model_snapshot: Optional[Dict[str, Any]] = None


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
    model_drift: Dict[str, Any]
    alerts: List[Dict[str, Any]]
    summary: str


@dataclass
class _TenantState:
    """One organisation's monitoring cache.

    Lives only in the module-level ``_STATE_CACHE`` map below, keyed by
    organization_id -- never as a class attribute of
    ArchitectureMonitoringService, which is what made the old cache
    process-wide and shared by every tenant's instance.
    """

    baselines: Dict[str, ArchitectureBaseline] = field(default_factory=dict)
    alerts: Dict[str, ArchitectureAlert] = field(default_factory=dict)
    status: MonitoringStatus = MonitoringStatus.ACTIVE
    last_scan_time: Optional[datetime] = None
    scan_interval_minutes: int = 60
    active_baseline_id: Optional[str] = None
    db_loaded: bool = False
    # Set on every instantiation that reuses this entry and on every active-
    # baseline change; read by _evict_stale_state's TTL check and by
    # _evict_oldest_state_if_full's cap check below.
    last_touched: float = field(default_factory=time.time)


# Per-organisation cache, one entry per tenant that has instantiated the
# service in this process. Replaces the old class-level _baselines / _alerts /
# _status / ... attributes, which every tenant's instance shared. Named to
# match scripts/check_cache_tenancy.py's cache-name pattern (the plain
# "_STATE" this replaces did not, so the gate never looked at it) -- shares
# _health_metrics_cache's (capability_health_service.py) TTL and its 256-
# tenant cap below, and is also dropped the moment a tenant's active baseline
# changes, since that is the one field most likely to be read stale by a
# concurrent worker process that made the change.
_STATE_CACHE: Dict[int, _TenantState] = {}

_STATE_TTL_SECONDS = 60
# Bound the map so a long-lived process serving many tenants cannot grow it
# without limit, the same cap _health_metrics_cache uses.
_STATE_CACHE_MAX_TENANTS = 256

# Bound how many stale-derived-fact ids one model-snapshot capture writes
# into the persisted baseline blob, the same shape as the cap above: a
# tenant with a stale set past this size still gets an honest, capped id
# list plus the true count from derived_fact_aggregates (a real SQL COUNT,
# unaffected by this cap) -- not an ever-growing blob per capture.
_MODEL_SNAPSHOT_STALE_ID_CAP = 1000


def _evict_stale_state(organization_id: int) -> None:
    """Drop ``organization_id``'s entry if it has not been touched inside
    the TTL, so the next access reloads a fresh one from the database."""
    entry = _STATE_CACHE.get(organization_id)
    if entry is not None and (time.time() - entry.last_touched) >= _STATE_TTL_SECONDS:
        _STATE_CACHE.pop(organization_id, None)


def _evict_oldest_state_if_full() -> None:
    """Drop the least-recently-touched entry once the cache is at capacity.

    Unlike the TTL eviction above, this does not depend on the evicted
    tenant ever instantiating the service again: it runs on every
    instantiation that is about to add a *new* organisation's entry, and
    removes whichever organisation's entry is oldest, whether or not that
    is the organisation being added. The same shape _health_metrics_cache
    uses to stay bounded.
    """
    if len(_STATE_CACHE) < _STATE_CACHE_MAX_TENANTS:
        return
    oldest = min(_STATE_CACHE, key=lambda org_id: _STATE_CACHE[org_id].last_touched)
    _STATE_CACHE.pop(oldest, None)


def _tenant_capability_filter(organization_id: int):
    """Own rows plus NULL-owner reference rows.

    Mirrors the request-scoped SELECT predicate the do_orm_execute listener
    installs for UnifiedCapability (app/models/unified_capability.py:587-590):
    that listener is a no-op with no Flask request on the stack, so a CLI
    command or scheduled job reading through this service needs the same
    predicate applied explicitly.
    """
    return or_(
        UnifiedCapability.organization_id == organization_id,
        UnifiedCapability.organization_id.is_(None),
    )


class ArchitectureMonitoringService:
    """
    Service for continuous architecture monitoring and drift detection.

    Provides comprehensive monitoring capabilities:
    - Baseline architecture capture
    - Drift detection algorithms
    - Alert generation and management
    - Integration with existing services

    One instance is scoped to one organisation (``organization_id``,
    required): its cached state, and every row it reads or writes, belongs
    to that tenant only.
    """

    # Alert thresholds
    COVERAGE_DECREASE_WARNING_THRESHOLD = 5  # 5% decrease
    COVERAGE_DECREASE_CRITICAL_THRESHOLD = 15  # 15% decrease
    HEALTH_SCORE_WARNING_THRESHOLD = 10  # 10 point decrease
    HEALTH_SCORE_CRITICAL_THRESHOLD = 20  # 20 point decrease

    def __init__(self, organization_id: int):
        """Initialize the Architecture Monitoring Service for one organisation.

        organization_id is required: a service with no tenant would have to
        fall back to an unfiltered, cross-tenant read, which is the defect
        this class exists to not have.
        """
        if organization_id is None:
            raise ValueError("ArchitectureMonitoringService requires an organization_id")
        self.organization_id = organization_id
        _evict_stale_state(organization_id)
        if organization_id not in _STATE_CACHE:
            _evict_oldest_state_if_full()
        self._state = _STATE_CACHE.setdefault(organization_id, _TenantState())
        self._state.last_touched = time.time()
        self._ensure_loaded()

    @classmethod
    def reset_state(cls, organization_id: Optional[int] = None) -> None:
        """Clear the cached monitoring state for one organisation, or all of them.

        Test-only for the *manual, immediate* form: production code relies on
        the TTL and active-baseline-change eviction above instead of calling
        this directly.
        """
        if organization_id is None:
            _STATE_CACHE.clear()
        else:
            _STATE_CACHE.pop(organization_id, None)

    def _ensure_loaded(self):
        """Load this organisation's baselines and alerts from the database if not already loaded."""
        if self._state.db_loaded:
            return
        try:
            from app.models.policy_monitoring import MonitoringAlert as MAModel
            from app.models.policy_monitoring import MonitoringBaseline as MBModel

            # Explicit predicate over the mixin's own request-scoped filter:
            # defence in depth (the same two-layer rule query_service.py
            # documents), so a call with no Flask request on the stack is
            # scoped too.
            for row in MBModel.query.filter(
                MBModel.organization_id == self.organization_id
            ).all():
                snapshot = json.loads(row.snapshot_data) if row.snapshot_data else {}
                baseline = ArchitectureBaseline(
                    id=row.baseline_id,
                    name=row.name,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                    created_by=row.created_by,
                    description=row.description,
                    capabilities_snapshot=snapshot.get("capabilities", []),
                    coverage_snapshot=snapshot.get("coverage", {}),
                    health_snapshot=snapshot.get("health", {}),
                    gap_snapshot=snapshot.get("gaps", []),
                    vendor_snapshot=snapshot.get("vendors", []),
                    checksum=row.checksum,
                    metadata=snapshot.get("metadata", {}),
                    # No default {}: a JSON blob with no "model" key was
                    # written before this dimension existed, and that is a
                    # different fact from an empty snapshot.
                    model_snapshot=snapshot.get("model"),
                )
                self._state.baselines[row.baseline_id] = baseline
                if row.is_active:
                    self._state.active_baseline_id = row.baseline_id

            # Load alerts
            for row in MAModel.query.filter(
                MAModel.organization_id == self.organization_id
            ).all():
                alert = ArchitectureAlert(
                    id=row.alert_id,
                    alert_type=row.alert_type,
                    severity=row.severity,
                    title=row.title,
                    description=row.description or "",
                    affected_element_id=row.affected_element_id,
                    affected_element_type=row.affected_element_type,
                    affected_element_name=row.affected_element_name,
                    baseline_value=json.loads(row.baseline_value) if row.baseline_value else None,
                    current_value=json.loads(row.current_value) if row.current_value else None,
                    delta=row.delta,
                    recommended_action=row.recommended_action,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                    acknowledged=row.acknowledged or False,
                    acknowledged_by=row.acknowledged_by,
                    acknowledged_at=row.acknowledged_at.isoformat() if row.acknowledged_at else None,
                    metadata=json.loads(row.alert_metadata) if row.alert_metadata else {},
                )
                self._state.alerts[row.alert_id] = alert

            self._state.db_loaded = True
            logger.info(
                "Loaded %d baselines and %d alerts from database for organization %s",
                len(self._state.baselines), len(self._state.alerts), self.organization_id,
            )
        except Exception as e:
            logger.warning("Could not load monitoring data from database: %s", e)
            self._state.db_loaded = True  # Don't retry on every call

    def _persist_baseline(self, baseline: ArchitectureBaseline):
        """Save or update a baseline in the database, scoped to this tenant."""
        try:
            from app.models.policy_monitoring import MonitoringBaseline as MBModel

            snapshot_data = json.dumps({
                "capabilities": baseline.capabilities_snapshot,
                "coverage": baseline.coverage_snapshot,
                "health": baseline.health_snapshot,
                "gaps": baseline.gap_snapshot,
                "vendors": baseline.vendor_snapshot,
                "metadata": baseline.metadata,
                "model": baseline.model_snapshot,
            })

            existing = MBModel.query.filter_by(
                baseline_id=baseline.id, organization_id=self.organization_id
            ).first()
            if existing:
                existing.name = baseline.name
                existing.snapshot_data = snapshot_data
                existing.checksum = baseline.checksum
            else:
                row = MBModel(
                    baseline_id=baseline.id,
                    organization_id=self.organization_id,
                    name=baseline.name,
                    description=baseline.description,
                    created_by=baseline.created_by,
                    is_active=(baseline.id == self._state.active_baseline_id),
                    snapshot_data=snapshot_data,
                    checksum=baseline.checksum,
                )
                db.session.add(row)

            db.session.commit()
        except Exception as e:
            logger.error("Failed to persist baseline %s: %s", baseline.id, e)
            db.session.rollback()

    def _persist_alert(self, alert: ArchitectureAlert):
        """Save or update an alert in the database, scoped to this tenant."""
        try:
            from app.models.policy_monitoring import MonitoringAlert as MAModel

            existing = MAModel.query.filter_by(
                alert_id=alert.id, organization_id=self.organization_id
            ).first()
            if existing:
                existing.acknowledged = alert.acknowledged
                existing.acknowledged_by = alert.acknowledged_by
                existing.acknowledged_at = (
                    datetime.fromisoformat(alert.acknowledged_at)
                    if alert.acknowledged_at else None
                )
            else:
                row = MAModel(
                    alert_id=alert.id,
                    organization_id=self.organization_id,
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
        except Exception as e:
            logger.error("Failed to persist alert %s: %s", alert.id, e)
            db.session.rollback()

    def _delete_baseline_from_db(self, baseline_id: str):
        """Remove a baseline from the database, scoped to this tenant."""
        try:
            from app.models.policy_monitoring import MonitoringBaseline as MBModel

            MBModel.query.filter_by(
                baseline_id=baseline_id, organization_id=self.organization_id
            ).delete()
            db.session.commit()
        except Exception as e:
            logger.error("Failed to delete baseline %s from DB: %s", baseline_id, e)
            db.session.rollback()

    def _delete_alert_from_db(self, alert_id: str):
        """Remove an alert from the database, scoped to this tenant."""
        try:
            from app.models.policy_monitoring import MonitoringAlert as MAModel

            MAModel.query.filter_by(
                alert_id=alert_id, organization_id=self.organization_id
            ).delete()
            db.session.commit()
        except Exception as e:
            logger.error("Failed to delete alert %s from DB: %s", alert_id, e)
            db.session.rollback()

    def _update_active_baseline_in_db(self):
        """Update which baseline is marked active in the database, scoped to this tenant."""
        try:
            from app.models.policy_monitoring import MonitoringBaseline as MBModel

            MBModel.query.filter_by(organization_id=self.organization_id).update(
                {MBModel.is_active: False}
            )
            if self._state.active_baseline_id:
                MBModel.query.filter_by(
                    baseline_id=self._state.active_baseline_id,
                    organization_id=self.organization_id,
                ).update({MBModel.is_active: True})
            db.session.commit()
            # The active baseline is the one field in this cache another
            # worker process is most likely to change concurrently (a second
            # gunicorn worker handling the same tenant's activate/delete
            # call). Drop this tenant's entry now rather than wait out the
            # TTL, so the next instantiation -- in this process or, after the
            # next request lands here, any other -- reloads it from the
            # database instead of serving what this process last cached.
            _STATE_CACHE.pop(self.organization_id, None)
        except Exception as e:
            logger.error("Failed to update active baseline in DB: %s", e)
            db.session.rollback()

    # =========================================================================
    # Monitoring Status
    # =========================================================================

    def get_monitoring_status(self) -> Dict[str, Any]:
        """
        Get current monitoring status and configuration.

        Returns:
            Dict with monitoring status information
        """
        active_baseline = None
        if self._state.active_baseline_id and self._state.active_baseline_id in self._state.baselines:
            baseline = self._state.baselines[self._state.active_baseline_id]
            active_baseline = {
                "id": baseline.id,
                "name": baseline.name,
                "created_at": baseline.created_at,
            }

        # Count alerts by severity
        alert_counts = {"info": 0, "warning": 0, "critical": 0, "total": 0, "unacknowledged": 0}
        for alert in self._state.alerts.values():
            alert_counts["total"] += 1
            alert_counts[alert.severity] += 1
            if not alert.acknowledged:
                alert_counts["unacknowledged"] += 1

        return {
            "success": True,
            "status": self._state.status.value,
            "last_scan_time": self._state.last_scan_time.isoformat() if self._state.last_scan_time else None,
            "scan_interval_minutes": self._state.scan_interval_minutes,
            "active_baseline": active_baseline,
            "total_baselines": len(self._state.baselines),
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
            self._state.status = MonitoringStatus(status)
            return {
                "success": True,
                "status": self._state.status.value,
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
            self._state.scan_interval_minutes = max(5, scan_interval_minutes)  # Min 5 minutes

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
                "scan_interval_minutes": self._state.scan_interval_minutes,
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

            # Capture the model itself: element/relationship ids and the
            # derived-fact aggregates (the sixth dimension).
            model_snapshot = self._capture_model_snapshot()

            # Calculate checksum for integrity. model_snapshot's captured_at
            # is excluded: it is wall-clock time, not estate content, and
            # including it would give an unchanged estate a new checksum on
            # every single capture -- the same reason none of the other five
            # snapshots carry a capture timestamp inside their own hashed
            # content either.
            model_snapshot_for_checksum = {
                k: v for k, v in model_snapshot.items() if k != "captured_at"
            }
            checksum = self._calculate_baseline_checksum(
                capabilities_snapshot,
                coverage_snapshot,
                health_snapshot,
                gap_snapshot,
                vendor_snapshot,
                model_snapshot_for_checksum,
            )

            baseline = ArchitectureBaseline(
                id=baseline_id,
                name=name,
                created_at=datetime.utcnow().isoformat(),
                created_by=created_by,
                description=description,
                capabilities_snapshot=capabilities_snapshot,
                coverage_snapshot=coverage_snapshot,
                health_snapshot=health_snapshot,
                gap_snapshot=gap_snapshot,
                vendor_snapshot=vendor_snapshot,
                model_snapshot=model_snapshot,
                checksum=checksum,
            )

            self._state.baselines[baseline_id] = baseline

            if set_as_active:
                self._state.active_baseline_id = baseline_id

            # Persist to database
            self._persist_baseline(baseline)
            if set_as_active:
                self._update_active_baseline_in_db()

            return {
                "success": True,
                "baseline": {
                    "id": baseline.id,
                    "name": baseline.name,
                    "created_at": baseline.created_at,
                    "created_by": baseline.created_by,
                    "description": baseline.description,
                    "checksum": baseline.checksum,
                    "is_active": baseline_id == self._state.active_baseline_id,
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
            return {"success": False, "error": str(e)}

    def get_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Get a specific baseline by ID.

        Args:
            baseline_id: ID of the baseline

        Returns:
            Dict with baseline details
        """
        if baseline_id not in self._state.baselines:
            return {"success": False, "error": "Baseline not found"}

        baseline = self._state.baselines[baseline_id]

        return {
            "success": True,
            "baseline": {
                "id": baseline.id,
                "name": baseline.name,
                "created_at": baseline.created_at,
                "created_by": baseline.created_by,
                "description": baseline.description,
                "checksum": baseline.checksum,
                "is_active": baseline_id == self._state.active_baseline_id,
                "capabilities_snapshot": baseline.capabilities_snapshot,
                "coverage_snapshot": baseline.coverage_snapshot,
                "health_snapshot": baseline.health_snapshot,
                "gap_snapshot": baseline.gap_snapshot,
                "vendor_snapshot": baseline.vendor_snapshot,
                "metadata": baseline.metadata,
            },
        }

    def list_baselines(self) -> Dict[str, Any]:
        """
        List all captured baselines.

        Returns:
            Dict with list of baselines
        """
        baselines = []
        for baseline in self._state.baselines.values():
            baselines.append(
                {
                    "id": baseline.id,
                    "name": baseline.name,
                    "created_at": baseline.created_at,
                    "created_by": baseline.created_by,
                    "description": baseline.description,
                    "checksum": baseline.checksum,
                    "is_active": baseline.id == self._state.active_baseline_id,
                }
            )

        # Sort by creation date (newest first)
        baselines.sort(key=lambda x: x["created_at"], reverse=True)

        return {
            "success": True,
            "baselines": baselines,
            "total": len(baselines),
            "active_baseline_id": self._state.active_baseline_id,
        }

    def set_active_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Set a baseline as the active baseline for drift comparison.

        Args:
            baseline_id: ID of the baseline to set as active

        Returns:
            Dict with result
        """
        if baseline_id not in self._state.baselines:
            return {"success": False, "error": "Baseline not found"}

        self._state.active_baseline_id = baseline_id
        baseline = self._state.baselines[baseline_id]

        return {
            "success": True,
            "active_baseline": {"id": baseline.id, "name": baseline.name},
            "message": f"Baseline '{baseline.name}' set as active",
        }

    def delete_baseline(self, baseline_id: str) -> Dict[str, Any]:
        """
        Delete a baseline.

        Args:
            baseline_id: ID of the baseline to delete

        Returns:
            Dict with result
        """
        if baseline_id not in self._state.baselines:
            return {"success": False, "error": "Baseline not found"}

        if baseline_id == self._state.active_baseline_id:
            self._state.active_baseline_id = None

        del self._state.baselines[baseline_id]
        self._delete_baseline_from_db(baseline_id)
        if baseline_id == self._state.active_baseline_id:
            self._update_active_baseline_in_db()

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
        if self._state.status == MonitoringStatus.PAUSED:
            return {
                "success": False,
                "error": "Monitoring is paused. Resume monitoring to trigger scans.",
            }

        try:
            scan_start = datetime.utcnow()
            new_alerts = []

            # If no active baseline, just capture current state
            if not self._state.active_baseline_id:
                # Run gap discovery
                gap_results = self._run_gap_discovery()

                self._state.last_scan_time = scan_start

                return {
                    "success": True,
                    "scan_time": scan_start.isoformat(),
                    "message": "Scan completed. No active baseline for drift comparison.",
                    "gap_summary": gap_results.get("summary", {}),
                    "new_alerts": [],
                    "recommendation": "Capture a baseline to enable drift detection",
                }

            # Perform drift analysis against active baseline
            drift_analysis = self.analyze_drift(self._state.active_baseline_id)

            if drift_analysis.get("success"):
                new_alerts = drift_analysis.get("alerts", [])

            self._state.last_scan_time = scan_start
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
            self._state.status = MonitoringStatus.ERROR
            return {"success": False, "error": str(e)}

    def compare_to_baseline(self, baseline_id: Optional[str] = None) -> DriftAnalysis:
        """Pure comparison: capture the current state, diff it against a
        baseline, and return the result. Writes nothing -- no alert
        persistence, no cache mutation, no ``_last_scan_time`` update. A GET
        through this seam never writes.

        Raises:
            LookupError: no baseline id was given and none is active, or the
                given id names no baseline this tenant holds.
        """
        target_baseline_id = baseline_id or self._state.active_baseline_id

        if not target_baseline_id or target_baseline_id not in self._state.baselines:
            raise LookupError("No valid baseline for comparison")

        baseline = self._state.baselines[target_baseline_id]
        analysis_time = datetime.utcnow()

        # Capture current state, all six dimensions.
        current_capabilities = self._capture_capabilities_snapshot()
        current_coverage = self._capture_coverage_snapshot()
        current_health = self._capture_health_snapshot()
        current_gaps = self._capture_gap_snapshot()
        current_vendors = self._capture_vendor_snapshot()
        current_model = self._capture_model_snapshot()

        # Analyze each dimension
        coverage_drift = self._analyze_coverage_drift(
            baseline.coverage_snapshot, current_coverage
        )

        health_drift = self._analyze_health_drift(baseline.health_snapshot, current_health)

        gap_drift = self._analyze_gap_drift(baseline.gap_snapshot, current_gaps)

        vendor_drift = self._analyze_vendor_drift(baseline.vendor_snapshot, current_vendors)

        capability_drift = self._analyze_capability_drift(
            baseline.capabilities_snapshot, current_capabilities
        )

        model_drift = self._analyze_model_drift(baseline.model_snapshot, current_model)

        # Generate alerts based on drift -- in memory only; this seam does
        # not persist them (no alert type reads the model dimension yet).
        alerts = self._generate_drift_alerts(
            coverage_drift, health_drift, gap_drift, vendor_drift, capability_drift
        )

        critical_count = sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL.value)
        warning_count = sum(1 for a in alerts if a.severity == AlertSeverity.WARNING.value)
        info_count = sum(1 for a in alerts if a.severity == AlertSeverity.INFO.value)

        summary = self._generate_drift_summary(
            coverage_drift, health_drift, gap_drift, len(alerts)
        )

        return DriftAnalysis(
            baseline_id=baseline.id,
            baseline_name=baseline.name,
            analysis_timestamp=analysis_time.isoformat(),
            total_drifts=len(alerts),
            critical_drifts=critical_count,
            warning_drifts=warning_count,
            info_drifts=info_count,
            coverage_drift=coverage_drift,
            health_drift=health_drift,
            gap_drift=gap_drift,
            vendor_drift=vendor_drift,
            model_drift=model_drift,
            alerts=[asdict(a) for a in alerts],
            summary=summary,
        )

    def analyze_drift(self, baseline_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Analyze architecture drift against a baseline, persisting the
        alerts the comparison found.

        Args:
            baseline_id: ID of baseline to compare against (uses active if not provided)

        Returns:
            Dict with drift analysis results
        """
        try:
            analysis = self.compare_to_baseline(baseline_id)

            # Store new alerts. compare_to_baseline returns them already
            # flattened to plain dicts (DriftAnalysis.alerts); rebuild the
            # ArchitectureAlert objects this cache and _persist_alert need.
            alerts = [ArchitectureAlert(**a) for a in analysis.alerts]
            for alert in alerts:
                self._state.alerts[alert.id] = alert
                self._persist_alert(alert)

            return {
                "success": True,
                "drift_analysis": asdict(analysis),
                "alerts": analysis.alerts,
            }

        except LookupError as e:
            # No baseline to compare against is a routine, expected outcome
            # (a tenant that has never captured one), not a failure -- no
            # ERROR log for it.
            return {"success": False, "error": str(e)}

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
        alerts = list(self._state.alerts.values())

        # Apply filters
        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        if alert_type:
            alerts = [a for a in alerts if a.alert_type == alert_type]

        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        # Sort by creation time (newest first)
        alerts.sort(key=lambda x: x.created_at, reverse=True)

        total = len(alerts)
        alerts = alerts[offset : offset + limit]

        return {
            "success": True,
            "alerts": [asdict(a) for a in alerts],
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
        if alert_id not in self._state.alerts:
            return {"success": False, "error": "Alert not found"}

        return {"success": True, "alert": asdict(self._state.alerts[alert_id])}

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
        if alert_id not in self._state.alerts:
            return {"success": False, "error": "Alert not found"}

        alert = self._state.alerts[alert_id]
        alert.acknowledged = True
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = datetime.utcnow().isoformat()
        self._persist_alert(alert)

        return {"success": True, "alert": asdict(alert), "message": "Alert acknowledged"}

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
        acknowledged = 0
        not_found = 0

        for alert_id in alert_ids:
            if alert_id in self._state.alerts:
                alert = self._state.alerts[alert_id]
                alert.acknowledged = True
                alert.acknowledged_by = acknowledged_by
                alert.acknowledged_at = datetime.utcnow().isoformat()
                self._persist_alert(alert)
                acknowledged += 1
            else:
                not_found += 1

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
        to_remove = [aid for aid, alert in self._state.alerts.items() if alert.acknowledged]

        for alert_id in to_remove:
            del self._state.alerts[alert_id]
            self._delete_alert_from_db(alert_id)

        return {
            "success": True,
            "cleared": len(to_remove),
            "message": f"{len(to_remove)} acknowledged alerts cleared",
        }

    # =========================================================================
    # Internal Helper Methods - Snapshot Capture
    # =========================================================================

    def _tenant_mapping_query(self, cap_id):
        """Active mappings of ``cap_id`` onto this tenant's own components.

        UnifiedApplicationCapabilityMapping carries no organization_id (no
        listener fences it), and a reference capability (organization_id IS
        NULL, admitted by _tenant_capability_filter) can be mapped by
        another tenant's ApplicationComponent -- so the predicate goes on
        the component, not the capability. Shared by the mapping count in
        _capture_capabilities_snapshot and the coverage read in
        _capture_coverage_snapshot below, which each call ``.count()`` or
        ``.all()`` on the result.
        """
        return UnifiedApplicationCapabilityMapping.query.join(
            ApplicationComponent,
            ApplicationComponent.id
            == UnifiedApplicationCapabilityMapping.application_component_id,
        ).filter(
            UnifiedApplicationCapabilityMapping.unified_capability_id == cap_id,
            UnifiedApplicationCapabilityMapping.is_active.is_(True),
            ApplicationComponent.organization_id == self.organization_id,
        )

    def _capture_capabilities_snapshot(self) -> List[Dict[str, Any]]:
        """Capture snapshot of all capabilities visible to this tenant."""
        try:
            capabilities = UnifiedCapability.query.filter(
                _tenant_capability_filter(self.organization_id)
            ).all()
            snapshot = []

            for cap in capabilities:
                mapping_count = self._tenant_mapping_query(cap.id).count()

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
            capabilities = UnifiedCapability.query.filter(
                _tenant_capability_filter(self.organization_id)
            ).all()

            total_coverage = 0
            covered_count = 0
            uncovered_count = 0
            coverage_by_domain = defaultdict(lambda: {"total": 0, "covered": 0})

            for cap in capabilities:
                mappings = self._tenant_mapping_query(cap.id).all()

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
        """Capture snapshot of vendor product status.

        Scoped to this tenant's own vendor mappings (VendorProductCapability,
        TenantMixin), not the shared VendorProduct catalogue: a tenant with
        no mappings gets an honest empty snapshot, not every vendor's
        products.
        """
        try:
            from app.models.vendor.vendor_organization import VendorProduct, VendorProductCapability

            vendors = []
            products = (
                VendorProduct.query.join(
                    VendorProductCapability,
                    VendorProductCapability.vendor_product_id == VendorProduct.id,
                )
                .filter(VendorProductCapability.organization_id == self.organization_id)
                .distinct()
                .all()
            )

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

    # Columns every element/relationship hash leaves out: the primary key and
    # the tenant column carry no content of their own, and the rest of each
    # set is a row's own audit trail -- who/when touched it -- not what it
    # currently is. Excluding an audit column here does not hide an edit: a
    # person changing what an element or relationship *is* always lands on a
    # column outside this set, so it still moves the hash.
    _ELEMENT_HASH_EXCLUDED_COLUMNS = frozenset(
        {
            "id",
            "organization_id",
            # The read this hash is taken over already filters deleted_at IS
            # NULL, and deleted_by is unset for every row that reaches it;
            # a soft delete is reported through elements_removed, not here.
            "deleted_at",
            "deleted_by",
        }
    )
    _RELATIONSHIP_HASH_EXCLUDED_COLUMNS = frozenset(
        {
            "id",
            "organization_id",
            # created_at/updated_at/created_by_id/reviewed_at record who or
            # when a row was touched, not the relationship's own definition;
            # updated_at in particular is not folded back in here -- every
            # other mapped column already is, so a real edit already moves
            # the hash, and updated_at can also move on a save that changes
            # nothing (a re-submit of the same values), which would register
            # a phantom change this hash exists to avoid.
            "created_at",
            "updated_at",
            "created_by_id",
            "reviewed_at",
        }
    )

    @staticmethod
    def _mapped_content_columns(model, excluded) -> List[str]:
        """Every attribute this mapper carries, minus ``excluded``.

        Derived from the mapper the same way ``ArchiMateElement.to_dict`` /
        ``ArchiMateRelationship.to_dict`` already read every column (through
        ``mapper.get_property_by_column``, not the column's own name, since
        an explicitly-named column like ``togaf_plateau`` maps to a DB column
        of a different name) -- so a column added to either model later is
        picked up here automatically instead of silently reporting no change
        when someone edits it.
        """
        mapper_ = sa_inspect(model)
        return sorted(
            {
                mapper_.get_property_by_column(col).key
                for col in model.__table__.columns
                if mapper_.get_property_by_column(col).key not in excluded
            }
        )

    @classmethod
    def _element_content_hash(cls, el) -> str:
        """A one-way digest of what makes this element itself: every mapped
        column bar identity and audit provenance (``_ELEMENT_HASH_EXCLUDED_COLUMNS``).

        ArchiMateElement carries no per-row modification timestamp in this
        schema (only deleted_at); an edit to any of its other columns is
        otherwise invisible to a snapshot restricted to ids and never shows
        up as a change. The hash lets a comparison detect that edit without
        storing the field itself in the snapshot -- only the digest is kept,
        which does not reveal what it was taken over.

        ``layer`` is canonicalised before hashing: the column's own type
        decorator (``_ArchiMateLayerType``) lower-cases it on the way back
        out of a SELECT, but an element captured in the same session it was
        written in has not made that round trip yet and still carries its
        as-assigned spelling, which would otherwise hash differently from
        the same element re-read later and register a one-off phantom
        change.
        """
        from app.models.models import canonical_archimate_layer

        payload = {}
        for attr in cls._mapped_content_columns(type(el), cls._ELEMENT_HASH_EXCLUDED_COLUMNS):
            value = getattr(el, attr)
            payload[attr] = canonical_archimate_layer(value) if attr == "layer" else value
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    def _relationship_content_hash(cls, rel) -> str:
        """A one-way digest of what makes this relationship itself: every
        mapped column bar identity and audit provenance
        (``_RELATIONSHIP_HASH_EXCLUDED_COLUMNS``). Same reasoning as
        ``_element_content_hash``.
        """
        payload = {
            attr: getattr(rel, attr)
            for attr in cls._mapped_content_columns(type(rel), cls._RELATIONSHIP_HASH_EXCLUDED_COLUMNS)
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def _capture_model_snapshot(self) -> Dict[str, Any]:
        """Capture the model's own drift surface: this tenant's element and
        relationship ids with a content hash each, plus the derived-fact
        aggregates -- so a comparison can say whether the modelled estate
        moved since the baseline, not only the intelligence built on it.

        Ids and content hashes only, never the fields the hash is taken
        over: no element or relationship name, description or other
        property leaves this method. Reads with the explicit organisation
        predicate the genome drift detector uses
        (app/modules/genome/services/drift_detector.py), so a call with no
        Flask request on the stack -- a job, or this service's own callers
        outside a request -- is scoped too, not relying only on the
        request-scoped tenant filter.
        """
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.modules.intelligence.services.derived_facts import (
            derived_fact_aggregates,
            stale_derived_fact_ids,
        )

        elements = ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == self.organization_id,
            ArchiMateElement.deleted_at.is_(None),
        ).all()
        # ArchiMateRelationship has no soft-delete column (the genome drift
        # detector filters it by organization_id only, the same predicate
        # here).
        relationships = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.organization_id == self.organization_id,
        ).all()

        derived = derived_fact_aggregates(self.organization_id)
        computed_at = derived.get("computed_at")
        stale_count = derived.get("stale_count")
        # The staleness filter lives in derived_facts.py, not re-implemented
        # here (that module is the one read path over the derived-fact
        # store, with the filter applied in one place). The id query itself
        # is capped (bounding what this capture materialises and stores),
        # not the Python list after an unbounded fetch; stale_count above is
        # a real SQL COUNT independent of the cap, so comparing it against
        # how many ids came back is enough to know whether the id list below
        # is the whole stale set or a bounded prefix of it.
        stale_ids = sorted(
            str(rid)
            for rid in stale_derived_fact_ids(
                self.organization_id, limit=_MODEL_SNAPSHOT_STALE_ID_CAP
            )
        )
        stale_ids_truncated = stale_count is not None and stale_count > len(stale_ids)

        return {
            "elements": {str(el.id): self._element_content_hash(el) for el in elements},
            "relationships": {
                str(rel.id): self._relationship_content_hash(rel) for rel in relationships
            },
            "derived": {
                "derived_count": derived.get("derived_count"),
                "stale_count": stale_count,
                "computed_at": computed_at.isoformat() if computed_at else None,
                "stale_ids": stale_ids,
                "stale_ids_truncated": stale_ids_truncated,
            },
            # Outside the checksummed payload (_calculate_baseline_checksum
            # is called on capabilities/coverage/health/gaps/vendors/model,
            # and this key changes on every capture regardless of the
            # estate) -- capture_baseline reads it back out before hashing,
            # so two baselines over an identical estate get identical
            # checksums.
            "captured_at": datetime.utcnow().isoformat(),
        }

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
        """Analyze coverage drift."""
        baseline_avg = baseline.get("average_coverage", 0)
        current_avg = current.get("average_coverage", 0)

        baseline_uncovered = baseline.get("uncovered_capabilities", 0)
        current_uncovered = current.get("uncovered_capabilities", 0)

        return {
            "baseline_average_coverage": baseline_avg,
            "current_average_coverage": current_avg,
            "coverage_delta": round(current_avg - baseline_avg, 2),
            "baseline_uncovered": baseline_uncovered,
            "current_uncovered": current_uncovered,
            "uncovered_delta": current_uncovered - baseline_uncovered,
            "has_regression": current_avg < baseline_avg,
        }

    def _analyze_health_drift(
        self, baseline: Dict[str, Any], current: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze health score drift."""
        baseline_health = baseline.get("average_health", 0)
        current_health = current.get("average_health", 0)

        baseline_at_risk = baseline.get("at_risk_capabilities", 0)
        current_at_risk = current.get("at_risk_capabilities", 0)

        return {
            "baseline_average_health": baseline_health,
            "current_average_health": current_health,
            "health_delta": round(current_health - baseline_health, 2),
            "baseline_at_risk": baseline_at_risk,
            "current_at_risk": current_at_risk,
            "at_risk_delta": current_at_risk - baseline_at_risk,
            "has_regression": current_health < baseline_health,
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

    def _analyze_model_drift(
        self,
        baseline_model: Optional[Dict[str, Any]],
        current_model: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze drift in the model itself: which element/relationship ids
        were added, removed or changed in place, and what happened to
        derivation since the baseline.

        A baseline captured before this dimension existed carries no
        "model" key in its persisted snapshot (``_ensure_loaded`` reads it
        back as ``None``, never ``{}``) -- every count is ``None`` with the
        ``baseline_lacks_model_snapshot`` reason in that case, not a
        fabricated ``0``: a ``0`` means a real comparison ran and found no
        change, whereas ``None`` means no comparison could run at all.

        No alert is generated from this dimension in this task.
        """
        if baseline_model is None:
            return {
                "reason": "baseline_lacks_model_snapshot",
                "elements_changed": None,
                "relationships_added": None,
                "relationships_removed": None,
                "derived_recomputed": None,
                "changed_element_ids": [],
                "added_relationship_ids": [],
                "removed_relationship_ids": [],
            }

        baseline_elements = baseline_model.get("elements", {}) or {}
        current_elements = current_model.get("elements", {}) or {}
        baseline_element_ids = set(baseline_elements)
        current_element_ids = set(current_elements)

        # Ids present on both sides whose content hash differs are a real,
        # in-place edit (a rename, a re-layer, a property change) -- not
        # merely present/absent, which the id-set alone already covers via
        # added/removed below.
        common_element_ids = baseline_element_ids & current_element_ids
        changed_element_ids = sorted(
            eid
            for eid in common_element_ids
            if baseline_elements.get(eid) != current_elements.get(eid)
        )
        added_element_ids = sorted(current_element_ids - baseline_element_ids)
        removed_element_ids = sorted(baseline_element_ids - current_element_ids)

        baseline_relationships = baseline_model.get("relationships", {}) or {}
        current_relationships = current_model.get("relationships", {}) or {}
        baseline_relationship_ids = set(baseline_relationships)
        current_relationship_ids = set(current_relationships)
        common_relationship_ids = baseline_relationship_ids & current_relationship_ids
        changed_relationship_ids = sorted(
            rid
            for rid in common_relationship_ids
            if baseline_relationships.get(rid) != current_relationships.get(rid)
        )
        added_relationship_ids = sorted(current_relationship_ids - baseline_relationship_ids)
        removed_relationship_ids = sorted(baseline_relationship_ids - current_relationship_ids)

        baseline_derived = baseline_model.get("derived", {}) or {}
        current_derived = current_model.get("derived", {}) or {}
        baseline_derived_count = baseline_derived.get("derived_count")
        current_derived_count = current_derived.get("derived_count")
        baseline_stale_count = baseline_derived.get("stale_count")
        current_stale_count = current_derived.get("stale_count")
        baseline_stale_ids = set(baseline_derived.get("stale_ids") or [])
        current_stale_ids = set(current_derived.get("stale_ids") or [])
        newly_stale_ids = sorted(current_stale_ids - baseline_stale_ids)
        resolved_stale_ids = sorted(baseline_stale_ids - current_stale_ids)
        # Either side's stale-id list can be a capped prefix of a larger
        # true stale set (_MODEL_SNAPSHOT_STALE_ID_CAP); when it is,
        # newly_stale_ids/resolved_stale_ids above are a lower bound, not
        # necessarily the whole difference -- stale_count_delta below still
        # is exact, since it comes from a real SQL COUNT on both sides.
        stale_ids_truncated = bool(
            baseline_derived.get("stale_ids_truncated") or current_derived.get("stale_ids_truncated")
        )
        baseline_computed_at = baseline_derived.get("computed_at")
        current_computed_at = current_derived.get("computed_at")
        derived_recomputed = (
            1
            if current_computed_at is not None
            and (baseline_computed_at is None or current_computed_at > baseline_computed_at)
            else 0
        )

        return {
            "elements_changed": len(changed_element_ids),
            "elements_added": len(added_element_ids),
            "elements_removed": len(removed_element_ids),
            "relationships_changed": len(changed_relationship_ids),
            "relationships_added": len(added_relationship_ids),
            "relationships_removed": len(removed_relationship_ids),
            "derived_recomputed": derived_recomputed,
            # derived_count/stale_count deltas plus the stale set itself: two
            # snapshots can carry the same stale_count while a different
            # fact went stale and another recovered, which a bare count
            # cannot show.
            "derived_count_delta": (
                None
                if baseline_derived_count is None or current_derived_count is None
                else current_derived_count - baseline_derived_count
            ),
            "stale_count_delta": (
                None
                if baseline_stale_count is None or current_stale_count is None
                else current_stale_count - baseline_stale_count
            ),
            "newly_stale_ids": newly_stale_ids,
            "resolved_stale_ids": resolved_stale_ids,
            "stale_ids_truncated": stale_ids_truncated,
            "derived_computed_at": {
                "baseline": baseline_computed_at,
                "current": current_computed_at,
            },
            "changed_element_ids": changed_element_ids,
            "added_element_ids": added_element_ids,
            "removed_element_ids": removed_element_ids,
            "changed_relationship_ids": changed_relationship_ids,
            "added_relationship_ids": added_relationship_ids,
            "removed_relationship_ids": removed_relationship_ids,
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
