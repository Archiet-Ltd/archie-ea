"""AI evaluation plans, runs and the release check (R1-B55, TB-0331, PB-0234).

An AI system cannot be marked released without a current, passing evaluation
against its own thresholds. This module owns the plan (test set and
thresholds), the recorded runs and the one check the release gate calls
(``evaluation_refusal_reason``), which names the missing or failing metric
instead of refusing generically.

Reuse
-----
* The AI system is the existing ``ArchiMateElement`` the AI systems register
  models (``ai_system_profile.is_ai_system``); ``ai_system_id`` on both new
  tables is that element's id. The model a run was made against is read from
  the same profile (``profile_from_element``), not re-parsed.
* ``ai_system_classification._lock_element`` serialises plan and run writes
  with release and classification acceptance on the one element row, so a
  release never reads a plan that is changing underneath it.
* The refusal flows through ``ai_system_classification.release_refusal_reason``
  (the one message source the release route and the register page share).
* Cost: ``cost_per_task`` is the measured cost per task, in the currency
  ``llm_cost_tracker`` reports (GBP). It is entered from the run, not
  recomputed here: the tracker's price table is an estimate, and a threshold
  must be compared with a measured figure, never an estimated one.

Organisation scoping is explicit everywhere: the automatic tenant filter does
nothing when no organisation is set, so every read names ``organization_id``
and every write refuses when it is ``None``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app import db
from app.models.ai_evaluation_plan import AIEvaluationPlan
from app.models.ai_evaluation_run import AIEvaluationRun

# metric -> (direction, upper bound or None, label). "min": the measured value
# must be at least the threshold; "max": at most the threshold. Ratios are
# fractions between 0 and 1; cost per task is a non-negative amount.
METRICS = {
    "groundedness": ("min", 1.0, "Groundedness"),
    "task_success": ("min", 1.0, "Task success"),
    "refusal_correctness": ("min", 1.0, "Refusal correctness"),
    "cost_per_task": ("max", None, "Cost per task"),
}

# Upper bound for case counts (the columns are 32-bit integers).
MAX_CASES = 1_000_000

NO_ORGANISATION = "Release refused: no organisation is set, so no evaluation can be read."


class EvaluationError(ValueError):
    """A plan or run was refused; the message is shown to the person."""


@dataclass
class PlanInput:
    test_set_name: str
    test_set_size: int
    thresholds: dict


def _clean_number(metric: str, raw, *, what: str) -> float:
    """A finite, in-range number for *metric*, or EvaluationError naming it."""
    if isinstance(raw, bool):
        raise EvaluationError(f"{what} for {metric} must be a number.")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise EvaluationError(f"{what} for {metric} must be a number.") from None
    if not math.isfinite(value):
        raise EvaluationError(f"{what} for {metric} must be a finite number.")
    _direction, upper, _label = METRICS[metric]
    if value < 0 or (upper is not None and value > upper):
        bound = "zero or more" if upper is None else f"between 0 and {upper:g}"
        raise EvaluationError(f"{what} for {metric} must be {bound}.")
    return value


def _clean_metric_map(raw: dict, *, what: str) -> dict:
    cleaned = {}
    for metric, value in (raw or {}).items():
        if metric not in METRICS:
            raise EvaluationError(
                f"{metric} is not an evaluation metric; use one of "
                f"{', '.join(METRICS)}."
            )
        cleaned[metric] = _clean_number(metric, value, what=what)
    return cleaned


def scoped_ai_system(element_id, organization_id):
    """The AI-system element in *organization_id*, or ``None``."""
    from app.models.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    if organization_id is None or element_id is None:
        return None
    element = ArchiMateElement.query.filter_by(
        id=element_id, organization_id=organization_id
    ).first()
    return element if element is not None and is_ai_system(element) else None


def _system_model_id(element) -> Optional[str]:
    from app.modules.enterprise_genome.services.ai_system_profile import recorded_model_id

    return recorded_model_id(element)


def get_plan(element_id, organization_id) -> Optional[AIEvaluationPlan]:
    if organization_id is None:
        return None
    return AIEvaluationPlan.query.filter_by(
        organization_id=organization_id, ai_system_id=element_id
    ).first()


def latest_run(element_id, organization_id) -> Optional[AIEvaluationRun]:
    if organization_id is None:
        return None
    return (
        AIEvaluationRun.query.filter_by(
            organization_id=organization_id, ai_system_id=element_id
        )
        .order_by(AIEvaluationRun.id.desc())
        .first()
    )


def summaries_for(element_ids, organization_id) -> dict:
    """``{element_id: {"plan": plan or None, "run": latest run or None}}`` for
    the systems on the page, in two queries (the register resolves extras only
    for the rows it shows)."""
    out = {int(i): {"plan": None, "run": None} for i in element_ids}
    if organization_id is None or not out:
        return out
    for plan in AIEvaluationPlan.query.filter(
        AIEvaluationPlan.organization_id == organization_id,
        AIEvaluationPlan.ai_system_id.in_(list(out)),
    ):
        out[plan.ai_system_id]["plan"] = plan
    for run in (
        AIEvaluationRun.query.filter(
            AIEvaluationRun.organization_id == organization_id,
            AIEvaluationRun.ai_system_id.in_(list(out)),
        ).order_by(AIEvaluationRun.id.asc())
    ):
        out[run.ai_system_id]["run"] = run  # ascending, so the last write is the latest
    return out


def set_plan(
    *,
    element_id,
    organization_id,
    test_set_name,
    test_set_size,
    thresholds,
    user_id=None,
) -> AIEvaluationPlan:
    """Create the system's plan, or change it. A change to the test set or a
    threshold raises ``version``, so earlier runs stop being current; an
    identical plan leaves the version alone."""
    from app.modules.enterprise_genome.services.ai_system_classification import (
        _lock_element,
    )

    if organization_id is None:
        raise EvaluationError("No organisation is set, so no plan can be saved.")
    if scoped_ai_system(element_id, organization_id) is None:
        raise EvaluationError("AI system not found.")

    name = (test_set_name or "").strip()
    if not name:
        raise EvaluationError("A plan needs the name of its test set.")
    if len(name) > 200:
        raise EvaluationError("The test set name is limited to 200 characters.")
    if isinstance(test_set_size, bool) or not isinstance(test_set_size, int) or test_set_size < 1:
        raise EvaluationError("The test set size must be a whole number of at least 1.")
    if test_set_size > MAX_CASES:
        raise EvaluationError(f"The test set size is limited to {MAX_CASES:,} cases.")
    cleaned = _clean_metric_map(thresholds, what="Threshold")
    if not cleaned:
        raise EvaluationError("A plan needs at least one threshold.")

    if _lock_element(element_id, organization_id) is None:
        raise EvaluationError("AI system not found.")
    plan = get_plan(element_id, organization_id)
    if plan is None:
        plan = AIEvaluationPlan(
            organization_id=organization_id,
            ai_system_id=element_id,
            version=1,
            test_set_name=name,
            test_set_size=test_set_size,
            thresholds=cleaned,
            created_by_id=user_id,
        )
        db.session.add(plan)
    elif (
        plan.test_set_name != name
        or plan.test_set_size != test_set_size
        or (plan.thresholds or {}) != cleaned
    ):
        plan.version = (plan.version or 1) + 1
        plan.test_set_name = name
        plan.test_set_size = test_set_size
        plan.thresholds = cleaned
    db.session.flush()
    return plan


def record_run(
    *,
    element_id,
    organization_id,
    metrics,
    cases_run,
    notes=None,
    user_id=None,
) -> AIEvaluationRun:
    """Record a run against the system's current plan. The plan version and the
    model the system currently records are taken from the system, not from the
    caller, so a run cannot claim a plan or model it was not entered under."""
    from app.modules.enterprise_genome.services.ai_system_classification import (
        _lock_element,
    )

    if organization_id is None:
        raise EvaluationError("No organisation is set, so no run can be recorded.")
    element = scoped_ai_system(element_id, organization_id)
    if element is None:
        raise EvaluationError("AI system not found.")
    if isinstance(cases_run, bool) or not isinstance(cases_run, int) or cases_run < 1:
        raise EvaluationError("The number of cases run must be a whole number of at least 1.")
    if cases_run > MAX_CASES:
        raise EvaluationError(f"The number of cases run is limited to {MAX_CASES:,}.")
    cleaned = _clean_metric_map(metrics, what="Measured value")
    if not cleaned:
        raise EvaluationError("A run needs at least one measured metric.")

    if _lock_element(element_id, organization_id) is None:
        raise EvaluationError("AI system not found.")
    plan = get_plan(element_id, organization_id)
    if plan is None:
        raise EvaluationError(
            "Set the evaluation plan (test set and thresholds) before recording a run."
        )
    run = AIEvaluationRun(
        organization_id=organization_id,
        ai_system_id=element_id,
        plan_id=plan.id,
        plan_version=plan.version,
        model_id=_system_model_id(element),
        metrics=cleaned,
        cases_run=cases_run,
        notes=(notes or "").strip() or None,
        recorded_by_id=user_id,
    )
    db.session.add(run)
    db.session.flush()
    return run


def _fmt(value: float) -> str:
    return f"{value:g}"


def metric_findings(plan: AIEvaluationPlan, run: AIEvaluationRun) -> list:
    """One sentence per threshold the run misses or did not measure, in the
    plan's metric order. Empty when every threshold is met."""
    findings = []
    measured = run.metrics or {}
    for metric in METRICS:
        if metric not in (plan.thresholds or {}):
            continue
        threshold = float(plan.thresholds[metric])
        direction = METRICS[metric][0]
        if metric not in measured:
            findings.append(f"{metric} was not measured in the latest run")
            continue
        value = float(measured[metric])
        if direction == "min" and value < threshold:
            findings.append(
                f"{metric} {_fmt(value)} is below the threshold {_fmt(threshold)}"
            )
        elif direction == "max" and value > threshold:
            findings.append(
                f"{metric} {_fmt(value)} is above the threshold {_fmt(threshold)}"
            )
    return findings


