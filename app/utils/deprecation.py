"""
Deprecation Utilities for Service Consolidation

This module provides decorators and utilities for marking deprecated services
and functions, helping track the migration to unified services.

Usage:
    from app.utils.deprecation import deprecated_service, deprecated_function

    @deprecated_service(
        replacement='unified_apqc_service.UnifiedAPQCService',
        version='2.0.0',
        reason='Consolidated into unified service for maintainability'
    )
    class OldService:
        pass

    @deprecated_function(replacement='get_unified_apqc_service')
    def get_old_service():
        pass
"""

import functools
import logging
import warnings
from datetime import datetime
from typing import Any, Callable, Optional, Type

logger = logging.getLogger(__name__)

# Registry of deprecated services for tracking
_DEPRECATED_SERVICES_REGISTRY = {}


class DeprecationWarningLevel:
    """Deprecation warning severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def deprecated_service(
    replacement: str,
    version: str = "2.0.0",
    reason: Optional[str] = None,
    removal_version: Optional[str] = None,
    warning_level: str = DeprecationWarningLevel.WARNING,
) -> Callable:
    """
    Decorator to mark a service class as deprecated.

    Args:
        replacement: The recommended replacement service (fully qualified name)
        version: The version in which this was deprecated
        reason: Optional explanation for the deprecation
        removal_version: Optional version when this will be removed
        warning_level: Severity level for the warning

    Example:
        @deprecated_service(
            replacement='app.services.unified_apqc_service.UnifiedAPQCService',
            version='2.0.0',
            reason='Consolidated into unified service'
        )
        class OldService:
            pass
    """

    def decorator(cls: Type) -> Type:
        # Store original __init__
        original_init = cls.__init__

        @functools.wraps(original_init)
        def new_init(self, *args, **kwargs):
            # Build deprecation message
            msg_parts = [
                f"'{cls.__module__}.{cls.__name__}' is deprecated since version {version}.",
                f"Use '{replacement}' instead.",
            ]
            if reason:
                msg_parts.append(f"Reason: {reason}")
            if removal_version:
                msg_parts.append(f"Will be removed in version {removal_version}.")

            message = " ".join(msg_parts)

            # Log the warning
            if warning_level == DeprecationWarningLevel.ERROR:
                logger.error(f"DEPRECATED SERVICE: {message}")
            elif warning_level == DeprecationWarningLevel.WARNING:
                logger.warning(f"DEPRECATED SERVICE: {message}")
            else:
                logger.info(f"DEPRECATED SERVICE: {message}")

            # Issue Python deprecation warning
            warnings.warn(message, DeprecationWarning, stacklevel=2)

            # Call original init
            return original_init(self, *args, **kwargs)

        cls.__init__ = new_init

        # Add deprecation metadata to the class
        cls._deprecated = True
        cls._deprecated_info = {
            "replacement": replacement,
            "version": version,
            "reason": reason,
            "removal_version": removal_version,
            "warning_level": warning_level,
        }

        # Register in global registry
        full_name = f"{cls.__module__}.{cls.__name__}"
        _DEPRECATED_SERVICES_REGISTRY[full_name] = cls._deprecated_info

        # Update docstring
        deprecation_notice = f"""
.. deprecated:: {version}
   Use :class:`{replacement}` instead.
   {f"Reason: {reason}" if reason else ""}
   {f"Will be removed in {removal_version}." if removal_version else ""}

