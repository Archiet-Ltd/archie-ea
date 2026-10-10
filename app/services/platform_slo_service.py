"""Platform service level objectives.

Defines availability and p95-latency objectives for the answer, API and
approval services and measures each one's attainment and error-budget burn
from the counters and histogram already declared in
``app.services.prometheus_metrics`` -- ``HTTP_REQUESTS_TOTAL`` and
``HTTP_REQUEST_DURATION``. This module adds no table, no second registry and
no second counter (CLAUDE.md ADR 0008): it is a pure read/aggregation layer
over the existing exported metrics, grouped by the Flask URL rule those
counters are labelled with (``app/_bootstrap/security.py`` is the one place
that increments them, per request, for every route in the app).

Objective definitions:

- ``answers`` -- ``/api/v1/intelligence/*`` -- 99.9% availability, p95 <= 2s.
- ``api``     -- ``/api/v1/*``               -- 99.9% availability, p95 <= 1s.
- ``approvals``-- ``/ai-chat/approvals/*``   -- 99.9% availability, p95 <= 1s.

"Availability" counts a 5xx response as bad; anything else (2xx/3xx/4xx) as
good, matching REQ-NFR-005's SLA framing of *service* failures rather than
caller-input errors. Window: rolling 30 days in production; this process
reports over whatever window it has actually observed since it started (see
docs/platform-slos.md) -- it never claims a 30-day figure it has not measured.

Missing data is never reported as 100%: an objective with zero observed
requests reports ``measured: false`` with reason ``"not measured"``, per
CLAUDE.md's "never invent data" rule and the brief's own "Missing data"
constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.services.prometheus_metrics import get_http_metrics_registry

_NOT_MEASURED_REASON = "not measured"

# Multi-window burn-rate rule (Google SRE workbook): a burn rate above this
# multiplier, sustained across both a long and a short window, means the
# 30-day error budget would be exhausted before the window closes.
# ``compute_burn_alert`` below implements the rule and is unit-tested in its
# own right; it is not wired into ``_objective_status`` because this
# deployment holds one process-lifetime cumulative counter, not a
# time-series store with separate 1h/5m samples (adding a Prometheus server
# to get one is forbidden by NFR-6) -- see the "burn_window_reason" field and
# docs/platform-slos.md.
_BURN_RATE_ALERT_THRESHOLD = 14.4
_BURN_WINDOW_NOT_MEASURED_REASON = "window not measured; process-lifetime counts only"

_REQUESTS_METRIC_NAME = "app_http_requests"
_DURATION_METRIC_NAME = "app_http_request_duration_seconds"

# Mirrors app.modules.intelligence.services.latency_probe's
# _MIN_SAMPLES_FOR_P95 rule: below this many observations a bucket-edge read
# is treated as unmeasured, never as an early, noisy percentile.
_MIN_SAMPLES_FOR_LATENCY_P95 = 100


@dataclass(frozen=True)
class SloObjective:
    """One objective's definition -- real config, not a magic literal."""

    name: str
    route_prefix: str
    availability_target: float
    latency_target_seconds: float


OBJECTIVES: tuple[SloObjective, ...] = (
    SloObjective("answers", "/api/v1/intelligence/", 0.999, 2.0),
    SloObjective("api", "/api/v1/", 0.999, 1.0),
    SloObjective("approvals", "/ai-chat/approvals/", 0.999, 1.0),
)


def compute_burn_alert(
    burn_rate_1h: Optional[float], burn_rate_5m: Optional[float]
) -> Optional[bool]:
    """Multi-window burn-rate alert condition: both windows must exceed the
    threshold. Returns ``None`` (never a fabricated ``False``) when either
    window has no measurement to burn against.
    """
    if burn_rate_1h is None or burn_rate_5m is None:
        return None
    return burn_rate_1h > _BURN_RATE_ALERT_THRESHOLD and burn_rate_5m > _BURN_RATE_ALERT_THRESHOLD


def _collect_families(registry):
    return {family.name: family for family in registry.collect()}


def _matching_request_counts(family, route_prefix: str) -> tuple[int, int]:
    """Return (total_requests, bad_requests) for samples whose ``endpoint``
    label (the URL rule) starts with ``route_prefix``. A 5xx status code is
    bad; everything else counts toward the total but not toward "bad".
    """
    if family is None:
        return 0, 0
    total = 0
    bad = 0
    for sample in family.samples:
        if not sample.name.endswith("_total"):
            continue
        endpoint = sample.labels.get("endpoint", "")
        if not endpoint.startswith(route_prefix):
            continue
        count = int(sample.value)
        total += count
        status_code = str(sample.labels.get("status_code", ""))
        if status_code.startswith("5"):
            bad += count
    return total, bad


