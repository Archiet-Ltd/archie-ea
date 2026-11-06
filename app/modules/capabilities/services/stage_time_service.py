"""How long each value stream stage really takes, measured from Jira.

A stage is mapped to one or more Jira statuses. For every issue the time it
spent in those statuses comes from the issue's status history (the changelog
Jira returns with ``expand=changelog``); the stage's figure is the median of
those times, with the number of issues it came from. A figure typed by a person
is stored beside it, labelled as typed, and is never presented as measured.

One writer owns every change to a stage's duration fields: ``apply_measurement``
for Jira, ``record_typed`` for a person.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.datetime_helpers import utcnow
from app.models.unified_capability import ValueStream, ValueStreamStage
from app.services.flow_analytics_service import _percentile

logger = logging.getLogger(__name__)

# Fewer issues than this is not a sample worth a median.
MIN_SAMPLE = 5
# Issues kept on the stage as evidence; the sample size is always the true count.
MAX_STORED_SAMPLES = 200
UNITS = ("days", "hours")


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        from datetime import timezone

        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def hours_in_statuses(issue: Dict[str, Any], statuses: Iterable[str]) -> Optional[float]:
    """Hours the issue spent in any of ``statuses``, from its status history.

    Only visits that have ended count: an issue still sitting in a mapped status
    has no finished visit to measure. ``None`` when the issue never completed a
    visit to one of them (or has no readable history).
    """
    wanted = {s.strip().lower() for s in statuses if s and s.strip()}
    if not wanted:
        return None
    created = _parse_time((issue.get("fields") or {}).get("created"))
    transitions = []
    for history in (issue.get("changelog") or {}).get("histories") or []:
        at = _parse_time(history.get("created"))
        if at is None:
            continue
        for item in history.get("items") or []:
            if item.get("field") == "status":
                transitions.append((at, item.get("fromString"), item.get("toString")))
    if created is None or not transitions:
        return None
    transitions.sort(key=lambda t: t[0])

    entered = created
    current = transitions[0][1]
    total = 0.0
    visited = False
    for at, _from, to in transitions:
        if (current or "").strip().lower() in wanted:
            total += (at - entered).total_seconds() / 3600.0
            visited = True
        current, entered = to, at
    return total if visited else None


def measure_stage(issues: List[Dict[str, Any]], statuses: Iterable[str]) -> Dict[str, Any]:
    """The median hours and the issues behind it, for one stage's statuses."""
    statuses = list(statuses)
    samples = []
    for issue in issues:
        hours = hours_in_statuses(issue, statuses)
        if hours is not None:
            samples.append({"key": issue.get("key"), "hours": round(hours, 2)})
    values = sorted(s["hours"] for s in samples)
    median = _percentile(values, 50) if len(values) >= MIN_SAMPLE else None
    samples.sort(key=lambda s: s["hours"], reverse=True)
    return {"median_hours": median, "sample_size": len(samples), "samples": samples}


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def parse_statuses(text: Optional[str]) -> List[str]:
    """Status names from a comma or line separated field, without blanks or repeats."""
    seen, out = set(), []
    for part in str(text or "").replace("\n", ",").split(","):
        name = part.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def stage_statuses(stage: ValueStreamStage) -> List[str]:
    try:
        value = json.loads(stage.jira_statuses) if stage.jira_statuses else []
    except ValueError:
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def save_mapping(org_id: int, value_stream_id: int, by_stage: Dict[int, str]) -> int:
    """Store each stage's Jira statuses; returns how many stages are now mapped."""
    from app import db

    stages = _stages(org_id, value_stream_id)
    for stage in stages:
        if stage.id in by_stage:
            names = parse_statuses(by_stage[stage.id])
            new = json.dumps(names) if names else None
            if new != stage.jira_statuses:
                stage.jira_statuses = new
    db.session.commit()
    return sum(1 for s in stages if stage_statuses(s))


def _stages(org_id: int, value_stream_id: Optional[int] = None) -> List[ValueStreamStage]:
    query = ValueStreamStage.query.filter(ValueStreamStage.organization_id == org_id)
    if value_stream_id is not None:
        query = query.filter(ValueStreamStage.value_stream_id == value_stream_id)
    return query.order_by(ValueStreamStage.stage_order).all()


# ---------------------------------------------------------------------------
# The one writer
# ---------------------------------------------------------------------------


def _in_unit(hours: float, unit: str) -> int:
    return int(round(hours / 24.0 if unit == "days" else hours))


def apply_measurement(stage: ValueStreamStage, result: Dict[str, Any], base_url: Optional[str]) -> str:
    """Write a Jira measurement onto the stage. Returns "measured", "too_few" or "kept_typed"."""
    median = result["median_hours"]
    if median is None and stage.duration_source == "typed":
        # Too few issues must not erase a figure a person entered.
        return "kept_typed"
    unit = stage.duration_unit if stage.duration_unit in UNITS else "days"
    stage.duration_unit = unit
    stage.duration_source = "jira"
    stage.duration_median_hours = median
    stage.current_duration = _in_unit(median, unit) if median is not None else None
    stage.duration_sample_size = result["sample_size"]
    stage.duration_measured_at = utcnow().replace(tzinfo=None)
    stage.duration_recorded_by = None
    base = (base_url or "").rstrip("/")
    stage.duration_samples = json.dumps(
        [
            {"key": s["key"], "hours": s["hours"], "url": "%s/browse/%s" % (base, s["key"]) if base else None}
            for s in result["samples"][:MAX_STORED_SAMPLES]
        ]
    )
    return "measured" if median is not None else "too_few"