"""
        if cls.__doc__:
            cls.__doc__ = deprecation_notice + cls.__doc__
        else:
            cls.__doc__ = deprecation_notice

        return cls

    return decorator


def deprecated_function(
    replacement: str,
    version: str = "2.0.0",
    reason: Optional[str] = None,
    removal_version: Optional[str] = None,
    warning_level: str = DeprecationWarningLevel.WARNING,
) -> Callable:
    """
    Decorator to mark a function as deprecated.

    Args:
        replacement: The recommended replacement function
        version: The version in which this was deprecated
        reason: Optional explanation for the deprecation
        removal_version: Optional version when this will be removed
        warning_level: Severity level for the warning

    Example:
        @deprecated_function(
            replacement='get_unified_apqc_service',
            version='2.0.0'
        )
        def get_old_service():
            pass
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Build deprecation message
            msg_parts = [
                f"'{func.__module__}.{func.__name__}' is deprecated since version {version}.",
                f"Use '{replacement}' instead.",
            ]
            if reason:
                msg_parts.append(f"Reason: {reason}")
            if removal_version:
                msg_parts.append(f"Will be removed in version {removal_version}.")

            message = " ".join(msg_parts)

            # Log the warning
            if warning_level == DeprecationWarningLevel.ERROR:
                logger.error(f"DEPRECATED FUNCTION: {message}")
            elif warning_level == DeprecationWarningLevel.WARNING:
                logger.warning(f"DEPRECATED FUNCTION: {message}")
            else:
                logger.info(f"DEPRECATED FUNCTION: {message}")

            # Issue Python deprecation warning
            warnings.warn(message, DeprecationWarning, stacklevel=2)

            return func(*args, **kwargs)

        # Add deprecation metadata
        wrapper._deprecated = True
        wrapper._deprecated_info = {
            "replacement": replacement,
            "version": version,
            "reason": reason,
            "removal_version": removal_version,
            "warning_level": warning_level,
        }

        # Update docstring
        deprecation_notice = f"""
.. deprecated:: {version}
   Use :func:`{replacement}` instead.
   {f"Reason: {reason}" if reason else ""}
   {f"Will be removed in {removal_version}." if removal_version else ""}

"""
        if func.__doc__:
            wrapper.__doc__ = deprecation_notice + func.__doc__
        else:
            wrapper.__doc__ = deprecation_notice

        return wrapper

    return decorator


def get_deprecated_services() -> dict:
    """
    Get a registry of all deprecated services.

    Returns:
        Dictionary mapping service names to their deprecation info
    """
    return _DEPRECATED_SERVICES_REGISTRY.copy()


def is_deprecated(obj: Any) -> bool:
    """
    Check if a class or function is deprecated.

    Args:
        obj: The class or function to check

    Returns:
        True if the object is marked as deprecated
    """
    return getattr(obj, "_deprecated", False)


def get_deprecation_info(obj: Any) -> Optional[dict]:
    """
    Get deprecation information for a class or function.

    Args:
        obj: The class or function to check

    Returns:
        Dictionary with deprecation info, or None if not deprecated
    """
    if is_deprecated(obj):
        return getattr(obj, "_deprecated_info", None)
    return None


# Convenience aliases for common APQC service deprecations
APQC_UNIFIED_SERVICE = "app.services.unified_apqc_service.UnifiedAPQCService"
APQC_UNIFIED_GETTER = "app.services.unified_apqc_service.get_unified_apqc_service"


# ============================================================================
# ROUTE DEPRECATION UTILITIES (for safe route migration)
# ============================================================================

from flask import jsonify, make_response, redirect, request, url_for


class RouteDeprecationWarning:
    """
    Container for route deprecation metadata.

    Holds the deprecation date, canonical endpoint and migration guide for a
    route, plus the optional ``retires_on`` date and ``replacement_operation_id``
    set when an administrator marks the operation as retiring. Records are
    registered in ``_ROUTE_DEPRECATION_REGISTRY`` keyed by
    ``(normalized_path, method)`` so the admin page and the response-header
    logic can look them up by operation.
    """

    def __init__(
        self,
        canonical_endpoint: str,
        deprecation_date: str = None,
        migration_guide: str = None,
        sunset_date: str = None,
        retires_on: str = None,
        replacement_operation_id: str = None,
    ):
        self.canonical_endpoint = canonical_endpoint
        self.deprecation_date = deprecation_date or datetime.utcnow().strftime(
            "%Y-%m-%d"
        )
        self.migration_guide = migration_guide or f"Use {canonical_endpoint} instead"
        self.sunset_date = sunset_date or "2026-06-01"
        self.created_at = datetime.utcnow().isoformat()
        self.retires_on = retires_on
        self.replacement_operation_id = replacement_operation_id

    def to_dict(self):
        return {
            "deprecated": True,
            "canonical_endpoint": self.canonical_endpoint,
            "deprecation_date": self.deprecation_date,
            "sunset_date": self.sunset_date,
            "migration_guide": self.migration_guide,
            "created_at": self.created_at,
            "retires_on": self.retires_on,
            "replacement_operation_id": self.replacement_operation_id,
        }

    @property
    def is_retiring(self) -> bool:
        """True when an administrator has set a retirement date."""
        return self.retires_on is not None


# One canonical registry of route deprecation records, keyed by
# (normalized_path, method) so the admin page and the header logic can look
# an operation up without a second registry.
_ROUTE_DEPRECATION_REGISTRY: dict = {}


