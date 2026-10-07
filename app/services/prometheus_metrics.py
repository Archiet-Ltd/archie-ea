"""
Prometheus Metrics Collection for Production Monitoring

Provides Prometheus-compatible metrics for the application.
Tracks HTTP requests, business operations, and system health.

Usage:
    from app.services.prometheus_metrics import track_metric

    # In route:
    track_metric('application_created', source='manual')
"""


import os

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
)

# Create a custom registry
REGISTRY = CollectorRegistry()

# Application info
APP_INFO = Info("app_info", "Application information", registry=REGISTRY)

# HTTP metrics
HTTP_REQUESTS_TOTAL = Counter(
    "app_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "app_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        # An explicit 2.0s edge so the "answers" objective's 2-second
        # p95 target (app/services/platform_slo_service.py) lands on a real
        # declared bucket boundary rather than only ever reading the 2.5s one.
        2.0,
        2.5,
        5.0,
        7.5,
        10.0,
    ],
    registry=REGISTRY,
)

# Application CRUD metrics
APPLICATIONS_CREATED = Counter(
    "app_applications_created_total",
    "Total applications created",
    ["source"],
    registry=REGISTRY,
)

APPLICATIONS_UPDATED = Counter(
    "app_applications_updated_total", "Total applications updated", registry=REGISTRY
)

APPLICATIONS_DELETED = Counter(
    "app_applications_deleted_total", "Total applications deleted", registry=REGISTRY
)

# Import metrics
IMPORTS_TOTAL = Counter(
    "app_imports_total",
    "Total import operations",
    ["import_type", "status"],
    registry=REGISTRY,
)

IMPORT_RECORDS = Counter(
    "app_import_records_total",
    "Total records imported",
    ["import_type"],
    registry=REGISTRY,
)

IMPORT_DURATION = Histogram(
    "app_import_duration_seconds",
    "Import operation duration",
    ["import_type"],
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
    registry=REGISTRY,
)

# AI Feature metrics
AI_REQUESTS_TOTAL = Counter(
    "app_ai_requests_total",
    "Total AI requests",
    ["feature", "status"],
    registry=REGISTRY,
)

