"""The service-status answer: is the platform healthy right now, and what happened.

Reads only what the platform already measures:

* the database probe of ``HealthService`` (this module's health service);
* the availability and latency objectives of ``platform_slo_service``, which
  aggregates the per-request counters every request already increments;
* open and recent ``ServiceIncident`` rows.

The current state is the worst of the three: an open outage incident or a
failed database probe is an outage; an open degraded incident or a measured
objective that misses its target is degraded; otherwise operational. An
objective with no requests observed yet is shown as not measured, never as
met.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.extensions import db

STATE_OPERATIONAL = "operational"
STATE_DEGRADED = "degraded"
STATE_OUTAGE = "outage"
_RANK = {STATE_OPERATIONAL: 0, STATE_DEGRADED: 1, STATE_OUTAGE: 2}

STATE_LABELS = {
    STATE_OPERATIONAL: "All systems operational",
    STATE_DEGRADED: "Degraded performance",
    STATE_OUTAGE: "Service outage",
}

HISTORY_DAYS = 90

_OBJECTIVE_LABELS = {
    "answers": "Answers",
    "api": "Public interface",
    "approvals": "Approvals",
}


@dataclass(frozen=True)
class Check:
    name: str
    state: Optional[str]          # None: not measured
    detail: str


def _worst(states) -> str:
    worst = STATE_OPERATIONAL
    for state in states:
        if state is not None and _RANK[state] > _RANK[worst]:
            worst = state
    return worst


def _database_check() -> Check:
    from app.modules.monitoring.services.health_service import HealthService

    result = HealthService.check_database()
    if result.get("status") == "healthy":
        return Check("Database", STATE_OPERATIONAL, "Responding")
    return Check("Database", STATE_OUTAGE, "Not responding")


def _objective_checks() -> list[Check]:
    from app.services.platform_slo_service import get_platform_slo_status

    checks = []
    for name, status in get_platform_slo_status().get("objectives", {}).items():
        label = _OBJECTIVE_LABELS.get(name, name.replace("_", " ").capitalize())
        if not status.get("measured"):
            checks.append(Check(label, None, "Not measured yet"))
            continue
        met = status.get("meets_availability_target") and status.get("meets_latency_target") is not False
        if met:
            checks.append(Check(label, STATE_OPERATIONAL, "Within its availability and response-time targets"))
        else:
            checks.append(Check(label, STATE_DEGRADED, "Missing its availability or response-time target"))
    return checks


def open_incidents():
    from app.models.service_incident import ServiceIncident

    return (
        ServiceIncident.query.filter(ServiceIncident.resolved_at.is_(None))
        .order_by(ServiceIncident.started_at.desc())
        .all()
    )


def incident_history(days: int = HISTORY_DAYS):
    from app.models.service_incident import ServiceIncident

    since = datetime.utcnow() - timedelta(days=days)
    return (
        ServiceIncident.query.filter(ServiceIncident.started_at >= since)
        .order_by(ServiceIncident.started_at.desc())
        .all()
    )


def current_status() -> dict:
    checks = [_database_check(), *_objective_checks()]
    incidents = open_incidents()
    state = _worst([c.state for c in checks] + [i.impact for i in incidents])
    return {
        "state": state,
        "label": STATE_LABELS[state],
        "checks": checks,
        "open_incidents": incidents,
        "history": incident_history(),
        "checked_at": datetime.utcnow(),
    }


# --------------------------------------------------------------------------- #
# Subscription (the signed-in user's own preference row)
# --------------------------------------------------------------------------- #


def is_subscribed(user_id: int) -> bool:
    from app.models.ai_suggestion import UserPreference

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    return bool(pref and pref.notify_on_service_status)


def set_subscribed(user_id: int, subscribed: bool) -> bool:
    from app.models.ai_suggestion import UserPreference

    pref = UserPreference.query.filter_by(user_id=user_id).first()
    if pref is None:
        pref = UserPreference(user_id=user_id)
        db.session.add(pref)
    pref.notify_on_service_status = bool(subscribed)
    db.session.commit()
    return pref.notify_on_service_status


# --------------------------------------------------------------------------- #
# Operator actions (the CLI)
# --------------------------------------------------------------------------- #


def open_incident(title: str, impact: str, summary: Optional[str] = None):
    from app.models.service_incident import IMPACTS, ServiceIncident

    title = (title or "").strip()
    if not title:
        raise ValueError("An incident needs a title.")
    if impact not in IMPACTS:
        raise ValueError(f"Impact must be one of: {', '.join(IMPACTS)}.")
    incident = ServiceIncident(title=title[:200], impact=impact, summary=(summary or None))
    db.session.add(incident)
    db.session.commit()
    return incident


def resolve_incident(incident_id: int):
    from app.models.service_incident import ServiceIncident

    incident = db.session.get(ServiceIncident, incident_id)
    if incident is None:
        raise LookupError(f"No incident {incident_id}.")
    if incident.resolved_at is None:
        incident.resolved_at = datetime.utcnow()
        db.session.commit()
    return incident