def _normalize_path(path: str) -> str:
    """Strip the /api/v1 prefix and replace path parameters with {}."""
    import re

    if path == "/api/v1" or path.startswith("/api/v1/"):
        path = path[len("/api/v1"):] or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return re.sub(r"\{[^}]+\}", "{}", path)


def register_route_deprecation(path: str, method: str, record: RouteDeprecationWarning) -> None:
    """Register a deprecation record for an operation."""
    _ROUTE_DEPRECATION_REGISTRY[(_normalize_path(path), method.lower())] = record


def get_route_deprecation(path: str, method: str) -> RouteDeprecationWarning | None:
    """Return the deprecation record for an operation, or None."""
    return _ROUTE_DEPRECATION_REGISTRY.get((_normalize_path(path), method.lower()))


def get_retiring_operations() -> list[RouteDeprecationWarning]:
    """Return all records that have a retirement date set."""
    return [r for r in _ROUTE_DEPRECATION_REGISTRY.values() if r.is_retiring]


def mark_operation_retiring(
    path: str, method: str, retires_on: str, replacement_operation_id: str
) -> RouteDeprecationWarning | None:
    """Mark an operation as retiring on *retires_on* with the given replacement.

    Creates a record if none exists yet for the operation. Returns the record,
    or None when the operation is not known to the registry.
    """
    key = (_normalize_path(path), method.lower())
    record = _ROUTE_DEPRECATION_REGISTRY.get(key)
    if record is None:
        record = RouteDeprecationWarning(canonical_endpoint=key[0])
        _ROUTE_DEPRECATION_REGISTRY[key] = record
    record.retires_on = retires_on
    record.replacement_operation_id = replacement_operation_id
    return record


def create_retiring_notices(
    operation_id: str,
    retires_on: str,
    replacement_operation_id: str | None,
    org_id: int,
) -> int:
    """Create one deprecation notice per affected client owner in *org_id*.

    Finds every client who called *operation_id* in the organisation, then
    every token owner for those clients, and creates one
    ``event_type="deprecation_notice"`` notice per user through
    ``NotificationService.create``. Users who already have a notice for this
    operation (matched by event type, entity type and message) are skipped, so
    a second marking creates no duplicate.

    Returns the number of notices created.
    """
    from app import db
    from app.models.models import Notification
    from app.modules.oauth_provider.models import ApiOperationCall, OAuthToken
    from app.services.notification_service import NotificationService

    notified_users = set()
    clients = (
        db.session.query(ApiOperationCall.client_id)
        .filter(
            ApiOperationCall.organization_id == org_id,
            ApiOperationCall.operation_id == operation_id,
        )
        .distinct()
        .all()
    )
    created = 0
    for (client_id,) in clients:
        token_owners = (
            db.session.query(OAuthToken.user_id)
            .filter(
                OAuthToken.client_id == client_id,
                OAuthToken.organization_id == org_id,
            )
            .distinct()
            .all()
        )
        for (user_id,) in token_owners:
            if user_id in notified_users:
                continue
            message = (
                f"Operation {operation_id} is retiring on {retires_on}. "
                f"Use {replacement_operation_id or 'the replacement'} instead."
            )
            already = (
                db.session.query(Notification)
                .filter(
                    Notification.user_id == user_id,
                    Notification.event_type == "deprecation_notice",
                    Notification.entity_type == "operation",
                    Notification.message == message,
                )
                .first()
            )
            if already is not None:
                notified_users.add(user_id)
                continue
            NotificationService.create(
                user_id=user_id,
                message=message,
                url="/admin/deprecation",
                organization_id=org_id,
                event_type="deprecation_notice",
                entity_type="operation",
            )
            notified_users.add(user_id)
            created += 1
    return created


