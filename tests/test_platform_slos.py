"""Platform SLO attainment for answers, API and approval services.

Uses a fresh, isolated CollectorRegistry (monkeypatched in place of
``get_http_metrics_registry``) for every attainment/burn assertion, so these
tests never depend on -- or pollute -- the shared process-global
``HTTP_REQUESTS_TOTAL``/``HTTP_REQUEST_DURATION`` counters that real request
traffic also writes to. The one exception is
``test_early_hook_failure_is_counted_as_a_bad_request``, which deliberately
exercises the real app-wide hook in ``app/_bootstrap/security.py`` against
the real global registry.
"""

from __future__ import annotations

import pytest
from flask import Blueprint
from prometheus_client import CollectorRegistry, Counter, Histogram

from app.services import platform_slo_service as slo


def _fresh_registry():
    """A private registry declaring the same metric names/labels as
    ``app.services.prometheus_metrics``, so the module's aggregation code
    exercises the real code path against controlled, synthetic data.
    """
    registry = CollectorRegistry()
    requests_total = Counter(
        "app_http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status_code"],
        registry=registry,
    )
    request_duration = Histogram(
        "app_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 30.0, 60.0],
        registry=registry,
    )
    return registry, requests_total, request_duration


def _patched_status(monkeypatch, registry):
    monkeypatch.setattr(slo, "get_http_metrics_registry", lambda: (registry, False))
    return slo.get_platform_slo_status()


def test_attainment_from_synthetic_counter_values(monkeypatch):
    """A route with real, healthy traffic reports a real availability and a
    real bucket-edge p95, computed from the counters -- never a literal."""
    registry, requests_total, duration = _fresh_registry()

    for _ in range(999):
        requests_total.labels(
            method="GET", endpoint="/api/v1/intelligence/cross-layer-impact", status_code="200"
        ).inc()
        duration.labels(
            method="GET", endpoint="/api/v1/intelligence/cross-layer-impact"
        ).observe(0.4)
    requests_total.labels(
        method="GET", endpoint="/api/v1/intelligence/cross-layer-impact", status_code="500"
    ).inc()

    status = _patched_status(monkeypatch, registry)
    answers = status["objectives"]["answers"]

    assert answers["measured"] is True
    assert answers["requests_observed"] == 1000
    assert answers["availability"] == 0.999
    assert answers["meets_availability_target"] is True
    assert answers["latency_p95_seconds"] == 0.5
    assert answers["meets_latency_target"] is True


def test_availability_is_null_reason_not_measured_when_unobserved(monkeypatch):
    """An objective with zero observed requests is `measured: false`, never a
    fabricated 100% (CLAUDE.md "never invent data")."""
    registry, _requests_total, _duration = _fresh_registry()

    status = _patched_status(monkeypatch, registry)

    for objective_name in ("answers", "api", "approvals"):
        objective = status["objectives"][objective_name]
        assert objective["measured"] is False
        assert objective["reason"] == "not measured"
        assert objective["availability"] is None
        assert objective["latency_p95_seconds"] is None
        # Never a fabricated 100 (or 100%) standing in for "unmeasured".
        assert objective["availability"] != 100
        assert objective["availability"] != 1.0


def test_unmeasured_traffic_on_other_prefixes_does_not_leak_into_api(monkeypatch):
    """Requests outside every declared prefix are simply not counted --
    the `api` objective stays unmeasured rather than reporting on them."""
    registry, requests_total, _duration = _fresh_registry()
    requests_total.labels(method="GET", endpoint="/health", status_code="200").inc()

    status = _patched_status(monkeypatch, registry)

    assert status["objectives"]["api"]["measured"] is False
    assert status["objectives"]["approvals"]["measured"] is False


def test_burn_alert_true_when_both_windows_exceed_threshold():
    assert slo.compute_burn_alert(20.0, 15.0) is True