AI_REQUEST_DURATION = Histogram(
    "app_ai_request_duration_seconds",
    "AI request duration",
    ["feature"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

# T-004 DE-18/OA-2: intelligence module query latency (US-1 cross-layer
# impact and later DE-9 queries). Declared on the existing REGISTRY -- no
# second registry, no second scrape path (CLAUDE.md "one system of record").
INTELLIGENCE_QUERY_DURATION = Histogram(
    "archie_intelligence_query_seconds",
    "Intelligence module query duration in seconds",
    # ``include_derived`` is a label (not just ``query``/``depth``) because
    # NFR-5's measurement point is specifically cross_layer_impact WITH
    # include_derived=true, max_depth=4 -- without this label the same
    # {query="cross_layer_impact", depth="4"} series would mix cheap
    # explicit-only samples (e.g. the canonical endpoint's app_id-branch-free
    # call) with the expensive derived-walk samples the threshold is
    # actually about, making the p95 read off this metric meaningless.
    ["query", "depth", "include_derived"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
    registry=REGISTRY,
)

# Document upload metrics
DOCUMENTS_UPLOADED = Counter(
    "app_documents_uploaded_total",
    "Total documents uploaded",
    ["source"],
    registry=REGISTRY,
)

DOCUMENT_UPLOAD_SIZE = Histogram(
    "app_document_upload_size_bytes",
    "Document upload size",
    buckets=[1024, 10240, 102400, 1048576, 10485760, 16777216],
    registry=REGISTRY,
)

# User metrics
USER_LOGINS = Counter(
    "app_user_logins_total", "Total user logins", ["status"], registry=REGISTRY
)

ACTIVE_USERS = Gauge(
    "app_active_users", "Number of active users", ["time_period"], registry=REGISTRY
)

# Business metrics
TOTAL_APPLICATIONS = Gauge(
    "app_total_applications", "Total number of applications", registry=REGISTRY
)

TOTAL_VENDORS = Gauge("app_total_vendors", "Total number of vendors", registry=REGISTRY)

TOTAL_CAPABILITIES = Gauge(
    "app_total_capabilities", "Total number of capabilities", registry=REGISTRY
)


class PrometheusMetrics:
    """Helper class for tracking Prometheus metrics"""

    @staticmethod
    def init_app(app):
        """Initialize metrics with Flask app info"""
        APP_INFO.info(
            {
                "name": app.name,
                "version": app.config.get("VERSION", "unknown"),
                "environment": app.config.get("FLASK_ENV", "production"),
            }
        )

    @staticmethod
    def track_http_request(
        method: str, endpoint: str, status_code: int, duration: float
    ):
        """Track HTTP request metrics"""
        HTTP_REQUESTS_TOTAL.labels(
            method=method, endpoint=endpoint, status_code=status_code
        ).inc()
        HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

    @staticmethod
    def track_application_created(source: str = "manual"):
        """Track application creation"""
        APPLICATIONS_CREATED.labels(source=source).inc()

    @staticmethod
    def track_application_updated():
        """Track application update"""
        APPLICATIONS_UPDATED.inc()

    @staticmethod
    def track_application_deleted():
        """Track application deletion"""
        APPLICATIONS_DELETED.inc()

    @staticmethod
    def track_import(
        import_type: str, status: str, record_count: int = 0, duration: float = 0
    ):
        """Track import operation"""
        IMPORTS_TOTAL.labels(import_type=import_type, status=status).inc()
        if record_count > 0:
            IMPORT_RECORDS.labels(import_type=import_type).inc(record_count)
        if duration > 0:
            IMPORT_DURATION.labels(import_type=import_type).observe(duration)

    @staticmethod
    def track_ai_request(feature: str, status: str, duration: float = 0):
        """Track AI request"""
        AI_REQUESTS_TOTAL.labels(feature=feature, status=status).inc()
        if duration > 0:
            AI_REQUEST_DURATION.labels(feature=feature).observe(duration)

    @staticmethod
    def track_document_upload(source: str, size_bytes: int):
        """Track document upload"""
        DOCUMENTS_UPLOADED.labels(source=source).inc()
        DOCUMENT_UPLOAD_SIZE.observe(size_bytes)

    @staticmethod
    def track_user_login(status: str = "success"):
        """Track user login"""
        USER_LOGINS.labels(status=status).inc()

    @staticmethod
    def set_active_users(count: int, time_period: str = "24h"):
        """Set active users gauge"""
        ACTIVE_USERS.labels(time_period=time_period).set(count)

    @staticmethod
    def set_totals(applications: int = 0, vendors: int = 0, capabilities: int = 0):
        """Set total counts"""
        if applications > 0:
            TOTAL_APPLICATIONS.set(applications)
        if vendors > 0:
            TOTAL_VENDORS.set(vendors)
        if capabilities > 0:
            TOTAL_CAPABILITIES.set(capabilities)


# Global instance
prometheus_metrics = PrometheusMetrics()


def track_metric(metric_name: str, **kwargs):
    """
    Convenience function to track metrics by name.

    Usage:
        track_metric('application_created', source='manual')
        track_metric('import', import_type='csv', status='success', record_count=100)
    """
    trackers = {
        "application_created": prometheus_metrics.track_application_created,
        "application_updated": prometheus_metrics.track_application_updated,
        "application_deleted": prometheus_metrics.track_application_deleted,
        "import": prometheus_metrics.track_import,
        "ai_request": prometheus_metrics.track_ai_request,
        "document_upload": prometheus_metrics.track_document_upload,
        "user_login": prometheus_metrics.track_user_login,
    }

    tracker = trackers.get(metric_name)
    if tracker:
        tracker(**kwargs)


def get_metrics_response():
    """Generate Prometheus metrics response"""
    from flask import Response

    return Response(generate_latest(REGISTRY), mimetype=CONTENT_TYPE_LATEST)


# Platform SLOs read HTTP_REQUESTS_TOTAL / HTTP_REQUEST_DURATION --
# the existing counters above, populated by the one request hook in
# app/_bootstrap/security.py -- rather than adding a second metrics store
# (CLAUDE.md ADR 0008, "one system of record per concept").


def get_http_metrics_registry() -> tuple[CollectorRegistry, bool]:
    """Return the registry to read HTTP_REQUESTS_TOTAL/HTTP_REQUEST_DURATION
    from, plus whether it is the multiprocess-aggregated view.

    Gunicorn runs several worker processes (``gunicorn.conf.py``), each with
    its own in-memory ``REGISTRY`` -- a counter incremented in one worker is
    invisible to another. When ``PROMETHEUS_MULTIPROC_DIR`` is set,
    ``prometheus_client``'s multiprocess mode combines every worker's counter
    files from that directory into one registry; this is the correct read
    path in that deployment. When it is not set (the case today -- nothing in
    this repo sets it), this returns the single process-local ``REGISTRY``,
    and the caller must treat attainment as reflecting only the worker that
    served the request, not the whole fleet (documented in
    ``docs/platform-slos.md``).
    """
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return REGISTRY, False

    from prometheus_client import multiprocess

    combined = CollectorRegistry()
    multiprocess.MultiProcessCollector(combined, path=multiproc_dir)
    return combined, True