def deprecated_route(
    canonical_endpoint: str,
    deprecation_date: str = None,
    migration_guide: str = None,
    redirect_code: int = 308,
):
    """
    Decorator to mark a Flask route as deprecated (soft deprecation).

    This decorator executes the original route function normally but adds
    deprecation headers to the response and logs warnings. This preserves
    backward compatibility while signaling that callers should migrate.

    Deprecation headers added:
        X-Deprecated: true
        X-Deprecation-Date: <date>
        X-Canonical-Endpoint: <endpoint>
        X-Migration-Guide: <guide>
        Sunset: <date>

    Args:
        canonical_endpoint: The canonical url_for endpoint
        deprecation_date: Date when deprecation started (ISO format)
        migration_guide: Human-readable migration instructions
        redirect_code: HTTP redirect code (unused in soft mode, kept for API)

    Returns:
        Decorated function
    """
    deprecation_date = deprecation_date or datetime.utcnow().strftime("%Y-%m-%d")
    migration_guide = migration_guide or f"Use {canonical_endpoint} instead"

    def decorator(f: Callable):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            # Log deprecation warning (once per endpoint per request)
            logger.warning(
                f"DEPRECATED ROUTE CALLED: {request.endpoint} -> {canonical_endpoint}"
                f" (IP: {request.remote_addr}, Path: {request.path})"
            )

            # Track metrics for deprecated endpoint usage
            try:
                _deprecated_endpoint = (
                    getattr(f, "_deprecated_endpoint", None) or canonical_endpoint
                )
                track_deprecated_route_usage(
                    endpoint=_deprecated_endpoint,
                    caller_ip=request.remote_addr or "unknown",
                    request_path=request.path,
                )
            except Exception as e:
                logger.debug("Deprecation metrics tracking failed for %s: %s", _deprecated_endpoint, e)

            # Register/lookup the deprecation record for this operation so an
            # administrator can mark it as retiring at runtime.
            record = _ROUTE_DEPRECATION_REGISTRY.get(
                (_normalize_path(request.path), request.method.lower())
            )
            if record is None:
                record = RouteDeprecationWarning(canonical_endpoint=canonical_endpoint)
                _ROUTE_DEPRECATION_REGISTRY[
                    (_normalize_path(request.path), request.method.lower())
                ] = record

            # Execute the original function — preserve existing behavior
            result = f(*args, **kwargs)

            # Build deprecation headers
            dep_headers = {
                "X-Deprecated": "true",
                "X-Deprecation-Date": deprecation_date,
                "X-Canonical-Endpoint": canonical_endpoint,
                "X-Migration-Guide": migration_guide,
                "Sunset": "2026-06-01",
            }

            # When the operation has been marked as retiring, answer with the
            # RFC 9745 Deprecation header and a Link to the replacement.
            if record.is_retiring:
                dep_headers["Deprecation"] = record.retires_on
                if record.replacement_operation_id:
                    dep_headers["Link"] = (
                        f'</api/v1/{record.replacement_operation_id}>; rel="deprecation"'
                    )

            # Attach headers to the response
            try:
                response = make_response(result)
                for key, value in dep_headers.items():
                    response.headers[key] = value
                return response
            except Exception:
                # If we can't modify headers, return result unchanged
                return result

        return decorated_function

    return decorator


def redirect_to_canonical(
    canonical_endpoint: str,
    deprecation_date: str = None,
    migration_guide: str = None,
):
    """
    Create a redirect function from a deprecated endpoint to a canonical one.

    Args:
        canonical_endpoint: The canonical url_for endpoint
        deprecation_date: Date of deprecation
        migration_guide: Human-readable migration instructions

    Returns:
        Redirect response function

    Example in routes.py:
        @application_mgmt.route('/applications/<int:id>/edit', methods=['GET', 'POST'])
        def application_edit(id):
            return redirect_to_canonical(
                'unified_applications.application_edit',
                migration_guide='Use unified_applications.application_edit instead'
            )(id=id)
    """
    deprecation_date = deprecation_date or datetime.utcnow().strftime("%Y-%m-%d")
    migration_guide = migration_guide or f"Use {canonical_endpoint} instead"

    def redirect_helper(**kwargs):
        logger.warning(
            f"DEPRECATED ROUTE REDIRECT: -> {canonical_endpoint} (Path: {request.path})"
        )

        response_headers = {
            "X-Deprecated": "true",
            "X-Deprecation-Date": deprecation_date,
            "X-Canonical-Endpoint": canonical_endpoint,
            "X-Migration-Guide": migration_guide,
        }

        try:
            canonical_url = url_for(canonical_endpoint, **kwargs)
            response = redirect(canonical_url, code=308)
            for key, value in response_headers.items():
                response.headers[key] = value
            return response
        except Exception as e:
            logger.error(f"Redirect failed: {e}")
            response = jsonify(
                {
                    "error": "Endpoint deprecated",
                    "canonical_endpoint": canonical_endpoint,
                    "message": migration_guide,
                }
            )
            response.status_code = 410
            return response

    return redirect_helper