def evaluation_refusal_reason(element, organization_id) -> Optional[str]:
    """Why the evaluation keeps *element* from being released, or ``None`` when
    it has a current run that meets every threshold of its plan.

    Current means: made against the plan's present version, and against the
    model the system records now. Every refusal names what is missing or
    failing.
    """
    if organization_id is None:
        return NO_ORGANISATION
    if getattr(element, "organization_id", None) != organization_id:
        return "Release refused: the AI system does not belong to this organisation."
    plan = get_plan(element.id, organization_id)
    if plan is None:
        return (
            "Release refused: no evaluation plan is set for this AI system; "
            "set its test set and thresholds, then record a passing run."
        )
    run = latest_run(element.id, organization_id)
    if run is None:
        return (
            f"Release refused: no evaluation run is recorded against plan "
            f"version {plan.version} ({plan.test_set_name}); record a run."
        )
    if run.plan_id != plan.id or run.plan_version != plan.version:
        return (
            f"Release refused: the latest evaluation run was made against plan "
            f"version {run.plan_version}, but the plan is now version "
            f"{plan.version}; record a new run."
        )
    current_model = _system_model_id(element)
    if (run.model_id or None) != current_model:
        return (
            f"Release refused: the latest evaluation run measured model "
            f"{run.model_id or 'not recorded'}, but the system now records "
            f"{current_model or 'no model'}; record a new run."
        )
    findings = metric_findings(plan, run)
    if findings:
        return "Release refused: evaluation not passed: " + "; ".join(findings) + "."
    return None