def record_typed(stage: ValueStreamStage, value: Optional[int], unit: Optional[str], user_name: Optional[str]) -> None:
    """Store a duration a person typed, labelled as typed. ``None`` clears it."""
    if unit in UNITS:
        stage.duration_unit = unit
    stage.current_duration = value
    stage.duration_median_hours = None
    stage.duration_sample_size = None
    stage.duration_samples = None
    if value is None:
        stage.duration_source = None
        stage.duration_measured_at = None
        stage.duration_recorded_by = None
        return
    stage.duration_source = "typed"
    stage.duration_measured_at = utcnow().replace(tzinfo=None)
    stage.duration_recorded_by = (user_name or "").strip() or "a team member"


def measure_from_issues(org_id: int, issues: List[Dict[str, Any]], base_url: Optional[str],
                        value_stream_id: Optional[int] = None) -> Dict[str, Any]:
    """Measure every mapped stage of the organisation (or of one stream) from fetched issues."""
    from app import db

    outcomes = {"measured": 0, "too_few": 0, "kept_typed": 0}
    for stage in _stages(org_id, value_stream_id):
        statuses = stage_statuses(stage)
        if not statuses:
            continue
        outcomes[apply_measurement(stage, measure_stage(issues, statuses), base_url)] += 1
    db.session.commit()
    return outcomes


class JiraNotConnected(Exception):
    """The organisation has no Jira connection to measure from."""


def jira_connection(org_id: int):
    from app.models.connector_config import ConnectorConfig

    return ConnectorConfig.query.filter_by(organization_id=org_id, connector_type="jira").first()


def measure_from_jira(org_id: int, value_stream_id: int) -> Dict[str, Any]:
    """Fetch the issues with their status history and measure one stream's mapped stages."""
    from app.modules.intelligence.services.connector_allowlist import assert_connector_permitted
    from app.services.connector_framework import build_connector, run_coroutine_sync

    cfg = jira_connection(org_id)
    if cfg is None:
        raise JiraNotConnected()
    assert_connector_permitted(cfg.connector_type)
    connector = build_connector(cfg, org_id)
    issues = run_coroutine_sync(connector.fetch_issues_with_changelog())
    return measure_from_issues(org_id, issues, connector.config.config.get("base_url"), value_stream_id)


# ---------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------


def _fmt(value: float) -> str:
    return ("%.1f" % value).rstrip("0").rstrip(".")


def _date(when: Optional[datetime]) -> Optional[str]:
    return "%d %s %d" % (when.day, when.strftime("%b"), when.year) if when else None


def describe_stage(stage: ValueStreamStage) -> Dict[str, Any]:
    """Plain facts about a stage's time for the page: figure, target, provenance."""
    unit = stage.duration_unit if stage.duration_unit in UNITS else "days"
    source = stage.duration_source
    measured = source == "jira" and stage.duration_median_hours is not None
    too_few = source == "jira" and stage.duration_median_hours is None
    figure = None
    if measured:
        hours = stage.duration_median_hours
        figure = "%s %s" % (_fmt(hours / 24.0 if unit == "days" else hours), unit)
    elif stage.current_duration is not None:
        figure = "%s %s" % (stage.current_duration, unit)
    when = _date(stage.duration_measured_at)
    if measured:
        provenance = "Measured from Jira: median of %d issues, %s" % (stage.duration_sample_size or 0, when)
    elif too_few:
        provenance = "Too few to measure: %d of the %d issues needed, %s" % (
            stage.duration_sample_size or 0, MIN_SAMPLE, when)
    elif source == "typed":
        provenance = "Typed by %s, %s" % (stage.duration_recorded_by or "a team member", when)
    elif stage.current_duration is not None:
        provenance = "Entered earlier; who typed it and when was not recorded"
    else:
        provenance = None
    try:
        samples = json.loads(stage.duration_samples) if stage.duration_samples else []
    except ValueError:
        samples = []
    return {
        "statuses": stage_statuses(stage),
        "statuses_text": ", ".join(stage_statuses(stage)),
        "unit": unit,
        "measured": measured,
        "too_few": too_few,
        "typed": source == "typed",
        "figure": figure,
        "median_hours": stage.duration_median_hours if measured else None,
        "target": "%s %s" % (stage.target_duration, unit) if stage.target_duration is not None else None,
        "provenance": provenance,
        "sample_size": stage.duration_sample_size,
        "samples": samples,
        "slowest": False,
    }


def describe_stages(stages: List[ValueStreamStage]) -> Dict[int, Dict[str, Any]]:
    """``describe_stage`` for each stage, with the slowest measured stage flagged.

    The flag needs at least two measured stages: with one there is nothing to be
    slower than.
    """
    info = {s.id: describe_stage(s) for s in stages}
    measured = [(i["median_hours"], sid) for sid, i in info.items() if i["measured"]]
    if len(measured) >= 2:
        info[max(measured)[1]]["slowest"] = True
    return info