def create_deprecation_response(
    canonical_endpoint: str,
    deprecation_date: str,
    migration_guide: str,
    status_code: int = 410,
):
    """
    Create a standardized deprecation JSON response.

    Args:
        canonical_endpoint: The canonical url_for endpoint
        deprecation_date: Date of deprecation
        migration_guide: Human-readable migration instructions
        status_code: HTTP status code

    Returns:
        Tuple of (response, status_code)
    """
    response_data = {
        "warning": "This endpoint is deprecated",
        "deprecated": True,
        "canonical_endpoint": canonical_endpoint,
        "deprecation_date": deprecation_date,
        "sunset_date": "2026-06-01",
        "migration_guide": migration_guide,
    }

    response = jsonify(response_data)
    response.status_code = status_code
    response.headers["X-Deprecated"] = "true"
    response.headers["X-Deprecation-Date"] = deprecation_date
    response.headers["X-Canonical-Endpoint"] = canonical_endpoint
    response.headers["X-Migration-Guide"] = migration_guide

    return response, status_code


# ============================================================================
# DEPRECATION METRICS COLLECTION
# ============================================================================

import threading
from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional


class DeprecationMetrics:
    """
    Thread-safe metrics collection for deprecated endpoint usage tracking.

    This class tracks:
    - Usage counts per endpoint
    - Request timestamps for spike detection
    - Caller IP distribution
    - Request path patterns

    Usage:
        metrics = DeprecationMetrics()
        metrics.track_usage('application_mgmt.api_table_data', '192.168.1.1', '/dashboard/api/applications/table-data')
        stats = metrics.get_usage_stats()
        alerts = metrics.get_spike_alerts(threshold=100, window_minutes=5)
    """

    def __init__(self, max_history_minutes: int = 60):
        self._lock = threading.Lock()
        self._usage_counts: Dict[str, int] = defaultdict(int)
        self._ip_distribution: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._request_paths: Dict[str, List[str]] = defaultdict(list)
        self._timestamps: Dict[str, List[datetime]] = defaultdict(list)
        self._max_history_minutes = max_history_minutes
        self._total_requests = 0

    def track_usage(
        self,
        endpoint: str,
        caller_ip: str,
        request_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Track a deprecated endpoint usage.

        Args:
            endpoint: The deprecated endpoint name (e.g., 'application_mgmt.api_table_data')
            caller_ip: IP address of the caller
            request_path: The original request path
            metadata: Optional additional metadata (user agent, etc.)
        """
        with self._lock:
            now = datetime.utcnow()
            cutoff = now - timedelta(minutes=self._max_history_minutes)

            self._usage_counts[endpoint] += 1
            self._total_requests += 1
            self._ip_distribution[endpoint][caller_ip] += 1
            self._request_paths[endpoint].append(request_path)

            self._timestamps[endpoint].append(now)

            self._timestamps[endpoint] = [
                ts for ts in self._timestamps[endpoint] if ts > cutoff
            ]

    def get_usage_counts(self) -> Dict[str, int]:
        """Get usage counts per endpoint."""
        with self._lock:
            return dict(self._usage_counts)

    def get_total_requests(self) -> int:
        """Get total deprecated endpoint requests."""
        with self._lock:
            return self._total_requests

    def get_ip_distribution(
        self, endpoint: Optional[str] = None
    ) -> Dict[str, Dict[str, int]]:
        """
        Get IP distribution for endpoints.

        Args:
            endpoint: Optional specific endpoint, otherwise all endpoints

        Returns:
            Dict mapping endpoints to IP -> count mappings
        """
        with self._lock:
            if endpoint:
                return {endpoint: dict(self._ip_distribution[endpoint])}
            return {k: dict(v) for k, v in self._ip_distribution.items()}

    def get_usage_stats(self, endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Get comprehensive usage statistics.

        Args:
            endpoint: Optional specific endpoint, otherwise all endpoints

        Returns:
            Dict with usage statistics
        """
        with self._lock:
            stats = {
                "total_requests": self._total_requests,
                "endpoints_tracked": len(self._usage_counts),
                "endpoints": {},
                "generated_at": datetime.utcnow().isoformat(),
            }

            endpoints_to_report = (
                [endpoint] if endpoint else list(self._usage_counts.keys())
            )

            for ep in endpoints_to_report:
                timestamps = self._timestamps.get(ep, [])
                ip_dist = self._ip_distribution.get(ep, {})

                if timestamps:
                    time_span = (max(timestamps) - min(timestamps)).total_seconds()
                    requests_per_minute = len(timestamps) / max(time_span / 60, 1)
                else:
                    requests_per_minute = 0

                stats["endpoints"][ep] = {
                    "count": self._usage_counts.get(ep, 0),
                    "unique_ips": len(ip_dist),
                    "top_ips": sorted(
                        ip_dist.items(), key=lambda x: x[1], reverse=True
                    )[:5],
                    "requests_per_minute": round(requests_per_minute, 2),
                }

            return stats

    def get_spike_alerts(
        self,
        threshold: int = 100,
        window_minutes: int = 5,
        endpoint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Detect usage spikes above threshold.

        Args:
            threshold: Minimum requests to trigger alert
            window_minutes: Time window to analyze
            endpoint: Optional specific endpoint, otherwise all endpoints

        Returns:
            List of spike alerts with endpoint, count, and metadata
        """
        with self._lock:
            alerts = []
            now = datetime.utcnow()
            window_start = now - timedelta(minutes=window_minutes)

            endpoints_to_check = (
                [endpoint] if endpoint else list(self._timestamps.keys())
            )

            for ep in endpoints_to_check:
                window_timestamps = [
                    ts for ts in self._timestamps.get(ep, []) if ts >= window_start
                ]

                if len(window_timestamps) >= threshold:
                    ip_dist = self._ip_distribution.get(ep, {})
                    top_ip = (
                        max(ip_dist.items(), key=lambda x: x[1])
                        if ip_dist
                        else ("unknown", 0)
                    )

                    alerts.append(
                        {
                            "endpoint": ep,
                            "request_count": len(window_timestamps),
                            "threshold": threshold,
                            "window_minutes": window_minutes,
                            "top_caller_ip": top_ip[0],
                            "top_caller_count": top_ip[1],
                            "severity": "critical"
                            if len(window_timestamps) >= threshold * 2
                            else "warning",
                            "timestamp": now.isoformat(),
                        }
                    )

            return alerts

    def get_endpoint_velocity(self, endpoint: str, window_minutes: int = 10) -> float:
        """
        Get request velocity (requests per minute) for an endpoint.

        Args:
            endpoint: The endpoint name
            window_minutes: Time window for velocity calculation

        Returns:
            Requests per minute (0 if no data)
        """
        with self._lock:
            now = datetime.utcnow()
            window_start = now - timedelta(minutes=window_minutes)

            window_timestamps = [
                ts for ts in self._timestamps.get(endpoint, []) if ts >= window_start
            ]

            if not window_timestamps:
                return 0.0

            time_span_minutes = (now - min(window_timestamps)).total_seconds() / 60
            return round(len(window_timestamps) / max(time_span_minutes, 0.1), 2)

    def export_metrics(self) -> Dict[str, Any]:
        """
        Export all metrics in a format suitable for monitoring systems.

        Returns:
            Dict with Prometheus-compatible metrics format
        """
        stats = self.get_usage_stats()

        metrics_output = {
            "deprecated_endpoint_total_requests": stats["total_requests"],
            "deprecated_endpoints_active": stats["endpoints_tracked"],
            "by_endpoint": {},
        }

        for ep, ep_stats in stats["endpoints"].items():
            metrics_output["by_endpoint"][ep] = {
                f'deprecated_endpoint_requests{{endpoint="{ep}"}}': ep_stats["count"],  # raw-html-ok: Prometheus metric label syntax (dict key), not HTML; ep is an internal Flask route path, not user input
                f'deprecated_endpoint_rpm{{endpoint="{ep}"}}': ep_stats[  # raw-html-ok: Prometheus metric label syntax (dict key), not HTML; ep is an internal Flask route path, not user input
                    "requests_per_minute"
                ],
                f'deprecated_endpoint_unique_ips{{endpoint="{ep}"}}': ep_stats[  # raw-html-ok: Prometheus metric label syntax (dict key), not HTML; ep is an internal Flask route path, not user input
                    "unique_ips"
                ],
            }

        return metrics_output

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        with self._lock:
            self._usage_counts.clear()
            self._ip_distribution.clear()
            self._request_paths.clear()
            self._timestamps.clear()
            self._total_requests = 0


_deprecation_metrics = None


def get_deprecation_metrics() -> DeprecationMetrics:
    """Get the global DeprecationMetrics instance."""
    global _deprecation_metrics
    if _deprecation_metrics is None:
        _deprecation_metrics = DeprecationMetrics()
    return _deprecation_metrics


def track_deprecated_route_usage(
    endpoint: str,
    caller_ip: str,
    request_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Convenience function to track deprecated route usage.

    Args:
        endpoint: The deprecated endpoint name
        caller_ip: IP address of the caller
        request_path: The original request path
        metadata: Optional additional metadata
    """
    metrics = get_deprecation_metrics()
    metrics.track_usage(endpoint, caller_ip, request_path, metadata)
