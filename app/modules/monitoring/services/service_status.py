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
# Public component view (no organisation, no user: platform-wide facts only)
# --------------------------------------------------------------------------- #

COMPONENT_STATE_LABELS = {
    STATE_OPERATIONAL: "Operational",
    STATE_DEGRADED: "Degraded",
    STATE_OUTAGE: "Down",
    None: "Not checked",
}


@dataclass(frozen=True)
class Component:
    key: str
    name: str
    state: Optional[str]          # None: not checked
    detail: str

    @property
    def label(self) -> str:
        return COMPONENT_STATE_LABELS[self.state]


def _objective(objectives: dict, name: str):
    status = objectives.get(name) or {}
    if not status.get("measured"):
        return None
    met = status.get("meets_availability_target") and status.get("meets_latency_target") is not False
    return STATE_OPERATIONAL if met else STATE_DEGRADED


def _component_checks() -> list[Component]:
    """The five public components, from the checks the platform already runs."""
    from app.modules.monitoring.services.health_service import HealthService

    try:
        from app.services.platform_slo_service import get_platform_slo_status

        objectives = get_platform_slo_status().get("objectives", {})
    except Exception:
        objectives = {}

    web_state = _objective(objectives, "api")
    web = Component(
        "web", "Web application",
        web_state or STATE_OPERATIONAL,
        "Within its availability and response-time targets" if web_state == STATE_OPERATIONAL
        else "Missing its availability or response-time target" if web_state
        else "Serving requests",
    )

    try:
        database_ok = HealthService.check_database().get("status") == "healthy"
        database = Component(
            "database", "Database",
            STATE_OPERATIONAL if database_ok else STATE_OUTAGE,
            "Responding" if database_ok else "Not responding",
        )
    except Exception:
        database = Component("database", "Database", None, "The check could not run")

    try:
        cache = HealthService.check_cache().get("status")
    except Exception:
        cache = "error"
    if cache == "error":
        jobs = Component("background-jobs", "Background jobs", None, "The check could not run")
    elif cache == "healthy":
        jobs = Component("background-jobs", "Background jobs", STATE_OPERATIONAL, "Job queue responding")
    elif cache == "unhealthy":
        jobs = Component("background-jobs", "Background jobs", STATE_OUTAGE, "Job queue not responding")
    else:
        jobs = Component("background-jobs", "Background jobs", None, "No job queue is configured to check")

    answers = _objective(objectives, "answers")
    ai = Component(
        "ai-provider", "AI answers", answers,
        "Within its availability and response-time targets" if answers == STATE_OPERATIONAL
        else "Missing its availability or response-time target" if answers
        else "No AI answers observed since the service started",
    )

    email = Component("email", "Email", None, "Email delivery is not probed from this page")
    return [web, database, jobs, ai, email]


def public_status() -> dict:
    """Everything the public status page shows; renders even when the database is down."""
    components = _component_checks()
    incidents_checked = True
    open_now, history = [], []
    if next(c for c in components if c.key == "database").state == STATE_OPERATIONAL:
        try:
            open_now = open_incidents()
            history = incident_history()
        except Exception:
            db.session.rollback()
            incidents_checked = False
    else:
        incidents_checked = False
    states = [c.state for c in components]
    db_down = any(c.key == "database" and c.state == STATE_OUTAGE for c in components)
    state = _worst([STATE_OUTAGE if db_down else (STATE_DEGRADED if s == STATE_OUTAGE else s) for s in states]
                   + [i.impact for i in open_now])
    return {
        "state": state,
        "label": STATE_LABELS[state],
        "components": components,
        "open_incidents": open_now,
        "history": history,
        "incidents_checked": incidents_checked,
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