def test_burn_alert_false_when_either_window_is_below_threshold():
    assert slo.compute_burn_alert(20.0, 5.0) is False
    assert slo.compute_burn_alert(1.0, 1.0) is False


def test_burn_alert_is_null_not_false_when_unmeasured():
    assert slo.compute_burn_alert(None, 20.0) is None
    assert slo.compute_burn_alert(20.0, None) is None
    assert slo.compute_burn_alert(None, None) is None


def test_burn_windows_are_honestly_null_end_to_end(monkeypatch):
    """No real 1h/5m time-series windows exist in this process, so
    `/health/slo` must never present its one lifetime observation as two
    independent windows -- every burn field is null with a stated reason,
    on both a measured and an unmeasured objective."""
    registry, requests_total, duration = _fresh_registry()

    for _ in range(95):
        requests_total.labels(
            method="POST", endpoint="/ai-chat/approvals/1/approve", status_code="200"
        ).inc()
        duration.labels(method="POST", endpoint="/ai-chat/approvals/1/approve").observe(0.1)
    for _ in range(5):
        requests_total.labels(
            method="POST", endpoint="/ai-chat/approvals/1/approve", status_code="500"
        ).inc()

    status = _patched_status(monkeypatch, registry)

    approvals = status["objectives"]["approvals"]
    assert approvals["measured"] is True
    assert approvals["availability"] == 0.95
    assert approvals["meets_availability_target"] is False
    assert approvals["burn_rate_1h"] is None
    assert approvals["burn_rate_5m"] is None
    assert approvals["burn_alert"] is None
    assert approvals["burn_window_reason"] == "window not measured; process-lifetime counts only"

    answers = status["objectives"]["answers"]
    assert answers["measured"] is False
    assert answers["burn_rate_1h"] is None
    assert answers["burn_rate_5m"] is None
    assert answers["burn_alert"] is None
    assert answers["burn_window_reason"] == "window not measured; process-lifetime counts only"


def test_latency_p95_is_null_past_the_last_finite_bucket(monkeypatch):
    """A service whose traffic mostly overflows the histogram's declared
    buckets reports p95 as null -- never the last finite edge standing in
    for a measurement the histogram never actually took."""
    registry, requests_total, duration = _fresh_registry()

    for _ in range(100):
        requests_total.labels(
            method="GET", endpoint="/api/v1/slow-thing", status_code="200"
        ).inc()
        # 90s is past the highest declared finite edge (60.0), so every
        # observation lands in the +Inf overflow bucket.
        duration.labels(method="GET", endpoint="/api/v1/slow-thing").observe(90.0)

    status = _patched_status(monkeypatch, registry)
    api = status["objectives"]["api"]

    assert api["measured"] is True
    assert api["latency_p95_seconds"] is None
    assert api["meets_latency_target"] is None


def test_latency_p95_below_min_sample_threshold_is_null(monkeypatch):
    """Fewer than 100 observations is treated as unmeasured for latency,
    mirroring latency_probe.read_p95_bucket_edge's minimum-sample rule."""
    registry, requests_total, duration = _fresh_registry()

    for _ in range(10):
        requests_total.labels(
            method="GET", endpoint="/api/v1/rare-thing", status_code="200"
        ).inc()
        duration.labels(method="GET", endpoint="/api/v1/rare-thing").observe(1.5)

    status = _patched_status(monkeypatch, registry)
    api = status["objectives"]["api"]

    assert api["measured"] is True  # request count is unaffected
    assert api["latency_p95_seconds"] is None