def _matching_latency_p95(family, route_prefix: str) -> Optional[float]:
    """Bucket-edge p95 (never interpolated) aggregated across every endpoint
    label under ``route_prefix``, following the same walk-the-declared-
    boundaries approach as
    ``app.modules.intelligence.services.latency_probe.read_p95_bucket_edge``:
    no PromQL, no averaging, no raw-sample retention -- just the first
    declared bucket boundary whose cumulative count reaches 95% of the
    matching total.
    """
    if family is None:
        return None

    bucket_totals: dict[str, float] = {}
    series_total = 0.0
    for sample in family.samples:
        endpoint = sample.labels.get("endpoint", "")
        if not endpoint.startswith(route_prefix):
            continue
        if sample.name.endswith("_bucket"):
            le = sample.labels.get("le")
            bucket_totals[le] = bucket_totals.get(le, 0.0) + sample.value
        elif sample.name.endswith("_count"):
            series_total += sample.value

    if series_total < _MIN_SAMPLES_FOR_LATENCY_P95:
        return None

    threshold = 0.95 * series_total

    def _sort_key(le: str) -> float:
        return float("inf") if le == "+Inf" else float(le)

    for le in sorted(bucket_totals, key=_sort_key):
        if le == "+Inf":
            continue
        if bucket_totals[le] >= threshold:
            return float(le)

    # The 95th percentile falls past every declared boundary: an honest
    # "we don't know" rather than the last finite edge standing in for a
    # real measurement (that edge is where 95% of requests are NOT, by
    # construction -- reporting it as the p95 would be fabricating a number
    # the histogram never actually measured).
    return None


def _objective_status(objective: SloObjective, families: dict) -> dict:
    requests_family = families.get(_REQUESTS_METRIC_NAME)
    duration_family = families.get(_DURATION_METRIC_NAME)

    total, bad = _matching_request_counts(requests_family, objective.route_prefix)

    if total == 0:
        return {
            "measured": False,
            "reason": _NOT_MEASURED_REASON,
            "requests_observed": 0,
            "availability": None,
            "availability_target": objective.availability_target,
            "latency_p95_seconds": None,
            "latency_target_seconds": objective.latency_target_seconds,
            "meets_availability_target": None,
            "meets_latency_target": None,
            "burn_rate_1h": None,
            "burn_rate_5m": None,
            "burn_alert": None,
            "burn_window_reason": _BURN_WINDOW_NOT_MEASURED_REASON,
        }

    availability = (total - bad) / total
    latency_p95 = _matching_latency_p95(duration_family, objective.route_prefix)

    return {
        "measured": True,
        "reason": None,
        "requests_observed": total,
        "availability": availability,
        "availability_target": objective.availability_target,
        "latency_p95_seconds": latency_p95,
        "latency_target_seconds": objective.latency_target_seconds,
        "meets_availability_target": availability >= objective.availability_target,
        "meets_latency_target": (
            None if latency_p95 is None else latency_p95 <= objective.latency_target_seconds
        ),
        # This process has no separate 1-hour/5-minute time-series samples --
        # only a lifetime cumulative count -- so the multi-window burn rule
        # cannot honestly be evaluated yet. Reporting the one real number
        # twice, as if it were two independent windows, would be exactly the
        # kind of invented precision CLAUDE.md's "never invent data" rule
        # forbids, so every field here is null with a stated reason instead.
        "burn_rate_1h": None,
        "burn_rate_5m": None,
        "burn_alert": None,
        "burn_window_reason": _BURN_WINDOW_NOT_MEASURED_REASON,
    }


def get_platform_slo_status() -> dict:
    """Aggregate attainment for every declared objective.

    Returns aggregate numbers only -- objective name, counts and computed
    rates -- never an organisation id, user id, or request path, satisfying
    REQ-NFR-005's exposure constraint for the unauthenticated ``/health/slo``
    endpoint.
    """
    registry, is_multiprocess = get_http_metrics_registry()
    families = _collect_families(registry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": "since process start (rolling 30 days in production; see docs/platform-slos.md)",
        "multiprocess_aggregated": is_multiprocess,
        "objectives": {
            objective.name: _objective_status(objective, families)
            for objective in OBJECTIVES
        },
    }