def test_latency_p95_at_1_5_and_60_seconds(monkeypatch):
    """1.5s and 60s requests each land on a real declared bucket edge."""
    registry, requests_total, duration = _fresh_registry()

    for _ in range(99):
        requests_total.labels(
            method="GET", endpoint="/api/v1/mixed-latency", status_code="200"
        ).inc()
        duration.labels(method="GET", endpoint="/api/v1/mixed-latency").observe(1.5)
    requests_total.labels(
        method="GET", endpoint="/api/v1/mixed-latency", status_code="200"
    ).inc()
    duration.labels(method="GET", endpoint="/api/v1/mixed-latency").observe(60.0)

    status = _patched_status(monkeypatch, registry)
    api = status["objectives"]["api"]

    assert api["measured"] is True
    assert api["requests_observed"] == 100
    # 99 of 100 requests (99%) fall at or below the 2.0s bucket -- the first
    # declared edge reaching the 95% threshold.
    assert api["latency_p95_seconds"] == 2.0


def test_health_slo_endpoint_shape(app):
    """`/health/slo` is reachable unauthenticated and returns the three
    declared objectives with the fields the brief specifies."""
    with app.test_client() as client:
        response = client.get("/health/slo")

    assert response.status_code == 200
    body = response.get_json()

    assert "objectives" in body
    assert set(body["objectives"].keys()) == {"answers", "api", "approvals"}
    for objective in body["objectives"].values():
        assert "measured" in objective
        assert "availability" in objective
        assert "availability_target" in objective
        assert "latency_p95_seconds" in objective
        assert "latency_target_seconds" in objective
        assert "burn_rate_1h" in objective
        assert "burn_rate_5m" in objective
        assert "burn_alert" in objective
        # Never presented as two real windows -- see burn_window_reason.
        assert objective["burn_alert"] is None


def test_health_slo_endpoint_leaks_no_organisation_or_user_data(app):
    """REQ-NFR-005: aggregate numbers only -- no organisation names, user
    data or request paths beyond the three fixed objective identifiers."""
    with app.test_client() as client:
        response = client.get("/health/slo")

    body = response.get_json()
    serialized = str(body).lower()

    for forbidden in ("organization_id", "org_id", "user_id", "email", "@"):
        assert forbidden not in serialized

    # The only strings identifying "what" are the three fixed objective
    # names -- not a per-tenant or per-user value.
    assert set(body["objectives"].keys()) == {"answers", "api", "approvals"}


_EARLY_FAILURE_BP_NAME = "test_platform_slo_early_failure_bp"
_EARLY_FAILURE_PATH = "/api/v1/__test_platform_slo_early_failure"


def test_early_hook_failure_is_counted_as_a_bad_request():
    """A request that fails in an earlier `before_request` hook -- before
    the view, and before this module's own `after_request` ever runs --
    must still be recorded, and as a bad (5xx) request: an outage that
    crashes before producing a response is exactly what an availability SLO
    exists to catch, not a gap it silently drops.

    Exercises the real hook wired in app/_bootstrap/security.py (teardown
    always runs, unlike after_request) against the real global registry --
    the one test in this file that does not use an isolated registry. Builds
    its own fresh app (rather than the shared session ``app`` fixture) so
    registering the failing blueprint is never rejected for happening after
    the shared app has already served a request from another test.
    """
    from app import create_app

    fresh_app = create_app("testing")
    fresh_app.config["TESTING"] = True
    fresh_app.config["WTF_CSRF_ENABLED"] = False

    early_failure_bp = Blueprint(_EARLY_FAILURE_BP_NAME, __name__)

    @early_failure_bp.before_request
    def _boom():
        raise RuntimeError("simulated early before_request failure")

    @early_failure_bp.route(_EARLY_FAILURE_PATH)
    def _never_reached():
        return "unreachable"

    fresh_app.register_blueprint(early_failure_bp)

    with fresh_app.test_client() as client:
        with pytest.raises(RuntimeError):
            client.get(_EARLY_FAILURE_PATH)

    status = slo.get_platform_slo_status()
    api = status["objectives"]["api"]

    assert api["measured"] is True
    assert api["requests_observed"] >= 1
    # The failing request is the only one this test itself sent to a route
    # under this exact never-reused path; a 5xx-only availability figure
    # below 1.0 proves it was counted as bad, not dropped.
    assert api["availability"] < 1.0
